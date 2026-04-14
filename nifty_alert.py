import requests
import time
import os
import sys
import numpy as np
import pandas as pd
from datetime import datetime, time as dtime, timedelta
import pytz

# Stdout flush karo — Railway logs ke liye
sys.stdout.flush()

TELEGRAM_TOKEN = "8754909402:AAGiudQUtZQeG_LjzF4LcFJ5ca9ScUD7ZN0"
CHAT_ID = "948684099"
UPSTOX_TOKEN = os.environ.get("UPSTOX_TOKEN")

IST = pytz.timezone("Asia/Kolkata")
MARKET_OPEN   = dtime(8, 45)
OBSERVE_START = dtime(9, 20)
TRADE_START   = dtime(9, 40)
MARKET_CLOSE  = dtime(15, 30)
EOD_EXIT      = dtime(15, 0)
TOLERANCE     = 0.002
HIST_TOLERANCE = 0.002

# MC+IB+ST ALGO SETTINGS
LOT_SIZE  = 65
ST_PERIOD = 10
ST_MULT   = 3
RR_RATIO  = 2
SL_BUFFER = 0.001
TRAIL_PTS = 10
DELTA_MIN = 0.20
DELTA_MAX = 0.25

def log(msg):
    print(msg, flush=True)

# =============================================
# TELEGRAM
# =============================================
def send_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
        log(f"✅ Alert sent!")
    except Exception as e:
        log(f"❌ Alert error: {e}")

# =============================================
# MARKET HOURS
# =============================================
def is_market_open():
    now = datetime.now(IST).time()
    if datetime.now(IST).weekday() >= 5:
        return False
    return MARKET_OPEN <= now <= MARKET_CLOSE

def can_observe():
    return datetime.now(IST).time() >= OBSERVE_START

def can_trade():
    return datetime.now(IST).time() >= TRADE_START

def is_eod():
    return datetime.now(IST).time() >= EOD_EXIT

# =============================================
# HEADERS
# =============================================
def get_headers():
    return {
        "Authorization": f"Bearer {UPSTOX_TOKEN}",
        "Accept": "application/json"
    }

# =============================================
# HISTORICAL LEVELS - 20 DIN
# =============================================
def get_historical_levels():
    try:
        end   = datetime.now(IST).strftime("%Y-%m-%d")
        start = (datetime.now(IST) - timedelta(days=28)).strftime("%Y-%m-%d")
        url   = f"https://api.upstox.com/v2/historical-candle/NSE_INDEX|Nifty%2050/day/{start}/{end}"
        r     = requests.get(url, headers=get_headers(), timeout=10)
        data  = r.json()
        if data.get('status') != 'success':
            return []
        levels = []
        for c in data['data']['candles']:
            levels.extend([round(float(c[1]),2), round(float(c[2]),2),
                           round(float(c[3]),2), round(float(c[4]),2)])
        return sorted(set(levels))
    except Exception as e:
        log(f"❌ Historical levels error: {e}")
        return []

# =============================================
# 5 MIN CANDLES
# =============================================
def get_candles():
    try:
        url  = "https://api.upstox.com/v3/historical-candle/intraday/NSE_INDEX|Nifty%2050/minutes/5"
        r    = requests.get(url, headers=get_headers(), timeout=10)
        data = r.json()
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
        result = result[::-1]
        result = [c for c in result if c['time'][11:16] >= '09:15']
        return result if len(result) >= 3 else None
    except Exception as e:
        log(f"❌ Candle fetch error: {e}")
        return None

# =============================================
# NIFTY REAL TIME LTP
# =============================================
def get_nifty_ltp():
    try:
        url  = "https://api.upstox.com/v2/market-quote/ltp?instrument_key=NSE_INDEX%7CNifty%2050"
        r    = requests.get(url, headers=get_headers(), timeout=10)
        data = r.json()
        if data.get('status') == 'success':
            return float(list(data['data'].values())[0]['last_price'])
        return None
    except:
        return None

