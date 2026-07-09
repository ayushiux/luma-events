"""
score_events.py — LLM-based relevancy scoring for Luma events.

Reads the latest event dump, auto-discovers profiles from profiles/*.md,
sends each event to Claude for scoring against ALL profiles in one call,
and writes an augmented JSON with scores.

Incremental: uses a cache (scores_cache.json) keyed by (event_api_id, profiles_hash).
Only re-scores events that are new or whose profiles changed.

Usage:
    python score_events.py              # Score new events only (cached)
    python score_events.py --force      # Re-score everything
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
PROFILES_DIR = Path(__file__).parent / "profiles"
CACHE_PATH = OUT_DIR / "scores_cache.json"

MAX_WORKERS = 20
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 768
MAX_DESC_CHARS = 1500


# ── LLM client ───────────────────────────────────────────────────────────────
def make_client():
    import anthropic
    return anthropic.Anthropic(
        base_url=os.getenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:5000"),
        api_key=os.getenv("ANTHROPIC_AUTH_TOKEN", "your-anthropic-auth-token"),
    )


def call_llm(client, prompt: str, retries: int = 3) -> str:
    """Call the LLM with retries. Guards against empty/blank responses from a
    flaky proxy (which previously raised an uncaught IndexError and killed the
    entire scoring run)."""
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
        except Exception as exc:  # noqa: BLE001 — any transient proxy error should retry
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    raise ValueError(f"LLM call failed after {retries} attempts: {last_exc}")


# ── Profile discovery ─────────────────────────────────────────────────────────
def load_profiles() -> dict[str, str]:
    """Load all markdown profiles from profiles/ dir. Returns {name: content}."""
    profiles = {}
    for f in sorted(PROFILES_DIR.glob("*.md")):
        if f.name.lower() == "readme.md":
            continue
        name = f.stem.lower()
        profiles[name] = f.read_text(encoding="utf-8")
    return profiles


def profiles_hash(profiles: dict[str, str]) -> str:
    blob = json.dumps(profiles, sort_keys=True)
    return hashlib.md5(blob.encode()).hexdigest()[:12]


# ── ProseMirror → plain text ─────────────────────────────────────────────────
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


# ── Event summary builder ────────────────────────────────────────────────────
def event_summary(record: dict) -> str:
    entry = record.get("list_entry") or {}
    ev = entry.get("event") or {}
    det = record.get("detail") or {}
    det_ev = det.get("event") or {}
    cal = det.get("calendar") or {}
    hosts = det.get("hosts") or []
    guests = det.get("featured_guests") or []
    cats = [c.get("name") for c in (det.get("categories") or []) if c.get("name")]

    desc_text = pm_to_text(det.get("description_mirror") or {}).strip()
    if len(desc_text) > MAX_DESC_CHARS:
        desc_text = desc_text[:MAX_DESC_CHARS] + "..."

    start = ev.get("start_at") or det_ev.get("start_at") or "?"
    city = (ev.get("geo_address_info") or {}).get("city") or "?"

    lines = [
        f"Title: {ev.get('name') or det_ev.get('name') or '?'}",
        f"Date: {start[:16]}",
        f"City: {city}",
        f"Host calendar: {cal.get('name') or '?'}",
        f"Categories: {', '.join(cats) if cats else 'none'}",
    ]
    if hosts:
        lines.append(f"Hosts: {', '.join(h.get('name') or '?' for h in hosts[:5])}")
    if guests:
        guest_parts = []
        for g in guests[:5]:
            name = g.get("name") or "?"
            bio = (g.get("bio_short") or "").strip()
            guest_parts.append(f"{name} ({bio[:80]})" if bio else name)
        lines.append(f"Featured guests: {'; '.join(guest_parts)}")

    loc_type = ev.get("location_type") or det_ev.get("location_type") or "unknown"
    capacity = det.get("ticket_count") or 0
    rsvps = det.get("guest_count", 0)
    sold_out = det.get("sold_out", False)
    waitlist = det.get("waitlist_active", False)
    reg = det.get("registration_availability") or "unknown"
    scale = "small (intimate)" if capacity and capacity <= 30 else "medium" if capacity and capacity <= 150 else "large" if capacity else "unknown size"
    lines.append(f"Format: {loc_type}, {scale}")
    lines.append(f"RSVPs: {rsvps}" + (f" / {capacity} capacity" if capacity else ""))
    if sold_out or waitlist or reg in ("sold-out", "waitlist"):
        lines.append(f"Availability: {'SOLD OUT' if sold_out else 'Waitlist only'}")
    if desc_text:
        lines.append(f"Description:\n{desc_text}")

    return "\n".join(lines)


# ── Prompt builder ────────────────────────────────────────────────────────────
def build_prompt(profiles: dict[str, str], event_text: str) -> str:
    profile_sections = []
    for i, (name, content) in enumerate(profiles.items(), 1):
        profile_sections.append(f"PERSON {i} — {name.upper()}:\n{content}")
    profiles_text = "\n\n".join(profile_sections)

    names = [n.lower() for n in profiles.keys()]
    response_schema = ", ".join(
        f'"{n}": {{"score": N, "reason": "one sentence"}}'
        for n in names
    )

    return f"""You are scoring the relevancy of an event for {len(profiles)} people.

