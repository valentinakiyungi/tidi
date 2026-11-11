"""Tidi - your inbox-tidying agent.

  python tidi.py          start Tidi
  python tidi.py --test   check that everything is set up, without changing any email

Every CHECK_EVERY_HOURS (default 3) Tidi:
  - sorts new unread mail with AI and labels it in Gmail (Tidi/...)
  - archives bulk mail (newsletters, promotions, notifications)
  - saves reply drafts in Gmail Drafts (it never sends replies itself)
  - alerts your phone ONLY if something important or needing a reply arrived

Every morning at BRIEF_TIME it sends a summary of everything since the last
brief, and asks before unsubscribing you from anything.
If anything goes wrong, it tells you on your phone in plain words.
"""
import datetime as dt
import logging
import os
import secrets
import sys
import time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

os.chdir(os.path.dirname(os.path.abspath(__file__)))  # always use files in the tidi folder
load_dotenv()

import brain  # noqa: E402
import core  # noqa: E402
import gmail_client as gm  # noqa: E402
import notify  # noqa: E402
import store  # noqa: E402

logging.basicConfig(format="%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M",
                    level=logging.INFO,
                    handlers=[logging.StreamHandler(), logging.FileHandler("tidi.log")])
for noisy in ("googleapiclient", "urllib3", "google_auth_oauthlib"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
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


def explain(exc):
    """Plain-English description of a problem."""
    msg = str(exc)
    if isinstance(exc, brain.AIError):
        return "Tidi couldn't sort your email because " + msg
    if "invalid_grant" in msg or "Token has been expired or revoked" in msg:
        return ("Your Google sign-in expired. On your Mac: stop Tidi (Ctrl+C), "
                "run  rm token.json  and start Tidi again to sign in.")
    if "insufficient authentication scopes" in msg or "insufficientPermissions" in msg:
        return ("Tidi doesn't have all the Gmail permissions it needs. Stop Tidi, run  "
                "rm token.json  and start it again. When signing in, tick EVERY box.")
    if isinstance(exc, FileNotFoundError):
        return msg
    if "Gmail API has not been used" in msg or "accessNotConfigured" in msg:
        return "The Gmail API isn't enabled in your Google Cloud project. Enable it and restart Tidi."
    if "ConnectionError" in type(exc).__name__ or "Max retries exceeded" in msg:
        return "Tidi couldn't reach the internet. It will try again in a minute."
    return f"Tidi hit a problem: {msg[:300]}"


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


notifier = None


def do_check(report=False):
    """report=True: always tell you the result, even if nothing needs you."""
    log.info("Checking your inbox...")
    service = gm.get_service()
    rows, found = core.process_new(service, state, LOOKBACK, MAX_EMAILS, log=log.info)
    alert = core.alert_text(rows)
    if alert:
        notifier.send(alert, title="📬 New email needs you", urgent=True)
    if report:
        notifier.send(core.summary_text(rows, found), title="🧹 Tidi check done")
    state["last_check"] = time.time()
    store.save(state)
    log.info("Check done: %d sorted, alert sent: %s", len(rows), "yes" if alert else "no")


def do_brief():
    log.info("Preparing your morning brief...")
    service = gm.get_service()
    try:
        core.process_new(service, state, LOOKBACK, MAX_EMAILS, log=log.info)
    except brain.AIError as exc:
        notifier.send(explain(exc), title="⚠️ Tidi")
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
    log.info("You sent: %s", action if not arg else f"{action} button")
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
        do_check(report=True)
    elif action in ("start", "help"):
        reply(f"Hi, I'm Tidi! 🧹 I check your inbox every {CHECK_HOURS:g} hours and "
              f"send your brief at {BRIEF_TIME:%H:%M}.\n\n{HELP}")
    else:
        reply(f"I don't know '{action}'.\n\n{HELP}")


def due_for_brief(now):
    if "last_brief_date" not in state:
        # First run: if today's brief time already passed, start tomorrow.
        state["last_brief_date"] = (now.date().isoformat() if now.time() >= BRIEF_TIME
                                    else (now.date() - dt.timedelta(days=1)).isoformat())
    return now.time() >= BRIEF_TIME and state["last_brief_date"] != now.date().isoformat()


# ---------------------------------------------------------------- self-test
def run_test():
    ok_all = True

    def show(ok, name, detail=""):
        nonlocal ok_all
        ok_all = ok_all and ok
        print(f"  {'✅' if ok else '❌'}  {name}" + (f"\n      {detail}" if detail else ""))

    print("\nTidi self-test (no email will be changed)\n")

    show(os.path.exists(".env"), "Settings file (.env)",
         "" if os.path.exists(".env") else "Missing. Run:  cp .env.example .env")
    key_hint = f"{len(brain.API_KEY)} characters" if brain.API_KEY else "EMPTY"
    print(f"      AI provider: {brain.PROVIDER}, model: {brain.MODEL}, key: {key_hint}")

    try:
        brain.self_test()
        show(True, "AI connection", f"{brain.PROVIDER} answered correctly")
    except Exception as exc:
        show(False, "AI connection", explain(exc))

    try:
        service = gm.get_service()
        n = gm.count_unread(service, LOOKBACK)
        show(True, "Gmail access",
             f"{n}{'+' if n == 100 else ''} unread email(s) in your inbox from the last "
             f"{LOOKBACK}h" + ("  (Tidi only sorts UNREAD email, so it will have nothing "
                              "to do until new mail arrives)" if n == 0 else ""))
    except Exception as exc:
        show(False, "Gmail access", explain(exc))

    try:
        global notifier
        notifier = make_notifier()
        if notifier.ready:
            notifier.send("This is a test from Tidi. If you see this, alerts work! 🎉",
                          title="🧹 Tidi test")
            show(True, "Phone alerts", "A test notification was sent. Did it arrive?")
        else:
            show(False, "Phone alerts", "TELEGRAM_CHAT_ID is empty. Start Tidi and send /start.")
    except Exception as exc:
        show(False, "Phone alerts", explain(exc))

    print("\n" + ("All good! Start Tidi with:  python tidi.py" if ok_all
                  else "Fix the ❌ items above, then run  python tidi.py --test  again.") + "\n")


# ---------------------------------------------------------------- main loop
def single_instance():
    """Stop if Tidi is already running (e.g. in the background AND in a terminal)."""
    import fcntl
    lock = open("tidi.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("Tidi is already running (maybe in the background). "
              "Check with:  bash service.sh status")
        sys.exit(0)
    return lock  # keep the file open for as long as Tidi runs


def main():
    global notifier
    _lock = single_instance()  # noqa: F841
    notifier = make_notifier()
    gm.get_service()  # first run: opens the browser to allow Gmail access
    if not notifier.ready:
        log.warning("TELEGRAM_CHAT_ID not set - send /start to your bot to get it.")
    else:
        notifier.send(f"Tidi is running. Checking every {CHECK_HOURS:g} hours, "
                      f"brief at {BRIEF_TIME:%H:%M}. Doing a first check now...",
                      title="🧹 Tidi")
    log.info("Tidi started. Keep this window open. Press Ctrl+C to stop.")

    first_check = True
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
            elif first_check or (CHECK_HOURS > 0 and
                                 time.time() - state.get("last_check", 0) >= CHECK_HOURS * 3600):
                do_check(report=first_check)
            first_check = False
            last_error = None
        except KeyboardInterrupt:
            print("\nTidi stopped. Bye! 👋")
            break
        except Exception as exc:
            text = explain(exc)
            log.error(text)
            if not isinstance(exc, brain.AIError):
                log.debug("details", exc_info=True)
            # Tell you about a problem, but at most once an hour per problem.
            if text != last_error or time.time() - last_error_time > 3600:
                try:
                    notifier.send(text, title="⚠️ Tidi needs attention", urgent=True)
                except Exception:
                    pass
                last_error, last_error_time = text, time.time()
            first_check = False
            state["last_check"] = time.time() - max(0, CHECK_HOURS * 3600 - 900)  # retry in 15 min
            try:
                time.sleep(60)
            except KeyboardInterrupt:
                print("\nTidi stopped. Bye! 👋")
                break


if __name__ == "__main__":
    if "--test" in sys.argv:
        run_test()
    else:
        main()
