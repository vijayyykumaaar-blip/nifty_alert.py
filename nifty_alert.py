import requests
import time
import os
import sys
import gzip
import json
from datetime import datetime, time as dtime, timedelta
import pytz

sys.stdout.flush()

=============================================

CONFIG

=============================================

TELEGRAM_TOKEN = "8754909402:AAGiudQUtZQeG_LjzF4LcFJ5ca9ScUD7ZN0"
CHAT_ID        = "948684099"
UPSTOX_TOKEN   = os.environ.get("UPSTOX_TOKEN")

IST = pytz.timezone("Asia/Kolkata")

MCX NatGas Mini timings

MARKET_OPEN  = dtime(9, 0)
MARKET_CLOSE = dtime(23, 30)
EOD_EXIT     = dtime(23, 0)

Trade settings

LOT_SIZE = 250
FUT_KEY  = "MCX_FO|487466"

=============================================

GLOBALS

=============================================

mother_candle = None
body_top = None
body_bottom = None

=============================================

HELPERS

=============================================

def log(msg):
print(msg, flush=True)

def send_alert(msg):
url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
try:
requests.post(url, json=payload, timeout=10)
except Exception as e:
log(f"❌ Alert error: {e}")

def get_headers():
return {
"Authorization": f"Bearer {UPSTOX_TOKEN}",
"Accept"       : "application/json"
}

def ist_now():
return datetime.now(IST)

def ist_time():
return datetime.now(IST).time()

def is_market_open():
if ist_now().weekday() >= 5:
return False
return MARKET_OPEN <= ist_time() <= MARKET_CLOSE

=============================================

LTP

=============================================

def get_natgas_ltp():
try:
encoded = FUT_KEY.replace("|", "%7C")
url     = f"https://api.upstox.com/v2/market-quote/ltp?instrument_key={encoded}"
r       = requests.get(url, headers=get_headers(), timeout=10)
data    = r.json()
if data.get('status') == 'success':
return float(list(data['data'].values())[0]['last_price'])
return None
except:
return None

=============================================

5 MIN CANDLES

=============================================

def get_candles_5min():
try:
encoded = FUT_KEY.replace("|", "%7C")
url     = f"https://api.upstox.com/v3/historical-candle/intraday/{encoded}/minutes/5"
r       = requests.get(url, headers=get_headers(), timeout=10)
data    = r.json()
if data.get('status') != 'success':
return None
candles = data['data']['candles']
if not candles:
return None

    result = []
    for c in candles:
        result.append({
            'time' : c[0],
            'open' : float(c[1]),
            'high' : float(c[2]),
            'low'  : float(c[3]),
            'close': float(c[4])
        })

    return result[::-1]

except Exception as e:
    log(f"❌ Candle error: {e}")
    return None

=============================================

MAIN

=============================================

def main():
global mother_candle, body_top, body_bottom

log("🚀 Commodity Mother Candle Algo Started!")

while True:
    try:
        if not is_market_open():
            time.sleep(60)
            continue

        candles = get_candles_5min()
        if not candles:
            time.sleep(10)
            continue

        # ============================
        # MOTHER CANDLE FIX
        # ============================
        if len(candles) >= 1 and mother_candle is None:
            mother_candle = candles[0]

            body_top    = round(max(mother_candle['open'], mother_candle['close']), 2)
            body_bottom = round(min(mother_candle['open'], mother_candle['close']), 2)

            log(f"✅ Mother Candle FIXED! Top:{body_top} Bottom:{body_bottom}")

            send_alert(
                f"📊 <b>Mother Candle FIXED!</b>\n\n"
                f"🕯 Open: {mother_candle['open']}\n"
                f"🕯 High: {mother_candle['high']}\n"
                f"🕯 Low: {mother_candle['low']}\n"
                f"🕯 Close: {mother_candle['close']}\n\n"
                f"🔴 Body Top: {body_top}\n"
                f"🟢 Body Bottom: {body_bottom}"
            )

        # ============================
        # CLOSED CANDLE ONLY
        # ============================
        if len(candles) < 2:
            time.sleep(10)
            continue

        last_closed = candles[-2]

        log(f"📊 Closed Candle → O:{last_closed['open']} C:{last_closed['close']}")

    except Exception as e:
        log(f"❌ Error: {e}")
        time.sleep(10)

    time.sleep(10)

if name == "main":
main()
