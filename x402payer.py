"""x402 payment client for EVM chains -- Base USDC via EIP-3009.

The wire format was reverse-engineered against a live x402 service and validated by
diffing our payload with the reference JavaScript client's payload at a local echo
server. Three details matter and each one cost a failed payment to find:

1. ``signature`` must be hex WITH the ``0x`` prefix. ``eth_account`` returns
   ``HexBytes.hex()`` without it on 0.14, and a verifier that parses the signature
   as a 65-byte hex string answers "payment verification unavailable" instead of
   telling you why.
2. ``validAfter`` is an absolute timestamp slightly in the past (now - 30s), not 0.
3. ``validBefore`` is roughly 11x the endpoint's ``maxTimeoutSeconds`` -- the
   facilitator settles the authorization on-chain after the response is produced.

Only the ``exact`` scheme on an EVM chain is implemented. Solana x402 uses a
completely different payload (a partially signed transaction) and is rejected
explicitly rather than silently mispaid.
"""
from __future__ import annotations

import base64
import json
import secrets as _secrets
import time
import urllib.error
import urllib.request

NETWORKS = {
    "base": {
        "chain_id": 8453,
        "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "domain_name": "USD Coin",
        "domain_version": "2",
    },
}

MAX_RESPONSE_BYTES = 200_000


class X402Error(Exception):
    """Raised for anything the caller must see: bad price, bad payload, no evm option."""


def _http(url, method="GET", body=None, headers=None, timeout=45):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "hermes-x402-pay/0.1")
    for k, v in (headers or {}).items():
        req.add_header(str(k), str(v))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read(MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read(MAX_RESPONSE_BYTES)


def pick_accept(accepts, network="base"):
    """Return (chosen_accept, reason). Prefers the requested network, else any evm one."""
    wanted = NETWORKS.get(network, {})
    for a in accepts or []:
        if a.get("network") == network and a.get("scheme") == "exact":
            return a, ""
    evm = [a for a in accepts or [] if a.get("network") in NETWORKS]
    if evm:
        return evm[0], f"requested {network} not offered, fell back to {evm[0]['network']}"
    if wanted:
        return None, "server offers no supported EVM payment option (solana-only endpoint?)"
    return None, "no payment options in 402 response"


def build_payment(key, accept, value_atomic, valid_seconds=None):
    """Sign an EIP-3009 TransferWithAuthorization and return the X-PAYMENT payload."""
    from eth_account import Account
    from eth_account.messages import encode_typed_data

    net = NETWORKS.get(accept.get("network"))
    if not net:
        raise X402Error(f"unsupported network: {accept.get('network')}")
    asset = accept.get("asset") or net["asset"]
    extra = accept.get("extra") or {}
    name = extra.get("name") or net["domain_name"]
    version = extra.get("version") or net["domain_version"]
    seconds = int(valid_seconds or accept.get("maxTimeoutSeconds") or 60)

    acct = Account.from_key(key)
    now = int(time.time())
    auth = {
        "from": acct.address,
        "to": accept["payTo"],
        "value": int(value_atomic),
        "validAfter": now - 30,
        "validBefore": now + max(60, seconds) * 11,
        "nonce": "0x" + _secrets.token_hex(32),
    }
    full = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ],
        },
        "primaryType": "TransferWithAuthorization",
        "domain": {
            "name": name,
            "version": version,
            "chainId": net["chain_id"],
            "verifyingContract": asset,
        },
        "message": auth,
    }
    signed = acct.sign_message(encode_typed_data(full_message=full))
    sig_hex = signed.signature.hex()
    if not sig_hex.startswith("0x"):
        sig_hex = "0x" + sig_hex  # see module docstring, point 1
    return {
        "x402Version": 1,
        "scheme": "exact",
        "network": accept.get("network"),
        "payload": {
            "signature": sig_hex,
            "authorization": {k: str(v) for k, v in auth.items()},
        },
    }, acct.address


def decode_receipt(headers):
    """Decode X-PAYMENT-RESPONSE and pull out the settlement hash if there is one."""
    raw = None
    for k, v in (headers or {}).items():
        if k.lower() == "x-payment-response":
            raw = v
            break
    if not raw:
        return {}, ""
    try:
        decoded = json.loads(base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", "replace"))
    except Exception:
        return {"raw": raw[:200]}, ""
    tx = ""
    for key in ("transaction", "txHash", "tx_hash", "signature", "txid", "txId"):
        if isinstance(decoded.get(key), str) and len(decoded[key]) > 10:
            tx = decoded[key]
            break
    if not tx:
        for k, v in decoded.items():
            if isinstance(v, str) and v.startswith("0x") and len(v) == 66:
                tx = v
                break
    return decoded, tx


def pay(url, body=None, method=None, key=None, max_usd=0.01, network="base", headers=None, timeout=45):
    """Pay ``url`` if it answers 402. Returns a dict, never raises for HTTP status."""
    if not url.startswith(("https://", "http://")):
        raise X402Error("url must be absolute http(s)")
    method = (method or ("POST" if body is not None else "GET")).upper()
    status, hdrs, raw = _http(url, method, body, headers, timeout)

    if status != 402:
        return {
            "paid": False, "status": status, "method": method,
            "price_usd": 0.0, "body": raw.decode("utf-8", "replace"),
            "note": "endpoint did not ask for payment",
        }

    try:
        req = json.loads(raw.decode("utf-8", "replace"))
    except Exception as exc:
        raise X402Error(f"402 response was not JSON: {exc}") from exc

    accept, reason = pick_accept(req.get("accepts"), network)
    if not accept:
        return {"paid": False, "status": 402, "method": method, "price_usd": 0.0,
                "error": reason, "accepts": req.get("accepts")}

    value = int(accept.get("maxAmountRequired") or 0)
    price = value / 1_000_000
    if value <= 0:
        return {"paid": False, "status": 402, "method": method, "price_usd": 0.0,
                "error": "server advertised a non-positive price"}
    if price > float(max_usd) + 1e-12:
        return {"paid": False, "status": 402, "method": method, "price_usd": price,
                "error": f"price {price:.6f} USD exceeds per-call cap {float(max_usd):.6f}",
                "pay_to": accept.get("payTo"), "network": accept.get("network")}

    payload, payer = build_payment(key, accept, value)
    hdr = base64.b64encode(json.dumps(payload).encode()).decode()
    status2, hdrs2, raw2 = _http(url, method, body, {**(headers or {}), "X-PAYMENT": hdr}, timeout)
    receipt, tx = decode_receipt(hdrs2)
    settled = status2 < 400 and bool(receipt)
    return {
        "paid": settled, "status": status2, "method": method, "price_usd": price,
        "payer": payer, "pay_to": accept.get("payTo"), "network": accept.get("network"),
        "tx_hash": tx, "receipt": receipt,
        "warning": reason or None,
        "body": raw2.decode("utf-8", "replace"),
        **({} if settled else {"error": "no X-PAYMENT-RESPONSE: the response is not a paid result"}),
    }
