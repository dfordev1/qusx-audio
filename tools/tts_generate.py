#!/usr/bin/env python3
"""Generate gloss clips with the ElevenLabs API, verifying the risky ones.

Writes into <data>/clips/<gloss_id>.wav in the same format the recorder produces
(mono 48 kHz 24-bit), so the Library, QC and Export tabs work on the result unchanged.

Settings were chosen by A/B testing (ab_test / voice_test / speed_test / punct_test):
turbo v2.5 matched multilingual on English at half the price, stability 0.8 steadied
short inputs, 0.85 was the preferred pace, and a TRAILING full stop gave the cleanest
close. A leading full stop measurably hurt, so the text is "gloss." and nothing more.

Generation is non-deterministic: the same text and settings produce different audio
each time, and roughly one in five takes of a very short word comes out wrong ("In"
spoken as "End"). Multi-word glosses never failed in testing. So single-word clips are
transcribed back and regenerated if they do not match, and everything else is trusted.
That targeting is what keeps verification at cents rather than dollars.

    setx ELEVENLABS_API_KEY "your-key"      (then open a new terminal)

    python tts_generate.py --list-voices
    python tts_generate.py --data data-en --voice <id> --limit 25
    python tts_generate.py --data data-en --voice <id>

Safe to interrupt and re-run: existing clips are skipped, so nothing is paid for twice.
Stops cleanly when the account runs out of credit.
"""

import argparse
import csv
import difflib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

API_ROOT = "https://api.elevenlabs.io/v1"
CLIPS_DIR = os.path.join("data", "clips")
WORDLIST = os.path.join("data", "wordlist.csv")

DEFAULT_MODEL = "eleven_turbo_v2_5"
MULTILINGUAL = "eleven_multilingual_v2"   # required for Urdu
STT_MODEL = "scribe_v1"

# Never spoken. Gives the model the sentence context a bare gloss lacks.
CTX_PREV = "He opened the book and read the following words aloud, slowly and clearly."
CTX_NEXT = "Then he paused before continuing with the next word."

# Trim the model's ragged silence, then pad by an exact amount, so every clip has an
# identical lead-in and tail rather than whatever the model happened to leave.
TRIM = ("silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.02:"
        "detection=rms")
TRIM_BOTH = f"{TRIM},areverse,{TRIM},areverse"

# Errors that will never succeed on retry and mean the whole run should stop.
FATAL_CODES = {"payment_issue", "payment_required", "quota_exceeded",
               "max_character_limit_exceeded", "invalid_api_key"}

ABORT = threading.Event()
ABORT_REASON = []


def api_key():
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        sys.exit("ELEVENLABS_API_KEY is not set. See the docstring at the top of this file.")
    return key


def request(path, method="GET", body=None, key=None, raw=False):
    req = urllib.request.Request(f"{API_ROOT}{path}", method=method)
    req.add_header("xi-api-key", key or api_key())
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data=data, timeout=180) as r:
        payload = r.read()
    return payload if raw else json.loads(payload)


def list_voices():
    for v in request("/voices").get("voices", []):
        print(f"{v.get('voice_id',''):<24} {v.get('category',''):<12} {v.get('name','')}")


