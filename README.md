# Playshire Central Management System 🏟️

A local automation server for synchronizing sports facility bookings between Playo (Web), Hudle (API), and an internal dashboard.

## 📋 Prerequisites
1.  **Windows OS** (required for the batch scripts).
2.  **Google Chrome** installed at default location (`C:\Program Files\Google\Chrome\Application\chrome.exe`).
3.  **Python 3.11** or higher. [Download Here](https://www.python.org/downloads/).

## 🚀 Installation (First Time)
1.  Unzip the project folder to your desired location (e.g., `D:\CentralManagement`).
2.  Double-click **`install.bat`**.
    *   This will create a virtual environment (`venv`).
    *   It will install all required libraries.
    *   It will download the necessary browser drivers.
3.  Ensure your **`.env`** file is present in the root directory with your credentials:
    ```ini
    SERVER_USERNAME=admin
    SERVER_PASSWORD=your_secure_password
    ```

## ▶️ How to Run
1.  Double-click **`run_server.bat`**.
    *   It will automatically open Chrome in **Debug Mode** (Port 9222).
    *   It will start the **FastAPI Server**.
    *   It will open the **Dashboard** in your default browser.
2.  **Log in** to Playo and Hudle in the opened Chrome window if not already logged in.

## 🛠️ Features
*   **Unified Dashboard**: View bookings from Playo and Hudle in one place.
*   **Auto-Sync**: Background syncing every 10 minutes.
*   **Panic Button**: Emergency stop to lock the system.
*   **Fast Scraper**: "Turbo Mode" for Hudle (Parallel) + Stealth Mode for Playo.

## 📂 Key Files
*   `install.bat` - One-click setup script.
*   `run_server.bat` - One-click launch script.
*   `access.log` - Logs of all system traffic.
*   `dashboard.html` - The main user interface.
