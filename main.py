import datetime
import json
import httpx
from fastapi import FastAPI, Form, Request, Response, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import database as db
import auth  # Integrated security logic

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# --- DATA MODELS (For JS Communication) ---
class JobUpdate(BaseModel):
    job_id: int
    status: str
    note: str | None = None

# --- GLOBAL FIX: JSON Date Serializer for Templates ---
def custom_json_serializer(obj):
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

def custom_dumps(obj, **kwargs):
    return json.dumps(obj, default=custom_json_serializer, **kwargs)

templates.env.policies["json.dumps_function"] = custom_dumps

# --- AUTH & HELPERS ---
def get_current_user(request: Request):
    token = request.cookies.get("session_token")
    if not token:
        return None
    return auth.get_session_data(token)

def clean_int(val):
    if not val or val == "":
        return None
    try:
        return int(val)
    except:
        return None

def clean_float(val):
    if not val or val == "":
        return 0.0
    try:
        return float(val)
    except:
        return 0.0

def generate_employee_number(role, cur):
    if role == "tech":
        start, end = 1001, 1999
    elif role in ["manager", "foreman"]:
        start, end = 2001, 2999
    elif role == "admin":
        start, end = 3001, 3999
    else:
        start, end = 9000, 9999

    cur.execute(
        "SELECT MAX(employee_number) FROM users WHERE employee_number BETWEEN %s AND %s",
        (start, end),
    )
    res = cur.fetchone()
    # Handle both tuple (psycopg2) and RealDictCursor cases safely
    if res:
        highest = res[0] if isinstance(res, tuple) else res.get('max')
    else:
        highest = None
        
    return highest + 1 if highest else start

def calculate_tenure(hire_date):
    if not hire_date:
        return "0.00"
    try:
        days = (datetime.date.today() - hire_date).days
        years = days / 365.25
        return f"{years:.2f}"
    except:
        return "0.00"

