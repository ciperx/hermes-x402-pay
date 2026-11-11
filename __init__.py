"""hermes-x402-pay -- pay x402 endpoints from inside Hermes, with spend caps.

Design rules this plugin follows (why, not just what):

* Dry-run by default. A plugin that can move money must be inert until the user
  explicitly enables it (`hermes plugins config hermes-x402-pay dry_run false`).
* Two caps, checked before signing: a per-call cap and a rolling 24h budget.
  The budget is derived from the receipt ledger, so it survives restarts.
* The private key is read from an env var whose NAME is configurable; the key
  itself is never logged, never written to the ledger, and never returned.
* Every paid call is recorded twice: returned to the model AND appended to the
  SQLite ledger by the post_tool_call hook, so the audit trail survives context
  truncation.
"""
import logging

from . import schemas, tools
from .ledger import Ledger

logger = logging.getLogger(__name__)

_LEDGER = None


def _ledger():
    global _LEDGER
    if _LEDGER is None:
        _LEDGER = Ledger()
    return _LEDGER


def _on_post_tool_call(tool_name, args, result, **kwargs):
    """Persist every x402_pay outcome, including failures with a price attached.

    The tool's return value is what the model reads; this row is what the user
    audits later. They are deliberately separate.
    """
    if tool_name not in ("x402_pay",):
        return
    try:
        data = result if isinstance(result, dict) else {}
        body = data.get("structuredContent") or data
        if not isinstance(body, dict):
            return
        _ledger().record(
            url=str(args.get("url", "")),
            method=str(body.get("method") or args.get("method") or "GET"),
            status=int(body.get("status") or 0),
            paid=bool(body.get("paid")),
            price_usd=float(body.get("price_usd") or 0.0),
            payer=str(body.get("payer") or ""),
            pay_to=str(body.get("pay_to") or ""),
            tx_hash=str(body.get("tx_hash") or ""),
            note=str(body.get("error") or ""),
        )
    except Exception:  # never break a session because auditing failed
        logger.debug("x402 ledger append failed", exc_info=True)


def handle_x402_command(ctx, raw_args):
    """/x402 -- show budget state and the last few payments."""
    return tools.budget_report(_ledger(), ctx)


def register(ctx):
    ctx.register_tool(name="x402_pay", toolset="x402",
                      schema=schemas.X402_PAY,
                      handler=lambda **kw: tools.x402_pay(ctx, _ledger(), **kw))
    ctx.register_tool(name="x402_receipts", toolset="x402",
                      schema=schemas.X402_RECEIPTS,
                      handler=lambda **kw: tools.x402_receipts(_ledger(), **kw))
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_command("x402", lambda raw: handle_x402_command(ctx, raw),
                         description="Show x402 spend budget and recent receipts")
