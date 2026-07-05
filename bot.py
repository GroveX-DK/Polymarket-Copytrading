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


def paper_book(state: dict, whale_positions: dict) -> tuple:
    """Settle resolved paper positions, value the rest at fresh prices."""
    latest = {p["asset"]: p for ps in whale_positions.values() for p in ps}
    positions = {}
    for token_id, pos in list(state["managed"].items()):
        won = None  # None = market still open (or unknown), else bool
        whale_pos = latest.get(token_id)
        if whale_pos is not None:
            pos["last_price"] = whale_pos["curPrice"]
            pos.setdefault("condition_id", whale_pos.get("conditionId", ""))
            if whale_pos.get("redeemable"):
                won = whale_pos["curPrice"] >= 0.5
        elif pos.get("condition_id"):
            # No tracked whale holds it anymore - ask the CLOB directly.
            try:
                status = datafeed.get_market_status(pos["condition_id"], token_id)
            except Exception as e:
                log.warning("Market status check failed for %s: %s",
                            pos.get("title", token_id[:16]), e)
                status = None
            if status:
                pos["last_price"] = status["price"]
                if status["closed"]:
                    won = status["winner"]

        if won is not None:
            payout = pos["shares"] * (1.0 if won else 0.0)
            state["cash"] += payout
            log.info("PAPER SETTLE %s: %s | %.2f shares -> $%.2f",
                     "WON" if won else "LOST", pos.get("title", token_id[:16]),
                     pos["shares"], payout)
            del state["managed"][token_id]
            continue

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
        if trade.condition_id:
            entry["condition_id"] = trade.condition_id
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

    managed = state["managed"]
    chunks = strategy.chunk_weights(whale_positions, cfg.max_chunks,
                                    cfg.min_chunk_weight, cfg.exit_chunk_weight,
                                    held=set(managed))
    now = time.time()
    recent = state.setdefault("recent", {})

    if cfg.dry_run:
        my_positions, cash = paper_book(state, whale_positions)
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
    log.info("portfolio=$%.2f (cash $%.2f + positions $%.2f) | whales=%d chunks=%d | trades=%d",
             bankroll, cash, bankroll - cash, len(whale_positions), len(chunks), len(trades))

    held = sorted(((t, my_positions[t]) for t in managed if t in my_positions),
                  key=lambda kv: kv[1]["currentValue"], reverse=True)
    for token_id, pos in held:
        value = pos["currentValue"]
        pct = 100 * value / bankroll if bankroll > 0 else 0.0
        if token_id in chunks:
            target = f"target {100 * cfg.copy_ratio * chunks[token_id]['weight']:.1f}%"
        else:
            target = "exiting"
        log.info("  %-45s $%6.2f = %4.1f%% of portfolio (%s)",
                 pos.get("title", token_id[:16]), value, pct, target)

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
