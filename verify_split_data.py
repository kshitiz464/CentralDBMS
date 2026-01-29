import sqlite3
from datetime import datetime

DB_FILE = "bookings.db"

def verify_tables():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    tables = ["bookings_hudle", "bookings_playo", "bookings"]
    
    # Check if tables exist
    print("\n--- Table Existence Check ---")
    for table in tables:
        try:
            cursor.execute(f"SELECT count(*) FROM {table}")
            count = cursor.fetchone()[0]
            print(f"Table '{table}' exists. Total Rows: {count}")
        except sqlite3.OperationalError:
            print(f"Table '{table}' DOES NOT EXIST.")

    # Check Data Content for Today/Tomorrow
    today = datetime.now().strftime("%Y-%m-%d")
    
    print(f"\n--- Data Check for {today} ---")
    for table in tables:
        try:
            print(f"\nScanning table: {table}")
            cursor.execute(f"SELECT date, sport, court, count(*) FROM {table} WHERE date >= ? GROUP BY date, sport, court", (today,))
            rows = cursor.fetchall()
            
            if not rows:
                print("  No data found for today/future.")
            else:
                print(f"{'Date':<12} | {'Sport':<25} | {'Court':<20} | {'Count':<5}")
                print("-" * 70)
                for row in rows:
                    print(f"{row[0]:<12} | {row[1]:<25} | {row[2]:<20} | {row[3]:<5}")
                
        except Exception as e:
            print(f"Error scanning {table}: {e}")

    conn.close()

if __name__ == "__main__":
    verify_tables()
