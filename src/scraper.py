"""Website scraper for publicly available boutique details.

Uses ordinary HTTP requests and BeautifulSoup. Playwright is out of scope
until testing shows it is required.

Not implemented in this increment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class BoutiqueRecord:
    """Public boutique fields stored in SQLite and exported to Excel."""

    name: str | None = None
    city: str | None = None
    address: str | None = None
    website: str | None = None
    instagram: str | None = None
    email: str | None = None
    phone: str | None = None
    source_url: str | None = None
    date_discovered: str | None = None
    id: int | None = None

    def to_dict(self) -> dict[str, str | int | None]:
        """Return a serializable mapping used by the database and Excel layers."""
        return {
            "id": self.id,
            "name": self.name,
            "city": self.city,
            "address": self.address,
            "website": self.website,
            "instagram": self.instagram,
            "email": self.email,
            "phone": self.phone,
            "source_url": self.source_url,
            "date_discovered": self.date_discovered,
        }


class WebsiteScraper:
    """Extract public contact and identity fields from a boutique website."""

    def scrape(self, url: str, city: str) -> BoutiqueRecord:
        """Fetch ``url`` and extract boutique fields.

        Args:
            url: Boutique website to fetch.
            city: City associated with the current search.

        Returns:
            BoutiqueRecord: Extracted public fields. Missing values are ``None``.

        Raises:
            NotImplementedError: Scraping is not implemented yet.
        """
        raise NotImplementedError(
            "Website scraping is not implemented in this increment. "
            "The next step will fetch pages with requests and BeautifulSoup."
        )


def today_iso() -> str:
    """Return today's date as ISO ``YYYY-MM-DD`` for ``date_discovered``."""
    return date.today().isoformat()
