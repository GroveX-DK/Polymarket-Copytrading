"""Polymarket copytrading bot - polls whale positions and mirrors them in chunks."""
import json
import logging
import time
from pathlib import Path

import config
import datafeed
import strategy
from executor import Executor

log = logging.getLogger("bot")

STATE_FILE = Path(__file__).resolve().parent / "state.json"
# Don't re-buy/re-sell the same token within this window; the data API can
# lag a filled order by a cycle or two, which would otherwise double-trade.
COOLDOWN_SECONDS = 300


def load_state(cfg) -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"cash": cfg.paper_balance, "managed": {}, "recent": {}}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=1))


def paper_book(state: dict, chunks: dict) -> tuple:
    """Value simulated holdings at the freshest known price."""
    positions = {}
    for token_id, pos in state["managed"].items():
        if token_id in chunks:
            pos["last_price"] = chunks[token_id]["meta"]["curPrice"]
        price = pos.get("last_price", 0.5)
        positions[token_id] = {
            "currentValue": pos["shares"] * price,
            "size": pos["shares"],
            "curPrice": price,
            "title": pos.get("title", ""),
            "negativeRisk": pos.get("neg_risk", False),
        }
    return positions, state["cash"]


def apply_fill(state: dict, trade, cfg):
    managed = state["managed"]
    if trade.side == "BUY":
        entry = managed.setdefault(trade.token_id, {
            "shares": 0.0, "last_price": trade.price,
            "title": trade.title, "neg_risk": trade.neg_risk,
        })
        entry["last_price"] = trade.price
        if cfg.dry_run:
            entry["shares"] += trade.usd / trade.price
            state["cash"] -= trade.usd
    elif cfg.dry_run:
        entry = managed.get(trade.token_id)
        if entry:
            entry["shares"] = max(0.0, entry["shares"] - trade.shares)
            state["cash"] += trade.usd
            if entry["shares"] < 0.01:
                del managed[trade.token_id]
    # Live sells: on-chain balances are authoritative; the next cycle's
    # position fetch reflects the fill, and full exits are pruned below.


def cycle(cfg, ex: Executor, state: dict):
    whale_positions = {}
    for addr in cfg.whales:
        try:
            whale_positions[addr] = datafeed.get_positions(addr)
        except Exception as e:
            log.warning("Could not fetch whale %s: %s", addr, e)
    if not whale_positions:
        return

    chunks = strategy.chunk_weights(whale_positions, cfg.max_chunks, cfg.min_chunk_weight)
    managed = state["managed"]
    now = time.time()
    recent = state.setdefault("recent", {})

    if cfg.dry_run:
        my_positions, cash = paper_book(state, chunks)
    else:
        my_positions = {p["asset"]: p for p in datafeed.get_positions(cfg.funder, size_threshold=0.1)}
        cash = ex.usdc_balance()
        # Prune fully exited/redeemed positions (grace period for API lag).
        for token_id in list(managed):
            if token_id not in my_positions and now - recent.get(f"BUY:{token_id}", 0) > 2 * COOLDOWN_SECONDS:
                del managed[token_id]

    bankroll = cash + sum(
        my_positions[t]["currentValue"] for t in managed if t in my_positions
    )
    trades = strategy.plan_trades(chunks, my_positions, managed, bankroll, cash, cfg)
    log.info("whales=%d chunks=%d | bankroll=$%.2f cash=$%.2f | trades=%d",
             len(whale_positions), len(chunks), bankroll, cash, len(trades))

    for trade in trades:
        key = f"{trade.side}:{trade.token_id}"
        if now - recent.get(key, 0) < COOLDOWN_SECONDS:
            continue
        if ex.execute(trade):
            recent[key] = now
            apply_fill(state, trade, cfg)

    state["recent"] = {k: v for k, v in recent.items() if now - v < 3600}


def main():
    cfg = config.load()
    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    ex = Executor(cfg)
    state = load_state(cfg)
    log.info("Copying %d whale(s) every %ds | mode=%s",
             len(cfg.whales), cfg.poll_seconds, "PAPER" if cfg.dry_run else "LIVE")

    while True:
        started = time.time()
        try:
            cycle(cfg, ex, state)
        except Exception:
            log.exception("Cycle failed; retrying next tick")
        save_state(state)
        time.sleep(max(5.0, cfg.poll_seconds - (time.time() - started)))


if __name__ == "__main__":
    main()
