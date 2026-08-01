#!/usr/bin/env python3
"""
build_itemnames.py - fill in the localised item names the app is missing.

docs/data/items.json maps every market id to its name in 5 languages. It had NO builder: it was
produced by hand once (12/07) and never touched again, so every game patch since would have added
items the app can price but cannot name - it falls back to printing the raw id, e.g.
"T6_ARMOR_CLOTH_AVALON@3" where a name belongs. Measured 01/08: 147 ids out of routesmeta's 8048
already had no name.

WHY IT ONLY ADDS, NEVER REMOVES
The hand-made file has 11216 entries: 5163 base ids and 6053 enchant variants. The enchant
variants are DERIVED (upstream carries none) and they are not cosmetic - the app uses the presence
of a key as an existence test, twice:
  - allVariants() builds the Market Prices tier x enchant grid from `if (itemCatalog[vid])`
  - the search box resolves a typed id with `itemCatalog[raw] ? raw : ...`
Dropping a key therefore removes a cell from the grid, and rebuilding the file "cleanly" from
upstream would silently delete 6053 of them. The 854 base ids upstream has and the file does not
(season chests, quest items, non-tradable skillbooks) were filtered by a rule nobody wrote down.
So this builder does not try to reproduce that rule: it KEEPS every existing entry and only adds
what the app can reach and cannot name. Nothing regresses, and the gap closes.

Universe = every id the rest of the pipeline can put on screen: routesmeta ids, baseline gear,
recipe outputs and their material lines, craftmeta keys, priced materials. An id with @N takes its
base name (upstream has one name for the whole enchant family - verified: every
T4_ARMOR_CLOTH_AVALON@1..@4 reads "Adept's Robe of Purity").

Source: ao-bin-dumps formatted/items.json (22 MB, LocalizedNames). That is a different file from
the raw dump the other builders share, hence its own download - once per game patch, not per run.

Usage:
  python scripts/build_itemnames.py
  python scripts/build_itemnames.py --dump formatted_items.json   # reuse a local copy
  python scripts/build_itemnames.py --check                       # report the gap, write nothing
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
OUT = DATA / "items.json"
# Ids upstream has no name for either. Remembered so the 4-hourly refresh can call this script
# every run and stay free: if the only ids still missing are ones already known to be unnameable,
# there is nothing a 22 MB download could add. Local, gitignored, rebuilt on its own.
ORPHANS = ROOT / "scripts" / "data" / "itemnames_orphans.json"
FORMATTED_URL = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/formatted/items.json"
UA = "albionsnipe-app/1.0 (item names builder)"
# our short key -> upstream LocalizedNames key. Same 5 the hand-made file carried; upstream has 15.
LANGS = {"en": "EN-US", "fr": "FR-FR", "ru": "RU-RU", "tr": "TR-TR", "es": "ES-ES"}


def load(name):
    try:
        return json.loads((DATA / name).read_text(encoding="utf-8"))
    except Exception:
        return None


def universe():
    """Every id the app can display, from the datasets that drive each tab. Missing files are
    skipped rather than fatal: this must still be able to close part of the gap on a partial repo."""
    ids = set()
    rm = load("routesmeta.json")
    if rm:
        ids |= set(rm.get("ids") or ())
        ids |= set(rm.get("meta") or ())
    for f in ("baseline.json", "craftmeta.json", "materials.json"):
        d = load(f)
        if d:
            ids |= set((d.get("items") or d) if isinstance(d, dict) else ())
    rec = load("recipes.json")
    if rec:
        items = rec.get("items") or {}
        ids |= set(items)
        for lines in items.values():          # material lines: [[market_id, count, noret?], ...]
            for line in (lines or ()):        # a key with no known recipe stores null, not []
                if not isinstance(line, list):
                    continue
                if line and isinstance(line[0], str):
                    ids.add(line[0])
    return {i for i in ids if isinstance(i, str) and i}


def upstream_names(path=None):
    """id (without the @ITEMS_ prefix) -> {en, fr, ru, tr, es}, dropping entries with no English
    name (a handful of unused placeholders upstream)."""
    if path:
        raw = Path(path).read_text(encoding="utf-8")
    else:
        req = urllib.request.Request(FORMATTED_URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=300) as r:
            raw = r.read().decode("utf-8")
    out = {}
    for e in json.loads(raw):
        var = e.get("LocalizationNameVariable") or ""
        loc = e.get("LocalizedNames") or {}
        if not var.startswith("@ITEMS_") or not loc.get("EN-US"):
            continue
        out[var[7:]] = {k: loc[v] for k, v in LANGS.items() if loc.get(v)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", help="local formatted/items.json instead of downloading")
    ap.add_argument("--check", action="store_true", help="report the gap, write nothing")
    args = ap.parse_args()

    current = load("items.json") or {}
    want = universe()
    missing = sorted(i for i in want if i not in current)
    print(f"{len(current)} named ids | universe {len(want)} | without a name {len(missing)}")
    if not missing:
        print("nothing to add - every id the app can reach already has a name")
        return 0
    if args.check:
        print("  sample:", missing[:8])
        return 0

    try:
        known = set(json.loads(ORPHANS.read_text(encoding="utf-8")))
    except Exception:
        known = set()
    if known and not (set(missing) - known) and not args.dump:
        print(f"  all {len(missing)} are already known to have no upstream name - no download")
        return 0

    names = upstream_names(args.dump)
    print(f"upstream formatted dump: {len(names)} base ids")

    added, orphan = 0, []
    for i in missing:
        e = names.get(i) or names.get(i.split("@")[0])   # @N inherits the family name
        if e:
            current[i] = e
            added += 1
        else:
            orphan.append(i)

    if added:
        OUT.write_text(json.dumps(current, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
        print(f"wrote {OUT}  ({OUT.stat().st_size/1024:.0f} KB)  {len(current)} ids (+{added})")
    else:
        print("  no new name found upstream - items.json left untouched")
    if orphan:
        # not an error: ids the market carries that the client's own name table does not
        # (retired content still holding orders, test ids). The app prints their raw id, as before.
        print(f"  {len(orphan)} ids have no name upstream either, left as-is: {orphan[:6]}")
    try:
        ORPHANS.parent.mkdir(parents=True, exist_ok=True)
        ORPHANS.write_text(json.dumps(sorted(orphan)), encoding="utf-8")
    except Exception as e:
        print(f"  warning: could not write {ORPHANS.name} ({e}); the next run will re-download")
    return 0


if __name__ == "__main__":
    sys.exit(main())
