import sqlite3
import logging
from datetime import datetime
from pydantic import BaseModel
from typing import List, Optional

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Database")

from contextlib import contextmanager

# ... (Logging setup remains) ...
# logging.basicConfig(...)
# logger = logging.getLogger("Database")

DB_FILE = "bookings.db"

class Booking(BaseModel):
    id: Optional[int] = None
    date: str
    time: str
    source: str
    sport: str = "Unknown"
    court: str = "Unknown"
    status: str
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None

@contextmanager
def get_db_connection():
    """
    Context Manager for Database Connections.
    Ensures transactions are committed on success and rolled back on failure.
    Enforces ACID properties by isolating transactions.
    """
    conn = sqlite3.connect(DB_FILE)
    # Enable Foreign Keys & other pragmas if needed
    conn.execute("PRAGMA foreign_keys = ON;") 
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Database transaction rolled back due to error: {e}")
        raise e
    finally:
        conn.close()

def init_db():
    try:
        # Use a raw connection for init to handle specific PRAGMAs
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Enable Write-Ahead Logging for concurrency (ACID - Durability/Performance)
        cursor.execute("PRAGMA journal_mode=WAL;")
        
        # Check if table exists to decide on migration (Simplified logic here)
        # Check if table exists to decide on migration (Simplified logic here)
        try:
            cursor.execute("SELECT court FROM bookings LIMIT 1")
        except sqlite3.OperationalError:
            pass # Table doesn't exist or schema mismatch, create below will handle IF NOT EXISTS
            # In a real app, we would use Alembic for migrations.


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
        
        # Create Separate Tables for Debugging
        for table in ["bookings_hudle", "bookings_playo"]:
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

        # Create Status Tracking Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS scrape_status (
                source TEXT NOT NULL,
                date TEXT NOT NULL,
                last_updated TEXT,
                status TEXT NOT NULL, -- 'success', 'failed'
                details TEXT,
                PRIMARY KEY (source, date)
            )
        ''')

        conn.commit()
        conn.close()
        logger.info("Database initialized (WAL Mode + Split Tables + Status Tracking).")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")

def update_scrape_status(source: str, date: str, status: str, details: str = None):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute('''
                INSERT OR REPLACE INTO scrape_status (source, date, last_updated, status, details)
                VALUES (?, ?, ?, ?, ?)
            ''', (source, date, timestamp, status, details))
    except Exception as e:
        logger.error(f"Error updating scrape status: {e}")

def get_scrape_status(date: str) -> dict:
    try:
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM scrape_status WHERE date = ?', (date,))
            rows = cursor.fetchall()
            return {row['source']: dict(row) for row in rows}
    except Exception as e:
        logger.error(f"Error getting scrape status: {e}")
        return {}

def add_booking(booking: Booking):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO bookings (date, time, source, sport, court, status, customer_name, customer_phone)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (booking.date, booking.time, booking.source, booking.sport, booking.court, booking.status, booking.customer_name, booking.customer_phone))
            # Commit happens automatically on exit
        logger.info(f"Booking added/updated: {booking.date} {booking.time} {booking.sport} ({booking.court})")
        return True
    except Exception as e:
        logger.error(f"Error adding booking: {e}")
        return False

def get_bookings(date: str, table_name: str = "bookings") -> List[Booking]:
    if table_name not in ["bookings", "bookings_hudle", "bookings_playo"]:
        table_name = "bookings"
        
    try:
        # Read-only, so we can just use connect or the context manager (commit is harmless)
        conn = sqlite3.connect(DB_FILE) 
        cursor = conn.cursor()
        cursor.execute(f'SELECT id, date, time, source, sport, court, status, customer_name, customer_phone FROM {table_name} WHERE date = ?', (date,))
        rows = cursor.fetchall()
        conn.close()
        
        bookings = []
        for row in rows:
            bookings.append(Booking(
                id=row[0], date=row[1], time=row[2], source=row[3], sport=row[4], court=row[5], status=row[6], customer_name=row[7], customer_phone=row[8]
            ))
        return bookings
    except Exception as e:
        logger.error(f"Error fetching bookings: {e}")
        return []

def is_slot_available(date: str, time: str, sport: str = "Unknown", court: str = "Unknown") -> bool:
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        if sport != "Unknown" and court != "Unknown":
            cursor.execute('SELECT 1 FROM bookings WHERE date = ? AND time = ? AND sport = ? AND court = ? AND status = "Booked"', (date, time, sport, court))
        else:
            cursor.execute('SELECT 1 FROM bookings WHERE date = ? AND time = ? AND status = "Booked"', (date, time))
            
        result = cursor.fetchone()
        conn.close()
        return result is None
    except Exception as e:
        logger.error(f"Error checking availability: {e}")
        return False

# --- GENERIC SAVE (Legacy/Main) ---
async def save_booked_slots(slots: List[dict]):
    await _save_slots_to_table(slots, "bookings")

# --- SPECIFIC SAVE (New) ---
async def save_booked_slots_hudle(slots: List[dict]):
    await _save_slots_to_table(slots, "bookings_hudle")

async def save_booked_slots_playo(slots: List[dict]):
    await _save_slots_to_table(slots, "bookings_playo")

def delete_slots_for_date_sport(table_name: str, date: str, sport: str):
    """
    Delete all slots for a specific date and sport.
    This ensures stale data is cleared before inserting fresh data.
    """
    if table_name not in ["bookings", "bookings_hudle", "bookings_playo"]:
        logger.error(f"Invalid table name: {table_name}")
        return
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f'DELETE FROM {table_name} WHERE date = ? AND sport = ?', (date, sport))
            deleted = cursor.rowcount
            if deleted > 0:
                logger.debug(f"Cleared {deleted} old slots from {table_name} for {date} {sport}")
    except Exception as e:
        logger.error(f"Error clearing slots: {e}")

async def _save_slots_to_table(slots: List[dict], table_name: str):
    """
    Internal helper to bulk upsert slots to a specific table.
    Clears old data for the date/sport first to ensure fresh data.
    Uses Transaction to ensure all slots are saved or none (Atomicity).
    """
    if not slots:
        return
    
    # Group slots by date/sport to clear old data
    date_sport_pairs = set()
    for s in slots:
        date_sport_pairs.add((s.get("date"), s.get("sport")))
    
    # Clear old data for each date/sport combination
    for date, sport in date_sport_pairs:
        if date and sport:
            delete_slots_for_date_sport(table_name, date, sport)
        
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            data_tuples = []
            for s in slots:
                t_date = s.get("date")
                t_time = s.get("time")
                t_source = s.get("source", "Unknown")
                t_sport = s.get("sport", "Unknown")
                t_court = s.get("court", "Unknown")
                t_status = s.get("status", "Available")
                
                data_tuples.append((t_date, t_time, t_source, t_sport, t_court, t_status))

            cursor.executemany(f'''
                INSERT OR REPLACE INTO {table_name} (date, time, source, sport, court, status)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', data_tuples)
            # Automatic Commit
            
    except Exception as e:
        logger.error(f"Error in _save_slots_to_table ({table_name}): {e}")

