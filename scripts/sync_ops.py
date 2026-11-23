#!/usr/bin/env python3
"""Regenerate data/ops.json, the rail on the site.

Publishes two things from SolarWinds Service Desk: my open changes and the
knowledge-base solutions I've written. Titles are redacted, then Claude
rewrites them into short public-safe wording. ops.js renders the JSON on
every page. Nothing about incidents or open ticket counts is published. Rewritten titles
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
MY_USER_ID = 15057749   # my user record (solution author)
MODEL = "claude-haiku-5-5"
MAX_ITEMS = 5
OPS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "ops.json")

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


# --- Claude rewrites -------------------------------------------------------

TITLE_PROMPT = """You shorten an internal IT record title for a school IT \
administrator's public portfolio site. The reader is a peer IT pro.

Rules:
- Stay faithful to the original title. Do not add facts, causes, device types \
or numbers that are not in it.
- Never include a person's name, room, building, mailbox or address, identifiers, \
the school's name, vendor or internal system names other than Apple, Google, \
Jamf, Microsoft and macOS/ChromeOS, or security specifics (keys, passwords, \
network names, policy or profile numbers).
- Anything about students' health, discipline, grades or records: reply {"title": null}.
- 3-8 words, sentence case, no trailing period.

Reply with ONLY a JSON object: {"title": "..."}"""


def ask(client, system, user):
    resp = client.messages.create(
        model=MODEL,
        max_tokens=300,
        output_config={"effort": "low"},
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    m = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        return json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return {}


def public_title(client, cache, kind, rec):
    """Cached public title, or a new one from Claude. None = skip the record."""
    raw = re.sub(r"^WHD-\d+\s*", "", redact(rec.get("name")))
    key = f"{kind}:{rec['number']}"
    h = hashlib.sha256((TITLE_PROMPT + raw).encode()).hexdigest()[:12]
    hit = cache.get(key)
    if hit and hit.get("h") == h:
        return hit.get("t")
    out = ask(client, TITLE_PROMPT, f"Record type: {kind}\nTitle: {raw}")
    title = (out.get("title") or "").strip() or None
    cache[key] = {"h": h, "t": title}
    return title


def fetch():
    changes = [c for c in sd_all("changes.json") if mine(c)]
    solutions = [
        s for s in sd_all("solutions.json", {"sort_by": "created_at", "sort_order": "DESC"}, max_pages=3)
        if (s.get("requester") or {}).get("id") == MY_USER_ID and (s.get("state") or "").lower() == "approved"
    ]
    return changes, solutions


def build(client, cache, now, changes, solutions):
    out_c = []
    for c in changes:
        state = change_state(c, now)
        if state not in ("planned", "scheduled", "in_progress"):
            continue
        title = public_title(client, cache, "change", c)
        if not title:
            continue
        start = local(c.get("planned_start_at"))
        out_c.append({"title": title, "state": state, "start": start.date().isoformat() if start else None})
    rank = {"in_progress": 0, "scheduled": 1, "planned": 2}
    out_c.sort(key=lambda x: (rank[x["state"]], x["start"] or ""))

    out_s = []
    for s in solutions[:MAX_ITEMS]:
        title = public_title(client, cache, "solution", s)
        if title:
            out_s.append({"title": title, "date": local(s["created_at"]).date().isoformat()})

    return {
        "updated": now.isoformat(timespec="minutes"),
        "changes": out_c[:MAX_ITEMS],
        "changes_total": len(out_c),
        "solutions": out_s,
        "_titles": cache,
    }


def main():
    now = datetime.now(SCHOOL_TZ)
    changes, solutions = fetch()

    if "--dry-run" in sys.argv:
        print(f"open changes: {sum(change_state(c, now) in ('planned','scheduled','in_progress') for c in changes)}")
        print(f"my solutions: {len(solutions)}")
        return

    try:
        with open(OPS_PATH) as f:
            prev = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        prev = {}

    import anthropic
    client = anthropic.Anthropic()
    data = build(client, prev.get("_titles", {}), now, changes, solutions)

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


if __name__ == "__main__":
    main()
