#!/usr/bin/env python3
"""XAUUSD Precision Engine v8.0 — Daily Chart Scanner.

Usage:
  python scan.py d1.png h4.png m15.png          # analyse three screenshots
  python scan.py d1.png h4.png m15.png --stream  # stream token-by-token
  python scan.py --demo                          # demo without real images / API key
"""

import argparse
import base64
import json
import sys
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# ── Precision Engine v8.0 system prompt ─────────────────────────────────────

SYSTEM_PROMPT = """You are an elite XAUUSD institutional trader with 20+ years experience.
The user will send you D1, H4 and M15 chart screenshots once per day around 12:45 UK time
alongside today's live high-impact USD/XAU news.

Your task is to run the XAUUSD PRECISION ENGINE v8.0 and deliver exactly ONE clean verdict.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 XAUUSD PRECISION ENGINE v8.0 — 6-STEP FILTERING SYSTEM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

STEP 1 — NEWS FILTER (HARD BLOCK)
Check the news context provided. Block the trade if:
• Any HIGH-impact USD or XAU/gold news event is within 2 hours (before OR after now)
• Events that ALWAYS block: NFP, CPI, FOMC statement/minutes/rate decision, Fed Chair speech,
  PPI, Retail Sales, GDP (advance), PCE, ISM, ADP, Unemployment Claims if surprise >30K,
  any "Gold" or "XAU" tagged event
• If blocked → output NO TRADE with news reason. STOP here.

STEP 2 — D1 BIAS
Read the Daily chart:
• Direction: Is price above or below the 200 EMA / major structure?
• Key daily levels: recent swing highs/lows, unfilled Fair Value Gaps (FVGs), Order Blocks (OBs)
• Determine: BULLISH BIAS, BEARISH BIAS, or NEUTRAL (no trade if neutral and no clear setup)
• Note the D1 Premium/Discount zone (above/below equilibrium = 50% of the most recent D1 range)

STEP 3 — H4 ZONE CONFLUENCE
Read the H4 chart:
• Is price currently inside or approaching a valid H4 Order Block or FVG?
• Does H4 zone ALIGN with D1 bias? (same direction = green light, opposite = abort)
• If no valid H4 zone within 15 pips → NO TRADE

STEP 4 — LIQUIDITY SWEEP CHECK
Look for recent liquidity grabs on D1 or H4:
• Was there a recent sweep of swing highs (for shorts) or swing lows (for longs)?
• Has price taken out buy-side liquidity (BSL) before a potential short?
• Has price taken out sell-side liquidity (SSL) before a potential long?
• A confirmed sweep + reversal candle increases conviction significantly
• No sweep present = lower conviction, note this

STEP 5 — M15 STRUCTURE SHIFT
Read the M15 chart:
• Has there been a confirmed Break of Structure (BOS) or Change of Character (CHOCH) in the
  direction of the trade?
• Is there a clean M15 entry block (OB/FVG) to target for entry?
• If no M15 structure shift → NO TRADE (wait for it)

STEP 6 — ENTRY TRIGGER & EXECUTION PLAN
Combine all steps:
• Entry: At or inside the M15 Order Block / FVG (limit or market on confirmation)
• Stop Loss: Below/above the M15 OB wick + small buffer (typically 10–20 pips)
• Take Profit 1 (50% close): At the nearest H4 liquidity target or FVG fill
• Take Profit 2 (remaining): At the D1 liquidity target or major structure level
• R:R must be minimum 1:2 (ideally 1:3+); if it isn't → NO TRADE

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 MANDATORY OUTPUT FORMAT — use EXACTLY this block, no deviation
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

════════════════════════════════════════
 XAUUSD PRECISION ENGINE v8.0
 Scan Time: {UK time}  |  Bias: {BULLISH/BEARISH/NEUTRAL}
════════════════════════════════════════

📰 NEWS FILTER
{CLEAR or list of blocking events}

📊 D1 BIAS
{2–3 sentences on daily direction, key levels, premium/discount zone}

📈 H4 ZONE
{Valid zone identified? Price? Alignment with D1?}

💧 LIQUIDITY
{Was there a sweep? What was taken? Conviction level: HIGH / MEDIUM / LOW}

🔀 M15 STRUCTURE
{BOS or CHOCH present? Entry block location?}

─────────────────────────────────────────
🎯 VERDICT
─────────────────────────────────────────
Direction : {LONG / SHORT / NO TRADE}
Entry     : {price or zone}
Stop Loss : {price}
TP1 (50%) : {price}
TP2 (50%) : {price}
R:R       : {1:X.X}
Confidence: {HIGH / MEDIUM / LOW}

Reason: {1–2 sentence plain-English summary of why this trade or why no trade}
════════════════════════════════════════

RULES:
• Never recommend a trade when the news filter blocks it.
• Never recommend a trade unless ALL of Steps 2–6 pass.
• If any step fails, issue NO TRADE with the specific failing step as the reason.
• Do not add commentary outside the output block.
• Prices should be in the format XXXX.XX (e.g. 2345.50).
"""

