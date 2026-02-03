import logging
import asyncio
import secrets
from fastapi import FastAPI, BackgroundTasks, HTTPException, Depends, status, Request
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
from pydantic import BaseModel
import database
from connection_manager import ConnectionManager
from browser_sync import BrowserSync
from datetime import datetime
import webbrowser

import os
import random
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Main")

# 1. Access Logger Setup
if not os.path.exists("logs"):
    os.makedirs("logs")

access_logger = logging.getLogger("AccessLog")
access_logger.setLevel(logging.INFO)
file_handler = logging.FileHandler("logs/access.log")
formatter = logging.Formatter('%(asctime)s - %(message)s')
file_handler.setFormatter(formatter)
access_logger.addHandler(file_handler)

# 2. SlowAPI Setup
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi import Request

limiter = Limiter(key_func=get_remote_address)

# Global instances
security = HTTPBasic()

# Security Global Lock
IS_SYSTEM_LOCKED = False

def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    server_user = os.getenv("SERVER_USERNAME", "admin_fallback") # Fallback to prevent crash if env missing
    server_pass = os.getenv("SERVER_PASSWORD", "secure_password_fallback")
    
    is_correct_username = secrets.compare_digest(credentials.username, server_user)
    is_correct_password = secrets.compare_digest(credentials.password, server_pass)
    
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
# Apply auth to entire app
app = FastAPI(lifespan=lifespan, dependencies=[Depends(verify_credentials)])

# 3. Apply SlowAPI State
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = datetime.now()
    client_ip = request.client.host
    
    response = await call_next(request)
    
    process_time = (datetime.now() - start_time).total_seconds()
    log_msg = f"Client: {client_ip} | Path: {request.url.path} | Status: {response.status_code} | Time: {process_time:.3f}s"
    
    access_logger.info(log_msg)
    
    # Security Audit for Failures
    if response.status_code in [401, 403]:
        logger.warning(f"SECURITY ALERT: {log_msg}")
        
    return response

# Static Files
# Static files mount moved to end

@app.get("/")
def read_root():
    return FileResponse("dashboard.html")

@app.get("/dashboard")
def read_dashboard():
    return FileResponse("dashboard.html")

@app.get("/favicon.ico")
async def favicon():
    return FileResponse("favicon.svg", media_type="image/svg+xml")

@app.get("/favicon.svg")
async def favicon_svg():
    return FileResponse("favicon.svg", media_type="image/svg+xml")

@app.post("/api/emergency_stop")
@limiter.limit("5/minute")
async def emergency_stop(request: Request):
    """
    Emergency Panic Button. Instantly locks the system.
    Prevents all future bookings and cancellations.
    """
    global IS_SYSTEM_LOCKED
    IS_SYSTEM_LOCKED = True
    logger.critical("!!! EMERGENCY STOP ACTIVATED !!! SYSTEM LOCKED.")
    return {"status": "locked", "message": "System is now LOCKED. Restart server to unlock."}

@app.get("/api/system_status")
async def system_status():
    return {"locked": IS_SYSTEM_LOCKED}

@app.post("/api/book")
@limiter.limit("10/minute")
async def receive_booking(request: Request, booking: BookingRequest):
    """
    Receives booking intent (from Dashboard or WhatsApp).
    """
    if IS_SYSTEM_LOCKED:
        raise HTTPException(status_code=503, detail="System is LOCKED via Emergency Stop")

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

class CancelRequest(BaseModel):
    date: str
    time: str
    source: str
    sport: str
    court: str
    refund_type: int = 1  # 1=As per Policy, 2=Full Refund, 3=No Refund
    send_sms: bool = True

@app.post("/api/cancel")
@limiter.limit("10/minute")
async def cancel_booking(request: Request, cancel: CancelRequest):
    """
    Cancels a booking on the specified platform.
    """
    if IS_SYSTEM_LOCKED:
        raise HTTPException(status_code=503, detail="System is LOCKED via Emergency Stop")
        
    logger.info(f"Received cancel request: {cancel}")
    
    success = await browser_sync.cancel_slot(
        cancel.date, cancel.time, cancel.source, cancel.sport, cancel.court,
        refund_type=cancel.refund_type, send_sms=cancel.send_sms
    )
    
    if success:
        return {"status": "success", "message": "Booking cancelled successfully"}
    else:
        raise HTTPException(status_code=500, detail="Failed to cancel booking on external portal")

@app.get("/dashboard-data")
@limiter.limit("100/minute")
async def get_dashboard_data(request: Request, date: str = None, force: bool = False, source: str = "all"):
    """
    Returns the unified calendar JSON.
    Also triggers on-demand scraping for the requested date.
    force=False respects cooldown (10m).
    source="all"|"hudle"|"playo"
    """
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    
    # Trigger on-demand scrape
    if browser_sync:
        await browser_sync.request_date(date, force=force)

    bookings = []
    
    # Fetch from separated tables
    if source in ["all", "hudle"]:
        bookings.extend(database.get_bookings(date, "bookings_hudle"))
    
    if source in ["all", "playo"]:
        bookings.extend(database.get_bookings(date, "bookings_playo"))
        
    scrape_status = database.get_scrape_status(date)
    return {
        "date": date,
        "bookings": [b.model_dump() for b in bookings],
        "scrape_status": scrape_status
    }

# Fallback for Static Files (Must be last)
app.mount("/", StaticFiles(directory=".", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    # Open dashboard in browser on startup
    webbrowser.open("http://127.0.0.1:8000/")
    uvicorn.run(app, host="127.0.0.1", port=8000)
