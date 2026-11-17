#!/usr/bin/env python3
"""Regenerate the "Today" section of index.html from tickets resolved today.

Pulls tickets assigned to Atticus that were updated today from SolarWinds
Service Desk, strips anything that could identify the school or a person,
asks Claude to rewrite each into one changelog-style bullet, and replaces
the <section class="today"> block in index.html with the result.

Env vars:
  SOLARWINDS_API_TOKEN          Service Desk JWT (Bearer)
  ANTHROPIC_IDENTITY_TOKEN      GitHub OIDC token for this run
  ANTHROPIC_FEDERATION_RULE_ID, ANTHROPIC_ORGANIZATION_ID,
  ANTHROPIC_SERVICE_ACCOUNT_ID  Workload identity federation config
"""
import html
import json
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic
import requests

SCHOOL_TZ = ZoneInfo("America/Chicago")
ASSIGNEE_GROUP_ID = "16556132"  # Atticus's personal assignee group in Service Desk
MODEL = "claude-haiku-5-5"

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


def fetch_resolved_today():
    token = os.environ["SOLARWINDS_API_TOKEN"]
    resp = requests.get(
        "https://api.samanage.com/incidents.json",
        headers={
            "X-Samanage-Authorization": f"Bearer {token}",
            "Accept": "application/vnd.samanage.v2.1+json",
        },
        params={"assigned_to[]": ASSIGNEE_GROUP_ID, "state[]": "Resolved", "per_page": 100},
        timeout=30,
    )
    resp.raise_for_status()
    today = datetime.now(SCHOOL_TZ).date()
    out = []
    for t in resp.json():
        updated = t.get("updated_at") or t.get("resolved_at")
        if not updated:
            continue
        ts = datetime.fromisoformat(updated.replace("Z", "+00:00")).astimezone(SCHOOL_TZ)
        if ts.date() == today:
            out.append(t)
    return out


SYSTEM_PROMPT = """You turn a K-12 school IT administrator's internal help-desk ticket \
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


def summarize(client, ticket):
    category = (ticket.get("category") or {}).get("name", "")
    subcategory = (ticket.get("subcategory") or {}).get("name", "")
    notes = redact(ticket.get("resolution_description") or ticket.get("description") or "")
    user_prompt = f"Category: {category}\nSubcategory: {subcategory}\nResolution notes: {notes}"

    resp = client.messages.create(
        model=MODEL,
        max_tokens=300,
        output_config={"effort": "low"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = resp.content[0].text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {"headline": f"Resolved a {category or 'help desk'} ticket", "detail": ""}
    try:
        data = json.loads(match.group(0))
        return {"headline": data.get("headline", "").strip(), "detail": data.get("detail", "").strip()}
    except json.JSONDecodeError:
        return {"headline": f"Resolved a {category or 'help desk'} ticket", "detail": ""}


def render_section(bullets, now):
    items = "\n".join(
        f'        <li><strong>{html.escape(b["headline"])}.</strong> {html.escape(b["detail"])}</li>'
        for b in bullets
    )
    day_label = now.strftime("%A, %B %-d")
    time_label = now.strftime("%-I:%M %p")
    iso = now.strftime("%Y-%m-%dT%H:%M") + now.strftime("%z")[:3] + ":" + now.strftime("%z")[3:]
    count = len(bullets)
    noun = "ticket" if count == 1 else "tickets"
    return f"""<section class="today" aria-labelledby="today-h">
      <div class="today-head">
        <h2 id="today-h"><span class="pulse" aria-hidden="true"></span>Today</h2>
        <time datetime="{iso}">{day_label} · updated {time_label}</time>
      </div>
      <p class="today-stats"><strong>{count}</strong> {noun} resolved</p>
      <ul class="today-log">
{items}
      </ul>
    </section>"""


def main():
    now = datetime.now(SCHOOL_TZ)
    tickets = fetch_resolved_today()

    github_output = os.environ.get("GITHUB_OUTPUT")

    if not tickets:
        print("No tickets resolved today; leaving index.html untouched.")
        if github_output:
            with open(github_output, "a") as f:
                f.write("changed=false\n")
        return

    client = anthropic.Anthropic()
    bullets = [summarize(client, t) for t in tickets]

    index_path = os.path.join(os.path.dirname(__file__), "..", "index.html")
    with open(index_path) as f:
        page = f.read()

    new_section = render_section(bullets, now)
    updated_page = re.sub(
        r'<section class="today".*?</section>',
        lambda _match: new_section,
        page,
        count=1,
        flags=re.DOTALL,
    )

    if updated_page == page:
        print("today section pattern not found or unchanged", file=sys.stderr)
        if github_output:
            with open(github_output, "a") as f:
                f.write("changed=false\n")
        return

    with open(index_path, "w") as f:
        f.write(updated_page)

    print(f"Wrote {len(bullets)} bullet(s) to index.html")
    if github_output:
        with open(github_output, "a") as f:
            f.write("changed=true\n")
            f.write(f"count={len(bullets)}\n")


if __name__ == "__main__":
    main()
