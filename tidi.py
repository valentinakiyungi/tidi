"""Tidi - your inbox-tidying agent.  Run with:  python tidi.py

Every CHECK_EVERY_HOURS (default 3) it:
  - sorts new unread mail with AI and labels it in Gmail (Tidi/...)
  - archives bulk mail (newsletters, promotions, notifications)
  - saves reply drafts in Gmail Drafts (it never sends replies itself)
  - alerts your phone ONLY if something important or needing a reply arrived

Every morning at BRIEF_TIME it sends a summary of everything since the last
brief, and asks before unsubscribing you from anything.
"""
import datetime as dt
import logging
import os
import secrets
import time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

import core  # noqa: E402
import gmail_client as gm  # noqa: E402
import notify  # noqa: E402
import store  # noqa: E402

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("tidi")

TZ = ZoneInfo(os.getenv("TIMEZONE", "UTC"))
BRIEF_TIME = dt.time(*map(int, os.getenv("BRIEF_TIME", "07:30").split(":")))
CHECK_HOURS = float(os.getenv("CHECK_EVERY_HOURS", "3"))
LOOKBACK = int(os.getenv("LOOKBACK_HOURS", "24"))
MAX_EMAILS = int(os.getenv("MAX_EMAILS", "50"))
MAX_UNSUB_QUESTIONS = int(os.getenv("MAX_UNSUB_QUESTIONS", "5"))

HELP = ("Tidi commands:\n"
        "brief - send the summary now\n"
        "check - check for new email now\n"
        "help - show this message")

state = store.load()


def make_notifier():
    via = os.getenv("NOTIFY_VIA", "ntfy").lower()
    if via == "telegram":
        return notify.Telegram(os.environ["TELEGRAM_BOT_TOKEN"],
                               os.getenv("TELEGRAM_CHAT_ID"), state)
    topic = os.getenv("NTFY_TOPIC", "").strip() or state.get("ntfy_topic")
    if not topic:
        # Long random name so nobody can guess it and read your alerts.
        topic = "tidi-" + secrets.token_hex(8)
        state["ntfy_topic"] = topic
        store.save(state)
    print("\n" + "=" * 60)
    print(f"  In the ntfy app, subscribe to this topic:  {topic}")
    print("=" * 60 + "\n")
    return notify.Ntfy(os.getenv("NTFY_SERVER", "https://ntfy.sh"), topic, state,
                       os.getenv("NTFY_TOKEN") or None)


notifier = make_notifier()


def do_check(manual=False):
    service = gm.get_service()
    rows = core.process_new(service, state, LOOKBACK, MAX_EMAILS)
    text = core.alert_text(rows)
    if text:
        notifier.send(text, title="📬 New email needs you", urgent=True)
    elif manual:
        quiet = len(rows)
        notifier.send(f"Nothing that needs you right now. ✨"
                      + (f" ({quiet} bulk email(s) tidied away.)" if quiet else ""),
                      title="Tidi")
    state["last_check"] = time.time()
    store.save(state)
    log.info("Check done: %d new, alert=%s", len(rows), bool(text))


def do_brief():
    service = gm.get_service()
    core.process_new(service, state, LOOKBACK, MAX_EMAILS)
    notifier.send(core.brief_text(state), title="☀️ Your morning brief")

    queue = state.get("unsub_queue", [])
    ask, state["unsub_queue"] = queue[:MAX_UNSUB_QUESTIONS], queue[MAX_UNSUB_QUESTIONS:]
    pending = state.setdefault("pending", {})
    asked = state.setdefault("asked_senders", [])
    for email in ask:
        pending[email["id"]] = email
        asked.append(email["from"])
        notifier.ask_unsubscribe(email)
    state["asked_senders"] = asked[-500:]

    state["last_brief_date"] = dt.datetime.now(TZ).date().isoformat()
    state["last_check"] = time.time()
    store.save(state)
    log.info("Brief sent")


def handle(action, arg, reply):
    if action in ("u", "k"):
        email = state.setdefault("pending", {}).pop(arg, None)
        if not email:
            reply("That question has expired.")
        elif action == "k":
            reply(f"Kept: {core.who(email['from'])}")
        else:
            try:
                result = gm.unsubscribe(gm.get_service(), email)
            except Exception as exc:
                result = f"failed: {exc}"
            reply(f"{core.who(email['from'])} → {result}")
        store.save(state)
    elif action == "brief":
        do_brief()
    elif action == "check":
        do_check(manual=True)
    elif action in ("start", "help"):
        reply(f"Hi, I'm Tidi! 🧹 I check your inbox every {CHECK_HOURS:g} hours and "
              f"send your brief at {BRIEF_TIME:%H:%M}.\n\n{HELP}")


def due_for_brief(now):
    if "last_brief_date" not in state:
        # First run: if today's brief time already passed, start tomorrow.
        state["last_brief_date"] = (now.date().isoformat() if now.time() >= BRIEF_TIME
                                    else (now.date() - dt.timedelta(days=1)).isoformat())
    return now.time() >= BRIEF_TIME and state["last_brief_date"] != now.date().isoformat()


def main():
    gm.get_service()  # first run: opens the browser to allow Gmail access
    if notifier.ready:
        notifier.send(f"Tidi is running. Checking every {CHECK_HOURS:g} hours, "
                      f"brief at {BRIEF_TIME:%H:%M}.", title="🧹 Tidi")
    else:
        log.warning("TELEGRAM_CHAT_ID not set - send /start to your bot to get it.")

    last_error, last_error_time = None, 0.0
    while True:
        try:
            for action, arg, reply in notifier.poll():
                handle(action, arg, reply)
            store.save(state)
            if not notifier.ready:
                continue
            now = dt.datetime.now(TZ)
            if due_for_brief(now):
                do_brief()
            elif CHECK_HOURS > 0 and time.time() - state.get("last_check", 0) >= CHECK_HOURS * 3600:
                do_check()
        except KeyboardInterrupt:
            print("Bye!")
            break
        except Exception as exc:
            log.exception("Problem")
            # Tell you about a problem, but at most once an hour per problem.
            msg = str(exc)[:300]
            if msg != last_error or time.time() - last_error_time > 3600:
                try:
                    notifier.send(f"Tidi hit a problem: {msg}", title="⚠️ Tidi")
                except Exception:
                    pass
                last_error, last_error_time = msg, time.time()
            time.sleep(60)


if __name__ == "__main__":
    main()
