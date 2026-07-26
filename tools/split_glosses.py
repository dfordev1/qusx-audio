#!/usr/bin/env python3
"""Split merged glosses so our segmentation matches QUSX word-for-word.

QUL treats "بعد ما" as one word; QUSX splits it into بَعْدَ + مَا. That is a legitimate
difference between two projects, but it means positions drift after the merge point,
so audio keyed to our positions cannot be played against QUSX text. Splitting the three
occurrences brings us to QUSX's 77,432 and lets word ids map 1:1.

Edits are declared in SPLITS, applied to the source CSV, and every later word in the
affected ayah is renumbered.

    python split_glosses.py --data data-en --apply

Without --apply it prints what it would do and changes nothing.
"""

import argparse
import csv
import os
import shutil
import sys

# (surah, ayah, word, expected_gloss, [replacement glosses in order])
SPLITS = [
    (2, 181, 3, "after what", ["after", "what"]),
    (8, 6, 4, "after what", ["after", "what"]),
    (13, 37, 8, "after what", ["after", "what"]),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data-en")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    src = os.path.join(args.data, "source", "wbw.csv")
    if not os.path.exists(src):
        sys.exit(f"missing {src}")

    with open(src, encoding="utf-8-sig", newline="") as fh:
        rows = [dict(r) for r in csv.DictReader(fh)]

    by_pos = {(int(r["surah"]), int(r["ayah"]), int(r["word"])): r for r in rows}

    # Verify every target before touching anything: a half-applied edit would silently
    # corrupt word numbering for the rest of the ayah.
    problems = []
    for s, a, w, expect, parts in SPLITS:
        row = by_pos.get((s, a, w))
        if row is None:
            problems.append(f"{s}:{a}:{w} not found")
        elif row["gloss"].strip() != expect:
            problems.append(f"{s}:{a}:{w} is {row['gloss']!r}, expected {expect!r}")
    if problems:
        for p in problems:
            print("  !", p)
        sys.exit("aborting - source does not match expectations (already applied?)")

    print(f"{len(SPLITS)} splits to apply:")
    for s, a, w, expect, parts in SPLITS:
        print(f"  {s}:{a}:{w}  {expect!r}  ->  " + " + ".join(repr(p) for p in parts))

    if not args.apply:
        print("\ndry run - nothing written. Re-run with --apply")
        return

    targets = {(s, a, w): parts for s, a, w, _e, parts in SPLITS}
    affected = {(s, a) for s, a, _w, _e, _p in SPLITS}

    out = []
    for r in rows:
        s, a, w = int(r["surah"]), int(r["ayah"]), int(r["word"])
        if (s, a) not in affected:
            out.append(r)
            continue
        # Rebuilt per ayah below; skip here.
        out.append(r)

    # Rebuild each affected ayah from scratch so numbering is contiguous.
    rebuilt = []
    for r in out:
        s, a = int(r["surah"]), int(r["ayah"])
        if (s, a) in affected:
            continue
        rebuilt.append(r)

    for (s, a) in sorted(affected):
        ayah_rows = sorted([r for r in rows
                            if int(r["surah"]) == s and int(r["ayah"]) == a],
                           key=lambda r: int(r["word"]))
        n = 0
        for r in ayah_rows:
            w = int(r["word"])
            parts = targets.get((s, a, w))
            for gloss in (parts if parts else [r["gloss"]]):
                n += 1
                rebuilt.append({"surah": str(s), "ayah": str(a), "word": str(n),
                                "gloss": gloss})
        print(f"  {s}:{a}  {len(ayah_rows)} -> {n} words")

    rebuilt.sort(key=lambda r: (int(r["surah"]), int(r["ayah"]), int(r["word"])))

    backup = src + ".bak"
    if not os.path.exists(backup):
        shutil.copy2(src, backup)
        print(f"\nbackup -> {backup}")

    with open(src, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["surah", "ayah", "word", "gloss"])
        w.writeheader()
        w.writerows(rebuilt)

    print(f"wrote {len(rebuilt):,} rows -> {src}  (was {len(rows):,})")
    print("\nnow re-run make_wordlist.py to rebuild wordlist.csv and index.csv")


if __name__ == "__main__":
    main()