# --- API ROUTES (VEHICLE & TIMER) ---
@app.get("/api/decode/{vin}")
async def decode_vin_api(vin: str):
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVin/{vin}?format=json"
            )
            data = resp.json()
        res_map = {item["Variable"]: item["Value"] for item in data["Results"] if item["Value"]}
        mfg = res_map.get("Engine Manufacturer", "") or res_map.get("Make", "")
        disp = res_map.get("Displacement (L)", "")
        config = res_map.get("Engine Configuration", "")
        return {
            "year": res_map.get("Model Year", ""),
            "make": res_map.get("Make", ""),
            "model": res_map.get("Model", ""),
            "engine": f"{mfg} {disp}L {config}".strip(),
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/search-vehicle")
async def search_vehicle_api(vin: str = None, unit: str = None, cust: int = None):
    conn = db.get_db_connection()
    cur = conn.cursor(cursor_factory=db.RealDictCursor)
    if vin:
        cur.execute("SELECT * FROM vehicles WHERE RIGHT(vin, 8) = %s", (vin,))
    elif unit and cust:
        cur.execute("SELECT * FROM vehicles WHERE unit_number = %s AND customer_id = %s", (unit, cust))
    else:
        conn.close()
        return {"found": False}
    vehicle = cur.fetchone()
    conn.close()
    return {"found": True, "vehicle": vehicle} if vehicle else {"found": False}

@app.get("/api/active-timer")
async def get_active_timer(request: Request):
    user = get_current_user(request)
    if not user: return {"active": False}
    conn = db.get_db_connection()
    cur = conn.cursor(cursor_factory=db.RealDictCursor)
    cur.execute("""
        SELECT l.start_time, j.id as line_id, j.title, r.ro_number
        FROM labor_lines l
        JOIN job_lines j ON l.job_line_id = j.id
        JOIN repair_orders r ON j.ro_id = r.id
        WHERE l.tech_id = %s AND l.end_time IS NULL
    """, (user["username"],))
    row = cur.fetchone()
    conn.close()
    if row:
        start = row["start_time"]
        now = datetime.datetime.now(start.tzinfo) if start.tzinfo else datetime.datetime.now()
        diff = (now - start).total_seconds()
        return {
            "active": True,
            "line_id": row["line_id"],
            "ro": row["ro_number"],
            "title": row["title"],
            "hours": round(diff / 3600.0, 2),
        }
    return {"active": False}

# --- DASHBOARD ROUTES ---
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login")

    conn = db.get_db_connection()
    cur = conn.cursor(cursor_factory=db.RealDictCursor)

    if user["role"] in ["admin", "manager", "foreman"]:
        cur.execute("SELECT id, username FROM users WHERE role='tech' AND is_active=TRUE ORDER BY username")
        techs = cur.fetchall()

        cur.execute("""
            SELECT ro.id, ro.ro_number, ro.status, ro.created_at,
                   ro.is_warranty, ro.tech_notes,
                   v.unit_number, v.make, v.model, v.vin,
                   c.name as customer_name,
                   u.username as tech_name, u.id as tech_id
            FROM repair_orders ro
            JOIN vehicles v ON ro.vehicle_id = v.id
            JOIN customers c ON v.customer_id = c.id
            LEFT JOIN users u ON ro.assigned_tech_id = u.id
            WHERE ro.status != 'Closed'
            ORDER BY ro.ro_number DESC
        """)
        active_ros = cur.fetchall()

        cur.execute("""
            SELECT ro.id, ro.ro_number, ro.status, ro.completed_at,
                   v.unit_number, v.make, v.model, v.vin,
                   c.name as customer_name,
                   u.username as tech_name
            FROM repair_orders ro
            JOIN vehicles v ON ro.vehicle_id = v.id
            JOIN customers c ON v.customer_id = c.id
            LEFT JOIN users u ON ro.assigned_tech_id = u.id
            WHERE ro.status = 'Closed'
            ORDER BY ro.completed_at DESC LIMIT 200
        """)
        archived_ros = cur.fetchall()

        cur.execute("""
            SELECT u.username as tech_name, r.ro_number, j.title as job_title, l.start_time
            FROM labor_lines l
            JOIN users u ON l.tech_id = u.username
            JOIN job_lines j ON l.job_line_id = j.id
            JOIN repair_orders r ON j.ro_id = r.id
            WHERE l.end_time IS NULL
            ORDER BY l.start_time ASC
        """)
        active_techs = cur.fetchall()
        
        for tech in active_techs:
            start = tech["start_time"]
            now = datetime.datetime.now(start.tzinfo) if start.tzinfo else datetime.datetime.now()
            diff = (now - start).total_seconds()
            tech["duration"] = round(diff / 3600.0, 2)

        conn.close()
        
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request, 
                "user": user, 
                "active_ros": active_ros, 
                "archived_ros": archived_ros, 
                "techs": techs,
                "active_techs": active_techs
            }
        )
    else:
        # --- TECH DASHBOARD LOGIC ---
        cur.execute("""
            SELECT ro.id, ro.ro_number, ro.vehicle_id, ro.status,
                   v.vin, v.unit_number, v.year, v.make, v.model,
                   v.engine_type, v.license_plate, v.mileage, v.engine_hours,
                   c.name as customer_name
            FROM repair_orders ro
            JOIN vehicles v ON ro.vehicle_id = v.id
            JOIN customers c ON v.customer_id = c.id
            JOIN users u ON ro.assigned_tech_id = u.id
            WHERE u.username = %s AND ro.status != 'Closed'
            ORDER BY CASE WHEN ro.status = 'Work in Progress' THEN 0 ELSE 1 END, ro.ro_number DESC
        """, (user["username"],))
        my_jobs = cur.fetchall()
        
        for job in my_jobs:
            cur.execute("SELECT id, title, notes FROM job_lines WHERE ro_id = %s ORDER BY id ASC", (job["id"],))
            job["lines"] = cur.fetchall()

        cur.execute("""
            SELECT ro.id, ro.ro_number, ro.vehicle_id, ro.status,
                   v.unit_number, v.make, v.model, c.name as customer_name
            FROM repair_orders ro
            JOIN vehicles v ON ro.vehicle_id = v.id
            JOIN customers c ON v.customer_id = c.id
            WHERE ro.assigned_tech_id IS NULL AND ro.status != 'Closed'
        """)
        unassigned_jobs = cur.fetchall()
        for job in unassigned_jobs:
            cur.execute("SELECT title FROM job_lines WHERE ro_id = %s ORDER BY id ASC", (job["id"],))
            job["lines"] = cur.fetchall()
        
        conn.close()
        return templates.TemplateResponse(
            "tech_dash.html",
            {"request": request, "user": user, "my_jobs": my_jobs, "unassigned_jobs": unassigned_jobs}
        )

# --- API ROUTES ---

