import datetime
import importlib.util
import json
import os
import tempfile
import unittest

from _load import ROOT

_spec = importlib.util.spec_from_file_location(
    "trader_markets_src", os.path.join(ROOT, "shared", "trader_markets.py"))
mk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mk)

_sync_spec = importlib.util.spec_from_file_location(
    "sync_shared", os.path.join(ROOT, "scripts", "sync_shared.py"))
sync = importlib.util.module_from_spec(_sync_spec)
_sync_spec.loader.exec_module(sync)

D = datetime.date
DT = datetime.datetime
T = datetime.time

# Published calendars, read 2026-10-23.
# NYSE: https://www.nyse.com/markets/hours-calendars
NYSE_2026 = ["2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19",
             "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25"]
NYSE_2027 = ["2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
             "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"]
NYSE_EARLY = ["2026-11-27", "2026-12-24", "2027-11-26"]
# Deutsche Boerse: Handelskalender 2026 PDF and live.deutsche-boerse.com/handelszeiten (2027)
XETRA_2026 = ["2026-01-01", "2026-04-03", "2026-04-06", "2026-05-01", "2026-12-24", "2026-12-25",
              "2026-12-31"]
XETRA_2027 = ["2027-01-01", "2027-03-26", "2027-03-29", "2027-12-24", "2027-12-31"]
# Tradegate: Tradegate_Handelskalender_2026.pdf
TRADEGATE_2026 = XETRA_2026
TRADEGATE_SHORT_2026 = {"2026-05-14": T(20, 0), "2026-05-25": T(20, 0), "2026-12-30": T(14, 0)}
# LSE: londonstockexchange.com business days (2026 via lynxbroker.de, 2027 via financecalendar.com)
LSE_2026 = ["2026-01-01", "2026-04-03", "2026-04-06", "2026-05-04", "2026-05-25", "2026-08-31",
            "2026-12-25", "2026-12-28"]
LSE_2027 = ["2027-01-01", "2027-03-26", "2027-03-29", "2027-05-03", "2027-05-31", "2027-08-30",
            "2027-12-27", "2027-12-28"]
LSE_EARLY = ["2026-12-24", "2026-12-31", "2027-12-24", "2027-12-31"]


class Calendars(unittest.TestCase):
    def test_nyse_matches_the_published_list(self):
        n = mk.get("XNYS")
        self.assertEqual(sorted(n.holidays(2026, 2027)), NYSE_2026 + NYSE_2027)
        self.assertEqual(sorted(n.early_closes(2026, 2027)), NYSE_EARLY)
        self.assertEqual(n.session(D(2026, 11, 27)), (T(9, 30), T(13, 0)))

    def test_nyse_rules_beyond_the_list(self):
        n = mk.get("XNYS")
        self.assertNotIn("2027-12-31", n.holidays(2027))   # Sat New Year 2028 is not observed
        self.assertIn("2028-07-03", n.early_closes(2028))
        self.assertIn("2023-01-02", n.holidays(2023))      # Sunday New Year -> Monday
        self.assertFalse(n.calendar_known(D(2028, 3, 1)))

    def test_xetra(self):
        x = mk.get("XETR")
        self.assertEqual(sorted(x.holidays(2026, 2027)), XETRA_2026 + XETRA_2027)
        self.assertEqual(x.session(D(2026, 10, 23)), (T(9, 0), T(17, 30)))
        self.assertEqual(x.trading_day(D(2026, 4, 6)), (False, "Xetra holiday: Easter Monday"))
        self.assertTrue(x.trading_day(D(2025, 10, 3))[0])     # German Unity Day: Xetra trades
        self.assertTrue(x.trading_day(D(2026, 5, 14))[0])     # Ascension Day: Xetra trades

    def test_tradegate(self):
        t = mk.get("XGAT")
        hol, early = t.calendar(2026)
        self.assertEqual(sorted(hol), TRADEGATE_2026)
        self.assertEqual({k: v[0] for k, v in early.items()}, TRADEGATE_SHORT_2026)
        self.assertEqual(t.session(D(2026, 10, 23)), (T(7, 30), T(22, 0)))
        self.assertEqual(t.yahoo("SAP"), "SAP.DE")

    def test_lse(self):
        l = mk.get("XLON")
        self.assertEqual(sorted(l.holidays(2026, 2027)), LSE_2026 + LSE_2027)
        self.assertEqual(sorted(l.early_closes(2026, 2027)), LSE_EARLY)
        self.assertEqual(l.session(D(2026, 12, 24)), (T(8, 0), T(12, 30)))

    def test_unverified_markets_say_so(self):
        for code in ("XPAR", "XAMS", "XSWX"):
            self.assertFalse(mk.get(code).calendar_known(D(2026, 6, 1)), code)

    def test_every_holiday_is_a_weekday(self):
        for code in mk.codes():
            m = mk.get(code)
            for y in range(2024, 2032):
                hol, early = m.calendar(y)
                for k in list(hol) + list(early):
                    self.assertLess(D.fromisoformat(k).weekday(), 5, (code, k))
                self.assertFalse(set(hol) & set(early), (code, y))

    def test_easter(self):
        self.assertEqual([mk.easter(y) for y in (2024, 2025, 2026, 2027, 2038)],
                         [D(2024, 3, 31), D(2025, 4, 20), D(2026, 4, 5), D(2027, 3, 28), D(2038, 4, 25)])


