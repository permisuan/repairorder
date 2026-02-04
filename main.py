import datetime
import json
from decimal import Decimal

import httpx
from fastapi import FastAPI, Form, Request, Response, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import database as db
import auth

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# --- MODELS ---
class JobUpdate(BaseModel):
    job_id: int
    status: str
    note: str | None = None


# --- TEMPLATE UTILS ---
def custom_json_serializer(obj):
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Type {type(obj)} not serializable")


templates.env.policies["json.dumps_function"] = lambda obj, **kw: json.dumps(
    obj, default=custom_json_serializer, **kw
)


# --- AUTH & HELPERS ---
def get_current_user(request: Request):
    token = request.cookies.get("session_token")
    return auth.get_session_data(token) if token else None


def clean_int(val):
    if not val or val == "":
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def clean_float(val):
    try:
        return float(val) if val else 0.0
    except (ValueError, TypeError):
        return 0.0


def generate_employee_number(role, cur):
    """Generate employee number based on role."""
    if role == "tech":
        start, end = 1001, 1999
    elif role == "foreman":
        start, end = 2001, 2999
    elif role == "admin":
        start, end = 3001, 3999
    else:
        start, end = 9000, 9999

    cur.execute(
        "SELECT MAX(employee_number) FROM users WHERE employee_number BETWEEN %s AND %s",
        (start, end),
    )
    highest = cur.fetchone()[0]
    return highest + 1 if highest else start


def calculate_tenure(hire_date):
    """Calculate tenure in years from hire date."""
    if not hire_date:
        return "0.00"
    try:
        days = (datetime.date.today() - hire_date).days
        years = days / 365.25
        return f"{years:.2f}"
    except (TypeError, AttributeError):
        return "0.00"


# --- CORE API (VIN & SEARCH) ---
@app.get("/api/decode/{vin}")
async def decode_vin_api(vin: str):
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVin/{vin}?format=json"
            )
            data = resp.json()
        res_map = {
            item["Variable"]: item["Value"] for item in data["Results"] if item["Value"]
        }
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
    
    # Build dynamic query with filters
    conditions = []
    params = []
    
    if vin and len(vin) >= 6:
        # Search by VIN suffix (last N characters where N >= 6)
        vin = vin.upper().strip()
        conditions.append(f"RIGHT(vin, {len(vin)}) = %s")
        params.append(vin)
    
    if unit:
        unit = unit.strip().upper()
        conditions.append("UPPER(unit_number) = %s")
        params.append(unit)
    
    if cust:
        conditions.append("customer_id = %s")
        params.append(cust)
    
    if not conditions:
        conn.close()
        return {"found": False, "vehicles": [], "message": "Please enter at least one search criteria"}
    
    query = "SELECT v.*, c.name as customer_name FROM vehicles v LEFT JOIN customers c ON v.customer_id = c.id WHERE " + " AND ".join(conditions)
    cur.execute(query, tuple(params))
    vehicles = cur.fetchall()
    conn.close()
    
    if vehicles:
        # Return single result for backward compatibility, but also return all matches
        return {"found": True, "vehicle": vehicles[0], "vehicles": vehicles, "count": len(vehicles)}
    return {"found": False, "vehicles": [], "count": 0}


@app.get("/api/active-timer")
async def get_active_timer(request: Request):
    user = get_current_user(request)
    if not user:
        return {"active": False}
    conn = db.get_db_connection()
    cur = conn.cursor(cursor_factory=db.RealDictCursor)
    cur.execute(
        """
        SELECT l.start_time, j.id as line_id, j.title, r.ro_number
        FROM labor_lines l
        JOIN job_lines j ON l.job_line_id = j.id
        JOIN repair_orders r ON j.ro_id = r.id
        WHERE l.tech_id = %s AND l.end_time IS NULL
    """,
        (user["username"],),
    )
    row = cur.fetchone()
    conn.close()
    if row:
        now = datetime.datetime.now()
        start = row["start_time"]
        if hasattr(start, "replace"):
            start = start.replace(tzinfo=None)
        diff = (now - start).total_seconds()
        hours = diff / 3600.0
        return {
            "active": True,
            "line_id": row["line_id"],
            "ro": row["ro_number"],
            "title": row["title"],
            "hours": round(hours, 2),
        }
    return {"active": False}


