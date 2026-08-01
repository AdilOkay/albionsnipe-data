#!/usr/bin/env python3
"""
build_craftmeta.py - AlbionSnipe craft metadata (item value + bonus city)

Emits docs/data/craftmeta.json: for every baseline gear key,
  - iv : Item Value = sum of the materials' @itemvalue (ao-bin-dumps) x count.
         Drives the exact station fee: nutrition = iv x 0.1125,
         fee = nutrition x (station fee per 100 nutrition) / 100.
         null when a material has no @itemvalue (never a guess).
  - bc : bonus city for the item's @craftingcategory (+15% production bonus
         when crafted there), from scripts/data/bonus_cities.json.
         null when the category has no city specialty (royal/faction sets...).
  - fc : base crafting focus cost (@craftingfocus). Mirrors the recipe mapping
         of build_recipes.py: a gear market key @N reads the enchantment-N
         node's own craftingrequirements (each enchant level has its own focus,
         e.g. T4_2H_CLAYMORE 1715/3001/5252/9191/16085 for @0..@4); an
         enchanted resource key (T4_METALBAR_LEVEL1@1) reads its LEVELn
         uniquename directly. Missing = the node has no @craftingfocus; the
         app shows focus n/a (never a guess).
  - jb : crafting-journal branch (W/M/H/T = Warrior/Mage/Hunter/Toolmaker forge), read
         from the journalitem famefillingmissions validitem lists - the game's own
         explicit item->journal mapping (1440 ids, none ambiguous). Missing = the item
         fills no journal (royal/faction gear, specialty capes): books/fame n/a.
  - ff : @destinyandjournalcraftfamefactor (1.1-1.4, artefact/avalonian lines only).
         Multiplies the craft fame counted for the Destiny Board AND journals.
         Missing = factor 1 (standard items carry no attribute).
  - ur : [resource id, count] to ENCHANT this key up from the level below, read
         from the enchantment node's <upgraderequirements>. This is a different
         path from craftingrequirements: crafting an @N item eats enchanted
         BARS, upgrading eats runes/souls/relics and nothing else - no silver,
         no focus (verified: across 4455 upgrade nodes the tag never carries
         @silver, @craftingfocus or @time). Enchanting preserves the item's
         quality. Only @1..@3 have it: x.3 -> x.4 has no upgrade path in the
         data (x.4 is craft-only), so an @4 key has no ur - not a gap, a fact.

Game-patch data (like recipes.json): rebuild only on game patches.

Usage:
  python scripts/build_craftmeta.py                      # downloads ao-bin-dumps items.json
  python scripts/build_craftmeta.py --dump items.json    # reuse a local dump (17 MB)
"""
import argparse, json, re, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "docs" / "data" / "baseline.json"
RECIPES = ROOT / "docs" / "data" / "recipes.json"
CITIES = ROOT / "scripts" / "data" / "bonus_cities.json"
OUT = ROOT / "docs" / "data" / "craftmeta.json"
AOBIN_URL = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/items.json"


