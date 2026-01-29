import sqlite3

DB_FILE = "bookings.db"

def init_tables():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    tables = ["bookings_hudle", "bookings_playo"]
    
    for table in tables:
        print(f"Creating table: {table}")
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS {table} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                source TEXT NOT NULL,
                sport TEXT DEFAULT "Unknown",
                court TEXT DEFAULT "Unknown",
                status TEXT NOT NULL,
                customer_name TEXT,
                customer_phone TEXT,
                UNIQUE(date, time, sport, court)
            )
        ''')
    
    conn.commit()
    conn.close()
    print("Tables created successfully.")

if __name__ == "__main__":
    init_tables()