# --- DASHBOARD ROUTES ---
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    conn = db.get_db_connection()
    cur = conn.cursor(cursor_factory=db.RealDictCursor)

    if user["role"] in ["admin", "foreman"]:
        # Fetch Techs for assignment dropdown
        cur.execute(
            "SELECT id, username FROM users WHERE role='tech' AND is_active=TRUE ORDER BY username"
        )
        techs = cur.fetchall()

        # Fetch ACTIVE ROs
        cur.execute("""
            SELECT ro.id, ro.ro_number, ro.status, ro.created_at,
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

        # Fetch ARCHIVED ROs
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

        conn.close()
        return templates.TemplateResponse(
            "foreman_dash.html",
            {
                "request": request,
                "user": user,
                "active_ros": active_ros,
                "archived_ros": archived_ros,
                "techs": techs,
            },
        )
    else:
        # Tech View
        cur.execute(
            """
            SELECT ro.id, ro.ro_number, ro.vehicle_id, ro.status,
                   v.vin, v.unit_number, v.year, v.make, v.model,
                   v.engine_type, v.license_plate, v.mileage, v.engine_hours,
                   c.name as customer_name
            FROM repair_orders ro
            JOIN vehicles v ON ro.vehicle_id = v.id
            JOIN customers c ON v.customer_id = c.id
            JOIN users u ON ro.assigned_tech_id = u.id
            WHERE u.username = %s AND ro.status != 'Closed'
        """,
            (user["username"],),
        )
        my_jobs = cur.fetchall()
        for job in my_jobs:
            cur.execute(
                "SELECT id, title, notes FROM job_lines WHERE ro_id = %s ORDER BY id ASC",
                (job["id"],),
            )
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
            cur.execute(
                "SELECT title FROM job_lines WHERE ro_id = %s ORDER BY id ASC",
                (job["id"],),
            )
            job["lines"] = cur.fetchall()
        conn.close()
        return templates.TemplateResponse(
            "tech_dash.html",
            {
                "request": request,
                "user": user,
                "my_jobs": my_jobs,
                "unassigned_jobs": unassigned_jobs,
            },
        )


# --- RO OPERATIONS ---
@app.get("/ro/{ro_id}", response_class=HTMLResponse)
async def ro_detail(request: Request, ro_id: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/")
    conn = db.get_db_connection()
    cur = conn.cursor(cursor_factory=db.RealDictCursor)

    cur.execute(
        """SELECT ro.*, v.vin, v.unit_number, v.year, v.make, v.model, v.mileage, v.engine_hours, 
           c.name as customer_name, c.contact_info, u.username as assigned_tech_username
           FROM repair_orders ro 
           JOIN vehicles v ON ro.vehicle_id = v.id 
           JOIN customers c ON v.customer_id = c.id 
           LEFT JOIN users u ON ro.assigned_tech_id = u.id
           WHERE ro.id = %s""",
        (ro_id,),
    )
    ro = cur.fetchone()

    # Tech can only view their own assigned ROs or unassigned ROs
    if user["role"] == "tech":
        if ro["assigned_tech_username"] and ro["assigned_tech_username"] != user["username"]:
            conn.close()
            return RedirectResponse("/")

    cur.execute("SELECT * FROM job_lines WHERE ro_id = %s ORDER BY id ASC", (ro_id,))
    lines = cur.fetchall()
    total_hours = 0.0

    for line in lines:
        cur.execute(
            "SELECT id, tech_id, start_time, duration_hours, notes FROM labor_lines WHERE job_line_id = %s ORDER BY start_time",
            (line["id"],),
        )
        line["punches"] = cur.fetchall()
        line["total_hours"] = round(
            sum(p["duration_hours"] or 0 for p in line["punches"]), 2
        )
        total_hours += line["total_hours"]

    cur.execute("SELECT * FROM shop_settings WHERE id = 1")
    shop = cur.fetchone()
    conn.close()

    labor_rate = shop["labor_rate"] if shop else 0
    return templates.TemplateResponse(
        "ro_detail.html",
        {
            "request": request,
            "ro": ro,
            "lines": lines,
            "user": user,
            "shop": shop,
            "total_hours": round(total_hours, 2),
            "labor_total": round(total_hours * labor_rate, 2),
        },
    )


@app.get("/new-ro", response_class=HTMLResponse)
async def new_ro_page(request: Request):
    user = get_current_user(request)
    if not user or user["role"] not in ["admin", "foreman"]:
        return RedirectResponse("/")
    conn = db.get_db_connection()
    cur = conn.cursor(cursor_factory=db.RealDictCursor)
    cur.execute("SELECT * FROM customers ORDER BY name ASC")
    customers = cur.fetchall()
    conn.close()
    return templates.TemplateResponse(
        "create_ro.html", {"request": request, "customers": customers}
    )


@app.post("/submit-ro")
async def submit_ro(request: Request):
    form = await request.form()
    user = get_current_user(request)
    if not user or user["role"] not in ["admin", "foreman"]:
        return RedirectResponse("/")
    conn = db.get_db_connection()
    cur = conn.cursor()
    try:
        v_id = clean_int(form.get("vehicle_id"))
        is_new = form.get("is_new") == "true"
        mileage = clean_int(form.get("mileage"))
        hours = clean_float(form.get("hours"))
        if is_new:
            cur.execute(
                """INSERT INTO vehicles (customer_id, vin, unit_number, year, make, model, engine_type, mileage, engine_hours) 
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (
                    clean_int(form.get("new_cust_id")),
                    form.get("new_vin", "").upper(),
                    form.get("new_unit"),
                    clean_int(form.get("new_year")),
                    form.get("new_make"),
                    form.get("new_model"),
                    form.get("new_engine"),
                    mileage,
                    hours,
                ),
            )
            v_id = cur.fetchone()[0]
        else:
            cur.execute(
                "UPDATE vehicles SET mileage=%s, engine_hours=%s WHERE id=%s",
                (mileage, hours, v_id),
            )
        cur.execute("CREATE SEQUENCE IF NOT EXISTS ro_sequence START 1000")
        cur.execute("SELECT nextval('ro_sequence')")
        next_ro = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO repair_orders (vehicle_id, ro_number, assigned_tech_id, status) VALUES (%s, %s, NULL, 'Open') RETURNING id",
            (v_id, str(next_ro)),
        )
        ro_id = cur.fetchone()[0]
        titles = form.getlist("lines_title[]")
        notes = form.getlist("lines_notes[]")
        for i in range(len(titles)):
            if titles[i].strip():
                cur.execute(
                    "INSERT INTO job_lines (ro_id, title, notes) VALUES (%s, %s, %s)",
                    (ro_id, titles[i], notes[i] if i < len(notes) else ""),
                )
        conn.commit()
    except Exception as e:
        print(f"Error creating RO: {e}")
        conn.rollback()
    conn.close()
    return RedirectResponse("/", status_code=303)


