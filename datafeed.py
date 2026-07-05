"""Read-only access to the Polymarket data API."""
import requests

DATA_API = "https://data-api.polymarket.com"
PAGE_SIZE = 500

_session = requests.Session()
_session.headers["User-Agent"] = "pm-copybot/1.0"


def get_positions(address: str, size_threshold: float = 1.0) -> list:
    """All open positions for an address (paginated)."""
    positions, offset = [], 0
    while True:
        r = _session.get(
            f"{DATA_API}/positions",
            params={
                "user": address,
                "limit": PAGE_SIZE,
                "offset": offset,
                "sizeThreshold": size_threshold,
            },
            timeout=15,
        )
        r.raise_for_status()
        batch = r.json()
        positions.extend(batch)
        if len(batch) < PAGE_SIZE:
            return positions
        offset += PAGE_SIZE
