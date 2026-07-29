#!/usr/bin/env python3
"""
build_routesmeta.py - AlbionSnipe trade-route universe + static item facts

Emits docs/data/routesmeta.json: the frozen item universe of the Routes tab
(city-to-city arbitrage) plus the static facts routes math needs and no other
dataset carries:
  - ids  : every market id the Routes pipeline prices (build_routes.py) and
           the per-city volume builder covers (build_toptraded.py).
           Universe = baseline.json gear (T4-T8, base + enchants)
                    + materials.json craft materials (refined, artefacts, ...)
                    + every marketable dump item in EXTRA_CATS (raw + refined
                      resources all tiers, consumables, mounts, farmables).
  - w    : item weight in kg, keyed by BASE uniquename (enchanting never
           changes weight); profit-per-kg = the transport-efficiency signal.
  - meta : {cat, sub, tier, ench} for ids NOT in baseline.json (gear metadata
           already lives there; this covers resources/consumables/mounts so the
           shop-categories picker can filter them too).

Source: ao-bin-dumps items.json (@weight/@shopcategory/@shopsubcategory1/@tier,
same canonical dump as build_recipes.py). Rebuild only on a game patch.
Prices are NOT here: build_routes.py (2x/day) prices this universe.

Market-id convention (mirrors build_recipes.py): enchanted resources trade as
"<uniquename>@<level>" where the uniquename already carries _LEVELn
(T4_ORE_LEVEL1 -> T4_ORE_LEVEL1@1); gear enchants are "<base>@<n>".

Usage:
  python scripts/build_routesmeta.py
  python scripts/build_routesmeta.py --dump path/to/items.json
"""
import argparse, json, re, sys, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "docs" / "data" / "baseline.json"
MATERIALS = ROOT / "docs" / "data" / "materials.json"
OUT = ROOT / "docs" / "data" / "routesmeta.json"
AOBIN_URL = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/items.json"

# dump shop categories added on top of gear + craft materials (AFM-style breadth,
# minus furniture/vanity/other: not haul trades). Tier 2+ only, must be marketable.
# Dump naming (verified 2026-07-13): raw resources = crafting/resources, refined =
# crafting/refinedresources, farm animals + seeds = farming, so "crafting" and
# "farming" are the dump-side names for what AFM's picker calls Resources/Farming.
EXTRA_CATS = ("crafting", "consumables", "mounts", "farming")

# Access items (dungeon and hellgate maps) sit in shopcategory "other", which the line
# above drops on purpose - the rest of "other" is furniture and vanity, not trade goods.
# They are a category the gear-and-materials universe cannot represent: looted, never
# craftable, so no recipe, no baseline, and at the launch of new content no price history
# at all. That is the most volatile profile on the market, which is exactly what the
# Sniper is for. Dragonfire (31/08/2026, external deadline) puts tradable raid cards on
# this same shelf. Allowing the SUB-category keeps furniture and vanity out; opening
# "other" wholesale would drown the Routes picker. Patch day = add one (cat, sub) pair.
# The tier>=2 floor below does NOT apply here: an access item may carry no tier at all.
EXTRA_SUBS = {("other", "maps")}

LEVEL_RE = re.compile(r"_LEVEL(\d)$")
TIER_RE = re.compile(r"^T(\d)_")


def load_dump(path):
    if path:
        print(f"reading local dump {path}")
        return json.loads(Path(path).read_text(encoding="utf-8"))
    print(f"downloading {AOBIN_URL} ...")
    with urllib.request.urlopen(AOBIN_URL, timeout=300) as r:
        return json.loads(r.read().decode("utf-8"))


def index_all(dump):
    """uniquename -> item element, across every list-of-dicts bucket in the dump."""
    idx = {}
    for bucket, entries in dump["items"].items():
        if not isinstance(entries, list):
            entries = [entries]
        for e in entries:
            if isinstance(e, dict) and "@uniquename" in e:
                idx.setdefault(e["@uniquename"], e)
    return idx


