#!/usr/bin/env python3
"""
build_journals.py - AlbionSnipe crafting-journal dataset (prices 2x/day + game constants)

Emits docs/data/journals.json for the Craft Planner journal layer:
  - tiers: per tier, cap (fame to fill, dump @maxfame, matches the wiki table incl. the
    odd T7 28380 / T8 58590) and npc (empty-journal silver cost at the laborer,
    dump craftingrequirements @silver - the price floor when the market is dearer).
  - fpr: crafting fame per REFINED RESOURCE consumed, by tier. The per-craft fame is
    fame = (refined resources in the recipe) x fpr[tier] x 2^enchant x item fame factor
    (craftmeta ff, dump @destinyandjournalcraftfamefactor, artefact lines only).
    Verified 18/18 against albiononlinegrind.com/table/item-crafting-fame on 2026-07-22
    (2H=32 res: T8.0 = 32x1395 = 44640) and consistent with the Lands Awakened patch note
    ("neatly divisible": 28380/645 = 44, 58590/1395 = 42). T2/T3 have no verified fpr:
    absent here, the app shows fame/books n/a for those tiers (never a guess).
  - prices: EMPTY and FULL journal market prices per city (same three layers as
    materials.json: by/by7/by30, buy-side sell_price_min + history means). null = no
    order and no history anywhere (T2/T3 FULL mostly).

Journal filling facts the app relies on (wiki Journal page, read 2026-07-22):
  - a crafting journal fills ONLY from crafts of its own tier and branch (dump validitem);
  - only BASE fame counts - the premium fame bonus does NOT fill journals;
  - overflow fame carries over into further journals (no waste while you carry enough).

Rebuild alongside baseline/materials (2x/day): the price layer goes stale, the constants
do not (game patch only).

Usage:
  python scripts/build_journals.py
  python scripts/build_journals.py --server west     # default: europe (match baseline)
"""
import argparse, json, sys, time, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "journals.json"
CITIES = ["Bridgewatch", "Fort Sterling", "Lymhurst", "Martlock", "Thetford", "Caerleon", "Brecilien"]
CHUNK = 50
SLEEP = 2.0
UA = "albionsnipe-app/1.0 (journal dataset builder)"

# ao-bin-dumps journalitem @maxfame / craftingrequirements @silver, cross-checked with the
# wiki tables (both read 2026-07-22). Game patch only.
TIERS = {
    2: {"cap": 900,   "npc": 500},
    3: {"cap": 1800,  "npc": 1000},
    4: {"cap": 3600,  "npc": 2000},
    5: {"cap": 7200,  "npc": 4000},
    6: {"cap": 14400, "npc": 8000},
    7: {"cap": 28380, "npc": 16000},
    8: {"cap": 58590, "npc": 32000},
}
# crafting fame per refined resource consumed (see module docstring for the verification)
FPR = {4: 22.5, 5: 90, 6: 270, 7: 645, 8: 1395}
# branch letter (craftmeta jb) -> journal id fragment + laborer name shown in the UI
BRANCHES = {
    "W": {"id": "WARRIOR",   "name": "Blacksmith"},
    "M": {"id": "MAGE",      "name": "Imbuer"},
    "H": {"id": "HUNTER",    "name": "Fletcher"},
    "T": {"id": "TOOLMAKER", "name": "Tinker"},
}


def get_json(url, tries=30):
    # AODP answers 429 with a short cooldown and no Retry-After; a fixed 3s wait beats an
    # exponential backoff here (same fix as build_toptraded, 2026-07-11).
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < tries - 1:
                print(f"    429; wait 3s ({attempt + 1}/{tries})")
                time.sleep(3.0)
            else:
                raise


def prices_url(server, ids):
    q = urllib.parse.urlencode({"locations": ",".join(CITIES), "qualities": "1"})
    return f"https://{server}.albion-online-data.com/api/v2/stats/prices/" + urllib.parse.quote(",".join(ids)) + "?" + q


def history_url(server, ids):
    q = urllib.parse.urlencode({"locations": ",".join(CITIES), "qualities": "1", "time-scale": "24"})
    return f"https://{server}.albion-online-data.com/api/v2/stats/history/" + urllib.parse.quote(",".join(ids)) + "?" + q


def wmean(series):
    cnt = sum(p.get("item_count", 0) for p in series)
    if not cnt:
        return None
    return round(sum(p.get("avg_price", 0) * p.get("item_count", 0) for p in series) / cnt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="europe", choices=["europe", "west", "east"])
    args = ap.parse_args()

    ids = [f"T{t}_JOURNAL_{b['id']}_{s}" for t in TIERS for b in BRANCHES.values() for s in ("EMPTY", "FULL")]
    print(f"{len(ids)} journal ids -> AODP {args.server}")

    rows = {m: {} for m in ids}
    tsmax = ""
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i:i + CHUNK]
        for row in get_json(prices_url(args.server, chunk)):
            p = row.get("sell_price_min") or 0
            if p > 0:
                rows[row["item_id"]][row["city"]] = p
                ts = row.get("sell_price_min_date") or ""
                if ts > tsmax:
                    tsmax = ts
        print(f"  direct {min(i + CHUNK, len(ids))}/{len(ids)}")
        time.sleep(SLEEP)

    hist7, hist30 = {m: {} for m in ids}, {m: {} for m in ids}
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i:i + CHUNK]
        try:
            data = get_json(history_url(args.server, chunk))
        except Exception as e:
            print(f"    history chunk failed ({e}); skipping")
            data = []
        for row in data:
            series = sorted(row.get("data") or [], key=lambda p: p.get("timestamp") or "")
            mid, city = row["item_id"], row["location"]
            a7, a30 = wmean(series[-7:]), wmean(series[-30:])
            if a7 is not None:
                hist7[mid][city] = a7
            if a30 is not None:
                hist30[mid][city] = a30
        print(f"  hist   {min(i + CHUNK, len(ids))}/{len(ids)}")
        time.sleep(SLEEP)

    prices, priced = {}, 0
    for m in ids:
        by, by7, by30 = rows[m], hist7[m], hist30[m]
        if by or by7 or by30:
            entry = {}
            if by:
                entry["min"] = min(by.values())
                entry["by"] = by
            if by7:
                entry["by7"] = by7
            if by30:
                entry["by30"] = by30
            prices[m] = entry
            priced += 1
        else:
            prices[m] = None

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "server": args.server,
        "cities": CITIES,
        "latest_price_ts": tsmax,
        "notes": {
            "fame": "per craft: (refined resources in recipe) x fpr[tier] x 2^enchant x (craftmeta ff or 1); verified vs albiononlinegrind item-crafting-fame 2026-07-22",
            "fill": "cap = base fame to fill (premium bonus does NOT count, wiki); overflow carries over; journal must match the craft's tier and branch (craftmeta jb)",
            "npc": "empty-journal silver cost at the laborer - the guaranteed buy price when the market has none or is dearer",
            "prices": "per journal id: {min, by, by7, by30} like materials.json, or null when nothing trades anywhere",
            "t2t3": "no verified fame-per-resource for T2/T3 crafts: fpr omits them, app shows fame/books n/a",
        },
        "tiers": {str(k): v for k, v in TIERS.items()},
        "fpr": {str(k): v for k, v in FPR.items()},
        "branches": BRANCHES,
        "prices": prices,
    }
    OUT.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {OUT}  ({OUT.stat().st_size/1024:.0f} KB)")
    print(f"priced {priced}/{len(ids)}")


if __name__ == "__main__":
    sys.exit(main())
