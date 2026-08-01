#!/usr/bin/env python3
"""
check_patch.py - rebuild the patch-only datasets when the game data actually changes.

recipes.json, routesmeta.json and craftmeta.json all mirror ao-bin-dumps items.json: they carry
recipe inputs, item weights, shop categories and item values, none of which move until Sandbox
ships a patch. So they were left out of the 4-hourly refresh, correctly - rebuilding them every
tick would burn a 16 MB download to rewrite the same bytes.

The hole was that NOTHING put them back in. They were rebuilt when someone remembered, and the
day a patch lands the app keeps serving the previous game's recipes and weights with no signal
at all - the failure looks like wrong crafting costs and missing items, not like stale data.
Found 01/08 while dating what build_public.py bundles: routesmeta was 70h old, which was FINE
(upstream's last commit was 21/07, ten days before), but nothing in the pipeline knew that, and
nothing would have known if it had been the other way round.

So: ask upstream for its fingerprint on every refresh - one HEAD request, no body - and rebuild
only when it moves. raw.githubusercontent serves an ETag that IS the sha256 of the file, which
makes the check exact rather than a date heuristic.

The stamp is committed, not local state: a fresh clone has to know which dump the published
datasets were built from, otherwise its first run rebuilds everything for nothing.

Rebuild order matters and is the one documented in scripts/README.md: recipes first (a patch adds
keys the others index by), then routesmeta, then craftmeta, then craft_extra which MERGES into
craftmeta.json and would have nothing to merge into if it ran first.

The stamp is written ONLY when every builder succeeded. A half-finished patch rebuild that
recorded the new fingerprint would be permanent: the next run would see "nothing to do" and the
datasets would stay half-patched forever.

Usage:
  python scripts/check_patch.py            # rebuild only if upstream moved
  python scripts/check_patch.py --force    # rebuild now, whatever the stamp says
  python scripts/check_patch.py --dry-run  # report only, touch nothing

Exit codes: 0 = up to date or rebuilt cleanly | 1 = a builder failed (stamp left untouched)
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAMP = ROOT / "scripts" / "data" / "aobin_stamp.json"
DUMP_URL = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/items.json"
UA = "albionsnipe-app/1.0 (patch watcher)"
# The dump is KEPT after a rebuild, not deleted: build_routesmeta.py runs on every refresh (see
# below) and re-downloading 16 MB every 4h to read the same bytes would be the exact waste this
# script exists to avoid. Local only, gitignored.
DUMP_CACHE = ROOT / "scripts" / "data" / "_aobin_items.json"
# (script, extra args) in dependency order - see the module docstring.
# build_routesmeta.py is deliberately NOT here. Its own header claimed "rebuild only on a game
# patch" and that is wrong: its universe is baseline.json gear + materials.json materials, both
# rebuilt twice a day off the live market. Measured 01/08 - rebuilding it against an unchanged
# dump still moved 9 ids in and 9 out (crystal artefacts and a Brecilien cape blueprint drifting
# in and out of having a Black Market price). Left on the patch cadence, the Routes universe is a
# photograph of the market on the day someone last thought to rebuild it: items traded today are
# missing, ids long gone are still priced. It belongs in the 4-hourly cycle, right after
# materials.json, and refresh-data.bat runs it there off this cache.
BUILDERS = [
    ("build_recipes.py", []),
    ("build_craftmeta.py", []),
    ("build_craft_extra.py", []),
]
OUTPUTS = ["recipes.json", "craftmeta.json"]


def upstream_fingerprint():
    """ETag of the upstream dump. raw.githubusercontent sets it to the file's sha256, so an equal
    ETag means byte-identical content - no date guessing, no 16 MB download."""
    req = urllib.request.Request(DUMP_URL, method="HEAD", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        etag = (r.headers.get("ETag") or "").strip('"W/ ')
        size = int(r.headers.get("Content-Length") or 0)
    if not etag:
        raise RuntimeError("upstream sent no ETag - cannot tell a patch from a no-op")
    return etag, size


def read_stamp():
    try:
        return json.loads(STAMP.read_text(encoding="utf-8"))
    except Exception:
        return {}


def fetch_dump(dest):
    """One download for every builder. Each of them would otherwise pull the same 16 MB itself."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(DUMP_URL, headers={"User-Agent": UA})
    tmp = dest.with_suffix(".part")
    with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as f:
        f.write(r.read())
    tmp.replace(dest)          # never leave a truncated dump behind for the next builder to read
    return dest.stat().st_size