class Clock(unittest.TestCase):
    def test_new_york_dst(self):
        n = mk.get("XNYS")
        self.assertEqual(n.offset(DT(2026, 3, 8, 6, 59)), -5)
        self.assertEqual(n.offset(DT(2026, 3, 8, 7, 0)), -4)
        self.assertEqual(n.offset(DT(2026, 11, 1, 5, 59)), -4)
        self.assertEqual(n.offset(DT(2026, 11, 1, 6, 0)), -5)

    def test_europe_dst(self):
        x, l = mk.get("XETR"), mk.get("XLON")
        self.assertEqual(x.offset(DT(2026, 3, 29, 0, 59)), 1)
        self.assertEqual(x.offset(DT(2026, 3, 29, 1, 0)), 2)
        self.assertEqual(x.offset(DT(2026, 10, 25, 0, 59)), 2)
        self.assertEqual(x.offset(DT(2026, 10, 25, 1, 0)), 1)
        self.assertEqual(l.offset(DT(2026, 7, 1)), 1)
        self.assertEqual(l.offset(DT(2026, 12, 1)), 0)
        self.assertEqual(x.tz_label(DT(2026, 7, 1)), "CEST")
        self.assertEqual(x.tz_label(DT(2026, 12, 1)), "CET")

    def test_round_trip(self):
        x = mk.get("XETR")
        self.assertEqual(x.to_utc(DT(2026, 10, 23, 9, 0)), DT(2026, 10, 23, 7, 0))
        self.assertEqual(x.to_utc(DT(2026, 10, 26, 9, 0)), DT(2026, 10, 26, 8, 0))
        for code in mk.codes():
            m = mk.get(code)
            for h in range(0, 24 * 400, 7):
                utc = DT(2026, 1, 1) + datetime.timedelta(hours=h)
                local = m.to_local(utc)
                back = m.to_utc(local)
                # only the autumn overlap hour is ambiguous
                if back != utc:
                    self.assertEqual(utc - back, datetime.timedelta(hours=1), (code, utc))
        self.assertEqual(mk.get("XNYS").iso_local(DT(2026, 10, 19, 14, 15)), "2026-10-19T10:15:00-04:00")
        self.assertEqual(x.iso_local(DT(2026, 10, 19, 14, 15)), "2026-10-19T16:15:00+02:00")


class Symbols(unittest.TestCase):
    def test_yahoo(self):
        self.assertEqual(mk.get("XNYS").yahoo("brk.b"), "BRK-B")
        self.assertEqual(mk.get("XETR").yahoo("sap"), "SAP.DE")
        self.assertEqual(mk.get("XETR").yahoo("SAP.DE"), "SAP.DE")
        self.assertEqual(mk.get("XLON").yahoo("VOD"), "VOD.L")
        self.assertEqual(mk.get("XLON").ticker("VOD.L"), "VOD")
        self.assertEqual(mk.get("XNYS").ticker("BRK-B"), "BRK.B")

    def test_pence(self):
        self.assertEqual(mk.get("XLON").price(7250.0), 72.5)
        self.assertEqual(mk.get("XETR").price(185.4), 185.4)

    def test_money(self):
        self.assertEqual(mk.get("XNYS").money(2913.44), "$2,913.44")
        self.assertEqual(mk.get("XETR").money(-46.88), "-EUR 46.88")
        self.assertEqual(mk.get("XNYS").money(5, signed=True), "+$5.00")


class Resolve(unittest.TestCase):
    def test_aliases_and_errors(self):
        self.assertEqual(mk.get("xetra").code, "XETR")
        self.assertEqual(mk.get("DE").code, "XETR")
        self.assertEqual(mk.get(None).code, "XNYS")
        with self.assertRaises(mk.MarketError):
            mk.get("mars")

    def test_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = os.path.join(tmp, "config.json")
            with open(cfg, "w") as fh:
                json.dump({"market": "XLON"}, fh)
            self.assertEqual(mk.resolve(config=cfg).code, "XLON")
            self.assertEqual(mk.resolve(doc={"market": "XETR"}, config=cfg).code, "XETR")
            self.assertEqual(mk.resolve(cli="US", doc={"market": "XETR"}, config=cfg).code, "XNYS")
            self.assertEqual(mk.resolve(config=os.path.join(tmp, "none.json")).code, "XNYS")


class Copies(unittest.TestCase):
    def test_every_skill_copy_matches_the_source(self):
        _, stale = sync.stale()
        self.assertEqual(stale, [], "run: python3 scripts/sync_shared.py")


if __name__ == "__main__":
    unittest.main()
