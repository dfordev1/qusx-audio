#!/usr/bin/env python3
"""Transliterate Urdu word-by-word glosses into Roman Urdu.

Urdu script does not write short vowels. "کہا" carries no vowel marks at all, so a
purely mechanical mapping yields "kha" where the word is "kaha". Rules alone therefore
cannot produce readable Roman Urdu.

The approach here is deliberately two-tier:

  * a lexicon of common words, where the vowels are supplied by hand. Urdu vocabulary
    is heavily skewed -- a few hundred words cover most of the corpus -- so this fixes
    the majority of text for a bounded amount of work;
  * character rules for everything else, which give a consistent, searchable
    approximation rather than a confident-looking wrong answer.

Output marks which tier produced each line so the approximate ones can be reviewed.

    python roman_urdu.py --data data --out data/roman.csv
"""

import argparse
import csv
import os
import re
import sys
import unicodedata

# ---------------------------------------------------------------- lexicon
# Word-level, hand-checked. Urdu vocabulary is steeply skewed: only 5,919 distinct
# words appear in the whole corpus, and the top 500 cover 86% of all word instances,
# so a finite lexicon reaches good coverage where rules never can.
#
# Loaded from data/roman_lexicon.csv when present -- keeping it in a data file rather
# than in code means corrections arrive as reviewable diffs.
LEXICON_FILE = "roman_lexicon.csv"

LEXICON = {
    "اللہ": "Allah", "اللہ کے": "Allah ke", "اللہ کی": "Allah ki",
    "اللہ نے": "Allah ne", "اللہ کا": "Allah ka", "اللہ تعالیٰ": "Allah ta'ala",
    "سے": "se", "میں": "mein", "نہیں": "nahin", "بیشک": "beshak", "جو": "jo",
    "اور": "aur", "اور نہ": "aur na", "مگر": "magar", "وہ": "woh", "کہ": "ke",
    "اور نہیں": "aur nahin", "پر": "par", "نہ": "na", "یا": "ya", "جب": "jab",
    "پھر": "phir", "طرف": "taraf", "اس میں": "is mein", "وہ لوگ": "woh log",
    "اگر": "agar", "یہ": "yeh", "میں سے": "mein se", "اور وہ": "aur woh",
    "ان کے لیے": "un ke liye", "کے لیے": "ke liye", "ان": "un", "اس": "is",
    "ہے": "hai", "ہیں": "hain", "تھا": "tha", "تھے": "the", "تھی": "thi",
    "کیا": "kiya", "کہا": "kaha", "کرو": "karo", "کرتے": "karte", "کرنے": "karne",
    "دیا": "diya", "لیا": "liya", "گیا": "gaya", "ہوا": "hua", "ہوئے": "hue",
    "لوگ": "log", "لوگوں": "logon", "تم": "tum", "ہم": "hum", "میرے": "mere",
    "تمہارے": "tumhare", "اپنے": "apne", "اپنی": "apni", "کوئی": "koi",
    "سب": "sab", "بڑا": "bara", "بہت": "bahut", "پھر بھی": "phir bhi",
    "دن": "din", "رات": "raat", "زمین": "zameen", "آسمان": "aasman",
    "رب": "Rabb", "نبی": "nabi", "رسول": "rasool", "کتاب": "kitab",
    "ایمان": "imaan", "عذاب": "azaab", "جنت": "jannat", "دوزخ": "dozakh",
    "رحم": "rehem", "مہربان": "meherban", "بار بار": "baar baar",
    "ساتھ": "saath", "نام": "naam", "ساتھ نام": "saath naam",
    "بعد": "baad", "پہلے": "pehle", "بغیر": "baghair", "سوا": "siwa",
    "کیوں": "kyun", "کون": "kaun", "کیسے": "kaise", "کہاں": "kahan",
}

# ------------------------------------------------------- character rules
# Order matters: multi-character sequences must be tried before single letters,
# otherwise "بھ" is consumed as "ب" + "ہ" and becomes "bh" only by accident.
DIGRAPHS = [
    ("بھ", "bh"), ("پھ", "ph"), ("تھ", "th"), ("ٹھ", "ṭh"), ("جھ", "jh"),
    ("چھ", "chh"), ("دھ", "dh"), ("ڈھ", "ḍh"), ("کھ", "kh"), ("گھ", "gh"),
    ("ڑھ", "ṛh"), ("رھ", "rh"), ("لھ", "lh"), ("مھ", "mh"), ("نھ", "nh"),
]

LETTERS = {
    "ا": "a", "آ": "aa", "أ": "a", "إ": "i", "ء": "'",
    "ب": "b", "پ": "p", "ت": "t", "ٹ": "ṭ", "ث": "s",
    "ج": "j", "چ": "ch", "ح": "h", "خ": "kh",
    "د": "d", "ڈ": "ḍ", "ذ": "z", "ر": "r", "ڑ": "ṛ", "ز": "z", "ژ": "zh",
    "س": "s", "ش": "sh", "ص": "s", "ض": "z", "ط": "t", "ظ": "z",
    "ع": "'", "غ": "gh", "ف": "f", "ق": "q", "ک": "k", "ك": "k", "گ": "g",
    "ل": "l", "م": "m", "ن": "n", "ں": "n", "ٹھ": "ṭh",
    "و": "o", "ؤ": "o", "ہ": "h", "ھ": "h", "ة": "a", "ۃ": "a",
    "ی": "i", "ي": "i", "ئ": "i", "ے": "e", "ى": "a",
}

