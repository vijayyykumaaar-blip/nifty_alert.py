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
MARKET_OPEN = dtime(8, 45)
OBSERVE_START = dtime(9, 20)
TRADE_START = dtime(9, 40)
MARKET_CLOSE = dtime(15, 30)
LOT_SIZE = 65
TOLERANCE = 0.002
HIST_TOLERANCE = 0.002
SL_POINTS = 20
TRAIL_TRIGGER = 40
TRAIL_STEP = 10
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

def can_observe():
    return datetime.now(IST).time() >= OBSERVE_START

def can_trade():
    return datetime.now(IST).time() >= TRADE_START

def get_headers():
    return {
        "Authorization": f"Bearer {UPSTOX_TOKEN}",
        "Accept": "application/json"
    }

def get_historical_levels():
    try:
        end = datetime.now(IST).strftime("%Y-%m-%d")
        start = (datetime.now(IST) - timedelta(days=28)).strftime("%Y-%m-%d")
        url = f"https://api.upstox.com/v2/historical-candle/NSE_INDEX|Nifty%2050/day/{end}/{start}"
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
        # V3 API use kar rahe hain — 5 minute candle support karta hai
        url = "https://api.upstox.com/v3/historical-candle/intraday/NSE_INDEX|Nifty%2050/minutes/5"
        response = requests.get(url, headers=get_headers(), timeout=10)
        data = response.json()
        if data.get('status') != 'success':
            print(f"Candle API error: {data}")
            return None
        candles = data['data']['candles']
        if not candles or len(candles) < 1:
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
        # Candles latest pehle hain — reverse karo taaki oldest pehle ho
        result = result[::-1]
        # 9:15 pehla candle ignore karo
        result = [c for c in result if c['time'][11:16] >= '09:20']
        return result if len(result) >= 1 else None
    except Exception as e:
        print(f"❌ Candle fetch error: {e}")
        return None

def get_nifty_ltp():
    try:
        url = "https://api.upstox.com/v2/market-quote/ltp?instrument_key=NSE_INDEX%7CNifty%2050"
        response = requests.get(url, headers=get_headers(), timeout=10)
        data = response.json()
        if data.get('status') == 'success':
            ltp = list(data['data'].values())[0]['last_price']
            return float(ltp)
        return None
    except Exception as e:
        print(f"❌ Nifty LTP error: {e}")
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

def near_historical(price, levels, tolerance=HIST_TOLERANCE):
    for level in levels:
        if abs(level - price) / price <= tolerance:
            return True
    return False

