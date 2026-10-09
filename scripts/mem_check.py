"""Measure RSS of a running Finalyzer process (phase V12 / V1b).

Usage, against a local or container process:

    python scripts/mem_check.py --url http://127.0.0.1:8000/health
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

import psutil


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000/health")
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument("--limit-mb", type=int, default=400)
    args = parser.parse_args()
    started = time.perf_counter()
    with urllib.request.urlopen(args.url, timeout=10) as response:
        body = response.read()
        status = response.status
    cold_ms = (time.perf_counter() - started) * 1000
    if args.pid:
        proc = psutil.Process(args.pid)
    else:
        proc = psutil.Process()
    rss_mb = proc.memory_info().rss / (1024 * 1024)
    report = {
        "url": args.url,
        "status": status,
        "body": body.decode()[:200],
        "rss_mb": round(rss_mb, 1),
        "limit_mb": args.limit_mb,
        "health_ms": round(cold_ms, 1),
        "ok": rss_mb <= args.limit_mb and status == 200,
    }
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
