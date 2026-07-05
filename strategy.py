"""Bundle whale positions into chunks and diff them against our account."""
import math
from dataclasses import dataclass


@dataclass
class Trade:
    side: str  # "BUY" or "SELL"
    token_id: str
    usd: float  # dollar amount to buy, or dollar value being sold
    shares: float  # shares to sell (0 for buys)
    price: float  # last known price, used for paper fills
    neg_risk: bool
    title: str
    condition_id: str = ""  # market id, kept so paper mode can check resolution


def tradable(p: dict) -> bool:
    """Open, unresolved position in a market we can still trade."""
    price = p.get("curPrice") or 0
    return not p.get("redeemable") and 0.0 < price < 1.0


def chunk_weights(whale_positions: dict, max_chunks: int, min_weight: float,
                  exit_weight: float, held=frozenset()) -> dict:
    """Aggregate whale portfolios into at most max_chunks target weights.

    Each whale's positions become weights of that whale's total position
    value; weights are averaged across whales (so a market held by several
    whales becomes one bigger chunk). A chunk is copied once it reaches
    min_weight; a chunk we already hold stays until it falls below
    exit_weight, so it doesn't flip-flop around the entry cutoff.
    Returns {token_id: {"weight": w, "meta": position}}.
    """
    per_asset = {}
    active_whales = 0
    for positions in whale_positions.values():
        positions = [p for p in positions if tradable(p)]
        total = sum(p["currentValue"] for p in positions)
        if total <= 0:
            continue
        active_whales += 1
        for p in positions:
            slot = per_asset.setdefault(p["asset"], {"weight": 0.0, "meta": p})
            slot["weight"] += p["currentValue"] / total
            slot["meta"] = p  # keep freshest price/flags
    if not active_whales:
        return {}

    chunks = {}
    for token_id, slot in per_asset.items():
        weight = slot["weight"] / active_whales
        if weight >= min_weight or (token_id in held and weight >= exit_weight):
            chunks[token_id] = {"weight": weight, "meta": slot["meta"]}
    ranked = sorted(chunks.items(), key=lambda kv: kv[1]["weight"], reverse=True)
    return dict(ranked[:max_chunks])


def _floor2(x: float) -> float:
    return math.floor(x * 100) / 100


def plan_trades(chunks: dict, my_positions: dict, managed: dict,
                bankroll: float, cash: float, cfg) -> list:
    """Diff chunk targets against our holdings and return the trades to place.

    my_positions: {token_id: {"currentValue": usd, "size": shares, "curPrice": p}}
    managed: state dict of positions this bot opened - only these are ever sold.
    """
    sells, buys = [], []

    # Exits and trims first - they free up cash for the buys.
    for token_id, held in my_positions.items():
        if token_id not in managed:
            continue  # never touch positions the user opened manually
        price = held.get("curPrice") or 0
        if not (0.0 < price < 1.0):
            continue  # market resolved/closed; redeem happens outside the bot
        value = held["currentValue"]
        target = bankroll * cfg.copy_ratio * chunks[token_id]["weight"] if token_id in chunks else 0.0
        excess = value - target
        if excess < cfg.min_order_usd:
            continue
        if target > 0 and excess / target < cfg.rebalance_band:
            continue  # inside the drift band, leave it alone
        shares = held["size"] if target == 0 else min(held["size"], _floor2(excess / price))
        if shares <= 0:
            continue
        sells.append(Trade("SELL", token_id, round(shares * price, 2), shares, price,
                           bool(held.get("negativeRisk")), held.get("title", token_id[:16])))

    # Buys, biggest gap first.
    gaps = []
    for token_id, chunk in chunks.items():
        target = bankroll * cfg.copy_ratio * chunk["weight"]
        value = my_positions.get(token_id, {}).get("currentValue", 0.0)
        gap = target - value
        if gap < cfg.min_order_usd:
            continue
        if value > 0 and gap / target < cfg.rebalance_band:
            continue
        gaps.append((gap, token_id, chunk))
    gaps.sort(reverse=True)

    available = cash + sum(t.usd for t in sells)
    for gap, token_id, chunk in gaps:
        usd = _floor2(min(gap, available * 0.99))  # keep a sliver of dust for fees/rounding
        if usd < cfg.min_order_usd:
            continue
        meta = chunk["meta"]
        buys.append(Trade("BUY", token_id, usd, 0.0, meta["curPrice"],
                          bool(meta.get("negativeRisk")), meta.get("title", token_id[:16]),
                          meta.get("conditionId", "")))
        available -= usd

    return sells + buys
