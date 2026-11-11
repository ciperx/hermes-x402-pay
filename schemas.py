"""Tool schemas (OpenAI-style) for hermes-x402-pay."""

X402_PAY = {
    "name": "x402_pay",
    "description": (
        "Pay an x402 (HTTP 402) API and return its payload. Sends the request; if the "
        "server answers 402, reads the payment requirements, checks them against the "
        "spend caps, signs an EIP-3009 authorization in USDC on Base, retries with the "
        "X-PAYMENT header, and returns the body plus the settlement receipt. Refuses "
        "before signing when the price exceeds max_usd, the rolling 24h budget, or when "
        "the plugin is still in dry_run. Use it only for endpoints that advertise x402."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute https URL of the x402 endpoint."},
            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                       "description": "HTTP method. Defaults to GET, or POST when body is given."},
            "body": {"type": "object", "description": "JSON request body (for POST/PUT/PATCH)."},
            "headers": {"type": "object", "description": "Extra request headers."},
            "max_usd": {"type": "number",
                        "description": "Per-call ceiling in USD. The call is refused above it. Defaults to the configured cap."},
            "network": {"type": "string", "enum": ["base"], "description": "EVM network to pay on. Only 'base' is supported today."},
            "dry_run": {"type": "boolean",
                        "description": "When true, report what would be paid without signing anything."},
        },
        "required": ["url"],
    },
}

X402_RECEIPTS = {
    "name": "x402_receipts",
    "description": (
        "Read the local x402 payment ledger: every paid and refused call with price, "
        "status, payer, payee and transaction hash. Use it to answer 'what did you spend' "
        "and to verify a payment after the fact."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "How many recent rows (default 10, max 200)."},
            "since_hours": {"type": "integer", "description": "Only rows newer than this many hours."},
            "paid_only": {"type": "boolean", "description": "Only rows that were actually settled."},
        },
    },
}
