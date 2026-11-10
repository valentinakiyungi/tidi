"""What Tidi actually does with your mail. No phone/Telegram code in here."""
import time
from email.utils import parseaddr

import brain
import gmail_client as gm

ARCHIVE = {"newsletter", "promotion", "notification"}
URGENT = {"important", "needs_reply"}
LABELS = {"important": "Important", "needs_reply": "Needs reply", "newsletter": "Newsletter",
          "promotion": "Promotion", "notification": "Notification", "other": "Other"}


def who(sender):
    name, addr = parseaddr(sender)
    return name or addr or sender


def process_new(service, state, lookback_hours=24, max_emails=50):
    """Sort, label, archive and draft for emails Tidi hasn't seen yet.

    Returns a list of rows (one per newly handled email).
    """
    emails = gm.fetch_recent(service, lookback_hours, max_emails)
    processed = state.setdefault("processed", {})
    new = [e for e in emails if e["id"] not in processed]
    if not new:
        return []

    decisions = brain.triage(new)
    log = state.setdefault("log", [])
    queue = state.setdefault("unsub_queue", [])
    asked = state.setdefault("asked_senders", [])
    rows = []
    for e in new:
        d = decisions.get(e["id"])
        if not d:
            continue  # AI skipped it; try again next check
        cat = d["category"]
        gm.label_message(service, e["id"], LABELS.get(cat, "Other"), archive=cat in ARCHIVE)
        drafted = False
        if cat == "needs_reply" and d.get("draft_reply"):
            gm.create_reply_draft(service, e, d["draft_reply"])
            drafted = True
        processed[e["id"]] = time.time()

        row = {"id": e["id"], "from": e["from"], "subject": e["subject"], "category": cat,
               "summary": d.get("summary", ""), "drafted": drafted}
        log.append(row)
        rows.append(row)

        if (d.get("suggest_unsubscribe") and e["list_unsubscribe"]
                and e["from"] not in asked
                and all(q["from"] != e["from"] for q in queue)):
            queue.append({k: e[k] for k in
                          ("id", "from", "subject", "list_unsubscribe", "list_unsubscribe_post")})

    # Forget emails older than two weeks so state.json stays small.
    cutoff = time.time() - 14 * 86400
    state["processed"] = {k: v for k, v in processed.items() if v > cutoff}
    return rows


def alert_text(rows):
    """Text for an instant alert, or None if nothing needs you."""
    urgent = [r for r in rows if r["category"] in URGENT]
    if not urgent:
        return None
    lines = []
    for r in urgent:
        tag = "↩️ reply drafted" if r["drafted"] else ("⚠️" if r["category"] == "important" else "↩️")
        lines.append(f"• {who(r['from'])}: {r['summary'] or r['subject']}  {tag}")
    head = f"{len(urgent)} new email{'s' if len(urgent) > 1 else ''} need{'' if len(urgent) > 1 else 's'} you:"
    return head + "\n" + "\n".join(lines)


def brief_text(state):
    """Morning summary of everything handled since the last brief. Clears the log."""
    rows = state.get("log", [])
    state["log"] = []
    if not rows:
        return "Good morning! No new email since your last brief. Enjoy the quiet ✨"
    text = brain.write_brief(rows)
    drafts = sum(1 for r in rows if r["drafted"])
    if drafts:
        text += f"\n\n✍️ {drafts} reply draft(s) waiting in Gmail Drafts."
    return text