# ── ForexFactory news check ──────────────────────────────────────────────────

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
UK_TZ = ZoneInfo("Europe/London")

HIGH_IMPACT_KEYWORDS = {
    "NFP", "Non-Farm", "CPI", "FOMC", "Federal Reserve", "Fed Chair",
    "PPI", "Retail Sales", "GDP", "PCE", "ISM", "ADP", "Unemployment Claims",
    "Gold", "XAU",
}


def _is_blocking_event(event: dict, now_uk: datetime) -> bool:
    """Return True if this event should block trading."""
    if event.get("impact", "").lower() != "high":
        return False
    currency = event.get("currency", "")
    if currency not in ("USD", "XAU"):
        return False
    title = event.get("title", "")
    if not any(kw.lower() in title.lower() for kw in HIGH_IMPACT_KEYWORDS):
        # Still block any high-impact USD event
        if currency != "USD":
            return False
    try:
        event_dt = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
        event_uk = event_dt.astimezone(UK_TZ)
        diff_hours = abs((event_uk - now_uk).total_seconds()) / 3600
        return diff_hours <= 2.0
    except (KeyError, ValueError):
        return False


def fetch_news_context(now_uk: datetime) -> tuple[bool, str]:
    """Return (is_blocked, news_summary_string)."""
    try:
        resp = requests.get(FF_URL, timeout=8)
        resp.raise_for_status()
        events = resp.json()
    except Exception as exc:
        return False, f"⚠ News feed unavailable ({exc}). Proceeding without news check."

    blocking = []
    upcoming_high = []

    for ev in events:
        if ev.get("currency") not in ("USD", "XAU"):
            continue
        if ev.get("impact", "").lower() != "high":
            continue
        try:
            event_dt = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
            event_uk = event_dt.astimezone(UK_TZ)
        except (KeyError, ValueError):
            continue

        diff_h = (event_uk - now_uk).total_seconds() / 3600
        time_str = event_uk.strftime("%H:%M UK")

        if _is_blocking_event(ev, now_uk):
            blocking.append(f"  ⛔ {ev.get('title', '?')} [{ev.get('currency')}] @ {time_str}")
        elif 0 <= diff_h <= 8:
            upcoming_high.append(f"  ⚠ {ev.get('title', '?')} [{ev.get('currency')}] @ {time_str}")

    lines = []
    if blocking:
        lines.append("BLOCKING EVENTS (within ±2h):")
        lines.extend(blocking)
    if upcoming_high:
        lines.append("Upcoming high-impact (today):")
        lines.extend(upcoming_high)
    if not lines:
        lines.append("No high-impact USD/XAU events within ±2h. CLEAR.")

    return bool(blocking), "\n".join(lines)


# ── Image helpers ────────────────────────────────────────────────────────────

def _encode_image(path: Path) -> tuple[str, str]:
    """Return (base64_data, media_type) for a chart image."""
    suffix = path.suffix.lower()
    media_types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                   ".gif": "image/gif", ".webp": "image/webp"}
    media_type = media_types.get(suffix, "image/png")
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return data, media_type


# ── Demo mode ────────────────────────────────────────────────────────────────

