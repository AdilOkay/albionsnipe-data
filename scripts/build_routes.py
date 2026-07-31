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

  - stuck : how long p has held its CURRENT value, carried across runs through a
            local state file. AODP's own timestamp cannot answer this - it moves
            on every player upload even when the order behind it never changed,
            so a price frozen for four days still reads "2h old". Measured 31/07
            on Robe of Purity 6.3: buy side pinned at ~749,98x since 28/07 while
            its timestamp refreshed all day. Testers read that as "the app does
            not update"; it was the market that was not moving, and nothing in
            the app could say so.

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
# Local, never published, gitignored: {"id|q|cityIndex": [price, firstSeenEpochMinutes]}. It is
# what lets a run know that a price has not moved since a PREVIOUS run - AODP cannot say it, since
# it re-stamps its timestamp on every player upload even when the order itself never changed.
SEEN = ROOT / "scripts" / "data" / "routes_seen.json"
CITIES = ["Bridgewatch", "Fort Sterling", "Lymhurst", "Martlock", "Thetford", "Caerleon", "Brecilien"]
QUALITIES = "1,2,3,4,5"
CHUNK = 50
SLEEP = 2.0
STUCK_MIN_H = 24        # only publish a "frozen since" marker past this age; under it, it is just a calm market
# Tolerance on "the price did not move". Measured 31/07 on Robe of Purity 6.3 at Lymhurst: 749,996
# -> 749,986 -> 749,981 over four days, a reseller nudging a few silver to stay on top. Comparing
# exact values called that four separate prices and reset the counter every time - it would have
# missed the very case that made testers report the tab as frozen. 0.5% is noise to a trader.
STUCK_TOL = 0.005
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


def load_seen():
    """Previous run's {key: [price, firstSeenMinutes]}. Missing/corrupt = start over: the
    markers simply reappear after STUCK_MIN_H, nothing breaks."""
    try:
        return json.loads(SEEN.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="europe", choices=["europe", "west", "east"])
    args = ap.parse_args()

    ids = json.loads(META.read_text(encoding="utf-8"))["ids"]
    cidx = {c: str(i) for i, c in enumerate(CITIES)}
    print(f"{len(ids)} ids from routesmeta.json -> AODP {args.server} prices (7 cities x q1-5)", flush=True)

    seen_old = load_seen()
    seen_new = {}
    now_min = int(time.time() // 60)

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
            t = epoch_min(row.get("sell_price_min_date"))
            rec = [p, t]
            if b:
                rec += [b, epoch_min(row.get("buy_price_max_date"))]
            items.setdefault(row["item_id"], {}).setdefault(str(row["quality"]), {})[ci] = rec
            entries += 1
            # how long this exact sell price has been the cheapest standing order. Carried over
            # from the previous run while the number does not move; reset the moment it does.
            if p:
                key = f'{row["item_id"]}|{row["quality"]}|{ci}'
                prev = seen_old.get(key)
                # keep the ORIGINAL reference price, not the latest one: re-anchoring on every
                # tolerated nudge would let a drift of +0.4% per run walk the price anywhere
                # while the counter kept saying "unchanged".
                seen_new[key] = prev if (prev and abs(p - prev[0]) <= prev[0] * STUCK_TOL) \
                    else [p, t or now_min]
        print(f"  {min(i+CHUNK, len(ids))}/{len(ids)}  items-with-orders={len(items)}", flush=True)
        time.sleep(SLEEP)

    # Publish only the markers worth showing. A price that has held for a few hours is a calm
    # market; one that has held for days is an order nobody fills, and the app has to say so -
    # that is exactly what a tester reads as "the price never updates".
    stuck, floor = {}, STUCK_MIN_H * 60
    for key, (p, first) in seen_new.items():
        if now_min - first < floor:
            continue
        iid, q, ci = key.rsplit("|", 2)
        stuck.setdefault(iid, {}).setdefault(q, {})[ci] = first

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "server": args.server,
        "cities": CITIES,
        "notes": {
            "value": "items[id][quality][cityIndex] = [p, t] or [p, t, b, bt]; p = sell_price_min (buy it / undercut it), b = buy_price_max (instant-sell exit), t/bt = epoch MINUTES of the AODP timestamp (0 = unknown)",
            "cityIndex": "index into the cities array",
            "freshness": "AODP is as fresh as the last player upload; the app must show age from t, never claim live",
            "missing": "no entry = no standing order seen; the app must show a gap, never a guess",
            "stuck": f"stuck[id][quality][cityIndex] = epoch MINUTES when p was FIRST seen at its current value, published only past {STUCK_MIN_H}h. t says when someone last looked at the market; this says when the price itself last moved. Absent = the price has moved recently.",
        },
        "items": items,
        "stuck": stuck,
    }
    OUT.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    try:
        SEEN.parent.mkdir(parents=True, exist_ok=True)
        SEEN.write_text(json.dumps(seen_new, separators=(",", ":")), encoding="utf-8")
    except Exception as e:
        print(f"    warning: could not write {SEEN} ({e}); frozen-price markers restart next run")
    print(f"\nwrote {OUT}  ({OUT.stat().st_size/1024:.0f} KB)")
    print(f"items with at least one order {len(items)}/{len(ids)} | (item,q,city) entries {entries}")
    print(f"prices unchanged for {STUCK_MIN_H}h+ {sum(len(c) for q in stuck.values() for c in q.values())}/{len(seen_new)}")


if __name__ == "__main__":
    sys.exit(main())