# NEW: Update Job Status from JS Fetch (Handles JSON)
@app.post("/update_job")
async def update_job_status(update: JobUpdate, request: Request):
    user = get_current_user(request)
    if not user: return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    
    conn = db.get_db_connection()
    cur = conn.cursor()

    # Map JS status values to Database values
    status_map = {
        'open': 'Open',
        'parts_ordered': 'Parts Ordered',
        'wip': 'Work in Progress',
        'complete': 'Closed'
    }
    
    db_status = status_map.get(update.status, 'Open')
    
    try:
        if db_status == 'Closed':
            cur.execute("""
                UPDATE repair_orders 
                SET status='Closed', completed_at=CURRENT_TIMESTAMP, tech_notes=%s 
                WHERE id=%s
            """, (update.note, update.job_id))
        else:
            cur.execute("UPDATE repair_orders SET status=%s WHERE id=%s", (db_status, update.job_id))
            
        conn.commit()
    except Exception as e:
        print(f"Error updating job: {e}")
        conn.close()
        return JSONResponse(status_code=500, content={"error": str(e)})

    conn.close()
    return {"status": "success"}

# NEW: Grab Job Route
@app.get("/grab-job/{ro_id}")
async def grab_job(request: Request, ro_id: int):
    user = get_current_user(request)
    if not user: return RedirectResponse("/")
    
    conn = db.get_db_connection()
    cur = conn.cursor()
    
    # 1. Get the tech's user ID
    cur.execute("SELECT id FROM users WHERE username = %s", (user['username'],))
    res = cur.fetchone()
    
    if res:
        tech_db_id = res[0] # Tuple access for standard cursor
        # 2. Assign the job
        cur.execute("UPDATE repair_orders SET assigned_tech_id = %s, status = 'Open' WHERE id = %s", (tech_db_id, ro_id))
        conn.commit()
        
    conn.close()
    return RedirectResponse("/", status_code=303)

@app.post("/api/assign-tech")
async def assign_tech(request: Request, ro_id: int = Form(...), tech_id: str = Form(...)):
    user = get_current_user(request)
    if not user or user["role"] not in ["admin", "manager", "foreman"]: return Response(status_code=403)
    conn = db.get_db_connection()
    cur = conn.cursor()
    val = clean_int(tech_id)
    if val in [-1, None]:
        cur.execute("UPDATE repair_orders SET assigned_tech_id = NULL WHERE id = %s", (ro_id,))
    else:
        cur.execute("UPDATE repair_orders SET assigned_tech_id = %s WHERE id = %s", (val, ro_id))
    conn.commit()
    conn.close()
    return RedirectResponse("/", status_code=303)

@app.post("/api/update-warranty")
async def update_warranty(request: Request, ro_id: int = Form(...), is_warranty: str = Form(...)):
    if not get_current_user(request): return Response(status_code=403)
    conn = db.get_db_connection()
    cur = conn.cursor()
    val = is_warranty.lower() == 'true'
    cur.execute("UPDATE repair_orders SET is_warranty=%s WHERE id=%s", (val, ro_id))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.post("/api/update-line")
async def update_line(request: Request, line_id: int = Form(...), title: str = Form(...), notes: str = Form(...)):
    user = get_current_user(request)
    if not user or user["role"] not in ["admin", "manager", "foreman"]: return Response(status_code=403)
    conn = db.get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE job_lines SET title=%s, notes=%s WHERE id=%s", (title, notes, line_id))
    conn.commit()
    conn.close()
    return RedirectResponse(request.headers.get("referer"), status_code=303)

@app.post("/api/add-line")
async def add_line(request: Request, ro_id: int = Form(...), title: str = Form(...)):
    user = get_current_user(request)
    if not user or user["role"] not in ["admin", "manager", "foreman"]: 
        return Response(content="Forbidden", status_code=403)
    conn = db.get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO job_lines (ro_id, title, notes) VALUES (%s, %s, '')", (ro_id, title))
    conn.commit()
    conn.close()
    return RedirectResponse(f"/ro/{ro_id}", status_code=303)

