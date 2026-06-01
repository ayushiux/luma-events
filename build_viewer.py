"""
build_viewer.py — Generate the Luma Events viewer.

Reads the latest scored/raw JSON dump and produces output/viewer.html —
a self-contained, mobile-first event discovery app with:
- Discover mode (time-grouped grid with hero spotlight)
- Swipe mode (Tinder-style card stack with drag physics)
- Score rings, keyboard nav, confetti, 3D tilt, command palette
"""

from __future__ import annotations

import html
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)

OUT_DIR = Path(__file__).parent / "output"


def prosemirror_to_html(node: dict) -> str:
    if not isinstance(node, dict):
        return ""
    ntype = node.get("type")
    content = node.get("content") or []
    attrs = node.get("attrs") or {}
    if ntype == "text":
        text = html.escape(node.get("text") or "")
        for mark in node.get("marks") or []:
            mtype = mark.get("type")
            mattrs = mark.get("attrs") or {}
            if mtype == "bold":
                text = f"<strong>{text}</strong>"
            elif mtype == "italic":
                text = f"<em>{text}</em>"
            elif mtype == "link":
                href = html.escape(mattrs.get("href") or "")
                text = f'<a href="{href}" target="_blank" rel="noopener">{text}</a>'
            elif mtype == "code":
                text = f"<code>{text}</code>"
        return text
    children = "".join(prosemirror_to_html(c) for c in content)
    if ntype == "doc": return children
    if ntype == "paragraph": return f"<p>{children}</p>" if children else ""
    if ntype == "heading":
        level = max(1, min(6, attrs.get("level") or 2))
        return f"<h{level}>{children}</h{level}>"
    if ntype == "hard_break": return "<br/>"
    if ntype == "bullet_list": return f"<ul>{children}</ul>"
    if ntype == "ordered_list": return f"<ol>{children}</ol>"
    if ntype == "list_item": return f"<li>{children}</li>"
    if ntype == "horizontal_rule": return "<hr/>"
    if ntype == "blockquote": return f"<blockquote>{children}</blockquote>"
    if ntype == "image":
        src = html.escape(attrs.get("src") or "")
        return f'<img src="{src}" alt="" loading="lazy"/>'
    if ntype == "code_block": return f"<pre><code>{children}</code></pre>"
    return children


def latest_dump() -> Path:
    for pattern in ["luma_scored_*.json", "luma_raw_recursive_*_merged.json",
                    "luma_raw_recursive_*.json", "luma_raw_*.json"]:
        dumps = sorted(OUT_DIR.glob(pattern))
        if dumps:
            return dumps[-1]
    raise SystemExit("No event data found.")


def slim(records: list[dict]) -> list[dict]:
    out: list[dict] = []
    now_utc = datetime.now(timezone.utc)
    dropped = 0
    for r in records:
        ev = (r.get("list_entry") or {}).get("event") or {}
        det = r.get("detail") or {}
        det_ev = det.get("event") or {}

        # Drop past events: use end_at if available, else start_at + 3h buffer
        end_str = ev.get("end_at") or det_ev.get("end_at")
        start_str = ev.get("start_at") or det_ev.get("start_at")
        try:
            if end_str:
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                if end_dt < now_utc:
                    dropped += 1
                    continue
            elif start_str:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                if start_dt + timedelta(hours=3) < now_utc:
                    dropped += 1
                    continue
        except (ValueError, TypeError):
            pass

        cal = det.get("calendar") or {}
        geo = ev.get("geo_address_info") or det_ev.get("geo_address_info") or {}
        ti = det.get("ticket_info") or {}
        hosts = det.get("hosts") or []
        cats = [c.get("name") for c in (det.get("categories") or []) if c.get("name")]
        guests = det.get("featured_guests") or []
        url_slug = ev.get("url") or det_ev.get("url") or ""
        rsvp_url = f"https://lu.ma/{url_slug}" if url_slug else ""
        text_blob = " ".join([ev.get("name") or "", det_ev.get("name") or "",
                              cal.get("name") or "",
                              (det.get("event") or {}).get("description") or ""]).lower()
        derived_tags: list[str] = []
        if any(t in text_blob for t in ["design", "ux ", "ui ", "ux/", "ui/", "/ux", "/ui",
            "figma", "framer", "prototype", "wireframe", "design system", "product designer",
            "user experience", "user interface", "interaction design"]):
            derived_tags.append("Design")
        if any(t in text_blob for t in [" ai ", "a.i.", "agent", "llm", "ml ", "machine learning",
            "claude", "openai", "anthropic", "gpt", "gemini", "neural", "transformer", "rag ", "vector db"]):
            derived_tags.append("AI (keyword)")
        out.append({
            "id": ev.get("api_id"),
            "name": ev.get("name") or det_ev.get("name"),
            "start_at": ev.get("start_at") or det_ev.get("start_at"),
            "end_at": ev.get("end_at") or det_ev.get("end_at"),
            "timezone": ev.get("timezone") or det_ev.get("timezone"),
            "location_type": ev.get("location_type") or det_ev.get("location_type"),
            "city": geo.get("city") or "",
            "venue": geo.get("address") or "",
            "full_address": geo.get("full_address") or "",
            "cover_url": ev.get("cover_url") or "",
            "tint": det.get("tint_color") or "#7c3aed",
            "calendar_name": cal.get("name") or "",
            "calendar_avatar": cal.get("avatar_url") or "",
            "hosts": [{"name": h.get("name"), "avatar": h.get("avatar_url")} for h in hosts],
            "guest_count": det.get("guest_count", 0) or 0,
            "categories": cats + derived_tags,
            "description_html": prosemirror_to_html(det.get("description_mirror") or {}),
            "registration_availability": det.get("registration_availability"),
            "sold_out": det.get("sold_out", False),
            "waitlist_active": det.get("waitlist_active", False),
            "featured_guests": [{"name": g.get("name"), "avatar": g.get("avatar_url")}
                                for g in guests][:8],
            "url": rsvp_url,
            "scores": r.get("scores") or {},
            "prep": r.get("prep") or {},
        })
    if dropped:
        print(f"  dropped {dropped} past events", flush=True)
    return out



