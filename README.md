# hermes-x402-pay

Give your Hermes agent a wallet it cannot drain.

x402 is the HTTP 402 payment handshake: the server answers `402 Payment Required` with
its price, the client pays, the server returns the payload. This plugin implements the
client half for **USDC on Base** (EIP-3009 `TransferWithAuthorization`, gasless for the
payer), with two hard caps and a receipt ledger.

```
you: pay https://api.example/x402/thing and give me the JSON
hermes: x402_pay -> 402 at $0.0044 -> within cap -> signed -> 200
        body: {...}
        receipt: tx 0xab..cd, payer 0x27Eb..90e, ledger row #12
```

## Why this exists

Hermes plugins can already *call* paid APIs; the missing piece was *paying* them without
handing a plugin a blank cheque. Every design decision here is about not losing money:

| Guard | Default | What it stops |
|---|---|---|
| `dry_run` | **on** | Any payment at all, until you flip it |
| `per_call_cap_usd` | `0.01` | A single endpoint asking $50 |
| `daily_budget_usd` | `0.05` | A loop that pays 1,000 times |
| key from env var name | `X402_PRIVATE_KEY` | Keys in config files or the ledger |
| ledger row per attempt | always | "what did it spend?" answered by SQL, not memory |

The budget is a `SUM()` over the last 24 hours of the ledger, so restarting Hermes does
not reset it. The plugin reads the 402 response *first* and refuses **before signing**:
a refused call never produces a signature and never touches the chain.

The key is read through Hermes's credential route (`agent.secret_scope.get_secret`), not
straight from the process environment, so a gateway serving several profiles resolves the
*active profile's* value instead of another profile's. The key is never logged, returned,
or written to the ledger; only its address appears in a receipt row.

## Install

```bash
hermes plugins install hermes-x402-pay    # once listed in the catalog
export X402_PRIVATE_KEY=0x...             # a dedicated wallet, not your main one
hermes plugins config hermes-x402-pay dry_run false
hermes plugins config hermes-x402-pay per_call_cap_usd 0.01
hermes plugins config hermes-x402-pay daily_budget_usd 0.05
```

Use a wallet that holds only what you are willing to spend. The plugin never needs ETH:
the facilitator pays gas, so the wallet needs USDC on Base and nothing else.

## Tools

- `x402_pay(url, method?, body?, headers?, max_usd?, dry_run?)` — pay a 402 endpoint.
  Returns `{paid, status, price_usd, payer, pay_to, tx_hash, receipt, body}`.
- `x402_receipts(limit?, since_hours?, paid_only?)` — read the ledger.
- `/x402` — spend in the last 24h, budget left, last five rows.

## The three wire-format details that cost real money to learn

These are documented because every one of them produces the same unhelpful server-side
message (`payment verification unavailable`) instead of an explanation:

1. **`0x` prefix on the signature.** `eth_account` 0.14 returns `HexBytes.hex()` without
   it; x402 verifiers parse 65-byte hex and fail closed.
2. **`validAfter` is `now - 30`, not `0`.** Zero is not treated as "valid immediately"
   by every facilitator.
3. **`validBefore` ≈ 11x the endpoint's `maxTimeoutSeconds`.** Settlement happens after
   the response body is produced; a 60s window expires mid-settlement.

The full payload was validated by running the reference JavaScript client against a local
echo server and diffing the `X-PAYMENT` header it produced against ours. If a payment
fails and the server says only "verification unavailable", do that diff before changing
anything else.

## Scope and limits

- **Base only.** Solana x402 sends a partially signed transaction, a different payload
  shape; this plugin refuses solana-only endpoints explicitly rather than mispaying.
- **No refunds, no escrow.** x402 pays for a response, not for an outcome. A 200 without
  `X-PAYMENT-RESPONSE` is reported as *unpaid* on purpose: no receipt means no proof.
- **The ledger is local and unencrypted.** It records URL, price, payer, payee and tx
  hash. It never records the private key or request bodies.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The suite stands up a local 402 server and asserts, among other things, that an
over-cap request is refused **without** a signature being sent (`Echo.seen == 1`).

## License

MIT