@app.post("/close-ro/{ro_id}")
async def close_ro(request: Request, ro_id: int):
    user = get_current_user(request)
    if not user or user["role"] not in ["admin", "foreman"]:
        return RedirectResponse("/")
    conn = db.get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE repair_orders SET status='Closed', completed_at=CURRENT_TIMESTAMP WHERE id=%s",
        (ro_id,),
    )
    conn.commit()
    conn.close()
    return RedirectResponse(f"/ro/{ro_id}", status_code=303)


# --- API ROUTES ---
@app.post("/api/assign-tech")
async def assign_tech(
    request: Request, ro_id: int = Form(...), tech_id: str = Form(...)
):
    user = get_current_user(request)
    if not user or user["role"] not in ["admin", "foreman"]:
        return Response(status_code=403)

    conn = db.get_db_connection()
    cur = conn.cursor()
    val = clean_int(tech_id)
    if val == -1 or val is None:
        cur.execute(
            "UPDATE repair_orders SET assigned_tech_id = NULL WHERE id = %s", (ro_id,)
        )
    else:
        cur.execute(
            "UPDATE repair_orders SET assigned_tech_id = %s WHERE id = %s", (val, ro_id)
        )
    conn.commit()
    conn.close()
    return RedirectResponse("/", status_code=303)