def unnamed_ids():
    """Ids the app can price but cannot NAME. items.json (localised names) has no builder in this
    repo - it was produced by hand once, and its 6053 enchant variants are derived, not upstream.
    So a patch that adds items leaves them nameless and the app falls back to the raw id. Not
    fixed here (reproducing that derivation is its own job); made VISIBLE, with a number, so it
    stops being a silent drift."""
    try:
        names = json.loads((ROOT / "docs" / "data" / "items.json").read_text(encoding="utf-8"))
        ids = json.loads((ROOT / "docs" / "data" / "routesmeta.json").read_text(encoding="utf-8"))["ids"]
    except Exception:
        return None, None
    return sum(1 for i in ids if i not in names), len(ids)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="rebuild whatever the stamp says")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    try:
        etag, size = upstream_fingerprint()
    except Exception as e:
        print(f"  patch check: upstream unreachable ({e}); leaving the datasets alone")
        return 0                                   # offline is not a patch - never a build failure

    old = read_stamp()
    if old.get("etag") == etag and not args.force:
        # No patch, but the cached dump still has to be there: build_routesmeta.py reads it on
        # every refresh. Absent (fresh clone, cleaned disk) means fetching it once, not skipping.
        if not DUMP_CACHE.exists() and not args.dry_run:
            try:
                mb = fetch_dump(DUMP_CACHE) / 1048576
                print(f"  patch check: no patch, but the dump cache was missing - fetched ({mb:.1f} MB)")
            except Exception as e:
                print(f"  patch check: no patch; dump cache missing and unreachable ({e})")
        miss, total = unnamed_ids()
        extra = f" | {miss}/{total} ids without a name" if miss else ""
        print(f"  patch check: game data unchanged since {old.get('checked_at', '?')}{extra}")
        return 0

    why = "forced" if args.force else ("first run" if not old else "GAME PATCH detected")
    print(f"  patch check: {why} - upstream dump {size/1048576:.1f} MB, etag {etag[:12]}")
    if args.dry_run:
        print("  dry run: nothing rebuilt")
        return 0

    try:
        mb = fetch_dump(DUMP_CACHE) / 1048576
        print(f"  downloaded the dump once ({mb:.1f} MB), shared by every builder and kept for "
              f"the 4-hourly routesmeta rebuild", flush=True)
    except Exception as e:
        print(f"*** patch rebuild ABORTED: cannot download the dump ({e})")
        return 1

    for script, extra in BUILDERS:
        print(f"  -> {script}", flush=True)
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / script), "--dump", str(DUMP_CACHE)] + extra,
                           cwd=str(ROOT))
        if r.returncode != 0:
            print(f"*** {script} FAILED (exit {r.returncode}) - stamp NOT written, "
                  f"the next refresh will retry the whole patch rebuild")
            return 1

    STAMP.write_text(json.dumps({
        "etag": etag,
        "size": size,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": DUMP_URL,
        "rebuilt": OUTPUTS,
        "note": "fingerprint of the ao-bin-dumps items.json the published patch-only datasets were "
                "built from. Written only after every builder succeeded, so a partial rebuild is "
                "retried instead of being recorded as done.",
    }, indent=1), encoding="utf-8")

    miss, total = unnamed_ids()
    if miss:
        print(f"  NOTE: {miss}/{total} ids have no localised name in items.json - the app will show "
              f"their raw id. items.json has no builder here; regenerate it if a patch added tradables.")
    print(f"  patch rebuild OK: {', '.join(OUTPUTS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
