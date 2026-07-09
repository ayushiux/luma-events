"""
scrape_luma_recursive.py — Exhaustive Luma crawler for Bay Area events.

Turns over every stone Luma exposes publicly:

  Phase A — CATEGORY x PLACE
    For each Luma top-level category (AI, Tech, Crypto, Climate, Food, ...),
    paginate `/discover/get-paginated-events` filtered to the SF Bay Area
    discover-place. Captures any event Luma has tagged with that category.

  Phase B — SEED CALENDAR HARVEST
    Pull every `featured_calendar` from each category page (via __NEXT_DATA__),
    plus every calendar from `/discover/get-calendars?place=SF`, plus a
    hand-picked list of known Bay Area AI/tech communities.

  Phase C — RECURSIVE CALENDAR CRAWL
    For each calendar in the queue:
      1. fetch its events via `/calendar/get-items`
      2. for every event that's in the Bay Area, record it
      3. each event has a host calendar; queue any unseen host
    BFS until the queue is empty or the calendar cap is hit.

  Phase D — DETAIL ENRICHMENT
    Hit `/event/get?event_api_id=<id>` for every unique event found.

  Phase E — WRITE
    Same JSON shape as scrape_luma.py so build_viewer.py works unchanged.

All endpoints are public; no auth.
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from threading import Lock

import requests

sys.stdout.reconfigure(line_buffering=True)

# ── Endpoints ─────────────────────────────────────────────────────────────────
DISCOVER_EVENTS_URL = "https://api.lu.ma/discover/get-paginated-events"
DISCOVER_CALENDARS_URL = "https://api.lu.ma/discover/get-calendars"
CALENDAR_URL = "https://api.lu.ma/calendar/get-items"
EVENT_URL = "https://api.lu.ma/event/get"

# ── Constants ─────────────────────────────────────────────────────────────────
BAY_AREA_PLACE_ID = "discplace-BDj7GNbGlsF7Cka"

# south, north, west, east — anything inside this box counts as Bay Area
BAY_AREA_BBOX = (37.0, 38.5, -123.0, -121.5)

CATEGORIES = {
    "cat-ai": ("ai", "AI"),
    "cat-tech": ("tech", "Tech"),
    "cat-crypto": ("crypto", "Crypto"),
    "cat-climate": ("climate", "Climate"),
    "cat-fooddrink": ("fooddrink", "Food & Drink"),
    "cat-C1VaNLnt25w9t6c": ("wellness", "Wellness"),
    "cat-0Km9ZnuBjFAjwFl": ("fitness", "Fitness"),
}

HAND_PICKED_SEED_SLUGS = [
    # ── AI / ML / agents (Bay Area communities) ────────────────────────────
    "genai-collective", "claudecommunity", "airstreet", "ls", "claw",
    "agihouse", "agi-house", "cerebralvalley", "cerebral-valley",
    "aitinkerers", "ai-tinkerers", "ai-engineer", "aiengineer",
    "ai-collective-sf", "ai-collective", "mlcollective", "mlops-community",
    "huggingface", "hugging-face", "langchain", "llamaindex", "crewai",
    "modal-labs", "modal", "replicate", "anthropic", "openai-events",
    "weaviate", "pinecone", "chroma", "qdrant", "instill-ai",
    "voice-ai", "video-ai", "agents", "rag", "vector", "evals", "evaluations",
    "ai-snack-club", "ai-research", "ai-safety", "ai-policy", "ai-leaders",
    "the-ai-collective", "ainetwork", "ai-network", "ainexus",
    # ── Builder / hacker / startup communities ────────────────────────────
    "southparkcommons", "south-park-commons", "foundersinc", "founders-inc",
    "pioneer", "yc", "ycombinator", "hackathon-hackers",
    "sf-builders-collective", "frontiertower", "frontier-tower",
    "sf-hardware-meetup", "big-brain-sf", "tech-week", "techweek",
    "sf-tech", "sftech", "tech-sf", "buildbox", "builder",
    "ondeck", "on-deck", "stripe-press", "indiehackers",
    "12-scrappy-founders", "scrappy-founders", "hustle-fund",
    # ── Design / UX / UI ───────────────────────────────────────────────────
    "designer-hangout", "designbuddies", "design", "blok",
    "lovable", "bolt-new", "cursor", "v0", "tldraw",
    "design-buddies", "fig-sf", "figma-events",
    "config2026", "config-2026", "config",
    "creative-mornings-sf", "cmsf", "creativemorningssf",
    "wearedesignerly", "designerly",
    # ── Dev tools / programming ────────────────────────────────────────────
    "vercel", "netlify", "deno", "nextjs", "supabase", "neon",
    "github-sf", "gitlab-sf", "docker-sf", "kubernetes-sf",
    "rust-sf", "go-sf", "python-sf", "typescript-sf",
    "javascript-sf", "react-sf", "nodejs-sf",
    "database-sf", "duckdb", "motherduck", "snowflake-sf",
    # ── Crypto / web3 ──────────────────────────────────────────────────────
    "ethsf", "eth-sf", "crypto-sf", "web3-sf", "solana-sf",
    "blockchain-sf", "defi-sf", "nft-sf", "dao-sf",
    "ethdenver", "consensus", "coinbase-events",
    # ── Climate / sustainability / hardware ────────────────────────────────
    "sfcw2026", "sfcw", "climate-sf", "cleantech", "greentech",
    "climate-tech", "energy-sf", "battery-sf", "solar-sf",
    "sustainability-sf", "eco-sf",
    # ── Investor / VC ──────────────────────────────────────────────────────
    "investor", "vc-sf", "a16z", "sequoia", "benchmark",
    "founders-fund", "kleiner-perkins", "accel-partners",
    "lightspeed", "general-catalyst", "index-ventures",
    # ── Brand-specific Bay Area calendars ──────────────────────────────────
    "workos-events", "workos", "baseten", "deel", "stripe-events",
    "googleforstartups", "google-developers-sf", "nvidia-developers",
    "microsoft-sf", "anthropic-events", "openai-sf", "openai",
    "cloudflare-sf", "datadog-sf", "twilio-sf",
    "ibm-sf", "amazon-sf", "aws-sf", "azure-sf",
    "salesforce-sf", "slack-sf", "notion-sf", "linear-sf",
    # ── Speaker series / talks / education ────────────────────────────────
    "talks-sf", "lectures-sf", "podcast-sf",
    "school-of-ai", "academy-sf", "ucbsf", "stanford-events",
    "berkeley-events", "ucsf-events", "ucla-sf", "ucsc-events",
    # ── Robotics / hardware ────────────────────────────────────────────────
    "robotics", "robotics-sf", "hardware", "iot-sf",
    "embedded-sf", "drones-sf", "av-sf", "avtech",
    # ── Specific verticals ─────────────────────────────────────────────────
    "ml", "llm", "agi", "mlops", "data-science", "data-sf",
    "fintech", "fintech-sf", "edtech", "healthtech", "biotech",
    "spacex-sf", "aerospace-sf", "quantum-sf",
    "voice-tech", "music-tech",
    # ── Food / drink ───────────────────────────────────────────────────────
    "food-sf", "dinner-sf", "wine-sf", "coffee-sf", "supper",
    "supperclub", "dinnerseries", "pop-up-sf",
    # ── Music / arts / culture ────────────────────────────────────────────
    "music-sf", "concert-sf", "arts-sf", "culture-sf",
    "gallery-sf", "museum-sf", "theater-sf", "comedy-sf",
    "improv-sf", "jazz-sf", "indie-sf",
    # ── Wellness / fitness ────────────────────────────────────────────────
    "yoga-sf", "fitness-sf", "running-sf", "cycling-sf",
    "hiking-sf", "meditation-sf", "mindfulness", "breathwork",
    "wim-hof", "cold-plunge", "sauna-sf",
    # ── Networking / social ───────────────────────────────────────────────
    "networking-sf", "happy-hour-sf", "founders-night", "drinks-sf",
    "young-professionals-sf", "ycp-sf", "twentysomething-sf",
    "expats-sf", "international-sf",
    # ── Schools / accelerators ────────────────────────────────────────────
    "techstars-sf", "500-startups", "500-global", "iterative",
    "afore-capital", "south-park-capital",
    "betaworks", "neo-collective", "beta-university",
    # ── Religion / spirituality ───────────────────────────────────────────
    "church-sf", "mosque-sf", "temple-sf", "synagogue-sf",
    "spiritual", "spiritual-sf",
    # ── Misc / catch-all ──────────────────────────────────────────────────
    "frontier-syndicate", "frontiersyndicate",
    "tatianasf", "the-love-potion-library",
    "communitykit", "scalekit", "techwalk", "unbound-events",
    "tiat", "intersection-art-tech",
    "japan-society", "japansocietynorcal", "us-japan-council",
    "asian-heritage-week", "asianheritage",
    "compassion-2-0", "compassion20",
    "techequity", "tech-equity-ai", "techequityai",
    "local-economy", "localeconomy",
    # ── GitHub-discovered slugs (via `gh search code`) ─────────────────────
    "aisf", "aisf-hack", "oss-ai", "openclaw",
    "dtc-events", "mlflow", "aidevfeb", "geminimeetup",
    "PMHive", "craft_", "scaledown", "the-space-events",
    "events-by-vara-gear", "moment", "mafia", "scala",
    "SF-TechTalks", "sf-openclaw-hackathon", "sf-openclaw-guided-workshop-bu",
    "sf-tech-week", "sfbayea", "sfruby", "sf-ruby-oct-2024",
    "SFwritingclubseries", "sfWritingClubCalendar",
    # ── SF page __NEXT_DATA__ slug references ──────────────────────────────
    "AsianHeritageWeek26", "batf", "berkeleygatewayaccelerator",
    "deepmind", "duckdb-motherduck-sfbay", "fdotinc",
    "marinarunclub", "notion-for-startups", "surreal_",
    "thirdlayer", "unboundevents",
]

# Calendar api_ids directly harvested from lu.ma/sf page __NEXT_DATA__
# (skip slug resolution — feed straight into Phase C queue)
SF_PAGE_DIRECT_CAL_IDS = [
    "cal-4TEeXLXVUtUqg91", "cal-5thnx2ZyREuxdJz", "cal-752i9ECdyr36HjD",
    "cal-7Q5A70Bz5Idxopu", "cal-GxGNsMwfrZt4srK", "cal-HImlOWziQ7yD36i",
    "cal-JDvWKGO7IFEV3WY", "cal-Q0U4JABvmidBDV4", "cal-QKhanAN5iCONZzq",
    "cal-V1ReDPXTb8z9zcK", "cal-Z1tslEBMjjCh4fd", "cal-abA0XUA5nOEMIvJ",
    "cal-iHkz5obZdong4ta", "cal-kmclvwZFldKtmzj", "cal-nGfPsblC0xslMQD",
    "cal-oOBz9Y16qzmpxg2", "cal-twiOosdGMMY66DI", "cal-veSMzkkl0ywVlWb",
    "cal-yP17DTziybSxLII", "cal-zauF7Gj3RTECxwR",
]

PAGE_SIZE = 100
DETAIL_DELAY_SEC = 0.0   # parallel mode: no per-call delay (workers self-throttle)
RETRY_BACKOFF_SEC = 5
MAX_RETRIES = 3
MAX_CALENDARS = 5000
MAX_EVENTS_PER_CALENDAR = 500
PARALLEL_WORKERS_LIST = 12   # parallel calendar crawls
PARALLEL_WORKERS_DETAIL = 16 # parallel event detail fetches
PARALLEL_WORKERS_SLUG = 12   # parallel slug resolution

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

OUT_DIR = Path(__file__).parent / "output"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ── HTTP ──────────────────────────────────────────────────────────────────────
def get_json(session: requests.Session, url: str, params: dict | None = None) -> dict:
    for attempt in range(MAX_RETRIES):
        try:
            r = session.get(url, params=params, headers=HEADERS, timeout=30)
            if r.status_code == 429:
                wait = RETRY_BACKOFF_SEC * (attempt + 1)
                print(f"    429, sleeping {wait}s", flush=True)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            if attempt == MAX_RETRIES - 1:
                print(f"    failed: {exc}", flush=True)
                return {}
            time.sleep(RETRY_BACKOFF_SEC)
    return {}


def get_page_initial_data(session: requests.Session, slug: str) -> dict | None:
    """Fetch lu.ma/<slug> and parse __NEXT_DATA__."""
    try:
        r = session.get(f"https://lu.ma/{slug}", headers=HEADERS, timeout=30,
                        allow_redirects=True)
        r.raise_for_status()
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                      r.text, re.DOTALL)
        if not m:
            return None
        return json.loads(m.group(1)).get("props", {}).get("pageProps", {}).get("initialData")
    except (requests.RequestException, json.JSONDecodeError, KeyError):
        return None


# ── Bay Area filter ───────────────────────────────────────────────────────────
BAY_CITIES = {
    "San Francisco", "Berkeley", "Oakland", "Palo Alto", "Mountain View",
    "Sunnyvale", "San Jose", "Menlo Park", "Redwood City", "San Mateo",
    "Cupertino", "Santa Clara", "Fremont", "Hayward", "Burlingame", "Millbrae",
    "Daly City", "Belmont", "South San Francisco", "Emeryville", "Albany",
    "Alameda", "Walnut Creek", "Pleasanton", "Foster City", "Brisbane",
    "Vallejo", "Richmond", "San Bruno", "San Carlos", "Saratoga", "Los Altos",
    "Los Gatos", "Mill Valley", "Sausalito", "San Rafael", "Napa", "Castro Valley",
}


def in_bay_area(event_dict: dict) -> bool:
    geo = event_dict.get("coordinate") or {}
    lat, lng = geo.get("latitude"), geo.get("longitude")
    if lat is not None and lng is not None:
        south, north, west, east = BAY_AREA_BBOX
        return south <= lat <= north and west <= lng <= east
    city = (event_dict.get("geo_address_info") or {}).get("city") or ""
    return city in BAY_CITIES


# ── Calendar discovery (Phase B helpers) ──────────────────────────────────────
def harvest_calendars_from_category_page(session: requests.Session,
                                         slug: str) -> list[tuple[str, str]]:
    """Visit lu.ma/<category-slug>, return list of (api_id, slug) for featured calendars."""
    init = get_page_initial_data(session, slug)
    if not init or init.get("kind") != "category":
        return []
    data = init.get("data") or {}
    out = []
    for c in (data.get("featured_calendars") or []) + (data.get("timeline_calendars") or []):
        cal = c.get("calendar", c) if isinstance(c, dict) and "calendar" in c else c
        api_id = cal.get("api_id")
        cal_slug = cal.get("slug")
        if api_id:
            out.append((api_id, cal_slug or api_id))
    return out


def harvest_calendars_via_discover(session: requests.Session,
                                   category_id: str | None = None) -> list[tuple[str, str]]:
    """Hit /discover/get-calendars and return (api_id, slug) pairs."""
    params: dict = {"discover_place_api_id": BAY_AREA_PLACE_ID, "pagination_limit": 100}
    if category_id:
        params["discover_category_api_id"] = category_id
    data = get_json(session, DISCOVER_CALENDARS_URL, params)
    out = []
    for c in data.get("calendars") or []:
        api_id = c.get("api_id")
        slug = c.get("slug")
        if api_id:
            out.append((api_id, slug or api_id))
    return out


def resolve_slug_to_calendar_id(session: requests.Session, slug: str) -> str | None:
    init = get_page_initial_data(session, slug)
    if not init or init.get("kind") != "calendar":
        return None
    return (init.get("data") or {}).get("calendar", {}).get("api_id")


# ── Crawlers ──────────────────────────────────────────────────────────────────
def crawl_category_place_events(session: requests.Session, category_id: str,
                                label: str) -> list[dict]:
    print(f"  [discover] {label} x SF Bay Area", flush=True)
    entries: list[dict] = []
    cursor: str | None = None
    page = 0
    while True:
        page += 1
        params: dict = {
            "period": "future",
            "pagination_limit": PAGE_SIZE,
            "discover_place_api_id": BAY_AREA_PLACE_ID,
            "discover_category_api_id": category_id,
        }
        if cursor:
            params["pagination_cursor"] = cursor
        data = get_json(session, DISCOVER_EVENTS_URL, params)
        ents = data.get("entries", [])
        entries.extend(ents)
        if not data.get("has_more") or not data.get("next_cursor"):
            break
        cursor = data.get("next_cursor")
        time.sleep(0.25)
    print(f"    -> {len(entries)} events", flush=True)
    return entries


def crawl_calendar_events(session: requests.Session, calendar_api_id: str,
                          label: str) -> list[dict]:
    entries: list[dict] = []
    cursor: str | None = None
    while len(entries) < MAX_EVENTS_PER_CALENDAR:
        params: dict = {
            "calendar_api_id": calendar_api_id,
            "period": "future",
            "pagination_limit": PAGE_SIZE,
        }
        if cursor:
            params["pagination_cursor"] = cursor
        data = get_json(session, CALENDAR_URL, params)
        ents = data.get("entries", [])
        entries.extend(ents)
        if not data.get("has_more") or not data.get("next_cursor"):
            break
        cursor = data.get("next_cursor")
        time.sleep(0.15)
    return entries[:MAX_EVENTS_PER_CALENDAR]


def fetch_event_detail(session: requests.Session, api_id: str) -> dict | None:
    data = get_json(session, EVENT_URL, {"event_api_id": api_id})
    return data if (isinstance(data, dict) and not data.get("message")) else None


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_path = OUT_DIR / f"luma_raw_recursive_{ts}.json"

    session = requests.Session()
    lock = Lock()
    all_entries: dict[str, dict] = {}       # event_api_id -> list_entry
    cal_meta: dict[str, str] = {}           # cal_api_id -> label
    visited_cals: set[str] = set()

    def enqueue(api_id: str | None, label: str) -> None:
        if not api_id or api_id in visited_cals or api_id in cal_meta:
            return
        cal_meta[api_id] = label

    def add_event(entry: dict) -> None:
        ev = entry.get("event") or {}
        aid = ev.get("api_id")
        if not aid:
            return
        with lock:
            if aid not in all_entries:
                all_entries[aid] = entry
        host = ev.get("calendar_api_id") or (entry.get("calendar") or {}).get("api_id")
        with lock:
            if host and host not in visited_cals and host not in cal_meta:
                cal_meta[host] = f"host:{(entry.get('calendar') or {}).get('slug') or host}"

    # ── Phase A: category x place events + uncategorized place events ───────
    print("[A] CATEGORY x BAY AREA EVENTS")
    for cid, (cslug, label) in CATEGORIES.items():
        for entry in crawl_category_place_events(session, cid, label):
            add_event(entry)

    print("  [discover] (no category) x SF Bay Area")
    entries: list[dict] = []
    cursor: str | None = None
    page = 0
    while True:
        page += 1
        params: dict = {
            "period": "future",
            "pagination_limit": PAGE_SIZE,
            "discover_place_api_id": BAY_AREA_PLACE_ID,
        }
        if cursor:
            params["pagination_cursor"] = cursor
        data = get_json(session, DISCOVER_EVENTS_URL, params)
        ents = data.get("entries", [])
        entries.extend(ents)
        if not data.get("has_more") or not data.get("next_cursor"):
            break
        cursor = data.get("next_cursor")
    print(f"    -> {len(entries)} events (uncategorized + categorized)", flush=True)
    for entry in entries:
        add_event(entry)

    print(f"  collected so far: {len(all_entries)} events, "
          f"{len(cal_meta)} calendars queued")

    # ── Phase B: seed calendar harvest ───────────────────────────────────────
    print("\n[B] SEED CALENDAR HARVEST")

    print("  B1: each category page's featured_calendars")
    for cid, (cslug, label) in CATEGORIES.items():
        cals = harvest_calendars_from_category_page(session, cslug)
        print(f"    /{cslug}: +{len(cals)} calendars", flush=True)
        for api_id, slug in cals:
            enqueue(api_id, f"cat-featured:{slug}")

    print("  B2: /discover/get-calendars for SF (per category + overall)")
    for api_id, slug in harvest_calendars_via_discover(session):
        enqueue(api_id, f"discover:{slug}")
    for cid, (cslug, label) in CATEGORIES.items():
        for api_id, slug in harvest_calendars_via_discover(session, cid):
            enqueue(api_id, f"discover-{cslug}:{slug}")

    print("  B3: direct cal_id seeds from SF page")
    for cid in SF_PAGE_DIRECT_CAL_IDS:
        enqueue(cid, f"sf-page:{cid}")

    print(f"  B4: parallel slug resolution ({len(HAND_PICKED_SEED_SLUGS)} slugs, "
          f"{PARALLEL_WORKERS_SLUG} workers)")
    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS_SLUG) as ex:
        futures = {ex.submit(resolve_slug_to_calendar_id, requests.Session(), s): s
                   for s in HAND_PICKED_SEED_SLUGS}
        for fut in as_completed(futures):
            slug = futures[fut]
            try:
                api_id = fut.result()
                if api_id:
                    enqueue(api_id, f"seed:{slug}")
            except Exception:
                pass

    print(f"  queue depth after seeding: {len(cal_meta) - len(visited_cals)}")

    # ── Phase C: PARALLEL recursive calendar crawl ───────────────────────────
    print(f"\n[C] PARALLEL RECURSIVE CALENDAR CRAWL "
          f"(cap {MAX_CALENDARS}, {PARALLEL_WORKERS_LIST} workers)")

    round_n = 0
    while True:
        # Drain the queue into a batch (atomic snapshot)
        with lock:
            batch = [(cid, cal_meta[cid]) for cid in cal_meta
                     if cid not in visited_cals]
            if not batch:
                break
            if len(visited_cals) >= MAX_CALENDARS:
                print(f"  hit MAX_CALENDARS cap ({MAX_CALENDARS})", flush=True)
                break
            batch = batch[:MAX_CALENDARS - len(visited_cals)]
            for cid, _ in batch:
                visited_cals.add(cid)
        round_n += 1
        print(f"  round {round_n}: crawling {len(batch)} calendars "
              f"(visited so far: {len(visited_cals)})", flush=True)

        def crawl_one(item: tuple[str, str]) -> tuple[str, list[dict]]:
            cid, label = item
            ents = crawl_calendar_events(requests.Session(), cid, label)
            return cid, ents

        new_hosts_found = 0
        with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS_LIST) as ex:
            for cid, ents in ex.map(crawl_one, batch):
                kept = 0
                for entry in ents:
                    ev = entry.get("event") or {}
                    if not ev.get("api_id"):
                        continue
                    if not in_bay_area(ev):
                        continue
                    kept += 1
                    before = len(cal_meta)
                    add_event(entry)
                    if len(cal_meta) > before:
                        new_hosts_found += (len(cal_meta) - before)
        print(f"    round {round_n}: events total {len(all_entries)}, "
              f"new hosts discovered {new_hosts_found}, "
              f"queue {len(cal_meta) - len(visited_cals)}", flush=True)

    print(f"\n  calendars crawled: {len(visited_cals)}, "
          f"unique bay events: {len(all_entries)}")

    # ── Phase D: PARALLEL detail enrichment ──────────────────────────────────
    print(f"\n[D] PARALLEL DETAIL ENRICHMENT ({len(all_entries)} events, "
          f"{PARALLEL_WORKERS_DETAIL} workers)")
    records: list[dict] = []
    items = list(all_entries.items())

    def fetch_one(item: tuple[str, dict]) -> dict:
        aid, entry = item
        detail = fetch_event_detail(requests.Session(), aid)
        return {"api_id": aid, "list_entry": entry, "detail": detail}

    done = 0
    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS_DETAIL) as ex:
        for rec in ex.map(fetch_one, items):
            records.append(rec)
            done += 1
            if done % 50 == 0 or done == len(items):
                print(f"  detail {done}/{len(items)}", flush=True)

    # ── Phase E: write ───────────────────────────────────────────────────────
    print(f"\n[E] WRITE {out_path}")
    out_path.write_text(json.dumps({
        "fetched_at": ts,
        "place_api_id": BAY_AREA_PLACE_ID,
        "categories_crawled": [lbl for _, (_, lbl) in CATEGORIES.items()],
        "calendars_crawled": len(visited_cals),
        "count": len(records),
        "events": records,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"DONE")
    print(f"  unique bay area events: {len(records)}")
    print(f"  calendars crawled:      {len(visited_cals)}")
    print(f"  output:                 {out_path}")
    print(f"  size:                   {out_path.stat().st_size / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