@app.post("/api/update-status")
async def update_status(
    request: Request, ro_id: int = Form(...), status: str = Form(...)
):
    user = get_current_user(request)
    if not user:
        return Response(status_code=403)

    conn = db.get_db_connection()
    cur = conn.cursor()
    if status == "Closed":
        cur.execute(
            "UPDATE repair_orders SET status='Closed', completed_at=CURRENT_TIMESTAMP WHERE id=%s",
            (ro_id,),
        )
    else:
        cur.execute("UPDATE repair_orders SET status=%s WHERE id=%s", (status, ro_id))
    conn.commit()
    conn.close()
    return RedirectResponse(f"/ro/{ro_id}", status_code=303)


@app.post("/api/update-line")
async def update_line(
    request: Request,
    line_id: int = Form(...),
    title: str = Form(...),
    notes: str = Form(...),
):
    user = get_current_user(request)
    if not user or user["role"] not in ["admin", "foreman"]:
        return Response(status_code=403)

    conn = db.get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE job_lines SET title=%s, notes=%s WHERE id=%s", (title, notes, line_id)
    )
    conn.commit()
    conn.close()
    return RedirectResponse(request.headers.get("referer", "/"), status_code=303)


@app.post("/api/add-line")
async def add_line(request: Request, ro_id: int = Form(...), title: str = Form(...)):
    user = get_current_user(request)
    if not user or user["role"] not in ["admin", "foreman"]:
        return Response(status_code=403)

    conn = db.get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO job_lines (ro_id, title, notes) VALUES (%s, %s, '')",
        (ro_id, title),
    )
    conn.commit()
    conn.close()
    return RedirectResponse(f"/ro/{ro_id}", status_code=303)


@app.post("/api/delete-line")
async def delete_line(request: Request, line_id: int = Form(...)):
    user = get_current_user(request)
    if not user or user["role"] not in ["admin", "foreman"]:
        return Response(status_code=403)

    conn = db.get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM labor_lines WHERE job_line_id = %s", (line_id,))
    cur.execute("DELETE FROM job_lines WHERE id = %s", (line_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(request.headers.get("referer", "/"), status_code=303)


@app.post("/api/edit-labor")
async def edit_labor(
    request: Request, punch_id: int = Form(...), duration: float = Form(...)
):
    user = get_current_user(request)
    if not user or user["role"] not in ["admin", "foreman"]:
        return Response(status_code=403)

    conn = db.get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE labor_lines SET duration_hours=%s WHERE id=%s AND end_time IS NOT NULL",
        (duration, punch_id),
    )
    conn.commit()
    conn.close()
    return RedirectResponse(request.headers.get("referer", "/"), status_code=303)


@app.post("/update_job")
async def update_job(update: JobUpdate, request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Auth failed"})
    conn = db.get_db_connection()
    cur = conn.cursor()
    status_map = {
        "open": "Open",
        "parts_ordered": "Parts Ordered",
        "wip": "Work in Progress",
        "complete": "Closed",
    }
    db_status = status_map.get(update.status, "Open")
    try:
        if db_status == "Closed":
            cur.execute(
                "UPDATE repair_orders SET status='Closed', completed_at=CURRENT_TIMESTAMP, tech_notes=%s WHERE id=%s",
                (update.note, update.job_id),
            )
        else:
            cur.execute(
                "UPDATE repair_orders SET status=%s WHERE id=%s",
                (db_status, update.job_id),
            )
        conn.commit()
        return {"status": "success"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        conn.close()


# --- WORKER ACTIONS ---
@app.post("/clock-on-line/{line_id}")
async def clock_on_line(request: Request, line_id: int, ro_id: int):
    user = get_current_user(request)
    if not user or user["role"] != "tech":
        return Response(status_code=403)
    conn = db.get_db_connection()
    cur = conn.cursor()

    # Clock out of any existing lines
    cur.execute(
        "UPDATE labor_lines SET end_time=CURRENT_TIMESTAMP, duration_hours=EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP-start_time))/3600 WHERE tech_id=%s AND end_time IS NULL",
        (user["username"],),
    )

    # Assign Tech if unassigned
    cur.execute(
        "UPDATE repair_orders SET assigned_tech_id=(SELECT id FROM users WHERE username=%s) WHERE id=%s AND assigned_tech_id IS NULL",
        (user["username"], ro_id),
    )

    # Set status to 'Work in Progress' if currently 'Open'
    cur.execute(
        "UPDATE repair_orders SET status='Work in Progress' WHERE id=%s AND status='Open'",
        (ro_id,),
    )

    # Clock in
    cur.execute(
        "INSERT INTO labor_lines (job_line_id, tech_id, start_time) VALUES (%s, %s, CURRENT_TIMESTAMP)",
        (line_id, user["username"]),
    )
    conn.commit()
    conn.close()
    return {"status": "success"}


@app.post("/clock-off")
async def clock_off(request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "tech":
        return Response(status_code=403)
    conn = db.get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE labor_lines SET end_time=CURRENT_TIMESTAMP, duration_hours=EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP-start_time))/3600 WHERE tech_id=%s AND end_time IS NULL",
        (user["username"],),
    )
    conn.commit()
    conn.close()
    return {"status": "clocked_out"}


@app.get("/grab-job/{ro_id}")
async def grab_job(request: Request, ro_id: int):
    user = get_current_user(request)
    if not user or user["role"] != "tech":
        return RedirectResponse("/")
    conn = db.get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE repair_orders SET assigned_tech_id=(SELECT id FROM users WHERE username=%s), status='Open' WHERE id=%s",
        (user["username"], ro_id),
    )
    conn.commit()
    conn.close()
    return RedirectResponse("/", status_code=303)


