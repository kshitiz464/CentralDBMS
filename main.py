import logging
import asyncio
import secrets
from fastapi import FastAPI, BackgroundTasks, HTTPException, Depends, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
from pydantic import BaseModel
import database
from connection_manager import ConnectionManager
from browser_sync import BrowserSync
from datetime import datetime
import webbrowser

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Main")

# Global instances
security = HTTPBasic()

def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    is_correct_username = secrets.compare_digest(credentials.username, "playshireServer")
    is_correct_password = secrets.compare_digest(credentials.password, "ServerPlayshire@1")
    
    if not (is_correct_username and is_correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

connection_manager = ConnectionManager()
browser_sync = BrowserSync(connection_manager)

# Pydantic models
class BookingRequest(BaseModel):
    date: str
    time: str
    source: str = "WhatsApp"
    sport: str = "Unknown"
    court: str = "Unknown"
    customer_name: str = "Unknown"
    customer_phone: str = "Unknown"
    customer_email: str = "Unknown"

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("System starting up...")
    database.init_db()
    
    # Initialize Connection Manager (Check browser, tabs, login)
    if not await connection_manager.initialize():
        logger.error("Initialization failed. Please check logs/popups.")
        # We might want to exit or keep running in broken state?
        # For now, we keep running but sync won't work.
    
    # Start background sync task
    asyncio.create_task(browser_sync.sync_availability())
    
    yield
    
    # Shutdown
    logger.info("System shutting down...")
    await connection_manager.close()

# Apply auth to entire app
app = FastAPI(lifespan=lifespan, dependencies=[Depends(verify_credentials)])

@app.get("/")
def read_root():
    return FileResponse("dashboard.html")

@app.get("/dashboard")
def read_dashboard():
    return FileResponse("dashboard.html")

@app.post("/api/book")
async def receive_booking(booking: BookingRequest):
    """
    Receives booking intent (from Dashboard or WhatsApp).
    Checks local DB for availability.
    If free -> Calls BrowserSync.book_slot() -> Returns success.
    """
    logger.info(f"Received booking request: {booking}")
    
    # 1. Check availability in local DB (Specific Court)
    if not database.is_slot_available(booking.date, booking.time, booking.sport, booking.court):
        raise HTTPException(status_code=409, detail="Slot already booked")

    # 2. Trigger Browser Automation to Block Slot
    success = await browser_sync.book_slot(
        booking.date, booking.time, booking.source, booking.sport, booking.court,
        booking.customer_name, booking.customer_phone, booking.customer_email
    )
    
    if success:
        return {"status": "success", "message": "Slot blocked successfully"}
    else:
        raise HTTPException(status_code=500, detail="Failed to block slot on external portal")

@app.get("/dashboard-data")
async def get_dashboard_data(date: str = None, force: bool = False):
    """
    Returns the unified calendar JSON.
    Also triggers on-demand scraping for the requested date.
    force=False respects cooldown (10m).
    """
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    
    # Trigger on-demand scrape
    if browser_sync:
        await browser_sync.request_date(date, force=force)

    bookings = database.get_bookings(date)
    return {
        "date": date,
        "bookings": [b.dict() for b in bookings]
    }

if __name__ == "__main__":
    import uvicorn
    # Open dashboard in browser on startup
    webbrowser.open("http://127.0.0.1:8000/")
    uvicorn.run(app, host="127.0.0.1", port=8000)
