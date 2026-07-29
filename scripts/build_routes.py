#!/usr/bin/env python3
"""
build_routes.py - AlbionSnipe per-city direct prices (the Routes tab price layer)

Emits docs/data/routes.json: for every id in the routesmeta.json universe, the
LIVE standing orders per (quality, city) from AODP - the one price layer no
other dataset carries (toptraded/materials only hold averages or the buy side
of a single tab's math):
  - p, t  : sell_price_min + its timestamp = what you PAY to buy it there now,
            and what you undercut to SELL it there now.
  - b, bt : buy_price_max + timestamp = instant-sell exit (fill a standing buy
            order), stored only when a buy order exists.

The Routes tab crosses these between cities: buy at A for p_A, haul, sell at B
against p_B (sell order), b_B (instant) or the 7/30d averages from
toptraded.json. Timestamps ride along so the app can show price age instead of
pretending AODP is live (it is as fresh as the last player upload, and that
honesty is the edge over tools that hide it).

Rebuild alongside baseline (2x/day). Same AODP etiquette as the sibling
builders: chunked requests, 2s pacing, per-call scoped backoff.

Output shape (cities indexed to keep the file small):
  { "cities": [...7 names...], "items": {
      "T4_ORE": {"1": {"0": [p, t], "5": [p, t, b, bt], ...}},
      ...                                  t/bt = epoch MINUTES (UTC)
  }}

Usage:
  python scripts/build_routes.py
  python scripts/build_routes.py --server west
"""
import argparse, calendar, json, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
META = ROOT / "docs" / "data" / "routesmeta.json"
OUT = ROOT / "docs" / "data" / "routes.json"
CITIES = ["Bridgewatch", "Fort Sterling", "Lymhurst", "Martlock", "Thetford", "Caerleon", "Brecilien"]
QUALITIES = "1,2,3,4,5"
CHUNK = 50
SLEEP = 2.0
UA = "albionsnipe-app/1.0 (routes dataset builder)"


def get_json(url, tries=10):
    # Exponential backoff SCOPED to this one call (resets for the next chunk) - same
    # convention as build_baseline/build_toptraded. Honors Retry-After when AODP sends
    # it, else backs off 3s/6s/12s/... capped at 20s.
    delay = 3
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < tries - 1:
                wait = int(e.headers.get("Retry-After") or 0) or delay
                time.sleep(wait)
                delay = min(delay * 2, 20)
            else:
                raise
        except Exception:
            if attempt < tries - 1:
                time.sleep(delay)
                delay = min(delay * 2, 20)
            else:
                raise


def prices_url(server, ids):
    q = urllib.parse.urlencode({"locations": ",".join(CITIES), "qualities": QUALITIES})
    return f"https://{server}.albion-online-data.com/api/v2/stats/prices/" + urllib.parse.quote(",".join(ids)) + "?" + q


def epoch_min(iso):
    """AODP naive-UTC ISO timestamp -> epoch minutes, 0 when missing/zero-date."""
    if not iso or iso.startswith("0001"):
        return 0
    try:
        return int(calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")) // 60)
    except ValueError:
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="europe", choices=["europe", "west", "east"])
    args = ap.parse_args()

    ids = json.loads(META.read_text(encoding="utf-8"))["ids"]
    cidx = {c: str(i) for i, c in enumerate(CITIES)}
    print(f"{len(ids)} ids from routesmeta.json -> AODP {args.server} prices (7 cities x q1-5)", flush=True)

    items, entries = {}, 0
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i:i + CHUNK]
        try:
            data = get_json(prices_url(args.server, chunk)) or []
        except Exception as e:
            print(f"    chunk {i} failed ({e}); skipping")
            data = []
        for row in data:
            p = row.get("sell_price_min") or 0
            b = row.get("buy_price_max") or 0
            if not p and not b:
                continue
            ci = cidx.get(row.get("city"))
            if ci is None:
                continue
            rec = [p, epoch_min(row.get("sell_price_min_date"))]
            if b:
                rec += [b, epoch_min(row.get("buy_price_max_date"))]
            items.setdefault(row["item_id"], {}).setdefault(str(row["quality"]), {})[ci] = rec
            entries += 1
        print(f"  {min(i+CHUNK, len(ids))}/{len(ids)}  items-with-orders={len(items)}", flush=True)
        time.sleep(SLEEP)

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "server": args.server,
        "cities": CITIES,
        "notes": {
            "value": "items[id][quality][cityIndex] = [p, t] or [p, t, b, bt]; p = sell_price_min (buy it / undercut it), b = buy_price_max (instant-sell exit), t/bt = epoch MINUTES of the AODP timestamp (0 = unknown)",
            "cityIndex": "index into the cities array",
            "freshness": "AODP is as fresh as the last player upload; the app must show age from t, never claim live",
            "missing": "no entry = no standing order seen; the app must show a gap, never a guess",
        },
        "items": items,
    }
    OUT.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {OUT}  ({OUT.stat().st_size/1024:.0f} KB)")
    print(f"items with at least one order {len(items)}/{len(ids)} | (item,q,city) entries {entries}")


if __name__ == "__main__":
    sys.exit(main())
