import requests
import time
import os
from datetime import datetime, time as dtime, timedelta
import pytz

TELEGRAM_TOKEN = "8754909402:AAGiudQUtZQeG_LjzF4LcFJ5ca9ScUD7ZN0"
CHAT_ID = "948684099"
UPSTOX_TOKEN = os.environ.get("UPSTOX_TOKEN")
UPSTOX_API_KEY = os.environ.get("UPSTOX_API_KEY")

IST = pytz.timezone("Asia/Kolkata")
MARKET_OPEN = dtime(9, 15)
TRADE_START = dtime(9, 40)
MARKET_CLOSE = dtime(15, 0)
LOT_SIZE = 65
TOLERANCE = 0.002
SL_POINTS = 30
DELTA_MIN = 0.20
DELTA_MAX = 0.25

def send_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
        print(f"✅ Alert sent!")
    except Exception as e:
        print(f"❌ Alert error: {e}")

def is_market_open():
    now = datetime.now(IST).time()
    today = datetime.now(IST).weekday()
    if today >= 5:
        return False
    return MARKET_OPEN <= now <= MARKET_CLOSE

def can_trade():
    now = datetime.now(IST).time()
    return now >= TRADE_START

def get_headers():
    return {
        "Authorization": f"Bearer {UPSTOX_TOKEN}",
        "Accept": "application/json"
    }

def get_historical_levels():
    try:
        end = datetime.now(IST).strftime("%Y-%m-%d")
        start = (datetime.now(IST) - timedelta(days=90)).strftime("%Y-%m-%d")
        url = f"https://api.upstox.com/v2/historical-candle/NSE_INDEX|Nifty%2050/day/{start}/{end}"
        response = requests.get(url, headers=get_headers(), timeout=10)
        data = response.json()
        if data.get('status') != 'success':
            print(f"Historical data error: {data}")
            return []
        levels = []
        for c in data['data']['candles']:
            levels.extend([
                round(float(c[1]), 2),
                round(float(c[2]), 2),
                round(float(c[3]), 2),
                round(float(c[4]), 2)
            ])
        return sorted(set(levels))
    except Exception as e:
        print(f"❌ Historical levels error: {e}")
        return []

def get_candles():
    try:
        url = "https://api.upstox.com/v2/historical-candle/intraday/NSE_INDEX|Nifty%2050/5minute"
        response = requests.get(url, headers=get_headers(), timeout=10)
        data = response.json()
        if data.get('status') != 'success':
            return None
        candles = data['data']['candles']
        if len(candles) < 2:
            return None
        result = []
        for c in candles:
            result.append({
                'time': c[0],
                'open': float(c[1]),
                'high': float(c[2]),
                'low': float(c[3]),
                'close': float(c[4])
            })
        # Pehla candle (9:15-9:20) ignore karo
        if len(result) > 0:
            result = result[:-1]
        return result
    except Exception as e:
        print(f"❌ Candle fetch error: {e}")
        return None

def get_weekly_expiry():
    today = datetime.now(IST)
    days_ahead = 3 - today.weekday()
    if days_ahead < 0:
        days_ahead += 7
    expiry = today + timedelta(days=days_ahead)
    return expiry.strftime("%Y-%m-%d")

def get_put_strike():
    try:
        expiry = get_weekly_expiry()
        url = f"https://api.upstox.com/v2/option/chain?instrument_key=NSE_INDEX|Nifty%2050&expiry_date={expiry}"
        response = requests.get(url, headers=get_headers(), timeout=10)
        data = response.json()
        if data.get('status') != 'success':
            print(f"Option chain error: {data}")
            return None, None, None, None
        best_strike = None
        best_delta = None
        best_premium = None
        best_instrument = None
        for option in data['data']:
            put_data = option.get('put_options', {})
            if not put_data:
                continue
            greeks = put_data.get('option_greeks', {})
            delta = greeks.get('delta', 0)
            premium = put_data.get('market_data', {}).get('ltp', 0)
            strike = option.get('strike_price', 0)
            instrument = put_data.get('instrument_key', '')
            if DELTA_MIN <= abs(delta) <= DELTA_MAX:
                if best_delta is None or abs(abs(delta) - 0.225) < abs(abs(best_delta) - 0.225):
                    best_strike = strike
                    best_delta = delta
                    best_premium = premium
                    best_instrument = instrument
        return best_strike, best_delta, best_premium, best_instrument
    except Exception as e:
        print(f"❌ Option chain error: {e}")
        return None, None, None, None

