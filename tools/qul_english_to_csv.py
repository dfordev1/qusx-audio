#!/usr/bin/env python3
"""Convert QUL's *colored* English word-by-word export into the standard CSV.

Why the coloured export rather than the plain one: the coloured file uses the same
77,429-word segmentation as the Urdu set (identical key sets), so English and Urdu
clips line up position-for-position. The plain English export uses QUL's 83,665-word
split and would not align. The cost is that every gloss is wrapped in <span> markup,
which this script strips.

    python qul_english_to_csv.py "C:/Users/Dv/Downloads/colored-english-wbw-translation.json.zip" \
        -o data-en/source/wbw.csv
"""

import argparse
import csv
import html
import json
import os
import re
import zipfile

TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")


def clean(raw):
    """Strip markup and normalise whitespace.

    Parentheses are kept. In this translation they mark words implied by the Arabic
    rather than present in it -- "(of) Allah". Dropping them would change the meaning
    of the gloss, and TTS engines treat them as ordinary prosodic grouping, so they
    cost nothing when spoken.
    """
    text = TAG.sub(" ", raw or "")
    text = html.unescape(text)
    return WS.sub(" ", text).strip()


def load(path):
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".json")]
            if not names:
                raise SystemExit(f"no .json inside {path}")
            return json.loads(z.read(names[0]).decode("utf-8"))
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="colored-english-wbw-translation.json(.zip)")
    ap.add_argument("-o", "--out", default="data-en/source/wbw.csv")
    args = ap.parse_args()

    data = load(args.source)
    rows, skipped, malformed = [], 0, 0

    for key, raw in data.items():
        parts = key.split(":")
        if len(parts) != 3:
            malformed += 1
            continue
        try:
            surah, ayah, word = (int(p) for p in parts)
        except ValueError:
            malformed += 1
            continue
        gloss = clean(raw)
        if not gloss:
            skipped += 1
            continue
        rows.append((surah, ayah, word, gloss))

    rows.sort()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["surah", "ayah", "word", "gloss"])
        w.writerows(rows)

    print(f"wrote {len(rows):,} rows -> {args.out}")
    if skipped:
        print(f"  skipped {skipped:,} empty glosses")
    if malformed:
        print(f"  skipped {malformed:,} malformed keys")


if __name__ == "__main__":
    main()
