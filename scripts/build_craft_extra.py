#!/usr/bin/env python3
"""
build_craft_extra.py - extend recipes.json + craftmeta.json beyond gear.

build_recipes.py / build_craftmeta.py only cover baseline gear. The Craft Calc
tab also plans REFINING (bars/planks/cloth/leather/stoneblock) and COOKING /
ALCHEMY (food + potions). This script pulls those recipes + item values from
the same ao-bin-dumps source and MERGES them into the existing JSON (gear keys
are preserved, never dropped). Patch-only, like the two scripts it extends.

Ingredient PRICES for the new inputs (raw ore/wood/..., crops, herbs, fish)
are not added here: they already live in routes.json (the Routes universe scan,
refreshed 2x/day), which the app reads as a fallback when materials.json has no
price. So no material-pricing pipeline change is needed.

Usage:
  python scripts/build_craft_extra.py --dump path/to/items.json   # reuse a local dump (17 MB)
  python scripts/build_craft_extra.py                             # download the dump
"""
import argparse, json, re, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECIPES = ROOT / "docs" / "data" / "recipes.json"
CRAFTMETA = ROOT / "docs" / "data" / "craftmeta.json"
CITIES = ROOT / "scripts" / "data" / "bonus_cities.json"
AOBIN_URL = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/items.json"

REFINED = ("METALBAR", "PLANKS", "CLOTH", "LEATHER", "STONEBLOCK")
REFINED_RE = re.compile(r"^T\d_(?:" + "|".join(REFINED) + r")(?:_LEVEL\d)?$")


