"""
prep_events.py — Generate attendee prep material for high-scored events.

For each event where either person's score >= 60:
  1. Latest developments/news related to the event's topics (2-3 bullets)
  2. Conversation starters (3-4 smart questions for the meetup)

Uses Claude API. Parallelized. Cached by (event_api_id, description_hash).
Writes enriched JSON that build_viewer.py picks up automatically.

Usage:
    python prep_events.py              # Prep new events only (cached)
    python prep_events.py --force      # Re-prep everything
    python prep_events.py --threshold 70  # Custom score threshold
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)

OUT_DIR = Path(__file__).parent / "output"
CACHE_PATH = OUT_DIR / "prep_cache.json"

MAX_WORKERS = 12
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 800
SCORE_THRESHOLD = 60


def make_client():
    import anthropic
    return anthropic.Anthropic(
        base_url=os.getenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:5000"),
        api_key=os.getenv("ANTHROPIC_AUTH_TOKEN", "your-anthropic-auth-token"),
    )


def call_llm(client, prompt: str, retries: int = 3) -> str:
    """Call the LLM with retries, guarding against empty/blank proxy responses
    (an empty response.content previously raised an uncaught IndexError)."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            if not response.content:
                raise ValueError("empty response.content from LLM")
            text = response.content[0].text
            if not text or not text.strip():
                raise ValueError("blank text in LLM response")
            return text
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    raise ValueError(f"LLM call failed after {retries} attempts: {last_exc}")


def pm_to_text(node: dict) -> str:
    if not isinstance(node, dict):
        return ""
    ntype = node.get("type")
    if ntype == "text":
        return node.get("text") or ""
    parts = []
    for child in node.get("content") or []:
        parts.append(pm_to_text(child))
    text = "".join(parts)
    if ntype in ("paragraph", "heading", "list_item", "blockquote"):
        text += "\n"
    if ntype == "hard_break":
        return "\n"
    return text


def event_text(record: dict) -> str:
    entry = record.get("list_entry") or {}
    ev = entry.get("event") or {}
    det = record.get("detail") or {}
    det_ev = det.get("event") or {}
    cal = det.get("calendar") or {}
    hosts = det.get("hosts") or []
    guests = det.get("featured_guests") or []
    cats = [c.get("name") for c in (det.get("categories") or []) if c.get("name")]

    desc = pm_to_text(det.get("description_mirror") or {}).strip()
    if len(desc) > 1500:
        desc = desc[:1500] + "..."

    lines = [
        f"Title: {ev.get('name') or det_ev.get('name') or '?'}",
        f"Date: {(ev.get('start_at') or '')[:16]}",
        f"Host: {cal.get('name') or '?'}",
        f"Categories: {', '.join(cats) if cats else 'none'}",
    ]
    if hosts:
        lines.append(f"Speakers/Hosts: {', '.join(h.get('name') or '?' for h in hosts[:6])}")
    if guests:
        lines.append(f"Featured guests: {', '.join(g.get('name') or '?' for g in guests[:6])}")
    if desc:
        lines.append(f"Description:\n{desc}")
    return "\n".join(lines)


def build_prompt(evt_text: str) -> str:
    return f"""You are preparing an attendee for a tech/design meetup in the SF Bay Area. Based on this event, provide prep material.

EVENT:
{evt_text}

Generate:

1. RECENT DEVELOPMENTS (2-3 bullets)
What's been happening recently in the topics this event covers? Name specific companies, products, releases, or trends. Be concrete — "Google released ADK 2.0 with graph workflows" not "AI is advancing." Focus on the last 6 months.

2. CONVERSATION STARTERS (3-4 questions)
Smart questions to ask other attendees or speakers at this event. Should demonstrate you follow the space. Mix:
- One technical depth question
- One "what's your experience with X" question
- One forward-looking "where do you think X is going" question
- One practical "how are you using X in production" question

Respond ONLY with valid JSON:
{{"news": ["bullet 1", "bullet 2", "bullet 3"], "starters": ["question 1", "question 2", "question 3", "question 4"]}}"""


def content_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:12]


def prep_one(client, record: dict) -> dict | None:
    txt = event_text(record)
    prompt = build_prompt(txt)
    raw = ""
    try:
        raw = call_llm(client, prompt).strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()
        start = raw.find("{")
        if start == -1:
            raise ValueError("no JSON object in response")
        data, _ = json.JSONDecoder().raw_decode(raw[start:])
        news = data.get("news") or []
        starters = data.get("starters") or []
        return {"news": news[:3], "starters": starters[:4]}
    except Exception as exc:  # noqa: BLE001 — never let one event kill the batch
        print(f"    prep error: {exc}", flush=True)
        return None


