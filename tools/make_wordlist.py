#!/usr/bin/env python3
"""Step 1: deduplicate the Urdu word-by-word glosses.

Input:  a CSV/TSV with columns surah,ayah,word,gloss  (however your QUSX export lands)
Output: wordlist.csv  -> gloss_id, gloss, occurrences
        index.csv     -> surah, ayah, word, gloss_id

The gloss_id is a short stable hash of the NFC-normalised gloss, so re-running
after adding data never renumbers anything you have already recorded.
"""

import argparse
import csv
import hashlib
import unicodedata
from collections import Counter, OrderedDict

# Urdu diacritics/tatweel that are cosmetic for a spoken gloss. Strip before
# hashing so "کہا" and "کَہا" collapse to one recording.
STRIP = set("ًٌٍَُِّْٰٕٓٔـ")


BRACKETS = "()[]"


def normalise(gloss: str, casefold: bool = False, strip_brackets: bool = False) -> str:
    s = unicodedata.normalize("NFC", gloss).strip()
    s = "".join(ch for ch in s if ch not in STRIP)
    # "from", "(from)" and "[ from ]" are one sound. The brackets mark words implied by
    # the Arabic -- real information in print, silent when spoken. Folding them together
    # for the audio key avoids paying for the same clip three times and guarantees the
    # word sounds identical everywhere it appears. The display form keeps its brackets.
    if strip_brackets:
        s = "".join(" " if ch in BRACKETS else ch for ch in s)
    s = " ".join(s.split())
    # For English, "The Most Gracious" and "the Most Gracious" are the same recording;
    # collapsing them avoids paying to synthesise the same audio twice. Urdu has no
    # case, so this is a no-op there.
    return s.casefold() if casefold else s


def gloss_id(norm: str) -> str:
    return "g" + hashlib.sha1(norm.encode("utf-8")).hexdigest()[:10]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="CSV/TSV with surah,ayah,word,gloss columns")
    ap.add_argument("--delimiter", default=",")
    ap.add_argument("--wordlist", default="wordlist.csv")
    ap.add_argument("--index", default="index.csv")
    ap.add_argument("--casefold", action="store_true",
                    help="treat case-variant glosses as one (use for English)")
    ap.add_argument("--strip-brackets", action="store_true",
                    help="fold '(of)' / '[ of ]' / 'of' into one audio key")
    args = ap.parse_args()

    counts: Counter = Counter()
    forms: "OrderedDict[str, str]" = OrderedDict()  # id -> display form (first seen)
    rows = []

    with open(args.source, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=args.delimiter):
            raw = (row.get("gloss") or "").strip()
            if not raw:
                continue
            norm = normalise(raw, args.casefold, args.strip_brackets)
            gid = gloss_id(norm)
            counts[gid] += 1
            # The stored form is what gets spoken by TTS and shown on the teleprompter,
            # so when brackets are folded away it must be the bracket-free text --
            # otherwise a clip could be synthesised from the literal string "[ from ]".
            display = raw
            if args.strip_brackets:
                display = " ".join(
                    "".join(" " if ch in BRACKETS else ch for ch in raw).split()
                )
            forms.setdefault(gid, display or raw)
            rows.append((row["surah"], row["ayah"], row["word"], gid))

    # Most frequent first: record these and you cover the most of the text early.
    ordered = counts.most_common()

    with open(args.wordlist, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["gloss_id", "gloss", "occurrences"])
        for gid, n in ordered:
            w.writerow([gid, forms[gid], n])

    with open(args.index, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["surah", "ayah", "word", "gloss_id"])
        w.writerows(rows)

    total = sum(counts.values())
    uniq = len(counts)
    top100 = sum(n for _, n in ordered[:100])
    print(f"{total:,} tokens -> {uniq:,} unique glosses ({uniq / total:.1%})")
    print(f"top 100 glosses cover {top100 / total:.1%} of all tokens")


if __name__ == "__main__":
    main()
