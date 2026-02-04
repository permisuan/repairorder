#!/usr/bin/env python3
"""
Migration script to consolidate roles.
Converts 'manager' users to 'admin'.
Safe to run multiple times.
"""

import os
import psycopg2


def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        database=os.getenv("DB_NAME", "repairorder"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASS", os.getenv("DB_PASSWORD", "")),
    )


def run_migration():
    print("Starting role consolidation migration...")
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Convert manager -> admin
        cur.execute("UPDATE users SET role = 'admin' WHERE role = 'manager'")
        converted = cur.rowcount
        print(f"  Converted {converted} 'manager' users to 'admin'")
        
        conn.commit()
        print("Migration completed successfully!")
        
    except Exception as e:
        conn.rollback()
        print(f"Migration failed: {e}")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    run_migration()
