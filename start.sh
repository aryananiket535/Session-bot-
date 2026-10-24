#!/bin/bash
cd "$(dirname "$0")"

# Terminate only this specific bot's previous instance to prevent cross-bot conflict
pkill -f "/root/session_manager/venv/bin/python.*main\.py" 2>/dev/null || true
screen -X -S session_manager quit 2>/dev/null || true
sleep 1

mkdir -p accstore_data

echo "Starting AccStore Session Manager in separate screen session..."
screen -dmS session_manager bash -c 'cd /root/session_manager && while true; do ./venv/bin/python main.py 2>&1 | tee -a accstore_data/bot.log; echo "Session Manager exited. Restarting in 3s..."; sleep 3; done'

sleep 2
echo "Bot is now running in screen: session_manager! 🚀"
echo "To view live logs: tail -f /root/session_manager/accstore_data/bot.log"
echo "To attach to screen console: screen -r session_manager"
