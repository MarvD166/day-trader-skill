"""Market profiles for the day-trader skills: clock, calendar, currency, symbols.

Standard library only, no time-zone files (zoneinfo needs tzdata on Windows).

SOURCE OF TRUTH: shared/trader_markets.py. Every skill folder that needs it
carries a byte-identical copy made by scripts/sync_shared.py, so a single skill
still works when it is installed on its own. Edit this file, then run the sync.

    import trader_markets as mk
    m = mk.get("XETR")                     # or "xetra", "DE", "NYSE", "US", ...
    m.to_local(utc)                        # naive UTC -> naive local wall clock
    m.to_utc(local)                        # and back
    m.session(date)                        # (open, close) or None
    m.trading_day(date)                    # (True, "") or (False, "Xetra holiday: Good Friday")
    m.yahoo("SAP")                         # "SAP.DE"
    m.money(1234.5)                        # "EUR 1,234.50"

Calendars are rules (Easter, nth weekday, observed days), so they do not run
out at the end of a hard-coded list. Years in a market's "verified" tuple were
checked against the exchange's own published calendar. Other years are
computed by rule and can miss one-off closures (a day of mourning, a royal
event), so callers report them as "calendar_known": False.
"""

import datetime
import json
import os

VERSION = "1"

_D = datetime.date
_T = datetime.time
_TD = datetime.timedelta
_DT = datetime.datetime


class MarketError(Exception):
    pass


# ---------------------------------------------------------------- date rules

def easter(year):
    """Easter Sunday, Gregorian calendar (anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return _D(year, month, day + 1)


def nth_weekday(year, month, weekday, n):
    """n-th weekday (Mon=0) of a month. n=-1 is the last one."""
    if n > 0:
        d = _D(year, month, 1)
        d += _TD(days=(weekday - d.weekday()) % 7)
        return d + _TD(weeks=n - 1)
    nxt = _D(year + (month == 12), month % 12 + 1, 1)
    d = nxt - _TD(days=1)
    return d - _TD(days=(d.weekday() - weekday) % 7)


def _weekday(d):
    return d.weekday() < 5


# ---------------------------------------------------------------- time zones

def _us_offset(utc, std):
    """US rule since 2007: 2:00 a.m. local, second Sunday of March to first Sunday of November."""
    y = utc.year
    start = _DT.combine(nth_weekday(y, 3, 6, 2), _T(2)) - _TD(hours=std)        # 2:00 standard time
    end = _DT.combine(nth_weekday(y, 11, 6, 1), _T(2)) - _TD(hours=std + 1)     # 2:00 daylight time
    return std + 1 if start <= utc < end else std


def _eu_offset(utc, std):
    """EU and UK rule: 01:00 UTC, last Sunday of March to last Sunday of October."""
    y = utc.year
    start = _DT.combine(nth_weekday(y, 3, 6, -1), _T(1))
    end = _DT.combine(nth_weekday(y, 10, 6, -1), _T(1))
    return std + 1 if start <= utc < end else std


# zone -> (rule, standard offset, standard label, daylight label)
ZONES = {
    "America/New_York": (_us_offset, -5, "ET", "ET"),
    "Europe/London": (_eu_offset, 0, "GMT", "BST"),
    "Europe/Berlin": (_eu_offset, 1, "CET", "CEST"),
    "Europe/Paris": (_eu_offset, 1, "CET", "CEST"),
    "Europe/Amsterdam": (_eu_offset, 1, "CET", "CEST"),
    "Europe/Zurich": (_eu_offset, 1, "CET", "CEST"),
}


def _naive_utc(utc):
    if utc.tzinfo is not None:
        utc = (utc - utc.utcoffset()).replace(tzinfo=None)
    return utc


# ---------------------------------------------------------------- calendars
# Each calendar: year -> (holidays {iso: name}, early closes {iso: (close time, name)}).

def _cal_nyse(y):
    hol, early = {}, {}

    def add(d, name, shift=True):
        if shift and d.weekday() == 5:
            d, name = d - _TD(days=1), name + " (observed)"
        elif shift and d.weekday() == 6:
            d, name = d + _TD(days=1), name + " (observed)"
        if _weekday(d):
            hol[d.isoformat()] = name

    ny = _D(y, 1, 1)
    if ny.weekday() == 6:                      # Sunday -> Monday. Saturday: not observed (NYSE rule)
        add(ny, "New Year's Day")
    elif ny.weekday() < 5:
        hol[ny.isoformat()] = "New Year's Day"
    add(nth_weekday(y, 1, 0, 3), "Martin Luther King, Jr. Day", False)
    add(nth_weekday(y, 2, 0, 3), "Washington's Birthday", False)
    add(easter(y) - _TD(days=2), "Good Friday", False)
    add(nth_weekday(y, 5, 0, -1), "Memorial Day", False)
    if y >= 2022:
        add(_D(y, 6, 19), "Juneteenth National Independence Day")
    add(_D(y, 7, 4), "Independence Day")
    add(nth_weekday(y, 9, 0, 1), "Labor Day", False)
    thanks = nth_weekday(y, 11, 3, 4)
    add(thanks, "Thanksgiving Day", False)
    add(_D(y, 12, 25), "Christmas Day")

    one = _T(13, 0)
    j3 = _D(y, 7, 3)
    if j3.weekday() < 4 and j3.isoformat() not in hol:   # Mon-Thu, so July 4 is a weekday
        early[j3.isoformat()] = (one, "day before Independence Day")
    early[(thanks + _TD(days=1)).isoformat()] = (one, "day after Thanksgiving")
    c24 = _D(y, 12, 24)
    if _weekday(c24) and c24.isoformat() not in hol:
        early[c24.isoformat()] = (one, "Christmas Eve")
    return hol, early


def _german_core(y):
    """Deutsche Boerse non-trading days (Xetra, Frankfurt), also Tradegate's."""
    e = easter(y)
    days = [(_D(y, 1, 1), "New Year's Day"), (e - _TD(days=2), "Good Friday"),
            (e + _TD(days=1), "Easter Monday"), (_D(y, 5, 1), "Labour Day"),
            (_D(y, 12, 24), "Christmas Eve"), (_D(y, 12, 25), "Christmas Day"),
            (_D(y, 12, 26), "St. Stephen's Day"), (_D(y, 12, 31), "New Year's Eve")]
    return {d.isoformat(): n for d, n in days if _weekday(d)}


