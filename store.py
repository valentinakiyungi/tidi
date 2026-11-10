"""Tidi's small memory, saved in state.json next to the code.

It remembers which emails were already handled (so nothing is drafted twice),
what happened since the last brief, and which unsubscribe questions are open.
"""
import json
import os

PATH = os.getenv("TIDI_STATE_FILE", "state.json")


def load():
    try:
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save(state):
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PATH)