def load_dump(path):
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    print(f"downloading {AOBIN_URL} ...")
    with urllib.request.urlopen(AOBIN_URL, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def craft_resources(node):
    """craftingrequirements -> ([[market_id, count, noret?], ...], batch). See build_recipes.py.

    This is the script that owns FOOD and POTIONS, so it is the one where both facts actually
    bite: a cooked meal makes 10 per craft (fish meals make 1), a potion 5 or 10, and the
    avalonian energy token carries @maxreturnamount=0 - both reported by a crash-tester, both
    read straight from the dump rather than assumed.
    """
    if not isinstance(node, dict):
        return None, 1
    cr = node.get("craftingrequirements")
    if isinstance(cr, list):
        cr = cr[0] if cr else None
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
            mid = x["@uniquename"]
            lvl = int(x.get("@enchantmentlevel", 0) or 0)
            if lvl > 0:
                mid = f"{mid}@{lvl}"
            row = [mid, int(x["@count"])]
            if str(x.get("@maxreturnamount", "")) == "0":
                row.append(0)              # never refunded by the return rate
            out.append(row)
    return (out or None), batch


def market_key(uniquename):
    """dump uniquename -> AODP market id (refined _LEVELn trades as <name>@n)."""
    m = re.search(r"_LEVEL(\d)$", uniquename)
    return f"{uniquename}@{m.group(1)}" if m else uniquename


def node_focus(node):
    """@craftingfocus of a node's own craftingrequirements (first block when a list).
    Every extra target (refined _LEVELn, meal, potion) is a single dump node carrying
    its own recipe - no enchantment traversal needed here, unlike gear in craftmeta."""
    cr = node.get("craftingrequirements")
    if isinstance(cr, list):
        cr = cr[0] if cr else None
    if not isinstance(cr, dict):
        return None
    f = cr.get("@craftingfocus")
    return int(float(f)) if f is not None else None


# No ur here, deliberately. Consumables DO enchant - potions eat T1_ALCHEMY_EXTRACT_LEVELn
# (3 per potion), food eats T1_FISHSAUCE_LEVELn (1 per meal) - but that requirement sits on
# their <enchantment> nodes, and the targets below are the BASE items only: an enchanted
# potion (T4_POTION_HEAL@1) has no recipe here, so the planner cannot plan it and the app has
# nothing to spend those materials on. Pricing them would be a dataset for a feature that does
# not exist. Add both together, or neither.


def index_iv_cc(dump):
    """uniquename -> (@itemvalue float|None, @craftingcategory str|None)."""
    idx = {}
    for group in dump["items"].values():
        if not isinstance(group, list):
            group = [group]
        for e in group:
            if isinstance(e, dict) and "@uniquename" in e:
                iv = e.get("@itemvalue")
                idx[e["@uniquename"]] = (float(iv) if iv is not None else None, e.get("@craftingcategory"))
    return idx


def item_iv(key, recipes, idx, seen, depth=0):
    if depth > 5 or key in seen:
        return None
    seen = seen | {key}
    r = recipes.get(key)
    if not r:
        return None
    total = 0
    for row in r:                        # [mat, count] or [mat, count, 0] when the
        mat, count = row[0], row[1]      # material is never refunded (see build_recipes.py)
        base = re.sub(r"@\d+$", "", mat)
        iv = idx.get(base, (None, None))[0]
        if iv is None:
            iv = item_iv(mat, recipes, idx, seen, depth + 1)
        if iv is None:
            return None
        total += iv * count
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", help="local ao-bin-dumps items.json")
    args = ap.parse_args()

    dump = load_dump(args.dump)
    items = dump["items"]

    # collect the extra craftable items: refined resources + food + potions
    targets = []
    nutrition = {}                                     # market_key -> @nutrition (food fed to a station)
    for e in (items.get("simpleitem") or []):
        if isinstance(e, dict) and REFINED_RE.match(e.get("@uniquename", "")):
            targets.append(e)
    for e in (items.get("consumableitem") or []):
        un = e.get("@uniquename", "") if isinstance(e, dict) else ""
        if "_MEAL_" in un or "_POTION_" in un:
            targets.append(e)
            if "_MEAL_" in un and e.get("@nutrition") is not None:
                nutrition[market_key(un)] = int(float(e["@nutrition"]))

    recipes_payload = json.loads(RECIPES.read_text(encoding="utf-8"))
    recipes = recipes_payload["items"]
    batches = recipes_payload.setdefault("batch", {})    # units produced per craft, when not 1
    craftmeta_payload = json.loads(CRAFTMETA.read_text(encoding="utf-8"))
    craftmeta = craftmeta_payload["items"]
    cat2city = {k: v for k, v in json.loads(CITIES.read_text(encoding="utf-8")).items() if not k.startswith("_")}
    idx = index_iv_cc(dump)

    added_r, added_m = 0, 0
    added_keys, all_keys = [], []
    focus_by_key = {}                                   # market key -> base crafting focus of its node
    for e in targets:
        un = e["@uniquename"]
        key = market_key(un)
        r, batch = craft_resources(e)
        if not r:
            continue
        if key not in recipes:
            added_keys.append(key)
        all_keys.append(key)
        recipes[key] = r
        if batch != 1:
            batches[key] = batch
        elif key in batches:
            del batches[key]               # a patch can turn a batch recipe into a single one
        fc = node_focus(e)
        if fc is not None:
            focus_by_key[key] = fc
        added_r += 1

    # item values + bonus city + food nutrition, recomputed for every target key each run
    for key in all_keys:
        base = re.sub(r"@\d+$", "", key)
        iv = item_iv(key, recipes, idx, set())
        cc = idx.get(base, (None, None))[1]
        bc = cat2city.get(cc) if cc else None
        rec = {}
        if iv is not None:
            rec["iv"] = round(iv)
        if bc:
            rec["bc"] = bc
        if key in nutrition:
            rec["nu"] = nutrition[key]                  # food nutrition (station-owner tool)
        if key in focus_by_key:
            rec["fc"] = focus_by_key[key]               # base crafting focus (see build_craftmeta.py)
        craftmeta[key] = rec or None
        added_m += 1

    recipes_payload.setdefault("notes", {})["extra"] = "refined resources + food + potions merged by build_craft_extra.py (ingredient prices come from routes.json)"
    craftmeta_payload.setdefault("notes", {})["extra"] = "refined + food + potion iv/bc merged by build_craft_extra.py"
    RECIPES.write_text(json.dumps(recipes_payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    CRAFTMETA.write_text(json.dumps(craftmeta_payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")

    print(f"targets {len(targets)} | recipes written {added_r} (new keys {len(added_keys)}) | craftmeta {added_m}")
    print(f"recipes.json now {len(recipes)} items ({RECIPES.stat().st_size/1024:.0f} KB)")
    print("sample new keys:", added_keys[:8])
    ivn = sum(1 for k in added_keys if craftmeta.get(k) and "iv" in craftmeta[k])
    print(f"iv computed for {ivn}/{len(added_keys)} new keys")


if __name__ == "__main__":
    main()