def _cal_xetra(y):
    return _german_core(y), {}


def _cal_tradegate(y):
    hol = _german_core(y)
    early = {}
    e = easter(y)
    for d, name in ((e + _TD(days=39), "Ascension Day"), (e + _TD(days=50), "Whit Monday")):
        if _weekday(d) and d.isoformat() not in hol:
            early[d.isoformat()] = (_T(20, 0), name)
    d = _D(y, 12, 30)                          # last exchange day of the year
    while not _weekday(d) or d.isoformat() in hol:
        d -= _TD(days=1)
    early[d.isoformat()] = (_T(14, 0), "last exchange day of the year")
    return hol, early


def _cal_lse(y):
    hol, early = {}, {}
    e = easter(y)
    ny = _D(y, 1, 1)
    while not _weekday(ny):
        ny += _TD(days=1)
    hol[ny.isoformat()] = "New Year's Day" + ("" if ny.day == 1 else " (substitute)")
    hol[(e - _TD(days=2)).isoformat()] = "Good Friday"
    hol[(e + _TD(days=1)).isoformat()] = "Easter Monday"
    hol[nth_weekday(y, 5, 0, 1).isoformat()] = "Early May Bank Holiday"
    hol[nth_weekday(y, 5, 0, -1).isoformat()] = "Spring Bank Holiday"
    hol[nth_weekday(y, 8, 0, -1).isoformat()] = "Summer Bank Holiday"
    taken = set()
    for day, name in ((25, "Christmas Day"), (26, "Boxing Day")):
        d = _D(y, 12, day)
        sub = not _weekday(d)
        while not _weekday(d) or d in taken:
            d += _TD(days=1)
        taken.add(d)
        hol[d.isoformat()] = name + (" (substitute)" if sub else "")
    half = _T(12, 30)
    for d, name in ((_D(y, 12, 24), "Christmas Eve"), (_D(y, 12, 31), "New Year's Eve")):
        if _weekday(d) and d.isoformat() not in hol:
            early[d.isoformat()] = (half, name)
    return hol, early


