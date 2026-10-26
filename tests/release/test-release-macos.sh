#!/bin/bash
# Release tests for the macOS build. Needs only a Mac with the build in dist/ (no Python).
# Usage: tests/release/test-release-macos.sh dist [results folder]
# The app is pointed at a data folder under the results folder (POSTQUEUE_DATA), so the tester's own queue is untouched.
DIST="${1:?dist folder}"; OUT="${2:-release-results}"
mkdir -p "$OUT/data"; echo '{"close_to_tray": false, "start_with_windows": false}' > "$OUT/data/settings.json"
export POSTQUEUE_DATA="$OUT/data"; LOCK="$OUT/data/postqueue.lock"
APP="$DIST/PostQueue.app"; BIN="$APP/Contents/MacOS/PostQueue"
pass=0; fail=0; results="$OUT/results.md"; echo "# Release test results (macOS $(sw_vers -productVersion), $(uname -m), $(date '+%Y-%m-%d %H:%M'))" > "$results"; echo >> "$results"; echo "| Test | Result | Detail |" >> "$results"; echo "|---|---|---|" >> "$results"
check() { if [ "$2" = "0" ]; then pass=$((pass+1)); r=PASS; else fail=$((fail+1)); r=FAIL; fi; echo "$r  $1 $3"; echo "| $1 | $r | $3 |" >> "$results"; }
wait_lock() { for i in $(seq 1 100); do [ -f "$LOCK" ] && return 0; sleep 0.3; done; return 1; }
wait_gone() { for i in $(seq 1 50); do kill -0 "$1" 2>/dev/null || return 0; sleep 0.3; done; return 1; }
app_pid() { python3 - "$LOCK" <<'EOF' 2>/dev/null || sed -n 's/.*"pid": *\([0-9]*\).*/\1/p' "$LOCK"
import json, sys; print(json.load(open(sys.argv[1]))['pid'])
EOF
}

[ -d "$APP" ]; check 'App bundle present' $? "$APP"
plutil -lint "$APP/Contents/Info.plist" >/dev/null 2>&1; check 'Info.plist is valid' $?
grep -q "$(uname -m)" <<< "$(file "$BIN")"; check 'CPU type matches this Mac' $? "$(file -b "$BIN" | cut -c1-60)"
[ -f "$APP/Contents/Resources/THIRD_PARTY_NOTICES.txt" ] || [ -f "$APP/Contents/Frameworks/THIRD_PARTY_NOTICES.txt" ]; check 'Carries third-party notices' $?
codesign --verify --deep --strict "$APP" 2>/dev/null; check 'Signature (informational)' 0 "$([ $? = 0 ] && echo signed || echo 'not signed')"
for f in "$DIST"/PostQueue-*-macos-*.dmg; do
  [ -f "$f" ] || continue
  recorded="$(cut -d' ' -f1 "$f.sha256.txt" 2>/dev/null)"; actual="$(shasum -a 256 "$f" | cut -d' ' -f1)"
  [ "$recorded" = "$actual" ]; check 'DMG matches recorded SHA-256' $?
  mnt="$(hdiutil attach "$f" -nobrowse -readonly | tail -1 | awk -F'\t' '{print $NF}')"
  [ -d "$mnt/PostQueue.app" ] && [ -L "$mnt/Applications" ]; check 'DMG holds the app and an Applications link' $?
  hdiutil detach "$mnt" >/dev/null 2>&1
done

rm -f "$LOCK"
t0=$(date +%s); "$BIN" & launched=$!
wait_lock; check 'Starts and writes its lock' $? "$(( $(date +%s) - t0 )) s"
pid="$(app_pid)"; [ "$pid" = "$launched" ]; check 'Lock records the app process' $?
[ -f "$OUT/data/postqueue.log" ] && [ -d "$OUT/data/media" ]; check 'Queue and log files created' $?
"$BIN"; second=$?; sleep 1; rm -f "$OUT/data/show.request"
[ "$second" = "0" ] && kill -0 "$pid" 2>/dev/null; check 'Second launch exits and leaves the first running' $?
osascript -e 'tell application "PostQueue" to quit' >/dev/null 2>&1 || kill -TERM "$pid"
wait_gone "$pid" && [ ! -f "$LOCK" ]; check 'Clean quit removes lock' $?
"$BIN" & launched=$!; wait_lock; kill -KILL "$launched"; wait_gone "$launched"
[ -f "$LOCK" ]; check 'Crash leaves lock behind' $?
"$BIN" & launched=$!; wait_lock; sleep 1
[ "$(app_pid)" = "$launched" ]; check 'Relaunch after crash takes over' $?
kill -TERM "$launched"; wait_gone "$launched"

echo >> "$results"; echo "**$pass passed, $fail failed.**" >> "$results"
echo "$pass passed, $fail failed. Results: $OUT"
exit $fail
