#!/usr/bin/env python3
"""Export per-word clips to Opus and build QUSX-addressed index files.

One file per UNIQUE word, named by gloss id. Words repeat heavily -- "Allah" occurs
1,311 times but is a single file -- so a reader caches common words almost immediately
and later pages load faster. That reuse is the whole reason not to pack per surah.

Output:
    dist-words/audio/<gloss_id>.opus
    dist-words/index/001.json     {"words": {"<qusx id>": "<gloss_id>"}, ...}
    dist-words/index.json         manifest

    python export_words.py --data data-en --out dist-words --surah 1
    python export_words.py --data data-en --out dist-words

Requires ffmpeg, and the QUSX cache written by compare_qusx.py.
"""

import argparse
import collections
import csv
import json
import os
import queue
import re
import subprocess
import sys
import threading

ATTR = re.compile(r'(\w+)="([^"]*)"')


def tools():
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from app import FFMPEG
        if FFMPEG:
            return FFMPEG
    except Exception:
        pass
    import shutil
    return shutil.which("ffmpeg")


def qusx_ids(cache_dir, surah):
    """(ayah, word) -> global QUSX id, real words only."""
    path = os.path.join(cache_dir, f"{surah:03d}.qusx.xml")
    if not os.path.exists(path):
        return None
    out, ayah, n = {}, None, 0
    for m in re.finditer(r"<(ayah|word)\b([^>]*)>", open(path, encoding="utf-8").read()):
        tag, attrs = m.group(1), m.group(2)
        a = dict(ATTR.findall(attrs))
        if tag == "ayah":
            ayah, n = int(a.get("number", 0)), 0
            continue
        if a.get("type") == "number":
            continue
        n += 1
        if a.get("id"):
            out[(ayah, n)] = int(a["id"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data-en")
    ap.add_argument("--out", default="dist-words")
    ap.add_argument("--cache", default=os.path.join(".qusx-cache", "madani-v2"))
    ap.add_argument("--bitrate", type=int, default=32)
    ap.add_argument("--lufs", type=float, default=-19.0)
    ap.add_argument("--surah", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--base-url", default="")
    args = ap.parse_args()

    ffmpeg = tools()
    if not ffmpeg:
        sys.exit("ffmpeg not found")

    clips_dir = os.path.join(args.data, "clips")

    # The gloss TEXT ships alongside the audio. Publishing only "qusx id -> audio file"
    # leaves a consumer with sounds and no words: they cannot render a word-by-word
    # view, and cannot tell whether a clip is even correct. The text is the point.
    glosses = {}
    with open(os.path.join(args.data, "wordlist.csv"), encoding="utf-8-sig",
              newline="") as fh:
        for r in csv.DictReader(fh):
            glosses[r["gloss_id"]] = r["gloss"]

    positions = {}
    with open(os.path.join(args.data, "index.csv"), encoding="utf-8-sig",
              newline="") as fh:
        for r in csv.DictReader(fh):
            positions[(int(r["surah"]), int(r["ayah"]), int(r["word"]))] = r["gloss_id"]

    surahs = sorted({s for (s, _a, _w) in positions})
    if args.surah:
        surahs = [s for s in surahs if s == args.surah]

    audio_out = os.path.join(args.out, "audio")
    index_out = os.path.join(args.out, "index")
    os.makedirs(audio_out, exist_ok=True)
    os.makedirs(index_out, exist_ok=True)

    # Which gloss files are actually needed for the requested surahs.
    needed = set()
    per_surah = collections.OrderedDict()
    missing_ids = 0
    for s in surahs:
        ids = qusx_ids(args.cache, s)
        if ids is None:
            print(f"  surah {s}: no QUSX cache, skipped")
            continue
        words = {}
        for (ss, a, w), gid in positions.items():
            if ss != s:
                continue
            qid = ids.get((a, w))
            if qid is None:
                missing_ids += 1
                continue
            if not os.path.exists(os.path.join(clips_dir, gid + ".wav")):
                continue
            words[str(qid)] = gid
            needed.add(gid)
        per_surah[s] = words

    todo = [g for g in sorted(needed)
            if not os.path.exists(os.path.join(audio_out, g + ".opus"))]
    print(f"{len(needed):,} unique words needed, {len(todo):,} to encode\n")

    work = queue.Queue()
    for g in todo:
        work.put(g)
    lock, state = threading.Lock(), {"n": 0, "fail": 0}

    def worker():
        while True:
            try:
                gid = work.get_nowait()
            except queue.Empty:
                return
            src = os.path.join(clips_dir, gid + ".wav")
            dst = os.path.join(audio_out, gid + ".opus")
            p = subprocess.run(
                [ffmpeg, "-y", "-v", "error", "-i", src,
                 "-af", f"loudnorm=I={args.lufs}:TP=-1.5:LRA=7",
                 "-c:a", "libopus", "-b:a", f"{args.bitrate}k", "-ac", "1", dst],
                capture_output=True, timeout=300)
            with lock:
                if p.returncode != 0:
                    state["fail"] += 1
                state["n"] += 1
                if state["n"] % 500 == 0:
                    print(f"  {state['n']:,}/{len(todo):,}", flush=True)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total_bytes = 0
    manifest_lang = os.path.basename(args.data).split("-")[-1]
    manifest = {"qusxAudio": "0.1", "language": manifest_lang,
                "kind": "word-gloss", "layoutAgnostic": True,
                "bitrate": args.bitrate, "lufs": args.lufs,
                "base": args.base_url, "surahs": []}

    for s, words in per_surah.items():
        # words: qusx id -> audio file id (many ids share one file)
        # text : audio file id -> the English gloss it speaks
        # Splitting them this way keeps repeated words stored once, exactly as the
        # audio is.
        text = {gid: glosses.get(gid, "") for gid in sorted(set(words.values()))}
        payload = {"qusxAudio": "0.1", "surah": s, "layoutAgnostic": True,
                   "language": manifest_lang, "base": args.base_url,
                   "words": words, "text": text}
        with open(os.path.join(index_out, f"{s:03d}.json"), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        b = sum(os.path.getsize(os.path.join(audio_out, g + ".opus"))
                for g in set(words.values())
                if os.path.exists(os.path.join(audio_out, g + ".opus")))
        total_bytes += b
        manifest["surahs"].append({"surah": s, "words": len(words),
                                   "uniqueClips": len(set(words.values()))})

    files = [f for f in os.listdir(audio_out) if f.endswith(".opus")]
    manifest["clips"] = len(files)
    manifest["bytesAudio"] = sum(os.path.getsize(os.path.join(audio_out, f))
                                 for f in files)
    with open(os.path.join(args.out, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, separators=(",", ":"))

    print(f"\nencoded      : {state['n']:,}  failed {state['fail']}")
    print(f"clips on disk: {len(files):,}")
    print(f"audio size   : {manifest['bytesAudio']/1024/1024:.1f} MB")
    print(f"index files  : {len(per_surah)}")
    if missing_ids:
        print(f"positions without a QUSX id: {missing_ids}")


if __name__ == "__main__":
    main()