def _cal_euronext(y):
    e = easter(y)
    days = [(_D(y, 1, 1), "New Year's Day"), (e - _TD(days=2), "Good Friday"),
            (e + _TD(days=1), "Easter Monday"), (_D(y, 5, 1), "Labour Day"),
            (_D(y, 12, 25), "Christmas Day"), (_D(y, 12, 26), "Boxing Day")]
    hol = {d.isoformat(): n for d, n in days if _weekday(d)}
    early = {}
    for d, name in ((_D(y, 12, 24), "Christmas Eve"), (_D(y, 12, 31), "New Year's Eve")):
        if _weekday(d) and d.isoformat() not in hol:
            early[d.isoformat()] = (_T(14, 5), name)
    return hol, early


def _cal_six(y):
    e = easter(y)
    days = [(_D(y, 1, 1), "New Year's Day"), (_D(y, 1, 2), "Berchtold's Day"),
            (e - _TD(days=2), "Good Friday"), (e + _TD(days=1), "Easter Monday"),
            (_D(y, 5, 1), "Labour Day"), (_D(y, 8, 1), "Swiss National Day"),
            (_D(y, 12, 24), "Christmas Eve"), (_D(y, 12, 25), "Christmas Day"),
            (_D(y, 12, 26), "St. Stephen's Day"), (_D(y, 12, 31), "New Year's Eve")]
    return {d.isoformat(): n for d, n in days if _weekday(d)}, {}


# ---------------------------------------------------------------- the market