{profiles_text}

EVENT:
{event_text}

Score this event 0-100 for EACH person. Weight these factors:

RELEVANCE (50% of score):
- How precisely does the topic match their CORE interests (not just same broad domain)?
- Check the person's Low Interest list -- events matching those should score 5-20.

NETWORKING VALUE (25% of score):
- Who specifically will be there? Named speakers/guests with relevant titles matter.
- For job seekers: face-time with hiring managers at target companies?
- Small/intimate events (under 30 people) with senior attendees score higher than large generic ones.

QUALITY & LOGISTICS (15% of score):
- Reputable host? Strong attendance relative to capacity?
- Is it actually available (not sold out or waitlist-only)?

FORMAT FIT (10% of score):
- In-person events score higher than virtual for networking.
- Hackathons/workshops score higher than passive talks for builders.

HARD RULES (apply after calculating base score):
- If SOLD OUT or waitlist-only, cap score at 40 max.
- If virtual/online, reduce networking value component by half.
- If a featured guest or host is from a target company, add +10.
- Do NOT apply a "minimum floor". Wrong-domain events should score 3-10, not 15-25.
- Engineering-only events (agent infra, vector DBs, MLOps) with no design relevance = 10-20 for designers.
- Pure design/UX events with no AI angle = 5-15 for engineers.

CALIBRATION:
- Use the FULL 0-100 scale. Do not round to multiples of 5.
- Use odd numbers: 37, 63, 81 are better than 35, 65, 80.
- If 5 events are "vaguely relevant networking", they should NOT all get the same score.

Scoring guide:
- 90-100: Must-attend. Core topic + exceptional speakers + right format.
- 75-89: Strong recommendation. Clearly relevant topic with good networking.
- 60-74: Worth considering. Relevant but generic, or tangential with great people.
- 40-59: Marginal. Only tangentially related.
- 20-39: Low relevance. Wrong audience or domain.
- 0-19: Not relevant at all. Completely different field.

