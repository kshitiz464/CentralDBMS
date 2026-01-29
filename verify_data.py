import sqlite3
import pandas as pd

def verify():
    conn = sqlite3.connect("bookings.db")
    
    # 1. Total Count for 2026-01-27
    print("--- Total Slots for 2026-01-27 ---")
    query = "SELECT COUNT(*) FROM bookings WHERE date='2026-01-27'"
    count = conn.execute(query).fetchone()[0]
    print(f"Total: {count}\n")

    # 2. Stats by Sport
    print("--- Breakdown by Sport ---")
    query = """
    SELECT sport, COUNT(*) as count 
    FROM bookings 
    WHERE date='2026-01-27' 
    GROUP BY sport
    """
    df_sport = pd.read_sql_query(query, conn)
    print(df_sport)
    print("\n")

    # 3. Stats by Court for Billiards
    print("--- Breakdown by Court (Billiards/Snooker) ---")
    query = """
    SELECT court, COUNT(*) as count 
    FROM bookings 
    WHERE date='2026-01-27' AND (sport LIKE '%Pool%' OR sport LIKE '%Snooker%')
    GROUP BY court
    """
    df_bill = pd.read_sql_query(query, conn)
    print(df_bill)
    print("\n")
    
    # 4. Stats by Court for Badminton
    print("--- Breakdown by Court (Badminton) ---")
    query = """
    SELECT court, COUNT(*) as count 
    FROM bookings 
    WHERE date='2026-01-27' AND sport LIKE '%Badminton%'
    GROUP BY court
    """
    df_bad = pd.read_sql_query(query, conn)
    print(df_bad)
    print("\n")

    # 5. Box Cricket
    print("--- Breakdown by Box Cricket ---")
    query = """
    SELECT court, COUNT(*) as count 
    FROM bookings 
    WHERE date='2026-01-27' AND sport LIKE '%Cricket%'
    GROUP BY court
    """
    df_cricket = pd.read_sql_query(query, conn)
    print(df_cricket)

    conn.close()

if __name__ == "__main__":
    verify()
