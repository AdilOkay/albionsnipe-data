#!/usr/bin/env python3
"""
build_recipes.py - AlbionSnipe craft dataset (static game data)

Emits docs/data/recipes.json: for every gear key in baseline.json, the exact
crafting materials + counts, pulled from the canonical ao-bin-dumps item data
(the game client's own craftingrequirements). Never a guessed count.

Keys mirror baseline.json exactly, including enchant variants:
  "T4_2H_ARCANESTAFF"      -> base recipe
  "T4_2H_ARCANESTAFF@1..4" -> the enchantment-level recipe (enchanted materials)

Value = list of [material_uniquename, count], or null when the item has no
craftable recipe (vanity / arena banners) so the app shows "no recipe", never
a fabricated one.

Source: https://github.com/ao-data/ao-bin-dumps (items.json). Rebuild only on a
game patch; prices live in build_materials.py, not here.

Usage:
  python scripts/build_recipes.py
  python scripts/build_recipes.py --dump path/to/items.json   # use a local dump
"""
import argparse, json, sys, time, urllib.request
from pathlib import Path

AOBIN_URL = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/items.json"
GEAR_BUCKETS = ("weapon", "equipmentitem", "transformationweapon")
ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "docs" / "data" / "baseline.json"
OUT = ROOT / "docs" / "data" / "recipes.json"