# --- SHOP MANAGEMENT ---
@app.get("/shop-management", response_class=HTMLResponse)
async def shop_management_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/")

    conn = db.get_db_connection()
    cur = conn.cursor(cursor_factory=db.RealDictCursor)

    cur.execute("SELECT * FROM shop_settings WHERE id = 1")
    shop = cur.fetchone()

    active_users = []
    archived_users = []

    if user["role"] == "admin":
        cur.execute(
            "SELECT * FROM users WHERE is_active = TRUE ORDER BY employee_number, role"
        )
        active_users = cur.fetchall()
        for u in active_users:
            u["tenure"] = calculate_tenure(u.get("hire_date"))

        cur.execute(
            "SELECT * FROM users WHERE is_active = FALSE ORDER BY employee_number"
        )
        archived_users = cur.fetchall()
        for u in archived_users:
            u["tenure"] = calculate_tenure(u.get("hire_date"))

    conn.close()
    return templates.TemplateResponse(
        "shop_management.html",
        {
            "request": request,
            "user": user,
            "shop": shop,
            "users": active_users,
            "archived": archived_users,
        },
    )


@app.post("/update-shop-settings")
async def update_shop_settings(request: Request):
    form = await request.form()
    user = get_current_user(request)

    if not user or user["role"] != "admin":
        return RedirectResponse("/")

    conn = db.get_db_connection()
    cur = conn.cursor(cursor_factory=db.RealDictCursor)

    cur.execute(
        "UPDATE shop_settings SET name=%s, address=%s, phone=%s, labor_rate=%s WHERE id=1",
        (
            form.get("name"),
            form.get("address"),
            form.get("phone"),
            clean_float(form.get("rate")),
        ),
    )
    conn.commit()
    conn.close()
    return RedirectResponse("/shop-management", status_code=303)


# --- USER MANAGEMENT ---
@app.post("/create-user")
async def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    pay_rate: str = Form(...),
    tech_level: str = Form(None),
    hire_date: str = Form(None),
):
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/")

    conn = db.get_db_connection()
    cur = conn.cursor()
    try:
        emp_num = generate_employee_number(role, cur)
        h_date = hire_date if hire_date else datetime.date.today()
        history = f"[{datetime.date.today()}] Hired as {role}\n"

        cur.execute(
            """
            INSERT INTO users (username, password_hash, role, pay_rate, tech_level, employee_number, is_active, hire_date, status_history)
            VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s, %s)
        """,
            (
                username,
                auth.hash_password(password),
                role,
                clean_float(pay_rate),
                tech_level,
                emp_num,
                h_date,
                history,
            ),
        )
        conn.commit()
    except Exception as e:
        print(f"Create Error: {e}")
    conn.close()
    return RedirectResponse("/shop-management", status_code=303)