def load_dump(path):
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    print(f"downloading {AOBIN_URL} ...")
    with urllib.request.urlopen(AOBIN_URL, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def node_focus(node):
    """@craftingfocus of a node's own craftingrequirements (first block when a list)."""
    cr = node.get("craftingrequirements")
    if isinstance(cr, list):
        cr = cr[0] if cr else None
    if not isinstance(cr, dict):
        return None
    f = cr.get("@craftingfocus")
    return int(float(f)) if f is not None else None


def node_upgrade(node):
    """[resource id, count] of a node's <upgraderequirements> - the enchant path, not the
    craft path. Single resource in every case seen; a list would be a game change, so read
    the first and let the count reveal it rather than guessing a sum."""
    ur = node.get("upgraderequirements")
    if not isinstance(ur, dict):
        return None
    r = ur.get("upgraderesource")
    if isinstance(r, list):
        r = r[0] if r else None
    if not isinstance(r, dict) or not r.get("@uniquename") or r.get("@count") is None:
        return None
    return [r["@uniquename"], int(float(r["@count"]))]


def journal_map(dump):
    """base uniquename -> branch letter, from the journalitem validitem lists (the game's
    own mapping; a tier's journal only lists that tier's items, so the full base id is
    the key). Only the four crafting branches; gathering/mercenary journals have no
    craftitemfame block and are skipped."""
    frag2letter = {"WARRIOR": "W", "MAGE": "M", "HUNTER": "H", "TOOLMAKER": "T"}
    out = {}
    group = dump["items"].get("journalitem") or []
    if not isinstance(group, list):
        group = [group]
    for j in group:
        if not isinstance(j, dict) or "@uniquename" not in j:
            continue
        m = re.match(r"^T\d+_JOURNAL_([A-Z]+)$", j["@uniquename"])
        if not m or m.group(1) not in frag2letter:
            continue
        cif = (j.get("famefillingmissions") or {}).get("craftitemfame")
        if not isinstance(cif, dict):
            continue
        vi = cif.get("validitem") or []
        if isinstance(vi, dict):
            vi = [vi]
        for v in vi:
            if isinstance(v, dict) and v.get("@id"):
                out[v["@id"]] = frag2letter[m.group(1)]
    return out


def index_dump(dump):
    """uniquename -> (@itemvalue, @craftingcategory, base focus, {enchant level -> focus})."""
    idx = {}
    for group in dump["items"].values():
        if not isinstance(group, list):
            group = [group]
        for e in group:
            if not isinstance(e, dict) or "@uniquename" not in e:
                continue
            iv = e.get("@itemvalue")
            fce, ure = {}, {}                             # per-enchant focus + upgrade recipe
            ench = e.get("enchantments", {}).get("enchantment") if isinstance(e.get("enchantments"), dict) else None
            for en in (ench if isinstance(ench, list) else [ench] if ench else []):
                lvl = en.get("@enchantmentlevel")
                if lvl is None:
                    continue
                lvl = int(lvl)
                lf = node_focus(en)
                if lf is not None:
                    fce[lvl] = lf
                lu = node_upgrade(en)
                if lu is not None:
                    ure[lvl] = lu
            ff = e.get("@destinyandjournalcraftfamefactor")
            idx[e["@uniquename"]] = (float(iv) if iv is not None else None, e.get("@craftingcategory"),
                                     node_focus(e), fce, ure, float(ff) if ff is not None else None)
    return idx


def item_iv(key, recipes, idx, missing, seen, depth=0):
    """Item Value of a crafted key = sum of material IVs x count. A material with no
    @itemvalue that is itself craftable (nested recipe: specialty capes eat a crafted
    T4_CAPE, faction sets eat a crafted SET1 piece) resolves recursively. None when a
    leaf material has no @itemvalue - never a guess."""
    if depth > 4 or key in seen:
        return None
    seen = seen | {key}
    r = recipes.get(key)
    if not r:
        return None
    total = 0
    for row in r:                        # [mat, count] or [mat, count, 0] when the
        mat, count = row[0], row[1]      # material is never refunded (see build_recipes.py)
        mat_un = re.sub(r"@\d+$", "", mat)               # T4_METALBAR_LEVEL1@1 -> T4_METALBAR_LEVEL1
        mat_iv = idx.get(mat_un, (None,) * 6)[0]
        if mat_iv is None:
            mat_iv = item_iv(mat, recipes, idx, missing, seen, depth + 1)   # nested craftable
        if mat_iv is None:
            missing[mat_un] = missing.get(mat_un, 0) + 1
            return None
        total += mat_iv * count
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", help="local ao-bin-dumps items.json instead of downloading")
    args = ap.parse_args()

    keys = list(json.loads(BASELINE.read_text(encoding="utf-8"))["items"].keys())
    recipes = json.loads(RECIPES.read_text(encoding="utf-8"))["items"]
    cat2city = {k: v for k, v in json.loads(CITIES.read_text(encoding="utf-8")).items() if not k.startswith("_")}
    dump = load_dump(args.dump)
    idx = index_dump(dump)
    jmap = journal_map(dump)
    print(f"{len(keys)} baseline keys | {len(idx)} dump uniquenames | {len(cat2city)} bonus categories | {len(jmap)} journal-mapped ids")

    items, iv_ok, iv_null, bc_ok, fc_ok, ur_ok, jb_ok, ff_ok = {}, 0, 0, 0, 0, 0, 0, 0
    missing_mats = {}
    for key in keys:
        base_un = re.sub(r"@\d+$", "", key)              # dump uniquename = market id minus @N
        m = re.search(r"@(\d+)$", key)
        ench_n = int(m.group(1)) if m else 0
        _, cc, fc_base, fce, ure, ff = idx.get(base_un, (None, None, None, {}, {}, None))
        bc = cat2city.get(cc) if cc else None
        # ur belongs to the enchant level itself: @N's ur is what it costs to reach @N from @N-1.
        # An unenchanted key has nothing to upgrade from, so no ur - and @4 has none either.
        ur = ure.get(ench_n) if ench_n else None
        # market key @N = the enchantment-N node's own recipe and focus (same mapping as
        # build_recipes.py); enchanted resources (LEVELn@N) keep their base node's focus.
        if ench_n == 0:
            fc = fc_base
        elif fce:                                        # gear: each enchant level is its own recipe
            fc = fce.get(ench_n)                         # None when that level has no direct recipe
        else:                                            # enchanted resource: LEVELn node holds it
            fc = fc_base

        iv = item_iv(key, recipes, idx, missing_mats, set())
        if iv is not None:
            iv = round(iv)
            iv_ok += 1
        else:
            iv_null += 1
        if bc:
            bc_ok += 1
        rec = {}
        if iv is not None:
            rec["iv"] = iv
        if bc:
            rec["bc"] = bc
        if fc is not None:
            rec["fc"] = fc
            fc_ok += 1
        if ur is not None:
            rec["ur"] = ur
            ur_ok += 1
        jb = jmap.get(base_un)
        if jb:
            rec["jb"] = jb
            jb_ok += 1
        if ff is not None:
            rec["ff"] = ff
            ff_ok += 1
        items[key] = rec or None

    payload = {
        "notes": {
            "iv": "Item Value = sum of material @itemvalue x count (ao-bin-dumps). Station fee = iv x 0.1125 x (fee per 100 nutrition) / 100. Missing = a material has no @itemvalue; app shows fee n/a.",
            "bc": "city whose +15% crafting specialty covers this item's @craftingcategory. Missing = no specialty (royal/faction/crystal-league sets).",
            "fc": "base crafting focus (@craftingfocus) of THIS market key's own recipe (gear @N = the enchantment-N node, each level has its own focus). Missing = no @craftingfocus on the node; app shows focus n/a.",
            "ur": "[resource id, count] to ENCHANT up to this key from the level below (<upgraderequirements>): runes/souls/relics only, no silver, no focus, quality preserved. Only on @1..@3 - x.3 to x.4 has no upgrade path, x.4 is craft-only.",
            "jb": "crafting-journal branch (W/M/H/T) from the dump's validitem lists. Missing = fills no journal (royal/faction gear, specialty capes): app shows books/fame n/a.",
            "ff": "@destinyandjournalcraftfamefactor - craft fame multiplier (Destiny Board AND journals), artefact/avalonian lines only. Missing = 1.",
            "source": "ao-bin-dumps + scripts/data/bonus_cities.json; rebuild on game patch only",
        },
        "items": items,
    }
    OUT.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT}  ({OUT.stat().st_size/1024:.0f} KB)")
    print(f"iv computed {iv_ok}/{len(keys)} | iv null {iv_null} | bonus city {bc_ok}/{len(keys)} | focus {fc_ok}/{len(keys)} | upgrade recipe {ur_ok}/{len(keys)} | journal branch {jb_ok}/{len(keys)} | fame factor {ff_ok}/{len(keys)}")
    if missing_mats:
        top = sorted(missing_mats.items(), key=lambda x: -x[1])[:10]
        print("materials without @itemvalue (item iv -> null):", top)


if __name__ == "__main__":
    sys.exit(main())