Respond ONLY with valid JSON, no other text:
{{{response_schema}}}"""


# ── Score one event ───────────────────────────────────────────────────────────
def score_one(client, profiles: dict[str, str],
              record: dict) -> dict[str, dict] | None:
    summary = event_summary(record)
    prompt = build_prompt(profiles, summary)
    raw = ""
    try:
        raw = call_llm(client, prompt)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rsplit("```", 1)[0]
        # Extract the FIRST JSON object even if the model appends extra prose
        # after it (raw_decode stops at the end of the first value, so trailing
        # "Extra data" no longer causes a JSONDecodeError).
        raw = raw.strip()
        start = raw.find("{")
        if start == -1:
            raise ValueError("no JSON object in response")
        scores, _ = json.JSONDecoder().raw_decode(raw[start:])
        for name in profiles:
            key = name.lower()
            if key not in scores:
                scores[key] = {"score": 0, "reason": "not scored"}
            scores[key]["score"] = max(0, min(100, int(scores[key].get("score", 0))))
        return scores
    except Exception as exc:  # noqa: BLE001 — never let one event kill the batch
        print(f"    score error: {exc} | raw: {raw[:200]}", flush=True)
        return None


# ── Find latest data file ────────────────────────────────────────────────────
def latest_dump() -> Path:
    for pattern in [
        "luma_raw_recursive_*_merged.json",
        "luma_raw_recursive_*.json",
        "luma_raw_*.json",
    ]:
        dumps = sorted(OUT_DIR.glob(pattern))
        if dumps:
            return dumps[-1]
    raise SystemExit("No event dump found. Run scrape_luma_recursive.py first.")


# ── Cache ─────────────────────────────────────────────────────────────────────
def load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict) -> None:
    CACHE_PATH.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    force = "--force" in sys.argv
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")

    profiles = load_profiles()
    if not profiles:
        print("No profiles found in profiles/. Create .md files there.")
        return 1
    print(f"Profiles: {', '.join(profiles.keys())}")
    phash = profiles_hash(profiles)
    print(f"Profiles hash: {phash}")

    src = latest_dump()
    print(f"Source: {src.name}")
    data = json.loads(src.read_text(encoding="utf-8"))
    records = data["events"]
    print(f"Events: {len(records)}")

    cache = load_cache() if not force else {}
    to_score = []
    cached_count = 0
    for r in records:
        aid = r.get("api_id")
        cached = cache.get(aid, {})
        if not force and cached.get("profiles_hash") == phash:
            r["scores"] = cached.get("scores", {})
            cached_count += 1
        else:
            to_score.append(r)

    print(f"Cached: {cached_count}, to score: {len(to_score)}")

    if to_score:
        print(f"\nScoring {len(to_score)} events with {MAX_WORKERS} parallel workers...")
        done = 0
        errors = 0

        def worker(record: dict) -> tuple[dict, dict | None]:
            client = make_client()
            scores = score_one(client, profiles, record)
            return record, scores

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(worker, r): r for r in to_score}
            for fut in as_completed(futures):
                record, scores = fut.result()
                done += 1
                if scores:
                    record["scores"] = scores
                    cache[record["api_id"]] = {
                        "profiles_hash": phash,
                        "scores": scores,
                    }
                else:
                    errors += 1
                    record["scores"] = {n: {"score": 0, "reason": "scoring failed"}
                                        for n in profiles}
                if done % 25 == 0 or done == len(to_score):
                    print(f"  scored {done}/{len(to_score)} "
                          f"({errors} errors)", flush=True)

        save_cache(cache)
        print(f"\nCache saved ({len(cache)} entries)")

    out_path = OUT_DIR / f"luma_scored_{ts}.json"
    out_data = {
        "scored_at": ts,
        "source": src.name,
        "profiles": list(profiles.keys()),
        "profiles_hash": phash,
        "count": len(records),
        "events": records,
    }
    out_path.write_text(
        json.dumps(out_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"\nWrote {out_path}")
    print(f"Size: {out_path.stat().st_size / 1024:.1f} KB")

    for name in profiles:
        scores = [r.get("scores", {}).get(name, {}).get("score", 0) for r in records]
        scores.sort(reverse=True)
        avg = sum(scores) / len(scores) if scores else 0
        top5 = scores[:5]
        print(f"\n{name.upper()} -- avg: {avg:.0f}, top 5: {top5}")
        for r in sorted(records, key=lambda x: x.get("scores", {}).get(name, {}).get("score", 0), reverse=True)[:5]:
            s = r["scores"][name]
            ev = r["list_entry"]["event"]
            title = (ev.get("name") or "?")[:55].encode("ascii", "replace").decode()
            reason = (s.get("reason") or "")[:50].encode("ascii", "replace").decode()
            print(f"  {s['score']:3}  {title:55}  | {reason}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
