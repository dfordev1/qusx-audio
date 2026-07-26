#!/usr/bin/env python3
"""Round-trip check: transcribe generated clips and compare to the intended text.

This is not listening. It is an objective substitute: if a text-to-speech clip is
transcribed back to the words it was made from, the audio almost certainly says the
right thing. Where the transcript diverges, something is wrong -- a mispronunciation,
a truncated clip, or a word the model rendered as something else.

    python verify_audio.py --data data-en --limit 250

Costs Scribe rates (~$0.22/hour of audio); a few hundred one-second clips is cents.
"""

import argparse
import csv
import difflib
import json
import os
import queue
import re
import sys
import threading
import urllib.error
import urllib.request
import uuid

API = "https://api.elevenlabs.io/v1/speech-to-text"


def key():
    k = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not k:
        sys.exit("ELEVENLABS_API_KEY is not set")
    return k


def multipart(fields, filename, filedata):
    """Build a multipart/form-data body without any third-party dependency."""
    boundary = "----" + uuid.uuid4().hex
    out = bytearray()
    for name, value in fields.items():
        out += f"--{boundary}\r\n".encode()
        out += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        out += f"{value}\r\n".encode()
    out += f"--{boundary}\r\n".encode()
    out += (f'Content-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\n').encode()
    out += b"Content-Type: audio/wav\r\n\r\n"
    out += filedata + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def transcribe(path, model, api_key, lang=None):
    with open(path, "rb") as fh:
        data = fh.read()
    fields = {"model_id": model}
    if lang:
        fields["language_code"] = lang
    body, ctype = multipart(fields, os.path.basename(path), data)
    req = urllib.request.Request(API, data=body, method="POST")
    req.add_header("xi-api-key", api_key)
    req.add_header("Content-Type", ctype)
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read()).get("text", "")


def norm(s):
    """Compare on words only -- punctuation and case are not audible."""
    s = re.sub(r"[^\w\s]", " ", (s or "").casefold())
    return " ".join(s.split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data-en")
    ap.add_argument("--model", default="scribe_v1")
    ap.add_argument("--language", default=None, help="e.g. eng, urd")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    api_key = key()
    clips_dir = os.path.join(args.data, "clips")
    wl_path = os.path.join(args.data, "wordlist.csv")

    with open(wl_path, encoding="utf-8-sig", newline="") as fh:
        wl = {r["gloss_id"]: r for r in csv.DictReader(fh)}

    have = [g for g in (os.path.splitext(f)[0] for f in os.listdir(clips_dir)
                        if f.endswith(".wav")) if g in wl]
    # Most frequent first: an error in a common word matters far more.
    have.sort(key=lambda g: -int(wl[g]["occurrences"]))
    if args.limit:
        have = have[:args.limit]

    print(f"verifying {len(have):,} clips from {clips_dir} with {args.model}")

    work = queue.Queue()
    for g in have:
        work.put(g)
    results, lock = [], threading.Lock()

    def worker():
        while True:
            try:
                gid = work.get_nowait()
            except queue.Empty:
                return
            expected = wl[gid]["gloss"]
            try:
                got = transcribe(os.path.join(clips_dir, gid + ".wav"),
                                 args.model, api_key, args.language)
                err = None
            except urllib.error.HTTPError as e:
                got, err = "", f"HTTP {e.code} {e.read().decode('utf-8','replace')[:120]}"
            except Exception as e:
                got, err = "", str(e)
            a, b = norm(expected), norm(got)
            ratio = difflib.SequenceMatcher(None, a, b).ratio() if (a and b) else 0.0
            with lock:
                results.append({"gloss_id": gid, "expected": expected, "heard": got,
                                "occurrences": int(wl[gid]["occurrences"]),
                                "match": a == b, "ratio": ratio, "error": err})
                if len(results) % 25 == 0:
                    print(f"  {len(results)}/{len(have)}", flush=True)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    errors = [r for r in results if r["error"]]
    ok = [r for r in results if not r["error"] and r["match"]]
    near = [r for r in results if not r["error"] and not r["match"] and r["ratio"] >= 0.7]
    bad = [r for r in results if not r["error"] and not r["match"] and r["ratio"] < 0.7]

    n = len(results) - len(errors)
    print(f"\nexact match : {len(ok):>5,} / {n:,}  ({len(ok)/max(1,n):.1%})")
    print(f"near match  : {len(near):>5,}  (differs slightly)")
    print(f"MISMATCH    : {len(bad):>5,}  (transcript unrelated to intended text)")
    if errors:
        print(f"api errors  : {len(errors):>5,}   e.g. {errors[0]['error'][:120]}")

    if bad:
        print("\nworst mismatches, most frequent first:")
        for r in sorted(bad, key=lambda r: -r["occurrences"])[:20]:
            print(f"  {r['occurrences']:>5}x  expected {r['expected']!r:<28} heard {r['heard']!r}")
    if near:
        print("\nnear misses (usually harmless homophones/spelling):")
        for r in sorted(near, key=lambda r: -r["occurrences"])[:10]:
            print(f"  {r['occurrences']:>5}x  expected {r['expected']!r:<28} heard {r['heard']!r}")

    if args.report:
        with open(args.report, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["gloss_id", "expected", "heard",
                                               "occurrences", "match", "ratio", "error"])
            w.writeheader()
            w.writerows(results)
        print(f"\nfull results -> {args.report}")


if __name__ == "__main__":
    main()
