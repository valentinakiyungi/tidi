#!/bin/bash
# Start Tidi and keep the Mac awake while it runs.
#   bash run.sh          start Tidi
#   bash run.sh --test   run the self-test
cd "$(dirname "$0")"
source venv/bin/activate
pip install -q -r requirements.txt
if [[ "$(uname)" == "Darwin" ]]; then
  # caffeinate stops the Mac from sleeping while Tidi runs
  # (it needs to stay plugged in with the lid open).
  exec caffeinate -i python tidi.py "$@"
else
  exec python tidi.py "$@"
fi
