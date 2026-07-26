#!/usr/bin/env python3
"""Fast R2 uploader using the S3 API directly, with SigV4 signed in pure Python.

Why not wrangler: `wrangler r2 object put` spawns a Node process per object. At ~1.5s
of startup each, 20,537 files is roughly eight hours of doing nothing but booting
Node. Signing the request ourselves and using threads turns that into minutes.

Needs R2 API credentials (Access Key ID + Secret) -- these are different from the
account API token. Create at:
    dash.cloudflare.com -> R2 -> API -> Manage API Tokens -> Create (Object Read & Write)

    set R2_ACCESS_KEY_ID=...
    set R2_SECRET_ACCESS_KEY=...
    python upload_s3.py --dist dist-words --prefix en --version v1

Resumable: successful objects are recorded and skipped on re-runs.
"""

import argparse
import datetime
import hashlib
import hmac
import json
import os
import queue
import sys
import threading
import urllib.error
import urllib.request

SERVICE = "s3"
REGION = "auto"
STATE = ".r2_uploaded.json"

CONTENT_TYPES = {".opus": "audio/ogg", ".json": "application/json",
                 ".wav": "audio/wav", ".ogg": "audio/ogg"}


def sign_key(secret, datestamp, region, service):
    def h(key, msg):
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()
    k = h(("AWS4" + secret).encode("utf-8"), datestamp)
    k = h(k, region)
    k = h(k, service)
    return h(k, "aws4_request")


def put_object(host, bucket, key, body, content_type, access_key, secret_key,
               cache_control):
    """Signature Version 4, single-chunk PUT. Payload hash is required by R2."""
    now = datetime.datetime.now(datetime.timezone.utc)
    amzdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")

    canonical_uri = "/" + bucket + "/" + "/".join(
        urllib.parse.quote(p, safe="") for p in key.split("/"))
    payload_hash = hashlib.sha256(body).hexdigest()

    headers = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amzdate,
        "content-type": content_type,
    }
    if cache_control:
        headers["cache-control"] = cache_control

    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
    canonical_request = "\n".join(
        ["PUT", canonical_uri, "", canonical_headers, signed_headers, payload_hash])

    scope = f"{datestamp}/{REGION}/{SERVICE}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amzdate, scope,
                         hashlib.sha256(canonical_request.encode()).hexdigest()])
    signature = hmac.new(sign_key(secret_key, datestamp, REGION, SERVICE),
                         to_sign.encode(), hashlib.sha256).hexdigest()

    headers["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}")

    req = urllib.request.Request(f"https://{host}{canonical_uri}", data=body,
                                 method="PUT", headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status


def main():
    import urllib.parse  # noqa: F401  (used inside put_object)
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default="dist-words")
    ap.add_argument("--bucket", default="quran-wbw-audio")
    ap.add_argument("--prefix", default="en")
    ap.add_argument("--version", default="v1")
    ap.add_argument("--account", default=os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""))
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--cache-control", default="public, max-age=31536000, immutable")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    access_key = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
    if not access_key or not secret_key:
        sys.exit("Set R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY "
                 "(R2 -> API -> Manage API Tokens -> Object Read & Write)")
    if not args.account:
        sys.exit("Set CLOUDFLARE_ACCOUNT_ID or pass --account")

    host = f"{args.account}.r2.cloudflarestorage.com"

    files = []
    for sub in ("audio", "index", "map"):
        d = os.path.join(args.dist, sub)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                ext = os.path.splitext(name)[1].lower()
                files.append((f"{args.prefix}/{args.version}/{sub}/{name}", p,
                              CONTENT_TYPES.get(ext, "application/octet-stream")))
    top = os.path.join(args.dist, "index.json")
    if os.path.exists(top):
        files.append((f"{args.prefix}/{args.version}/index.json", top,
                      "application/json"))

    state_path = os.path.join(args.dist, STATE)
    done = {}
    if os.path.exists(state_path):
        try:
            done = json.load(open(state_path, encoding="utf-8"))
        except Exception:
            done = {}

    todo = [(k, p, c) for k, p, c in files if done.get(k) != os.path.getsize(p)]
    total_mb = sum(os.path.getsize(p) for _k, p, _c in todo) / 1048576
    print(f"{len(files):,} files, {len(todo):,} to upload ({total_mb:.1f} MB)")
    print(f"-> https://{host}/{args.bucket}/{args.prefix}/{args.version}/")
    if args.dry_run or not todo:
        return

    work = queue.Queue()
    for item in todo:
        work.put(item)

    lock = threading.Lock()
    state = {"ok": 0, "fail": 0, "bytes": 0}
    failures = []
    # The ledger is flushed periodically rather than per object: 20k small writes
    # would dominate the runtime we just saved.
    def flush():
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(done, fh)
        os.replace(tmp, state_path)

    def worker():
        while True:
            try:
                key, path, ctype = work.get_nowait()
            except queue.Empty:
                return
            try:
                with open(path, "rb") as fh:
                    body = fh.read()
                cc = args.cache_control if ctype == "audio/ogg" else "public, max-age=3600"
                put_object(host, args.bucket, key, body, ctype,
                           access_key, secret_key, cc)
                with lock:
                    done[key] = len(body)
                    state["ok"] += 1
                    state["bytes"] += len(body)
                    n = state["ok"] + state["fail"]
                    if n % 250 == 0:
                        flush()
                        print(f"  {n:,}/{len(todo):,}  "
                              f"{state['bytes']/1048576:.1f} MB", flush=True)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:200]
                with lock:
                    state["fail"] += 1
                    failures.append((key, f"HTTP {e.code} {detail}"))
            except Exception as e:
                with lock:
                    state["fail"] += 1
                    failures.append((key, str(e)))

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.workers)]
    import time
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    flush()

    dt = time.time() - t0
    print(f"\nuploaded {state['ok']:,} in {dt:.0f}s "
          f"({state['ok']/max(dt,1):.0f} files/s, "
          f"{state['bytes']/1048576/max(dt,1):.1f} MB/s)")
    if failures:
        print(f"failed {len(failures):,}")
        for k, e in failures[:8]:
            print(f"  {k}: {e[:140]}")


if __name__ == "__main__":
    import urllib.parse
    main()
