import os

import psycopg2
from psycopg2.extras import RealDictCursor


def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "db"),  # Defaults to 'db' (docker service name)
        port="5432",  # Always 5432 inside Docker network
        database="workorder_system",
        user="admin",
        password="your_secure_password",
    )


def search_vehicles(term):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    query = """
    SELECT v.*, c.name as customer_name
    FROM vehicles v
    JOIN customers c ON v.customer_id = c.id
    WHERE v.vin = %s
       OR RIGHT(v.vin, 8) = %s
       OR v.unit_number = %s
       OR c.name ILIKE %s
    """
    cur.execute(query, (term, term, term, f"%{term}%"))
    results = cur.fetchall()
    cur.close()
    conn.close()
    return results
