#!/usr/bin/env python3
"""Check whether QUSX word ids are identical across all print layouts.

Layouts differ in page/line milestones, which should not affect the word stream. If
that holds, audio can be published against a single id space. If it does not, ids are
layout-specific and every consumer must know which layout an id came from -- worth
discovering before publishing, not after.

    python check_layouts.py
"""

import argparse
import hashlib
import os
import re
import sys
import urllib.request

RAW = "https://raw.githubusercontent.com/dfordev1/usxv2/main/output/{layout}/{s:03d}.qusx.xml"
ATTR = re.compile(r'(\w+)="([^"]*)"')

LAYOUTS = ["madani-v2", "madani-v1", "madani-v4-tajweed", "indopak-15", "indopak-16-taj",
           "indopak-13-taj", "indopak-13-qudratullah", "indopak-9-gaba", "nastaleeq",
           "qatar"]


def fetch(layout, s, cache):
    d = os.path.join(cache, layout)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{s:03d}.qusx.xml")
    if os.path.exists(p) and os.path.getsize(p) > 200:
        return open(p, encoding="utf-8").read()
    with urllib.request.urlopen(RAW.format(layout=layout, s=s), timeout=120) as r:
        text = r.read().decode("utf-8")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def word_signature(text):
    """(count, list of ids, hash of the id+position sequence).

    Deliberately ignores the Arabic text itself: layouts legitimately differ in script
    (indopak vs madani), so comparing glyphs would report false differences. What must
    match is the addressing.
    """
    ids, seq = [], []
    ayah = None
    n = 0
    for m in re.finditer(r"<(ayah|word)\b([^>]*)>", text):
        tag, attrs = m.group(1), m.group(2)
        a = dict(ATTR.findall(attrs))
        if tag == "ayah":
            ayah = int(a.get("number", 0))
            n = 0
            continue
        if a.get("type") == "number":
            continue
        n += 1
        wid = a.get("id", "")
        ids.append(wid)
        seq.append(f"{ayah}:{n}={wid}")
    h = hashlib.sha1("|".join(seq).encode("utf-8")).hexdigest()[:12]
    return len(ids), ids, h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=".qusx-cache")
    ap.add_argument("--surahs", default="1,2,9,18,55,78,114",
                    help="sample to compare; 'all' for every surah")
    args = ap.parse_args()

    if args.surahs == "all":
        surahs = list(range(1, 115))
    else:
        surahs = [int(x) for x in args.surahs.split(",")]

    print(f"comparing {len(LAYOUTS)} layouts over surahs {surahs}\n")

    base = LAYOUTS[0]
    mismatches = []

    for s in surahs:
        sigs = {}
        for layout in LAYOUTS:
            try:
                text = fetch(layout, s, args.cache)
            except Exception as e:
                print(f"  surah {s:>3} {layout:<24} fetch failed: {e}")
                continue
            sigs[layout] = word_signature(text)

        if base not in sigs:
            continue
        n0, _ids0, h0 = sigs[base]
        differing = [l for l, (n, _i, h) in sigs.items() if h != h0]
        status = "OK" if not differing else "DIFFERS"
        print(f"  surah {s:>3}  words={n0:<6} {status}")
        for l in differing:
            n, _i, h = sigs[l]
            print(f"        {l:<24} words={n:<6} hash={h} (base {h0})")
            mismatches.append((s, l, n0, n))

    print()
    if mismatches:
        print(f"{len(mismatches)} layout mismatches - ids are LAYOUT-SPECIFIC.")
        print("Audio must record which layout its ids came from.")
    else:
        print("All layouts share one word-id space.")
        print("Audio can be published against QUSX ids with no layout qualifier.")


if __name__ == "__main__":
    main()
