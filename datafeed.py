"""Read-only access to the Polymarket data API."""
import requests

DATA_API = "https://data-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
PAGE_SIZE = 500

_session = requests.Session()
_session.headers["User-Agent"] = "pm-copybot/1.0"


def get_market_status(condition_id: str, token_id: str):
    """Price, resolution status and winner for one outcome token (public CLOB).

    Used in paper mode when no tracked whale holds the token anymore.
    Returns {"price": float, "closed": bool, "winner": bool} or None.
    """
    r = _session.get(f"{CLOB_API}/markets/{condition_id}", timeout=15)
    r.raise_for_status()
    market = r.json()
    for token in market.get("tokens", []):
        if token["token_id"] == token_id:
            return {
                "price": float(token["price"]),
                "closed": bool(market.get("closed")),
                "winner": bool(token["winner"]),
            }
    return None


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
