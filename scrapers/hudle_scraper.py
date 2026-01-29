import logging
import asyncio
from typing import List, Dict, Any
from playwright.async_api import Page
import database
from .base_scraper import BaseScraper
import re

logger = logging.getLogger("HudleScraper")

class HudleScraper(BaseScraper):
    def __init__(self):
        self.VENUE_ID = "07d910dd-7730-42ca-bc61-45fbac1019d6"
        # Sports Mapping for Playshire
        # 2: Badminton, 8: Football, 24: Box Cricket, 5: Billiard (Pool/Snooker)
        self.sports_config = [
            ("2", "Badminton"),
            ("8", "Football"),
            ("24", "Box Cricket"),
            ("5", "Billiard")
        ]

    def get_name(self) -> str:
        return "Hudle"

    async def scrape(self, page: Page, scrape_requests: list) -> None:
        """
        Executes Hudle API scraping for requested dates.
        """
        logger.info(f"HudleScraper started with {len(scrape_requests)} requests.")
        
        for req in scrape_requests:
            date_str = req['date']
            # Hudle API handles specific sports via ID, so we iterate config
            
            for sport_id, sport_name_debug in self.sports_config:
                try:
                    # 1. Fetch JSON via Browser Context
                    result = await self._fetch_hudle_api(page, self.VENUE_ID, date_str, sport_id)
                    
                    if not result or not result.get("success"):
                        status_code = result.get("status") if result else "N/A"
                        if status_code != 404: # 404 might just mean no slots
                            logger.warning(f"Hudle Fetch Failed for {date_str} {sport_name_debug}: Status={status_code}")
                        continue

                    json_data = result.get("data")
                    if not json_data:
                        continue

                    # 2. Parse & Save
                    slots = self._parse_hudle_response(json_data, date_str, sport_id)
                    
                    if slots:
                        await database.save_booked_slots_hudle(slots)
                        logger.info(f"Saved {len(slots)} slots for Hudle {date_str} ({sport_name_debug})")

                except Exception as e:
                    logger.error(f"Error scraping Hudle date {date_str} {sport_name_debug}: {e}")

    async def _fetch_hudle_api(self, page: Page, venue_id: str, date_str: str, sport_id: str) -> Dict:
        url = f"https://api.hudle.in/api/v1/venues/{venue_id}/slots?view_type=1&date={date_str}&sport={sport_id}&grid=1"
        try:
            # 1. Get Token from LocalStorage
            token = await page.evaluate("""() => {
                return localStorage.getItem('token') || localStorage.getItem('access_token') || localStorage.getItem('authToken');
            }""")
            
            # Fallback
            if not token:
                token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ2ZXJzaW9uIjoib2VWRkZnVGZKYVh6eVQ4a0czTWtaVm5zcVZleTVRWFlGemE3WmpHOWRtdk5HT0ExVk81WTc3VUpqZWs0Iiwic3ViIjo3OTE4NjUsImlzcyI6Imh0dHBzOi8vYXBpLmh1ZGxlLmluL2FwaS92MS9sb2dpbiIsImlhdCI6MTc2ODY3MjkzMSwiZXhwIjoxNzk5Nzc2OTMxLCJuYmYiOjE3Njg2NzI5MzEsImp0aSI6IkQ0VjFuZ3VsY2hJRUd1YTIifQ.uDZsIAjTRqzPi9-ovJ4PB4VFZAhBsAT888qwxrpHucc"
            
            if not token.startswith("Bearer "):
                token = "Bearer " + token

            headers = {
                "accept": "*/*",
                "accept-language": "en-US,en;q=0.9",
                "api-secret": "hudle-api1798@prod",
                "authorization": token,
                "content-type": "application/json",
                "x-app-source": "partner",
                "nr-meta": "hudle_partner",
                "referer": "https://partner.hudle.in/"
            }

            response = await page.request.get(url, headers=headers)
            
            if not response.ok:
                return { "success": False, "status": response.status, "error": f"HTTP {response.status}" }
            
            json_obj = await response.json()
            return { "success": True, "status": response.status, "data": json_obj }

        except Exception as e:
            logger.error(f"Playwright Request failed: {e}")
            return { "success": False, "status": 0, "error": str(e) }

    def _parse_hudle_response(self, json_data: dict, date_str: str, sport_id: str) -> list:
        parsed_slots = []
        try:
            slot_groups = json_data.get("data", {}).get("slot_data", [])
            
            pool_counter = 1
            snooker_counter = 1
            snooker_pro_counter = 1

            for group in slot_groups:
                court_name = group.get("group_name", "Unknown Court")
                court_lower = court_name.lower()
                
                sport_name = "Unknown"
                mapped_court_name = court_name

                # --- MAPPING LOGIC START ---
                if sport_id == "2": # Badminton
                    match = re.search(r'\d+', court_name)
                    court_num = int(match.group(0)) if match else 0
                    if 1 <= court_num <= 4:
                        sport_name = "Badminton Premium Hybrid"
                    elif 5 <= court_num <= 8:
                        sport_name = "Badminton Synthetic"
                        new_num = court_num - 4
                        mapped_court_name = f"Court {new_num}"
                    else:
                        sport_name = "Badminton Synthetic"

                elif sport_id == "8":
                    sport_name = "Football 7 a side"
                elif sport_id == "24":
                    sport_name = "Box Cricket 7 a side"
                elif sport_id == "5": # Billiard
                    if "pool" in court_lower:
                        sport_name = "Pool 8 Ball"
                        mapped_court_name = f"Pool Table {pool_counter}"
                        pool_counter += 1
                    elif "pro" in court_lower:
                         sport_name = "Snooker Pro"
                         mapped_court_name = f"Snooker Pro {snooker_pro_counter}"
                         snooker_pro_counter += 1
                    elif "snooker" in court_lower:
                        sport_name = "Snooker"
                        mapped_court_name = f"Snooker Table {snooker_counter}"
                        snooker_counter += 1
                    else:
                        sport_name = "Snooker"
                        mapped_court_name = f"Snooker Table {snooker_counter}"
                        snooker_counter += 1
                # --- MAPPING LOGIC END ---

                if sport_name == "Unknown":
                    continue

                for slot in group.get("slots", []):
                    start_full = slot.get("start_time", "")
                    is_booked = slot.get("is_booked", False)
                    is_av = slot.get("is_available", False)

                    try:
                        time_part = start_full.split(" ")[1][:5]
                    except:
                        continue
                    
                    status = "Available"
                    if is_booked:
                        status = "Booked"
                    elif not is_av:
                        status = "Locked"

                    parsed_slots.append({
                        "date": date_str,
                        "time": time_part,
                        "court": mapped_court_name,
                        "sport": sport_name,
                        "status": status,
                        "source": "Hudle"
                    })
            return parsed_slots
        except Exception as e:
            logger.error(f"Error parsing Hudle response: {e}")
            return []