def place_order(instrument_key):
    try:
        url = "https://api.upstox.com/v2/order/place"
        payload = {
            "quantity": LOT_SIZE,
            "product": "D",
            "validity": "DAY",
            "price": 0,
            "tag": "NiftyAlert",
            "instrument_token": instrument_key,
            "order_type": "MARKET",
            "transaction_type": "BUY",
            "disclosed_quantity": 0,
            "trigger_price": 0,
            "is_amo": False
        }
        response = requests.post(url, headers=get_headers(), json=payload, timeout=10)
        data = response.json()
        if data.get('status') == 'success':
            order_id = data['data']['order_id']
            print(f"✅ Order placed! ID: {order_id}")
            return order_id
        else:
            print(f"❌ Order failed: {data}")
            send_alert(f"❌ <b>Order place nahi hua!</b>\nError: {data}")
            return None
    except Exception as e:
        print(f"❌ Order error: {e}")
        return None

def exit_order(instrument_key):
    try:
        url = "https://api.upstox.com/v2/order/place"
        payload = {
            "quantity": LOT_SIZE,
            "product": "D",
            "validity": "DAY",
            "price": 0,
            "tag": "NiftyAlert",
            "instrument_token": instrument_key,
            "order_type": "MARKET",
            "transaction_type": "SELL",
            "disclosed_quantity": 0,
            "trigger_price": 0,
            "is_amo": False
        }
        response = requests.post(url, headers=get_headers(), json=payload, timeout=10)
        data = response.json()
        if data.get('status') == 'success':
            print(f"✅ Exit order placed!")
            return True
        else:
            print(f"❌ Exit failed: {data}")
            send_alert(f"❌ <b>Exit order fail!</b>\nError: {data}")
            return False
    except Exception as e:
        print(f"❌ Exit error: {e}")
        return False

def get_current_premium(instrument_key):
    try:
        encoded_key = instrument_key.replace("|", "%7C")
        url = f"https://api.upstox.com/v2/market-quote/ltp?instrument_key={encoded_key}"
        response = requests.get(url, headers=get_headers(), timeout=10)
        data = response.json()
        if data.get('status') == 'success':
            ltp = list(data['data'].values())[0]['last_price']
            return float(ltp)
        return None
    except Exception as e:
        print(f"❌ Premium fetch error: {e}")
        return None

def near_level(price, levels, tolerance=TOLERANCE):
    for level in levels:
        diff = abs(level - price) / price
        if diff <= tolerance:
            return True, level
    return False, None

def find_support(price, levels):
    support_levels = [l for l in levels if l < price]
    if support_levels:
        return support_levels[-1]
    return None

