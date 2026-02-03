import asyncio
import logging
import random
from playwright.async_api import Page
from connection_manager import ConnectionManager
import database
from datetime import datetime, timedelta
from scrapers.playo_scraper import PlayoScraper
from scrapers.hudle_scraper import HudleScraper
from services.playo_booking_service import PlayoBookingService

logger = logging.getLogger("BrowserSync")

class SafeAsyncLock:
    def __init__(self):
        self._lock = asyncio.Lock()
    async def __aenter__(self):
        await self._lock.acquire()
        return self
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            self._lock.release()
        except RuntimeError:
            pass # Swallow 'Lock is not acquired' error

class BrowserSync:
    def __init__(self, connection_manager: ConnectionManager):
        self.cm = connection_manager
        self.scrape_queue = asyncio.Queue()
        self.wake_event = asyncio.Event()
        self.INTERVAL_SECONDS = 600  # Default background sync interval (10 mins)
        self.last_scraped = {} # date_str -> datetime
        self.pending_scrapes = {} # date_str -> asyncio.Future
        self.booking_lock = SafeAsyncLock()
        
        # SOLID Strategy: Scrapers handled by dedicated classes
        self.playo_scraper = PlayoScraper()
        self.hudle_scraper = HudleScraper()
        
        # Booking Service
        self.playo_booking_service = PlayoBookingService()
        
        # Legacy sports list (if needed by external callers, though ideally deprecated)
        self.sports = self.playo_scraper.sports 

    async def request_date(self, date_str: str, force: bool = True, limit_to_sports: list = None):
        """
        Queue a specific date for immediate scraping and wait for completion.
        """
        future = None
        existing = self.pending_scrapes.get(date_str)
        
        # If force=True OR existing future is already done, create a new one
        if force or existing is None or existing.done():
            future = asyncio.Future()
            self.pending_scrapes[date_str] = future
            await self.scrape_queue.put((date_str, force, limit_to_sports))
            self.wake_event.set()
        else:
            future = existing
        
        logger.info(f"Requested immediate scrape for {date_str} (Force={force}). Waiting...")
        try:
            # Increased timeout to 90s
            await asyncio.wait_for(future, timeout=90) 
            logger.info(f"Scrape completed for {date_str}")
        except asyncio.TimeoutError:
            logger.warning(f"Timeout waiting for scrape of {date_str}")
        except asyncio.CancelledError:
            logger.info(f"Request checks for {date_str} cancelled by client/server.")
            raise 
        except Exception as e:
            logger.error(f"Error awaiting scrape: {e}")
        
    async def sync_availability(self):
        """
        Periodically scrapes the open tabs to find booked slots and updates the DB.
        """
        while True:
            # Clear event at START of loop cycle
            self.wake_event.clear()

            try:
                # 1. Determine Dates to Scrape (Queue + Defaults)
                scrape_requests = await self._get_dates_to_scrape_and_cleanup_queue()
                
                if not scrape_requests:
                     # logger.info("No dates to scrape.")
                     pass
                else:
                    logger.info(f"Starting availability sync for {len(scrape_requests)} dates...")
                    
                    # Define independent tasks with error handling
                    async def _run_playo():
                        if not self.cm.playo_tab: return
                        try:
                            success = await self.playo_scraper.scrape(self.cm.playo_tab, scrape_requests)
                            status = "success" if success else "failed"
                            for req in scrape_requests:
                                database.update_scrape_status("Playo", req['date'], status)
                        except Exception as e:
                            logger.error(f"Playo task failed: {e}")
                            for req in scrape_requests:
                                database.update_scrape_status("Playo", req['date'], "failed", str(e))

                    async def _run_hudle():
                        if not self.cm.hudle_tab: return
                        try:
                            await self.hudle_scraper.scrape(self.cm.hudle_tab, scrape_requests)
                            # Hudle scraper swallowing errors internally usually means partial success or success
                            for req in scrape_requests:
                                database.update_scrape_status("Hudle", req['date'], "success")
                        except Exception as e:
                            logger.error(f"Hudle task failed: {e}")
                            for req in scrape_requests:
                                database.update_scrape_status("Hudle", req['date'], "failed", str(e))

                    # Run in parallel
                    await asyncio.gather(_run_playo(), _run_hudle())
                    
                    logger.info("Availability sync completed.")
                    
                    # 4. Resolve Futures
                    self._resolve_futures(scrape_requests)

            except Exception as e:
                logger.error(f"Error during sync: {e}")
            
            # Wait for event or timeout with Jitter (Stealth)
            if not self.wake_event.is_set():
                jitter = random.randint(-60, 60)
                sleep_time = max(60, self.INTERVAL_SECONDS + jitter)
                logger.info(f"Sleeping for {sleep_time}s (Jitter: {jitter}s) before next sync...")
                
                try:
                    await asyncio.wait_for(self.wake_event.wait(), timeout=sleep_time)
                    logger.info("Sync woken up by event!")
                except asyncio.TimeoutError:
                    pass

    async def _get_dates_to_scrape_and_cleanup_queue(self):
        """
        Drains queue, adds defaults, checks cooldowns, and returns list of request dicts.
        """
        # 1. Drain Queue (Priority)
        raw_requests = {} # date -> (force, limit)
        
        while not self.scrape_queue.empty():
            item = await self.scrape_queue.get()
            if isinstance(item, tuple):
                if len(item) == 3:
                    d, f, l = item
                    raw_requests[d] = (f, l)
                else:
                    d, f = item
                    raw_requests[d] = (f, None)
            else:
                raw_requests[item] = (True, None)

        # 2. Add Defaults (Today, Tomorrow)
        today = datetime.now()
        tomorrow = today + timedelta(days=1)
        today_str = today.strftime("%Y-%m-%d")
        tomorrow_str = tomorrow.strftime("%Y-%m-%d")
        
        if today_str not in raw_requests:
            raw_requests[today_str] = (False, None)
        if tomorrow_str not in raw_requests:
            raw_requests[tomorrow_str] = (False, None)

        # 3. Process & Filter (Cooldowns)
        final_requests = []
        
        # Sort by Force (True first)
        sorted_items = sorted(raw_requests.items(), key=lambda x: (not x[1][0], x[0]))
        
        for date_str, (force, limit_to_sports) in sorted_items:
            # Cooldown check
            if not force:
                last_time = self.last_scraped.get(date_str)
                if last_time and (datetime.now() - last_time).total_seconds() < 600:
                    # Recently scraped, skip unless forced
                    # Check if there is a pending future for this
                    if date_str in self.pending_scrapes and not self.pending_scrapes[date_str].done():
                         self.pending_scrapes[date_str].set_result(True)
                         del self.pending_scrapes[date_str]
                    continue
            
            # Add to list
            final_requests.append({
                "date": date_str,
                "force": force,
                "limit_sports": limit_to_sports
            })
            
            # Update last scraped time eagerly
            self.last_scraped[date_str] = datetime.now()
            
        return final_requests

    def _resolve_futures(self, requests):
        """
        Resolves any pending futures for the processed dates.
        """
        for req in requests:
            date_str = req['date']
            if date_str in self.pending_scrapes:
                if not self.pending_scrapes[date_str].done():
                    self.pending_scrapes[date_str].set_result(True)
                del self.pending_scrapes[date_str]

    async def book_slot(
        self,
        date_str: str,
        time_str: str,
        source: str,  # 'Playo' or 'Hudle'
        sport: str,
        court: str,
        customer_name: str,
        customer_phone: str,
        customer_email: str
    ) -> bool:
        """
        Books a slot on the specified platform.
        Returns True on success, False on failure.
        """
        async with self.booking_lock:
            try:
                if source.lower() == 'playo':
                    if not self.cm.playo_tab:
                        logger.error("Playo tab not available for booking")
                        return False
                    
                    result = await self.playo_booking_service.book_slot(
                        page=self.cm.playo_tab,
                        date_str=date_str,
                        time_str=time_str,
                        sport_name=sport,
                        court_name=court,
                        customer_name=customer_name,
                        customer_phone=customer_phone,
                        customer_email=customer_email
                    )
                    
                    if result and result.get('bookingId'):
                        # Save to local DB
                        from database import Booking
                        booking = Booking(
                            date=date_str,
                            time=time_str,
                            source="Playo",
                            sport=sport,
                            court=court,
                            status="Booked",
                            customer_name=customer_name,
                            customer_phone=customer_phone
                        )
                        database.add_booking(booking)
                        logger.info(f"Booking successful: {result.get('bookingId')}")
                        return True
                    return False
                    
                elif source.lower() == 'hudle':
                    # TODO: Implement Hudle booking
                    logger.warning("Hudle booking not yet implemented")
                    return False
                else:
                    logger.error(f"Unknown booking source: {source}")
                    return False
                    
            except Exception as e:
                logger.error(f"Booking failed: {e}")
                return False

    async def cancel_slot(
        self,
        date_str: str,
        time_str: str,
        source: str,
        sport: str,
        court: str,
        refund_type: int = 1,
        send_sms: bool = True
    ) -> bool:
        """
        Cancels a booking on the specified platform.
        Returns True on success, False on failure.
        """
        async with self.booking_lock:
            try:
                if source.lower() == 'playo':
                    if not self.cm.playo_tab:
                        logger.error("Playo tab not available for cancellation")
                        return False
                    
                    # First, get the booking ID from availability API
                    availability = await self.playo_booking_service.get_availability(
                        self.cm.playo_tab, sport, date_str
                    )
                    
                    # Find the booking ID for this slot
                    booking_id = None
                    slot_time_api = f"{time_str}:00"
                    
                    for court_data in availability.get("data", []):
                        if court in court_data.get("courtName", "") or court_data.get("courtName", "").endswith(court):
                            for slot in court_data.get("slots", []):
                                if slot.get("slotTime") == slot_time_api and slot.get("status") == "Booked":
                                    booking_id = slot.get("bookingId")
                                    break
                            if booking_id:
                                break
                    
                    if not booking_id:
                        logger.error(f"Could not find booking ID for {sport} {court} at {time_str}")
                        return False
                    
                    logger.info(f"Found booking ID: {booking_id} - Cancelling with refund_type={refund_type}, send_sms={send_sms}")
                    
                    # Cancel the booking with options
                    result = await self.playo_booking_service.cancel_booking(
                        self.cm.playo_tab, booking_id, 
                        refund_type=refund_type, 
                        send_sms=send_sms
                    )
                    
                    if result:
                        logger.info(f"Booking {booking_id} cancelled successfully")
                        return True
                    return False
                    
                elif source.lower() == 'hudle':
                    logger.warning("Hudle cancellation not yet implemented")
                    return False
                else:
                    logger.error(f"Unknown cancel source: {source}")
                    return False
                    
            except Exception as e:
                logger.error(f"Cancellation failed: {e}")
                return False
