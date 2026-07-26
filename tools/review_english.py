#!/usr/bin/env python3
"""Review the English gloss text for problems, before any of it is spoken.

Two classes of problem exist and they need different treatment:

  * damage WE introduced while converting QUL's markup -- e.g. "disbelieve [d]"
    became "disbelieve d", which is then read aloud as "disbelieve dee";
  * oddities already present in the QUL source, which we should report but not
    silently rewrite.

Comparing the processed CSV against the original export separates the two.

    python review_english.py --raw <colored-english-wbw.zip> --data data-en
"""

import argparse
import collections
import csv
import json
import os
import re
import sys
import zipfile

TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")

# English inflectional endings. Bracketed, these belong to the preceding word;
# anything else in brackets is a word in its own right.
SUFFIXES = {"s", "es", "d", "ed", "ing", "n", "en", "ly", "er", "est", "ies"}
SUFFIX_BRACKET = re.compile(r"([A-Za-z]+)\s*[\[(]\s*([A-Za-z]{1,3})\s*[\])]")


def clean(raw):
    return WS.sub(" ", TAG.sub(" ", raw or "")).strip()


def load_raw(path):
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            name = [n for n in z.namelist() if n.lower().endswith(".json")][0]
            return json.loads(z.read(name).decode("utf-8"))
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--data", default="data-en")
    ap.add_argument("--report", default="english_review.csv")
    args = ap.parse_args()

    raw = load_raw(args.raw)
    src = os.path.join(args.data, "source", "wbw.csv")
    rows = list(csv.DictReader(open(src, encoding="utf-8-sig", newline="")))

    findings = collections.defaultdict(list)

    for r in rows:
        ref = f"{r['surah']}:{r['ayah']}:{r['word']}"
        g = r["gloss"]

        # 1. a suffix separated from its stem -- the "disbelieve d" family
        for m in SUFFIX_BRACKET.finditer(g):
            if m.group(2).lower() in SUFFIXES:
                findings["SUFFIX_SPLIT"].append((ref, g))
                break

        # 2. spacing introduced by tag stripping: "[ d ]" rather than "[d]"
        if re.search(r"[\[(]\s+|\s+[\])]", g):
            findings["BRACKET_SPACING"].append((ref, g))

        # 3. nothing left but punctuation
        if not re.search(r"[A-Za-z]", g):
            findings["NO_LETTERS"].append((ref, g))

        # 4. a bracket that never closes
        if g.count("[") != g.count("]") or g.count("(") != g.count(")"):
            findings["UNBALANCED"].append((ref, g))

        # 5. duplicated word -- "the the"
        if re.search(r"\b(\w+)\s+\1\b", g, re.I):
            findings["REPEATED_WORD"].append((ref, g))

        # 6. stray single letters that are not real English words
        for tok in g.split():
            t = tok.strip("[]().,").lower()
            if len(t) == 1 and t.isalpha() and t not in {"a", "i", "o"}:
                findings["LONE_LETTER"].append((ref, g))
                break

        # 7. our text differs from the source in more than whitespace/markup
        key = f"{r['surah']}:{r['ayah']}:{r['word']}"
        if key in raw:
            want = clean(raw[key])
            if want.replace(" ", "") != g.replace(" ", ""):
                findings["DIFFERS_FROM_SOURCE"].append((ref, f"{g!r} vs source {want!r}"))

    order = ["SUFFIX_SPLIT", "BRACKET_SPACING", "LONE_LETTER", "NO_LETTERS",
             "UNBALANCED", "REPEATED_WORD", "DIFFERS_FROM_SOURCE"]

    print(f"reviewed {len(rows):,} glosses\n")
    total = 0
    for code in order:
        hits = findings.get(code, [])
        total += len(hits)
        print(f"{code:<22} {len(hits):>6,}")
        for ref, g in hits[:4]:
            print(f"{'':<22}   {ref:<12} {g}")
        if hits:
            print()

    uniq = {g for hits in findings.values() for _r, g in hits}
    print(f"total findings   : {total:,}")
    print(f"distinct glosses : {len(uniq):,}")

    with open(args.report, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "ref", "gloss"])
        for code in order:
            for ref, g in findings.get(code, []):
                w.writerow([code, ref, g])
    print(f"\nfull report -> {args.report}")


if __name__ == "__main__":
    sys.exit(main())