def main():
    print("🚀 Nifty 50 ALGO TRADING System Started!")
    print(f"📊 LOT: {LOT_SIZE} | SL: {SL_POINTS}pts | Trail: {TRAIL_TRIGGER}pts trigger, {TRAIL_STEP}pts step")
    print(f"⏰ Observe: 9:20 AM | Trade: 9:40 AM")

    traded_today = None
    day_high = 0
    came_down = False
    all_levels = []
    in_trade = False
    entry_nifty_price = None
    entry_premium = None
    sl_level = None
    trail_trigger_level = None
    instrument_key = None
    strike = None
    entry_delta = None
    resistance = None
    hist_confirmed = False
    trailing_active = False
    lowest_nifty = None
    trail_stop = None

    while True:
        now = datetime.now(IST)
        today = now.date()

        if not is_market_open():
            print(f"[{now.strftime('%H:%M')}] Market closed.")
            if traded_today is not None and traded_today != today:
                traded_today = None
                day_high = 0
                came_down = False
                all_levels = []
                in_trade = False
                entry_nifty_price = None
                entry_premium = None
                sl_level = None
                trail_trigger_level = None
                instrument_key = None
                strike = None
                entry_delta = None
                resistance = None
                hist_confirmed = False
                trailing_active = False
                lowest_nifty = None
                trail_stop = None
            time.sleep(60)
            continue

        if len(all_levels) == 0:
            print("📈 20 din ke historical levels load ho rahe hain...")
            all_levels = get_historical_levels()
            if len(all_levels) == 0:
                send_alert("⚠️ Historical levels load nahi hue!")
                time.sleep(60)
                continue
            print(f"✅ {len(all_levels)} levels loaded!")

        candles = get_candles()
        if not candles or len(candles) < 1:
            print(f"[{now.strftime('%H:%M')}] No candle data!")
            time.sleep(60)
            continue

        last_closed = candles[-2] if len(candles) >= 2 else candles[-1]
        prev_closed = candles[-3] if len(candles) >= 3 else None
        nifty_close = last_closed['close']

        if can_observe():
            for c in candles:
                if c['high'] > day_high:
                    day_high = c['high']
                    came_down = False
            if day_high > 0 and nifty_close < day_high * 0.998:
                came_down = True

        if in_trade and instrument_key:
            nifty_ltp = get_nifty_ltp()
            current_premium = get_current_premium(instrument_key)
            print(f"[{now.strftime('%H:%M')}] IN TRADE | LTP: {nifty_ltp} | SL: {sl_level} | Trail: {trail_stop} | Active: {trailing_active}")

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
                trailing_active = False
                lowest_nifty = None
                trail_stop = None
                time.sleep(60)
                continue

            if nifty_ltp:
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
                    trailing_active = False
                    lowest_nifty = None
                    trail_stop = None

                elif trailing_active and trail_stop:
                    if nifty_ltp < lowest_nifty:
                        lowest_nifty = nifty_ltp
                        trail_stop = round(lowest_nifty + TRAIL_STEP, 2)
                        print(f"📉 New low! {lowest_nifty} | Trail: {trail_stop}")

                    if nifty_ltp >= trail_stop:
                        exit_order(instrument_key)
                        curr_prem = get_current_premium(instrument_key) or entry_premium
                        pnl = round((entry_premium - curr_prem) * LOT_SIZE, 2)
                        send_alert(
                            f"✅ <b>PROFIT EXIT (Trail)!</b>\n\n"
                            f"📊 Strike: {strike} PE\n"
                            f"📍 Resistance: {resistance}\n"
                            f"🎯 Trail Stop: {trail_stop}\n"
                            f"💰 Entry Premium: ₹{entry_premium}\n"
                            f"💰 Exit Premium: ₹{curr_prem}\n"
                            f"📈 Profit: ₹{pnl}\n"
                            f"🕐 Time: {now.strftime('%H:%M')} IST"
                        )
                        in_trade = False
                        trailing_active = False
                        lowest_nifty = None
                        trail_stop = None

                elif not trailing_active:
                    if nifty_ltp <= trail_trigger_level:
                        trailing_active = True
                        lowest_nifty = nifty_ltp
                        trail_stop = round(lowest_nifty + TRAIL_STEP, 2)
                        print(f"🎯 Trailing activated! LTP: {nifty_ltp} | Trail: {trail_stop}")
                        send_alert(
                            f"🎯 <b>TRAILING ACTIVATED!</b>\n\n"
                            f"📉 Nifty: {nifty_ltp}\n"
                            f"🔒 Trail Stop: {trail_stop}\n"
                            f"🕐 Time: {now.strftime('%H:%M')} IST"
                        )

        elif not in_trade and traded_today != today:
            if not can_trade():
                print(f"[{now.strftime('%H:%M')}] 9:40 AM ka wait...")
                time.sleep(60)
                continue

            if prev_closed is None or not (prev_closed['close'] > prev_closed['open']):
                print(f"[{now.strftime('%H:%M')}] Prev candle not green.")
                time.sleep(60)
                continue

            if not (last_closed['close'] < last_closed['open']):
                print(f"[{now.strftime('%H:%M')}] Candle not red.")
                time.sleep(60)
                continue

            if not came_down:
                print(f"[{now.strftime('%H:%M')}] came_down nahi hua.")
                time.sleep(60)
                continue

            if day_high == 0:
                print(f"[{now.strftime('%H:%M')}] Day high not set.")
                time.sleep(60)
                continue

            falling = last_closed['close'] < prev_closed['close']
            if not falling:
                print(f"[{now.strftime('%H:%M')}] Market upar ja rahi hai - skip!")
                time.sleep(60)
                continue

            near_high = abs(nifty_close - day_high) / nifty_close <= TOLERANCE
            below_high = nifty_close < day_high

            if not (near_high and below_high):
                print(f"[{now.strftime('%H:%M')}] Not near day high. Price: {nifty_close} | High: {day_high}")
                time.sleep(60)
                continue

            resistance = day_high
            hist_confirmed = near_historical(resistance, all_levels)
            setup_type = "🔥 STRONG (Day High + Historical)" if hist_confirmed else "⚡ Normal (Day High only)"
            print(f"✅ Setup found! {setup_type} | Resistance: {resistance}")

            sl_level = round(resistance + SL_POINTS, 2)
            entry_nifty_price = nifty_close
            trail_trigger_level = round(entry_nifty_price - TRAIL_TRIGGER, 2)

            strike, entry_delta, entry_premium, inst_key = get_put_strike()

            if not strike or not inst_key:
                send_alert("⚠️ <b>Setup mila par PUT strike nahi mila!</b>")
                time.sleep(60)
                continue

            order_id = place_order(inst_key)

            if order_id:
                in_trade = True
                traded_today = today
                instrument_key = inst_key
                trailing_active = False
                lowest_nifty = None
                trail_stop = None

                send_alert(
                    f"🔴 <b>PUT ENTRY!</b>\n\n"
                    f"📊 Strike: {strike} PE\n"
                    f"💰 Premium: ₹{entry_premium}\n"
                    f"📍 Resistance: {resistance}\n"
                    f"🎯 Trail Trigger: {trail_trigger_level}\n"
                    f"🛑 SL: {sl_level}\n"
                    f"📉 Delta: {entry_delta}\n"
                    f"📦 Qty: {LOT_SIZE} (1 lot)\n"
                    f"🏷️ Setup: {setup_type}\n"
                    f"🕐 Time: {now.strftime('%H:%M')} IST"
                )
        else:
            print(f"[{now.strftime('%H:%M')}] Already traded today.")

        time.sleep(60)

if __name__ == "__main__":
    main()