def main():
    print("🚀 Nifty 50 ALGO TRADING System Started!")
    print(f"📊 LOT: {LOT_SIZE} | TOLERANCE: {TOLERANCE*100}% | SL: {SL_POINTS} pts")
    print(f"⏰ Trade start time: 9:40 AM | First candle ignored")

    traded_today = None
    day_high = 0
    all_levels = []
    in_trade = False
    entry_nifty_price = None
    entry_premium = None
    sl_level = None
    target_level = None
    instrument_key = None
    strike = None
    entry_delta = None
    resistance = None

    while True:
        now = datetime.now(IST)
        today = now.date()

        if not is_market_open():
            print(f"[{now.strftime('%H:%M')}] Market closed.")
            if traded_today is not None and traded_today != today:
                traded_today = None
                day_high = 0
                all_levels = []
                in_trade = False
                entry_nifty_price = None
                entry_premium = None
                sl_level = None
                target_level = None
                instrument_key = None
                strike = None
                entry_delta = None
                resistance = None
            time.sleep(60)
            continue

        if len(all_levels) == 0:
            print("📈 3 mahine ke historical levels load ho rahe hain...")
            all_levels = get_historical_levels()
            if len(all_levels) == 0:
                send_alert("⚠️ Historical levels load nahi hue!")
                time.sleep(60)
                continue
            print(f"✅ {len(all_levels)} levels loaded!")

        candles = get_candles()
        if not candles or len(candles) < 2:
            print(f"[{now.strftime('%H:%M')}] No candle data!")
            time.sleep(60)
            continue

        last_closed = candles[-2]
        nifty_close = last_closed['close']

        for c in candles:
            if c['high'] > day_high:
                day_high = c['high']

        if in_trade and instrument_key:
            current_premium = get_current_premium(instrument_key)
            print(f"[{now.strftime('%H:%M')}] IN TRADE | Close: {nifty_close} | SL: {sl_level} | Target: {target_level}")

            if now.time() >= dtime(15, 0):
                exit_order(instrument_key)
                curr_prem = get_current_premium(instrument_key) or entry_premium
                pnl = round((entry_premium - curr_prem) * LOT_SIZE, 2)
                send_alert(
                    f"⏰ <b>EOD EXIT!</b>\n\n"
                    f"📊 Strike: {strike} PE\n"
                    f"📍 Resistance: {resistance}\n"
                    f"💰 Entry Premium: ₹{entry_premium}\n"
                    f"💰 Exit Premium: ₹{curr_prem}\n"
                    f"📈 P&L: ₹{pnl}\n"
                    f"🕐 Time: {now.strftime('%H:%M')} IST"
                )
                in_trade = False
                time.sleep(60)
                continue

            if nifty_close >= sl_level:
                exit_order(instrument_key)
                curr_prem = get_current_premium(instrument_key) or entry_premium
                pnl = round((entry_premium - curr_prem) * LOT_SIZE, 2)
                send_alert(
                    f"❌ <b>SL HIT!</b>\n\n"
                    f"📊 Strike: {strike} PE\n"
                    f"📍 Resistance: {resistance}\n"
                    f"🛑 SL: {sl_level}\n"
                    f"💰 Entry Premium: ₹{entry_premium}\n"
                    f"💰 Exit Premium: ₹{curr_prem}\n"
                    f"📉 Loss: ₹{abs(pnl)}\n"
                    f"🕐 Time: {now.strftime('%H:%M')} IST"
                )
                in_trade = False

            elif nifty_close <= target_level:
                exit_order(instrument_key)
                curr_prem = get_current_premium(instrument_key) or entry_premium
                pnl = round((entry_premium - curr_prem) * LOT_SIZE, 2)
                send_alert(
                    f"✅ <b>PROFIT EXIT!</b>\n\n"
                    f"📊 Strike: {strike} PE\n"
                    f"📍 Resistance: {resistance}\n"
                    f"🎯 Target: {target_level}\n"
                    f"💰 Entry Premium: ₹{entry_premium}\n"
                    f"💰 Exit Premium: ₹{curr_prem}\n"
                    f"📈 Profit: ₹{pnl}\n"
                    f"🕐 Time: {now.strftime('%H:%M')} IST"
                )
                in_trade = False

            else:
                print(f"[{now.strftime('%H:%M')}] Trade active. Waiting...")

        elif not in_trade and traded_today != today:

            if not can_trade():
                print(f"[{now.strftime('%H:%M')}] 9:40 AM ka wait kar rahe hain...")
                time.sleep(60)
                continue

            is_red = last_closed['close'] < last_closed['open']

            if not is_red:
                print(f"[{now.strftime('%H:%M')}] Candle green. No entry.")
                time.sleep(60)
                continue

            near_today = abs(nifty_close - day_high) / nifty_close <= TOLERANCE
            near_hist, hist_level = near_level(nifty_close, all_levels)

            if not (near_today or near_hist):
                print(f"[{now.strftime('%H:%M')}] No resistance. Price: {nifty_close} | High: {day_high}")
                time.sleep(60)
                continue

            if near_today and near_hist:
                resistance = min(day_high, hist_level)
            elif near_today:
                resistance = day_high
            else:
                resistance = hist_level

            support = find_support(resistance, all_levels)
            if support is None:
                print("No support found!")
                time.sleep(60)
                continue

            sl_level = round(resistance + SL_POINTS, 2)
            target_level = round(resistance - ((resistance - support) / 2), 2)

            strike, entry_delta, entry_premium, inst_key = get_put_strike()

            if not strike or not inst_key:
                send_alert("⚠️ <b>Setup mila par PUT strike nahi mila!</b>")
                time.sleep(60)
                continue

            order_id = place_order(inst_key)

            if order_id:
                in_trade = True
                traded_today = today
                entry_nifty_price = nifty_close
                instrument_key = inst_key

                send_alert(
                    f"🔴 <b>PUT ENTRY!</b>\n\n"
                    f"📊 Strike: {strike} PE\n"
                    f"💰 Premium: ₹{entry_premium}\n"
                    f"📍 Resistance: {resistance}\n"
                    f"🎯 Target: {target_level}\n"
                    f"🛑 SL: {sl_level}\n"
                    f"📉 Delta: {entry_delta}\n"
                    f"📦 Qty: {LOT_SIZE} (1 lot)\n"
                    f"🕐 Time: {now.strftime('%H:%M')} IST"
                )
        else:
            print(f"[{now.strftime('%H:%M')}] Already traded today.")

        time.sleep(60)

if __name__ == "__main__":
    main()