class Market(object):
    def __init__(self, code, name, short, city, zone, open_, close, currency, cur_symbol,
                 yahoo_suffix, calendar, verified=(), price_divisor=1, data_from=None,
                 extended=None, source="", notes=()):
        self.code, self.name, self.short, self.city, self.zone = code, name, short, city, zone
        self.open, self.close = _T(*open_), _T(*close)
        self.currency, self.cur_symbol = currency, cur_symbol
        self.yahoo_suffix, self._calendar = yahoo_suffix, calendar
        self.verified = tuple(verified)
        self.price_divisor = price_divisor     # Yahoo quotes LSE shares in pence: 100
        self.data_from = data_from             # market whose Yahoo bars stand in for this one
        self.extended = tuple(_T(*t) for t in extended) if extended else None
        self.source, self.notes = source, tuple(notes)
        self._cache = {}

    def __repr__(self):
        return "Market(%s)" % self.code

    # ---- clock
    def offset(self, utc):
        """Hours from UTC at this naive (or aware) UTC instant."""
        rule, std = ZONES[self.zone][:2]
        return rule(_naive_utc(utc), std)

    def tz_label(self, utc):
        _, std, lab_std, lab_dst = ZONES[self.zone]
        return lab_dst if self.offset(utc) != std else lab_std

    def label_on(self, d):
        """The zone's short name on a local date: ET, CET or CEST, GMT or BST."""
        return self.tz_label(self.to_utc(_DT.combine(d, _T(12))))

    def to_local(self, utc):
        utc = _naive_utc(utc)
        return utc + _TD(hours=self.offset(utc))

    def to_utc(self, local):
        """Naive local wall clock -> naive UTC. In the autumn overlap the daylight reading wins."""
        std = ZONES[self.zone][1]
        for off in (std + 1, std):
            utc = local - _TD(hours=off)
            if self.offset(utc) == off:
                return utc
        return local - _TD(hours=std)          # inside the spring gap

    def ts_to_local(self, ts):
        return self.to_local(_DT(1970, 1, 1) + _TD(seconds=int(ts)))

    def iso_local(self, utc):
        """naive UTC -> '2026-10-19T10:15:00-04:00' (local wall clock with its offset)."""
        utc = _naive_utc(utc)
        return "%s%+03d:00" % (self.to_local(utc).replace(microsecond=0).isoformat(), self.offset(utc))

    # ---- calendar
    def calendar(self, year):
        if year not in self._cache:
            self._cache[year] = self._calendar(year)
        return self._cache[year]

    def holidays(self, *years):
        out = {}
        for y in years:
            out.update(self.calendar(y)[0])
        return out

    def early_closes(self, *years):
        """{iso: name} for the given years (the close time is in session())."""
        out = {}
        for y in years:
            out.update((k, v[1]) for k, v in self.calendar(y)[1].items())
        return out

    def holiday(self, d):
        return self.calendar(d.year)[0].get(d.isoformat())

    def early_close(self, d):
        """(close time, name) when d closes early, else None."""
        return self.calendar(d.year)[1].get(d.isoformat())

    def calendar_known(self, d):
        return d.year in self.verified

    def trading_day(self, d):
        if d.weekday() >= 5:
            return False, "weekend"
        name = self.holiday(d)
        if name:
            return False, "%s holiday: %s" % (self.short, name)
        return True, ""

    def session(self, d):
        """(open, close) in local time, or None if closed all day."""
        if not self.trading_day(d)[0]:
            return None
        early = self.early_close(d)
        return self.open, (early[0] if early else self.close)

    def next_session(self, d, include_today=False):
        """First trading date on or after d (after d unless include_today)."""
        nd = d if include_today else d + _TD(days=1)
        for _ in range(30):
            if self.session(nd):
                return nd
            nd += _TD(days=1)
        return None

    def bars_market(self):
        """The market whose Yahoo bars and session grid stand in for this one (Tradegate -> Xetra)."""
        return get(self.data_from) if self.data_from else self

    def minutes_after_open(self, minutes):
        return (_DT.combine(_D(2000, 1, 3), self.open) + _TD(minutes=minutes)).time()

    def minutes_before_close(self, minutes, d=None):
        close = self.session(d)[1] if d is not None and self.session(d) else self.close
        return (_DT.combine(_D(2000, 1, 3), close) - _TD(minutes=minutes)).time()

    # ---- symbols and money
    def yahoo(self, sym):
        """Plain ticker -> Yahoo symbol. A symbol that already carries a suffix is left alone."""
        s = sym.strip().upper()
        if self.data_from:
            return get(self.data_from).yahoo(s)
        if not self.yahoo_suffix:
            return s.replace(".", "-")        # BRK.B -> BRK-B
        if "." in s:
            return s
        return s + self.yahoo_suffix

    def ticker(self, yahoo_sym):
        """Yahoo symbol -> the plain ticker used in the trader/ files."""
        s = yahoo_sym.strip().upper()
        suffix = (get(self.data_from) if self.data_from else self).yahoo_suffix
        if suffix and s.endswith(suffix):
            return s[:-len(suffix)]
        if not suffix:
            return s.replace("-", ".")
        return s

    def price(self, x):
        """A Yahoo price -> this market's currency unit (pence -> pounds on the LSE)."""
        if x is None or self.price_divisor == 1:
            return x
        return x / float(self.price_divisor)

    def money(self, x, signed=False):
        if x is None:
            return "n/a"
        neg = x < 0
        body = "%s%s" % (self.cur_symbol, "{:,.2f}".format(abs(x)))
        if neg:
            return "-" + body
        return ("+" + body) if signed else body

    def describe(self):
        return {"code": self.code, "name": self.name, "city": self.city, "zone": self.zone,
                "opens": self.open.strftime("%H:%M"), "closes": self.close.strftime("%H:%M"),
                "extended": ["%s" % t.strftime("%H:%M") for t in self.extended] if self.extended else None,
                "currency": self.currency, "yahoo_suffix": self.yahoo_suffix,
                "data_from": self.data_from, "verified_years": list(self.verified),
                "source": self.source, "notes": list(self.notes)}


