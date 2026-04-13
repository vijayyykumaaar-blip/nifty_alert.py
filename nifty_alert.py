import requests
import time
import os
from datetime import datetime, time as dtime, timedelta
import pytz

TELEGRAM_TOKEN = "8754909402:AAGiudQUtZQeG_LjzF4LcFJ5ca9ScUD7ZN0"
CHAT_ID = "948684099"
UPSTOX_TOKEN = os.environ.get("UPSTOX_TOKEN")

IST = pytz.timezone("Asia/Kolkata")
MARKET_OPEN = dtime(8, 45)
OBSERVE_START = dtime(9, 20)
TRADE_START = dtime(9, 40)
MARKET_CLOSE = dtime(15, 30)
TOLERANCE = 0.002
HIST_TOLERANCE = 0.002

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
        candles = data['data']['candles']
        for c in candles:
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
        url = "https://api.upstox.com/v3/historical-candle/intraday/NSE_INDEX|Nifty%2050/minutes/5"
        response = requests.get(url, headers=get_headers(), timeout=10)
        data = response.json()
        if data.get('status') != 'success':
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
        result = result[::-1]
        result = [c for c in result if c['time'][11:16] >= '09:20']
        return result if len(result) >= 1 else None
    except Exception as e:
        print(f"❌ Candle fetch error: {e}")
        return None

def near_historical(price, levels, tolerance=HIST_TOLERANCE):
    for level in levels:
        if abs(level - price) / price <= tolerance:
            return True, level
    return False, None

