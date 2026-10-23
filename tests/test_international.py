"""The pack outside the US: Xetra, Tradegate and the LSE, end to end on synthetic bars.

The bars are made up (a quiet range, then a breakout), the clock and calendar are real.
"""
import datetime
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load

alerts = load("trader-alerts", "alerts")
entry = load("trader-entry", "entry")
exitp = load("trader-exit", "exit")
risk = load("trader-risk", "check")
ticket = load("trader-broker", "ticket")
size = load("trader-budget", "size")
scan = load("trader-scanner", "scan")

DT = datetime.datetime
MODULES = (alerts, entry, exitp, risk, ticket, size, scan)


def run(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main(argv)
    return code, out.getvalue(), err.getvalue()


def chart_json(ysym, day, zone_offset_h, open_hm=(9, 0), bars=40, base=180.0, breakout_at=6, scale=1.0):
    """A Yahoo v8 chart: 5-minute bars from the open. Range bars 0..2 sit between base-1 and base+1;
    bar `breakout_at` closes 1.5 above the range high. Prices times `scale` (100 = pence)."""
    open_utc = DT(day.year, day.month, day.day, *open_hm) - datetime.timedelta(hours=zone_offset_h)
    t0 = int((open_utc - DT(1970, 1, 1)).total_seconds())
    ts, o, h, l, c, v = [], [], [], [], [], []
    for i in range(bars):
        px = base + (0.2 if i % 2 else -0.2)
        hi, lo = px + 0.8, px - 0.8
        if i >= breakout_at:
            px = base + 2.5 + 0.1 * (i - breakout_at)
            hi, lo = px + 0.3, px - 0.3
        ts.append(t0 + 300 * i)
        o.append(px * scale)
        h.append(hi * scale)
        l.append(lo * scale)
        c.append(px * scale)
        v.append(10000 + i)
    meta = {"symbol": ysym, "dataGranularity": "5m", "regularMarketTime": ts[-1] + 300,
            "chartPreviousClose": base * scale, "previousClose": base * scale}
    return json.dumps({"chart": {"result": [{"meta": meta, "timestamp": ts,
                                             "indicators": {"quote": [{"open": o, "high": h, "low": l,
                                                                       "close": c, "volume": v}]}}],
                                 "error": None}})


class Base(unittest.TestCase):
    def tearDown(self):
        for m in MODULES:
            m.set_market("XNYS")


class Clock(Base):
    def test_xetra_clock(self):
        code, out, _ = run(alerts, ["clock", "--market", "XETR", "--now", "2026-10-23 10:00", "--json"])
        st = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual((st["market"], st["opens"], st["closes"], st["tz"]), ("XETR", "09:00", "17:30", "CEST"))
        self.assertTrue(st["calendar_known"])
        self.assertEqual(st["now_local"], "2026-10-23 10:00")

    def test_xetra_holiday_and_next_open(self):
        code, out, _ = run(alerts, ["clock", "--market", "xetra", "--now", "2026-12-24 10:00"])
        self.assertEqual(code, 1)
        self.assertIn("Xetra holiday: Christmas Eve", out)
        self.assertIn("Next open: 2026-12-28 09:00 Frankfurt.", out)

    def test_config_picks_the_market(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = os.path.join(tmp, "config.json")
            with open(cfg, "w") as fh:
                json.dump({"market": "XLON"}, fh)
            code, out, _ = run(alerts, ["add", "--config", cfg, "--rules", os.path.join(tmp, "a.json"),
                                        "--state", os.path.join(tmp, "s.json"), "--opening-bell"])
            self.assertEqual(code, 0)
            self.assertEqual(alerts.MARKET.code, "XLON")

    def test_unknown_market(self):
        code, _, err = run(alerts, ["clock", "--market", "MARS"])
        self.assertEqual(code, 1)
        self.assertIn("unknown market", err)


class Entry(Base):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.day = datetime.date(2026, 10, 23)          # a Friday, CEST (UTC+2)
        self.file = os.path.join(self.tmp.name, "SAP.json")
        with open(self.file, "w") as fh:
            fh.write(chart_json("SAP.DE", self.day, 2))

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def test_orb_triggers_on_xetra_time(self):
        code, out, _ = run(entry, ["check", "SAP", "--market", "XETR", "--offline", self.file,
                                   "--now", "2026-10-23 10:00", "--config", "/nonexistent.json"])
        self.assertEqual(code, 0, out)
        self.assertIn("SAP  Fri 2026-10-23 10:00 CEST", out)
        self.assertIn("Opening range 9:00-9:15", out)
        self.assertIn("TRIGGERED 9:35 AM at EUR 182.50", out)
        self.assertNotIn("$", out)

    def test_before_the_xetra_open(self):
        code, out, _ = run(entry, ["check", "SAP", "--market", "XETR", "--offline", self.file,
                                   "--now", "2026-10-23 08:55", "--json", "--config", "/nonexistent.json"])
        res = json.loads(out)
        self.assertEqual((res["phase"], res["market"]), ("pre-open", "XETR"))
        self.assertIn("the Xetra opens at 9:00", res["reason"])
        self.assertEqual(res["no_entries_before"], "09:15")
        self.assertEqual(res["no_entries_after"], "17:00")

    def test_tradegate_reads_xetra_bars(self):
        code, out, err = run(entry, ["check", "SAP", "--market", "XGAT", "--offline", self.file,
                                     "--now", "2026-10-23 10:00", "--config", "/nonexistent.json"])
        self.assertEqual(code, 0, out + err)
        self.assertIn("Yahoo has no Tradegate prices", err)

    def test_lse_pence_become_pounds(self):
        f = os.path.join(self.tmp.name, "VOD.json")
        with open(f, "w") as fh:
            fh.write(chart_json("VOD.L", self.day, 1, open_hm=(8, 0), base=72.0, scale=100))
        code, out, _ = run(entry, ["check", "VOD", "--market", "XLON", "--offline", f,
                                   "--now", "2026-10-23 09:00", "--config", "/nonexistent.json"])
        self.assertEqual(code, 0, out)
        self.assertIn("09:00 BST", out)
        self.assertIn("at GBP 74.50", out)


class Exit(Base):
    def test_flat_by_follows_the_close(self):
        code, out, _ = run(exitp, ["plan", "--market", "XETR", "--symbol", "SAP", "--entry", "182.50",
                                   "--or-high", "181.20", "--or-low", "179.00", "--date", "2026-10-23",
                                   "--time", "9:35 AM", "--now", "2026-10-23 10:00", "--config",
                                   "/nonexistent.json", "--json"])
        plan = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual((plan["flat_by"], plan["market"], plan["currency"]), ("17:25", "XETR", "EUR"))
        self.assertEqual(plan["stop"], 180.1)
        code, out, _ = run(exitp, ["plan", "--market", "XETR", "--symbol", "SAP", "--entry", "182.50",
                                   "--or-high", "181.20", "--or-low", "179.00", "--date", "2026-10-23",
                                   "--now", "2026-10-23 10:00", "--config", "/nonexistent.json"])
        self.assertIn("Entry         EUR 182.50", out)
        self.assertIn("Flat by       17:25 CEST", out)

    def test_lse_half_day(self):
        code, out, _ = run(exitp, ["plan", "--market", "XLON", "--symbol", "VOD", "--entry", "74.50",
                                   "--or-high", "73.00", "--or-low", "71.00", "--date", "2026-12-24",
                                   "--now", "2026-12-24 09:00", "--config", "/nonexistent.json", "--json"])
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(out)["flat_by"], "12:25")


class Risk(Base):
    ORDER = {"symbol": "SAP", "side": "buy", "qty": "5", "type": "market", "time_in_force": "day",
             "order_class": "bracket", "take_profit_limit_price": "187.30", "stop_loss_stop_price": "180.10",
             "plan": {"entry": 182.5, "stop": 180.1, "target": 187.3}}

    def test_window_on_xetra(self):
        risk.set_market("XETR")
        cfg = {"budget": 5000}
        r = risk.check([dict(self.ORDER)], cfg, now=risk.local_to_utc(DT(2026, 10, 23, 9, 10)))
        reasons = r["results"][0]["reasons"]
        self.assertIn("window: no new entries before 09:15 Frankfurt (it is 09:10)", reasons)
        r = risk.check([dict(self.ORDER)], cfg, now=risk.local_to_utc(DT(2026, 10, 23, 10, 0)))
        self.assertEqual(r["results"][0]["verdict"], "PASS", r["results"][0]["reasons"])
        r = risk.check([dict(self.ORDER)], cfg, now=risk.local_to_utc(DT(2026, 12, 31, 10, 0)))
        self.assertIn("Xetra holiday: New Year's Eve", r["results"][0]["reasons"][0])

    def test_pdt_is_off_outside_the_us(self):
        risk.set_market("XETR")
        r = risk.check([dict(self.ORDER)], {"budget": 5000, "pdt_guard": True},
                       now=risk.local_to_utc(DT(2026, 10, 23, 10, 0)))
        self.assertTrue(any("pdt_guard is a US rule" in w for w in r["warnings"]))

    def test_tickers_with_digits(self):
        risk.set_market("XETR")
        r = risk.check([dict(self.ORDER, symbol="VOW3")], {"budget": 5000},
                       now=risk.local_to_utc(DT(2026, 10, 23, 10, 0)))
        self.assertFalse(any(x.startswith("asset") for x in r["results"][0]["reasons"]))
        risk.set_market("XNYS")
        r = risk.check([dict(self.ORDER, symbol="VOW3")], {"budget": 5000},
                       now=risk.local_to_utc(DT(2026, 10, 19, 10, 15)))
        self.assertTrue(any(x.startswith("asset") for x in r["results"][0]["reasons"]))

    def test_tradegate_evening(self):
        risk.set_market("XGAT")
        r = risk.check([dict(self.ORDER)], {"budget": 5000}, now=risk.local_to_utc(DT(2026, 10, 23, 20, 0)))
        self.assertEqual(r["results"][0]["verdict"], "PASS", r["results"][0]["reasons"])


class Broker(Base):
    def test_alpaca_never_gets_a_xetra_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            orders = os.path.join(tmp, "orders.json")
            with open(orders, "w") as fh:
                json.dump({"market": "XETR", "orders": [dict(Risk.ORDER)]}, fh)
            code, out, _ = run(ticket, ["show", "--orders", orders, "--config", os.path.join(tmp, "c.json"),
                                        "--risk", os.path.join(tmp, "r.json"),
                                        "--state", os.path.join(tmp, "b.json"), "--now", "2026-10-23 10:00"])
            self.assertEqual(code, 1)
            self.assertIn("NOT READY: Alpaca trades US stocks only", out)
            code, _, err = run(ticket, ["approve", "--index", "1", "--reply", "yes", "--orders", orders,
                                        "--config", os.path.join(tmp, "c.json"),
                                        "--risk", os.path.join(tmp, "r.json"),
                                        "--state", os.path.join(tmp, "b.json"), "--now", "2026-10-23 10:00"])
            self.assertEqual(code, 1)


class Budget(Base):
    def test_orders_carry_the_market(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = os.path.join(tmp, "plan.json")
            with open(plan, "w") as fh:
                json.dump({"symbol": "SAP", "side": "buy", "date": "2026-10-23", "entry": 182.5, "stop": 180.1,
                           "target": 187.3, "setup": "orb", "flat_by": "17:25", "market": "XETR"}, fh)
            out_path = os.path.join(tmp, "orders.json")
            code, out, err = run(size, ["--plan", plan, "--config", os.path.join(tmp, "c.json"),
                                        "--portfolio", os.path.join(tmp, "p.json"), "--budget", "5000",
                                        "--now", "2026-10-23 10:00", "--write", out_path])
            self.assertEqual(code, 0, out + err)
            self.assertIn("Flat by 17:25 Frankfurt", out)
            self.assertIn("EUR", out)
            with open(out_path) as fh:
                doc = json.load(fh)
            self.assertEqual((doc["market"], doc["currency"]), ("XETR", "EUR"))


class Scanner(Base):
    def test_universe_is_us_only(self):
        code, _, err = run(scan, ["universe", "--market", "XETR", "--no-write"])
        self.assertEqual(code, 1)
        self.assertIn("pass --symbols", err)

    def test_orb_on_xetra(self):
        with tempfile.TemporaryDirectory() as tmp:
            day = datetime.date(2026, 10, 23)
            with open(os.path.join(tmp, "SAP.json"), "w") as fh:
                fh.write(chart_json("SAP.DE", day, 2))
            code, out, err = run(scan, ["orb", "--market", "XETR", "--offline", tmp, "--at", "10:00",
                                        "--min-price", "0", "--no-write", "--config", "/nonexistent.json"])
            self.assertEqual(code, 0, out + err)
            self.assertIn("15-minute range (9:00 to 9:15 AM)", out)
            self.assertIn("SAP", out)


if __name__ == "__main__":
    unittest.main()