def ffmpeg_bin():
    """Reuse app.py's PATH-independent lookup so a stale shell still works."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from app import FFMPEG  # noqa: E402
        if FFMPEG:
            return FFMPEG
    except Exception:
        pass
    return shutil.which("ffmpeg")


def is_fatal(detail_text):
    low = (detail_text or "").lower()
    return any(code in low for code in FATAL_CODES)


# ------------------------------- audio -------------------------------

def to_clip_wav(mp3_bytes, dst, ffmpeg, pad):
    filt = TRIM_BOTH
    if pad > 0:
        filt = f"{TRIM_BOTH},adelay={int(pad * 1000)},apad=pad_dur={pad}"
    p = subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", "pipe:0", "-af", filt,
         "-ac", "1", "-ar", "48000", "-c:a", "pcm_s24le", dst],
        input=mp3_bytes, capture_output=True, timeout=120,
    )
    if p.returncode != 0 or not os.path.exists(dst) or os.path.getsize(dst) == 0:
        raise RuntimeError(f"ffmpeg failed: {p.stderr.decode('utf-8', 'replace')[:200]}")


# ---------------------------- verification ----------------------------

def multipart(fields, filename, filedata):
    b = "----" + uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    out += f"--{b}\r\n".encode()
    out += f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
    out += b"Content-Type: audio/wav\r\n\r\n" + filedata + b"\r\n"
    out += f"--{b}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={b}"


def transcribe(path, key, language):
    with open(path, "rb") as fh:
        data = fh.read()
    fields = {"model_id": STT_MODEL}
    if language:
        fields["language_code"] = language
    body, ctype = multipart(fields, os.path.basename(path), data)
    req = urllib.request.Request(f"{API_ROOT}/speech-to-text", data=body, method="POST")
    req.add_header("xi-api-key", key)
    req.add_header("Content-Type", ctype)
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read()).get("text", "")


def norm(s):
    return " ".join(re.sub(r"[^\w\s]", " ", (s or "").casefold()).split())


def close_enough(expected, heard, threshold):
    a, b = norm(expected), norm(heard)
    if not a or not b:
        return False
    if a == b:
        return True
    # Homophones like "to"/"two" and "of"/"off" are correct audio that the transcriber
    # spells differently. Rejecting those would burn retries on clips that are fine.
    return difflib.SequenceMatcher(None, a, b).ratio() >= threshold


# ---------------------------- generation ----------------------------

def synth(text, voice, model, key, stability, similarity, speed, context):
    settings = {"stability": stability, "similarity_boost": similarity}
    if speed is not None:
        settings["speed"] = speed
    body = {"text": text, "model_id": model, "voice_settings": settings}
    if context:
        body["previous_text"] = CTX_PREV
        body["next_text"] = CTX_NEXT
    return request(f"/text-to-speech/{voice}", "POST", body, key, raw=True)


def make_clip(item, cfg, key, ffmpeg):
    """Generate one gloss, verifying and retrying when it is a short word.

    Returns (status, detail) where status is 'ok', 'unverified', 'failed' or 'fatal'.
    """
    gid = item["gloss_id"]
    gloss = item["gloss"]
    dst = os.path.join(CLIPS_DIR, gid + ".wav")
    text = gloss + ("." if cfg["period"] else "")

    # Only short glosses are checked -- multi-word ones were correct in every test, and
    # they are 90% of the corpus, so verifying them would cost ten times as much for
    # nothing.
    verify = cfg["verify"] and len(gloss.split()) <= cfg["verify_max_words"]
    attempts = cfg["attempts"] if verify else 1

    last_heard = ""
    for attempt in range(attempts):
        if ABORT.is_set():
            return "failed", "aborted"

        delay = 2.0
        audio = None
        for net_try in range(4):
            try:
                audio = synth(text, cfg["voice"], cfg["model"], key, cfg["stability"],
                              cfg["similarity"], cfg["speed"], cfg["context"])
                break
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                if is_fatal(detail) or e.code in (401, 403):
                    ABORT.set()
                    if not ABORT_REASON:
                        ABORT_REASON.append(f"HTTP {e.code} {detail}")
                    return "fatal", detail
                if e.code == 429 or e.code >= 500:
                    time.sleep(delay)
                    delay *= 2
                    continue
                return "failed", f"HTTP {e.code} {detail}"
            except Exception as e:
                if net_try == 3:
                    return "failed", str(e)
                time.sleep(delay)
                delay *= 2
        if audio is None:
            return "failed", "no audio returned"

        try:
            to_clip_wav(audio, dst, ffmpeg, cfg["pad"])
        except Exception as e:
            return "failed", str(e)

        if not verify:
            return "unverified", ""

        try:
            heard = transcribe(dst, key, cfg["language"])
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if is_fatal(detail):
                ABORT.set()
                if not ABORT_REASON:
                    ABORT_REASON.append(f"HTTP {e.code} {detail}")
                return "fatal", detail
            # Verification is a safety net; if it is unavailable, keep the audio
            # rather than throwing away a clip that is probably fine.
            return "unverified", f"stt error: {detail[:80]}"
        except Exception as e:
            return "unverified", f"stt error: {e}"

        last_heard = heard
        if close_enough(gloss, heard, cfg["threshold"]):
            return "ok", heard

    return "failed", f"heard {last_heard!r} after {attempts} attempts"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data", help="dataset dir (data=Urdu, data-en=English)")
    ap.add_argument("--list-voices", action="store_true")
    ap.add_argument("--voice")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--stability", type=float, default=0.8)
    ap.add_argument("--similarity", type=float, default=0.85)
    ap.add_argument("--speed", type=float, default=0.85)
    ap.add_argument("--pad", type=float, default=0.20)
    ap.add_argument("--no-period", action="store_true", help="do not append a full stop")
    ap.add_argument("--no-context", action="store_true")
    ap.add_argument("--no-verify", action="store_true", help="skip transcription checking")
    ap.add_argument("--verify-max-words", type=int, default=1,
                    help="verify glosses up to this many words (1 = single words only)")
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--threshold", type=float, default=0.75,
                    help="transcript similarity accepted as correct")
    ap.add_argument("--language", default="eng", help="STT language code, e.g. eng, urd")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    key = api_key()

    global CLIPS_DIR, WORDLIST
    CLIPS_DIR = os.path.join(args.data, "clips")
    WORDLIST = os.path.join(args.data, "wordlist.csv")

    if args.list_voices:
        list_voices()
        return
    if not args.voice:
        sys.exit("--voice is required (see --list-voices)")

    ffmpeg = ffmpeg_bin()
    if not ffmpeg:
        sys.exit("ffmpeg not found")

    os.makedirs(CLIPS_DIR, exist_ok=True)
    done = {os.path.splitext(f)[0] for f in os.listdir(CLIPS_DIR) if f.endswith(".wav")}

    with open(WORDLIST, encoding="utf-8-sig", newline="") as fh:
        items = list(csv.DictReader(fh))
    if not args.overwrite:
        items = [i for i in items if i["gloss_id"] not in done]
    if args.limit:
        items = items[:args.limit]
    if not items:
        print("nothing to do - every gloss already has a clip")
        return

    cfg = {
        "voice": args.voice, "model": args.model, "stability": args.stability,
        "similarity": args.similarity, "speed": args.speed, "pad": args.pad,
        "period": not args.no_period, "context": not args.no_context,
        "verify": not args.no_verify, "verify_max_words": args.verify_max_words,
        "attempts": args.attempts, "threshold": args.threshold,
        "language": args.language,
    }

    chars = sum(len(i["gloss"]) for i in items)
    to_verify = sum(1 for i in items if len(i["gloss"].split()) <= args.verify_max_words)
    print(f"{len(items):,} glosses to synthesise ({chars:,} characters)")
    print(f"dataset={args.data}  voice={args.voice}  model={args.model}")
    print(f"stability={args.stability} speed={args.speed} pad={args.pad}s "
          f"period={'yes' if cfg['period'] else 'no'} "
          f"context={'yes' if cfg['context'] else 'no'}")
    if cfg["verify"]:
        print(f"verifying {to_verify:,} short clips (<={args.verify_max_words} word), "
              f"up to {args.attempts} attempts each")
    print()

    work = queue.Queue()
    for it in items:
        work.put(it)

    lock = threading.Lock()
    state = {"ok": 0, "unverified": 0, "failed": 0}
    failures = []

    def worker():
        while not ABORT.is_set():
            try:
                item = work.get_nowait()
            except queue.Empty:
                return
            status, detail = make_clip(item, cfg, key, ffmpeg)
            with lock:
                if status in ("failed", "fatal"):
                    state["failed"] += 1
                    failures.append((item["gloss_id"], item["gloss"], detail))
                else:
                    state[status] += 1
                n = sum(state.values())
                if n % 25 == 0:
                    print(f"  {n}/{len(items)}  verified={state['ok']} "
                          f"ok={state['unverified']} failed={state['failed']}", flush=True)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.workers)]
    started = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total_done = state["ok"] + state["unverified"]
    print(f"\n{total_done:,} clips written in {time.time()-started:.0f}s")
    print(f"  verified correct : {state['ok']:,}")
    print(f"  written unchecked: {state['unverified']:,}")
    print(f"  failed           : {state['failed']:,}")

    if ABORT.is_set():
        print("\nSTOPPED EARLY — the account rejected further requests:")
        print(f"  {ABORT_REASON[0] if ABORT_REASON else 'unknown'}")
        print("Top up the balance and re-run the same command; finished clips are kept")
        print("and will not be paid for again.")

    if failures:
        path = os.path.join(args.data, "tts_failures.csv")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["gloss_id", "gloss", "detail"])
            w.writerows(failures)
        print(f"\nfailures -> {path}")
        for gid, gloss, detail in failures[:10]:
            print(f"  {gloss!r}: {detail[:110]}")


if __name__ == "__main__":
    main()
