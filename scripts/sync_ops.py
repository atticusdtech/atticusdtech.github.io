#!/usr/bin/env python3
"""Regenerate data/ops.json, the ITIL operations rail on the site.

Pulls my incidents, problems, changes and releases from SolarWinds Service
Desk, scrubs anything that could identify the school or a person, asks Claude
to rewrite record titles and today's resolved incidents into public-safe
plain language, and writes the result to data/ops.json. ops.js renders it on
every page.

Only records assigned to my assignee group are published. Rewritten titles
are cached in the JSON by record and a hash of the source title, so a record
keeps the same public wording until its real title changes.

Env vars:
  SOLARWINDS_API_TOKEN          Service Desk JWT (Bearer)
  ANTHROPIC_IDENTITY_TOKEN      GitHub OIDC token for this run
  ANTHROPIC_FEDERATION_RULE_ID, ANTHROPIC_ORGANIZATION_ID,
  ANTHROPIC_SERVICE_ACCOUNT_ID  Workload identity federation config

  --dry-run   fetch and print counts only; no Claude calls, no file written
"""
import hashlib
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

SCHOOL_TZ = ZoneInfo("America/Chicago")
API = "https://api.samanage.com"
MY_GROUP_ID = 16556132  # my personal assignee group in Service Desk
MODEL = "claude-haiku-5-5"
MAX_LOG = 6           # incident bullets shown for today
CHANGE_WINDOW = 7     # days a completed change stays on the rail
OPS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "ops.json")

DONE_INCIDENT = {"Resolved", "Closed"}

# --- redaction, applied before anything leaves this machine for Claude ---
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
SCHOOL_TERMS = [
    "Bernard Zell Anshe Emet Day School",
    "Bernard Zell",
    "Anshe Emet",
    "bzaeds.org",
    "bernardzell.org",
    "BZAEDS",
]
TAG_RE = re.compile(r"<[^>]+>")


