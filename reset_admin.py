"""Reset admin password to 'admin123'."""
import database as db
import auth

def reset_admin_password():
    conn = db.get_db_connection()
    cur = conn.cursor()
    
    # Generate correct bcrypt hash for 'admin123'
    password_hash = auth.hash_password("admin123")
    
    # Update or insert admin user
    cur.execute("""
        INSERT INTO users (username, password_hash, role, employee_number, is_active)
        VALUES ('admin', %s, 'admin', 3001, TRUE)
        ON CONFLICT (username) DO UPDATE SET password_hash = %s
    """, (password_hash, password_hash))
    
    conn.commit()
    conn.close()
    print("Admin password reset to 'admin123'")

if __name__ == "__main__":
    reset_admin_password()
