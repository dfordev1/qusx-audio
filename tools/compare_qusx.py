#!/usr/bin/env python3
"""Compare our word segmentation against the QUSX standard, surah by surah.

Why it matters: QUSX word ids are a single global sequence (1..N) that runs across the
whole mushaf and INCLUDES ayah-number tokens. If our word count differs anywhere, every
id after that point is wrong -- so a per-surah match is not enough, the ayah-level
counts have to line up too.

    python compare_qusx.py --layout madani-v2

Caches downloaded XML so re-runs are free.
"""

import argparse
import collections
import csv
import os
import re
import sys
import urllib.request

RAW = "https://raw.githubusercontent.com/dfordev1/usxv2/main/output/{layout}/{s:03d}.qusx.xml"
WORD_RE = re.compile(r"<word\b([^>]*)>", re.S)
ATTR_RE = re.compile(r'(\w+)="([^"]*)"')
AYAH_RE = re.compile(r'<ayah\b[^>]*number="(\d+)"[^>]*/>')


def fetch(layout, s, cache_dir):
    os.makedirs(cache_dir, exist_ok=True)
    p = os.path.join(cache_dir, f"{s:03d}.qusx.xml")
    if os.path.exists(p) and os.path.getsize(p) > 200:
        return open(p, encoding="utf-8").read()
    url = RAW.format(layout=layout, s=s)
    with urllib.request.urlopen(url, timeout=120) as r:
        text = r.read().decode("utf-8")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def parse(text):
    """Return (words_per_ayah, total_elements, number_tokens).

    Words are attributed to the most recent <ayah> milestone, which is how QUSX's
    flat-stream model expresses verse boundaries.
    """
    per_ayah = collections.Counter()
    total = numbers = 0
    current = None
    for m in re.finditer(r"<(ayah|word)\b([^>]*)>", text):
        tag, attrs = m.group(1), m.group(2)
        a = dict(ATTR_RE.findall(attrs))
        if tag == "ayah":
            current = int(a.get("number", 0))
        else:
            total += 1
            if a.get("type") == "number":
                numbers += 1
            else:
                per_ayah[current] += 1
    return per_ayah, total, numbers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data-en")
    ap.add_argument("--layout", default="madani-v2")
    ap.add_argument("--cache", default=".qusx-cache")
    ap.add_argument("--report", default="qusx_diff.csv")
    args = ap.parse_args()

    ours = collections.Counter()
    ours_ayah = collections.Counter()
    with open(os.path.join(args.data, "index.csv"), encoding="utf-8-sig",
              newline="") as fh:
        for r in csv.DictReader(fh):
            s, a = int(r["surah"]), int(r["ayah"])
            ours[s] += 1
            ours_ayah[(s, a)] += 1

    cache = os.path.join(args.cache, args.layout)
    rows, bad_surahs, bad_ayahs = [], [], []
    q_total = q_numbers = 0

    for s in range(1, 115):
        try:
            text = fetch(args.layout, s, cache)
        except Exception as e:
            print(f"  surah {s}: fetch failed ({e})")
            continue
        per_ayah, total, numbers = parse(text)
        q_words = sum(per_ayah.values())
        q_total += total
        q_numbers += numbers

        diff = q_words - ours[s]
        rows.append((s, ours[s], q_words, diff))
        if diff:
            bad_surahs.append((s, ours[s], q_words, diff))
            for a in sorted(set(per_ayah) | {k[1] for k in ours_ayah if k[0] == s}):
                d = per_ayah.get(a, 0) - ours_ayah.get((s, a), 0)
                if d:
                    bad_ayahs.append((s, a, ours_ayah.get((s, a), 0),
                                      per_ayah.get(a, 0), d))
        if s % 20 == 0:
            print(f"  ...{s}/114", flush=True)

    print(f"\nlayout           : {args.layout}")
    print(f"QUSX elements    : {q_total:,}  (words {q_total-q_numbers:,} "
          f"+ number tokens {q_numbers:,})")
    print(f"our words        : {sum(ours.values()):,}")
    print(f"surahs differing : {len(bad_surahs)} of 114")

    if bad_surahs:
        print(f"\n{'surah':>6} {'ours':>7} {'qusx':>7} {'diff':>6}")
        for s, o, q, d in bad_surahs:
            print(f"{s:>6} {o:>7,} {q:>7,} {d:>+6}")

    if bad_ayahs:
        print(f"\nayahs differing : {len(bad_ayahs)}")
        print(f"{'ref':>10} {'ours':>6} {'qusx':>6} {'diff':>6}")
        for s, a, o, q, d in bad_ayahs[:40]:
            print(f"{s:>4}:{a:<5} {o:>6} {q:>6} {d:>+6}")
        with open(args.report, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["surah", "ayah", "ours", "qusx", "diff"])
            w.writerows(bad_ayahs)
        print(f"\nfull list -> {args.report}")
    else:
        print("\nEVERY ayah matches - QUSX ids can be assigned directly.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
