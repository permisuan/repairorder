"""Database migration utilities for PostgreSQL."""
import database as db


def add_warranty_column():
    """Add is_warranty column to repair_orders table if it doesn't exist."""
    try:
        conn = db.get_db_connection()
        cursor = conn.cursor()

        print("Connecting to PostgreSQL database...")

        # PostgreSQL syntax to add column if it doesn't exist
        cursor.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'repair_orders' AND column_name = 'is_warranty'
                ) THEN
                    ALTER TABLE repair_orders ADD COLUMN is_warranty BOOLEAN DEFAULT FALSE;
                END IF;
            END
            $$;
        """)

        conn.commit()
        print("SUCCESS: Ensured 'is_warranty' column exists in 'repair_orders' table.")

    except Exception as e:
        print(f"ERROR: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    add_warranty_column()