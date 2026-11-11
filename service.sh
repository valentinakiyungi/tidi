#!/bin/bash
# Run Tidi in the background on a Mac, without VS Code or a terminal.
#
#   bash service.sh install    start Tidi now and every time you log in
#   bash service.sh status     is it running?
#   bash service.sh logs       show the last lines of tidi.log
#   bash service.sh restart    restart it (after changing code or .env)
#   bash service.sh stop       stop it until next login
#   bash service.sh uninstall  remove the background service completely
set -e
LABEL="com.tidi.agent"
DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
TARGET="gui/$(id -u)"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "This script is for macOS. On Linux, use a systemd service instead."; exit 1
fi

write_plist() {
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string>
    <string>-is</string>
    <string>$DIR/venv/bin/python</string>
    <string>$DIR/tidi.py</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key>
  <dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>60</integer>
  <key>StandardOutPath</key><string>$DIR/service.log</string>
  <key>StandardErrorPath</key><string>$DIR/service.log</string>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
</dict>
</plist>
EOF
}

is_loaded() { launchctl print "$TARGET/$LABEL" >/dev/null 2>&1; }

case "${1:-}" in
  install)
    [[ -x "$DIR/venv/bin/python" ]] || { echo "❌ venv not found. Run: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"; exit 1; }
    [[ -f "$DIR/token.json" ]] || { echo "❌ Sign in to Gmail first: run  bash run.sh  once, approve in the browser, then Ctrl+C."; exit 1; }
    if ! is_loaded && pgrep -f "tidi.py" >/dev/null; then
      echo "❌ Tidi is already running in a terminal. Go to that terminal, press Ctrl+C,"
      echo "   then run  bash service.sh install  again."; exit 1
    fi
    "$DIR/venv/bin/pip" install -q -r "$DIR/requirements.txt"
    is_loaded && launchctl bootout "$TARGET/$LABEL" 2>/dev/null || true
    write_plist
    launchctl bootstrap "$TARGET" "$PLIST"
    sleep 3
    echo "✅ Tidi is now running in the background and will start every time you log in."
    echo "   You can close VS Code. Check it with:  bash service.sh status"
    ;;
  status)
    if is_loaded && launchctl print "$TARGET/$LABEL" | grep -q "state = running"; then
      echo "✅ Tidi is running in the background."
    elif is_loaded; then
      echo "⚠️  Tidi is installed but not running. Last lines of service.log:"; tail -n 15 "$DIR/service.log" 2>/dev/null
    else
      echo "❌ Tidi's background service is not installed. Run:  bash service.sh install"
    fi
    echo; echo "Last activity:"; tail -n 5 "$DIR/tidi.log" 2>/dev/null || true
    ;;
  logs)
    tail -n 30 "$DIR/tidi.log"
    ;;
  restart)
    launchctl kickstart -k "$TARGET/$LABEL" && echo "✅ Tidi restarted."
    ;;
  stop)
    launchctl bootout "$TARGET/$LABEL" 2>/dev/null && echo "⏸  Tidi stopped. It will start again at next login, or run: bash service.sh install"
    ;;
  uninstall)
    launchctl bootout "$TARGET/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "🗑  Background service removed. Your files are untouched."
    ;;
  *)
    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
    ;;
esac
