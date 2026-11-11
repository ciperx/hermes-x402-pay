"""Tool handlers. Every money decision happens here, before a signature exists."""
from __future__ import annotations

import os

try:  # package-relative when loaded by Hermes, flat when run from the repo
    from . import x402payer
except ImportError:  # pragma: no cover
    import x402payer

KEY_ENV_DEFAULT = "X402_PRIVATE_KEY"


def _cfg(ctx, key, default):
    try:
        return ctx.get_config(key, default=default)
    except Exception:
        return default


def budget_report(ledger, ctx):
    spent = ledger.spend_last_24h()
    budget = float(_cfg(ctx, "daily_budget_usd", 0.05))
    dry = bool(_cfg(ctx, "dry_run", True))
    rows = ledger.recent(limit=5)
    lines = [
        f"x402 spend (24h): ${spent:.6f} of ${budget:.6f} budget",
        f"dry_run: {'ON -- nothing can be paid' if dry else 'off'}",
        f"ledger: {ledger.path}",
    ]
    for r in rows:
        mark = "PAID" if r["paid"] else "----"
        lines.append(f"  {mark} ${r['price_usd']:.6f} {r['method']} {r['status']} {r['url'][:70]} {r['tx_hash'][:14]}")
    return "\n".join(lines)


def _key(ctx):
    env_name = _cfg(ctx, "private_key_env", KEY_ENV_DEFAULT)
    key = os.environ.get(env_name, "")
    if not key:
        return None, (f"no signing key: set the {env_name} env var to a 0x-prefixed private key. "
                      "Use a dedicated low-balance wallet, never a main one.")
    if not key.startswith("0x") or len(key) < 66:
        return None, "signing key does not look like a 32-byte hex key"
    return key, ""


def x402_pay(ctx, ledger, url, method=None, body=None, headers=None, max_usd=None,
             network="base", dry_run=None):
    dry = bool(_cfg(ctx, "dry_run", True)) if dry_run is None else bool(dry_run)
    per_call = float(max_usd if max_usd is not None else _cfg(ctx, "per_call_cap_usd", 0.01))
    daily = float(_cfg(ctx, "daily_budget_usd", 0.05))
    key, key_err = _key(ctx)

    # Probe first, decide second: never sign before the price is known.
    method = (method or ("POST" if body is not None else "GET")).upper()
    status, hdrs, raw = x402payer._http(url, method, body, headers)
    if status != 402:
        return {"paid": False, "status": status, "method": method, "price_usd": 0.0,
                "body": raw.decode("utf-8", "replace"),
                "note": "endpoint did not ask for payment; nothing signed"}

    try:
        import json as _json
        req = _json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return {"paid": False, "status": 402, "method": method, "error": "402 body was not JSON"}
    accept, reason = x402payer.pick_accept(req.get("accepts"), network)
    if not accept:
        return {"paid": False, "status": 402, "method": method, "error": reason}

    price = int(accept.get("maxAmountRequired") or 0) / 1_000_000
    if price > per_call + 1e-12:
        return {"paid": False, "status": 402, "method": method, "price_usd": price,
                "error": f"price ${price:.6f} exceeds per-call cap ${per_call:.6f}"}
    spent = ledger.spend_last_24h()
    if spent + price > daily + 1e-12:
        return {"paid": False, "status": 402, "method": method, "price_usd": price,
                "error": f"rolling 24h budget would be exceeded (${spent:.6f} + ${price:.6f} > ${daily:.6f})"}
    if dry:
        return {"paid": False, "dry_run": True, "status": 402, "method": method,
                "price_usd": price, "pay_to": accept.get("payTo"), "network": accept.get("network"),
                "note": "dry_run is ON; enable with: hermes plugins config hermes-x402-pay dry_run false"}
    if key_err:
        return {"paid": False, "status": 402, "method": method, "price_usd": price, "error": key_err}

    out = x402payer.pay(url, body=body, method=method, key=key, max_usd=per_call,
                        network=network, headers=headers)
    out.setdefault("warning", reason or None)
    ledger.record(url=url, method=method, status=out.get("status", 0), paid=out.get("paid", False),
                  price_usd=out.get("price_usd", 0.0), payer=out.get("payer", ""),
                  pay_to=out.get("pay_to", ""), tx_hash=out.get("tx_hash", ""),
                  note=out.get("error") or out.get("note") or "")
    return out


def x402_receipts(ledger, limit=10, since_hours=None, paid_only=False):
    rows = ledger.recent(limit=limit, since_hours=since_hours, paid_only=paid_only)
    return {"count": len(rows), "spend_24h_usd": ledger.spend_last_24h(),
            "ledger_path": ledger.path, "rows": rows}