# Short-vowel marks are usually absent, but honour them when present.
DIACRITICS = {
    "َ": "a",   # zabar / fatha
    "ِ": "i",   # zer / kasra
    "ُ": "u",   # pesh / damma
    "ّ": "",    # shadda -- doubling handled implicitly
    "ْ": "",    # jazm / sukun
    "ٰ": "a",   # superscript alef
}

DROP = set("ـ")  # tatweel


def load_lexicon(data_dir):
    """Merge the CSV lexicon over the built-in seed entries."""
    path = os.path.join(data_dir, LEXICON_FILE)
    if not os.path.exists(path):
        return 0
    n = 0
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            u = (row.get("urdu") or "").strip()
            r = (row.get("roman") or "").strip()
            if u and r:
                LEXICON[u] = r
                n += 1
    return n


def rule_translit(word):
    out = []
    i = 0
    while i < len(word):
        two = word[i:i + 2]
        hit = next((r for d, r in DIGRAPHS if d == two), None)
        if hit:
            out.append(hit)
            i += 2
            continue
        ch = word[i]
        if ch in DROP:
            i += 1
            continue
        if ch in DIACRITICS:
            out.append(DIACRITICS[ch])
            i += 1
            continue
        out.append(LETTERS.get(ch, ch if ch.isascii() else ""))
        i += 1
    s = "".join(out)

    # Word-initial alef is a vowel carrier, not an "a" of its own before another vowel.
    s = re.sub(r"^aa", "aa", s)
    s = re.sub(r"([bcdfghjklmnpqrstvwxyzṭḍṛ])\1{2,}", r"\1\1", s)
    return s


def transliterate(gloss):
    """Return (roman, tier) where tier is 'lexicon', 'mixed' or 'rules'."""
    g = unicodedata.normalize("NFC", gloss).strip()
    if g in LEXICON:
        return LEXICON[g], "lexicon"

    # Alternatives are transliterated on both sides of the slash and rejoined, so the
    # Roman line mirrors the printed line rather than silently dropping a variant.
    if "/" in g:
        parts, tiers = [], []
        for chunk in g.split("/"):
            r, t = transliterate(chunk.strip())
            if r:
                parts.append(r)
                tiers.append(t)
        tier = ("lexicon" if all(t == "lexicon" for t in tiers)
                else ("rules" if all(t == "rules" for t in tiers) else "mixed"))
        return " / ".join(parts), tier

    words = g.split()
    parts, known = [], 0
    for w in words:
        bare = w.strip("۔،؟!\"'()[]")
        if bare in LEXICON:
            parts.append(LEXICON[bare])
            known += 1
        else:
            parts.append(rule_translit(bare))
    roman = " ".join(p for p in parts if p)
    if known == len(words) and words:
        tier = "lexicon"
    elif known:
        tier = "mixed"
    else:
        tier = "rules"
    return roman, tier


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="")
    ap.add_argument("--preview", type=int, default=30)
    args = ap.parse_args()

    wl_path = os.path.join(args.data, "wordlist.csv")
    if not os.path.exists(wl_path):
        sys.exit(f"missing {wl_path}")
    added = load_lexicon(args.data)
    rows = list(csv.DictReader(open(wl_path, encoding="utf-8-sig", newline="")))
    print(f"lexicon: {len(LEXICON):,} entries ({added:,} from {LEXICON_FILE})\n")

    out_rows, tiers = [], {"lexicon": 0, "mixed": 0, "rules": 0}
    covered = {"lexicon": 0, "mixed": 0, "rules": 0}
    total_occ = sum(int(r["occurrences"]) for r in rows)

    for r in rows:
        # Transliterate the DISPLAY form. Roman Urdu is a reading aid shown beside the
        # Urdu line, so it must mirror what is printed -- including alternatives, which
        # the spoken form deliberately drops.
        source = r.get("display") or r["gloss"]
        roman, tier = transliterate(source)
        tiers[tier] += 1
        covered[tier] += int(r["occurrences"])
        out_rows.append({"gloss_id": r["gloss_id"], "urdu": source,
                         "roman": roman, "tier": tier,
                         "occurrences": r["occurrences"]})

    print(f"{len(rows):,} unique Urdu glosses\n")
    print(f"{'tier':<10} {'glosses':>8} {'positions':>10} {'coverage':>9}")
    for t in ("lexicon", "mixed", "rules"):
        print(f"{t:<10} {tiers[t]:>8,} {covered[t]:>10,} "
              f"{covered[t]/total_occ:>8.1%}")

    print(f"\nmost frequent {args.preview}:")
    for r in out_rows[:args.preview]:
        mark = "" if r["tier"] == "lexicon" else ("  ~" if r["tier"] == "mixed" else "  ?")
        print(f"  {int(r['occurrences']):>5}x  {r['urdu']:<22} {r['roman']}{mark}")

    out = args.out or os.path.join(args.data, "roman.csv")
    with open(out, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["gloss_id", "urdu", "roman", "tier",
                                           "occurrences"])
        w.writeheader()
        w.writerows(out_rows)
    print(f"\nwrote {out}")
    print("~ = partly from the lexicon, ? = rules only (review these)")


if __name__ == "__main__":
    main()