# =============================================
# SUPERTREND
# =============================================
def calculate_supertrend(candles, period=10, multiplier=3):
    if len(candles) < period + 2:
        return None
    df = pd.DataFrame(candles)
    df.rename(columns={'high':'High','low':'Low','close':'Close','open':'Open'}, inplace=True)

    high  = df['High']
    low   = df['Low']
    close = df['Close']

    hl    = high - low
    hpc   = abs(high - close.shift(1))
    lpc   = abs(low  - close.shift(1))
    tr    = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    atr   = tr.ewm(span=period, adjust=False).mean()

    hl2        = (high + low) / 2
    upper_band = hl2 + multiplier * atr
    lower_band = hl2 - multiplier * atr

    supertrend = pd.Series(index=df.index, dtype=float)
    direction  = pd.Series(index=df.index, dtype=int)

    for i in range(period, len(df)):
        if i == period:
            supertrend.iloc[i] = upper_band.iloc[i]
            direction.iloc[i]  = -1
            continue

        prev_st  = supertrend.iloc[i-1]
        prev_dir = direction.iloc[i-1]
        curr_c   = close.iloc[i]

        if upper_band.iloc[i] < prev_st or close.iloc[i-1] > prev_st:
            curr_upper = upper_band.iloc[i]
        else:
            curr_upper = prev_st

        if lower_band.iloc[i] > prev_st or close.iloc[i-1] < prev_st:
            curr_lower = lower_band.iloc[i]
        else:
            curr_lower = prev_st

        if prev_dir == -1 and curr_c > curr_upper:
            direction.iloc[i]  = 1
            supertrend.iloc[i] = curr_lower
        elif prev_dir == 1 and curr_c < curr_lower:
            direction.iloc[i]  = -1
            supertrend.iloc[i] = curr_upper
        elif prev_dir == 1:
            direction.iloc[i]  = 1
            supertrend.iloc[i] = curr_lower
        else:
            direction.iloc[i]  = -1
            supertrend.iloc[i] = curr_upper

    df['ST']     = supertrend
    df['ST_Dir'] = direction
    return df

# =============================================
# OPTION CHAIN
# =============================================
def get_weekly_expiry():
    today      = datetime.now(IST)
    days_ahead = 3 - today.weekday()
    if days_ahead < 0:
        days_ahead += 7
    return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

def get_option_strike(option_type="PUT"):
    try:
        expiry = get_weekly_expiry()
        url    = f"https://api.upstox.com/v2/option/chain?instrument_key=NSE_INDEX|Nifty%2050&expiry_date={expiry}"
        r      = requests.get(url, headers=get_headers(), timeout=10)
        data   = r.json()
        if data.get('status') != 'success':
            return None, None, None, None

        best_strike = best_delta = best_premium = best_instrument = None

        for option in data['data']:
            opt_data = option.get('put_options' if option_type == "PUT" else 'call_options', {})
            if not opt_data:
                continue
            greeks     = opt_data.get('option_greeks', {})
            delta      = greeks.get('delta', 0)
            premium    = opt_data.get('market_data', {}).get('ltp', 0)
            strike     = option.get('strike_price', 0)
            instrument = opt_data.get('instrument_key', '')
            if DELTA_MIN <= abs(delta) <= DELTA_MAX:
                if best_delta is None or abs(abs(delta) - 0.225) < abs(abs(best_delta) - 0.225):
                    best_strike     = strike
                    best_delta      = delta
                    best_premium    = premium
                    best_instrument = instrument
        return best_strike, best_delta, best_premium, best_instrument
    except Exception as e:
        log(f"❌ Option chain error: {e}")
        return None, None, None, None

# =============================================
# PLACE ORDER
# =============================================
def place_order(instrument_key, transaction_type="BUY"):
    try:
        url     = "https://api.upstox.com/v2/order/place"
        payload = {
            "quantity"          : LOT_SIZE,
            "product"           : "D",
            "validity"          : "DAY",
            "price"             : 0,
            "tag"               : "NiftyAlgo",
            "instrument_token"  : instrument_key,
            "order_type"        : "MARKET",
            "transaction_type"  : transaction_type,
            "disclosed_quantity": 0,
            "trigger_price"     : 0,
            "is_amo"            : False
        }
        r    = requests.post(url, headers=get_headers(), json=payload, timeout=10)
        data = r.json()
        if data.get('status') == 'success':
            return data['data']['order_id']
        else:
            send_alert(f"❌ <b>Order fail!</b>\n{data}")
            return None
    except Exception as e:
        log(f"❌ Order error: {e}")
        return None