def redact(text):
    if not text:
        return ""
    text = html.unescape(TAG_RE.sub(" ", text))
    text = EMAIL_RE.sub("[redacted]", text)
    text = PHONE_RE.sub("[redacted]", text)
    for term in SCHOOL_TERMS:
        text = re.sub(re.escape(term), "[redacted]", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


# --- Service Desk ---------------------------------------------------------

def sd_get(path, params=None):
    resp = requests.get(
        f"{API}/{path}",
        headers={
            "X-Samanage-Authorization": f"Bearer {os.environ['SOLARWINDS_API_TOKEN']}",
            "Accept": "application/vnd.samanage.v2.1+json",
        },
        params=params or {},
        timeout=30,
    )
    resp.raise_for_status()
    return resp


def sd_all(path, params=None, max_pages=10):
    out = []
    for page in range(1, max_pages + 1):
        resp = sd_get(path, {**(params or {}), "per_page": 100, "page": page})
        batch = resp.json()
        out.extend(batch)
        if page >= int(resp.headers.get("X-Total-Pages", page)):
            break
    return out


def mine(rec):
    a = rec.get("assignee") or {}
    return a.get("group_id") == MY_GROUP_ID or a.get("id") == MY_GROUP_ID


def local(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(SCHOOL_TZ) if ts else None


def ids(rec, key):
    return [i["id"] for i in (rec.get(key) or []) if i.get("id")]


# --- state mapping into plain ITIL lifecycle words -------------------------

def problem_state(p):
    s = (p.get("state") or "").lower()
    if s in ("closed", "resolved"):
        return "resolved"
    if "change" in s:
        return "fix_in_change"
    if redact(p.get("root_cause")) and redact(p.get("workaround")):
        return "known_error"
    return "investigating"


def change_state(c, now):
    s = (c.get("state") or "").lower()
    if "completed" in s:
        return "completed"
    if "closed" in s or "cancel" in s or "reject" in s:
        return "cancelled"
    if "fail" in s or "rollback" in s:
        return "rolled_back"
    start, end = local(c.get("planned_start_at")), local(c.get("planned_end_at"))
    if start and end and start <= now <= end:
        return "in_progress"
    if start and start > now:
        return "scheduled"
    return "planned"


def release_state(r):
    s = (r.get("state") or "").lower()
    return "done" if ("closed" in s or "completed" in s) else "open"


# --- Claude rewrites -------------------------------------------------------

TITLE_PROMPT = """You rewrite an internal IT service-management record title for \
a school IT administrator's public portfolio site. The reader is a peer IT pro.

Rules:
- Never include a person's name, room or building, student or staff identifier, \
email address, internal mailbox name, phone number, or the school's name.
- Drop internal object numbers (policy 756, profile 64, script 298).
- Keep product names (Jamf Pro, Google Admin, Chromebook, macOS) and the technical gist.
- Anything about student health, discipline, grades or other student records: \
reply with {"title": null}.
- 4-10 words, sentence case, no trailing period.

Reply with ONLY a JSON object: {"title": "..."}"""

INCIDENT_PROMPT = """You turn a K-12 school IT administrator's internal help-desk incident \
into one changelog-style bullet for his personal portfolio site, written in his own \
voice (first person, past tense, plain language, no jargon dump).

Match this style exactly:
  headline: "Fixed a teacher's printing remotely"
  detail: "Their screenshot pointed to a bad printer setup, so I connected to the \
laptop and rebuilt it without a visit."

Rules:
- Never include a person's name, job title tied to a person, room or building \
identifier, student ID, email address, phone number, or the school's name.
- If the notes don't have enough detail, write the most honest generic technical \
description the category supports rather than inventing specifics.
- headline: 3-7 words, no trailing period, states the action and result.
- detail: one or two sentences, concrete about the technical work.

Reply with ONLY a JSON object: {"headline": "...", "detail": "..."}"""


def ask(client, system, user):
    resp = client.messages.create(
        model=MODEL,
        max_tokens=300,
        output_config={"effort": "low"},
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    m = re.search(r"\{.*\}", resp.content[0].text, re.DOTALL)
    try:
        return json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return {}


def public_title(client, cache, kind, rec):
    """Return the cached public title, or ask Claude for a new one.

    Returns None when Claude declines (sensitive record): the record is skipped.
    """
    raw = redact(rec.get("name"))
    key = f"{kind}:{rec['number']}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:12]
    hit = cache.get(key)
    if hit and hit.get("h") == h:
        return hit.get("t")
    detail = redact(rec.get("description_no_html") or rec.get("description"))[:600]
    out = ask(client, TITLE_PROMPT, f"Record type: {kind}\nTitle: {raw}\nDescription: {detail}")
    title = (out.get("title") or "").strip() or None
    cache[key] = {"h": h, "t": title}
    return title


def incident_bullet(client, t):
    category = (t.get("category") or {}).get("name", "")
    subcategory = (t.get("subcategory") or {}).get("name", "")
    notes = redact(t.get("resolution_description") or t.get("description") or "")[:1500]
    out = ask(client, INCIDENT_PROMPT, f"Category: {category}\nSubcategory: {subcategory}\nResolution notes: {notes}")
    head = (out.get("headline") or "").strip() or f"Resolved a {category.lower() or 'help desk'} incident"
    return {"headline": head, "detail": (out.get("detail") or "").strip()}


# --- build -----------------------------------------------------------------

def fetch(now):
    today = now.date()
    incidents = sd_all("incidents.json", {"assigned_to[]": MY_GROUP_ID, "sort_by": "updated_at", "sort_order": "DESC"})
    problems = [p for p in sd_all("problems.json") if mine(p)]
    changes = [c for c in sd_all("changes.json") if mine(c)]
    releases = [r for r in sd_all("releases.json") if mine(r)]

    resolved_today = [
        t for t in incidents
        if t.get("state") in DONE_INCIDENT and (local(t.get("updated_at")) or now).date() == today
    ]
    open_count = sum(1 for t in incidents if t.get("state") not in DONE_INCIDENT)
    return incidents, resolved_today, open_count, problems, changes, releases


def build(client, cache, now, resolved_today, open_count, problems, changes, releases):
    today = now.date()
    window = today - timedelta(days=CHANGE_WINDOW)
    num = {}  # Service Desk id -> public label, for cross-links

    for p in problems:
        num[p["id"]] = f"PRB-{p['number']}"
    for c in changes:
        num[c["id"]] = f"CHG-{c['number']}"
    for r in releases:
        num[r["id"]] = f"REL-{r['number']}"

    out_p = []
    for p in problems:
        state = problem_state(p)
        updated = local(p.get("updated_at"))
        if state == "resolved" and (not updated or updated.date() < window):
            continue
        title = public_title(client, cache, "problem", p)
        if not title:
            continue
        out_p.append({
            "id": num[p["id"]],
            "title": title,
            "state": state,
            "incidents": len(ids(p, "incidents")),
            "changes": [num[i] for i in ids(p, "changes") if i in num],
            "today": bool(updated and updated.date() == today),
        })

    out_c, change_by_id = [], {}
    for c in changes:
        state = change_state(c, now)
        updated = local(c.get("updated_at"))
        change_by_id[c["id"]] = state
        if state in ("completed", "cancelled", "rolled_back") and (not updated or updated.date() < window):
            continue
        title = public_title(client, cache, "change", c)
        if not title:
            continue
        start = local(c.get("planned_start_at"))
        out_c.append({
            "id": num[c["id"]],
            "title": title,
            "state": state,
            "type": (c.get("change_type") or "Normal") if c.get("change_type") not in (None, "None") else "Normal",
            "fixes": [num[i] for i in ids(c, "problems") if i in num],
            "incidents": len(ids(c, "incidents")),
            "start": start.date().isoformat() if start else None,
            "done": updated.date().isoformat() if state == "completed" and updated else None,
            "today": bool(updated and updated.date() == today),
        })

    out_r = []
    for r in releases:
        if release_state(r) == "done":
            continue
        title = public_title(client, cache, "release", r)
        if not title:
            continue
        linked = ids(r, "changes")
        # changes linked to a release can belong to others; count what we can see
        done = sum(1 for i in linked if change_by_id.get(i) == "completed")
        out_r.append({
            "id": num[r["id"]],
            "title": title,
            "changes_done": done,
            "changes_total": len(linked),
            "changes": [num[i] for i in linked if i in num],
            "target": (local(r.get("planned_end_at") or r.get("due_at")) or now).date().isoformat()
            if (r.get("planned_end_at") or r.get("due_at")) else None,
        })

    order_p = {"investigating": 0, "known_error": 1, "fix_in_change": 2, "resolved": 3}
    out_p.sort(key=lambda x: (order_p[x["state"]], -x["incidents"]))
    order_c = {"in_progress": 0, "scheduled": 1, "planned": 2, "completed": 3, "rolled_back": 4, "cancelled": 5}
    out_c.sort(key=lambda x: (order_c[x["state"]], x["done"] or "", x["id"]), reverse=False)

    log = [incident_bullet(client, t) for t in resolved_today[:MAX_LOG]]

    return {
        "updated": now.isoformat(timespec="minutes"),
        "incidents": {"resolved_today": len(resolved_today), "open": open_count, "log": log},
        "problems": out_p,
        "changes": out_c,
        "releases": out_r,
        "_titles": cache,
    }


def main():
    now = datetime.now(SCHOOL_TZ)
    dry = "--dry-run" in sys.argv
    _, resolved_today, open_count, problems, changes, releases = fetch(now)

    if dry:
        print(f"incidents: {len(resolved_today)} resolved today, {open_count} open")
        print(f"problems: {len(problems)}  changes: {len(changes)}  releases: {len(releases)}")
        print("problem states:", sorted(problem_state(p) for p in problems))
        print("change states:", sorted(change_state(c, now) for c in changes))
        return

    try:
        with open(OPS_PATH) as f:
            prev = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        prev = {}
    cache = prev.get("_titles", {})

    import anthropic
    client = anthropic.Anthropic()
    data = build(client, cache, now, resolved_today, open_count, problems, changes, releases)

    # Don't wipe yesterday's incident log on a quiet day; ITIL lanes still refresh.
    if not data["incidents"]["log"] and prev.get("incidents"):
        data["incidents"]["log"] = []
        data["incidents"]["quiet"] = True

    strip = lambda d: {k: v for k, v in d.items() if k not in ("updated", "_titles")}
    changed = strip(data) != strip(prev)

    if changed:
        os.makedirs(os.path.dirname(OPS_PATH), exist_ok=True)
        with open(OPS_PATH, "w") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
            f.write("\n")
        print("Wrote data/ops.json")
    else:
        print("No changes since last run.")

    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a") as f:
            f.write(f"changed={'true' if changed else 'false'}\n")
            f.write(f"count={data['incidents']['resolved_today']}\n")


if __name__ == "__main__":
    main()