def main():
    print("🚀 Nifty 50 ALERT System Started!")
    print(f"⏰ Observe: 9:20 AM | Alert: 9:40 AM onwards")

    day_high = 0
    day_low = float('inf')
    came_down = False
    all_levels = []
    levels_alerted = False
    alerted_levels = set()
    alerted_day_high = False
    alerted_day_low = False
    last_reset_date = None

    while True:
        now = datetime.now(IST)
        today = now.date()

        if last_reset_date != today:
            day_high = 0
            day_low = float('inf')
            came_down = False
            all_levels = []
            levels_alerted = False
            alerted_levels = set()
            alerted_day_high = False
            alerted_day_low = False
            last_reset_date = today
            print(f"🔄 Daily reset done!")

        if not is_market_open():
            print(f"[{now.strftime('%H:%M')}] Market closed.")
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

            if not levels_alerted:
                top_levels = all_levels[-10:]
                bottom_levels = all_levels[:10]
                top_str = "\n".join([f"  🔴 {l}" for l in reversed(top_levels)])
                bottom_str = "\n".join([f"  🟢 {l}" for l in reversed(bottom_levels)])
                send_alert(
                    f"📊 <b>20 Din Ke Key Levels</b>\n\n"
                    f"🔴 <b>Top Resistance Levels:</b>\n{top_str}\n\n"
                    f"🟢 <b>Top Support Levels:</b>\n{bottom_str}\n\n"
                    f"📅 Date: {today}\n"
                    f"🕐 Time: {now.strftime('%H:%M')} IST"
                )
                levels_alerted = True

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
                    alerted_day_high = False
                if c['low'] < day_low:
                    day_low = c['low']
                    alerted_day_low = False
            if day_high > 0 and nifty_close < day_high * 0.998:
                came_down = True

        if can_trade():

            # DAY HIGH - RESISTANCE ALERT
            if day_high > 0:
                near_high = abs(nifty_close - day_high) / nifty_close <= TOLERANCE
                below_high = nifty_close < day_high

                if near_high and below_high and came_down and not alerted_day_high:
                    hist_match, hist_level = near_historical(day_high, all_levels)
                    setup_type = "🔥 STRONG" if hist_match else "⚡ Normal"
                    extra = f"\n📊 Historical Match: {hist_level}" if hist_match else ""

                    prev_green = ""
                    if prev_closed:
                        if prev_closed['close'] > prev_closed['open']:
                            prev_green = "\n✅ Pichli candle GREEN hai!"
                        else:
                            prev_green = "\n⚠️ Pichli candle GREEN nahi!"

                    curr_red = "\n🔴 Current candle RED hai!" if last_closed['close'] < last_closed['open'] else "\n⚠️ Current candle RED nahi!"

                    send_alert(
                        f"🚨 <b>RESISTANCE ALERT! {setup_type}</b>\n\n"
                        f"📍 Day High: {day_high}\n"
                        f"💹 Current Price: {nifty_close}\n"
                        f"📉 came_down: {'✅ Haan' if came_down else '❌ Nahi'}"
                        f"{extra}"
                        f"{prev_green}"
                        f"{curr_red}\n\n"
                        f"🕐 Time: {now.strftime('%H:%M')} IST"
                    )
                    alerted_day_high = True
                    print(f"✅ Day High Alert sent! {setup_type}")

            # DAY LOW - SUPPORT ALERT
            if day_low < float('inf'):
                near_low = abs(nifty_close - day_low) / nifty_close <= TOLERANCE
                above_low = nifty_close > day_low

                if near_low and above_low and not alerted_day_low:
                    hist_match, hist_level = near_historical(day_low, all_levels)
                    setup_type = "🔥 STRONG" if hist_match else "⚡ Normal"
                    extra = f"\n📊 Historical Match: {hist_level}" if hist_match else ""

                    prev_green = ""
                    if prev_closed:
                        if prev_closed['close'] > prev_closed['open']:
                            prev_green = "\n✅ Pichli candle GREEN hai!"
                        else:
                            prev_green = "\n⚠️ Pichli candle GREEN nahi!"

                    curr_red = "\n🔴 Current candle RED hai!" if last_closed['close'] < last_closed['open'] else "\n⚠️ Current candle RED nahi (CALL le sakte ho!)"

                    send_alert(
                        f"🟢 <b>SUPPORT ALERT! {setup_type}</b>\n\n"
                        f"📍 Day Low: {day_low}\n"
                        f"💹 Current Price: {nifty_close}"
                        f"{extra}"
                        f"{prev_green}"
                        f"{curr_red}\n\n"
                        f"🕐 Time: {now.strftime('%H:%M')} IST"
                    )
                    alerted_day_low = True
                    print(f"✅ Day Low Alert sent! {setup_type}")

            # HISTORICAL LEVELS ALERT
            for level in all_levels:
                if level in alerted_levels:
                    continue

                near = abs(nifty_close - level) / nifty_close <= TOLERANCE

                if near:
                    direction = "🔴 RESISTANCE" if level > nifty_close else "🟢 SUPPORT"
                    emoji = "🔴" if level > nifty_close else "🟢"

                    day_match = ""
                    if day_high > 0 and abs(level - day_high) / day_high <= TOLERANCE:
                        day_match = "\n🔥 Day High se bhi match! STRONG!"
                    elif day_low < float('inf') and abs(level - day_low) / day_low <= TOLERANCE:
                        day_match = "\n🔥 Day Low se bhi match! STRONG!"

                    prev_green = ""
                    if prev_closed:
                        if prev_closed['close'] > prev_closed['open']:
                            prev_green = "\n✅ Pichli candle GREEN hai!"
                        else:
                            prev_green = "\n⚠️ Pichli candle GREEN nahi!"

                    curr_red = "\n🔴 Current candle RED hai!" if last_closed['close'] < last_closed['open'] else "\n⚪ Current candle GREEN hai!"

                    send_alert(
                        f"{emoji} <b>HISTORICAL LEVEL ALERT!</b>\n\n"
                        f"📍 Level: {level} ({direction})\n"
                        f"💹 Current Price: {nifty_close}"
                        f"{day_match}"
                        f"{prev_green}"
                        f"{curr_red}\n\n"
                        f"🕐 Time: {now.strftime('%H:%M')} IST"
                    )
                    alerted_levels.add(level)
                    print(f"✅ Historical Level Alert: {level}")

        print(f"[{now.strftime('%H:%M')}] Price: {nifty_close} | High: {day_high} | Low: {day_low} | came_down: {came_down}")
        time.sleep(60)

if __name__ == "__main__":
    main()