MARKETS = {
    "XNYS": Market(
        "XNYS", "US stocks (NYSE and Nasdaq)", "NYSE", "New York", "America/New_York",
        (9, 30), (16, 0), "USD", "$", "", _cal_nyse, verified=(2026, 2027),
        source="https://www.nyse.com/markets/hours-calendars"),
    "XETR": Market(
        "XETR", "Xetra (Deutsche Boerse)", "Xetra", "Frankfurt", "Europe/Berlin",
        (9, 0), (17, 30), "EUR", "EUR ", ".DE", _cal_xetra, verified=(2026, 2027),
        extended=((8, 0), (22, 0)),
        source="https://live.deutsche-boerse.com/handelszeiten",
        notes=("Core session 09:00-17:30 with opening and closing auctions. Since Dec 2025 "
               "private investors can also trade 08:00-22:00, outside the core session's liquidity.",)),
    "XGAT": Market(
        "XGAT", "Tradegate", "Tradegate", "Berlin", "Europe/Berlin",
        (7, 30), (22, 0), "EUR", "EUR ", None, _cal_tradegate, verified=(2026,),
        data_from="XETR",
        source="https://www.tradegatebsx.com/docs/Tradegate_Handelskalender_2026.pdf",
        notes=("Yahoo has no Tradegate prices: bars come from Xetra (.DE) and only cover "
               "09:00-17:30. Outside that window the tools report no data.",)),
    "XLON": Market(
        "XLON", "London Stock Exchange", "LSE", "London", "Europe/London",
        (8, 0), (16, 30), "GBP", "GBP ", ".L", _cal_lse, verified=(2026, 2027),
        price_divisor=100,
        source="https://www.londonstockexchange.com/equities-trading/business-days",
        notes=("Yahoo quotes LSE shares in pence (GBp): prices are divided by 100 into pounds.",)),
    "XPAR": Market(
        "XPAR", "Euronext Paris", "Euronext", "Paris", "Europe/Paris",
        (9, 0), (17, 30), "EUR", "EUR ", ".PA", _cal_euronext,
        source="https://www.euronext.com/en/trade/trading-hours-holidays"),
    "XAMS": Market(
        "XAMS", "Euronext Amsterdam", "Euronext", "Amsterdam", "Europe/Amsterdam",
        (9, 0), (17, 30), "EUR", "EUR ", ".AS", _cal_euronext,
        source="https://www.euronext.com/en/trade/trading-hours-holidays"),
    "XSWX": Market(
        "XSWX", "SIX Swiss Exchange", "SIX", "Zurich", "Europe/Zurich",
        (9, 0), (17, 30), "CHF", "CHF ", ".SW", _cal_six,
        source="https://www.six-group.com/en/products-services/the-swiss-stock-exchange/trading/trading-provisions/exchange-holidays.html"),
}

ALIASES = {
    "US": "XNYS", "USA": "XNYS", "NYSE": "XNYS", "NASDAQ": "XNYS", "XNAS": "XNYS",
    "DE": "XETR", "XETRA": "XETR", "FRANKFURT": "XETR",
    "TRADEGATE": "XGAT", "TG": "XGAT",
    "UK": "XLON", "LSE": "XLON", "LONDON": "XLON",
    "FR": "XPAR", "PARIS": "XPAR", "NL": "XAMS", "AMSTERDAM": "XAMS",
    "CH": "XSWX", "SIX": "XSWX", "ZURICH": "XSWX",
}
DEFAULT = "XNYS"


def get(code=None):
    """Market by MIC code or alias. None -> the default (XNYS)."""
    key = (code or DEFAULT).strip().upper()
    key = ALIASES.get(key, key)
    if key not in MARKETS:
        raise MarketError("unknown market %r: use one of %s" % (code, ", ".join(sorted(MARKETS))))
    return MARKETS[key]


def resolve(cli=None, doc=None, config="trader/config.json"):
    """Which market a run uses: --market, else a file's "market" field (plan, orders),
    else trader/config.json "market", else XNYS."""
    if cli:
        return get(cli)
    if isinstance(doc, dict) and doc.get("market"):
        return get(doc["market"])
    try:
        with open(config, encoding="utf-8") as fh:
            cfg = json.load(fh)
        if isinstance(cfg, dict) and cfg.get("market"):
            return get(cfg["market"])
    except (OSError, ValueError):
        pass
    return get(DEFAULT)


def codes():
    return sorted(MARKETS)


if __name__ == "__main__":
    import sys
    m = get(sys.argv[1] if len(sys.argv) > 1 else None)
    year = int(sys.argv[2]) if len(sys.argv) > 2 else _DT.utcnow().year
    print(json.dumps(m.describe(), indent=2))
    hol, early = m.calendar(year)
    for k in sorted(hol):
        print("%s  closed  %s" % (k, hol[k]))
    for k in sorted(early):
        print("%s  closes %s  %s" % (k, early[k][0].strftime("%H:%M"), early[k][1]))
    if year not in m.verified:
        print("(%d computed by rule, not checked against %s)" % (year, m.source or "the exchange"))
