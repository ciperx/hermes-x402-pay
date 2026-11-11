"""Tests that prove the payment path without moving money.

Run with:  python3 -m unittest discover -s tests -v
Needs eth-account; no network calls leave the machine (local HTTP server only).
"""
import base64
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import x402payer  # noqa: E402
from ledger import Ledger  # noqa: E402

TEST_KEY = "0x" + "11" * 32
PAY_TO = "0xC22f661733B85C1349F7C83EE0B2BCF3234603dE"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


class Echo(BaseHTTPRequestHandler):
    price = "3000"
    seen = []

    def log_message(self, *a):
        pass

    def _accepts(self):
        return [{
            "scheme": "exact", "network": "base", "maxAmountRequired": self.price,
            "resource": f"http://127.0.0.1:{self.server.server_port}/paid",
            "description": "test", "mimeType": "application/json",
            "payTo": PAY_TO, "maxTimeoutSeconds": 30, "asset": USDC,
            "extra": {"name": "USD Coin", "version": "2"},
        }]

    def do_GET(self):
        pay = self.headers.get("X-PAYMENT")
        self.seen.append({"has_payment": bool(pay), "path": self.path})
        if not pay:
            body = json.dumps({"x402Version": 1, "accepts": self._accepts()}).encode()
            self.send_response(402)
        else:
            self.payload = json.loads(base64.b64decode(pay + "=" * (-len(pay) % 4)))
            self.send_response(200)
            receipt = base64.b64encode(json.dumps(
                {"success": True, "transaction": "0x" + "ab" * 32}).encode()).decode()
            self.send_header("X-PAYMENT-RESPONSE", receipt)
            body = b'{"ok":true}'
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class PayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Echo)
        cls.port = cls.srv.server_port
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        Echo.seen = []
        Echo.price = "3000"
        self.url = f"http://127.0.0.1:{self.port}/paid"

    def test_signature_has_0x_prefix(self):
        accept, _ = x402payer.pick_accept(
            [{"scheme": "exact", "network": "base", "payTo": PAY_TO, "maxAmountRequired": "3000",
              "asset": USDC, "extra": {"name": "USD Coin", "version": "2"}}])
        payload, payer = x402payer.build_payment(TEST_KEY, accept, 3000)
        sig = payload["payload"]["signature"]
        self.assertTrue(sig.startswith("0x"), "verifier rejects unprefixed signatures")
        self.assertEqual(len(sig), 132)
        auth = payload["payload"]["authorization"]
        now = int(time.time())
        self.assertLess(int(auth["validAfter"]), now)
        self.assertGreater(int(auth["validBefore"]), now)

    def test_pays_and_reads_receipt(self):
        out = x402payer.pay(self.url, key=TEST_KEY, max_usd=0.01)
        self.assertTrue(out["paid"], out)
        self.assertEqual(out["status"], 200)
        self.assertAlmostEqual(out["price_usd"], 0.003)
        self.assertTrue(out["tx_hash"].startswith("0x"), out["tx_hash"])
        self.assertEqual(len(Echo.seen), 2)
        self.assertFalse(Echo.seen[0]["has_payment"])
        self.assertTrue(Echo.seen[1]["has_payment"])

    def test_refuses_over_cap_without_signing(self):
        Echo.price = "20000"  # $0.02
        out = x402payer.pay(self.url, key=TEST_KEY, max_usd=0.01)
        self.assertFalse(out["paid"])
        self.assertIn("exceeds", out["error"])
        self.assertEqual(len(Echo.seen), 1, "must not send a payment attempt after refusing")

    def test_refuses_solana_only_endpoint(self):
        accepts = [{"scheme": "exact", "network": "solana", "maxAmountRequired": "3000"}]
        accept, reason = x402payer.pick_accept(accepts, "base")
        self.assertIsNone(accept)
        self.assertIn("EVM", reason)


class FailingTools:
    pass


class Ctx:
    """Minimal stand-in for the plugin ctx surface."""

    def __init__(self, **cfg):
        self.cfg = cfg

    def get_config(self, key, default=None):
        return self.cfg.get(key, default)


class ToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Echo)
        cls.port = cls.srv.server_port
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        Echo.seen = []
        Echo.price = "3000"
        self.url = f"http://127.0.0.1:{self.port}/paid"
        self.tmp = tempfile.mkdtemp()
        self.ledger = Ledger(os.path.join(self.tmp, "r.db"))
        os.environ["X402_PRIVATE_KEY"] = TEST_KEY
        import tools
        self.tools = tools

    def test_dry_run_is_default_and_spends_nothing(self):
        ctx = Ctx()
        out = self.tools.x402_pay(ctx, self.ledger, self.url)
        self.assertTrue(out.get("dry_run"))
        self.assertFalse(out["paid"])
        self.assertEqual(len(Echo.seen), 1)

    def test_budget_cap_blocks_second_payment(self):
        ctx = Ctx(dry_run=False, daily_budget_usd=0.004, per_call_cap_usd=0.01)
        self.ledger.record(url="x", paid=True, price_usd=0.003)
        out = self.tools.x402_pay(ctx, self.ledger, self.url)
        self.assertFalse(out["paid"])
        self.assertIn("budget", out["error"])
        self.assertEqual(len(Echo.seen), 1)

    def test_paid_call_lands_in_ledger(self):
        ctx = Ctx(dry_run=False, daily_budget_usd=1.0, per_call_cap_usd=0.01)
        out = self.tools.x402_pay(ctx, self.ledger, self.url)
        self.assertTrue(out["paid"], out)
        rows = self.ledger.recent(limit=5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tx_hash"], out["tx_hash"])
        self.assertAlmostEqual(self.ledger.spend_last_24h(), 0.003)

    def test_missing_key_is_reported_not_crashed(self):
        os.environ.pop("X402_PRIVATE_KEY", None)
        ctx = Ctx(dry_run=False)
        out = self.tools.x402_pay(ctx, self.ledger, self.url)
        self.assertFalse(out["paid"])
        self.assertIn("X402_PRIVATE_KEY", out["error"])


if __name__ == "__main__":
    unittest.main()
