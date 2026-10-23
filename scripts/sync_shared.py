"""Copy shared/trader_markets.py into every skill folder that imports it.

    python3 scripts/sync_shared.py           # write the copies
    python3 scripts/sync_shared.py --check   # exit 1 if any copy differs (CI, tests)

Skills are installed folder by folder, so each one carries its own copy.
Edit only shared/trader_markets.py, then run this script.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "shared", "trader_markets.py")
TARGETS = ["trader-scanner", "trader-news", "trader-entry", "trader-exit", "trader-timemachine",
           "trader-risk", "trader-budget", "trader-broker", "trader-alerts"]


def stale():
    with open(SOURCE, "rb") as fh:
        src = fh.read()
    out = []
    for t in TARGETS:
        path = os.path.join(ROOT, t, "trader_markets.py")
        try:
            with open(path, "rb") as fh:
                if fh.read() == src:
                    continue
        except OSError:
            pass
        out.append(path)
    return src, out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    src, paths = stale()
    if "--check" in argv:
        for p in paths:
            print("out of date: %s" % os.path.relpath(p, ROOT))
        return 1 if paths else 0
    for p in paths:
        with open(p, "wb") as fh:
            fh.write(src)
        print("wrote %s" % os.path.relpath(p, ROOT))
    if not paths:
        print("all %d copies are current" % len(TARGETS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
