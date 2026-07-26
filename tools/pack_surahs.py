#!/usr/bin/env python3
"""Pack per-word clips into one audio file per surah, plus a JSON timing map.

Why: the corpus is ~20,000 tiny clips. Serving them individually means ~20,000 HTTP
requests and ~20,000 stored objects, which is slow on mobile and awkward to host. One
file per surah is 114 requests; the player seeks to an offset instead of fetching.

Words repeat, so a gloss used 1,311 times is stored ONCE per surah it appears in and
referenced by every position that needs it.

Output (default dist/):
    audio/001.opus ... audio/114.opus
    map/001.json   ... map/114.json      {"words": {"1:1:1": [start, dur]}, ...}
    index.json                            manifest: sizes, counts, coverage

    python pack_surahs.py --data data-en --out dist-en

Requires ffmpeg. Run after the clips exist; missing clips are simply skipped and
reported, so this works fine on a partially generated corpus.
"""

import argparse
import csv
import json
import os
import subprocess
import sys


def tools():
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from app import FFMPEG, FFPROBE
        if FFMPEG:
            return FFMPEG, FFPROBE
    except Exception:
        pass
    import shutil
    return shutil.which("ffmpeg"), shutil.which("ffprobe")


def duration(probe, path):
    try:
        out = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", path],
                             capture_output=True, text=True, timeout=30)
        return float(out.stdout.strip())
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data-en")
    ap.add_argument("--out", default="dist-en")
    ap.add_argument("--bitrate", type=int, default=32)
    ap.add_argument("--lufs", type=float, default=-19.0)
    ap.add_argument("--gap", type=float, default=0.0,
                    help="extra silence between words inside the packed file")
    ap.add_argument("--surah", type=int, default=0, help="pack only this surah")
    args = ap.parse_args()

    ffmpeg, ffprobe = tools()
    if not ffmpeg or not ffprobe:
        sys.exit("ffmpeg/ffprobe not found")

    clips_dir = os.path.join(args.data, "clips")
    index_csv = os.path.join(args.data, "index.csv")
    if not os.path.exists(index_csv):
        sys.exit(f"missing {index_csv}")

    # position -> gloss_id
    positions = {}
    with open(index_csv, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            s, a, w = int(row["surah"]), int(row["ayah"]), int(row["word"])
            positions[(s, a, w)] = row["gloss_id"]

    have = set()
    if os.path.isdir(clips_dir):
        have = {f[:-4] for f in os.listdir(clips_dir) if f.endswith(".wav")}

    os.makedirs(os.path.join(args.out, "audio"), exist_ok=True)
    os.makedirs(os.path.join(args.out, "map"), exist_ok=True)

    surahs = sorted({s for (s, _a, _w) in positions})
    if args.surah:
        surahs = [s for s in surahs if s == args.surah]

    manifest = {"surahs": [], "bitrate": args.bitrate, "lufs": args.lufs}
    grand_words = grand_have = 0

    for s in surahs:
        rows = sorted([(a, w, gid) for (ss, a, w), gid in positions.items() if ss == s])
        # One entry per DISTINCT gloss in this surah -- a word used 40 times is stored
        # once and pointed at 40 times.
        order, seen = [], set()
        for _a, _w, gid in rows:
            if gid in have and gid not in seen:
                seen.add(gid)
                order.append(gid)

        grand_words += len(rows)
        grand_have += sum(1 for _a, _w, gid in rows if gid in have)

        if not order:
            manifest["surahs"].append({"surah": s, "words": len(rows), "voiced": 0,
                                       "clips": 0, "bytes": 0})
            continue

        # Concatenate with the demuxer: no re-encode of the inputs, exact ordering.
        listfile = os.path.join(args.out, f"_concat_{s:03d}.txt")
        with open(listfile, "w", encoding="utf-8") as fh:
            for gid in order:
                p = os.path.abspath(os.path.join(clips_dir, gid + ".wav")).replace("\\", "/")
                fh.write(f"file '{p}'\n")
                if args.gap > 0:
                    fh.write(f"duration {args.gap}\n")

        wav_tmp = os.path.join(args.out, f"_tmp_{s:03d}.wav")
        subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0",
                        "-i", listfile, "-ac", "1", "-ar", "48000",
                        "-c:a", "pcm_s24le", wav_tmp], capture_output=True, timeout=1800)

        # Offsets come from the source clip durations, summed in the same order the
        # concat used. Measuring the packed file instead would drift.
        offsets, t = {}, 0.0
        for gid in order:
            d = duration(ffprobe, os.path.join(clips_dir, gid + ".wav")) or 0.0
            offsets[gid] = (round(t, 3), round(d, 3))
            t += d + args.gap

        out_audio = os.path.join(args.out, "audio", f"{s:03d}.opus")
        subprocess.run([ffmpeg, "-y", "-v", "error", "-i", wav_tmp,
                        "-af", f"loudnorm=I={args.lufs}:TP=-1.5:LRA=7",
                        "-c:a", "libopus", "-b:a", f"{args.bitrate}k", "-ac", "1",
                        out_audio], capture_output=True, timeout=1800)

        words = {}
        for a, w, gid in rows:
            if gid in offsets:
                words[f"{s}:{a}:{w}"] = offsets[gid]

        with open(os.path.join(args.out, "map", f"{s:03d}.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"surah": s, "audio": f"audio/{s:03d}.opus", "words": words},
                      fh, separators=(",", ":"))

        size = os.path.getsize(out_audio) if os.path.exists(out_audio) else 0
        manifest["surahs"].append({"surah": s, "words": len(rows), "voiced": len(words),
                                   "clips": len(order), "bytes": size})
        for f in (listfile, wav_tmp):
            try:
                os.remove(f)
            except OSError:
                pass

        print(f"  surah {s:>3}  {len(order):>5} clips  {len(words):>5}/{len(rows):<5} words"
              f"  {size/1024/1024:>6.2f} MB", flush=True)

    manifest["words_total"] = grand_words
    manifest["words_voiced"] = grand_have
    manifest["bytes_total"] = sum(x["bytes"] for x in manifest["surahs"])
    with open(os.path.join(args.out, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, separators=(",", ":"))

    print(f"\n{len(surahs)} surahs packed -> {args.out}")
    print(f"words voiced : {grand_have:,} / {grand_words:,}")
    print(f"total size   : {manifest['bytes_total']/1024/1024:.1f} MB")
    print(f"requests     : {len(surahs)} audio + {len(surahs)} map (was ~{len(have):,})")


if __name__ == "__main__":
    main()
