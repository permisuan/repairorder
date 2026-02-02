import sqlite3

# This assumes your database file is named 'repairorder.db' or similar.
# CHECK your 'database.py' or 'main.py' to see the actual filename.
DB_NAME = "repairorder.db" 

def add_warranty_column():
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        print(f"Connecting to {DB_NAME}...")
        
        # SQL Command to add the column
        cursor.execute("ALTER TABLE repair_orders ADD COLUMN is_warranty BOOLEAN DEFAULT 0")
        
        conn.commit()
        print("SUCCESS: Added 'is_warranty' column to 'repair_orders' table.")
        
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("NOTICE: Column 'is_warranty' already exists. No changes made.")
        else:
            print(f"ERROR: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    add_warranty_column()