# ── CSS ───────────────────────────────────────────────────────────────────────
CSS = r"""
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700;800&family=Newsreader:ital,wght@0,400;0,600;1,400&display=swap');
:root{
  --bg:#eeede9;--surface:#ffffff;--s2:#f0eeea;--s3:#e4e0da;
  --border:#ddd9d3;--bh:#c5c0b8;
  --text:#1a1916;--t2:#4a4640;--t3:#7a7570;--t4:#a09b96;
  --accent:#e8620a;--ah:#c9520a;--abg:rgba(232,98,10,.07);--aborder:rgba(232,98,10,.2);
  --score:#d97706;--sbg:rgba(217,119,6,.08);--sglow:rgba(217,119,6,.15);
  --pos:#16a34a;--info:#0284c7;--danger:#dc2626;
  --img-filter:none;
  --rs:8px;--rl:16px;
  --eo:cubic-bezier(0.23,1,0.32,1);
  --fd:'Outfit',system-ui,sans-serif;
  --fb:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;
  --fi:'Newsreader',Georgia,serif;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{color-scheme:light;scroll-behavior:smooth}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms!important;transition-duration:.01ms!important;scroll-behavior:auto!important}}
body{font-family:var(--fb);background:var(--bg);color:var(--t2);line-height:1.55;min-height:100vh;-webkit-font-smoothing:antialiased;overflow-x:hidden;font-size:15px}
.wrap{max-width:1280px;margin:0 auto;padding:32px 40px}
@media(max-width:768px){.wrap{padding:20px 16px}}

/* Header */
header{margin-bottom:32px;padding-bottom:24px;border-bottom:2px solid var(--border)}
.header-row{display:flex;align-items:flex-end;justify-content:space-between;flex-wrap:wrap;gap:16px}
h1{font-family:var(--fi);font-size:44px;font-weight:400;font-style:normal;letter-spacing:-.02em;color:var(--text);line-height:1.05}
@media(max-width:640px){h1{font-size:28px}}
.sub{color:var(--t4);font-size:12px;font-weight:400;letter-spacing:.04em}
.summary{font-size:15px;color:var(--t2);margin:12px 0 0;line-height:1.5}
.summary strong{color:var(--text);font-weight:600}
.mode-toggle{display:flex;gap:2px;background:var(--s2);border:1px solid var(--border);border-radius:var(--rs);padding:3px}
.mode-btn{padding:8px 16px;font-size:12px;font-weight:500;color:var(--t3);background:transparent;border:none;border-radius:6px;cursor:pointer;transition:all .15s var(--eo);font-family:var(--fb)}
.mode-btn.active{background:var(--surface);color:var(--text);box-shadow:0 1px 4px rgba(0,0,0,.08)}
.mode-btn:active{transform:scale(.97)}
@media(hover:hover)and(pointer:fine){.mode-btn:hover:not(.active){color:var(--text)}}

/* Toolbar */
.toolbar{display:flex;gap:10px;align-items:center;margin:0 0 8px;flex-wrap:wrap}
.toolbar input,.toolbar select{background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:var(--rs);padding:10px 16px;font-size:13px;font-family:var(--fb);outline:none;transition:border-color .15s var(--eo),box-shadow .15s var(--eo)}
.toolbar input:focus,.toolbar select:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--abg)}
.toolbar input[type=search]{flex:1;min-width:200px}
.toolbar input::placeholder{color:var(--t4)}
.toolbar label{font-size:11px;color:var(--t4);font-weight:500;text-transform:uppercase;letter-spacing:.06em}
.toolbar select{cursor:pointer}
.filter-toggle{background:var(--surface);color:var(--t2);border:1px solid var(--border);border-radius:var(--rs);padding:10px 16px;font-size:13px;font-weight:500;cursor:pointer;font-family:var(--fb);transition:all .15s var(--eo)}
.filter-toggle:active{transform:scale(.97)}
@media(hover:hover)and(pointer:fine){.filter-toggle:hover{border-color:var(--bh)}}
.filter-toggle.has-active{color:var(--accent);border-color:var(--aborder);background:var(--abg)}
.filter-count{background:var(--accent);color:#fff;border-radius:999px;padding:1px 7px;font-size:10px;font-weight:700;margin-left:6px;display:none}
.filter-count.show{display:inline}
.filter-drawer{display:none;padding:16px 0 20px;border-bottom:1px solid var(--border);margin:0 0 24px}
.filter-drawer.open{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
.chips{display:flex;flex-wrap:wrap;gap:6px;width:100%}
.chip{background:var(--surface);color:var(--t3);border:1px solid var(--border);border-radius:999px;padding:7px 14px;font-size:12px;font-weight:500;cursor:pointer;user-select:none;transition:all .15s var(--eo)}
.chip:active{transform:scale(.96)}
@media(hover:hover)and(pointer:fine){.chip:hover{color:var(--text);border-color:var(--bh)}}
.chip.active{background:var(--text);color:var(--surface);border-color:var(--text)}
.person-chips{display:flex;gap:8px;align-items:center}
.person-chips .label{font-size:10px;color:var(--t4);text-transform:uppercase;letter-spacing:.12em;font-weight:600}
.person-chip{width:36px;height:36px;border-radius:50%;background:var(--surface);color:var(--t2);border:1.5px solid var(--border);font-size:14px;font-weight:700;font-family:var(--fd);cursor:pointer;user-select:none;transition:all .15s var(--eo);display:flex;align-items:center;justify-content:center;padding:0}
.person-chip:active{transform:scale(.92)}
@media(hover:hover)and(pointer:fine){.person-chip:hover{color:var(--text);border-color:var(--bh);background:var(--s2)}}
.person-chip.active{background:var(--accent);color:#fff;border-color:transparent;box-shadow:0 2px 8px rgba(232,98,10,.3)}
.date-range{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.date-range label{font-size:11px;color:var(--t4);font-weight:500}
.date-range input[type=date]{background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:var(--rs);padding:8px 12px;font-size:12px;font-family:var(--fb);outline:none;cursor:pointer;color-scheme:light}
.date-range input[type=date]:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--abg)}
.date-quick{display:flex;gap:4px}
.date-quick button{background:var(--surface);color:var(--t3);border:1px solid var(--border);border-radius:var(--rs);padding:8px 12px;font-size:12px;font-weight:500;cursor:pointer;transition:all .15s var(--eo);font-family:var(--fb)}
.date-quick button:active{transform:scale(.96)}
@media(hover:hover)and(pointer:fine){.date-quick button:hover{color:var(--text);border-color:var(--bh)}}
.date-quick button.active{background:var(--accent);color:#fff;border-color:transparent}
.score-slider{display:none;align-items:center;gap:12px;width:100%;padding:4px 0}
.score-slider.show{display:flex}
.score-slider label{font-size:11px;color:var(--score);font-weight:600;white-space:nowrap}
.score-slider input[type=range]{flex:1;height:4px;-webkit-appearance:none;appearance:none;background:var(--s3);border-radius:2px;outline:none;cursor:pointer}
.score-slider input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:22px;height:22px;border-radius:50%;background:var(--score);cursor:pointer;border:3px solid #fff;box-shadow:0 1px 6px var(--sglow)}
.score-slider input[type=range]::-moz-range-thumb{width:22px;height:22px;border-radius:50%;background:var(--score);cursor:pointer;border:3px solid #fff}
.score-slider .val{font-family:var(--fd);font-size:15px;font-weight:700;color:var(--score);min-width:28px;text-align:right}
.count{margin:0 0 16px;color:var(--t4);font-size:12px;font-weight:400}

/* Going */
.card.going{border-color:rgba(22,163,74,.3)}
.card.going .going-badge{display:flex}
.going-badge{display:none;position:absolute;top:12px;left:12px;z-index:4;background:var(--pos);color:#fff;font-size:9px;font-weight:700;padding:3px 10px;border-radius:999px;text-transform:uppercase;letter-spacing:.08em}
.going-btn{background:transparent;color:var(--pos);border:1px solid rgba(22,163,74,.3);padding:10px 20px;border-radius:var(--rs);font-size:13px;font-weight:500;cursor:pointer;transition:all .15s var(--eo);font-family:var(--fb);display:inline-flex;align-items:center;gap:6px}
.going-btn:active{transform:scale(.97)}
@media(hover:hover)and(pointer:fine){.going-btn:hover{border-color:var(--pos);background:rgba(22,163,74,.06)}}
.going-btn.active{background:var(--pos);color:#fff;border-color:transparent}
.cal-btn{background:transparent;color:var(--info);border:1px solid rgba(2,132,199,.25);padding:10px 20px;border-radius:var(--rs);font-size:13px;font-weight:500;cursor:pointer;transition:all .15s var(--eo);text-decoration:none;font-family:var(--fb);display:inline-flex;align-items:center;gap:6px}
.cal-btn:active{transform:scale(.97)}
@media(hover:hover)and(pointer:fine){.cal-btn:hover{border-color:var(--info);background:rgba(2,132,199,.06)}}
.my-events{margin:0 0 24px;padding:20px 24px;background:rgba(22,163,74,.05);border:1px solid rgba(22,163,74,.15);border-radius:var(--rl);display:none}
.my-events.show{display:block}
.my-events h3{font-family:var(--fd);font-size:11px;font-weight:700;color:var(--pos);text-transform:uppercase;letter-spacing:.1em;margin-bottom:12px}
.my-events-list{display:flex;flex-wrap:wrap;gap:8px}
.my-event-chip{background:var(--surface);color:var(--pos);border:1px solid rgba(22,163,74,.2);border-radius:var(--rs);padding:6px 14px;font-size:12px;font-weight:500;cursor:pointer;transition:all .15s var(--eo);display:flex;align-items:center;gap:8px}
@media(hover:hover)and(pointer:fine){.my-event-chip:hover{border-color:var(--pos)}}
.my-event-chip .remove{color:var(--t4);font-size:14px;cursor:pointer}
@media(hover:hover)and(pointer:fine){.my-event-chip .remove:hover{color:var(--danger)}}

/* Hero */
.hero{display:none;margin:0 0 40px;border-radius:var(--rl);overflow:hidden;position:relative;min-height:380px;cursor:pointer;background-color:var(--s3)}
.hero.show{display:block}
.hero img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;filter:var(--img-filter)}
.hero-overlay{position:absolute;inset:0;background:linear-gradient(to top,rgba(10,10,8,.88) 0%,rgba(10,10,8,.55) 45%,rgba(10,10,8,.1) 70%,transparent 100%);display:flex;align-items:flex-end;padding:40px}
@media(max-width:640px){.hero-overlay{padding:20px}.hero{min-height:260px}}
.hero-content{display:flex;align-items:flex-end;gap:24px;width:100%}
.hero-text{flex:1}
.hero-label{font-size:10px;color:var(--accent);text-transform:uppercase;letter-spacing:.15em;font-weight:700;margin-bottom:10px}
.hero-title{font-family:var(--fi);font-size:30px;font-weight:400;color:#fff;line-height:1.15;margin-bottom:10px}
@media(max-width:640px){.hero-title{font-size:22px}}
.hero-meta{font-size:13px;color:rgba(255,255,255,.65)}
.hero-ring{flex-shrink:0}

/* Score ring */
.ring-wrap{position:relative;display:inline-flex;align-items:center;justify-content:center}
.ring-val{position:absolute;font-family:var(--fd);font-weight:800;color:#fff;text-shadow:0 1px 2px rgba(0,0,0,.4)}
.ring-sm .ring-val{font-size:13px}
.ring-lg .ring-val{font-size:20px}
/* Light ring on card (dark bg ring backdrop) */
.cover .ring-wrap .ring-val{color:#fff}

/* Section headers — uppercase timeline divider */
.section-header{font-family:var(--fb);font-size:11px;font-weight:700;color:var(--t4);margin:40px 0 16px;letter-spacing:.12em;text-transform:uppercase;display:flex;align-items:center;gap:12px}
.section-header::after{content:'';flex:1;height:1px;background:var(--border)}
.section{opacity:0;transform:translateY(12px);transition:opacity .4s var(--eo),transform .4s var(--eo);content-visibility:auto;contain-intrinsic-size:auto 600px}
.section.visible{opacity:1;transform:translateY(0)}
.section .grid .card{opacity:0;transform:translateY(8px);transition:opacity .35s var(--eo),transform .35s var(--eo)}
.section.visible .grid .card{opacity:1;transform:translateY(0);will-change:auto}

/* Card grid */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
@media(max-width:768px){.grid{grid-template-columns:1fr;gap:10px}}
/* Equal size cards — no more first-child hero */
.grid .card{background:var(--surface);border:1px solid var(--border);border-radius:var(--rl);overflow:hidden;cursor:pointer;display:flex;flex-direction:column;height:280px;transition:border-color .2s var(--eo),box-shadow .2s var(--eo),transform .2s var(--eo);contain:layout style}
.section[data-period="today"] .grid{display:flex;gap:14px;overflow-x:auto;scroll-snap-type:x mandatory;padding-bottom:8px;-webkit-overflow-scrolling:touch}
.section[data-period="today"] .grid .card{background:var(--surface);border:1px solid var(--border);border-radius:var(--rl);overflow:hidden;cursor:pointer;display:flex;flex-direction:column;height:280px;transition:border-color .2s var(--eo),box-shadow .2s var(--eo),transform .2s var(--eo);contain:layout style}
@media(max-width:768px){.section[data-period="today"] .grid .card{flex:0 0 85vw}}
/* Remove asymmetric tomorrow layout */
.section[data-period="month"] .grid,.section[data-period="later"] .grid{grid-template-columns:1fr;gap:8px}
.section[data-period="month"] .grid .card,.section[data-period="later"] .grid .card{height:auto;min-height:88px;flex-direction:row;border-radius:var(--rs)}
.section[data-period="month"] .grid .card .cover,.section[data-period="later"] .grid .card .cover{position:relative;width:130px;min-width:130px;height:auto;border-radius:var(--rs) 0 0 var(--rs)}
.section[data-period="month"] .grid .card .body,.section[data-period="later"] .grid .card .body{position:relative;background:none;padding:14px 16px;display:flex;flex-direction:column;gap:4px}
.section[data-period="month"] .grid .card .title,.section[data-period="later"] .grid .card .title{font-size:14px;font-weight:600;color:var(--text);text-shadow:none}
.section[data-period="month"] .grid .card .meta,.section[data-period="later"] .grid .card .meta{color:var(--t3);font-size:12px}
.section[data-period="month"] .grid .card .rsvp-count,.section[data-period="later"] .grid .card .rsvp-count{color:var(--t4)}
.section[data-period="month"] .grid .card .going-badge,.section[data-period="later"] .grid .card .going-badge{top:8px;left:8px}

/* Cards — grayscale images */
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--rl);overflow:hidden;cursor:pointer;display:flex;flex-direction:column;position:relative;height:380px;transition:border-color .2s var(--eo),box-shadow .2s var(--eo),transform .2s var(--eo);contain:layout style}
.card:active{transform:scale(.99)}
@media(hover:hover)and(pointer:fine){.card:hover{border-color:var(--bh);box-shadow:0 6px 24px rgba(0,0,0,.08);transform:translateY(-2px)}}
.cover{position:relative;height:58%;background-color:var(--s3);overflow:hidden;flex-shrink:0}
.cover img{width:100%;height:100%;object-fit:cover;display:block}

.cover .ring-wrap{position:absolute;top:10px;left:10px;z-index:3;background:rgba(255,255,255,.95);border-radius:50%;padding:4px;box-shadow:0 2px 6px rgba(0,0,0,.12)}
.badges{position:absolute;top:10px;right:10px;display:flex;gap:5px;z-index:2}
.badge{background:rgba(255,255,255,.95);color:var(--text);font-size:9px;font-weight:700;padding:4px 10px;border-radius:999px;text-transform:uppercase;letter-spacing:.06em;box-shadow:0 1px 4px rgba(0,0,0,.1)}
.badge.sold{background:var(--danger);color:#fff}.badge.waitlist{background:#f97316;color:#fff}.badge.open{background:var(--pos);color:#fff}
.body{position:relative;flex:1;padding:12px 14px;display:flex;flex-direction:column;gap:4px;background:var(--surface)}
.title{font-family:var(--fd);font-size:14px;font-weight:700;color:var(--text);line-height:1.3;text-shadow:none}
.meta{display:flex;flex-direction:column;gap:2px;font-size:11px;color:var(--t3)}
.meta .row{display:flex;align-items:center;gap:5px}
.icon{width:13px;height:13px;opacity:.6;flex-shrink:0}
.host{display:none}.cats{display:none}
.rsvp-strip{display:flex;justify-content:space-between;align-items:center;font-size:11px;margin-top:auto;padding-top:4px}
.rsvp-count{color:var(--t4);font-weight:500}
.rsvp-count span{color:var(--t4);font-weight:400}

/* Swipe */
.swipe-container{display:none;flex-direction:column;align-items:center;padding:40px 0;min-height:70vh}
.swipe-container.show{display:flex}
.swipe-stack{position:relative;width:360px;height:520px;max-width:90vw}
@media(max-width:400px){.swipe-stack{width:300px;height:460px}}
.swipe-card{position:absolute;inset:0;border-radius:var(--rl);overflow:hidden;background:var(--surface);border:1px solid var(--border);cursor:grab;touch-action:none;user-select:none;will-change:transform;box-shadow:0 4px 24px rgba(0,0,0,.08)}
.swipe-card:active{cursor:grabbing}
.swipe-card .s-cover{height:58%;background-size:cover;background-position:center;position:relative;overflow:hidden}
.swipe-card .s-cover img{filter:var(--img-filter)}
.swipe-card .s-body{padding:20px;display:flex;flex-direction:column;gap:8px;height:42%;overflow:hidden}
.swipe-card .s-title{font-family:var(--fd);font-size:20px;font-weight:700;color:var(--text);line-height:1.2}
.swipe-card .s-meta{font-size:13px;color:var(--t2)}
.swipe-card .s-host{font-size:12px;color:var(--t4);margin-top:auto}
.swipe-flash{position:absolute;top:20px;border-radius:var(--rs);padding:8px 18px;font-family:var(--fd);font-size:22px;font-weight:800;text-transform:uppercase;letter-spacing:.06em;opacity:0;transition:opacity .15s var(--eo);pointer-events:none;z-index:10}
.swipe-flash.like{right:20px;color:var(--pos);border:3px solid var(--pos);transform:rotate(12deg);background:rgba(255,255,255,.9)}
.swipe-flash.nope{left:20px;color:var(--danger);border:3px solid var(--danger);transform:rotate(-12deg);background:rgba(255,255,255,.9)}
.swipe-actions{display:flex;gap:16px;margin-top:28px}
.swipe-btn{width:52px;height:52px;border-radius:50%;display:flex;align-items:center;justify-content:center;border:1.5px solid var(--border);background:var(--surface);cursor:pointer;font-size:20px;transition:all .15s var(--eo);color:var(--t3);box-shadow:0 2px 8px rgba(0,0,0,.06)}
.swipe-btn:active{transform:scale(.9)}
@media(hover:hover)and(pointer:fine){.swipe-btn:hover{border-color:var(--bh);color:var(--text)}}
.swipe-btn.skip{color:var(--danger)}
.swipe-btn.like-btn{color:var(--pos)}
.swipe-btn.undo-btn{color:var(--score);font-size:15px}
.swipe-counter{margin-top:16px;font-size:12px;color:var(--t4)}
.picks-bar{margin-top:24px;width:100%;max-width:400px}
.picks-label{font-size:11px;color:var(--t4);text-transform:uppercase;letter-spacing:.1em;font-weight:600;margin-bottom:8px}
.picks-list{display:flex;flex-wrap:wrap;gap:6px}
.pick-chip{background:var(--surface);color:var(--pos);border:1px solid rgba(22,163,74,.2);border-radius:var(--rs);padding:6px 12px;font-size:11px;font-weight:500;cursor:pointer;transition:all .15s var(--eo)}
@media(hover:hover)and(pointer:fine){.pick-chip:hover{border-color:var(--pos)}}

/* Modal */
.modal-bg{display:none;position:fixed;inset:0;background:rgba(26,25,22,.55);backdrop-filter:blur(4px);z-index:100;align-items:flex-start;justify-content:center;padding:48px 24px;overflow-y:auto}
.modal-bg.open{display:flex}
@media(max-width:640px){.modal-bg{padding:0;align-items:flex-end}.modal-bg .modal-wrap{max-width:100%}.modal-bg .modal{border-radius:var(--rl) var(--rl) 0 0;max-height:92vh;overflow-y:auto}}
.modal-wrap{position:relative;width:100%;max-width:700px}
.modal{background:var(--surface);border:1px solid var(--border);border-radius:var(--rl);width:100%;overflow:hidden;box-shadow:0 24px 80px rgba(0,0,0,.12)}
.modal .cover{position:relative;height:260px;overflow:hidden;background-color:var(--s3)}
.modal .cover img{width:100%;height:100%;object-fit:cover;display:block}
@media(max-width:640px){.modal .cover{height:200px}}
.modal-body{padding:28px}
@media(max-width:640px){.modal-body{padding:20px}}
.modal h2{font-family:var(--fi);font-size:26px;font-weight:400;color:var(--text);line-height:1.2;margin-bottom:14px}
.modal .meta{font-size:13px;color:var(--t2);margin-bottom:16px}
.modal-scores{display:flex;gap:12px;margin:16px 0;padding:16px;background:var(--s2);border:1px solid var(--border);border-radius:var(--rl)}
.modal-score-item{flex:1;text-align:center}
.modal-score-item .name{font-size:10px;color:var(--t4);text-transform:uppercase;letter-spacing:.12em;font-weight:700;margin-bottom:6px}
.modal-score-item .why{font-size:11px;color:var(--t3);margin-top:6px;line-height:1.5}
.modal .desc{color:var(--t2);font-size:14px;line-height:1.65;margin:20px 0;max-height:360px;overflow-y:auto;scrollbar-width:thin;scrollbar-color:var(--border) transparent}
.modal .desc p{margin-bottom:10px}.modal .desc a{color:var(--accent);text-decoration:underline;text-underline-offset:2px}
@media(hover:hover)and(pointer:fine){.modal .desc a:hover{color:var(--ah)}}
.modal .desc img{max-width:100%;border-radius:var(--rs);margin:10px 0;filter:var(--img-filter)}
.modal .desc h2,.modal .desc h3{font-family:var(--fd);color:var(--text);margin:16px 0 6px;font-weight:600}
.modal .desc ul,.modal .desc ol{padding-left:18px;margin-bottom:10px}.modal .desc li{margin-bottom:3px}
.modal .desc hr{border:none;border-top:1px solid var(--border);margin:16px 0}
.modal .actions{display:flex;gap:10px;margin-top:20px;flex-wrap:wrap}
.btn{display:inline-flex;align-items:center;gap:6px;background:var(--accent);color:#fff;padding:11px 22px;border-radius:var(--rs);text-decoration:none;font-size:13px;font-weight:600;border:none;cursor:pointer;transition:all .15s var(--eo);font-family:var(--fb)}
.btn:active{transform:scale(.97)}
@media(hover:hover)and(pointer:fine){.btn:hover{background:var(--ah)}}
.btn.ghost{background:transparent;border:1.5px solid var(--border);color:var(--t2)}
.btn.ghost:active{transform:scale(.97)}
@media(hover:hover)and(pointer:fine){.btn.ghost:hover{border-color:var(--bh);color:var(--text)}}
.guests{display:flex;margin:10px 0}.guests img{width:28px;height:28px;border-radius:50%;border:2px solid var(--surface);margin-left:-6px;object-fit:cover;filter:grayscale(40%)}.guests img:first-child{margin-left:0}
.close{position:absolute;top:12px;right:12px;background:rgba(255,255,255,.92);color:var(--text);width:36px;height:36px;border-radius:50%;border:1px solid var(--border);cursor:pointer;font-size:16px;z-index:2;display:flex;align-items:center;justify-content:center;transition:all .15s var(--eo);box-shadow:0 1px 6px rgba(0,0,0,.08)}
.close:active{transform:scale(.9)}
@media(hover:hover)and(pointer:fine){.close:hover{background:var(--danger);color:#fff;border-color:var(--danger)}}

/* Prep */
/* Layout toggle buttons */
.layout-toggle{display:flex;gap:2px;background:var(--s2);border:1px solid var(--border);border-radius:var(--rs);padding:3px}
.layout-btn{width:36px;height:36px;display:flex;align-items:center;justify-content:center;background:transparent;border:none;border-radius:6px;cursor:pointer;color:var(--t3);transition:all .15s var(--eo)}
.layout-btn.active{background:var(--surface);color:var(--text);box-shadow:0 1px 4px rgba(0,0,0,.08)}
@media(hover:hover)and(pointer:fine){.layout-btn:hover:not(.active){color:var(--text)}}

/* List card (Option 1) */
.card-list{height:auto!important;min-height:88px;flex-direction:row;border-radius:var(--rs)!important}
.list-thumb{position:relative;width:110px;min-width:110px;border-radius:var(--rs) 0 0 var(--rs);overflow:hidden;background:var(--s3);flex-shrink:0}
.list-thumb::after{content:'';position:absolute;inset:0;background:linear-gradient(135deg,rgba(0,0,0,.25) 0%,transparent 60%);pointer-events:none}
.list-thumb img{width:100%;height:100%;object-fit:cover;display:block}
.list-thumb .badge{position:absolute;top:8px;left:8px;font-size:8px;padding:3px 7px}
.list-thumb .going-badge{position:absolute;bottom:6px;left:6px;font-size:8px;padding:2px 8px}
.list-body{flex:1;padding:14px 16px;display:flex;flex-direction:column;gap:5px;min-width:0}
.list-title{font-family:var(--fd);font-size:14px;font-weight:600;color:var(--text);line-height:1.3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.list-meta{display:flex;align-items:center;gap:5px;font-size:12px;color:var(--t3)}
.list-meta svg{width:12px;height:12px;opacity:.5;flex-shrink:0}
.list-foot{display:flex;align-items:center;gap:10px;margin-top:auto}
.list-host{font-size:11px;color:var(--t4);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.list-score{font-family:var(--fd);font-size:13px;font-weight:700}
.list-rsvps{font-size:11px;color:var(--t4);white-space:nowrap}
.grid-list{grid-template-columns:1fr!important;gap:0!important}
.grid-list .card:first-child,.grid-list .card:nth-child(2),.grid-list .card:nth-child(3),.grid-list .card:nth-child(n+4){height:auto!important;grid-column:1!important}
.grid-list .card:first-child .list-title{font-size:14px!important;font-family:var(--fd)!important}
.section[data-period="today"] .grid-list,.section[data-period="tomorrow"] .grid-list{display:grid!important;overflow-x:visible!important;grid-template-columns:1fr!important}
.section[data-period="tomorrow"] .grid-list .card:first-child{grid-row:auto!important}

/* Timeline container for list mode */
.timeline-group{display:flex;gap:0;margin-bottom:8px}
.timeline-left{width:64px;flex-shrink:0;display:flex;flex-direction:column;align-items:center;padding-top:20px}
.timeline-date{font-family:var(--fd);font-size:11px;font-weight:700;color:var(--text);text-align:center;line-height:1.2;white-space:nowrap}
.timeline-day{font-family:var(--fi);font-size:18px;font-weight:400;color:var(--accent);line-height:1}
.timeline-dot{width:7px;height:7px;border-radius:50%;background:var(--border);border:1.5px solid var(--bh);margin:6px 0;flex-shrink:0}
.timeline-line{flex:1;width:1.5px;background:repeating-linear-gradient(to bottom,var(--border) 0,var(--border) 4px,transparent 4px,transparent 8px);min-height:20px}
.timeline-events{flex:1;display:flex;flex-direction:column;gap:8px;padding-bottom:8px}

.prep-section{margin:20px 0;padding:20px;background:var(--s2);border:1px solid var(--border);border-radius:var(--rl)}
.prep-section h3{font-family:var(--fd);font-size:10px;font-weight:700;color:var(--accent);text-transform:uppercase;letter-spacing:.12em;margin-bottom:12px}
.prep-news{list-style:none;padding:0;margin:0 0 16px}
.prep-news li{font-size:13px;color:var(--t2);line-height:1.6;padding:7px 0 7px 16px;position:relative;border-bottom:1px solid var(--border)}
.prep-news li::before{content:'';position:absolute;left:0;top:13px;width:5px;height:5px;border-radius:50%;background:var(--accent)}
.prep-news li:last-child{border-bottom:none}
.prep-starters{list-style:none;padding:0;margin:0}
.prep-starters li{font-size:13px;color:var(--text);line-height:1.6;padding:10px 14px;margin-bottom:6px;background:var(--surface);border:1.5px solid var(--border);border-radius:var(--rs);transition:border-color .15s var(--eo)}
@media(hover:hover)and(pointer:fine){.prep-starters li:hover{border-color:var(--accent)}}

::-webkit-scrollbar{width:5px;height:5px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}::-webkit-scrollbar-thumb:hover{background:var(--bh)}
.cmd-bg{display:none;position:fixed;inset:0;background:rgba(26,25,22,.45);backdrop-filter:blur(4px);z-index:150;align-items:flex-start;justify-content:center;padding:16vh 24px 24px}
.cmd-bg.open{display:flex}
.cmd-box{width:100%;max-width:540px;background:var(--surface);border:1px solid var(--border);border-radius:var(--rl);overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,.1)}
.cmd-input{width:100%;background:transparent;border:none;border-bottom:1px solid var(--border);padding:16px 20px;font-size:15px;color:var(--text);font-family:var(--fb);outline:none}
.cmd-input::placeholder{color:var(--t4)}
.cmd-results{max-height:340px;overflow-y:auto;padding:6px}
.cmd-item{display:flex;align-items:center;gap:12px;padding:10px 14px;border-radius:var(--rs);cursor:pointer;transition:background .12s var(--eo)}
.cmd-item:active{transform:scale(.99)}
@media(hover:hover)and(pointer:fine){.cmd-item:hover{background:var(--s2)}}
.cmd-item.active{background:var(--s2)}
.cmd-item .ci-title{font-size:14px;font-weight:500;color:var(--text)}
.cmd-item .ci-meta{font-size:11px;color:var(--t4)}
"""

