#!/usr/bin/env python3
"""
build_baseline.py - AlbionSnipe Black Market baseline

Emits docs/data/baseline.json: the file every other dataset keys off
(recipes.json, materials.json, craftmeta.json all mirror its item set).
For every key already in recipes.json (the frozen, patch-only gear universe:
T4-T8 weapons/armors/head/shoes/offhands/bags/capes, base + crafted enchant
levels), the Black Market baseline per quality:
  - a1 / a7 / a30 : volume-weighted average price (AODP history, last 1 / 7 / 30 days)
  - vol7     : mean items sold per day at the Black Market (last 7 days)
  - bm_buy, bm_buy_ts : the live Black Market buy order + its timestamp
                         (AODP prices, snapshot at build time)

Item metadata (cat/sub/tier/artefact/name) barely changes between game
patches, so it is read once from ao-bin-dumps + items.json (same source as
build_recipes.py) rather than fetched from AODP.

This dataset was historically produced by an external generator never checked
into this repo. Recreated here so the pipeline is self-contained and can run
on a schedule (see .github/workflows/refresh-data.yml).

An item that has no market data anywhere keeps its metadata but no "q" key -
the app must show "no baseline", never a guess (unchanged convention).

Usage:
  python scripts/build_baseline.py
  python scripts/build_baseline.py --dump items.json --server west
"""
import argparse, json, re, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECIPES = ROOT / "docs" / "data" / "recipes.json"
ITEMS_I18N = ROOT / "docs" / "data" / "items.json"
OUT = ROOT / "docs" / "data" / "baseline.json"
AOBIN_URL = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/items.json"
GEAR_BUCKETS = ("weapon", "equipmentitem", "transformationweapon")
BM_LOC = "Black Market"
QUALITIES = "1,2,3,4,5"
CHUNK = 50
SLEEP = 2.0
UA = "albionsnipe-app/1.0 (baseline dataset builder)"
KEY_RE = re.compile(r"^(T(\d)_.+?)(?:@(\d))?$")


