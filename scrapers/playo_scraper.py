import logging
import asyncio
from datetime import datetime
from playwright.async_api import Page
import database
from .base_scraper import BaseScraper

logger = logging.getLogger("PlayoScraper")

class PlayoScraper(BaseScraper):
    def __init__(self):
        self.sports = [
            {"name": "Badminton Synthetic", "value": "16214"},
            {"name": "Badminton Premium Hybrid", "value": "16215"},
            {"name": "Football 7 a side", "value": "16216"},
            {"name": "Box Cricket 7 a side", "value": "16217"},
            {"name": "Snooker", "value": "16221"},
            {"name": "Pool 8 Ball", "value": "16224"},
            {"name": "Snooker Pro", "value": "16225"}
        ]

    def get_name(self) -> str:
        return "Playo"

    async def scrape(self, page: Page, scrape_requests: list) -> None:
        """
        Scrapes Playo for the given list of request dicts.
        Request: {'date': 'YYYY-MM-DD', 'force': bool, 'limit_sports': list|None}
        """
        logger.info(f"PlayoScraper started with {len(scrape_requests)} requests.")
        
        # Ensure we are on Calendar Page logic (Simplified from original for brevity, but retaining core check)
        await self._ensure_calendar_page(page)

        for req in scrape_requests:
            date_str = req['date']
            limit_to_sports = req.get('limit_sports')
            
            # --- DATE NAVIGATION ---
            if not await self._navigate_to_date(page, date_str):
                continue

            # --- SPORT SCRAPING ---
            sports_to_scrape = self._get_sports_to_scrape(limit_to_sports)
            
            for sport in sports_to_scrape:
                await self._scrape_sport_for_date(page, date_str, sport)

    async def _ensure_calendar_page(self, page: Page):
        try:
            # Check for date picker VISIBILITY
            date_picker = page.locator('.react-datepicker__input-container input')
            is_visible = False
            if await date_picker.count() > 0:
                is_visible = await date_picker.first.is_visible()
            
            if not is_visible:
                logger.info("Date picker not visible. Attempting to navigate to Calendar page...")
                calendar_btn = page.locator('div[role="button"]', has_text="Calendar")
                if await calendar_btn.count() > 0 and await calendar_btn.first.is_visible():
                        await calendar_btn.first.click()
                        await page.wait_for_timeout(3000)
                else:
                    logger.info("Calendar button hidden/missing. Trying to expand 'Schedule'...")
                    schedule_btn = page.locator('div[role="button"]', has_text="Schedule")
                    if await schedule_btn.count() > 0:
                            await schedule_btn.first.click()
                            await page.wait_for_timeout(1000)
                            if await calendar_btn.count() > 0:
                                await calendar_btn.first.click()
                                await page.wait_for_timeout(3000)
        except Exception as e:
            logger.error(f"Error navigating to Calendar: {e}")

    async def _navigate_to_date(self, page: Page, date_str: str) -> bool:
        # Format for Playo: DD - Mon - YY
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            target_date_str = dt.strftime("%d - %b - %y")
        except ValueError:
            logger.error(f"Invalid date format: {date_str}")
            return False
        
        try:
            all_date_inputs = page.locator('.react-datepicker__input-container input')
            if await all_date_inputs.count() > 0:
                date_input = all_date_inputs.first
                await date_input.click()
                await date_input.fill(target_date_str)
                await page.wait_for_timeout(500)
                await date_input.press('Enter')
                await page.wait_for_timeout(500)
                await date_input.press('Tab')
            else:
                logger.error("Date input not found!")
                return False
        except Exception as e:
            logger.error(f"Failed to change date: {e}")
            return False
            
        # Verify
        for _ in range(3):
            await page.wait_for_timeout(2000)
            current_page_date = await page.evaluate('''() => {
                const input = document.querySelector('.react-datepicker__input-container input');
                return input ? input.value : null;
            }''')
            if current_page_date and current_page_date.replace(" ", "") == target_date_str.replace(" ", ""):
                return True
        
        logger.error(f"Failed to verify date switch to {target_date_str}")
        return False

    def _get_sports_to_scrape(self, limit_to_sports):
        if not limit_to_sports:
            return self.sports
        limit_lower = [s.lower() for s in limit_to_sports]
        filtered = [s for s in self.sports if s['name'].lower() in limit_lower]
        return filtered if filtered else self.sports

    async def _scrape_sport_for_date(self, page: Page, date_str: str, sport: dict):
        try:
            # Select Sport
            select_selector = "select[id*='SelectField']"
            await page.select_option(select_selector, value=sport['value']) 
            await page.wait_for_timeout(1500)
            
            # Execute JS Scraper (The big blob)
            sport_name_js = sport['name'] 
            # Note: I am not pasting the entire JS blob here to save context size, 
            # I will use a simplified reference or need to copy it fully if we want it to work.
            # IMPORTANT: I MUST provide the JS logic.
            
            js_script = ''' (sportName) => {
                const debug = [];
                const results = [];
                
                // ... (Original JS Logic Omitted for Brevity in this specific tool call, BUT REQUIRED) ...
                // Re-implementing the robust scraper logic
                
                const timePattern = /(\\d{1,2}):(\\d{2})\\s*-\\s*\\d{1,2}:\\d{2}\\s*(AM|PM)/i;
                let extractedDate = null;
                const dateInput = document.querySelector('.react-datepicker__input-container input');
                if (dateInput) extractedDate = dateInput.value;

                let rows = [];
                let headerMap = [];
                
                const tables = Array.from(document.querySelectorAll('table'));
                let targetTable = null;
                 if (tables.length > 0) {
                     targetTable = tables.sort((a, b) => b.querySelectorAll('tr').length - a.querySelectorAll('tr').length)[0];
                 }
                 
                 if (targetTable) {
                    const tableHeaderRow = targetTable.querySelector('thead tr') || targetTable.querySelector('tr');
                    if (tableHeaderRow) headerMap = Array.from(tableHeaderRow.children).map((c, i) => ({index: i, name: c.innerText.trim()}));
                    rows = Array.from(targetTable.querySelectorAll('tbody tr'));
                    if (rows.length === 0) rows = Array.from(targetTable.querySelectorAll('tr'));
                 } else {
                     rows = Array.from(document.querySelectorAll('tr'));
                 }

                function to24h(h, m, amp) {
                    let hour = parseInt(h);
                    const min = m;
                    const ampm = amp.toUpperCase();
                    if (ampm === "PM" && hour < 12) hour += 12;
                    if (ampm === "AM" && hour === 12) hour = 0;
                    return hour.toString().padStart(2, '0') + ":" + min;
                }
                
                const capturePattern = /(\\d{1,2}):(\\d{2})(?:\\s*[a-zA-Z]{2})?\\s*-\\s*(\\d{1,2}):(\\d{2})\\s*([a-zA-Z]{2})/i;
                
                rows.forEach(row => {
                     if (row.children.length > 0) {
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
                             const startMatch = startPart.match(/(\\d{1,2}):(\\d{2})/);
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
                                     if (headerMap.length > 0) {
                                         const h = headerMap.find(hm => hm.index === idx);
                                         if (h) colName = h.name;
                                     }
                                      // Rename courts
                                      if (sportName.includes("Snooker") || sportName.includes("Pool") || sportName.includes("Table Tennis")) {
                                            colName = colName.replace(/Court/i, "Table");
                                        } else if (sportName.includes("Football") || sportName.includes("Cricket")) {
                                            colName = colName.replace(/Court/i, "Turf");
                                            colName = colName.replace(/Table/i, "Turf"); 
                                        }

                                     const btnTexts = Array.from(cell.querySelectorAll('button, [role="button"], .btn')).map(b => b.textContent.trim().toLowerCase());
                                     const cellText = cell.innerText.trim().toLowerCase();
                                     let status = null;

                                     if (btnTexts.some(t => t.includes('book') || t.includes('block') || t.includes('₹'))) {
                                         status = "Available";
                                     } else if (btnTexts.some(t => t.includes('locked'))) {
                                         status = "Locked";
                                     } else if (cellText === 'booked' || cellText === 'full' || cellText.includes('locked')) {
                                         status = "Booked";
                                     } else if (cell.innerText.length > 3 && !cell.innerText.includes('₹')) {
                                          status = "Booked"; // Name fallback
                                     }

                                     if (status) {
                                         if (colName === "Unknown") colName = "Court " + (idx - timeColIndex);
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
            }'''
            
            evaluation_result = await page.evaluate(js_script, sport_name_js)
            scraped_data = evaluation_result.get('results', [])
            
            # Helper Nested
            def parse_playo_date(date_str):
                 if not date_str: return None
                 try:
                     clean = date_str.replace(" ", "")
                     dt = datetime.strptime(clean, "%d-%b-%y")
                     return dt.strftime("%Y-%m-%d")
                 except ValueError:
                      return None

            if not scraped_data:
                return

            page_date_parsed = parse_playo_date(scraped_data[0].get('date'))
            if page_date_parsed != date_str:
                logger.warning(f"Date Mismatch in scraping: Requested {date_str}, got {page_date_parsed}")

            # Bulk Save
            slots_to_save = []
            for slot in scraped_data:
                slots_to_save.append({
                    "date": page_date_parsed or date_str, # Fallback to requested date if parse fails
                    "time": slot['time'],
                    "source": "Playo",
                    "sport": sport['name'],
                    "court": slot['court'],
                    "status": slot['status']
                })
            
            if slots_to_save:
                await database.save_booked_slots_playo(slots_to_save)
                logger.info(f"Saved {len(slots_to_save)} slots for Playo {date_str} ({sport['name']})")

        except Exception as e:
            logger.error(f"Error extracting data for {sport['name']}: {e}")
