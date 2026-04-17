import requests
import time
import os
import sys
from datetime import datetime, time as dtime
import pytz

sys.stdout.flush()

# =============================================
# CONFIG
# =============================================
TELEGRAM_TOKEN = "YOUR_TOKEN"
CHAT_ID        = "YOUR_CHAT_ID"
UPSTOX_TOKEN   = os.environ.get("UPSTOX_TOKEN")

IST = pytz.timezone("Asia/Kolkata")

MARKET_OPEN  = dtime(9, 0)
MARKET_CLOSE = dtime(23, 30)

FUT_KEY = "MCX_FO|487466"

# =============================================
# GLOBALS
# =============================================
mother_candle = None
body_top = None
body_bottom = None

# =============================================
# HELPERS
# =============================================
def log(msg):
    print(msg, flush=True)

def send_alert(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        log(f"Alert error: {e}")

def get_headers():
    return {
        "Authorization": f"Bearer {UPSTOX_TOKEN}",
        "Accept": "application/json"
    }

def ist_now():
    return datetime.now(IST)

def is_market_open():
    now = ist_now()
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE

# =============================================
# CANDLES
# =============================================
def get_candles():
    try:
        encoded = FUT_KEY.replace("|", "%7C")
        url = f"https://api.upstox.com/v3/historical-candle/intraday/{encoded}/minutes/5"
        r = requests.get(url, headers=get_headers(), timeout=10)
        data = r.json()

        if data.get("status") != "success":
            return None

        raw = data["data"]["candles"]
        if not raw:
            return None

        result = []
        for c in raw:
            result.append({
                "time": c[0],
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4])
            })

        return result[::-1]

    except Exception as e:
        log(f"Candle error: {e}")
        return None

# =============================================
# MAIN
# =============================================
def main():
    global mother_candle, body_top, body_bottom

    log("🚀 Commodity Algo Started")

    while True:
        try:
            if not is_market_open():
                time.sleep(60)
                continue

            candles = get_candles()
            if not candles:
                time.sleep(5)
                continue

            # ============================
            # MOTHER CANDLE FIX
            # ============================
            if mother_candle is None:
                if len(candles) >= 1:
                    mother_candle = candles[0]

                    body_top = max(mother_candle["open"], mother_candle["close"])
                    body_bottom = min(mother_candle["open"], mother_candle["close"])

                    log(f"Mother Candle Set: {body_top} / {body_bottom}")

                    send_alert(
                        f"📊 Mother Candle\n"
                        f"Open: {mother_candle['open']}\n"
                        f"High: {mother_candle['high']}\n"
                        f"Low: {mother_candle['low']}\n"
                        f"Close: {mother_candle['close']}"
                    )

            # ============================
            # CLOSED CANDLE ONLY
            # ============================
            if len(candles) < 2:
                time.sleep(5)
                continue

            last_closed = candles[-2]

            log(
                f"CLOSED → O:{last_closed['open']} "
                f"H:{last_closed['high']} "
                f"L:{last_closed['low']} "
                f"C:{last_closed['close']}"
            )

        except Exception as e:
            log(f"ERROR: {e}")
            time.sleep(5)

        time.sleep(5)

# =============================================
# RUN
# =============================================
if __name__ == "__main__":
    main()
