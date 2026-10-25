#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Stop only the named screen session; never kill arbitrary processes by port/pattern.
screen -S session_manager -X quit 2>/dev/null || true
mkdir -p accstore_data

echo "Starting Onyx Session Manager in screen session..."
screen -dmS session_manager bash -lc "cd '$SCRIPT_DIR' && while true; do ./venv/bin/python main.py 2>&1 | tee -a accstore_data/bot.log; echo 'Session Manager exited. Restarting in 3s...'; sleep 3; done"

sleep 2
echo "Onyx is running in screen: session_manager"
echo "To view live logs: tail -f '$SCRIPT_DIR/accstore_data/bot.log'"
echo "To attach to screen console: screen -r session_manager"