def get_current_premium(instrument_key):
    try:
        encoded = instrument_key.replace("|", "%7C")
        url     = f"https://api.upstox.com/v2/market-quote/ltp?instrument_key={encoded}"
        r       = requests.get(url, headers=get_headers(), timeout=10)
        data    = r.json()
        if data.get('status') == 'success':
            return float(list(data['data'].values())[0]['last_price'])
        return None
    except:
        return None

def near_historical(price, levels, tolerance=HIST_TOLERANCE):
    for level in levels:
        if abs(level - price) / price <= tolerance:
            return True, level
    return False, None

# =============================================
# MAIN - INFINITE LOOP
# =============================================
def main():
    log("🚀 Nifty 50 — ALERT + MC/IB/ST ALGO Started!")
    log("♾️  Infinite loop running...")
    send_alert("🚀 <b>Nifty Alert System Started!</b>\nMarket open hone ka wait kar raha hoon...")

    # Alert variables
    day_high         = 0
    day_low          = float('inf')
    came_down        = False
    all_levels       = []
    levels_alerted   = False
    alerted_levels   = set()
    alerted_day_high = False
    alerted_day_low  = False
    last_reset_date  = None

    # Algo variables
    in_trade         = False
    traded_today     = None
    algo_direction   = None
    entry_price      = None
    entry_premium    = None
    hard_sl          = None
    target_price     = None
    instrument_key   = None
    strike           = None
    entry_delta      = None
    trail_active     = False
    trail_sl         = None
    best_nifty       = None

    while True:
        try:
            now   = datetime.now(IST)
            today = now.date()

            # =============================================
            # DAILY RESET
            # =============================================
            if last_reset_date != today:
                day_high         = 0
                day_low          = float('inf')
                came_down        = False
                all_levels       = []
                levels_alerted   = False
                alerted_levels   = set()
                alerted_day_high = False
                alerted_day_low  = False
                last_reset_date  = today
                in_trade         = False
                traded_today     = None
                algo_direction   = None
                entry_price      = None
                entry_premium    = None
                hard_sl          = None
                target_price     = None
                instrument_key   = None
                strike           = None
                entry_delta      = None
                trail_active     = False
                trail_sl         = None
                best_nifty       = None
                log(f"🔄 Daily reset! Date: {today}")

            # =============================================
            # SLEEP LOGIC
            # =============================================
            if not is_market_open():
                log(f"[{now.strftime('%H:%M')}] Market closed. Sleep 30 min...")
                time.sleep(1800)
                continue

            # Market open — 10 sec polling
            sleep_time = 10

            # =============================================
            # HISTORICAL LEVELS
            # =============================================
            if len(all_levels) == 0:
                log("📈 20 din ke historical levels load ho rahe hain...")
                all_levels = get_historical_levels()
                if len(all_levels) == 0:
                    time.sleep(60)
                    continue
                log(f"✅ {len(all_levels)} levels loaded!")

                if not levels_alerted:
                    top_str    = "\n".join([f"  🔴 {l}" for l in reversed(all_levels[-10:])])
                    bottom_str = "\n".join([f"  🟢 {l}" for l in reversed(all_levels[:10])])
                    send_alert(
                        f"📊 <b>20 Din Ke Key Levels</b>\n\n"
                        f"🔴 <b>Resistance:</b>\n{top_str}\n\n"
                        f"🟢 <b>Support:</b>\n{bottom_str}\n\n"
                        f"📅 {today} | 🕐 {now.strftime('%H:%M')} IST"
                    )
                    levels_alerted = True

            # =============================================
            # CANDLES FETCH
            # =============================================
            candles = get_candles()
            if not candles or len(candles) < 3:
                log(f"[{now.strftime('%H:%M')}] No candle data!")
                time.sleep(sleep_time)
                continue

            last_closed = candles[-2]
            prev_closed = candles[-3]
            nifty_close = last_closed['close']

            # =============================================
            # DAY HIGH/LOW TRACK
            # =============================================
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

            # =============================================
            # ALERT SYSTEM
            # =============================================
            if can_trade():

                # Day High - Resistance Alert
                if day_high > 0:
                    near_high  = abs(nifty_close - day_high) / nifty_close <= TOLERANCE
                    below_high = nifty_close < day_high
                    if near_high and below_high and came_down and not alerted_day_high:
                        hist_match, hist_level = near_historical(day_high, all_levels)
                        setup_type = "🔥 STRONG" if hist_match else "⚡ Normal"
                        extra      = f"\n📊 Historical Match: {hist_level}" if hist_match else ""
                        prev_green = "\n✅ Pichli GREEN!" if prev_closed['close'] > prev_closed['open'] else "\n⚠️ Pichli GREEN nahi!"
                        curr_red   = "\n🔴 Current RED!" if last_closed['close'] < last_closed['open'] else "\n⚠️ Current RED nahi!"
                        send_alert(
                            f"🚨 <b>RESISTANCE ALERT! {setup_type}</b>\n\n"
                            f"📍 Day High: {day_high}\n"
                            f"💹 Price: {nifty_close}"
                            f"{extra}{prev_green}{curr_red}\n\n"
                            f"🕐 {now.strftime('%H:%M')} IST"
                        )
                        alerted_day_high = True

                # Day Low - Support Alert
                if day_low < float('inf'):
                    near_low  = abs(nifty_close - day_low) / nifty_close <= TOLERANCE
                    above_low = nifty_close > day_low
                    if near_low and above_low and not alerted_day_low:
                        hist_match, hist_level = near_historical(day_low, all_levels)
                        setup_type = "🔥 STRONG" if hist_match else "⚡ Normal"
                        extra      = f"\n📊 Historical Match: {hist_level}" if hist_match else ""
                        prev_green = "\n✅ Pichli GREEN!" if prev_closed['close'] > prev_closed['open'] else "\n⚠️ Pichli GREEN nahi!"
                        curr_red   = "\n🔴 Current RED!" if last_closed['close'] < last_closed['open'] else "\n⚪ Current GREEN!"
                        send_alert(
                            f"🟢 <b>SUPPORT ALERT! {setup_type}</b>\n\n"
                            f"📍 Day Low: {day_low}\n"
                            f"💹 Price: {nifty_close}"
                            f"{extra}{prev_green}{curr_red}\n\n"
                            f"🕐 {now.strftime('%H:%M')} IST"
                        )
                        alerted_day_low = True

                # Historical Levels Alert
                for level in all_levels:
                    if level in alerted_levels:
                        continue
                    if abs(nifty_close - level) / nifty_close <= TOLERANCE:
                        direction = "🔴 RESISTANCE" if level > nifty_close else "🟢 SUPPORT"
                        emoji     = "🔴" if level > nifty_close else "🟢"
                        day_match = ""
                        if day_high > 0 and abs(level - day_high) / day_high <= TOLERANCE:
                            day_match = "\n🔥 Day High match! STRONG!"
                        elif day_low < float('inf') and abs(level - day_low) / day_low <= TOLERANCE:
                            day_match = "\n🔥 Day Low match! STRONG!"
                        send_alert(
                            f"{emoji} <b>HISTORICAL LEVEL!</b>\n\n"
                            f"📍 Level: {level} ({direction})\n"
                            f"💹 Price: {nifty_close}"
                            f"{day_match}\n\n"
                            f"🕐 {now.strftime('%H:%M')} IST"
                        )
                        alerted_levels.add(level)

            # =============================================
            # MC + IB + ST ALGO - EOD EXIT
            # =============================================
            if in_trade and is_eod():
                curr_prem = get_current_premium(instrument_key) or entry_premium
                pnl = round((entry_premium - curr_prem) * LOT_SIZE, 2) if algo_direction == "PUT" else round((curr_prem - entry_premium) * LOT_SIZE, 2)
                place_order(instrument_key, "SELL")
                send_alert(
                    f"⏰ <b>EOD EXIT!</b>\n\n"
                    f"📊 {strike} {algo_direction}\n"
                    f"💰 Entry: ₹{entry_premium} | Exit: ₹{curr_prem}\n"
                    f"📈 P&L: ₹{pnl}\n"
                    f"🕐 {now.strftime('%H:%M')} IST"
                )
                in_trade     = False
                trail_active = False
                trail_sl     = None

            # =============================================
            # TRADE MONITOR - Real time trailing
            # =============================================
            elif in_trade and instrument_key:
                nifty_ltp = get_nifty_ltp()
                if nifty_ltp:
                    log(f"[{now.strftime('%H:%M:%S')}] IN TRADE | LTP: {nifty_ltp} | SL: {hard_sl} | Trail: {trail_sl} | Target: {target_price}")

                    if algo_direction == "CALL":
                        if nifty_ltp > best_nifty:
  
