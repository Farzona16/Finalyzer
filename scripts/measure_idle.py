"""Start the backend once, wait for health, print idle RSS and cold-start seconds (JSON).

Usage (from the repo root, backend venv): python scripts/measure_idle.py [--path /health]
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]


def wait_ready(url: str, timeout: float) -> float:
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return time.perf_counter() - start
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(url)


def tree_rss(proc: psutil.Process) -> int:
    total = proc.memory_info().rss
    for child in proc.children(recursive=True):
        try:
            total += child.memory_info().rss
        except psutil.NoSuchProcess:
            continue
    return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default="/health")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    env = {
        **os.environ,
        "APP_ENV": "development",
        "APP_SECRET_KEY": secrets.token_urlsafe(48),
        "DATABASE_URL": "sqlite:///" + (ROOT / "data" / "runtime" / "measure.db").as_posix(),
        "LLM_MODE": "offline",
        "PYTHONPATH": str(ROOT),
    }
    (ROOT / "data" / "runtime").mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
         "--port", str(args.port), "--workers", "1", "--log-level", "warning"],
        cwd=ROOT / "backend", env=env,
    )
    try:
        wait_ready(f"http://127.0.0.1:{args.port}{args.path}", 90)
        cold = time.perf_counter() - started
        time.sleep(3)
        rss = tree_rss(psutil.Process(proc.pid))
        print(json.dumps({"cold_start_s": round(cold, 2), "idle_rss_mb": round(rss / 2**20, 1)}))
    finally:
        proc.terminate()
        proc.wait(timeout=20)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
