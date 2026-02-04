#!/usr/bin/env python3
"""
Migration script to add customer management enhancements.
Adds billing_address to customers, and creates customer_emails/customer_phones tables.
Safe to run multiple times.
"""

import os
import sys

import psycopg2


def get_db_connection():
    """Get a database connection using environment variables."""
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        database=os.getenv("DB_NAME", "repairorder"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASS", os.getenv("DB_PASSWORD", "")),
    )


def run_migration():
    print("Starting customer management migration...")
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Add billing_address column to customers if it doesn't exist
        print("  Adding billing_address column to customers...")
        cur.execute("""
            ALTER TABLE customers 
            ADD COLUMN IF NOT EXISTS billing_address TEXT
        """)
        
        # Create customer_emails table
        print("  Creating customer_emails table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS customer_emails (
                id SERIAL PRIMARY KEY,
                customer_id INTEGER REFERENCES customers(id) ON DELETE CASCADE,
                email VARCHAR(255) NOT NULL,
                is_primary BOOLEAN DEFAULT FALSE
            )
        """)
        
        # Create customer_phones table
        print("  Creating customer_phones table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS customer_phones (
                id SERIAL PRIMARY KEY,
                customer_id INTEGER REFERENCES customers(id) ON DELETE CASCADE,
                phone VARCHAR(50) NOT NULL,
                is_primary BOOLEAN DEFAULT FALSE
            )
        """)
        
        conn.commit()
        print("Migration completed successfully!")
        
    except Exception as e:
        conn.rollback()
        print(f"Migration failed: {e}")
        sys.exit(1)
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    run_migration()
