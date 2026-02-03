"""
Playo Booking Service - API-based booking automation.
Handles the complete booking flow: cart -> customer lookup -> discount -> book -> cancel
"""
import logging
from typing import Dict, Optional, Any
from playwright.async_api import Page
import json

logger = logging.getLogger("PlayoBookingService")


class PlayoBookingService:
    """
    Handles Playo booking operations via API.
    Uses the browser's authenticated session (cookies) to make API calls.
    """
    
    BASE_URL = "https://api.playo.io/controller/ppc"
    
    # Activity ID mapping (Sport Name -> Playo Activity ID)
    ACTIVITY_IDS = {
        "Badminton Synthetic": "16214",
        "Badminton Premium Hybrid": "16215",
        "Football 7 a side": "16216",
        "Box Cricket 7 a side": "16217",
        "Snooker": "16221",
        "Pool 8 Ball": "16224",
        "Snooker Pro": "16225",
    }
    
    # Court ID mapping - will be populated from availability API
    # Format: {sport_name: {court_name: court_id}}
    COURT_IDS = {}
    
    def __init__(self):
        self._auth_token: Optional[str] = None
    
    async def _get_auth_token(self, page: Page) -> str:
        """Get auth token from playoAuthToken cookie."""
        if self._auth_token:
            return self._auth_token
        
        # Get from cookie
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
        
        if not token:
            raise Exception("Playo auth token not found in cookies. Please login to Playo.")
        
        self._auth_token = token
        return token
    
    def _get_headers(self, token: str) -> Dict[str, str]:
        """Build request headers."""
        return {
            "accept": "application/json",
            "authorization": token,
            "content-type": "application/json",
            "referer": "https://dashboard.playo.club/"
        }
    
    async def get_availability(
        self, 
        page: Page, 
        sport_name: str, 
        date_str: str
    ) -> Dict[str, Any]:
        """
        Fetch slot availability for a sport on a given date.
        Returns court IDs and slot details.
        """
        token = await self._get_auth_token(page)
        activity_id = self.ACTIVITY_IDS.get(sport_name)
        
        if not activity_id:
            raise ValueError(f"Unknown sport: {sport_name}")
        
        url = f"{self.BASE_URL}/availability"
        body = {
            "activityIds": [int(activity_id)],
            "activityStartDate": date_str,
            "activityEndDate": date_str,
            "customerStatus": 0
        }
        
        # Retry logic for 5xx errors
        max_retries = 3
        for attempt in range(max_retries):
            response = await page.request.post(
                url,
                headers=self._get_headers(token),
                data=json.dumps(body)
            )
            
            if response.ok:
                break
            elif response.status >= 500 and attempt < max_retries - 1:
                # Server error, retry after delay
                logger.warning(f"Availability API returned {response.status}, retrying ({attempt + 1}/{max_retries})...")
                import asyncio
                await asyncio.sleep(1)
            else:
                raise Exception(f"Availability API failed: {response.status}")
        
        data = await response.json()
        
        # Cache court IDs for this sport
        if sport_name not in self.COURT_IDS:
            self.COURT_IDS[sport_name] = {}
        
        for court in data.get("data", []):
            court_name = court.get("courtName", "")
            court_id = court.get("courtId")
            # Map "Court 1" properly
            simple_name = court_name.replace(f"{sport_name} ", "")
            self.COURT_IDS[sport_name][simple_name] = court_id
            self.COURT_IDS[sport_name][court_name] = court_id
        
        return data
    
    async def add_to_cart(
        self,
        page: Page,
        court_id: int,
        court_name: str,
        slot_time: str,  # Format: "HH:MM:00"
        slot_date: str,  # Format: "YYYY-MM-DD"
        activity_id: int,
        price: int = 0
    ) -> Dict[str, Any]:
        """Add a slot to the cart."""
        token = await self._get_auth_token(page)
        
        # Correct endpoint is /carting/slot/add
        url = f"{self.BASE_URL}/carting/slot/add"
        
        # Calculate end time (30 min slots)
        time_parts = slot_time.split(":")
        hour = int(time_parts[0])
        minute = int(time_parts[1])
        minute += 30
        if minute >= 60:
            minute -= 60
            hour += 1
            if hour >= 24:
                hour = 0
        end_time = f"{hour:02d}:{minute:02d}:00"
        
        body = {
            "slotDuration": "00:30:00",
            "slot": {
                "activityId": activity_id,
                "activityType": 0,
                "count": 1,
                "courtId": court_id,
                "courtName": court_name,
                "courtBrothers": [],
                "slotDate": slot_date,
                "slotTime": slot_time,
                "endTime": end_time,
                "available": 1,
                "blocked": False,
                "blockingId": None,
                "price": price,
                "slotDiscount": {}
            }
        }
        
        logger.info(f"Add to cart URL: {url}")
        logger.info(f"Add to cart body: {body}")
        
        response = await page.request.post(
            url,
            headers=self._get_headers(token),
            data=json.dumps(body)
        )
        
        if not response.ok:
            error_text = await response.text()
            logger.error(f"Add to cart response: {error_text}")
            raise Exception(f"Add to cart failed: {response.status}")
        
        return await response.json()
    
    async def get_cart_details(self, page: Page) -> Dict[str, Any]:
        """Get current cart contents."""
        token = await self._get_auth_token(page)
        
        url = f"{self.BASE_URL}/carting/details"
        response = await page.request.get(url, headers=self._get_headers(token))
        
        if not response.ok:
            raise Exception(f"Cart details failed: {response.status}")
        
        return await response.json()
    
    async def lookup_customer(
        self,
        page: Page,
        mobile: str
    ) -> Dict[str, Any]:
        """
        Lookup customer by phone number.
        Returns nonMemberId if customer exists.
        """
        token = await self._get_auth_token(page)
        
        url = f"{self.BASE_URL}/customer/details"
        body = {
            "clubId": 1,  # Seems to be a constant
            "mobile": mobile.replace("+91", "").replace(" ", "")
        }
        
        response = await page.request.post(
            url,
            headers=self._get_headers(token),
            data=json.dumps(body)
        )
        
        if not response.ok:
            raise Exception(f"Customer lookup failed: {response.status}")
        
        return await response.json()
    
    async def reset_credits(self, page: Page) -> Dict[str, Any]:
        """Reset credits in cart."""
        token = await self._get_auth_token(page)
        
        url = f"{self.BASE_URL}/club/credits/reset"
        response = await page.request.post(url, headers=self._get_headers(token))
        
        if not response.ok:
            raise Exception(f"Credits reset failed: {response.status}")
        
        return await response.json()
    
    async def apply_discount(
        self,
        page: Page,
        discount_amount: int
    ) -> Dict[str, Any]:
        """Apply discount to make balance zero."""
        token = await self._get_auth_token(page)
        
        url = f"{self.BASE_URL}/club/discount/apply"
        body = {"discountAmount": discount_amount}
        
        response = await page.request.post(
            url,
            headers=self._get_headers(token),
            data=json.dumps(body)
        )
        
        if not response.ok:
            raise Exception(f"Apply discount failed: {response.status}")
        
        return await response.json()
    
    async def create_booking(
        self,
        page: Page,
        non_member_id: int,
        gross_amount: int,
        customer_name: str,
        customer_phone: str,
        customer_email: str,
        remarks: str = ""
    ) -> Dict[str, Any]:
        """
        Create the booking.
        Sets discount = grossAmount for INR 0 balance.
        Disables SMS and payment link.
        """
        token = await self._get_auth_token(page)
        
        url = f"{self.BASE_URL}/booking"
        body = {
            "coupon": None,
            "toBeRegistered": False,
            "memberId": None,
            "nonMemberId": non_member_id,
            "paymentMode": "No Pay",
            "bookingRemarks": remarks,
            "totalPaidAmount": 0,
            "grossAmount": gross_amount,
            "clubDiscount": gross_amount,  # Full discount
            "credits": 0,
            "customerDetails": {
                "name": customer_name,
                "countryCode": "+91",
                "mobile": customer_phone.replace("+91", "").replace(" ", ""),
                "email": customer_email,
                "additionalInfo": "",
                "company": "",
                "uniqueId": ""
            },
            "isPatternBooking": False,
            "patternBookingData": {},
            "transactionData": {"type": 1, "mode": 0},
            "sendSMS": False,
            "sendPaymentLink": False
        }
        
        response = await page.request.post(
            url,
            headers=self._get_headers(token),
            data=json.dumps(body)
        )
        
        if not response.ok:
            error_text = await response.text()
            raise Exception(f"Booking failed: {response.status} - {error_text}")
        
        result = await response.json()
        
        if result.get("requestStatus") != 1:
            raise Exception(f"Booking failed: {result.get('message')}")
        
        logger.info(f"Booking created: {result.get('bookingId')}")
        return result
    
    async def clear_cart(self, page: Page) -> Dict[str, Any]:
        """Clear the cart after booking."""
        token = await self._get_auth_token(page)
        
        url = f"{self.BASE_URL}/carting/clear"
        response = await page.request.post(url, headers=self._get_headers(token))
        
        if not response.ok:
            raise Exception(f"Clear cart failed: {response.status}")
        
        return await response.json()
    
    async def cancel_booking(
        self,
        page: Page,
        booking_id: str,
        refund_type: int = 3,  # 1=Policy, 2=Full, 3=No Refund
        send_sms: bool = False
    ) -> Dict[str, Any]:
        """Cancel an existing booking."""
        token = await self._get_auth_token(page)
        
        url = f"{self.BASE_URL}/booking/cancellation"
        body = {
            "bookingId": booking_id,
            "patternBookingId": None,
            "cancelRemarks": "",
            "playoCancelRemarks": "",
            "refundMode": "cash",
            "refundType": refund_type,
            "transactionData": {"type": -1, "mode": 1},
            "sendSMS": send_sms
        }
        
        response = await page.request.post(
            url,
            headers=self._get_headers(token),
            data=json.dumps(body)
        )
        
        if not response.ok:
            raise Exception(f"Cancellation failed: {response.status}")
        
        result = await response.json()
        logger.info(f"Booking cancelled: {booking_id}")
        return result
    
    # ==================== HIGH-LEVEL ORCHESTRATION ====================
    
    async def book_slot(
        self,
        page: Page,
        date_str: str,
        time_str: str,  # "HH:MM" format
        sport_name: str,
        court_name: str,
        customer_name: str,
        customer_phone: str,
        customer_email: str,
        remarks: str = ""
    ) -> Dict[str, Any]:
        """
        Complete booking flow:
        1. Get availability (to get court_id and price)
        2. Add to cart
        3. Lookup customer (to get nonMemberId)
        4. Reset credits
        5. Apply discount (full)
        6. Create booking
        7. Clear cart
        """
        try:
            # 1. Get availability and court info
            logger.info(f"Booking: {sport_name} {court_name} on {date_str} at {time_str}")
            
            availability = await self.get_availability(page, sport_name, date_str)
            
            # Find court_id
            court_id = None
            full_court_name = court_name  # Will store the full name from API
            slot_price = 0
            activity_id = int(self.ACTIVITY_IDS[sport_name])
            
            for court in availability.get("data", []):
                # Match by court name (e.g., "Court 1" in "Badminton Synthetic Court 1")
                if court_name in court.get("courtName", "") or court.get("courtName", "").endswith(court_name):
                    court_id = court.get("courtId")
                    full_court_name = court.get("courtName", court_name)
                    # Find the slot price
                    slot_time_api = f"{time_str}:00"
                    for slot in court.get("slots", []):
                        if slot.get("slotTime") == slot_time_api:
                            slot_price = slot.get("price", 0)
                            if slot.get("available") != 1:
                                raise Exception(f"Slot not available: {slot.get('status')}")
                            break
                    break
            
            if not court_id:
                raise Exception(f"Could not find court: {court_name} for {sport_name}")
            
            # 2. Add to cart (with full details)
            slot_time_api = f"{time_str}:00"
            await self.add_to_cart(
                page=page,
                court_id=court_id,
                court_name=full_court_name,
                slot_time=slot_time_api,
                slot_date=date_str,
                activity_id=activity_id,
                price=slot_price
            )
            
            # 3. Lookup customer
            customer_data = await self.lookup_customer(page, customer_phone)
            customer_details = customer_data.get("data", {}).get("customerDetails", {})
            non_member_id = customer_details.get("id")
            
            if not non_member_id:
                # Customer doesn't exist - they'll be created during booking
                # Use a placeholder; the booking API handles new customer creation
                non_member_id = None
                logger.info("New customer - will be created during booking")
            
            # 4. Reset credits
            await self.reset_credits(page)
            
            # 5. Apply full discount
            if slot_price > 0:
                await self.apply_discount(page, slot_price)
            
            # 6. Create booking
            result = await self.create_booking(
                page=page,
                non_member_id=non_member_id,
                gross_amount=slot_price,
                customer_name=customer_name,
                customer_phone=customer_phone,
                customer_email=customer_email,
                remarks=remarks
            )
            
            # 7. Clear cart
            await self.clear_cart(page)
            
            return result
            
        except Exception as e:
            logger.error(f"Booking failed: {e}")
            # Try to clear cart on failure
            try:
                await self.clear_cart(page)
            except:
                pass
            raise
