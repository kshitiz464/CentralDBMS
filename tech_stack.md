# Project Tech Stack: Smart Sports Facility Booking Bridge

## 1. Core Backend & API
*   **Language:** Python 3.10+
*   **Framework:** **FastAPI** (High-performance, async web framework).
*   **Server:** **Uvicorn** (ASGI server for handling asynchronous requests).
*   **Architecture:** Asynchronous Event-Driven Architecture using Python’s `asyncio` for non-blocking browser control and scraping.

## 2. Browser Automation & scraping
*   **Engine:** **Playwright (Python Async API)**
*   **Browser Integration:** **Chrome DevTools Protocol (CDP)**.
    *   *Unique Feature:* The system attaches to an **existing** Chrome instance (via Remote Debugging Port 9222) rather than launching a headless browser. This allows persistence of user sessions (WhatsApp Web login, Booking Portal logins) and avoids bot detection mechanisms.
*   **Scraping Strategies:**
    *   **DOM Manipulation (Standard):** Used for **Playo** integration. Direct interaction with HTML elements (Inputs, Buttons) to read slots and execute bookings.
    *   **Network Interception (Novel):** Used for **Hudle** integration.
        *   *Problem:* Hudle uses a Flutter-based UI which renders as a simplified Canvas/WebAssembly, making standard DOM scraping impossible (no access to text/buttons in DOM).
        *   *Solution:* The system utilizes Playwright's network sniffing capabilities (`page.on("response")`) to intercept raw JSON API payloads floating in the background, extracting slot availability data directly from the server response rather than the UI.

## 3. Data Persistence
*   **Database:** **SQLite3**
*   **Why:** Lightweight, serverless, and perfectly suited for a local-first application. Stores booking logs, synchronization state, and cache.
*   **Schema:** Relational storage for `Bookings` with strict constraints to prevent double-booking.

## 4. Frontend & monitoring
*   **Dashboard:** HTML5, CSS3, JavaScript (Fetch API).
*   **Function:** Real-time dashboard to monitor scraper status, view current bookings, and manually trigger sync cycles.

## 5. Infrastructure & Deployment
*   **Operating System:** Windows 10/11.
*   **Tunneling:** **Ngrok** (Secure tunneling to expose the local FastAPI webhook to external services like WhatsApp bots).
*   **Process Management:** Custom Windows Batch Scripts (`.bat`) for "One-Click" startup of the database, browser debugging access, and Python server.

## 6. Key Libraries
*   `playwright`: For browser automation.
*   `fastapi`: Web server.
*   `uvicorn`: ASGI application server.
*   `httpx`: Async HTTP client.
*   `pydantic`: Data validation and settings management.
