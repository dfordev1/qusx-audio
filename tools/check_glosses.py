#!/usr/bin/env python3
"""Audit a word-by-word gloss CSV for mechanical errors.

This finds problems that are decidable from the text alone -- markup residue, encoding
damage, stray characters, malformed parentheses, placeholder junk, suspicious lengths.

It cannot judge whether a translation is *correct*. Nothing here substitutes for review
by someone who reads the source language.

    python check_glosses.py data-en/source/wbw.csv --lang en
    python check_glosses.py data/source/wbw.csv --lang ur
"""

import argparse
import collections
import csv
import re
import sys
import unicodedata

ARABIC = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
LATIN = re.compile(r"[A-Za-z]")
TAGS = re.compile(r"<[^>]+>")
ENTITY = re.compile(r"&(?:[a-zA-Z]+|#\d+);")
CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Classic UTF-8-decoded-as-latin1 signatures.
MOJIBAKE = re.compile(r"[ÃÂ][-¿]|â€|Ã©|Ã¢|Ø")
REPEAT_WORD = re.compile(r"\b(\w+)\s+\1\b", re.IGNORECASE)
# "none" and "na" are excluded deliberately: "none" is an ordinary English gloss
# ("none despairs"), and flagging it produced 7 false positives on real data.
PLACEHOLDER = {"undefined", "null", "n/a", "todo", "tbd", "???", "-", "--"}

CHECKS = [
    "EMPTY", "MARKUP", "ENTITY", "CONTROL", "MOJIBAKE", "REPLACEMENT_CHAR",
    "UNBALANCED_PAREN", "EMPTY_PAREN", "PLACEHOLDER", "REPEATED_WORD",
    "WRONG_SCRIPT", "DIGITS", "NO_LETTERS", "VERY_LONG", "ODD_WHITESPACE",
    "EDGE_PUNCT", "NONPRINTING",
]


def check(gloss, lang):
    """Return a list of problem codes for one gloss."""
    p = []
    raw = gloss
    s = gloss.strip()

    if not s:
        p.append("EMPTY")
        return p

    if TAGS.search(s):
        p.append("MARKUP")
    if ENTITY.search(s):
        p.append("ENTITY")
    if CTRL.search(s):
        p.append("CONTROL")
    if MOJIBAKE.search(s):
        p.append("MOJIBAKE")
    if "�" in s:
        p.append("REPLACEMENT_CHAR")

    if s.count("(") != s.count(")"):
        p.append("UNBALANCED_PAREN")
    if re.search(r"\(\s*\)", s):
        p.append("EMPTY_PAREN")

    if s.casefold() in PLACEHOLDER:
        p.append("PLACEHOLDER")

    if lang == "en":
        if REPEAT_WORD.search(s):
            p.append("REPEATED_WORD")
        # An English gloss containing Arabic script is a data-merge error.
        if ARABIC.search(s):
            p.append("WRONG_SCRIPT")
        if not LATIN.search(s):
            p.append("NO_LETTERS")
    else:
        # Latin letters inside an Urdu gloss usually mean an untranslated fragment.
        if LATIN.search(s):
            p.append("WRONG_SCRIPT")
        if not ARABIC.search(s):
            p.append("NO_LETTERS")

    if re.search(r"\d", s):
        p.append("DIGITS")
    if len(s) > 80:
        p.append("VERY_LONG")
    if raw != s or "  " in raw or "\t" in raw or "\n" in raw:
        p.append("ODD_WHITESPACE")
    if s[0] in ",;:!?." or s[-1] in ",;:":
        p.append("EDGE_PUNCT")

    for ch in s:
        if unicodedata.category(ch) in ("Cf", "Co", "Cs"):
            p.append("NONPRINTING")
            break

    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="CSV with surah,ayah,word,gloss")
    ap.add_argument("--lang", choices=["en", "ur"], default="en")
    ap.add_argument("--report", default="", help="write full findings to this CSV")
    ap.add_argument("--show", type=int, default=6, help="examples printed per problem")
    args = ap.parse_args()

    with open(args.source, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    tally = collections.Counter()
    examples = collections.defaultdict(list)
    findings = []

    for r in rows:
        gloss = r.get("gloss") or ""
        probs = check(gloss, args.lang)
        if not probs:
            continue
        ref = f"{r['surah']}:{r['ayah']}:{r['word']}"
        for code in probs:
            tally[code] += 1
            if len(examples[code]) < args.show:
                examples[code].append((ref, gloss))
        findings.append((ref, gloss, ";".join(probs)))

    print(f"checked {len(rows):,} glosses from {args.source}")
    print(f"{len(findings):,} with at least one problem "
          f"({len(findings)/max(1,len(rows)):.2%})\n")

    if not tally:
        print("no mechanical problems found")
    else:
        width = max(len(c) for c in tally)
        for code, n in tally.most_common():
            print(f"{code:<{width}}  {n:>6,}")
            for ref, g in examples[code]:
                shown = g if len(g) <= 70 else g[:67] + "..."
                print(f"{'':<{width}}    {ref:<10} {shown}")
            print()

    if args.report:
        with open(args.report, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["ref", "gloss", "problems"])
            w.writerows(findings)
        print(f"full findings -> {args.report}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
