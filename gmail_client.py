"""Gmail access: read recent mail, label/archive, create drafts, unsubscribe."""
import base64
import os
import re
from email.mime.text import MIMEText
from email.utils import parseaddr

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# modify = read + label + archive; compose = drafts + sending unsubscribe emails
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
]
LABEL_PREFIX = "Tidi"


def get_service():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _header(headers, name):
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _body_text(payload, limit=3000):
    """Pull plain text out of a message, falling back to stripped HTML."""
    parts = {"text/plain": [], "text/html": []}

    def walk(part):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mime in parts:
            parts[mime].append(base64.urlsafe_b64decode(data).decode("utf-8", "ignore"))
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    if parts["text/plain"]:
        text = " ".join(parts["text/plain"])
    else:
        text = " ".join(parts["text/html"])
        text = re.sub(r"<style.*?</style>|<script.*?</script>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def fetch_recent(service, hours=24, max_results=50):
    """Return unread inbox emails from the last `hours` hours."""
    days = max(1, round(hours / 24))
    query = f"in:inbox is:unread newer_than:{days}d"
    resp = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    emails = []
    for ref in resp.get("messages", []):
        msg = service.users().messages().get(userId="me", id=ref["id"], format="full").execute()
        headers = msg["payload"].get("headers", [])
        emails.append({
            "id": msg["id"],
            "thread_id": msg["threadId"],
            "from": _header(headers, "From"),
            "to": _header(headers, "To"),
            "subject": _header(headers, "Subject"),
            "date": _header(headers, "Date"),
            "message_id": _header(headers, "Message-ID"),
            "list_unsubscribe": _header(headers, "List-Unsubscribe"),
            "list_unsubscribe_post": _header(headers, "List-Unsubscribe-Post"),
            "snippet": msg.get("snippet", ""),
            "body": _body_text(msg["payload"]),
        })
    return emails


def ensure_label(service, name):
    full = f"{LABEL_PREFIX}/{name}"
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for lab in labels:
        if lab["name"] == full:
            return lab["id"]
    created = service.users().labels().create(
        userId="me", body={"name": full, "labelListVisibility": "labelShow",
                           "messageListVisibility": "show"}).execute()
    return created["id"]


def label_message(service, msg_id, label_name, archive=False):
    body = {"addLabelIds": [ensure_label(service, label_name)]}
    if archive:
        body["removeLabelIds"] = ["INBOX"]
    service.users().messages().modify(userId="me", id=msg_id, body=body).execute()


def create_reply_draft(service, email, reply_text):
    """Create a draft reply in the same thread. It is NOT sent."""
    to_addr = parseaddr(email["from"])[1]
    subject = email["subject"] or ""
    if not subject.lower().startswith("re:"):
        subject = "Re: " + subject
    mime = MIMEText(reply_text)
    mime["To"] = to_addr
    mime["Subject"] = subject
    if email.get("message_id"):
        mime["In-Reply-To"] = email["message_id"]
        mime["References"] = email["message_id"]
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
    service.users().drafts().create(
        userId="me", body={"message": {"raw": raw, "threadId": email["thread_id"]}}).execute()


def unsubscribe(service, email):
    """Use the sender's own List-Unsubscribe header. Returns a short status string.

    Only called after the user approves it in Telegram.
    """
    header = email.get("list_unsubscribe", "")
    links = re.findall(r"<([^>]+)>", header)
    https = [l for l in links if l.lower().startswith("https://")]
    mailto = [l for l in links if l.lower().startswith("mailto:")]

    # 1) One-click unsubscribe (RFC 8058) - the safest option.
    if https and "one-click" in email.get("list_unsubscribe_post", "").lower():
        r = requests.post(https[0], data={"List-Unsubscribe": "One-Click"}, timeout=15)
        if r.ok:
            return "unsubscribed (one-click)"
    # 2) mailto: send the unsubscribe email the sender asked for.
    if mailto:
        addr, _, query = mailto[0][7:].partition("?")
        subj = re.search(r"subject=([^&]+)", query or "", re.I)
        msg = MIMEText("unsubscribe")
        msg["To"] = addr
        msg["Subject"] = requests.utils.unquote(subj.group(1)) if subj else "unsubscribe"
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return "unsubscribed (email sent)"
    # 3) Plain link: don't visit it automatically, hand it to the user.
    if https:
        return f"open this link to finish: {https[0]}"
    return "no unsubscribe option found"