def load_dump(path):
    if path:
        print(f"reading local dump {path}")
        return json.loads(Path(path).read_text(encoding="utf-8"))
    print(f"downloading {AOBIN_URL} ...")
    with urllib.request.urlopen(AOBIN_URL, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def index_gear(dump):
    """base uniquename -> {cat, sub, tier} from @shopcategory/@shopsubcategory1/@tier."""
    idx = {}
    for bucket in GEAR_BUCKETS:
        for e in dump["items"].get(bucket, []):
            if isinstance(e, dict) and "@uniquename" in e and e.get("@shopcategory"):
                idx[e["@uniquename"]] = {
                    "cat": e["@shopcategory"],
                    "sub": e.get("@shopsubcategory1"),
                    "tier": int(e.get("@tier", 0) or 0),
                }
    return idx


def get_json(url, tries=10):
    # Exponential backoff SCOPED to this one call (resets for the next chunk) - distinct
    # from the bug this pipeline hit before, where a delay escalated and PERSISTED across
    # chunks, permanently slowing the whole run after one early 429. Honors Retry-After
    # when AODP sends it, else backs off 3s/6s/12s/... capped at 20s.
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
    q = urllib.parse.urlencode({"locations": BM_LOC, "qualities": QUALITIES})
    return f"https://{server}.albion-online-data.com/api/v2/stats/prices/" + urllib.parse.quote(",".join(ids)) + "?" + q


def history_url(server, ids):
    q = urllib.parse.urlencode({"locations": BM_LOC, "qualities": QUALITIES, "time-scale": "24"})
    return f"https://{server}.albion-online-data.com/api/v2/stats/history/" + urllib.parse.quote(",".join(ids)) + "?" + q


def wmean(series):
    cnt = sum(p.get("item_count", 0) for p in series)
    if not cnt:
        return None
    return round(sum(p.get("avg_price", 0) * p.get("item_count", 0) for p in series) / cnt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="europe", choices=["europe", "west", "east"])
    ap.add_argument("--dump", help="local ao-bin-dumps items.json instead of downloading")
    args = ap.parse_args()

    recipes_data = json.loads(RECIPES.read_text(encoding="utf-8"))["items"]
    names = json.loads(ITEMS_I18N.read_text(encoding="utf-8"))
    gear = index_gear(load_dump(args.dump))
    print(f"{len(recipes_data)} keys from recipes.json (frozen gear universe) -> AODP {args.server} Black Market")

    items, skipped = {}, []
    for key in recipes_data:
        m = KEY_RE.match(key)
        if not m:
            skipped.append(key)
            continue
        base, tier, ench = m.group(1), int(m.group(2)), int(m.group(3) or 0)
        meta = gear.get(base)
        if not meta:
            skipped.append(key)
            continue
        recipe = recipes_data.get(key)
        artefact = bool(recipe) and any("ARTEFACT_" in mat for mat, _cnt in recipe)
        items[key] = {
            "cat": meta["cat"], "sub": meta["sub"], "tier": tier,
            "artefact": artefact, "name": (names.get(base) or {}).get("en", base),
            "ench": ench,
        }
    print(f"metadata resolved {len(items)}/{len(recipes_data)} | skipped (no dump entry) {len(skipped)}")
    if skipped:
        print("  skipped sample:", skipped[:10])

    keys = list(items.keys())

    # pass 1: live prices (bm_buy + timestamp)
    for i in range(0, len(keys), CHUNK):
        chunk = keys[i:i + CHUNK]
        data = get_json(prices_url(args.server, chunk)) or []
        for row in data:
            if row.get("buy_price_max"):
                q = str(row["quality"])
                slot = items[row["item_id"]].setdefault("q", {}).setdefault(q, {})
                slot["bm_buy"] = row["buy_price_max"]
                slot["bm_buy_ts"] = row.get("buy_price_max_date")
        print(f"  prices  {min(i+CHUNK, len(keys))}/{len(keys)}", flush=True)
        time.sleep(SLEEP)

    # pass 2: history (a7 / a30 / vol7)
    for i in range(0, len(keys), CHUNK):
        chunk = keys[i:i + CHUNK]
        try:
            data = get_json(history_url(args.server, chunk)) or []
        except Exception as e:
            print(f"    history chunk {i} failed ({e}); skipping")
            data = []
        for row in data:
            series = sorted(row.get("data") or [], key=lambda p: p.get("timestamp") or "")
            if not series:
                continue
            q = str(row["quality"])
            slot = items[row["item_id"]].setdefault("q", {}).setdefault(q, {})
            a7, a30 = wmean(series[-7:]), wmean(series[-30:])
            # a1 = the LAST day alone. Added 30/07 for one reason: with only a7 and a30 the app
            # cannot tell a dip in a rising item from a real turn - both print `over avg` negative
            # and `trend` positive (MECANIQUE_MARCHE_ALBION section 9 point 8). What is missing is the
            # RECENT DIRECTION of the BM order, and a1 against a7 gives it. Free: the series is
            # already fetched at time-scale 24, so this is arithmetic on data we hold, no extra call.
            a1 = wmean(series[-1:])
            if a1 is not None:
                slot["a1"] = a1
            if a7 is not None:
                slot["a7"] = a7
                vol7 = sum(p.get("item_count", 0) for p in series[-7:]) / 7
                if vol7:
                    slot["vol7"] = round(vol7, 1)
            if a30 is not None:
                slot["a30"] = a30
        print(f"  history {min(i+CHUNK, len(keys))}/{len(keys)}", flush=True)
        time.sleep(SLEEP)

    with_q = sum(1 for v in items.values() if v.get("q"))
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "server": args.server,
        "notes": {
            "scope": "T4-T8 gear (weapons, armors, head, shoes, offhands, bags, capes)",
            "a1_a7_a30": "volume-weighted mean of AODP Black Market daily history (1/7/30 days); a1 vs a7 = recent direction",
            "vol7": "mean items/day at Black Market over last 7 days (item_count)",
            "bm_buy": "AODP buy_price_max at Black Market at build time; check bm_buy_ts for staleness",
            "missing": "items without 'q' have no reliable Black Market baseline; the app must show 'no baseline', never a guess",
        },
        "items": items,
    }
    OUT.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {OUT}  ({OUT.stat().st_size/1024:.0f} KB)")
    print(f"items {len(items)} | with Black Market data (has 'q') {with_q} | no data {len(items)-with_q}")


if __name__ == "__main__":
    sys.exit(main())
