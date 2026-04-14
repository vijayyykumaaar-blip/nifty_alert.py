import requests
import time
import os
import sys
import numpy as np
import pandas as pd
from datetime import datetime, time as dtime, timedelta
import pytz

sys.stdout.flush()

TELEGRAM_TOKEN = "8754909402:AAGiudQUtZQeG_LjzF4LcFJ5ca9ScUD7ZN0"
CHAT_ID        = "948684099"
UPSTOX_TOKEN   = os.environ.get("UPSTOX_TOKEN")

IST           = pytz.timezone("Asia/Kolkata")
MARKET_OPEN   = dtime(8, 45)
TRADE_START   = dtime(9, 40)
MARKET_CLOSE  = dtime(15, 30)
EOD_EXIT      = dtime(15, 0)

LOT_SIZE  = 65
ST_PERIOD = 10
ST_MULT   = 3
RR_RATIO  = 2
SL_BUFFER = 5
TRAIL_PTS = 10
DELTA_MIN = 0.20
DELTA_MAX = 0.25

def log(msg):
    print(msg, flush=True)

def send_alert(message):
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
        log("✅ Alert sent!")
    except Exception as e:
        log(f"❌ Alert error: {e}")

def is_market_open():
    now = datetime.now(IST).time()
    if datetime.now(IST).weekday() >= 5:
        return False
    return MARKET_OPEN <= now <= MARKET_CLOSE

def can_trade():
    return datetime.now(IST).time() >= TRADE_START

def is_eod():
    return datetime.now(IST).time() >= EOD_EXIT

def get_headers():
    return {
        "Authorization": f"Bearer {UPSTOX_TOKEN}",
        "Accept": "application/json"
    }

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
        return result if len(result) >= 4 else None
    except Exception as e:
        log(f"❌ Candle error: {e}")
        return None

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

def calculate_supertrend(candles, period=10, multiplier=3):
    if len(candles) < period + 2:
        return None
    df = pd.DataFrame(candles)
    df.rename(columns={'high':'High','low':'Low','close':'Close','open':'Open'}, inplace=True)

    high  = df['High']
    low   = df['Low']
    close = df['Close']

    hl  = high - low
    hpc = abs(high - close.shift(1))
    lpc = abs(low - close.shift(1))
    tr  = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()

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

        curr_upper = upper_band.iloc[i] if upper_band.iloc[i] < prev_st or close.iloc[i-1] > prev_st else prev_st
        curr_lower = lower_band.iloc[i] if lower_band.iloc[i] > prev_st or close.iloc[i-1] < prev_st else prev_st

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
            opt_data   = option.get('put_options' if option_type == "PUT" else 'call_options', {})
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

