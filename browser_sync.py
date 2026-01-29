import asyncio
import logging
from playwright.async_api import Page
from connection_manager import ConnectionManager
import database
from datetime import datetime, timedelta
from scrapers.playo_scraper import PlayoScraper
from scrapers.hudle_scraper import HudleScraper

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
        self.last_scraped = {} # date_str -> datetime
        self.pending_scrapes = {} # date_str -> asyncio.Future
        self.booking_lock = SafeAsyncLock()
        
        # SOLID Strategy: Scrapers handled by dedicated classes
        self.playo_scraper = PlayoScraper()
        self.hudle_scraper = HudleScraper()
        
        # Legacy sports list (if needed by external callers, though ideally deprecated)
        self.sports = self.playo_scraper.sports 

    async def request_date(self, date_str: str, force: bool = True, limit_to_sports: list = None):
        """
        Queue a specific date for immediate scraping and wait for completion.
        """
        future = None
        if date_str in self.pending_scrapes:
             future = self.pending_scrapes[date_str]
        else:
             future = asyncio.Future()
             self.pending_scrapes[date_str] = future
             await self.scrape_queue.put((date_str, force, limit_to_sports))
             self.wake_event.set()
        
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
                    
                    # 2. Dispatch to Playo
                    if self.cm.playo_tab:
                        # try catch block for safety so one failure doesn't stop the other
                        try:
                            await self.playo_scraper.scrape(self.cm.playo_tab, scrape_requests)
                        except Exception as e:
                            logger.error(f"Playo scraper failed: {e}")
                    
                    # 3. Dispatch to Hudle
                    if self.cm.hudle_tab:
                        try:
                            await self.hudle_scraper.scrape(self.cm.hudle_tab, scrape_requests)
                        except Exception as e:
                            logger.error(f"Hudle scraper failed: {e}")
                    
                    logger.info("Availability sync completed.")
                    
                    # 4. Resolve Futures
                    self._resolve_futures(scrape_requests)

            except Exception as e:
                logger.error(f"Error during sync: {e}")
            
            # Wait for event or timeout
            if not self.wake_event.is_set():
                try:
                    await asyncio.wait_for(self.wake_event.wait(), timeout=600)
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
