"""
run_daily.py — Daily Luma events pipeline orchestrator.

Runs the full pipeline in sequence:
  1. Scrape all Bay Area events (recursive crawler)
  2. Merge extra agent-discovered seeds (second pass)
  3. Score events with Claude (incremental, cached)
  4. Build the viewer HTML
  5. Copy to docs/index.html for GitHub Pages
  6. Git commit + push (only if viewer changed)

Designed to be wrapped by supervisor/run.py and scheduled via Task Scheduler.
Each step is fault-tolerant — failures are logged, pipeline continues.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)

ROOT = Path(__file__).parent
OUTPUT = ROOT / "output"
DOCS = ROOT / "docs"
PYTHON = Path(r"C:\Users\suagraw\Ayushi\browser-agent\.venv\Scripts\python.exe")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("luma-daily")


def run_step(name: str, script: str, args: list[str] | None = None,
             timeout_min: int = 15) -> bool:
    cmd = [str(PYTHON), str(ROOT / script)] + (args or [])
    log.info(f"[{name}] Starting: {' '.join(cmd)}")
    t0 = time.time()
    try:
        result = subprocess.run(
            cmd,
            cwd=str(ROOT),
            timeout=timeout_min * 60,
            capture_output=False,
        )
        elapsed = time.time() - t0
        if result.returncode == 0:
            log.info(f"[{name}] OK ({elapsed:.0f}s)")
            return True
        else:
            log.error(f"[{name}] Failed with exit code {result.returncode} ({elapsed:.0f}s)")
            return False
    except subprocess.TimeoutExpired:
        log.error(f"[{name}] Timed out after {timeout_min} min")
        return False
    except Exception as exc:
        log.error(f"[{name}] Exception: {exc}")
        return False


def copy_to_docs() -> bool:
    viewer = OUTPUT / "viewer.html"
    if not viewer.exists():
        log.error("[copy] viewer.html not found")
        return False
    DOCS.mkdir(exist_ok=True)
    dest = DOCS / "index.html"
    shutil.copy2(viewer, dest)
    log.info(f"[copy] {viewer.name} -> docs/index.html ({dest.stat().st_size / 1024:.0f} KB)")
    return True


def git_push() -> bool:
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git"] + list(args),
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )

    def gh(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["gh"] + list(args),
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )

    # Only publish when the tracked page actually changed; untracked output/*
    # JSON should never gate or get swept into the commit.
    if not git("status", "--porcelain", "docs/index.html").stdout.strip():
        log.info("[git] docs/index.html unchanged — nothing to publish")
        return True

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    git("add", "docs/index.html")

    result = git("commit", "-m", f"Daily update: {ts}")
    if result.returncode != 0:
        log.error(f"[git] commit failed: {result.stderr.strip()}")
        return False
    log.info(f"[git] Committed: Daily update: {ts}")

    # The repo (ayushiux/luma-events, upstream ayushi/dev) requires the ayushiux
    # GitHub account. The machine's default active account is agrawalsumeet25-dot,
    # which 403s on push. Switch to ayushiux for the push, then restore.
    RESTORE_ACCOUNT = "agrawalsumeet25-dot"
    sw = gh("auth", "switch", "--user", "ayushiux")
    if sw.returncode != 0:
        log.error(f"[git] gh auth switch to ayushiux failed: {sw.stderr.strip()}")

    try:
        result = git("push")
    finally:
        gh("auth", "switch", "--user", RESTORE_ACCOUNT)

    if result.returncode != 0:
        log.error(f"[git] push failed: {result.stderr.strip()}")
        return False
    log.info("[git] Pushed to ayushi/dev as ayushiux")
    return True


def main() -> int:
    log.info("=" * 50)
    log.info("Luma Events Daily Pipeline")
    log.info("=" * 50)
    t0 = time.time()
    results = {}

    results["scrape"] = run_step("scrape", "scrape_luma_recursive.py", timeout_min=10)
    results["second_pass"] = run_step("second_pass", "second_pass.py", timeout_min=8)
    results["score"] = run_step("score", "score_events.py", timeout_min=20)
    results["prep"] = run_step("prep", "prep_events.py", timeout_min=20)
    results["build"] = run_step("build", "build_viewer.py", timeout_min=2)
    results["copy"] = copy_to_docs()
    results["git"] = git_push()

    elapsed = time.time() - t0
    log.info("")
    log.info("=" * 50)
    log.info(f"Pipeline complete in {elapsed:.0f}s")
    for step, ok in results.items():
        status = "OK" if ok else "FAILED"
        log.info(f"  {step:15} {status}")
    log.info("=" * 50)

    failed = [k for k, v in results.items() if not v]
    if failed:
        log.warning(f"Failed steps: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
