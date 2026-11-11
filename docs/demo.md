# Demo: a real paid x402 call from inside Hermes

Everything below is verbatim from a live run on 2026-11-11. One call, $0.003, settled on
Base. This is the reproduction script, not a mock: swap in any x402 endpoint.

## 1. The endpoint answers 402

```
GET https://hermes.swap.io/v1/swap/status?chainKey=base&id=0x11..11
402 Payment Required
{"x402Version":1,"accepts":[
  {"scheme":"exact","network":"solana","maxAmountRequired":"3000", ...},
  {"scheme":"exact","network":"base","maxAmountRequired":"3000",
   "payTo":"0xC22f661733B85C1349F7C83EE0B2BCF3234603dE",
   "asset":"0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
   "extra":{"name":"USD Coin","version":"2"}}]}
```

## 2. Hermes checks the price against its caps

`per_call_cap_usd = 0.01`, `daily_budget_usd = 0.05`, `dry_run = false`.
$0.003 fits both, so the plugin signs and retries.

## 3. The plugin calls the tool and pays

```
$ X402_PRIVATE_KEY=0x...  python3 - <<'PY'
import tools
from ledger import Ledger
class Ctx:
    def get_config(self, k, default=None):
        return {"dry_run": False, "daily_budget_usd": 0.05, "per_call_cap_usd": 0.01}.get(k, default)
out = tools.x402_pay(Ctx(), Ledger("receipts.db"),
                     "https://hermes.swap.io/v1/swap/status?chainKey=base&id=0x11..11")
PY

{"paid": true, "status": 200, "method": "GET", "price_usd": 0.003,
 "payer": "0x27EbD1DDCB6607bE8641784A1bf654623Fc8290e",
 "pay_to": "0xC22f661733B85C1349F7C83EE0B2BCF3234603dE",
 "network": "base",
 "tx_hash": "0xf28c144c3b093f9ca23367fd2adbc079108fc20fe351a7a326b4922ffb1b8930",
 "receipt": {"success": true,
             "transaction": "0xf28c144c3b093f9ca23367fd2adbc079108fc20fe351a7a326b4922ffb1b8930",
             "network": "base",
             "payer": "0x27ebd1ddcb6607be8641784a1bf654623fc8290e"}}
```

## 4. The settlement is on-chain (checked with an independent RPC)

```
POST https://base-rpc.publicnode.com  eth_getTransactionByHash
0xf28c144c3b093f9ca23367fd2adbc079108fc20fe351a7a326b4922ffb1b8930
→ found in block 55875318
```

## 5. The ledger row (SQL, not memory)

```
$ sqlite3 receipts.db "select ts,url,status,paid,price_usd,tx_hash from payments"
1778... https://hermes.swap.io/v1/swap/status?... 200 1 0.003 0xf28c144c...
```

## What a refused call looks like

The price is read from the 402 response and the call is refused *before any signature is
produced*, so nothing is sent and nothing is spent:

```
{"paid": false, "status": 402, "price_usd": 0.02,
 "error": "price $0.020000 exceeds per-call cap $0.010000"}
```

`tests/test_x402payer.py::test_refuses_over_cap_without_signing` asserts the server saw
exactly one request in that case.

## Reproduce it yourself

```bash
export X402_PRIVATE_KEY=0x...          # a dedicated wallet with a little USDC on Base
hermes plugins config hermes-x402-pay dry_run false
hermes plugins config hermes-x402-pay daily_budget_usd 0.05
# then ask Hermes: "pay https://hermes.swap.io/v1/swap/status?chainKey=base&id=0x11..11 and show me the body"
```
