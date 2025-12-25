import asyncio
import logging
from playwright.async_api import Page
from connection_manager import ConnectionManager
import database
from datetime import datetime

logger = logging.getLogger("BrowserSync")

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
        # Use SafeAsyncLock to prevent crashes on release error
        self.booking_lock = SafeAsyncLock()
        self.sports = [
            {"name": "Badminton Wooden+Mat", "value": "7162"},
            {"name": "Table Tennis", "value": "7163"},
            {"name": "Snooker", "value": "7164"},
            {"name": "Pool", "value": "7165"},
            {"name": "Box Cricket 6 a Side", "value": "9073"},
            {"name": "Football 5 a Side", "value": "9074"}
        ]

    async def request_date(self, date_str: str, force: bool = True, limit_to_sports: list = None):
        """
        Queue a specific date for immediate scraping and wait for completion.
        date_str: YYYY-MM-DD
        force: If True, ignores cooldown. If False, returns immediately if recently scraped.
        limit_to_sports: Optional list of sport names to scrape ONLY.
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
            # Increased timeout to 90s to ensure it waits for full scrape even if queue is busy
            await asyncio.wait_for(future, timeout=90) 
            logger.info(f"Scrape completed for {date_str}")
        except asyncio.TimeoutError:
            logger.warning(f"Timeout waiting for scrape of {date_str}")
        except asyncio.CancelledError:
            logger.info(f"Request checks for {date_str} cancelled by client/server.")
            # We don't delete from pending_scrapes here because the scrape is likely still running 
            # and we want it to finish and clean itself up.
            raise 
        except Exception as e:
            logger.error(f"Error awaiting scrape: {e}")
        
        # We generally do NOT delete here, because if it timed out, the scrape is still running 
        # and checking the map prevents duplicate jobs. 
        # The consumer (_scrape_playo) is responsible for deletion.

    async def sync_availability(self):
        """
        Periodically scrapes the open tabs to find booked slots and updates the DB.
        """
        while True:
            # Clear event at START of loop cycle, so if a request comes in DURING scrape,
            # it will set the event and we will loop again immediately after finishing.
            self.wake_event.clear()

            try:
                logger.info("Starting availability sync...")
                
                if self.cm.playo_tab:
                    await self._scrape_playo(self.cm.playo_tab)
                
                if self.cm.hudle_tab:
                    await self._scrape_hudle(self.cm.hudle_tab)
                
                logger.info("Availability sync completed.")
            except Exception as e:
                logger.error(f"Error during sync: {e}")
            
            # Wait for event or timeout (10 minutes default)
            # self.wake_event.clear() REMOVED FROM HERE
            if not self.wake_event.is_set():
                try:
                    # Wait 10 mins (600s) or until woken up by request_date
                    await asyncio.wait_for(self.wake_event.wait(), timeout=600)
                    logger.info("Sync woken up by event!")
                except asyncio.TimeoutError:
                    pass # Timeout reached, run normal loop

    async def _scrape_playo(self, page: Page):
        
        from datetime import timedelta

        try:
            logger.info("Scraping Playo loop started...")
            
            # --- ENSURE ON CALENDAR PAGE ---
            try:
                # Check for date picker VISIBILITY (it might exist in DOM but be hidden on Home page)
                date_picker = page.locator('.react-datepicker__input-container input')
                is_visible = False
                if await date_picker.count() > 0:
                    is_visible = await date_picker.first.is_visible()
                
                if not is_visible:
                    logger.info("Date picker not visible. Attempting to navigate to Calendar page...")
                    
                    # Target the Clickable DIV container, not just the span
                    calendar_btn = page.locator('div[role="button"]', has_text="Calendar")
                    
                    # Check if visible immediately
                    if await calendar_btn.count() > 0 and await calendar_btn.first.is_visible():
                         await calendar_btn.first.click()
                         logger.info("Clicked Calendar button directly.")
                         await page.wait_for_timeout(3000)
                    else:
                        # Might be collapsed under "Schedule"
                        logger.info("Calendar button hidden/missing. Trying to expand 'Schedule'...")
                        schedule_btn = page.locator('div[role="button"]', has_text="Schedule")
                        if await schedule_btn.count() > 0:
                             await schedule_btn.first.click()
                             await page.wait_for_timeout(1000) # Wait for animation
                             
                             # Try clicking Calendar again
                             if await calendar_btn.count() > 0:
                                 await calendar_btn.first.click()
                                 logger.info("Clicked Calendar button after expanding Schedule.")
                                 await page.wait_for_timeout(3000)
                             else:
                                 logger.error("Calendar button still not found after expanding Schedule.")
                        else:
                             logger.error("Could not find 'Schedule' parent menu.")
                             
            except Exception as e:
                logger.error(f"Error navigating to Calendar: {e}")
            # -------------------------------
            
            # 1. Determine dates to scrape
            # Priority Dates (Force Scrape) + Default Dates (Cooldown check)
            dates_to_process = {} # date_str -> (force_bool, limit_to_sports_list)
            
            # Drain queue
            while not self.scrape_queue.empty():
                item = await self.scrape_queue.get()
                if isinstance(item, tuple):
                    if len(item) == 3:
                        d, f, l = item
                        dates_to_process[d] = (f, l)
                    else:
                        d, f = item
                        dates_to_process[d] = (f, None)
                else:
                    dates_to_process[item] = (True, None) # Force fallback
            
            # Add Defaults (Today, Tomorrow) if not present
            today = datetime.now()
            tomorrow = today + timedelta(days=1)
            
            if today.strftime("%Y-%m-%d") not in dates_to_process:
                dates_to_process[today.strftime("%Y-%m-%d")] = (False, None)
            if tomorrow.strftime("%Y-%m-%d") not in dates_to_process:
                dates_to_process[tomorrow.strftime("%Y-%m-%d")] = (False, None)
                
            # SORT BY PRIORITY (Force=True first), then Date
            # Value is (force, limit). sort key uses force (val[0]).
            sorted_dates = sorted(dates_to_process.items(), key=lambda x: (not x[1][0], x[0]))
            
            for date_str, (force, limit_to_sports) in sorted_dates:
                # Cooldown Check (10 mins)
                if not force:
                    last_time = self.last_scraped.get(date_str)
                    if last_time and (datetime.now() - last_time).total_seconds() < 600:
                        logger.info(f"Skipping {date_str} (Recently scraped at {last_time.strftime('%H:%M:%S')})")
                        # RESOLVE FUTURE IF SKIPPED
                        if date_str in self.pending_scrapes:
                            if not self.pending_scrapes[date_str].done():
                                self.pending_scrapes[date_str].set_result(True)
                            del self.pending_scrapes[date_str]
                        continue

                # Connectivity Check
                if not await page.evaluate("navigator.onLine"):
                    logger.warning("Browser reports offline. Skipping scrape to avoid stale data.")
                    # Resolve future with False
                    if date_str in self.pending_scrapes:
                        if not self.pending_scrapes[date_str].done():
                            self.pending_scrapes[date_str].set_result(False)
                        del self.pending_scrapes[date_str]
                    continue



                # Format for Playo: DD - Mon - YY
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    target_date_str = dt.strftime("%d - %b - %y")
                except ValueError:
                    logger.error(f"Invalid date format in queue: {date_str}")
                    continue
                
                # Change Date via Playwright Native Methods (More Reliable)
                try:
                    # Use .first to resolve strict mode violation (Matches document.querySelector behavior)
                    all_date_inputs = page.locator('.react-datepicker__input-container input')
                    if await all_date_inputs.count() > 0:
                        date_input = all_date_inputs.first
                        await date_input.click()
                        # Clear existing (select all + backspace usually works, or fill automatically clears)
                        await date_input.fill(target_date_str)
                        await page.wait_for_timeout(500) # Short pause
                        await date_input.press('Enter')
                        await page.wait_for_timeout(500)
                        # Tab out to ensure blur/commit
                        await date_input.press('Tab')
                    else:
                        logger.error("Date input not found!")
                        continue
                except Exception as e:
                    logger.error(f"Failed to change date via Playwright: {e}")
                    continue
                
                # Wait + Verify loop
                scrape_date_confirmed = False
                for _ in range(3): # Retry verification 3 times
                    await page.wait_for_timeout(2000) 
                    
                    # Check if date on page matches target
                    current_page_date = await page.evaluate('''() => {
                        const input = document.querySelector('.react-datepicker__input-container input');
                        return input ? input.value : null;
                    }''')
                    
                    # Normalize strings for comparison (remove spaces)
                    if current_page_date and current_page_date.replace(" ", "") == target_date_str.replace(" ", ""):
                        scrape_date_confirmed = True
                        break
                    else:
                        logger.warning(f"Date mismatch retry: Page has {current_page_date}, wanted {target_date_str}")
                        # Retry setting if failed? No, just wait longer or retry loop.
                
                if not scrape_date_confirmed:
                    logger.error(f"Failed to switch date to {target_date_str} after verify. Skipping.")
                    continue

                # SCRAPE SPORTS IF DATE CHANGED SUCCESSFULLY
                # Filter sports if limited
                sports_to_scrape = self.sports
                if limit_to_sports:
                    # Filter by name (case insensitive)
                    limit_lower = [s.lower() for s in limit_to_sports]
                    sports_to_scrape = [s for s in self.sports if s['name'].lower() in limit_lower]
                    if not sports_to_scrape:
                        logger.warning(f"Limit sports {limit_to_sports} yielded no matches. Scraping ALL.")
                        sports_to_scrape = self.sports

                for sport in sports_to_scrape:
                    # logger.info(f"Scraping sport: {sport['name']} for {target_date_str}")
                    
                    # 1. Select Sport
                    select_selector = "select[id*='SelectField']"
                    try:
                        await page.select_option(select_selector, value=sport['value']) 
                        await page.wait_for_timeout(1500) 
                         # Reduced wait slightly to speed up multi-date scraping
                    except Exception as e:
                        logger.error(f"Failed to select sport {sport['name']}: {e}")
                        continue

                    # 2. Extract Slots (Reuse existing JS)
                    # Pass sport name to JS for conditional renaming
                    sport_name_js = sport['name'] 
                    
                    evaluation_result = await page.evaluate(''' (sportName) => {
                        const debug = [];
                        const results = [];
                        const timePattern = /(\\d{1,2}):(\\d{2})\\s*-\\s*\\d{1,2}:\\d{2}\\s*(AM|PM)/i;
                        
                        // DATE EXTRACTION
                        let extractedDate = null;
                        const dateInput = document.querySelector('.react-datepicker__input-container input');
                        if (dateInput) extractedDate = dateInput.value;
                        debug.push("Extracted Date via select: " + extractedDate);

                        if (!extractedDate) {
                             // Try all inputs
                             const inputs = Array.from(document.querySelectorAll('input'));
                             const dateMatch = inputs.find(i => /\\d{1,2}\\s*-\\s*[a-zA-Z]{3}\\s*-\\s*\\d{2}/.test(i.value));
                             if (dateMatch) extractedDate = dateMatch.value;
                             debug.push("Extracted Date via fallback: " + extractedDate);
                        }

                        // FIND CORRECT TABLE OR ROWS
                        // FIND CORRECT TABLE OR ROWS
                        let targetTable = null;
                        const tables = Array.from(document.querySelectorAll('table'));
                        debug.push("Found tables: " + tables.length);
                        
                        let rows = [];
                        let headerMap = [];

                        // Strategy 1: Standard Table Tag
                        if (tables.length > 0) {
                            for (const tbl of tables) {
                                const headerText = tbl.innerText.toLowerCase();
                                if (headerText.includes("time") && (headerText.includes("court") || headerText.includes("table") || headerText.includes("turf") || headerText.includes("ground"))) {
                                    targetTable = tbl;
                                    break;
                                }
                            }
                            if (!targetTable) {
                                debug.push("No specific header match in tables, picking largest");
                                targetTable = tables.sort((a, b) => b.querySelectorAll('tr').length - a.querySelectorAll('tr').length)[0];
                            }
                        }

                        if (targetTable) {
                            const tableHeaderRow = targetTable.querySelector('thead tr') || targetTable.querySelector('tr');
                            if (tableHeaderRow) {
                                headerMap = Array.from(tableHeaderRow.children).map((c, i) => ({index: i, name: c.innerText.trim()}));
                            }
                            rows = Array.from(targetTable.querySelectorAll('tbody tr'));
                            if (rows.length === 0) rows = Array.from(targetTable.querySelectorAll('tr'));
                        } else {
                            // Strategy 2: All TR tags
                            const allTrs = Array.from(document.querySelectorAll('tr'));
                            if (allTrs.length > 0) {
                                debug.push("No tables found, using all TRs: " + allTrs.length);
                                rows = allTrs;
                                
                                // Find Header Row
                                const headerRow = rows.find(r => {
                                    const txt = r.innerText.toLowerCase();
                                    return txt.includes('time') && (txt.includes('court') || txt.includes('table') || txt.includes('turf'));
                                });
                                
                                if (headerRow) {
                                    headerMap = Array.from(headerRow.children).map((c, i) => ({index: i, name: c.innerText.trim()}));
                                } else {
                                     // Infer headers from first row if valid
                                     if(rows.length > 0) headerMap = Array.from(rows[0].children).map((c, i) => ({index: i, name: c.innerText.trim()}));
                                }
                            } else {
                                // Strategy 3: Text-based/Div-based Row Detection (Last Resort)
                                debug.push("No TRs found. Attempting Time-Pattern Search.");
                                
                                // Relaxed Regex to handle "10:00 AM - 11:00 AM" (AM possibly in middle)
                                const candidates = Array.from(document.querySelectorAll('div, span, p, td, li')); 
                                // Matches: "10:00" or "10:00 AM" followed by "-" followed by "11:00 AM"
                                const timePatternFragment = /(\d{1,2}:\d{2})(?:\s*[a-zA-Z]{2})?\s*-\s*(\d{1,2}:\d{2})\s*([a-zA-Z]{2})/i;
                                
                                const timeCells = candidates.filter(el => timePatternFragment.test(el.innerText) && el.children.length === 0); // Leaf nodes preferred
                                debug.push("Found candidate time cells: " + timeCells.length);
                                
                                if (timeCells.length > 0) {
                                    // Deduplicate parents to get unique rows
                                    const potentialRows = new Set();
                                    timeCells.forEach(tc => {
                                        if(tc.parentElement) potentialRows.add(tc.parentElement);
                                    });
                                    rows = Array.from(potentialRows);
                                    debug.push("Inferred Rows from Time Cells: " + rows.length);
                                    
                                    // Try to find a header row (Best Effort)
                                    // Look for "Time" in the siblings of the first time cell's parent?
                                    // Or just use the first row of "rows" as structure template if inconsistent?
                                }
                            }
                        }
                        
                        debug.push("Header Map: " + JSON.stringify(headerMap));
                        debug.push("Processing rows: " + rows.length);

                        function to24h(h, m, amp) {
                            let hour = parseInt(h);
                            const min = m;
                            const ampm = amp.toUpperCase();
                            if (ampm === "PM" && hour < 12) hour += 12;
                            if (ampm === "AM" && hour === 12) hour = 0;
                            return hour.toString().padStart(2, '0') + ":" + min;
                        }
                        
                        const capturePattern = /(\d{1,2}):(\d{2})(?:\s*[a-zA-Z]{2})?\s*-\s*(\d{1,2}):(\d{2})\s*([a-zA-Z]{2})/i;
                        
                        rows.forEach(row => {
                             if (row.children.length > 0) {
                                 // Smart Time Detection in Row
                                 let match = null;
                                 let timeColIndex = -1;
                                 const cells = Array.from(row.children);
                                 
                                 for(let i=0; i<Math.min(3, cells.length); i++) {
                                     const txt = cells[i].textContent.trim();
                                     match = txt.match(capturePattern);
                                     if(match) {
                                         timeColIndex = i;
                                         break;
                                     }
                                 }

                                 if (match) {
                                     const fullStr = match[0];
                                     const parts = fullStr.split('-');
                                     const startPart = parts[0].trim();
                                     const endPart = parts[1].trim();
                                     
                                     const startMatch = startPart.match(/(\d{1,2}):(\d{2})/);
                                     const endMatch = endPart.match(/([a-zA-Z]{2})/); 
                                     
                                     if (startMatch && endMatch) {
                                         const h = startMatch[1];
                                         const m = startMatch[2];
                                         let effectiveAMPM = endMatch[1];
                                         const startAMPM = startPart.match(/([a-zA-Z]{2})/);
                                         if(startAMPM) effectiveAMPM = startAMPM[1];

                                         const startTime24 = to24h(h, m, effectiveAMPM);
                                         
                                         cells.forEach((cell, idx) => {
                                             if (idx <= timeColIndex) return;

                                             let colName = "Unknown";
                                             let isTrustedCourtCol = false;

                                             // A. Header Check
                                             if (headerMap.length > 0) {
                                                 const h = headerMap.find(hm => hm.index === idx);
                                                 if (h) {
                                                     colName = h.name;
                                                     const lower = colName.toLowerCase();
                                                     if (lower.includes('court') || lower.includes('table') || lower.includes('turf')) {
                                                         isTrustedCourtCol = true;
                                                         
                                                         // --- RENAMING LOGIC ---
                                                         if (sportName.includes("Snooker") || sportName.includes("Pool") || sportName.includes("Table Tennis")) {
                                                             colName = colName.replace(/Court/i, "Table");
                                                         } else if (sportName.includes("Football") || sportName.includes("Cricket")) {
                                                             colName = colName.replace(/Court/i, "Turf");
                                                             colName = colName.replace(/Table/i, "Turf"); // Fix if Football accidentally says "Table"
                                                         }
                                                         // ----------------------
                                                     }
                                                 }
                                             }

                                              // B. Content Check
                                              const buttons = Array.from(cell.querySelectorAll('button, [role="button"], [class*="btn-"], .btn'));
                                              const btnTexts = buttons.map(b => b.textContent.trim().toLowerCase());
                                             const cellText = cell.innerText.trim();
                                             const lowerText = cellText.toLowerCase();

                                             const hasBook = btnTexts.some(t => t.includes('book') || t.includes('block') || t.includes('₹') || (/\d/.test(t) && !t.includes('locked')));
                                             const hasLocked = btnTexts.some(t => t.includes('locked'));
                                             
                                             let status = null;

                                             if (hasBook) {
                                                 status = "Available";
                                                 isTrustedCourtCol = true; 
                                             } else if (hasLocked) {
                                                 status = "Locked";
                                                 isTrustedCourtCol = true;
                                             } else if (lowerText === 'booked' || lowerText === 'full' || lowerText === 'sold out' || lowerText.includes('locked')) {
                                                 status = "Booked";
                                                 isTrustedCourtCol = true;
                                             } else if (cellText.length > 2 && isTrustedCourtCol) {
                                                 // If header says 'Court', any non-empty text (e.g. 'Vinay...') is a booking
                                                 status = "Booked";
                                             } else if (cellText.length > 3 && !isTrustedCourtCol) {
                                                  // Name detection fallback: "Vinay for ..."
                                                  // If text is long enough and we are in a likely grid
                                                  // We treat as Booked, but only if it doesn't look like a price alone?
                                                  // Risk: "₹ 500" -> Booked?
                                                  if (!cellText.includes('₹') || cellText.length > 8) {
                                                      status = "Booked";
                                                  }
                                             }

                                             if (status) {
                                                 if (colName === "Unknown" || !colName) colName = "Court/Table " + (idx - timeColIndex);
                                                 
                                                 results.push({
                                                     time: startTime24, 
                                                     court: colName,
                                                     status: status,
                                                     date: extractedDate 
                                                 });
                                             }
                                         });
                                     }
                                  }
                             }
                        });


                        
                        // Deduplicate
                        const unique = [];
                        const seen = new Set();
                        results.forEach(r => {
                            const key = r.time + "|" + r.court;
                            if(!seen.has(key)) {
                                unique.push(r);
                                seen.add(key);
                            }
                        });
                        return { results: unique, debug: debug };
                    }''', sport_name_js)

                    scraped_data = evaluation_result.get('results', [])
                    debug_logs = evaluation_result.get('debug', [])
                    
                    # Log JS debug if empty
                    if not scraped_data:
                        logger.warning(f"JS Debug for {target_date_str}: {debug_logs}")

                    # Helper to parse "15 - Dec - 25" -> "2025-12-15"
                    def parse_playo_date(date_str):
                        if not date_str: return None
                        try:
                            clean = date_str.replace(" ", "")
                            dt = datetime.strptime(clean, "%d-%b-%y")
                            return dt.strftime("%Y-%m-%d")
                        except ValueError:
                             return None

                    page_date_parsed = None
                    if scraped_data: 
                         page_date_parsed = parse_playo_date(scraped_data[0].get('date'))
                    
                    # Verify we got data for the Target Date
                    if page_date_parsed and page_date_parsed != date_str:
                         logger.warning(f"Mismatch: Targeted {date_str} but scraped {page_date_parsed}. JS Debug: {debug_logs}")
                    
                    if not page_date_parsed:
                        # logger.warning(f"No date found in scraped data. Skipping.")
                        continue # No date, no save

                    # Save to DB
                    for slot in scraped_data:
                        database.add_booking(database.Booking(
                            date=page_date_parsed,
                            time=slot['time'],
                            source="Playo",
                            sport=sport['name'],
                            court=slot['court'],
                            status=slot['status']
                        ))
                
                # Update Last Scraped Time for this date
                self.last_scraped[date_str] = datetime.now()
                logger.info(f"Finished scraping {date_str}")

                # Resolve pending request
                if date_str in self.pending_scrapes:
                    if not self.pending_scrapes[date_str].done():
                        self.pending_scrapes[date_str].set_result(True)
                    # Correctly clean up the map entry now that work is done
                    del self.pending_scrapes[date_str]
                    
        except Exception as e:
            logger.error(f"Error scraping Playo loop: {e}")
                
        except Exception as e:
            logger.error(f"Error scraping Playo: {e}")

    async def _scrape_hudle(self, page: Page):
        try:
            # Hypothetical script
            booked_slots = await page.evaluate('''() => {
                const slots = [];
                return slots;
            }''')
             # Similar logic...
        except Exception as e:
            logger.error(f"Error scraping Hudle: {e}")



    async def book_slot(self, date: str, time: str, source: str, sport: str = "Unknown", court: str = "Unknown", 
                        c_name: str="Unknown", c_phone: str="UNKNOWN", c_email: str="UNKNOWN"):
        """
        Automates the Playo booking flow:
        1. Ensure Correct Date.
        2. Find & Click "Book" on Grid.
        3. Fill Form & Confirm.
        """
        async with self.booking_lock:
            logger.info(f"Booking LOCK acquired for {date} {time} ({sport}-{court})")
            
            # 1. SAFETY: Ensure we are on the CORRECT PAGE/DATE
            # This is critical. If we are on the wrong date, we might book the wrong slot.
            logger.info("Verifying Page Date and Optimizing Sport...")
            # OPTIMIZATION: Only scrape the required sport to save time!
            await self.request_date(date, force=True, limit_to_sports=[sport]) 
            await asyncio.sleep(1.0) # Stability wait
    
            page = self.cm.playo_tab
            if not page:
                logger.error("Playo tab not active!")
                return False

            # 1b. SAFETY: Ensure correct SPORT is selected
            # The scrape loop cycles through all sports, so we are likely on the WRONG sport page.
            target_sport_val = None
            for s in self.sports:
                # Fuzzy match or exact? Dashboard sends exact `sport` string from DB/Scrape
                # e.g. "Badminton Wooden+Mat"
                if s['name'].lower() == sport.lower():
                    target_sport_val = s['value']
                    break
            
            if target_sport_val:
                logger.info(f"Switching Sport to: {sport}")
                try:
                    await page.select_option("select[id*='SelectField']", value=target_sport_val)
                    await page.wait_for_timeout(2000) # Wait for table reload
                except Exception as e:
                    logger.error(f"Failed to switch sport: {e}")
                    return False
            else:
                 logger.warning(f"Could not find sport config for '{sport}'. Assuming correct page or trying layout search.")

            try:
                # 2. Click "Book" Button on Grid (Reuse existing JS logic)
                # We need to find the button corresponding to the time/court.
                # This is complex because we need to map the court name back to the column index/ID.
                
                # SIMPLIFIED: We will re-run the JS block search but this time CLICK the button.
                # We pass 'click=True' to a modified evaluate script or handle it here.
                # actually, let's use the same logic we had for finding the button.
                
                # Find Button Handle using JS logic
                # We use evaluate_handle to get the actual DOM element back to Python
                result_handle = await page.evaluate_handle('''({ time, courtName }) => {
                    // ROBUST TABLE FINDER (Copied from Scraper)
                    let rows = [];
                    let headerMap = [];
                    const tables = Array.from(document.querySelectorAll('table'));
                    let targetTable = null;

                    // 1. Find table by Header Keywords
                    for (const t of tables) {
                        const text = t.innerText.toLowerCase();
                        if (text.includes('time') && (text.includes('court') || text.includes('table') || text.includes('turf'))) {
                            targetTable = t;
                            break;
                        }
                    }
                    // 2. Fallback: Largest Table
                    if (!targetTable && tables.length > 0) {
                        targetTable = tables.sort((a,b) => b.querySelectorAll('tr').length - a.querySelectorAll('tr').length)[0];
                    }

                    if (targetTable) {
                        const tableHeaderRow = targetTable.querySelector('thead tr') || targetTable.querySelector('tr');
                        if (tableHeaderRow) {
                            headerMap = Array.from(tableHeaderRow.children).map((c, i) => ({index: i, name: c.innerText.trim()}));
                        }
                        rows = Array.from(targetTable.querySelectorAll('tbody tr'));
                        if (rows.length === 0) rows = Array.from(targetTable.querySelectorAll('tr'));
                    } else {
                        // Strategy 3: All TRs (No Table)
                        const allTrs = Array.from(document.querySelectorAll('tr'));
                        rows = allTrs;
                        const headerRow = rows.find(r => {
                            const txt = r.innerText.toLowerCase();
                            return txt.includes('time') && (txt.includes('court') || txt.includes('table') || txt.includes('turf'));
                        });
                        if (headerRow) {
                            headerMap = Array.from(headerRow.children).map((c, i) => ({index: i, name: c.innerText.trim()}));
                        }
                    }

                    // 3. Find Column Index
                    const norm = (t) => t ? t.toLowerCase().replace(/\\s+/g, '').replace(/[^a-z0-9]/g, '') : "";
                    const targetCourt = norm(courtName);
                    let colIndex = -1;

                    if (headerMap.length > 0) {
                        // Try exact match then partial
                        const h = headerMap.find(hm => norm(hm.name) === targetCourt) || 
                                headerMap.find(hm => norm(hm.name).includes(targetCourt));
                        if(h) colIndex = h.index;
                    }

                    if (colIndex === -1 && rows.length > 0) {
                        // Last ditch: If "Court 1" and we have columns, maybe assume layout?
                        // But headerMap is safer.
                        return "Court not found: " + targetCourt + " Headers: " + JSON.stringify(headerMap.map(h=>h.name));
                    }

                    // 4. Find Row & Click
                    let foundRow = null;
                    const targetTime = time.trim();

                    for (const row of rows) {
                        if (row.children.length === 0) continue;
                        const fullText = row.children[0].innerText.trim().toLowerCase();
                        
                        // Parse Start Text
                        let startText = fullText;
                        if (fullText.includes("-")) {
                            startText = fullText.split("-")[0].trim();
                        }
                        
                        let matched = false;
                        const tHour = parseInt(targetTime.split(':')[0]);
                        const tMin = targetTime.split(':')[1];

                        // Case 1: Midnight Hour (00:xx) -> Matches "12:xx" + AM context
                        if (tHour === 0) {
                            const expectedStart = `12:${tMin}`;
                            // Check for "12:30 am" explicit or "12:30" + implied AM
                            if (startText.includes(expectedStart)) {
                                if (startText.includes("am") || fullText.includes("am")) matched = true;
                            }
                        }
                        // Case 2: Noon Hour (12:xx) -> Matches "12:xx" + PM context
                        else if (tHour === 12) {
                            const expectedStart = `12:${tMin}`;
                            // Check for "12:30 pm" explicit or "12:30" + implied PM
                            // Also accept if NO am/pm matches (24h fallback)
                             if (startText.includes(expectedStart)) {
                                if (startText.includes("pm") || fullText.includes("pm")) matched = true;
                                else if (!fullText.includes("am")) matched = true; // Fallback
                            }
                        }
                        // Case 3: Other Times (Strict Match)
                        else {
                            if (startText.includes(targetTime)) matched = true;
                        }

                        if (matched) {
                            foundRow = row;
                            break;
                        }
                    }

                    if (foundRow) {
                        if (colIndex !== -1 && foundRow.children.length > colIndex) {
                            const cell = foundRow.children[colIndex];
                            // Enhanced Button Finding
                            const btn = cell.querySelector('button') || cell.querySelector('.button') || cell.querySelector('[role="button"]');
                            
                            if (btn) {
                                const btnText = btn.innerText.trim(); // Keep case for name display
                                const btnLower = btnText.toLowerCase();
                                
                                if (btnLower.includes('book') || btnLower.includes('block') || !isNaN(parseFloat(btnText))) {
                                    if(btn.disabled || btn.classList.contains('booked')) {
                                         return "Slot is disabled/booked";
                                    }
                                    // Return the element to Python
                                    return btn;
                                }
                                // If text is something else (like "testing"), it means it IS booked by that person.
                                // DEBUG: Return surrounding HTML to prove it to the user
                                const rowHtml = foundRow.outerHTML.substring(0, 200).replace(/\\n/g, "");
                                return "Slot occupied by: '" + btnText + "' (Row Content: " + rowHtml + "...)";
                            }
                            // DEBUG INFO: Return cell content on failure
                            const cellHtml = cell.innerHTML.substring(0, 50).replace(/\\n/g, "");
                            const cellText = cell.innerText.trim();
                            return "Button not found in cell. Text: '" + cellText + "' HTML: '" + cellHtml + "'"; 
                        }
                        return "Column index invalid for row. Row len: " + foundRow.children.length + " ColIndex: " + colIndex;
                    }
                    return "Time row not found: " + targetTime + " in table with " + rows.length + " rows";
                }''', {'time': time, 'courtName': court}) 
            
                # Process result handle
                js_result = await result_handle.json_value()
                element = result_handle.as_element()

                if element:
                    logger.info("Button found! Performing Native Click...")
                    # Scroll & Click reliably
                    try:
                        await element.scroll_into_view_if_needed()
                        await element.hover()
                        await element.click(force=True) # Force true to bypass overlays if any
                        logger.info("Native Click performed.")
                    except Exception as e:
                        logger.error(f"Native click failed: {e}")
                        self.booking_lock.release()
                        return False
                else:
                    # It's an error string
                    error_msg = str(js_result)
                    if "disabled/booked" in error_msg or "occupied by" in error_msg:
                        logger.warning(f"Slot is already Booked/Occupied: {error_msg}")
                        self.booking_lock.release() # Release lock on graceful failure
                        return False # Fail gracefully, don't crash
                    
                    # If it's some other non-button string, assume error
                    logger.error(f"Failed to find button: {error_msg}")
                    self.booking_lock.release() # Release lock on error
                    return False
                
                logger.info("Clicked initial Book button.")
                await page.wait_for_timeout(2000)

                # 2. Click "Book Now" Popup (Blue Button)
                try:
                    # Selector based on user HTML: Blue button with "Book Now"
                    # Using text match is safest
                    book_now_btn = page.locator('button', has_text="Book Now")
                    if await book_now_btn.is_visible():
                        await book_now_btn.click()
                        logger.info("Clicked Pop-up 'Book Now'")
                        await page.wait_for_timeout(3000)
                    else:
                        logger.warning("'Book Now' popup not seen, maybe direct form?")
                except Exception as e:
                    logger.warning(f"Error handling Book Now popup: {e}")

                # 3. Fill Form
                # WAIT FOR FORM CONTAINER
                try:
                    form_container = page.locator('.BC-A1-booking-checkout-form-container')
                    await form_container.wait_for(state="visible", timeout=10000)
                    logger.info("Booking form detected.")
                except Exception as e:
                    return False

                # USER REPORT: Form scrolls/animates after appearing. Wait for stabilization.
                await page.wait_for_timeout(2000)

                # Helper for React inputs: Click -> Clear -> Type
                async def robust_fill(locator, value):
                    try:
                        await locator.click(timeout=2000)
                        await locator.focus()
                        # Clear existing text (Ctrl+A, Backspace) to avoid autofill concatenation
                        await locator.press("Control+A")
                        await locator.press("Backspace")
                        # Use press_sequentially to simulate real typing (React compatible)
                        await locator.press_sequentially(value, delay=100)
                    except Exception as ex:
                        logger.warning(f"Robust fill failed, retrying standard fill: {ex}")
                        await locator.fill(value)

                # Phone (Mobile placeholder exists)
                # already filled? usually yes if user logged in, but we fill anyway
                await robust_fill(page.locator('input[placeholder="Mobile"] >> visible=true'), c_phone)
                
                # Name
                try:
                    # Strategy: Filter Container by Label Text, then find Input.
                    # Structure: .BC-A1-text-input-field-container > ... > Label "name" & Input type="text"
                    name_input = form_container.locator(".BC-A1-text-input-field-container").filter(has_text="name").locator("input[type='text']").first
                    await robust_fill(name_input, c_name)
                except Exception as e:
                    logger.error(f"Name fill failed: {e}. HTML Context: {await form_container.inner_html()}")

                # Email
                try:
                    email_input = form_container.locator(".BC-A1-text-input-field-container").filter(has_text="email").locator("input[type='text']").first
                    await robust_fill(email_input, c_email)
                except Exception as e:
                    logger.error(f"Email fill failed. HTML Context: {await form_container.inner_html()}")

                # 4a. Payment (Correct Discount Logic)
                try:
                    await page.wait_for_timeout(500) # Wait for price calc
                    # Find element containing "INR " that is visible to determine PRICE
                    # Usually in the right panel: "Gross (Court Price) ... INR 175"
                    price_texts = await form_container.locator("text=/INR \\d+/").all_inner_texts()
                    price = "0"
                    for t in price_texts:
                        import re
                        m = re.search(r"INR\s*(\d+)", t)
                        if m:
                             p_val = m.group(1)
                             if int(p_val) > 0:
                                 price = p_val
                                 break # Found the gross price
                    
                    logger.info(f"Detected Price: {price}")
                    
                    if int(price) > 0:
                         # Strategy: Set Discount Amount == Price needed.
                         
                         # 1. Select "Discount" dropdown
                         # The dropdown is in a container.
                         select_box = form_container.locator('select').filter(has_text="Discount")
                         if await select_box.count() > 0:
                                await select_box.select_option("discount")
                                await page.wait_for_timeout(300)
                                
                                # 2. Find the Discount Amount Input (placeholder="Amount") adjacent to it
                                # We can look for the input in the same row/container
                                discount_amount_input = form_container.locator('input[placeholder="Amount"]')
                                
                                # 3. Fill Discount Amount = Price
                                await robust_fill(discount_amount_input, price)
                                
                                # 4. Click Apply
                                await form_container.locator('button', has_text="Apply").click()
                                await page.wait_for_timeout(1000)
                                logger.info(f"Applied Discount of {price} (100% off).")
                         else:
                             logger.warning("Discount dropdown not found.")
                except Exception as e:
                    logger.error(f"Error handling payment/discount: {e}")

                # 4b. Fill Remarks & Toggle Confirmation
                try:
                    # Remarks
                    remarks_input = form_container.locator('textarea[placeholder="Remarks"]')
                    await robust_fill(remarks_input, "Booked via Dashboard")
                    
                    # Confirm Checkbox
                    # Structure: Label > Input(hidden) + Span(Text)
                    # We click the SPAN "Send Confirmation SMS/Email"
                    # USER REQUEST: Disable to avoid API spam/detection
                    # await form_container.locator('span', has_text="Send Confirmation SMS/Email").click()
                    logger.info("Filled Remarks (Skipped Confirmation SMS/Email Checkbox)")
                except Exception as e:
                    logger.warning(f"Error handling Remarks/Checkbox: {e}")
                
                # 5. Confirm Booking
                # HTML: Button "Confirm Booking"
                confirm_btn = form_container.locator('button', has_text="Confirm Booking")
                if await confirm_btn.is_visible():
                    # ---------------------------------------------------------
                    # USER ACTION: Clicking Confirm
                    await confirm_btn.click()
                    logger.info("Clicked 'Confirm Booking'")
                    # ---------------------------------------------------------
                    
                    # Wait for Success Modal or Navigation?
                    # Usually Playo shows a success message or redirects.
                    await page.wait_for_timeout(3000)
                    
                    # TRIGGER IMMEDIATE RE-SCRAPE TO UPDATE DASHBOARD
                    # This ensures when the user sees "Success", the data is actually ready.
                    logger.info("Refreshing data for dashboard...")
                    await self.request_date(date, force=True, limit_to_sports=[sport])
                    
                    return True
                else:
                    logger.error("Confirm Booking button not found!")
                    return False

            except Exception as e:
                logger.error(f"Error executing booking flow: {e}")
                return False
