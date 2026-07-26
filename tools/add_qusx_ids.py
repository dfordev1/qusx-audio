#!/usr/bin/env python3
"""Add QUSX global word ids to the packed surah maps.

QUSX identifies every word by a single global position that runs across the whole
mushaf and includes ayah-number tokens. Our maps are keyed "surah:ayah:word". Carrying
both means a player can address audio either way -- by scripture reference, or straight
from a QUSX <word id="...">.

Run after pack_surahs.py. Reads the QUSX XML cached by compare_qusx.py.

    python add_qusx_ids.py --dist dist-en --layout madani-v2

Adds to each map file:
    "byId":   {"3474": [start, dur], ...}
    "qusxId": {"2:181:3": 3474, ...}
"""

import argparse
import json
import os
import re
import sys

ATTR = re.compile(r'(\w+)="([^"]*)"')


def qusx_ids(cache_dir, surah):
    """(surah, ayah, word) -> global QUSX id, for real words only."""
    path = os.path.join(cache_dir, f"{surah:03d}.qusx.xml")
    if not os.path.exists(path):
        return None
    text = open(path, encoding="utf-8").read()
    out = {}
    ayah = None
    n = 0
    for m in re.finditer(r"<(ayah|word)\b([^>]*)>", text):
        tag, attrs = m.group(1), m.group(2)
        a = dict(ATTR.findall(attrs))
        if tag == "ayah":
            ayah = int(a.get("number", 0))
            n = 0
            continue
        # Number tokens occupy an id but are not words; skip them for addressing while
        # leaving the id sequence itself untouched.
        if a.get("type") == "number":
            continue
        n += 1
        wid = a.get("id")
        if wid and ayah:
            out[(surah, ayah, n)] = int(wid)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default="dist-en")
    ap.add_argument("--layout", default="madani-v2")
    ap.add_argument("--cache", default=".qusx-cache")
    args = ap.parse_args()

    cache_dir = os.path.join(args.cache, args.layout)
    map_dir = os.path.join(args.dist, "map")
    if not os.path.isdir(map_dir):
        sys.exit(f"no maps in {map_dir} - run pack_surahs.py first")
    if not os.path.isdir(cache_dir):
        sys.exit(f"no QUSX cache in {cache_dir} - run compare_qusx.py first")

    files = sorted(f for f in os.listdir(map_dir) if f.endswith(".json"))
    done = skipped = missing = 0

    for name in files:
        surah = int(name[:3])
        ids = qusx_ids(cache_dir, surah)
        if not ids:
            skipped += 1
            continue
        path = os.path.join(map_dir, name)
        data = json.load(open(path, encoding="utf-8"))
        words = data.get("words", {})

        by_id, qusx_of = {}, {}
        for key, span in words.items():
            s, a, w = (int(x) for x in key.split(":"))
            wid = ids.get((s, a, w))
            if wid is None:
                missing += 1
                continue
            by_id[str(wid)] = span
            qusx_of[key] = wid

        data["byId"] = by_id
        data["qusxId"] = qusx_of
        data["qusxLayout"] = args.layout
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, separators=(",", ":"))
        done += 1

    print(f"maps updated : {done}")
    if skipped:
        print(f"skipped      : {skipped} (no QUSX file cached)")
    if missing:
        print(f"unmatched    : {missing} positions had no QUSX id")

    idx_path = os.path.join(args.dist, "index.json")
    if os.path.exists(idx_path):
        idx = json.load(open(idx_path, encoding="utf-8"))
        idx["qusxLayout"] = args.layout
        idx["addressing"] = ["surah:ayah:word", "qusx-global-id"]
        with open(idx_path, "w", encoding="utf-8") as fh:
            json.dump(idx, fh, separators=(",", ":"))
        print("index.json annotated")


if __name__ == "__main__":
    main()