def load_dump(dump_path):
    if dump_path:
        print(f"reading local dump {dump_path}")
        return json.loads(Path(dump_path).read_text(encoding="utf-8"))
    print(f"downloading {AOBIN_URL} ...")
    with urllib.request.urlopen(AOBIN_URL, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def index_gear(dump):
    """uniquename -> item element, across the gear buckets."""
    idx = {}
    items = dump["items"]
    for bucket in GEAR_BUCKETS:
        for e in items.get(bucket, []):
            if isinstance(e, dict) and "@uniquename" in e:
                idx[e["@uniquename"]] = e
    return idx


def craft_resources(node):
    """Normalise a craftingrequirements block into ([[uniquename, count, noret?], ...], batch).

    Two facts the dump carries and we used to throw away, both reported by a crash-tester
    and both confirmed here in the game's own data:

    @amountcrafted - ONE craft does not make one item. Every cooked meal makes 10 (the ones
      with fish make 1), potions make 5 or 10, butchering makes 18. 204 recipes make 10 and
      97 make 5. Ignoring it prices a batch as a single unit, so a meal looked ten times more
      expensive to craft than it is. Emitted per key, only when it is not 1.

    @maxreturnamount="0" - this material is NEVER refunded by the resource return rate. The
      game marks it itself, on 4952 recipes: the avalonian energy token (which is exactly the
      case the tester hit), faction and royal tokens, community tokens, and the cape used to
      craft a better cape. The app was guessing this with a hard-coded regex of refined
      resource names, which got the tokens right by accident and cooking ingredients wrong -
      milk, meat, eggs and crops carry no such flag, so they DO get the return rate (wiki:
      "Ingredient Quantity = Portions x Recipe x (100% - RRR) + RRR x Recipe"). Carried as an
      optional third element so every existing reader, which destructures [mat, count], is
      untouched.
    """
    if not isinstance(node, dict):
        return None, 1
    cr = node.get("craftingrequirements")
    if isinstance(cr, list):            # a few items carry multiple recipe blocks
        cr = cr[0] if cr else None      # first block = the standard resource recipe
    if not isinstance(cr, dict):
        return None, 1
    res = cr.get("craftresource")
    if res is None:
        return None, 1
    batch = int(cr.get("@amountcrafted", 1) or 1)
    res = res if isinstance(res, list) else [res]
    out = []
    for x in res:
        if isinstance(x, dict) and "@uniquename" in x and "@count" in x:
            # market/AODP id: enchanted resources trade as "<uniquename>@<level>"
            # (e.g. T4_METALBAR_LEVEL1@1); base resources and artefacts stay bare.
            mid = x["@uniquename"]
            lvl = int(x.get("@enchantmentlevel", 0) or 0)
            if lvl > 0:
                mid = f"{mid}@{lvl}"
            row = [mid, int(x["@count"])]
            if str(x.get("@maxreturnamount", "")) == "0":
                row.append(0)           # 0 = never refunded, straight from the game data
            out.append(row)
    return (out or None), batch


def recipe_for(idx, base, ench):
    e = idx.get(base)
    if e is None:
        return "missing", 1             # base item not in the dump at all
    if ench == 0:
        return craft_resources(e)
    enc = (e.get("enchantments") or {}).get("enchantment")
    enc = enc if isinstance(enc, list) else ([enc] if enc else [])
    for x in enc:
        if isinstance(x, dict) and int(x.get("@enchantmentlevel", -1)) == ench:
            return craft_resources(x)
    return None, 1                      # enchant level not present


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", help="path to a local ao-bin-dumps items.json")
    args = ap.parse_args()

    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))["items"]
    dump = load_dump(args.dump)
    idx = index_gear(dump)

    recipes, batches, no_recipe, missing = {}, {}, [], []
    for key, v in baseline.items():
        base = key.split("@")[0]
        r, batch = recipe_for(idx, base, v.get("ench", 0))
        if r == "missing":
            missing.append(key)
            recipes[key] = None
        elif r is None:
            no_recipe.append(key)
            recipes[key] = None
        else:
            recipes[key] = r
            if batch != 1:
                batches[key] = batch

    # transformationweapon = the shapeshifter weapons (Lightcaller, Bloodmoon, Stillgaze, ...).
    # Craftable gear sold at the Black Market, but never seeded into the frozen baseline universe,
    # so they were invisible to the planner. Enumerate the bucket (T4-T8) and add their keys here;
    # build_baseline then resolves metadata + Black Market prices from the same buckets. Standard
    # artefact-weapon model: refined resources + the SHAPESHIFTER artefact + a rare tracking mat.
    added = 0
    for e in dump["items"].get("transformationweapon", []):
        if not (isinstance(e, dict) and e.get("@uniquename") and e.get("@shopcategory")):
            continue
        base = e["@uniquename"]
        if len(base) < 2 or not base[1].isdigit() or not (4 <= int(base[1]) <= 8):
            continue
        for ench in range(0, 5):                      # base + each enchant level that has a recipe
            k = base if ench == 0 else f"{base}@{ench}"
            if k in recipes:                          # never override an existing entry
                continue
            r, batch = recipe_for(idx, base, ench)
            if isinstance(r, list) and r:
                recipes[k] = r
                if batch != 1:
                    batches[k] = batch
                added += 1
    if added:
        print(f"transformationweapon (shapeshifter): +{added} recipe keys added to the universe")

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": AOBIN_URL,
        "notes": {
            "scope": "recipe = crafting materials for every baseline.json gear key",
            "keys": "mirror baseline.json exactly; '@N' = enchantment level N (enchanted materials)",
            "value": "[[material_uniquename, count], ...] or null when the item has no craft recipe",
            "third_element": "an optional 3rd element 0 means this material is NEVER refunded by the resource return rate (@maxreturnamount=0 in the dump): avalonian energy, faction/royal/community tokens, the cape consumed by a cape upgrade",
            "batch": "key -> units produced by ONE craft, listed only when it is not 1 (cooked meals 10, potions 5 or 10, butchering 18). Costs and margins are per unit, so divide by it.",
            "null": "vanity / arena-banner items have no recipe; never fabricated",
            "pricing": "materials are priced separately by build_materials.py (materials.json)",
        },
        "items": recipes,
        "batch": batches,
    }
    OUT.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")

    total = len(baseline)
    resolved = sum(1 for x in recipes.values() if x)
    print(f"\nwrote {OUT}  ({OUT.stat().st_size/1024:.0f} KB)")
    print(f"resolved {resolved}/{total} recipes | no-recipe {len(no_recipe)} | base-missing {len(missing)}")
    if missing:
        print(" base-missing sample:", missing[:10])
    if no_recipe:
        print(" no-recipe sample   :", no_recipe[:10])


if __name__ == "__main__":
    sys.exit(main())
