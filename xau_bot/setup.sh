#!/bin/bash
# XAU/USD Trading Bot — one-time setup
# Run once: bash setup.sh
# After that the bot starts automatically on every reboot.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
PID_FILE="$SCRIPT_DIR/bot.pid"

echo ""
echo "╔══════════════════════════════════════╗"
echo "║   XAU/USD Paper Trader — Setup       ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ── 1. Create virtual environment ──────────────────────────────────────────
if [ ! -d "$VENV" ]; then
    echo "▶ Creating virtual environment..."
    python3 -m venv "$VENV"
else
    echo "✓ Virtual environment already exists"
fi

# ── 2. Install dependencies ─────────────────────────────────────────────────
echo "▶ Installing dependencies..."
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"
echo "✓ Dependencies installed"

# ── 3. Create start_bot.sh ───────────────────────────────────────────────────
cat > "$SCRIPT_DIR/start_bot.sh" << STARTSCRIPT
#!/bin/bash
SCRIPT_DIR="\$(cd "\$(dirname "\$0")" && pwd)"
PID_FILE="\$SCRIPT_DIR/bot.pid"

# Don't start if already running
if [ -f "\$PID_FILE" ] && kill -0 "\$(cat "\$PID_FILE")" 2>/dev/null; then
    echo "Bot is already running (PID \$(cat "\$PID_FILE"))"
    exit 0
fi

nohup "\$SCRIPT_DIR/.venv/bin/python" "\$SCRIPT_DIR/daemon.py" --demo \
    >> "\$SCRIPT_DIR/xau_bot.log" 2>&1 &
echo \$! > "\$PID_FILE"
echo "✓ Bot started (PID \$(cat "\$PID_FILE")) — demo mode"
echo "  View stats:  cd \$SCRIPT_DIR && python stats.py"
echo "  Watch logs:  tail -f \$SCRIPT_DIR/xau_bot.log"
STARTSCRIPT

chmod +x "$SCRIPT_DIR/start_bot.sh"

# ── 4. Create stop_bot.sh ────────────────────────────────────────────────────
cat > "$SCRIPT_DIR/stop_bot.sh" << STOPSCRIPT
#!/bin/bash
SCRIPT_DIR="\$(cd "\$(dirname "\$0")" && pwd)"
PID_FILE="\$SCRIPT_DIR/bot.pid"
if [ -f "\$PID_FILE" ]; then
    PID=\$(cat "\$PID_FILE")
    if kill -0 "\$PID" 2>/dev/null; then
        kill "\$PID"
        echo "✓ Bot stopped (PID \$PID)"
    else
        echo "Bot was not running"
    fi
    rm -f "\$PID_FILE"
else
    echo "Bot is not running (no PID file)"
fi
STOPSCRIPT

chmod +x "$SCRIPT_DIR/stop_bot.sh"

# ── 5. Auto-start on reboot via cron (skip if crontab unavailable) ───────────
if command -v crontab &>/dev/null; then
    CRON_JOB="@reboot $SCRIPT_DIR/start_bot.sh >> $SCRIPT_DIR/xau_bot.log 2>&1"
    ( crontab -l 2>/dev/null | grep -v "start_bot.sh" ; echo "$CRON_JOB" ) | crontab -
    echo "✓ Auto-start on reboot configured (cron @reboot)"
else
    echo "⚠ crontab not available — bot won't auto-start on reboot (run ./start_bot.sh manually)"
fi

# ── 6. Start now ─────────────────────────────────────────────────────────────
echo ""
echo "▶ Starting bot now..."
"$SCRIPT_DIR/start_bot.sh"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  Setup complete! The bot is now running in the           ║"
echo "║  background and will restart automatically on reboot.    ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Daily commands:                                         ║"
echo "║                                                          ║"
echo "║   python stats.py       ← check your stats any time     ║"
echo "║   ./start_bot.sh        ← start the bot                 ║"
echo "║   ./stop_bot.sh         ← stop the bot                  ║"
echo "║   tail -f xau_bot.log   ← watch live activity           ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Want real market data instead of demo?                  ║"
echo "║  Edit start_bot.sh and remove the --demo flag.          ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