def market_id(uniquename):
    """AODP market id for a dump uniquename (resources: _LEVELn trades as @n)."""
    m = LEVEL_RE.search(uniquename)
    return f"{uniquename}@{m.group(1)}" if m else uniquename


def meta_of(e, ench):
    # sub falls back to "other": the app's shop-categories picker labels every sub and
    # a null would crash it (baseline.json always has one, this universe not always).
    tier = int(e.get("@tier", 0) or 0)
    return {"cat": e.get("@shopcategory"), "sub": e.get("@shopsubcategory1") or "other", "tier": tier, "ench": ench}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", help="local ao-bin-dumps items.json instead of downloading")
    args = ap.parse_args()

    base_items = json.loads(BASELINE.read_text(encoding="utf-8"))["items"]
    mats = [m for m, v in json.loads(MATERIALS.read_text(encoding="utf-8"))["prices"].items() if v is not None]
    idx = index_all(load_dump(args.dump))
    print(f"{len(base_items)} baseline gear ids | {len(mats)} priced materials | dump items {len(idx)}")

    ids, meta, weights = set(base_items.keys()), {}, {}
    percat = {}

    def base_of(mid):
        return mid.split("@")[0]

    def add(mid, e, ench):
        ids.add(mid)
        if mid not in base_items:
            meta[mid] = meta_of(e, ench)
        w = e.get("@weight")
        if w is not None:
            try:
                weights[base_of(mid)] = float(w)
            except ValueError:
                pass
        cat = e.get("@shopcategory") or "?"
        percat[cat] = percat.get(cat, 0) + 1

    # gear weights (ids already in the universe via baseline)
    gear_no_dump = 0
    for key in base_items:
        e = idx.get(base_of(key))
        if e is None:
            gear_no_dump += 1
            continue
        add(key, e, base_items[key].get("ench", 0))

    # craft materials (materials.json keys are already market ids)
    mat_no_dump = []
    for mid in mats:
        e = idx.get(base_of(mid))
        if e is None:
            mat_no_dump.append(mid)
            continue
        ench = int(mid.split("@")[1]) if "@" in mid else 0
        add(mid, e, ench)

    # dump-wide extra categories (raw resources all tiers, consumables, mounts, farmables)
    # plus the explicitly allowed sub-categories (access items: dungeon and raid maps)
    access = 0
    for uname, e in idx.items():
        cat = e.get("@shopcategory")
        sub = e.get("@shopsubcategory1")
        by_sub = (cat, sub) in EXTRA_SUBS
        if cat not in EXTRA_CATS and not by_sub:
            continue
        tier = int(e.get("@tier", 0) or 0)
        if tier < 2 and not by_sub:                    # access items may carry no tier
            continue
        if by_sub:
            access += 1
        mid = market_id(uname)
        m = LEVEL_RE.search(uname)
        add(mid, e, int(m.group(1)) if m else 0)

    print(f"universe {len(ids)} ids | extra meta {len(meta)} | weights {len(weights)} base ids")
    print(f"access items via EXTRA_SUBS {sorted(EXTRA_SUBS)}: {access}")
    print(f"gear without dump entry {gear_no_dump} | materials without dump entry {len(mat_no_dump)}")
    if mat_no_dump:
        print("  material sample:", mat_no_dump[:6])
    print("per shop category:", dict(sorted(percat.items(), key=lambda kv: -kv[1])))

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": AOBIN_URL,
        "notes": {
            "ids": "frozen Routes universe: baseline gear + craft materials + dump categories " + ", ".join(EXTRA_CATS) + " (tier 2+) + access items in " + ", ".join(f"{c}/{s}" for c, s in sorted(EXTRA_SUBS)) + " (any tier)",
            "w": "item weight in kg keyed by BASE uniquename (strip @N first); enchanting never changes weight",
            "meta": "{cat, sub, tier, ench} only for ids missing from baseline.json (gear metadata lives there)",
            "rebuild": "patch-only, like recipes.json; prices live in routes.json (build_routes.py, 2x/day)",
        },
        "ids": sorted(ids),
        "w": weights,
        "meta": meta,
    }
    OUT.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {OUT}  ({OUT.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    sys.exit(main())
