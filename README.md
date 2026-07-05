# Polymarket copytrading bot

Polls a list of whale wallets every minute, bundles their positions into a
handful of big chunks, and mirrors those chunks proportionally in your own
account. Built to run 24/7 on a Raspberry Pi 5 with 1 GB RAM.

## How it works

Every `POLL_SECONDS` (default 60):

1. **Analyse whales** - fetches all open positions for every address in
   `whales.txt` from the Polymarket data API.
2. **Bundle into chunks** - each whale's positions become portfolio weights;
   weights are averaged across whales, then only the top `MAX_CHUNKS` above
   `MIN_CHUNK_WEIGHT` are kept. 10-50 dust positions collapse into ~15 chunks.
3. **Trade relative to your account** - each chunk's weight is applied to your
   bankroll (USDC + bot-held positions). The bot buys the gap when you're
   under target, trims when over, and exits when the whales exit. Minimum
   order is $1 (`MIN_ORDER_USD`), and rebalances only fire when a position
   drifts more than `REBALANCE_BAND` from target, so it doesn't churn.

The bot only ever sells positions it opened itself (tracked in `state.json`),
so your manual positions are never touched. Resolved markets are skipped -
redeem winnings in the Polymarket UI.

## Setup (Raspberry Pi 5)

```bash
sudo apt update && sudo apt install -y python3-venv
git clone <this repo> /home/pi/polymarket-copybot   # or copy the folder over
cd /home/pi/polymarket-copybot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # ARM wheels come from piwheels
cp .env.example .env
nano .env        # fill in your settings
nano whales.txt  # one whale address per line
```

Start in paper mode first (the default, `DRY_RUN=true`):

```bash
.venv/bin/python bot.py
```

It logs every trade it *would* make against a simulated `PAPER_BALANCE`.

### Going live

1. In the Polymarket web app: Settings -> Export private key -> `PRIVATE_KEY`.
2. Your profile address (where your USDC lives) -> `FUNDER_ADDRESS`.
3. `SIGNATURE_TYPE=1` for email/Magic login, `2` for MetaMask login.
4. Set `DRY_RUN=false`.

Your USDC allowances are already set if you've ever traded through the
Polymarket website.

### Run 24/7 with systemd

```bash
sudo cp copybot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now copybot
journalctl -u copybot -f   # watch the logs
```

The unit restarts the bot on any crash and caps memory at 300 MB.

## Files

| File | Purpose |
|------|---------|
| `bot.py` | Main loop, state, paper book |
| `datafeed.py` | Polymarket data API (whale + own positions) |
| `strategy.py` | Chunking and trade planning |
| `executor.py` | CLOB market orders (or paper logging) |
| `config.py` | `.env` + `whales.txt` loading |
| `state.json` | Runtime state - which positions the bot owns |

## Notes

- Market orders are `FAK` (fill what's available) by default; unfilled
  remainders are retried automatically on later cycles because the gap to
  target persists.
- A 5-minute per-token cooldown prevents double-trading while the data API
  catches up with fresh fills.
- Keep `.env` private - it contains your wallet's private key.
