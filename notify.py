"""How Tidi talks to you. Two options, chosen with NOTIFY_VIA in .env:

  ntfy     - push notifications through the free ntfy phone app (no Telegram needed)
  telegram - messages from your Telegram bot

Both support: sending a message, asking "Unsubscribe?" with buttons, and
receiving your taps or commands (brief / check / help).

poll() returns a list of (action, arg, reply) tuples:
  action: "u" (unsubscribe), "k" (keep), or a command like "brief", "check", "help"
  reply:  function(text) to answer that specific tap
"""
import json
import time

import requests


class Telegram:
    def __init__(self, token, chat_id, state):
        self.api = f"https://api.telegram.org/bot{token}/"
        self.chat = str(chat_id or "").strip()
        self.state = state

    @property
    def ready(self):
        return bool(self.chat)

    def _call(self, method, **params):
        r = requests.post(self.api + method, json=params, timeout=40)
        r.raise_for_status()
        return r.json().get("result")

    def send(self, text, title=None, urgent=False):
        if not self.chat:
            return
        if title:
            text = f"{title}\n\n{text}"
        self._call("sendMessage", chat_id=self.chat, text=text[:4000],
                   disable_notification=False)

    def ask_unsubscribe(self, email):
        self._call("sendMessage", chat_id=self.chat,
                   text=f"Unsubscribe from this?\n{email['from']}\n“{email['subject']}”",
                   reply_markup={"inline_keyboard": [[
                       {"text": "✅ Unsubscribe", "callback_data": f"u:{email['id']}"},
                       {"text": "Keep", "callback_data": f"k:{email['id']}"}]]})

    def poll(self):
        """Waits up to 20 seconds for new messages or button taps."""
        updates = self._call("getUpdates", offset=self.state.get("tg_offset", 0), timeout=20,
                             allowed_updates=["message", "callback_query"]) or []
        out = []
        for u in updates:
            self.state["tg_offset"] = u["update_id"] + 1
            if "message" in u:
                chat = str(u["message"]["chat"]["id"])
                if not self.chat:
                    self._call("sendMessage", chat_id=chat, text=(
                        f"Your chat id is {chat}.\nPut it in .env as TELEGRAM_CHAT_ID "
                        "and restart Tidi."))
                    continue
                if chat != self.chat:
                    continue  # ignore strangers
                words = (u["message"].get("text") or "").strip().lstrip("/").split()
                if words:
                    out.append((words[0].split("@")[0].lower(), None, self.send))
            elif "callback_query" in u:
                cq = u["callback_query"]
                self._call("answerCallbackQuery", callback_query_id=cq["id"])
                if str(cq["message"]["chat"]["id"]) != self.chat:
                    continue
                action, _, arg = (cq.get("data") or "").partition(":")
                mid = cq["message"]["message_id"]
                out.append((action, arg, lambda text, mid=mid: self._call(
                    "editMessageText", chat_id=self.chat, message_id=mid, text=text)))
        return out


class Ntfy:
    """Free push notifications via https://ntfy.sh (Android & iPhone app).

    Tidi publishes to TOPIC (subscribe to it in the app).
    Buttons and your commands go to TOPIC-replies, which Tidi reads.
    """

    def __init__(self, server, topic, state, token=None):
        self.server = server.rstrip("/")
        self.topic = topic
        self.reply_topic = topic + "-replies"
        self.state = state
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        if self.state.get("ntfy_since_topic") != topic:  # new topic: start listening from now
            self.state["ntfy_since"] = str(int(time.time()))
            self.state["ntfy_since_topic"] = topic
        self.state.setdefault("ntfy_since", str(int(time.time())))

    ready = True

    def _publish(self, **msg):
        msg["topic"] = self.topic
        r = requests.post(self.server, json=msg, headers=self.headers, timeout=20)
        r.raise_for_status()

    def _button(self, label, body):
        return {"action": "http", "label": label, "url": f"{self.server}/{self.reply_topic}",
                "method": "POST", "body": body, "headers": self.headers, "clear": True}

    def send(self, text, title=None, urgent=False):
        self._publish(title=title or "Tidi", message=text[:4000],
                      priority=4 if urgent else 3,
                      tags=["envelope"] if urgent else ["broom"],
                      actions=[self._button("Check now", "check")])

    def ask_unsubscribe(self, email):
        self._publish(title="Unsubscribe?", message=f"{email['from']}\n“{email['subject']}”",
                      priority=2, tags=["wastebasket"],
                      actions=[self._button("✅ Unsubscribe", f"u:{email['id']}"),
                               self._button("Keep", f"k:{email['id']}")])

    def poll(self):
        """Checks the reply topic for taps/commands, then waits 10 seconds."""
        out = []
        r = requests.get(f"{self.server}/{self.reply_topic}/json",
                         params={"poll": "1", "since": self.state["ntfy_since"]},
                         headers=self.headers, timeout=20)
        r.raise_for_status()
        for line in r.text.splitlines():
            if not line.strip():
                continue
            msg = json.loads(line)
            if msg.get("event") != "message":
                continue
            self.state["ntfy_since"] = msg["id"]
            body = (msg.get("message") or "").strip()
            action, sep, arg = body.partition(":")
            if sep and action in ("u", "k"):
                out.append((action, arg, lambda text: self.send(text, title="Tidi")))
            elif body:
                out.append((body.lstrip("/").split()[0].lower(), None, self.send))
        time.sleep(10)
        return out
