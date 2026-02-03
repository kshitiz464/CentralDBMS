import logging
import json
import asyncio
import random
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
        
        # Get auth token with retry (user might be logging in)
        auth_token = None
        max_auth_retries = 3
        for attempt in range(max_auth_retries):
            auth_token = await self._get_auth_token(page)
            if auth_token:
                break
            if attempt < max_auth_retries - 1:
                logger.warning(f"No auth token found, retrying in 10s ({attempt + 1}/{max_auth_retries})... Please login to Playo.")
                await asyncio.sleep(10)
        
        if not auth_token:
            logger.error("No auth token found for Playo scraping after retries. Please login to Playo.")
            return False
        
        logger.info(f"Playo auth token retrieved successfully (length: {len(auth_token)})")

        for req in scrape_requests:
            date_str = req['date']
            limit_to_sports = req.get('limit_sports')
            
            sports_to_scrape = self._get_sports_to_scrape(limit_to_sports)
            
            for sport in sports_to_scrape:
                await self._scrape_sport_for_date_api(page, date_str, sport, auth_token)
                # Stealth: Add random delay between requests to avoid burst pattern
                delay = random.uniform(2, 5)
                logger.info(f"Sleeping for {delay:.2f}s before next sport...")
                await asyncio.sleep(delay)
        
        return True  # Success

    async def _get_auth_token(self, page: Page) -> str:
        """Get auth token from playoAuthToken cookie."""
        try:
            token = await page.evaluate("""() => {
                const cookies = document.cookie.split(';');
                for (const cookie of cookies) {
                    const [name, value] = cookie.trim().split('=');
                    if (name === 'playoAuthToken') {
                        return decodeURIComponent(value);
                    }
                }
                return null;
            }""")
            return token
        except Exception as e:
            logger.error(f"Error getting auth token: {e}")
            return None

    def _get_sports_to_scrape(self, limit_to_sports):
        if not limit_to_sports:
            return self.sports
        limit_lower = [s.lower() for s in limit_to_sports]
        filtered = [s for s in self.sports if s['name'].lower() in limit_lower]
        return filtered if filtered else self.sports

    async def _scrape_sport_for_date_api(self, page: Page, date_str: str, sport: dict, auth_token: str):
        """
        Scrapes slot data using the Playo Availability API.
        This is much more reliable than DOM parsing.
        """
        try:
            sport_name = sport['name']
            activity_id = int(sport['value'])
            
            # Call the availability API
            url = "https://api.playo.io/controller/ppc/availability"
            headers = {
                "accept": "application/json",
                "authorization": auth_token,
                "content-type": "application/json",
            }
            body = {
                "activityIds": [activity_id],
                "activityStartDate": date_str,
                "activityEndDate": date_str,
                "customerStatus": 0
            }
            
            response = await page.request.post(url, headers=headers, data=json.dumps(body))
            
            if not response.ok:
                logger.error(f"Playo API failed for {sport_name}: {response.status}")
                error_text = await response.text()
                logger.error(f"Error response: {error_text[:500]}")
                return
            
            data = await response.json()
            
            # Log response details
            court_count = len(data.get("data", []))
            logger.info(f"API response for {sport_name}: {court_count} courts returned")
            
            # Parse API response into slot format
            slots_to_save = []
            for court in data.get("data", []):
                court_name = court.get("courtName", "Unknown")
                
                # Simplify court name (e.g., "Badminton Synthetic Court 1" -> "Court 1")
                simple_court_name = court_name
                if sport_name in court_name:
                    simple_court_name = court_name.replace(sport_name + " ", "")
                
                # Football/Cricket -> Turf (standardize with Hudle)
                if "Football" in sport_name or "Cricket" in sport_name:
                    # Always use "Turf 1" for single turf sports
                    simple_court_name = "Turf 1"
                
                # Snooker -> Table 1 (standardize with Hudle)
                elif "Snooker" in sport_name:
                    # Match Hudle format: "Table 1" for Snooker AND Snooker Pro
                    simple_court_name = "Table 1"
                
                # Pool -> Table X (standardize with Hudle)
                elif "Pool" in sport_name:
                    # Keep court number for Pool tables
                    simple_court_name = simple_court_name.replace("Court", "Table")
                
                for slot in court.get("slots", []):
                    slot_time = slot.get("slotTime", "")  # "HH:MM:SS"
                    
                    # Convert to "HH:MM" format
                    time_24 = slot_time[:5] if slot_time else "00:00"
                    
                    # Determine status from API response
                    available = slot.get("available", 0)
                    blocked = slot.get("blocked", False)
                    status_str = slot.get("status", "")
                    customer_name = slot.get("customerName", "")
                    
                    if blocked:
                        status = "Locked"
                    elif available == 1 and status_str == "Book":
                        status = "Available"
                    elif status_str == "Booked" or customer_name:
                        status = "Booked"
                    else:
                        status = "Available"  # Default
                    
                    slots_to_save.append({
                        "date": date_str,
                        "time": time_24,
                        "source": "Playo",
                        "sport": sport_name,
                        "court": simple_court_name,
                        "status": status
                    })
            
            if not slots_to_save:
                logger.warning(f"No slots found for {sport_name} on {date_str}")
                return
            
            # Save to database
            await database.save_booked_slots_playo(slots_to_save)
            logger.info(f"Saved {len(slots_to_save)} slots for Playo {date_str} ({sport_name})")
            
        except Exception as e:
            logger.error(f"Error scraping {sport['name']}: {e}")
