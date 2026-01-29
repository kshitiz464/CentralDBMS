import asyncio
import logging
import socket
import tkinter as tk
from tkinter import messagebox
from playwright.async_api import async_playwright, BrowserContext, Page

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ConnectionManager")

class ConnectionManager:
    def __init__(self, cdp_url="http://127.0.0.1:9222"):
        self.cdp_url = cdp_url
        self.playwright = None
        self.browser = None
        self.context: BrowserContext = None
        self.playo_tab: Page = None
        self.hudle_tab: Page = None

    async def _show_error_popup(self, title, message):
        """Shows a blocking error popup using tkinter."""
        # Tkinter needs to run in the main thread usually, but for simple messageboxes
        # we can try to spawn a small process or just run it here if we accept blocking.
        # Since the requirement says "Wait for user confirmation", blocking is desired.
        root = tk.Tk()
        root.withdraw()  # Hide main window
        root.attributes("-topmost", True) # Make sure it's visible
        messagebox.showerror(title, message)
        root.destroy()

    async def _show_info_popup(self, title, message):
        """Shows a blocking info popup."""
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showinfo(title, message)
        root.destroy()

    async def check_browser_connection(self):
        """Check 1: Try connecting to Chrome via CDP."""
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.connect_over_cdp(self.cdp_url)
            self.context = self.browser.contexts[0]
            logger.info("Successfully connected to Chrome.")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to browser: {e}")
            await self._show_error_popup(
                "Browser Error", 
                "Error: Chrome is not open with remote debugging.\nPlease run the 'start_chrome_debug.bat' shortcut."
            )
            return False

    async def check_tabs(self):
        """Check 2: Ensure Playo and Hudle tabs are open."""
        if not self.context:
            return False

        pages = self.context.pages
        self.playo_tab = None
        self.hudle_tab = None

        for page in pages:
            url = page.url
            if "playo.club" in url:
                self.playo_tab = page
            elif "partner.hudle.in" in url:
                self.hudle_tab = page

        if not self.playo_tab:
            logger.info("Playo tab not found. Opening...")
            self.playo_tab = await self.context.new_page()
            await self.playo_tab.goto("https://dashboard.playo.club/")
        
        if not self.hudle_tab:
            logger.info("Hudle tab not found. Opening...")
            self.hudle_tab = await self.context.new_page()
            await self.hudle_tab.goto("https://partner.hudle.in/")
            
        return True

    async def check_login_status(self):
        """Check 3: Verify login status on both portals."""
        if not self.playo_tab or not self.hudle_tab:
            return False

        # These selectors are hypothetical placeholders. 
        # In a real scenario, we'd need exact selectors. 
        # Using generic text checks or likely class names for now.
        
        async def is_logged_in(page: Page, setup_name):
            try:
                # Example: Check for a "Login" button. If present, we are NOT logged in.
                # Or check for a "Profile" icon/Dashboard element.
                # Adjust selectors as per actual site structure.
                
                # Heuristic: If we see "Login" text in a button, we are probably out.
                # Playo: Look for 'Login' or specific dashboard element
                # Hudle: Look for 'Login'
                
                # This is a bit fragile without seeing the DOM, but following the spec:
                # "Check for a 'Login' button or specific dashboard element"
                
                # We'll try to find a Login button.
                login_button = await page.query_selector("text=Login") # Simple text match
                if login_button:
                     # Double check visibility
                     if await login_button.is_visible():
                         return False
                
                return True # Assume logged in if no Login button found
            except Exception:
                return False

        playo_in = await is_logged_in(self.playo_tab, "Playo")
        hudle_in = await is_logged_in(self.hudle_tab, "Hudle")

        if not playo_in or not hudle_in:
            await self._show_info_popup(
                "Login Required",
                "Please Log In to Playo/Hudle in the open browser window, then click OK."
            )
            # Re-check? Or just assume user did it? 
            # The spec says "Wait for user confirmation before proceeding."
            # The popup is blocking, so we wait. 
            # We return True to proceed, trusting the user.
            return True
            
        return True

    async def check_network_and_connectivity(self):
        """Check 4: Simple network check."""
        # If we can reach google, we have internet? 
        # Or we can rely on Playwright navigation errors.
        # Here we just implement a basic connectivity check.
        try:
            # Using socket to check connectivity to a reliable host
            socket.create_connection(("8.8.8.8", 53), timeout=3)
            return True
        except OSError:
            await self._show_error_popup(
                "Network Error",
                "Internet Connection Lost. Sync Paused."
            )
            return False

    async def initialize(self):
        """Runs the full health check sequence."""
        if not await self.check_browser_connection():
            return False
        if not await self.check_tabs():
            return False
        if not await self.check_login_status():
            return False # Should not happen if user clicked OK
        if not await self.check_network_and_connectivity():
            return False
            
        logger.info("Health check passed. System ready.")
        return True

    async def close(self):
        if self.playwright:
            await self.playwright.stop()