@app.post("/api/delete-line")
async def delete_line(request: Request, line_id: int = Form(...)):
    user = get_current_user(request)
    if not user or user["role"] not in ["admin", "manager", "foreman"]: return Response(status_code=403)
    conn = db.get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM labor_lines WHERE job_line_id = %s", (line_id,))
    cur.execute("DELETE FROM job_lines WHERE id = %s", (line_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(request.headers.get("referer"), status_code=303)

@app.post("/api/edit-labor")
async def edit_labor(request: Request, punch_id: int = Form(...), duration: float = Form(...)):
    user = get_current_user(request)
    if not user or user["role"] not in ["admin", "manager", "foreman"]: return Response(status_code=403)
    conn = db.get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE labor_lines SET duration_hours=%s WHERE id=%s AND end_time IS NOT NULL", (duration, punch_id))
    conn.commit()
    conn.close()
    return RedirectResponse(request.headers.get("referer"), status_code=303)

# --- SHOP MANAGEMENT ---
@app.get("/shop-management", response_class=HTMLResponse)
async def shop_management_page(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse("/")
    conn = db.get_db_connection()
    cur = conn.cursor(cursor_factory=db.RealDictCursor)
    cur.execute("SELECT * FROM shop_settings WHERE id = 1")
    shop = cur.fetchone()
    active_users, archived_users = [], []
    if user["role"] == "admin":
        cur.execute("SELECT * FROM users WHERE is_active = TRUE ORDER BY employee_number, role")
        active_users = cur.fetchall()
        for u in active_users: u["tenure"] = calculate_tenure(u.get("hire_date"))
        cur.execute("SELECT * FROM users WHERE is_active = FALSE ORDER BY employee_number")
        archived_users = cur.fetchall()
        for u in archived_users: u["tenure"] = calculate_tenure(u.get("hire_date"))
    conn.close()
    return templates.TemplateResponse(
        "shop_management.html",
        {"request": request, "user": user, "shop": shop, "users": active_users, "archived": archived_users}
    )

@app.post("/update-shop-settings")
async def update_shop_settings(request: Request):
    form = await request.form()
    user = get_current_user(request)
    if not user or user["role"] != "admin": return RedirectResponse("/")
    conn = db.get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE shop_settings SET name=%s, address=%s, phone=%s, labor_rate=%s WHERE id=1",
                (form.get("name"), form.get("address"), form.get("phone"), clean_float(form.get("rate"))))
    conn.commit()
    conn.close()
    return RedirectResponse("/shop-management", status_code=303)

# --- USER ACTIONS ---
@app.post("/create-user")
async def create_user(request: Request, username: str = Form(...), password: str = Form(...), role: str = Form(...),
                      pay_rate: str = Form(...), tech_level: str = Form(None), hire_date: str = Form(None)):
    user = get_current_user(request)
    if not user or user["role"] != "admin": return RedirectResponse("/")
    conn = db.get_db_connection()
    cur = conn.cursor()
    try:
        emp_num = generate_employee_number(role, cur)
        h_date = hire_date or datetime.date.today()
        history = f"[{datetime.date.today()}] Hired as {role}\n"
        cur.execute("""
            INSERT INTO users (username, password_hash, role, pay_rate, tech_level, employee_number, is_active, hire_date, status_history)
            VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s, %s)
        """, (username, auth.hash_password(password), role, clean_float(pay_rate), tech_level, emp_num, h_date, history))
        conn.commit()
    except Exception as e: print(f"Create Error: {e}")
    conn.close()
    return RedirectResponse("/shop-management", status_code=303)

@app.post("/update-user")
async def update_user(request: Request, user_id: str = Form(...), password: str = Form(None), role: str = Form(...),
                      pay_rate: str = Form(...), tech_level: str = Form(None), hire_date: str = Form(None)):
    if not get_current_user(request) or get_current_user(request)["role"] != "admin": return RedirectResponse("/")
    conn = db.get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE users SET role=%s, pay_rate=%s, tech_level=%s, hire_date=%s WHERE id=%s",
                    (role, clean_float(pay_rate), tech_level if role == "tech" else None, hire_date, clean_int(user_id)))
        if password and password.strip():
            cur.execute("UPDATE users SET password_hash=%s WHERE id=%s", (auth.hash_password(password), clean_int(user_id)))
        conn.commit()
    except Exception as e: print(f"Update Error: {e}")
    conn.close()
    return RedirectResponse("/shop-management", status_code=303)

@app.post("/archive-user")
async def archive_user(request: Request, user_id: str = Form(...), reason: str = Form(...)):
    if not get_current_user(request) or get_current_user(request)["role"] != "admin": return RedirectResponse("/")
    conn = db.get_db_connection()
    cur = conn.cursor()
    try:
        note = f"\n[{datetime.date.today()}] Archived: {reason}"
        cur.execute("UPDATE users SET is_active = FALSE, status_history = COALESCE(status_history, '') || %s WHERE id = %s", (note, clean_int(user_id)))
        conn.commit()
    except Exception as e: print(f"Archive Error: {e}")
    conn.close()
    return RedirectResponse("/shop-management", status_code=303)

@app.post("/reactivate-user")
async def reactivate_user(request: Request, user_id: str = Form(...)):
    if not get_current_user(request) or get_current_user(request)["role"] != "admin": return RedirectResponse("/")
    conn = db.get_db_connection()
    cur = conn.cursor()
    try:
        note = f"\n[{datetime.date.today()}] Reactivated / Rehired"
        cur.execute("UPDATE users SET is_active = TRUE, status_history = COALESCE(status_history, '') || %s WHERE id = %s", (note, clean_int(user_id)))
        conn.commit()
    except Exception as e: print(f"Reactivate Error: {e}")
    conn.close()
    return RedirectResponse("/shop-management", status_code=303)

# --- RO OPERATIONS ---
@app.get("/ro/{ro_id}", response_class=HTMLResponse)
async def ro_detail(request: Request, ro_id: int):
    user = get_current_user(request)
    if not user: return RedirectResponse("/")
    conn = db.get_db_connection()
    cur = conn.cursor(cursor_factory=db.RealDictCursor)
    
    # 1. Fetch RO
    cur.execute("""
        SELECT ro.*, v.vin, v.unit_number, v.year, v.make, v.model, v.mileage, v.engine_hours, 
               c.name as customer_name, c.contact_info 
        FROM repair_orders ro 
        JOIN vehicles v ON ro.vehicle_id = v.id 
        JOIN customers c ON v.customer_id = c.id 
        WHERE ro.id = %s
    """, (ro_id,))
    ro = cur.fetchone()
    
    if not ro:
        conn.close()
        return Response(content="RO not found", status_code=404)

    # 2. Fetch Lines
    cur.execute("SELECT * FROM job_lines WHERE ro_id = %s ORDER BY id ASC", (ro_id,))
    lines = cur.fetchall()
    total_hours = 0.0

    for line in lines:
        cur.execute("SELECT id, tech_id, start_time, duration_hours, notes FROM labor_lines WHERE job_line_id = %s ORDER BY start_time", (line["id"],))
        line["punches"] = cur.fetchall()
        
        # Calculate individual line hours safely
        line_sum = sum(p["duration_hours"] or 0 for p in line["punches"])
        line["total_hours"] = round(line_sum, 2)
        total_hours += line_sum

    # 3. Fetch Shop Settings with safety
    cur.execute("SELECT * FROM shop_settings WHERE id = 1")
    shop = cur.fetchone()
    if not shop:
        shop = {"name": "Shop", "labor_rate": 0.0} # Fallback
        
    rate = float(shop.get("labor_rate") or 0)
    
    conn.close()
    return templates.TemplateResponse(
        "ro_detail.html", 
        {
            "request": request, 
            "ro": ro, 
            "lines": lines, 
            "user": user, 
            "shop": shop, 
            "total_hours": round(total_hours, 2), 
            "labor_total": round(total_hours * rate, 2)
        }
    )

@app.post("/close-ro/{ro_id}")
async def close_ro(request: Request, ro_id: int):
    user = get_current_user(request)
    if not user or user["role"] not in ["admin", "manager", "foreman"]: return RedirectResponse("/")
    conn = db.get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE repair_orders SET status='Closed', completed_at=CURRENT_TIMESTAMP WHERE id=%s", (ro_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(f"/ro/{ro_id}", status_code=303)

@app.post("/submit-ro")
async def submit_ro(request: Request):
    form = await request.form()
    user = get_current_user(request)
    if not user or user["role"] not in ["admin", "manager", "foreman"]: return RedirectResponse("/")
    conn = db.get_db_connection()
    cur = conn.cursor()
    try:
        v_id, is_new = clean_int(form.get("vehicle_id")), form.get("is_new") == "true"
        mileage, hours = clean_int(form.get("mileage")), clean_float(form.get("hours"))
        if is_new:
            cur.execute("""
                INSERT INTO vehicles (customer_id, vin, unit_number, year, make, model, engine_type, mileage, engine_hours) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (clean_int(form.get("new_cust_id")), form.get("new_vin", "").upper(), form.get("new_unit"),
                  clean_int(form.get("new_year")), form.get("new_make"), form.get("new_model"), form.get("new_engine"), mileage, hours))
            v_id = cur.fetchone()[0]
        else:
            cur.execute("UPDATE vehicles SET mileage=%s, engine_hours=%s WHERE id=%s", (mileage, hours, v_id))
        
        cur.execute("CREATE SEQUENCE IF NOT EXISTS ro_sequence START 1000")
        cur.execute("SELECT nextval('ro_sequence')")
        next_ro = cur.fetchone()[0]
        cur.execute("INSERT INTO repair_orders (vehicle_id, ro_number, assigned_tech_id, status) VALUES (%s, %s, NULL, 'Open') RETURNING id", (v_id, str(next_ro)))
        ro_id, titles, notes = cur.fetchone()[0], form.getlist("lines_title[]"), form.getlist("lines_notes[]")
        for i in range(len(titles)):
            if titles[i].strip():
                cur.execute("INSERT INTO job_lines (ro_id, title, notes) VALUES (%s, %s, %s)", (ro_id, titles[i], notes[i] if i < len(notes) else ""))
        conn.commit()
    except Exception as e:
        print(f"Error creating RO: {e}")
        conn.rollback()
    conn.close()
    return RedirectResponse("/", status_code=303)

@app.post("/clock-on-line/{line_id}")
async def clock_on_line(request: Request, line_id: int, ro_id: int):
    user = get_current_user(request)
    if not user: return Response(status_code=401)
    conn = db.get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE labor_lines SET end_time=CURRENT_TIMESTAMP, duration_hours=EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP-start_time))/3600 WHERE tech_id=%s AND end_time IS NULL", (user["username"],))
    cur.execute("UPDATE repair_orders SET assigned_tech_id=(SELECT id FROM users WHERE username=%s) WHERE id=%s AND assigned_tech_id IS NULL", (user["username"], ro_id))
    cur.execute("UPDATE repair_orders SET status='Work in Progress' WHERE id=%s AND status='Open'", (ro_id,))
    cur.execute("INSERT INTO labor_lines (job_line_id, tech_id, start_time) VALUES (%s, %s, CURRENT_TIMESTAMP)", (line_id, user["username"]))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.post("/clock-off")
async def clock_off(request: Request):
    user = get_current_user(request)
    if not user: return Response(status_code=401)
    conn = db.get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE labor_lines SET end_time=CURRENT_TIMESTAMP, duration_hours=EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP-start_time))/3600 WHERE tech_id=%s AND end_time IS NULL", (user["username"],))
    conn.commit()
    conn.close()
    return {"status": "clocked_out"}

@app.get("/login", response_class=HTMLResponse)
async def login_page(r: Request):
    return templates.TemplateResponse("login.html", {"request": r})

@app.post("/login")
async def login(r: Response, username: str = Form(...), password: str = Form(...)):
    conn = db.get_db_connection()
    cur = conn.cursor(cursor_factory=db.RealDictCursor)
    cur.execute("SELECT * FROM users WHERE username = %s AND is_active = TRUE", (username,))
    user = cur.fetchone()
    conn.close()
    
    if user and auth.verify_password(password, user["password_hash"]):
        token = auth.create_session_token({"username": user['username'], "role": user['role']})
        resp = RedirectResponse(url="/", status_code=303)
        resp.set_cookie(key="session_token", value=token, httponly=True)
        return resp
        
    return templates.TemplateResponse("login.html", {"request": {}, "error": "Invalid Credentials"})

@app.get("/logout")
async def logout(r: Response):
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("session_token")
    return resp

@app.get("/new-ro", response_class=HTMLResponse)
async def new_ro_page(r: Request):
    user = get_current_user(r)
    if not user or user["role"] not in ["admin", "manager", "foreman"]: return RedirectResponse("/")
    conn = db.get_db_connection()
    cur = conn.cursor(cursor_factory=db.RealDictCursor)
    cur.execute("SELECT * FROM customers ORDER BY name ASC")
    customers = cur.fetchall()
    conn.close()
    return templates.TemplateResponse("create_ro.html", {"request": r, "customers": customers})

@app.get("/customers", response_class=HTMLResponse)
async def customer_page(r: Request):
    conn = db.get_db_connection()
    cur = conn.cursor(cursor_factory=db.RealDictCursor)
    cur.execute("SELECT * FROM customers ORDER BY name ASC")
    customers = cur.fetchall()
    conn.close()
    return templates.TemplateResponse("customers.html", {"request": r, "customers": customers})

@app.post("/add-customer")
async def add_customer(r: Request, name: str = Form(...)):
    conn = db.get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO customers (name) VALUES (%s)", (name,))
    conn.commit()
    conn.close()
    return RedirectResponse("/customers", 303)