DEMO_VERDICT = """
════════════════════════════════════════
 XAUUSD PRECISION ENGINE v8.0
 Scan Time: 12:45 UK  |  Bias: BULLISH
════════════════════════════════════════

📰 NEWS FILTER
No high-impact USD/XAU events within ±2h. CLEAR.

📊 D1 BIAS
Price is trading above the 200 EMA with a series of higher highs and higher lows intact.
The most recent Daily candle closed as a bullish engulf above the prior swing high at 2318.
We are currently in a discount zone (below equilibrium of the last major D1 range), giving
longs a structural edge.

📈 H4 ZONE
Valid H4 Bullish Order Block identified at 2302–2308. Price has pulled back cleanly into
this zone without closing below. Aligned with D1 bullish bias. ✅

💧 LIQUIDITY
Sell-side liquidity (equal lows at 2304) was swept on the 08:00 H4 wick before a sharp
reversal close. Confirmed SSL sweep with rejection wick. Conviction: HIGH

🔀 M15 STRUCTURE
Clean CHOCH to the upside confirmed at 10:15 UK on M15. Bullish OB sits at 2305.50–2308.00.
Price is currently retesting this block. Entry condition met. ✅

─────────────────────────────────────────
🎯 VERDICT
─────────────────────────────────────────
Direction : LONG
Entry     : 2306.50 (inside M15 OB)
Stop Loss : 2300.80 (below OB wick + 5-pip buffer)
TP1 (50%) : 2324.00 (H4 FVG fill / BSL target)
TP2 (50%) : 2338.50 (D1 swing high liquidity)
R:R       : 1:3.1
Confidence: HIGH

Reason: D1 discount + H4 OB confluence with confirmed SSL sweep and M15 CHOCH.
All six filters passed. Clean institutional long setup.
════════════════════════════════════════
"""


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="XAUUSD Precision Engine v8.0")
    parser.add_argument("charts", nargs="*", metavar="CHART",
                        help="Three chart images: d1 h4 m15 (PNG/JPG)")
    parser.add_argument("--demo", action="store_true",
                        help="Demo mode — no real images or API key required")
    parser.add_argument("--stream", action="store_true",
                        help="Stream the response token by token")
    args = parser.parse_args()

    now_uk = datetime.now(UK_TZ)
    scan_time = now_uk.strftime("%Y-%m-%d %H:%M UK")

    print(f"\n  XAUUSD PRECISION ENGINE v8.0   [{scan_time}]")
    print("  " + "─" * 44)

    # ── Demo mode ──
    if args.demo:
        print("  [DEMO MODE — synthetic verdict]\n")
        print(DEMO_VERDICT)
        return

    # ── Validate inputs ──
    if len(args.charts) != 3:
        print("\n  Usage: python scan.py d1.png h4.png m15.png [--stream]\n")
        print("  Provide exactly 3 chart screenshots: D1, H4, M15.")
        print("  Use --demo to run without real images.\n")
        sys.exit(1)

    chart_paths = []
    labels = ["D1 (Daily)", "H4 (4-Hour)", "M15 (15-Min)"]
    for label, p in zip(labels, args.charts):
        path = Path(p)
        if not path.exists():
            print(f"\n  Error: {label} chart not found: {p}\n")
            sys.exit(1)
        chart_paths.append(path)
        print(f"  ✓ {label}: {path.name}")

    # ── News check ──
    print("\n  Checking ForexFactory news …", end="", flush=True)
    is_blocked, news_text = fetch_news_context(now_uk)
    print(" done")

    if is_blocked:
        print(f"\n  ⛔ TRADE BLOCKED BY NEWS FILTER\n\n{news_text}\n")
        print("════════════════════════════════════════")
        print(" XAUUSD PRECISION ENGINE v8.0")
        print(f" Scan Time: {scan_time}  |  Bias: N/A")
        print("════════════════════════════════════════")
        print("\n📰 NEWS FILTER")
        print(news_text)
        print("\n─────────────────────────────────────────")
        print("🎯 VERDICT")
        print("─────────────────────────────────────────")
        print("Direction : NO TRADE")
        print("Reason: High-impact news event within ±2 hours. Stand aside.")
        print("════════════════════════════════════════\n")
        return

    # ── Encode images ──
    print("  Encoding chart images …", end="", flush=True)
    image_blocks = []
    for label, path in zip(labels, chart_paths):
        b64, mt = _encode_image(path)
        image_blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": mt, "data": b64},
        })
        image_blocks.append({
            "type": "text",
            "text": f"↑ This is the {label} chart.",
        })
    print(" done")

    # ── Build user message ──
    user_content = [
        {
            "type": "text",
            "text": (
                f"Scan time: {scan_time}\n\n"
                f"LIVE NEWS CONTEXT:\n{news_text}\n\n"
                "I am now sending you the three charts. Run the Precision Engine v8.0 "
                "and deliver your verdict."
            ),
        }
    ] + image_blocks

    # ── Call Claude API ──
    try:
        import anthropic
    except ImportError:
        print("\n  Error: 'anthropic' package not installed.")
        print("  Run: pip install anthropic\n")
        sys.exit(1)

    client = anthropic.Anthropic()

    print("  Calling Claude API (claude-opus-4-8) …\n")
    print("─" * 48)

    try:
        if args.stream:
            with client.messages.stream(
                model="claude-opus-4-8",
                max_tokens=1500,
                thinking={"type": "adaptive"},
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            ) as stream:
                for text in stream.text_stream:
                    print(text, end="", flush=True)
            print("\n" + "─" * 48)
        else:
            response = client.messages.create(
                model="claude-opus-4-8",
                max_tokens=1500,
                thinking={"type": "adaptive"},
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
            # Print only text blocks (skip thinking blocks)
            for block in response.content:
                if block.type == "text":
                    print(block.text)
            print("─" * 48)

    except anthropic.APIError as exc:
        print(f"\n  API error: {exc}\n")
        sys.exit(1)

    print()


if __name__ == "__main__":
    main()