def main():
    log("🚀 MC+IB+ST ALGO Started!")
    send_alert("🚀 <b>MC+IB+ST Algo Started!</b>\nMarket open hone ka wait kar raha hoon...")

    in_trade       = False
    traded_today   = None
    algo_direction = None
    entry_price    = None
    entry_premium  = None
    hard_sl        = None
    target_price   = None
    instrument_key = None
    strike         = None
    entry_delta    = None
    trail_active   = False
    trail_sl       = None
    best_nifty     = None
    last_reset     = None

    while True:
        try:
            now        = datetime.now(IST)
            today      = now.date()
            sleep_time = 10  # DEFAULT sleep time - har loop mein reset

            # Daily reset
            if last_reset != today:
                in_trade       = False
                traded_today   = None
                algo_direction = None
                entry_price    = None
                entry_premium  = None
                hard_sl        = None
                target_price   = None
                instrument_key = None
                strike         = None
                entry_delta    = None
                trail_active   = False
                trail_sl       = None
                best_nifty     = None
                last_reset     = today
                log(f"🔄 Daily reset! {today}")

            # Market closed - 30 min sleep
            if not is_market_open():
                log(f"[{now.strftime('%H:%M')}] Market closed. Sleep 30 min...")
                time.sleep(1800)
                continue

            # Candles fetch
            candles = get_candles()
            if not candles or len(candles) < 4:
                log(f"[{now.strftime('%H:%M')}] No candle data!")
                time.sleep(sleep_time)
                continue

            last_closed  = candles[-2]
            candle_close = last_closed['close']

            # EOD EXIT
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
                time.sleep(sleep_time)
                continue

            # TRADE MONITOR
            if in_trade and instrument_key:
                nifty_ltp = get_nifty_ltp()
                if nifty_ltp:
                    log(f"[{now.strftime('%H:%M:%S')}] LTP:{nifty_ltp} | Close:{candle_close} | SL:{hard_sl} | Trail:{trail_sl} | Target:{target_price}")

                    if algo_direction == "CALL":
                        if nifty_ltp > best_nifty:
                            best_nifty = nifty_ltp
                            if trail_active:
                                trail_sl = round(best_nifty - TRAIL_PTS, 2)

                        if not trail_active and nifty_ltp > entry_price:
                            trail_active = True
                            trail_sl     = round(nifty_ltp - TRAIL_PTS, 2)
                            send_alert(f"🎯 <b>TRAIL ON!</b>\nTrail SL: {trail_sl}\n🕐 {now.strftime('%H:%M')} IST")

                        if candle_close <= hard_sl:
                            curr_prem = get_current_premium(instrument_key) or entry_premium
                            place_order(instrument_key, "SELL")
                            pnl = round((curr_prem - entry_premium) * LOT_SIZE, 2)
                            send_alert(
                                f"🛑 <b>SL HIT!</b>\n\n"
                                f"📊 {strike} CALL\n"
                                f"💹 Candle Close: {candle_close}\n"
                                f"🛑 SL: {hard_sl}\n"
                                f"💰 P&L: ₹{pnl}\n"
                                f"🕐 {now.strftime('%H:%M')} IST"
                            )
                            in_trade = False; trail_active = False; trail_sl = None

                        elif trail_active and trail_sl and nifty_ltp <= trail_sl:
                            curr_prem = get_current_premium(instrument_key) or entry_premium
                            place_order(instrument_key, "SELL")
                            pnl = round((curr_prem - entry_premium) * LOT_SIZE, 2)
                            send_alert(
                                f"✅ <b>TRAIL EXIT!</b>\n\n"
                                f"📊 {strike} CALL\n"
                                f"💹 Nifty: {nifty_ltp}\n"
                                f"🎯 Trail SL: {trail_sl}\n"
                                f"💰 P&L: ₹{pnl}\n"
                                f"🕐 {now.strftime('%H:%M')} IST"
                            )
                            in_trade = False; trail_active = False; trail_sl = None

                        elif nifty_ltp >= target_price:
                            curr_prem = get_current_premium(instrument_key) or entry_premium
                            place_order(instrument_key, "SELL")
                            pnl = round((curr_prem - entry_premium) * LOT_SIZE, 2)
                            send_alert(
                                f"🎯 <b>TARGET HIT!</b>\n\n"
                                f"📊 {strike} CALL\n"
                                f"💹 Nifty: {nifty_ltp}\n"
                                f"🎯 Target: {target_price}\n"
                                f"💰 P&L: ₹{pnl}\n"
                                f"🕐 {now.strftime('%H:%M')} IST"
                            )
                            in_trade = False; trail_active = False; trail_sl = None

                    else:  # PUT
                        if nifty_ltp < best_nifty:
                            best_nifty = nifty_ltp
                            if trail_active:
                                trail_sl = round(best_nifty + TRAIL_PTS, 2)

                        if not trail_active and nifty_ltp < entry_price:
                            trail_active = True
                            trail_sl     = round(nifty_ltp + TRAIL_PTS, 2)
                            send_alert(f"🎯 <b>TRAIL ON!</b>\nTrail SL: {trail_sl}\n🕐 {now.strftime('%H:%M')} IST")

                        if candle_close >= hard_sl:
                            curr_prem = get_current_premium(instrument_key) or entry_premium
                            place_order(instrument_key, "SELL")
                            pnl = round((entry_premium - curr_prem) * LOT_SIZE, 2)
                            send_alert(
                                f"🛑 <b>SL HIT!</b>\n\n"
                                f"📊 {strike} PUT\n"
                                f"💹 Candle Close: {candle_close}\n"
                                f"🛑 SL: {hard_sl}\n"
                                f"💰 P&L: ₹{pnl}\n"
                                f"🕐 {now.strftime('%H:%M')} IST"
                            )
                            in_trade = False; trail_active = False; trail_sl = None

                        elif trail_active and trail_sl and nifty_ltp >= trail_sl:
                            curr_prem = get_current_premium(instrument_key) or entry_premium
                            place_order(instrument_key, "SELL")
                            pnl = round((entry_premium - curr_prem) * LOT_SIZE, 2)
                            send_alert(
                                f"✅ <b>TRAIL EXIT!</b>\n\n"
                                f"📊 {strike} PUT\n"
                                f"💹 Nifty: {nifty_ltp}\n"
                                f"🎯 Trail SL: {trail_sl}\n"
                                f"💰 P&L: ₹{pnl}\n"
                                f"🕐 {now.strftime('%H:%M')} IST"
                            )
                            in_trade = False; trail_active = False; trail_sl = None

                        elif nifty_ltp <= target_price:
                            curr_prem = get_current_premium(instrument_key) or entry_premium
                            place_order(instrument_key, "SELL")
                            pnl = round((entry_premium - curr_prem) * LOT_SIZE, 2)
                            send_alert(
                                f"🎯 <b>TARGET HIT!</b>\n\n"
                                f"📊 {strike} PUT\n"
                                f"💹 Nifty: {nifty_ltp}\n"
                                f"🎯 Target: {target_price}\n"
                                f"💰 P&L: ₹{pnl}\n"
                                f"🕐 {now.strftime('%H:%M')} IST"
                            )
                            in_trade = False; trail_active = False; trail_sl = None

            # ENTRY CHECK
            elif not in_trade and traded_today != today and can_trade():
                df_st = calculate_supertrend(candles, ST_PERIOD, ST_MULT)
                if df_st is not None and len(df_st) >= 4:
                    mc = df_st.iloc[-4]
                    ic = df_st.iloc[-3]
                    bo = df_st.iloc[-2]
                    ec = df_st.iloc[-1]

                    mc_high = mc['High']
                    mc_low  = mc['Low']
                    mc_st   = mc['ST_Dir']

                    is_inside  = ic['High'] < mc_high and ic['Low'] > mc_low
                    buy_setup  = mc_st == 1
                    sell_setup = mc_st == -1

                    if is_inside and (buy_setup or sell_setup):
                        direction = "CALL" if buy_setup else "PUT"
                        bo_valid  = (direction == "CALL" and bo['Close'] > mc_high) or \
                                    (direction == "PUT"  and bo['Close'] < mc_low)

                        if bo_valid:
                            entry_nifty = ec['Open']

                            if direction == "CALL":
                                sl_calc = round(mc_low - SL_BUFFER, 2)
                                risk    = entry_nifty - sl_calc
                                tgt     = round(entry_nifty + risk * RR_RATIO, 2)
                            else:
                                sl_calc = round(mc_high + SL_BUFFER, 2)
                                risk    = sl_calc - entry_nifty
                                tgt     = round(entry_nifty - risk * RR_RATIO, 2)

                            if risk > 0:
                                opt_strike, opt_delta, opt_premium, opt_key = get_option_strike(direction)
                                if opt_strike and opt_key:
                                    order_id = place_order(opt_key, "BUY")
                                    if order_id:
                                        in_trade       = True
                                        traded_today   = today
                                        algo_direction = direction
                                        entry_price    = entry_nifty
                                        entry_premium  = opt_premium
                                        hard_sl        = sl_calc
                                        target_price   = tgt
                                        instrument_key = opt_key
                                        strike         = opt_strike
                                        entry_delta    = opt_delta
                                        best_nifty     = entry_nifty
                                        trail_active   = False
                                        trail_sl       = None

                                        send_alert(
                                            f"{'📈' if direction=='CALL' else '📉'} <b>MC+IB+ST ENTRY!</b>\n\n"
                                            f"📊 Strike: {strike} {direction}\n"
                                            f"💰 Premium: ₹{entry_premium}\n"
                                            f"📍 MC High: {mc_high} | MC Low: {mc_low}\n"
                                            f"🛑 SL: {hard_sl}\n"
                                            f"🎯 Target: {target_price}\n"
                                 
