#!/usr/bin/env python3
"""Transliterate Devanagari to Roman.

Unlike Urdu script, Devanagari writes its short vowels, so this needs no dictionary:
the mapping is deterministic. That is the whole reason it is worth having here -- the
Urdu word-by-word text cannot be romanised, or spoken reliably, without guessing
vowels that are simply absent from the page.

    python devanagari.py --raw hindi-wbw-translation.json.zip --out data-hi/source/wbw.csv

Two things the naive table gets wrong, both handled below: nukta letters, which may
arrive precomposed or decomposed; and the inherent schwa, which Hindi drops in
positions where writing it produces "meharabaan" for what is said "meherbaan".
"""

import argparse
import csv
import json
import os
import re
import sys
import unicodedata
import zipfile

CONS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "ṭ", "ठ": "ṭh", "ड": "ḍ", "ढ": "ḍh", "ण": "ṇ",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "w", "ळ": "l",
    "श": "sh", "ष": "sh", "स": "s", "ह": "h",
}

# Nukta forms, keyed by the base letter. Unicode has precomposed characters for these
# (क़ U+0958 and friends) AND a decomposed form (क + ़). Text in the wild uses both,
# so normalise to NFD first and handle only the decomposed case.
NUKTA = {"क": "q", "ख": "kh", "ग": "gh", "ज": "z", "ड": "ṛ", "ढ": "ṛh",
         "फ": "f", "य": "y"}

MATRA = {"ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo",
         "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ृ": "ri",
         "ॉ": "o", "ॅ": "e"}
NASAL = {"ं": "n", "ँ": "n"}
VISARGA = {"ः": "h"}
INDEP = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo",
         "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऋ": "ri", "ॐ": "om"}

VIRAMA = "्"
NUKTA_MARK = "़"


def _units(word):
    """Split into (consonant|vowel, following vowel) units, tracking explicit schwa."""
    w = unicodedata.normalize("NFD", word)
    out = []
    i = 0
    while i < len(w):
        ch = w[i]
        if ch in INDEP:
            out.append(("V", INDEP[ch]))
            i += 1
            continue
        if ch in CONS:
            base = CONS[ch]
            i += 1
            if i < len(w) and w[i] == NUKTA_MARK:
                base = NUKTA.get(ch, base)
                i += 1
            vowel = "a"          # inherent, unless cancelled or replaced
            if i < len(w) and w[i] == VIRAMA:
                vowel = ""
                i += 1
            elif i < len(w) and w[i] in MATRA:
                vowel = MATRA[w[i]]
                i += 1
            tail = ""
            while i < len(w) and (w[i] in NASAL or w[i] in VISARGA):
                tail += NASAL.get(w[i]) or VISARGA.get(w[i])
                i += 1
            out.append(("C", base, vowel, tail))
            continue
        if ch in MATRA:
            out.append(("V", MATRA[ch]))
            i += 1
            continue
        if ch in NASAL or ch in VISARGA:
            out.append(("V", NASAL.get(ch) or VISARGA.get(ch)))
            i += 1
            continue
        if ch in (VIRAMA, NUKTA_MARK):
            i += 1
            continue
        out.append(("X", ch if ch.isascii() else ""))
        i += 1
    return out


def translit(word):
    """Devanagari word -> Roman, with Hindi schwa deletion applied."""
    units = _units(word)

    # Schwa deletion. Hindi drops the inherent 'a' word-finally, and in the penultimate
    # syllable when a vowel follows -- "मेहरबान" is meherbaan, not meharabaan. Applying
    # only the word-final rule leaves an extra vowel in the middle of most long words.
    cons = [k for k, u in enumerate(units) if u[0] == "C"]
    for pos, k in enumerate(cons):
        _, base, vowel, tail = units[k]
        if vowel != "a" or tail:
            continue
        is_last = pos == len(cons) - 1
        if is_last:
            units[k] = ("C", base, "", tail)
            continue
        # medial: drop if the next consonant carries its own vowel and the one after
        # exists, i.e. the schwa sits between two pronounced syllables.
        if pos + 1 < len(cons):
            nxt = units[cons[pos + 1]]
            if pos > 0 and nxt[2] not in ("", "a"):
                units[k] = ("C", base, "", tail)

    out = []
    for u in units:
        if u[0] == "C":
            out.append(u[1] + u[2] + u[3])
        else:
            out.append(u[1])
    return "".join(out)


def load(path):
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            name = [n for n in z.namelist() if n.lower().endswith(".json")][0]
            return json.loads(z.read(name).decode("utf-8"))
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--out", default="data-hi/source/wbw.csv")
    ap.add_argument("--roman-out", default="data-hi/source/roman.csv")
    ap.add_argument("--preview", type=int, default=12)
    args = ap.parse_args()

    data = load(args.raw)
    rows, roman_rows = [], []
    for key, gloss in data.items():
        parts = key.split(":")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            continue
        g = re.sub(r"\s+", " ", (gloss or "")).strip()
        if not g:
            continue
        s, a, w = (int(p) for p in parts)
        rom = " ".join(translit(x) for x in g.split())
        rows.append((s, a, w, g))
        roman_rows.append((s, a, w, rom))

    rows.sort()
    roman_rows.sort()

    for path, data_rows in ((args.out, rows), (args.roman_out, roman_rows)):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["surah", "ayah", "word", "gloss"])
            w.writerows(data_rows)

    print(f"{len(rows):,} glosses -> {args.out}")
    print(f"{len(roman_rows):,} romanised -> {args.roman_out}\n")
    for (s, a, w, g), (_, _, _, r) in list(zip(rows, roman_rows))[:args.preview]:
        print(f"  {s}:{a}:{w:<3} {g:<26} {r}")


if __name__ == "__main__":
    sys.exit(main())
