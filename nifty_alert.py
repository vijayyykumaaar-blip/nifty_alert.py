import requests
import time
import os
import sys
from datetime import datetime, time as dtime
import pytz

sys.stdout.flush()

# CONFIG
TELEGRAM_TOKEN = "YOUR_TOKEN"
CHAT_ID        = "YOUR_CHAT_ID"
UPSTOX_TOKEN   = os.environ.get("UPSTOX_TOKEN")

IST = pytz.timezone("Asia/Kolkata")

MARKET_OPEN  = dtime(9, 0)
MARKET_CLOSE = dtime(23, 30)

FUT_KEY = "MCX_FO|487466"

# =============================================
# HELPERS
# =============================================
def log(msg):
    print(msg, flush=True)

def send_alert(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": msg}
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
    last_processed_time = None

    log("🚀 Stable Candle Debug Mode Started")

    while True:
        try:
            if not is_market_open():
                time.sleep(60)
                continue

            candles = get_candles()
            if not candles or len(candles) < 2:
                time.sleep(5)
                continue

            # CLOSED candle
            c = candles[-2]

            # 🔥 DUPLICATE BLOCK FIX
            if c["time"] == last_processed_time:
                time.sleep(5)
                continue

            last_processed_time = c["time"]

            log(
                f"NEW CLOSED → "
                f"O:{c['open']} "
                f"H:{c['high']} "
                f"L:{c['low']} "
                f"C:{c['close']}"
            )

            send_alert(
                f"📊 Candle Closed\n\n"
                f"Time: {c['time']}\n"
                f"Open: {c['open']}\n"
                f"High: {c['high']}\n"
                f"Low: {c['low']}\n"
                f"Close: {c['close']}"
            )

        except Exception as e:
            log(f"ERROR: {e}")
            time.sleep(5)

        time.sleep(10)

# =============================================
# RUN
# =============================================
if __name__ == "__main__":
    main()
