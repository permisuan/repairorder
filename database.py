import os

import psycopg2
from psycopg2.extras import RealDictCursor

# Export RealDictCursor for clean imports in other modules
__all__ = ["get_db_connection", "search_vehicles", "RealDictCursor"]


def get_db_connection():
    """Get a database connection using environment variables."""
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "db"),
        port=os.getenv("DB_PORT", "5432"),
        database=os.getenv("DB_NAME", "repairorder"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASS", os.getenv("DB_PASSWORD", "")),
    )


def search_vehicles(term):
    """Search for vehicles by VIN, unit number, or customer name."""
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