# ── JS ────────────────────────────────────────────────────────────────────────
JS = r"""
const E=window.__EVENTS__,S=s=>document.querySelector(s),SA=s=>document.querySelectorAll(s);
let mode='discover',activePerson=null,picks=[],undoStack=[],scoreThreshold=0,cardLayout='overlay';
const cityF=new Set(),catF=new Set(),statusF=new Set();
const isMobile='ontouchstart'in window&&innerWidth<768;
let debounceTimer=null;
const debounce=(fn,ms)=>(...a)=>{clearTimeout(debounceTimer);debounceTimer=setTimeout(()=>fn(...a),ms)};

const PST=-8,PDT=-7,TZ_OFF=PDT;
function toPST(d){return new Date(d.getTime()+(d.getTimezoneOffset()+TZ_OFF*60)*60000)}
const fD=s=>{if(!s)return'?';const d=new Date(s);return d.toLocaleString('en-US',{timeZone:'America/Los_Angeles',weekday:'short',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})};
const relDay=s=>{if(!s)return'later';const d=toPST(new Date(s)),n=toPST(new Date());const ed=new Date(d.getFullYear(),d.getMonth(),d.getDate()),td=new Date(n.getFullYear(),n.getMonth(),n.getDate());const diff=Math.round((ed-td)/864e5);if(diff<0)return'past';if(diff===0)return'today';if(diff===1)return'tomorrow';if(diff<=7)return'week';if(diff<=30)return'month';return'later'};
const st=e=>e.sold_out?'sold':e.waitlist_active?'waitlist':'open';
const stL=s=>({sold:'Sold out',waitlist:'Waitlist',open:'Open'})[s];
const scC=n=>n>=80?'#2db87a':n>=50?'#d97706':'#3a3a38';
const gS=(e,p)=>((e.scores||{})[p]||{}).score||0;
const esc=s=>s==null?'':String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);

function ring(score,size){
  const r=size==='lg'?40:20,sw=size==='lg'?6:4,circ=2*Math.PI*r;
  const off=circ*(1-score/100),col=scC(score);
  return `<div class="ring-wrap ring-${size}"><svg width="${(r+sw)*2}" height="${(r+sw)*2}" style="transform:rotate(-90deg)"><circle cx="${r+sw}" cy="${r+sw}" r="${r}" fill="none" stroke="rgba(255,255,255,.08)" stroke-width="${sw}"/><circle cx="${r+sw}" cy="${r+sw}" r="${r}" fill="none" stroke="${col}" stroke-width="${sw}" stroke-linecap="round" stroke-dasharray="${circ}" stroke-dashoffset="${off}" style="transition:stroke-dashoffset 1s var(--eo)"/></svg><span class="ring-val">${score}</span></div>`;
}
function iconCal(){return '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>'}
function iconPin(){return '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0118 0z"/><circle cx="12" cy="10" r="3"/></svg>'}

function buildCard(e){
  if(cardLayout==='list') return buildCardList(e);
  return buildCardOverlay(e);
}

function buildCardOverlay(e){
  // Option 2: full image, gradient fade, text at bottom
  const s=st(e),sc=activePerson?gS(e,activePerson):0;
  const covImg=e.cover_url?`<img src="${esc(e.cover_url)}" loading="lazy" decoding="async" alt="" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover"/>`:'';
  const covBg=`style="background-color:${esc(e.tint)}"`;
  const hn=e.calendar_name||(e.hosts[0]||{}).name||'';
  const ringPh=activePerson?`<div class="ring-placeholder" data-score="${sc}" style="position:absolute;top:12px;left:12px;z-index:3;width:42px;height:42px"></div>`:'';
  const isGoing=goingSet.has(e.id);
  return `<div class="card${isGoing?' going':''}" data-id="${esc(e.id)}" tabindex="0"><div class="cover" ${covBg}>${covImg}${ringPh}<div class="badges"><span class="badge ${s}">${stL(s)}</span></div><div class="going-badge">Going</div></div><div class="body"><div class="title">${esc(e.name||'?')}</div><div class="meta"><div class="row">${iconCal()} ${esc(fD(e.start_at))}</div><div class="row">${iconPin()} ${esc(e.venue||e.city||'?')}${e.city&&e.venue?', '+esc(e.city):''}</div></div><div class="rsvp-strip"><span class="rsvp-count">${(e.guest_count||0).toLocaleString()} <span>RSVPs</span></span></div></div></div>`;
}

function buildCardList(e){
  // Option 1: thumbnail left, text right
  const s=st(e),sc=activePerson?gS(e,activePerson):0;
  const covImg=e.cover_url?`<img src="${esc(e.cover_url)}" loading="lazy" decoding="async" alt="" style="width:100%;height:100%;object-fit:cover"/>`:'';
  const hn=e.calendar_name||(e.hosts[0]||{}).name||'';
  const isGoing=goingSet.has(e.id);
  const scoreTag=activePerson&&sc?`<span class="list-score" style="color:${scC(sc)}">${sc}</span>`:'';
  return `<div class="card card-list${isGoing?' going':''}" data-id="${esc(e.id)}" tabindex="0"><div class="list-thumb" style="background-color:${esc(e.tint)}">${covImg}<span class="badge ${s}">${stL(s)}</span>${isGoing?'<span class="going-badge">Going</span>':''}</div><div class="list-body"><div class="list-title">${esc(e.name||'?')}</div><div class="list-meta">${iconCal()} ${esc(fD(e.start_at))}</div><div class="list-meta">${iconPin()} ${esc(e.venue||e.city||'?')}${e.city&&e.venue?', '+esc(e.city):''}</div><div class="list-foot"><span class="list-host">by ${esc(hn)}</span>${scoreTag}<span class="list-rsvps">${(e.guest_count||0).toLocaleString()} RSVPs</span></div></div></div>`;
}

/* Summary */
function renderSummary(){
  const today=E.filter(e=>relDay(e.start_at)==='today').length;
  const tmrw=E.filter(e=>relDay(e.start_at)==='tomorrow').length;
  const el=S('#summary');
  if(!el)return;
  let txt=`<strong>${E.length} events</strong> in the Bay Area`;
  if(today)txt+=`. <strong>${today}</strong> happening today`;
  if(tmrw)txt+=`, <strong>${tmrw}</strong> tomorrow`;
  el.innerHTML=txt;
}

/* Hero */
function renderHero(){
  const h=S('#hero');
  if(!activePerson){h.classList.remove('show');return}
  const sorted=[...E].sort((a,b)=>gS(b,activePerson)-gS(a,activePerson));
  const e=sorted[0];if(!e||gS(e,activePerson)<40){h.classList.remove('show');return}
  const sc=gS(e,activePerson);
  h.style.backgroundImage=e.cover_url?`url('${e.cover_url}')`:'none';
  h.style.backgroundColor=e.tint;
  h.innerHTML=`<div class="hero-overlay"><div class="hero-content"><div class="hero-text"><div class="hero-label">Top pick for ${activePerson}</div><div class="hero-title">${esc(e.name)}</div><div class="hero-meta">${esc(fD(e.start_at))} · ${esc(e.city||'?')} · ${(e.guest_count||0).toLocaleString()} RSVPs</div></div><div class="hero-ring">${ring(sc,'lg')}</div></div></div>`;
  h.classList.add('show');
  h.onclick=()=>openModal(e.id);
}

/* Section observer */
const sectionObs=new IntersectionObserver(entries=>{
  entries.forEach(entry=>{
    if(entry.isIntersecting){
      // Hydrate section if it has deferred content
      const render=entry.target._deferredRender;
      if(render){
        const container=entry.target.querySelector('.grid,.section-header+div');
        if(container)container.outerHTML=render();
        else entry.target.insertAdjacentHTML('beforeend',render());
        delete entry.target._deferredRender;
      }
      entry.target.classList.add('visible');
      const cards=entry.target.querySelectorAll('.card');
      cards.forEach((c,i)=>{c.style.transitionDelay=`${Math.min(i*50,400)}ms`});
      entry.target.querySelectorAll('.ring-placeholder').forEach(ph=>{
        const sc=parseInt(ph.dataset.score)||0;ph.innerHTML=ring(sc,'sm');ph.classList.remove('ring-placeholder');
      });
      // Click handlers attached via event delegation on #grid (see init)
      sectionObs.unobserve(entry.target);
    }
  });
},{rootMargin:'200px 0px',threshold:0.01});

/* Discover */
function filterAndRender(){
  let list=E.slice();
  const q=(S('#search').value||'').toLowerCase().trim();
  if(q)list=list.filter(e=>(e.name||'').toLowerCase().includes(q)||(e.calendar_name||'').toLowerCase().includes(q)||(e.city||'').toLowerCase().includes(q));
  if(cityF.size)list=list.filter(e=>cityF.has(e.city||'TBD'));
  if(catF.size)list=list.filter(e=>(e.categories||[]).some(c=>catF.has(c)));
  if(statusF.size)list=list.filter(e=>statusF.has(st(e)));
  const df=S('#dateFrom').value,dt=S('#dateTo').value;
  if(df){const from=new Date(df+'T00:00:00');list=list.filter(e=>{const d=new Date(e.start_at);return d>=from})}
  if(dt){const to=new Date(dt+'T23:59:59');list=list.filter(e=>{const d=new Date(e.start_at);return d<=to})}
  if(activePerson&&scoreThreshold>0)list=list.filter(e=>gS(e,activePerson)>=scoreThreshold);
  const sv=S('#sort').value;
  if(sv==='date')list.sort((a,b)=>(a.start_at||'').localeCompare(b.start_at||''));
  else if(sv==='rsvps')list.sort((a,b)=>(b.guest_count||0)-(a.guest_count||0));
  else if(sv==='name')list.sort((a,b)=>(a.name||'').localeCompare(b.name||''));
  else list.sort((a,b)=>gS(b,sv)-gS(a,sv));

  const grid=S('#grid');
  const groups={today:[],tomorrow:[],week:[],month:[],later:[]};
  list.forEach(e=>{const g=relDay(e.start_at);(groups[g]||groups.later).push(e)});
  const labels={today:'Happening Today',tomorrow:'Tomorrow',week:'This Week',month:'This Month',later:'Later'};
  const frag=document.createDocumentFragment();
  let sectionIdx=0;

  function buildGridContent(evts){
    if(cardLayout==='list') return buildTimelineHtml(evts);
    return `<div class="grid">${evts.map(buildCard).join('')}</div>`;
  }

  function buildTimelineHtml(evts){
    // Group by date
    const byDate={};
    evts.forEach(e=>{
      const d=toPST(new Date(e.start_at||''));
      const key=`${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
      const label=d.toLocaleDateString('en-US',{timeZone:'America/Los_Angeles',weekday:'short',month:'short',day:'numeric'});
      if(!byDate[key])byDate[key]={label,events:[]};
      byDate[key].events.push(e);
    });
    return Object.values(byDate).map((group,gi,arr)=>{
      const parts=group.label.split(' ');
      const weekday=parts[0],month=parts[1],day=parts[2];
      const hasMore=gi<arr.length-1;
      return `<div class="timeline-group">
        <div class="timeline-left">
          <div class="timeline-date"><span style="font-size:10px;color:var(--t3);font-weight:600;text-transform:uppercase;letter-spacing:.06em">${weekday}</span><br/><span style="font-size:12px;color:var(--t2);font-weight:600">${month} ${day}</span></div>
          <div class="timeline-dot"></div>
          ${hasMore?'<div class="timeline-line"></div>':''}
        </div>
        <div class="timeline-events">${group.events.map(buildCard).join('')}</div>
      </div>`;
    }).join('');
  }

  for(const[k,lbl]of Object.entries(labels)){
    const evts=groups[k];if(!evts||!evts.length)continue;
    const section=document.createElement('div');
    section.className='section';
    section.dataset.period=k;
    const headerHtml=`<div class="section-header">${lbl} <span style="color:var(--t4);font-weight:400;font-size:13px">(${evts.length})</span></div>`;
    if(sectionIdx<2){
      section.innerHTML=headerHtml+buildGridContent(evts);
    }else{
      section.innerHTML=headerHtml+(cardLayout==='list'?'<div></div>':'<div class="grid"></div>');
      section._deferredEvts=evts;
      section._deferredRender=()=>buildGridContent(evts);
    }
    frag.appendChild(section);
    requestAnimationFrame(()=>sectionObs.observe(section));
    sectionIdx++;
  }
  grid.innerHTML='';
  grid.appendChild(frag);
  S('#count').textContent=`${list.length} of ${E.length} events`;
  renderHero();
}

/* Modal */
function scoresHtml(e){
  const sc=e.scores||{};const names=Object.keys(sc);if(!names.length)return'';
  return `<div class="modal-scores">${names.map(n=>{const s=sc[n]||{};const v=s.score||0;return `<div class="modal-score-item"><div class="name">${esc(n)}</div>${ring(v,'sm')}<div class="why">${esc(s.reason||'')}</div></div>`}).join('')}</div>`;
}
function prepHtml(e){
  const p=e.prep||{};const news=p.news||[];const st=p.starters||[];
  if(!news.length&&!st.length)return'';
  let h='';
  if(news.length)h+=`<div class="prep-section"><h3>Latest on this topic</h3><ul class="prep-news">${news.map(n=>`<li>${esc(n)}</li>`).join('')}</ul></div>`;
  if(st.length)h+=`<div class="prep-section"><h3>Conversation starters</h3><ul class="prep-starters">${st.map(s=>`<li>${esc(s)}</li>`).join('')}</ul></div>`;
  return h;
}
function openModal(id){
  const e=E.find(x=>x.id===id);if(!e)return;
  const s=st(e);
  const modalCovBg=`style="background-color:${esc(e.tint)}"`;
  const modalCovImg=e.cover_url?`<img src="${esc(e.cover_url)}" decoding="async" alt="" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover"/>`:'';
  const guests=(e.featured_guests||[]).map(g=>`<img src="${esc(g.avatar||'')}" title="${esc(g.name||'')}" alt=""/>`).join('');
  const desc=e.description_html||'<p style="color:var(--t4)">No description.</p>';
  S('#modal').innerHTML=`<div class="modal-wrap"><button class="close" onclick="closeModal()">&#215;</button><div class="modal"><div class="cover" ${modalCovBg}>${modalCovImg}<div class="badges"><span class="badge ${s}">${stL(s)}</span></div></div><div class="modal-body"><h2>${esc(e.name||'?')}</h2><div class="meta"><div class="row">${iconCal()} ${esc(fD(e.start_at))}</div><div class="row">${iconPin()} ${esc(e.full_address||e.venue||e.city||'')}</div></div>${scoresHtml(e)}${prepHtml(e)}${guests?`<div class="guests">${guests}</div>`:''}<div class="desc">${desc}</div><div class="actions"><button class="going-btn${goingSet.has(e.id)?' active':''}" id="modalGoingBtn" data-id="${esc(e.id)}" onclick="toggleGoing('${esc(e.id)}')">${goingSet.has(e.id)?'Going':'Mark as Going'}</button><a class="cal-btn" href="${esc(gcalUrl(e))}" target="_blank" rel="noopener" onclick="if(!goingSet.has('${esc(e.id)}'))toggleGoing('${esc(e.id)}')">Add to Calendar</a><a class="btn" href="${esc(e.url)}" target="_blank" rel="noopener">RSVP on Luma</a><button class="btn ghost" onclick="closeModal()">Close</button></div></div></div></div>`;
  S('#modalBg').classList.add('open');document.body.style.overflow='hidden';
}
function closeModal(){S('#modalBg').classList.remove('open');document.body.style.overflow=''}

/* Going / Calendar */
const GOING_KEY='luma_going_events';
let goingSet=new Set(JSON.parse(localStorage.getItem(GOING_KEY)||'[]'));
function saveGoing(){localStorage.setItem(GOING_KEY,JSON.stringify([...goingSet]))}
function toggleGoing(id,evt){
  if(evt)evt.stopPropagation();
  if(goingSet.has(id))goingSet.delete(id);else goingSet.add(id);
  saveGoing();renderMyEvents();
  SA(`.card[data-id="${id}"]`).forEach(c=>c.classList.toggle('going',goingSet.has(id)));
  const btn=S('#modalGoingBtn');if(btn&&btn.dataset.id===id){btn.classList.toggle('active',goingSet.has(id));btn.textContent=goingSet.has(id)?'Going':'Mark as Going'}
}
function gcalUrl(e){
  const start=(e.start_at||'').replace(/[-:]/g,'').replace(/\.\d{3}/,'');
  const end=(e.end_at||e.start_at||'').replace(/[-:]/g,'').replace(/\.\d{3}/,'');
  const title=encodeURIComponent(e.name||'Event');
  const loc=encodeURIComponent(e.full_address||e.venue||(e.city?e.city+', CA':''));
  const details=encodeURIComponent(`RSVP: ${e.url||''}\nHost: ${e.calendar_name||''}`);
  return `https://calendar.google.com/calendar/render?action=TEMPLATE&text=${title}&dates=${start}/${end}&location=${loc}&details=${details}&add=ayushi999@gmail.com`;
}
function renderMyEvents(){
  const el=S('#myEvents'),list=S('#myEventsList');
  const going=E.filter(e=>goingSet.has(e.id)).sort((a,b)=>(a.start_at||'').localeCompare(b.start_at||''));
  if(!going.length){el.classList.remove('show');return}
  el.classList.add('show');
  list.innerHTML=going.map(e=>`<span class="my-event-chip" onclick="openModal('${esc(e.id)}')">${esc((e.name||'').slice(0,35))} <span style="color:var(--t4);font-weight:400">${esc(fD(e.start_at).split(',')[0])}</span> <span class="remove" onclick="event.stopPropagation();toggleGoing('${esc(e.id)}')">&times;</span></span>`).join('');
}
window.toggleGoing=toggleGoing;window.closeModal=closeModal;window.openModal=openModal;

/* Swipe Mode */
let swipeList=[],swipeIdx=0;
function initSwipe(){
  swipeList=[...E].sort((a,b)=>activePerson?gS(b,activePerson)-gS(a,activePerson):(a.start_at||'').localeCompare(b.start_at||''));
  swipeIdx=0;picks=[];undoStack=[];renderSwipe();
}
function renderSwipe(){
  const stack=S('#swipeStack');if(!stack)return;
  stack.innerHTML='';
  const remaining=swipeList.slice(swipeIdx);
  const show=remaining.slice(0,3).reverse();
  show.forEach((e,i)=>{
    const isTop=i===show.length-1;
    const sc=activePerson?gS(e,activePerson):0;
    const card=document.createElement('div');
    card.className='swipe-card';
    card.style.zIndex=i+1;
    if(!isTop){card.style.transform=`scale(${1-(.03*(show.length-1-i))}) translateY(${(show.length-1-i)*8}px)`;card.style.opacity=.7+i*.15}
    card.innerHTML=`<div class="s-cover" style="background-color:${esc(e.tint)}"><img src="${esc(e.cover_url)}" loading="lazy" decoding="async" alt="" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover"><div class="badges"><span class="badge ${st(e)}">${stL(st(e))}</span></div><div class="swipe-flash like">Like</div><div class="swipe-flash nope">Nope</div>${activePerson?`<div style="position:absolute;bottom:12px;left:12px">${ring(sc,'sm')}</div>`:''}</div><div class="s-body"><div class="s-title">${esc(e.name)}</div><div class="s-meta"><div>${esc(fD(e.start_at))}</div><div>${esc(e.city||'?')}</div></div><div class="s-host">by ${esc(e.calendar_name)}</div></div>`;
    if(isTop)setupDrag(card,e);
    stack.appendChild(card);
  });
  S('#swipeCounter').textContent=`${swipeIdx}/${swipeList.length}`;
  renderPicks();
}
function setupDrag(card,event){
  let sx=0,dx=0,dragging=false;
  const start=(x)=>{sx=x;dragging=true;card.style.transition='none'};
  const move=(x)=>{if(!dragging)return;dx=x-sx;const r=dx*.1;card.style.transform=`translateX(${dx}px) rotate(${r}deg)`;const like=card.querySelector('.like'),nope=card.querySelector('.nope');if(like)like.style.opacity=Math.max(0,dx/100);if(nope)nope.style.opacity=Math.max(0,-dx/100)};
  const end=()=>{if(!dragging)return;dragging=false;if(Math.abs(dx)>100){swipeAction(dx>0?'like':'skip',card,event)}else{card.style.transition='transform .3s var(--eo)';card.style.transform='';const like=card.querySelector('.like'),nope=card.querySelector('.nope');if(like)like.style.opacity=0;if(nope)nope.style.opacity=0}dx=0};
  card.addEventListener('mousedown',e=>{e.preventDefault();start(e.clientX)});
  window.addEventListener('mousemove',e=>move(e.clientX));
  window.addEventListener('mouseup',end);
  card.addEventListener('touchstart',e=>{start(e.touches[0].clientX)},{passive:true});
  card.addEventListener('touchmove',e=>{move(e.touches[0].clientX)},{passive:true});
  card.addEventListener('touchend',end);
}
function swipeAction(action,card,event){
  const dir=action==='like'?1:-1;
  if(card){card.style.transition='transform .4s var(--eo),opacity .4s';card.style.transform=`translateX(${dir*600}px) rotate(${dir*30}deg)`;card.style.opacity='0'}
  undoStack.push({idx:swipeIdx,action,event});
  if(action==='like')picks.push(event);
  swipeIdx++;
  setTimeout(renderSwipe,350);
}
function undoSwipe(){
  if(!undoStack.length)return;
  const last=undoStack.pop();
  if(last.action==='like')picks=picks.filter(p=>p.id!==last.event.id);
  swipeIdx=last.idx;
  renderSwipe();
}
function renderPicks(){
  const el=S('#picksList');if(!el)return;
  el.innerHTML=picks.map(p=>`<span class="pick-chip" onclick="openModal('${esc(p.id)}')">${esc((p.name||'').slice(0,30))}</span>`).join('');
  S('#picksCount').textContent=picks.length?`My Picks (${picks.length})`:'My Picks';
}
window.undoSwipe=undoSwipe;window.swipeAction=swipeAction;

/* Mode */
function setMode(m){
  mode=m;
  S('#discoverView').style.display=m==='discover'?'block':'none';
  S('#swipeView').classList.toggle('show',m==='swipe');
  SA('.mode-btn').forEach(b=>b.classList.toggle('active',b.dataset.mode===m));
  if(m==='swipe')initSwipe();
  if(m==='discover')filterAndRender();
}

/* Layout toggle */
function setCardLayout(l){
  cardLayout=l;
  SA('.layout-btn').forEach(b=>b.classList.toggle('active',b.dataset.layout===l));
  filterAndRender();
}
window.setCardLayout=setCardLayout;

/* Date */
function setDateRange(preset){
  const df=S('#dateFrom'),dt=S('#dateTo');
  const now=new Date();
  const fmt=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  SA('.date-quick button').forEach(b=>b.classList.remove('active'));
  if(preset==='today'){df.value=fmt(now);dt.value=fmt(now)}
  else if(preset==='week'){df.value=fmt(now);const end=new Date(now);end.setDate(end.getDate()+7);dt.value=fmt(end)}
  else if(preset==='month'){df.value=fmt(now);const end=new Date(now);end.setMonth(end.getMonth()+1);dt.value=fmt(end)}
  else{df.value='';dt.value=''}
  event.target.classList.add('active');
  filterAndRender();
}
window.setDateRange=setDateRange;

/* Filter drawer */
function toggleFilters(){
  const drawer=S('#filterDrawer'),btn=S('#filterToggle');
  drawer.classList.toggle('open');
  btn.classList.toggle('has-active',drawer.classList.contains('open'));
}
function updateFilterCount(){
  const count=cityF.size+catF.size+statusF.size+(S('#dateFrom').value?1:0)+(activePerson?1:0);
  const el=S('#filterCount');
  if(count){el.textContent=count;el.classList.add('show')}else{el.classList.remove('show')}
  S('#filterToggle').classList.toggle('has-active',count>0);
}
window.toggleFilters=toggleFilters;

/* Command palette */
function openCmd(){S('#cmdBg').classList.add('open');S('#cmdInput').value='';S('#cmdInput').focus();cmdSearch('')}
function closeCmd(){S('#cmdBg').classList.remove('open')}
function cmdSearch(q){
  q=q.toLowerCase().trim();
  const list=q?E.filter(e=>(e.name||'').toLowerCase().includes(q)||(e.calendar_name||'').toLowerCase().includes(q)||(e.city||'').toLowerCase().includes(q)).slice(0,8):E.slice(0,8);
  S('#cmdResults').innerHTML=list.map(e=>`<div class="cmd-item" onclick="closeCmd();openModal('${esc(e.id)}')"><div><div class="ci-title">${esc(e.name)}</div><div class="ci-meta">${esc(fD(e.start_at))} · ${esc(e.city||'?')}</div></div></div>`).join('');
}

/* Chips */
function buildChips(){
  const cities=new Map(),cats=new Map();
  E.forEach(e=>{cities.set(e.city||'TBD',(cities.get(e.city||'TBD')||0)+1);(e.categories||[]).forEach(c=>cats.set(c,(cats.get(c)||0)+1))});
  const pRow=S('#personChips'),people=new Set();
  E.forEach(e=>Object.keys(e.scores||{}).forEach(n=>people.add(n)));
  [...people].sort().forEach(p=>{
    const initial=p[0].toUpperCase();
    const el=document.createElement('button');
    el.className='person-chip';
    el.title=p[0].toUpperCase()+p.slice(1);
    el.dataset.person=p;
    el.innerHTML=initial;
    el.onclick=()=>{if(activePerson===p){activePerson=null;el.classList.remove('active');S('#sort').value='date';S('#scoreSlider').classList.remove('show');scoreThreshold=0;S('#scoreRange').value=0;S('#scoreVal').textContent='0'}else{activePerson=p;pRow.querySelectorAll('.person-chip').forEach(x=>x.classList.remove('active'));el.classList.add('active');S('#sort').value=p;S('#scoreSlider').classList.add('show');scoreThreshold=parseInt(S('#scoreRange').value)}updateFilterCount();if(mode==='discover')filterAndRender();else initSwipe()};
    pRow.appendChild(el);
  });
  const cRow=S('#cityChips');[...cities.entries()].sort((a,b)=>b[1]-a[1]).slice(0,8).forEach(([c,n])=>{const el=document.createElement('span');el.className='chip';el.textContent=`${c} (${n})`;el.onclick=()=>{cityF.has(c)?cityF.delete(c):cityF.add(c);el.classList.toggle('active');updateFilterCount();filterAndRender()};cRow.appendChild(el)});
  const tRow=S('#catChips');[...cats.entries()].sort((a,b)=>b[1]-a[1]).slice(0,6).forEach(([c,n])=>{const el=document.createElement('span');el.className='chip';el.textContent=`${c} (${n})`;el.onclick=()=>{catF.has(c)?catF.delete(c):catF.add(c);el.classList.toggle('active');updateFilterCount();filterAndRender()};tRow.appendChild(el)});
  const sRow=S('#statusChips');[['open','Open'],['waitlist','Waitlist'],['sold','Sold out']].forEach(([s,l])=>{const el=document.createElement('span');el.className='chip';el.textContent=`${l} (${E.filter(e=>st(e)===s).length})`;el.onclick=()=>{statusF.has(s)?statusF.delete(s):statusF.add(s);el.classList.toggle('active');updateFilterCount();filterAndRender()};sRow.appendChild(el)});
}

/* Keyboard */
document.addEventListener('keydown',e=>{
  if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA')return;
  if(e.key==='/'){e.preventDefault();openCmd()}
  if(e.key==='Escape'){closeModal();closeCmd()}
  if(e.key==='s')setMode(mode==='discover'?'swipe':'discover');
  if(e.key==='1'){const chips=SA('.person-chip');if(chips[0])chips[0].click()}
  if(e.key==='2'){const chips=SA('.person-chip');if(chips[1])chips[1].click()}
  if(e.key==='f')toggleFilters();
});

/* Init */
// Event delegation: single listener handles all card clicks (including deferred sections)
S('#grid').addEventListener('click',e=>{
  const card=e.target.closest('.card');
  if(card&&card.dataset.id)openModal(card.dataset.id);
});
S('#modalBg').addEventListener('click',e=>{if(e.target.id==='modalBg')closeModal()});
S('#cmdBg').addEventListener('click',e=>{if(e.target.id==='cmdBg')closeCmd()});
S('#cmdInput').addEventListener('input',e=>cmdSearch(e.target.value));
S('#search').addEventListener('input',debounce(filterAndRender,150));
S('#dateFrom').addEventListener('change',()=>{updateFilterCount();filterAndRender()});
S('#dateTo').addEventListener('change',()=>{updateFilterCount();filterAndRender()});
S('#scoreRange').addEventListener('input',e=>{scoreThreshold=parseInt(e.target.value);S('#scoreVal').textContent=scoreThreshold;filterAndRender()});
S('#sort').addEventListener('change',()=>{const v=S('#sort').value;if(v!=='date'&&v!=='rsvps'&&v!=='name'){activePerson=v;SA('.person-chip').forEach(x=>x.classList.toggle('active',x.textContent.toLowerCase()===v))}filterAndRender()});
SA('.mode-btn').forEach(b=>b.addEventListener('click',()=>setMode(b.dataset.mode)));
buildChips();
setMode(isMobile?'swipe':'discover');
renderSummary();
renderMyEvents();
"""



