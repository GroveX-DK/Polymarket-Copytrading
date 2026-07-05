"""Order execution: live via the Polymarket CLOB, or simulated paper fills."""
import logging

log = logging.getLogger("executor")

CLOB_HOST = "https://clob.polymarket.com"
POLYGON = 137


class Executor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.client = None
        if not cfg.dry_run:
            # Import lazily so paper mode works without the CLOB stack loaded.
            from py_clob_client.client import ClobClient

            self.client = ClobClient(
                CLOB_HOST,
                key=cfg.private_key,
                chain_id=POLYGON,
                signature_type=cfg.signature_type,
                funder=cfg.funder,
            )
            self.client.set_api_creds(self.client.create_or_derive_api_creds())
            log.info("CLOB client ready (funder %s)", cfg.funder)

    def usdc_balance(self) -> float:
        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

        res = self.client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        return int(res["balance"]) / 1e6

    def execute(self, trade) -> bool:
        """Place one trade. Returns True if it (at least partially) filled."""
        tag = "PAPER" if self.cfg.dry_run else "LIVE"
        log.info("%s %s $%.2f%s @ ~%.3f | %s", tag, trade.side, trade.usd,
                 "" if trade.side == "BUY" else f" ({trade.shares} sh)",
                 trade.price, trade.title)
        if self.cfg.dry_run:
            return True

        from py_clob_client.clob_types import (
            MarketOrderArgs,
            OrderType,
            PartialCreateOrderOptions,
        )
        from py_clob_client.order_builder.constants import BUY, SELL

        order_type = OrderType.FOK if self.cfg.order_type == "FOK" else OrderType.FAK
        # Market orders: amount is dollars for buys, shares for sells.
        args = MarketOrderArgs(
            token_id=trade.token_id,
            amount=trade.usd if trade.side == "BUY" else trade.shares,
            side=BUY if trade.side == "BUY" else SELL,
            order_type=order_type,
        )
        options = PartialCreateOrderOptions(neg_risk=trade.neg_risk)

        try:
            signed = self.client.create_market_order(args, options)
            res = self.client.post_order(signed, order_type)
        except Exception:
            log.exception("Order failed: %s %s", trade.side, trade.title)
            return False

        ok = bool(res) and res.get("success", False)
        log.info("Order result: %s", res)
        return ok
