#!/usr/bin/env python3
"""
build_materials.py - City Buy List craft material prices (live-ish game data)

Emits docs/data/materials.json: the buy-side price of every material referenced
by recipes.json, per royal city + Caerleon + Brecilien, from AODP (the same
public source and server as baseline.json). Rebuild alongside baseline (2x/day):
recipes.json is static game data, this is the price layer that goes stale.

Two price layers per material, per city:
  - direct  (by)   = sell_price_min: the live cheapest standing sell order.
  - avg 7d  (by7)  = volume-weighted mean of the last 7 daily history points.
  - avg 30d (by30) = same over the last 30 days.
The app lets the craft calculator price materials on any of the three (direct is
freshest, the averages are steadier but cover fewer materials - history is thin).

Materials with no order and no history anywhere (faction / bound tokens,
faction-cape blueprints) are written as null so the app shows "cost n/a", never a
guess. Quality is always 1 (resources and artefacts are single-quality).

Usage:
  python scripts/build_materials.py
  python scripts/build_materials.py --server west   # default: europe (match baseline)
"""
import argparse, json, sys, time, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECIPES = ROOT / "docs" / "data" / "recipes.json"
CRAFTMETA = ROOT / "docs" / "data" / "craftmeta.json"
OUT = ROOT / "docs" / "data" / "materials.json"
CITIES = ["Bridgewatch", "Fort Sterling", "Lymhurst", "Martlock", "Thetford", "Caerleon", "Brecilien"]
CHUNK = 50
SLEEP = 2.0
UA = "city-buy-list-pro/1.0 (craft dataset builder)"


def materials_from_recipes():
    data = json.loads(RECIPES.read_text(encoding="utf-8"))["items"]
    mats = set()
    for r in data.values():
        if r:
            # row[0] et pas un depaquetage a deux : voir le meme correctif dans build_baseline.py.
            # Une ligne d'ingredient porte des champs en plus depuis le 29/07.
            for row in r:
                mats.add(row[0])
    return mats


def upgrade_materials():
    """Enchantment materials, read from craftmeta's ur (itself dump-derived).

    These are NOT in any recipe: crafting an enchanted item eats enchanted BARS, while
    ENCHANTING an existing one eats runes/souls/relics, which live in the dump's separate
    <upgraderequirements>. recipes.json never carried that path, so materials_from_recipes()
    has always missed them and the app could not price an enchant upgrade at all.

    Derived, not hard-coded, so a game patch that adds a material is picked up on the next
    craftmeta rebuild. Expect 21 ids as of 2026-07-16 (T4-T8 x rune/soul/relic, plus the
    consumable enchanters T1_ALCHEMY_EXTRACT/FISHSAUCE LEVEL1-3). Notably absent, and it is
    a fact rather than a hole: no avalonian shard - x.3 to x.4 has no upgrade path.

    A stale craftmeta would silently drop these ids and the upgrade flips would quietly go
    n/a, so an empty result is loud rather than silent."""
    try:
        items = json.loads(CRAFTMETA.read_text(encoding="utf-8"))["items"]
    except Exception as e:
        print(f"  WARNING: craftmeta.json unreadable ({e}) - enchant materials will NOT be priced")
        return set()
    mats = {v["ur"][0] for v in items.values() if v and v.get("ur") and v["ur"][0]}
    if not mats:
        print("  WARNING: craftmeta.json has no 'ur' field - rebuild it (build_craftmeta.py) "
              "or enchant upgrades stay unpriced")
    return mats


def get_json(url, tries=6):
    delay = 5
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < tries - 1:
                wait = int(e.headers.get("Retry-After") or 0) or delay
                print(f"    429; backoff {wait}s")
                time.sleep(wait)
                delay = min(delay * 2, 60)
            else:
                raise


def prices_url(server, ids):
    q = urllib.parse.urlencode({"locations": ",".join(CITIES), "qualities": "1"})
    return f"https://{server}.albion-online-data.com/api/v2/stats/prices/" + urllib.parse.quote(",".join(ids)) + "?" + q


def history_url(server, ids):
    q = urllib.parse.urlencode({"locations": ",".join(CITIES), "qualities": "1", "time-scale": "24"})
    return f"https://{server}.albion-online-data.com/api/v2/stats/history/" + urllib.parse.quote(",".join(ids)) + "?" + q


def wmean(series):
    """volume-weighted mean of avg_price over daily history points, or None."""
    cnt = sum(p.get("item_count", 0) for p in series)
    if not cnt:
        return None
    return round(sum(p.get("avg_price", 0) * p.get("item_count", 0) for p in series) / cnt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="europe", choices=["europe", "west", "east"])
    args = ap.parse_args()

    craft_mats, up_mats = materials_from_recipes(), upgrade_materials()
    mats = sorted(craft_mats | up_mats)
    print(f"{len(mats)} distinct materials -> AODP {args.server} "
          f"({len(craft_mats)} from recipes + {len(up_mats - craft_mats)} enchant materials)")

    # --- pass 1: direct buy-side price (cheapest live sell order) per city ---
    rows = {m: {} for m in mats}       # uniquename -> {city: sell_price_min>0}
    tsmax = ""
    for i in range(0, len(mats), CHUNK):
        chunk = mats[i:i + CHUNK]
        data = get_json(prices_url(args.server, chunk))
        for row in data:
            p = row.get("sell_price_min") or 0
            if p > 0:
                rows[row["item_id"]][row["city"]] = p
                ts = row.get("sell_price_min_date") or ""
                if ts > tsmax:
                    tsmax = ts
        print(f"  direct {min(i+CHUNK, len(mats))}/{len(mats)}")
        time.sleep(SLEEP)

    # --- pass 2: 7d / 30d volume-weighted average price per city (history) ---
    hist7, hist30 = {m: {} for m in mats}, {m: {} for m in mats}
    for i in range(0, len(mats), CHUNK):
        chunk = mats[i:i + CHUNK]
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
        print(f"  hist   {min(i+CHUNK, len(mats))}/{len(mats)}")
        time.sleep(SLEEP)

    # --- assemble: null only when no direct price AND no history anywhere ---
    prices, priced, bound, avg_cov = {}, 0, 0, 0
    for m in mats:
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
            if by7 or by30:
                avg_cov += 1
        else:
            prices[m] = None
            bound += 1

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "server": args.server,
        "cities": CITIES,
        "latest_price_ts": tsmax,
        "notes": {
            "value": "per material: {min: cheapest city direct price, by: {city: direct}, by7: {city: 7d avg}, by30: {city: 30d avg}} or null when no order and no history exist anywhere",
            "buy_side": "direct = sell_price_min = the live price you PAY to buy the material (cheapest standing sell order)",
            "averages": "by7/by30 = volume-weighted mean of AODP daily history over 7/30 days; thinner coverage than direct, missing cities fall back to direct in the app",
            "null": "faction / bound tokens and faction-cape blueprints are not market-traded; app must show 'cost n/a'",
            "quality": "always 1 (resources and artefacts are single-quality)",
            "staleness": "AODP prices lag; latest_price_ts shows direct freshness, rebuild with baseline",
        },
        "prices": prices,
    }
    OUT.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {OUT}  ({OUT.stat().st_size/1024:.0f} KB)")
    print(f"priced {priced}/{len(mats)} | with 7/30d avg {avg_cov} | no-market (null) {bound}")


if __name__ == "__main__":
    sys.exit(main())