def latest_scored() -> Path:
    dumps = sorted(OUT_DIR.glob("luma_scored_*.json"))
    if not dumps:
        raise SystemExit("No scored data. Run score_events.py first.")
    return dumps[-1]


def assert_fresh_source(src: Path) -> None:
    """Guard against the silent-freeze failure mode: if score_events crashed and
    wrote nothing new, the latest scored file will be older than the latest raw
    dump. Forwarding it would republish stale data as if it were today's. Fail
    loudly instead so the pipeline (and supervisor) sees a real failure."""
    raw_dumps = sorted(OUT_DIR.glob("luma_raw_recursive_*.json"))
    if not raw_dumps:
        return
    latest_raw = raw_dumps[-1]
    if src.stat().st_mtime < latest_raw.stat().st_mtime:
        raise SystemExit(
            f"STALE SOURCE: scored file {src.name} is older than the latest raw "
            f"dump {latest_raw.name}. score_events likely failed — refusing to "
            f"republish stale data. Fix scoring and re-run."
        )


def load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict) -> None:
    CACHE_PATH.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def main() -> int:
    force = "--force" in sys.argv
    threshold = SCORE_THRESHOLD
    for i, a in enumerate(sys.argv):
        if a == "--threshold" and i + 1 < len(sys.argv):
            threshold = int(sys.argv[i + 1])

    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    src = latest_scored()
    assert_fresh_source(src)
    print(f"Source: {src.name}")
    data = json.loads(src.read_text(encoding="utf-8"))
    records = data["events"]
    print(f"Events: {len(records)}")

    # Filter to events where any person's score >= threshold
    eligible = []
    for r in records:
        scores = r.get("scores") or {}
        max_score = max((s.get("score", 0) for s in scores.values()), default=0)
        if max_score >= threshold:
            eligible.append(r)
    print(f"Eligible (score >= {threshold}): {len(eligible)}")

    cache = load_cache() if not force else {}
    to_prep: list[dict] = []
    cached_count = 0

    for r in eligible:
        aid = r.get("api_id")
        txt = event_text(r)
        chash = content_hash(txt)
        cached = cache.get(aid)
        if not force and cached and cached.get("content_hash") == chash:
            r["prep"] = cached.get("prep", {})
            cached_count += 1
        else:
            to_prep.append(r)

    print(f"Cached: {cached_count}, to prep: {len(to_prep)}")

    if to_prep:
        print(f"\nPrepping {len(to_prep)} events with {MAX_WORKERS} workers...")
        done = 0
        errors = 0

        def worker(record: dict) -> tuple[dict, dict | None]:
            client = make_client()
            prep = prep_one(client, record)
            return record, prep

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(worker, r): r for r in to_prep}
            for fut in as_completed(futures):
                record, prep = fut.result()
                done += 1
                aid = record["api_id"]
                txt = event_text(record)
                chash = content_hash(txt)
                if prep:
                    record["prep"] = prep
                    cache[aid] = {"content_hash": chash, "prep": prep}
                else:
                    errors += 1
                    record["prep"] = {"news": [], "starters": []}
                if done % 10 == 0 or done == len(to_prep):
                    print(f"  prepped {done}/{len(to_prep)} ({errors} errors)",
                          flush=True)

        save_cache(cache)
        print(f"Cache saved ({len(cache)} entries)")

    # Also attach cached prep to eligible events that were already cached
    for r in eligible:
        if "prep" not in r:
            aid = r.get("api_id")
            cached = cache.get(aid)
            if cached:
                r["prep"] = cached.get("prep", {})

    # Write back to scored JSON (augmented with prep)
    out_path = OUT_DIR / f"luma_scored_{ts}.json"
    data["events"] = records
    data["prepped_at"] = ts
    data["prep_threshold"] = threshold
    data["prep_count"] = len(eligible)
    out_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"\nWrote {out_path}")
    print(f"Size: {out_path.stat().st_size / 1024:.1f} KB")

    # Show samples
    print(f"\nSample prep (first 2 eligible events):")
    for r in eligible[:2]:
        ev = r["list_entry"]["event"]
        p = r.get("prep", {})
        name = (ev.get("name") or "?")[:50].encode("ascii", "replace").decode()
        print(f"\n  {name}")
        for n in (p.get("news") or [])[:2]:
            print(f"    NEWS: {n[:80].encode('ascii','replace').decode()}")
        for s in (p.get("starters") or [])[:2]:
            print(f"    TALK: {s[:80].encode('ascii','replace').decode()}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