@app.post("/update-user")
async def update_user(
    request: Request,
    user_id: str = Form(...),
    password: str = Form(None),
    role: str = Form(...),
    pay_rate: str = Form(...),
    tech_level: str = Form(None),
    hire_date: str = Form(None),
):
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/")

    conn = db.get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE users SET role=%s, pay_rate=%s, tech_level=%s, hire_date=%s WHERE id=%s
        """,
            (
                role,
                clean_float(pay_rate),
                tech_level if role == "tech" else None,
                hire_date,
                clean_int(user_id),
            ),
        )

        if password and password.strip():
            cur.execute(
                "UPDATE users SET password_hash=%s WHERE id=%s",
                (auth.hash_password(password), clean_int(user_id)),
            )

        conn.commit()
    except Exception as e:
        print(f"Update Error: {e}")
    conn.close()
    return RedirectResponse("/shop-management", status_code=303)


@app.post("/archive-user")
async def archive_user(
    request: Request, user_id: str = Form(...), reason: str = Form(...)
):
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/")

    conn = db.get_db_connection()
    cur = conn.cursor()
    try:
        note = f"\n[{datetime.date.today()}] Archived: {reason}"
        cur.execute(
            """
            UPDATE users SET is_active = FALSE, status_history = COALESCE(status_history, '') || %s
            WHERE id = %s
        """,
            (note, clean_int(user_id)),
        )
        conn.commit()
    except Exception as e:
        print(f"Archive Error: {e}")
    conn.close()
    return RedirectResponse("/shop-management", status_code=303)


@app.post("/reactivate-user")
async def reactivate_user(request: Request, user_id: str = Form(...)):
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/")

    conn = db.get_db_connection()
    cur = conn.cursor()
    try:
        note = f"\n[{datetime.date.today()}] Reactivated / Rehired"
        cur.execute(
            """
            UPDATE users SET is_active = TRUE, status_history = COALESCE(status_history, '') || %s
            WHERE id = %s
        """,
            (note, clean_int(user_id)),
        )
        conn.commit()
    except Exception as e:
        print(f"Reactivate Error: {e}")
    conn.close()
    return RedirectResponse("/shop-management", status_code=303)


# --- CUSTOMERS ---
@app.get("/customers", response_class=HTMLResponse)
async def customer_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")
    
    conn = db.get_db_connection()
    cur = conn.cursor(cursor_factory=db.RealDictCursor)
    cur.execute("SELECT * FROM customers ORDER BY name ASC")
    customers = cur.fetchall()
    
    # Fetch emails and phones for each customer
    for customer in customers:
        cur.execute(
            "SELECT id, email, is_primary FROM customer_emails WHERE customer_id = %s ORDER BY is_primary DESC, id",
            (customer["id"],)
        )
        customer["emails"] = cur.fetchall()
        
        cur.execute(
            "SELECT id, phone, contact_name, extension, is_primary FROM customer_phones WHERE customer_id = %s ORDER BY is_primary DESC, id",
            (customer["id"],)
        )
        customer["phones"] = cur.fetchall()
    
    conn.close()
    
    # Role-based permissions
    can_create = user["role"] in ["admin", "foreman"]
    can_edit = user["role"] == "admin"
    
    return templates.TemplateResponse(
        "customers.html", {
            "request": request,
            "user": user,
            "customers": customers,
            "can_create": can_create,
            "can_edit": can_edit,
        }
    )


@app.post("/add-customer")
async def add_customer(request: Request):
    user = get_current_user(request)
    if not user or user["role"] not in ["admin", "foreman"]:
        return Response(status_code=403)
    
    form = await request.form()
    name = form.get("name", "").strip()
    billing_address = form.get("billing_address", "").strip()
    emails = form.getlist("emails[]")
    phones = form.getlist("phones[]")
    
    if not name:
        return RedirectResponse("/customers", status_code=303)
    
    conn = db.get_db_connection()
    cur = conn.cursor()
    
    try:
        # Insert customer
        cur.execute(
            "INSERT INTO customers (name, billing_address) VALUES (%s, %s) RETURNING id",
            (name, billing_address or None)
        )
        customer_id = cur.fetchone()[0]
        
        # Insert emails
        for i, email in enumerate(emails):
            email = email.strip()
            if email:
                cur.execute(
                    "INSERT INTO customer_emails (customer_id, email, is_primary) VALUES (%s, %s, %s)",
                    (customer_id, email, i == 0)
                )
        
        # Insert phones with contact names and extensions
        contact_names = form.getlist("contact_names[]")
        extensions = form.getlist("extensions[]")
        for i, phone in enumerate(phones):
            phone = phone.strip()
            if phone:
                contact_name = contact_names[i].strip() if i < len(contact_names) else ""
                extension = extensions[i].strip() if i < len(extensions) else ""
                cur.execute(
                    "INSERT INTO customer_phones (customer_id, phone, contact_name, extension, is_primary) VALUES (%s, %s, %s, %s, %s)",
                    (customer_id, phone, contact_name or None, extension or None, i == 0)
                )
        
        conn.commit()
    except Exception as e:
        print(f"Error adding customer: {e}")
        conn.rollback()
    finally:
        conn.close()
    
    return RedirectResponse("/customers", status_code=303)


@app.post("/api/customer/{customer_id}")
async def update_customer(request: Request, customer_id: int):
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return Response(status_code=403)
    
    form = await request.form()
    name = form.get("name", "").strip()
    billing_address = form.get("billing_address", "").strip()
    emails = form.getlist("emails[]")
    phones = form.getlist("phones[]")
    
    if not name:
        return JSONResponse({"error": "Name is required"}, status_code=400)
    
    conn = db.get_db_connection()
    cur = conn.cursor()
    
    try:
        # Update customer basic info
        cur.execute(
            "UPDATE customers SET name = %s, billing_address = %s WHERE id = %s",
            (name, billing_address or None, customer_id)
        )
        
        # Clear existing emails and phones, then re-insert
        cur.execute("DELETE FROM customer_emails WHERE customer_id = %s", (customer_id,))
        cur.execute("DELETE FROM customer_phones WHERE customer_id = %s", (customer_id,))
        
        # Insert emails
        for i, email in enumerate(emails):
            email = email.strip()
            if email:
                cur.execute(
                    "INSERT INTO customer_emails (customer_id, email, is_primary) VALUES (%s, %s, %s)",
                    (customer_id, email, i == 0)
                )
        
        # Insert phones with contact names and extensions
        contact_names = form.getlist("contact_names[]")
        extensions = form.getlist("extensions[]")
        for i, phone in enumerate(phones):
            phone = phone.strip()
            if phone:
                contact_name = contact_names[i].strip() if i < len(contact_names) else ""
                extension = extensions[i].strip() if i < len(extensions) else ""
                cur.execute(
                    "INSERT INTO customer_phones (customer_id, phone, contact_name, extension, is_primary) VALUES (%s, %s, %s, %s, %s)",
                    (customer_id, phone, contact_name or None, extension or None, i == 0)
                )
        
        conn.commit()
    except Exception as e:
        print(f"Error updating customer: {e}")
        conn.rollback()
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        conn.close()
    
    return RedirectResponse("/customers", status_code=303)


# --- AUTH ---
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login(username: str = Form(...), password: str = Form(...)):
    conn = db.get_db_connection()
    cur = conn.cursor(cursor_factory=db.RealDictCursor)
    cur.execute(
        "SELECT * FROM users WHERE LOWER(username) = LOWER(%s) AND is_active = TRUE", (username,)
    )
    user = cur.fetchone()
    conn.close()
    if user and auth.verify_password(password, user["password_hash"]):
        token = auth.create_session_token(
            {"username": user["username"], "role": user["role"]}
        )
        resp = RedirectResponse(url="/", status_code=303)
        resp.set_cookie(key="session_token", value=token, httponly=True)
        return resp
    return RedirectResponse("/login?error=1", status_code=303)


@app.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("session_token")
    return resp