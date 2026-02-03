from abc import ABC, abstractmethod
from playwright.async_api import Page
from typing import List, Optional, Any, Dict

class BaseScraper(ABC):
    @abstractmethod
    def get_name(self) -> str:
        """Returns the name of the scraper (e.g., 'Playo', 'Hudle')."""
        pass

    @abstractmethod
    async def scrape(self, page: Page, scrape_requests: List[Dict[str, Any]]) -> Any:
        """
        Executes the scraping logic.
        :param page: Playwright Page object (tab).
        :param scrape_requests: List of dicts, e.g., 
               [{'date': '2025-01-01', 'force': True, 'limit_sports': [...]}]
        """
        pass
