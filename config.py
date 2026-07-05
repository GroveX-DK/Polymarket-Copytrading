"""Load configuration from .env and whales.txt."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


def _bool(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    whales: list
    dry_run: bool
    private_key: str
    funder: str
    signature_type: int
    copy_ratio: float
    max_chunks: int
    min_chunk_weight: float
    exit_chunk_weight: float
    min_order_usd: float
    rebalance_band: float
    order_type: str
    poll_seconds: int
    paper_balance: float
    log_level: str


def load() -> Config:
    load_dotenv(ROOT / ".env")

    whales = []
    whales_file = ROOT / "whales.txt"
    if whales_file.exists():
        for line in whales_file.read_text().splitlines():
            addr = line.split("#", 1)[0].strip().lower()
            if addr:
                whales.append(addr)

    min_chunk_weight = float(os.getenv("MIN_CHUNK_WEIGHT", "0.10"))
    exit_raw = os.getenv("EXIT_CHUNK_WEIGHT", "").strip()

    cfg = Config(
        whales=whales,
        dry_run=_bool(os.getenv("DRY_RUN", "true")),
        private_key=os.getenv("PRIVATE_KEY", "").strip(),
        funder=os.getenv("FUNDER_ADDRESS", "").strip(),
        signature_type=int(os.getenv("SIGNATURE_TYPE", "1")),
        copy_ratio=float(os.getenv("COPY_RATIO", "1.0")),
        max_chunks=int(os.getenv("MAX_CHUNKS", "15")),
        min_chunk_weight=min_chunk_weight,
        # Hysteresis: a chunk already held is only dropped when it falls
        # below this weight, so it doesn't flip-flop around the entry cutoff.
        exit_chunk_weight=float(exit_raw) if exit_raw else 0.8 * min_chunk_weight,
        min_order_usd=float(os.getenv("MIN_ORDER_USD", "1.0")),
        rebalance_band=float(os.getenv("REBALANCE_BAND", "0.05")),
        order_type=os.getenv("ORDER_TYPE", "FAK").strip().upper(),
        poll_seconds=int(os.getenv("POLL_SECONDS", "60")),
        paper_balance=float(os.getenv("PAPER_BALANCE", "10")),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
    )

    if not cfg.whales:
        raise SystemExit("whales.txt is empty - add at least one address to copy.")
    if not cfg.dry_run and (not cfg.private_key or not cfg.funder):
        raise SystemExit("DRY_RUN=false requires PRIVATE_KEY and FUNDER_ADDRESS in .env.")
    return cfg