def build_html(data: dict, slimmed: list[dict]) -> str:
    fetched = data.get("scored_at") or data.get("fetched_at") or ""
    total = len(slimmed)
    total_rsvps = sum(e.get("guest_count", 0) or 0 for e in slimmed)
    open_count = sum(1 for e in slimmed if not e.get("sold_out") and not e.get("waitlist_active"))
    sold_count = sum(1 for e in slimmed if e.get("sold_out"))

    data_json = json.dumps(slimmed, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Luma Bay Area Events</title>
<style>{CSS}</style>
</head><body>
<div class="wrap">
  <header>
    <div class="header-row">
      <div>
        <h1>Luma Bay Area Events</h1>
        <div class="sub">{total} events &middot; updated {html.escape(fetched[:10])}</div>
        <div class="summary" id="summary"></div>
      </div>
      <div class="mode-toggle">
        <button class="mode-btn active" data-mode="discover">Discover</button>
        <button class="mode-btn" data-mode="swipe">Swipe</button>
      </div>
      <div class="layout-toggle">
        <button class="layout-btn active" data-layout="overlay" onclick="setCardLayout('overlay')" title="Image cards">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><rect x="1" y="1" width="14" height="14" rx="2" fill="currentColor" opacity=".15"/><rect x="1" y="1" width="14" height="14" rx="2" stroke="currentColor" stroke-width="1.5"/><rect x="1" y="9" width="14" height="6" rx="0 0 2 2" fill="currentColor" opacity=".4"/></svg>
        </button>
        <button class="layout-btn" data-layout="list" onclick="setCardLayout('list')" title="List view">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><rect x="1" y="2" width="14" height="4" rx="1.5" fill="currentColor" opacity=".15" stroke="currentColor" stroke-width="1.5"/><rect x="1" y="9" width="14" height="4" rx="1.5" fill="currentColor" opacity=".15" stroke="currentColor" stroke-width="1.5"/></svg>
        </button>
      </div>
    </div>
  </header>

  <div class="toolbar">
    <input type="search" id="search" placeholder="Search events..."/>
    <div class="person-chips" id="personChips"></div>
    <label>Sort: <select id="sort">
      <option value="date">Date</option>
      <option value="rsvps">RSVPs</option>
      <option value="sumeet">Sumeet</option>
      <option value="ayushi">Ayushi</option>
      <option value="name">Name</option>
    </select></label>
    <button class="filter-toggle" id="filterToggle" onclick="toggleFilters()">Filters <span class="filter-count" id="filterCount"></span></button>
  </div>
  <div class="filter-drawer" id="filterDrawer">
    <div class="score-slider" id="scoreSlider">
      <label>Min score</label>
      <input type="range" id="scoreRange" min="0" max="100" value="0"/>
      <span class="val" id="scoreVal">0</span>
    </div>
    <div class="chips" id="statusChips"></div>
    <div class="chips" id="catChips"></div>
    <div class="chips" id="cityChips"></div>
    <div class="date-range">
      <label>From</label><input type="date" id="dateFrom"/>
      <label>To</label><input type="date" id="dateTo"/>
      <div class="date-quick">
        <button onclick="setDateRange('today')">Today</button>
        <button onclick="setDateRange('week')">This Week</button>
        <button onclick="setDateRange('month')">This Month</button>
        <button onclick="setDateRange('all')" class="active">All</button>
      </div>
    </div>
  </div>

  <div id="discoverView">
    <div class="my-events" id="myEvents">
      <h3>My Events</h3>
      <div class="my-events-list" id="myEventsList"></div>
    </div>
    <div class="hero" id="hero"></div>
    <div class="count" id="count"></div>
    <div id="grid"></div>
  </div>

  <div class="swipe-container" id="swipeView">
    <div class="swipe-stack" id="swipeStack"></div>
    <div class="swipe-actions">
      <button class="swipe-btn undo-btn" onclick="undoSwipe()" title="Undo">&#8630;</button>
      <button class="swipe-btn skip" onclick="swipeAction('skip')" title="Skip">&#10005;</button>
      <button class="swipe-btn like-btn" onclick="swipeAction('like')" title="Interested">&#10003;</button>
    </div>
    <div class="swipe-counter" id="swipeCounter"></div>
    <div class="picks-bar">
      <div class="picks-label" id="picksCount">My Picks</div>
      <div class="picks-list" id="picksList"></div>
    </div>
  </div>
</div>

<div class="modal-bg" id="modalBg"><div id="modal"></div></div>

<div class="cmd-bg" id="cmdBg">
  <div class="cmd-box">
    <input class="cmd-input" id="cmdInput" placeholder="Search events..." autocomplete="off"/>
    <div class="cmd-results" id="cmdResults"></div>
  </div>
</div>

<script type="application/json" id="data">{data_json}</script>
<script>window.__EVENTS__=JSON.parse(document.getElementById('data').textContent);</script>
<script>{JS}</script>
</body></html>"""


def main() -> int:
    src = latest_dump()
    print(f"Reading {src.name}", flush=True)
    data = json.loads(src.read_text(encoding="utf-8"))
    slimmed = slim(data["events"])
    print(f"Slimmed {len(slimmed)} events", flush=True)
    out = OUT_DIR / "viewer.html"
    out.write_text(build_html(data, slimmed), encoding="utf-8")
    print(f"Wrote {out}", flush=True)
    print(f"Size: {out.stat().st_size / 1024:.1f} KB")
    print(f"\nOpen in browser: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

