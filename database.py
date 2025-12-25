import sqlite3
import logging
from datetime import datetime
from pydantic import BaseModel
from typing import List, Optional

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Database")

DB_FILE = "bookings.db"

class Booking(BaseModel):
    id: Optional[int] = None
    date: str
    time: str
    source: str
    sport: str = "Unknown"
    court: str = "Unknown" # New field
    status: str
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # For this migration, we'll try to add the column, or if that fails due to constraint issues
    # (sqlite strictness), we might need to recreate.
    # Given user permission to reset, let's check if 'court' exists.
    try:
        cursor.execute("SELECT court FROM bookings LIMIT 1")
    except sqlite3.OperationalError:
        # Court column missing. Drop and recreate to handle Unique constraint change easily.
        logger.warning("Updating DB schema: Dropping old bookings table.")
        cursor.execute("DROP TABLE IF EXISTS bookings")
        
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bookings (
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
    logger.info("Database initialized.")

def add_booking(booking: Booking):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO bookings (date, time, source, sport, court, status, customer_name, customer_phone)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (booking.date, booking.time, booking.source, booking.sport, booking.court, booking.status, booking.customer_name, booking.customer_phone))
        conn.commit()
        conn.close()
        logger.info(f"Booking added/updated: {booking.date} {booking.time} {booking.sport} ({booking.court})")
        return True
    except Exception as e:
        logger.error(f"Error adding booking: {e}")
        return False

def get_bookings(date: str) -> List[Booking]:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT id, date, time, source, sport, court, status, customer_name, customer_phone FROM bookings WHERE date = ?', (date,))
    rows = cursor.fetchall()
    conn.close()
    
    bookings = []
    for row in rows:
        bookings.append(Booking(
            id=row[0], date=row[1], time=row[2], source=row[3], sport=row[4], court=row[5], status=row[6], customer_name=row[7], customer_phone=row[8]
        ))
    return bookings

def is_slot_available(date: str, time: str, sport: str = "Unknown", court: str = "Unknown") -> bool:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # If court/sport is known, check specific slot.
    # Otherwise check generally (risk of conflict if any slot is booked).
    if sport != "Unknown" and court != "Unknown":
        cursor.execute('SELECT 1 FROM bookings WHERE date = ? AND time = ? AND sport = ? AND court = ? AND status = "Booked"', (date, time, sport, court))
    else:
        cursor.execute('SELECT 1 FROM bookings WHERE date = ? AND time = ? AND status = "Booked"', (date, time))
        
    result = cursor.fetchone()
    conn.close()
    return result is None

# Initialize on module load or manually? Better manually in main.
