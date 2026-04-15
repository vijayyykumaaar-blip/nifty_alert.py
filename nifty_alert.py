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

IST          = pytz.timezone("Asia/Kolkata")
MARKET_OPEN  = dtime(8, 45)
TRADE_START  = dtime(9, 40)
MARKET_CLOSE = dtime(15, 30)
EOD_EXIT     = dtime(15, 0)
PROFIT_TIME  = dtime(14, 15)  # 2:15 PM profit booking
NO_TRADE     = dtime(14, 30)  # 2:30 PM ke baad no new trade

LOT_SIZE       = 130   # 2 lots fixed
TRAIL_PTS      = 10
BUDGET         = 14000 # Max budget
CALL_DELTA_MIN = 0.18  # CALL delta positive
CALL_DELTA_MAX = 0.30
PUT_DELTA_MIN  = -0.30 # PUT delta negative
PUT_DELTA_MAX  = -0.18
NEAR_PTS       = 40    # 40 points near day high/low

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
    now = datetime.now(IST).time()
    return TRADE_START <= now < NO_TRADE

def is_eod():
    return datetime.now(IST).time() >= EOD_EXIT

def is_profit_time():
    return datetime.now(IST).time() >= PROFIT_TIME

def get_headers():
    return {
        "Authorization": f"Bearer {UPSTOX_TOKEN}",
        "Accept": "application/json"
    }

def get_candles_30min():
    try:
        url  = "https://api.upstox.com/v3/historical-candle/intraday/NSE_INDEX|Nifty%2050/minutes/30"
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
        return result if len(result) >= 2 else None
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
        max_premium = BUDGET / LOT_SIZE  # 14000 / 130 = ~107

        for option in data['data']:
            # Upstox API: call_options = positive delta, put_options = negative delta
            opt_data   = option.get('put_options' if option_type == "PUT" else 'call_options', {})
            if not opt_data:
                continue

            greeks     = opt_data.get('option_greeks', {})
            delta      = greeks.get('delta', 0)
            premium    = opt_data.get('market_data', {}).get('ltp', 0)
            strike     = option.get('strike_price', 0)
            instrument = opt_data.get('instrument_key', '')

            # Budget check - 2 lot affordable hona chahiye
            if premium * LOT_SIZE > BUDGET:
                continue

            if option_type == "CALL":
                # CALL: delta positive 0.18 to 0.30
                if CALL_DELTA_MIN <= delta <= CALL_DELTA_MAX:
                    if best_delta is None or abs(delta - 0.24) < abs(best_delta - 0.24):
                        best_strike     = strike
                        best_delta      = delta
                        best_premium    = premium
                        best_instrument = instrument
            else:
                # PUT: delta negative -0.30 to -0.18
                if PUT_DELTA_MIN <= delta <= PUT_DELTA_MAX:
                    if best_delta is None or abs(delta - (-0.24)) < abs(best_delta - (-0.24)):
                        best_strike     = strike
                        best_delta      = delta
                        best_premium    = premium
                        best_instrument = instrument

        if best_strike is None:
            log(f"⚠️ {option_type} strike nahi mila! Delta range ya budget check karo. Trade skip.")
            send_alert(f"⚠️ <b>{option_type} Strike nahi mila!</b>\nDelta 0.18-0.30 range ya budget ₹{BUDGET} check karo!")
            return None, None, None, None

        log(f"✅ {option_type} Strike: {best_strike} | Delta: {best_delta} | Premium: ₹{best_premium} | Cost: ₹{best_premium * LOT_SIZE}")
        return best_strike, best_delta, best_premium, best_instrument

    except Exception as e:
        log(f"❌ Option chain error: {e}")
        return None, None, None, None

def place_order(instrument_key, transaction_type="BUY"):
    try:
        url     = "https://api-hft.upstox.com/v3/order/place"
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
            "is_amo"            : False,
            "slice"             : True
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

def exit_trade(instrument_key, algo_direction, entry_premium, strike, reason, now):
    curr_prem = get_current_premium(instrument_key) or entry_premium
    place_order(instrument_key, "SELL")
    if algo_direction == "PUT":
        pnl = round((entry_premium - curr_prem) * LOT_SIZE, 2)
    else:
        pnl = round((curr_prem - entry_premium) * LOT_SIZE, 2)
    send_alert(
        f"{'✅' if pnl > 0 else '❌'} <b>{reason}</b>\n\n"
        f"📊 {strike} {algo_direction}\n"
        f"💰 Entry: ₹{entry_premium} | Exit: ₹{curr_prem}\n"
        f"📈 P&L: ₹{pnl}\n"
        f"🕐 {now.strftime('%H:%M')} IST"
    )
    return pnl

def main():
    log("🚀 MC Body Breakout + Flip ALGO Started!")
    send_alert("🚀 <b>MC Body Breakout Algo Started!</b>\nMarket open hone ka wait kar raha hoon...")

    # Daily variables
    last_reset     = None
    mother_candle  = None   # 9:15 AM candle
    body_top       = None
    body_bottom    = None
    direction_done = False  # Direction mil gayi?
    trade_type     = None   # CALL ya PUT
    breakout_level = None   # SL level
    flip_done      = False  # Flip already hua?
    day_high       = 0
    day_low        = float('inf')

    # Trade variables
    in_trade       = False
    algo_direction = None
    entry_price    = None
    entry_premium  = None
    hard_sl        = None
    instrument_key = None
    strike         = None
    entry_delta    = None
    trail_active   = False
    trail_sl       = None
    best_nifty     = None

    while True:
        try:
            now   = datetime.now(IST)
            today = now.date()

            # Daily reset
            if last_reset != today:
                mother_candle  = None
                body_top       = None
                body_bottom    = None
                direction_done = False
                trade_type     = None
                breakout_level = None
                flip_done      = False
                day_high       = 0
                day_low        = float('inf')
                in_trade       = False
                algo_direction = None
                entry_price    = None
                entry_premium  = None
                hard_sl        = None
                instrument_key = None
                strike         = None
                entry_delta    = None
                trail_active   = False
                trail_sl       = None
                best_nifty     = None
                last_reset     = today
                log(f"🔄 Daily reset! {today}")

            # Market closed - 15 min sleep
            if not is_market_open():
                # Agar trade open hai aur market band ho raha hai - EOD exit pehle!
                if in_trade and instrument_key:
                    log(f"[{now.strftime('%H:%M')}] Market band - EOD exit pehle!")
                    exit_trade(instrument_key, algo_direction, entry_premium, strike, "⏰ EOD EXIT (Market Close)", now)
                    in_trade     = False
                    trail_active = False
                    trail_sl     = None
                log(f"[{now.strftime('%H:%M')}] Market closed. Sleep 15 min...")
                time.sleep(900)
                continue

            sleep_time = 10

            # 30 min candles fetch
            candles = get_candles_30min()
            if not candles or len(candles) < 1:
                log(f"[{now.strftime('%H:%M')}] No candle data! Sleep 15 min...")
                time.sleep(900)
                continue

            # Day high/low update
            for c in candles:
                if c['high'] > day_high:
                    day_high = c['high']
                if c['low'] < day_low:
                    day_low = c['low']

            # Mother candle = 9:15 AM (pehli candle)
            if mother_candle is None and len(candles) >= 1:
                first = candles[0]
                if first['time'][11:16] == '09:15':
                    mother_candle = first
                    body_top      = max(first['open'], first['close'])
                    body_bottom   = min(first['open'], first['close'])
                    log(f"✅ Mother Candle set! Body Top:{body_top:.0f} Bottom:{body_bottom:.0f}")
                    send_alert(
                        f"📊 <b>Mother Candle Set!</b>\n\n"
                        f"🔴 Body Top: {body_top}\n"
                        f"🟢 Body Bottom: {body_bottom}\n"
                        f"🕐 {now.strftime('%H:%M')} IST"
                    )

            # Mother candle set nahi hua abhi
            if mother_candle is None:
                time.sleep(sleep_time)
                continue

            # Last closed candle
            last_closed  = candles[-2] if len(candles) >= 2 else candles[-1]
            candle_close = last_closed['close']
            candle_high  = last_closed['high']
            candle_low   = last_closed['low']
            candle_time  = last_closed['time'][11:16]

            # Nifty LTP
            nifty_ltp = get_nifty_ltp()

            # =============================================
            # EOD EXIT - 3 PM
            # =============================================
            if in_trade and is_eod():
                exit_trade(instrument_key, algo_direction, entry_premium, strike, "EOD EXIT", now)
                in_trade     = False
                trail_active = False
                trail_sl     = None
                time.sleep(sleep_time)
                continue

            # =============================================
            # TRADE MONITOR
            # =============================================
            if in_trade and instrument_key and nifty_ltp:
                log(f"[{now.strftime('%H:%M:%S')}] LTP:{nifty_ltp} | SL:{hard_sl} | Trail:{trail_sl}")

                # 2:15 PM KE BAAD PROFIT BOOKING - IST check
                now_ist_check = datetime.now(IST)
                if now_ist_check.time() >= dtime(14, 15):
                    if algo_direction == "PUT" and nifty_ltp <= day_low + NEAR_PTS:
                        exit_trade(instrument_key, algo_direction, entry_premium, strike, "💰 PROFIT BOOK (Near Day Low)", now_ist_check)
                        in_trade = False; trail_active = False; trail_sl = None
                        time.sleep(sleep_time)
                        continue

                    elif algo_direction == "CALL" and nifty_ltp >= day_high - NEAR_PTS:
                        exit_trade(instrument_key, algo_direction, entry_premium, strike, "💰 PROFIT BOOK (Near Day High)", now_ist_check)
                        in_trade = False; trail_active = False; trail_sl = None
                        time.sleep(sleep_time)
                        continue

                # Trail + Profit booking - SIRF 2:15 PM KE BAAD IST
                now_ist = datetime.now(IST)
                after_215 = now_ist.time() >= dtime(14, 15)

                if algo_direction == "CALL":
                    if after_215:
                        # Best price update
                        if nifty_ltp > best_nifty:
                            best_nifty = nifty_ltp
                            if trail_active:
                                trail_sl = round(best_nifty - TRAIL_PTS, 2)

                        # Trail activate
                        if not trail_active and nifty_ltp > entry_price:
                            trail_active = True
                            trail_sl     = round(nifty_ltp - TRAIL_PTS, 2)
                            send_alert(f"🎯 <b>TRAIL ON! (2:15+)</b>\nTrail SL: {trail_sl}\n🕐 {now_ist.strftime('%H:%M')} IST")

                        # Trail SL hit - real time
                        if trail_active and trail_sl and nifty_ltp <= trail_sl:
                            exit_trade(instrument_key, algo_direction, entry_premium, strike, "✅ TRAIL EXIT", now_ist)
                            in_trade = False; trail_active = False; trail_sl = None
                            time.sleep(sleep_time)
                            continue
                    else:
                        log(f"[{now_ist.strftime('%H:%M:%S')}] 2:15 se pehle - trail nahi karenge")

                else:  # PUT
                    if after_215:
                        # Best price update
                        if nifty_ltp < best_nifty:
                            best_nifty = nifty_ltp
                            if trail_active:
                                trail_sl = round(best_nifty + TRAIL_PTS, 2)

                        # Trail activate
                        if not trail_active and nifty_ltp < entry_price:
                            trail_active = True
                            trail_sl     = round(nifty_ltp + TRAIL_PTS, 2)
                            send_alert(f"🎯 <b>TRAIL ON! (2:15+)</b>\nTrail SL: {trail_sl}\n🕐 {now_ist.strftime('%H:%M')} IST")

                        # Trail SL hit - real time
                        if trail_active and trail_sl and nifty_ltp >= trail_sl:
                            exit_trade(instrument_key, algo_direction, entry_premium, strike, "✅ TRAIL EXIT", now_ist)
                            in_trade = False; trail_active = False; trail_sl = None
                            time.sleep(sleep_time)
                            continue
                    else:
                        log(f"[{now_ist.strftime('%H:%M:%S')}] 2:15 se pehle - trail nahi karenge")

                # SL check - CANDLE CLOSE pe
                if algo_direction == "CALL" and candle_close < hard_sl:
                    exit_trade(instrument_key, algo_direction, entry_premium, strike, "🛑 SL HIT (Candle Close)", now)
                    in_trade = False; trail_active = False; trail_sl = None

                    # FLIP to PUT - same SL level - agar flip nahi hua
                    if not flip_done and can_trade():
                        flip_done = True
                        log(f"🔄 Flip to PUT | SL same: {hard_sl:.0f}")
                        opt_strike, opt_delta, opt_premium, opt_key = get_option_strike("PUT")
                        if opt_strike and opt_key:
                            order_id = place_order(opt_key, "BUY")
                            if order_id:
                                in_trade       = True
                                algo_direction = "PUT"
                                entry_price    = nifty_ltp
                                entry_premium  = opt_premium
                                instrument_key = opt_key
                                strike         = opt_strike
                                entry_delta    = opt_delta
                                best_nifty     = nifty_ltp
                                trail_active   = False
                                trail_sl       = None
                                # hard_sl same rehta hai!
                                send_alert(
                                    f"🔄 <b>FLIP → PUT!</b>\n\n"
                                    f"📊 Strike: {strike} PUT\n"
                                    f"💰 Premium: ₹{entry_premium}\n"
                                    f"🛑 SL: {hard_sl} (Same level)\n"
                                    f"🕐 {now.strftime('%H:%M')} IST"
                                )

                elif algo_direction == "PUT" and candle_close > hard_sl:
                    exit_trade(instrument_key, algo_direction, entry_premium, strike, "🛑 SL HIT (Candle Close)", now)
                    in_trade = False; trail_active = False; trail_sl = None

                    # FLIP to CALL - same SL level - agar flip nahi hua
                    if not flip_done and can_trade():
                        flip_done = True
                        log(f"🔄 Flip to CALL | SL same: {hard_sl:.0f}")
                        opt_strike, opt_delta, opt_premium, opt_key = get_option_strike("CALL")
                        if opt_strike and opt_key:
                            order_id = place_order(opt_key, "BUY")
                            if order_id:
                                in_trade       = True
                                algo_direction = "CALL"
                                entry_price    = nifty_ltp
                                entry_premium  = opt_premium
                                instrument_key = opt_key
                                strike         = opt_strike
                                entry_delta    = opt_delta
                                best_nifty     = nifty_ltp
                                trail_active   = False
                                trail_sl       = None
                                # hard_sl same rehta hai!
                                send_alert(
                                    f"🔄 <b>FLIP → CALL!</b>\n\n"
                                    f"📊 Strike: {strike} CALL\n"
                                    f"💰 Premium: ₹{entry_premium}\n"
                                    f"🛑 SL: {hard_sl} (Same level)\n"
                                    f"🕐 {now.strftime('%H:%M')} IST"
                                )

            # =============================================
            # ENTRY CHECK - MC Body Breakout
            # =============================================
            elif not in_trade and not direction_done and can_trade():

                if body_top is None or body_bottom is None:
                    time.sleep(sleep_time)
                    continue

                # Direction candle check
                if candle_close > body_top:
                    # CALL setup
                    direction_done = True
                    trade_type     = "CALL"
                    breakout_level = candle_low   # CALL: Breakout candle ka Low = SL

                    log(f"🎯 CALL Direction! Close:{candle_close:.0f} > Body Top:{body_top:.0f} | SL:{breakout_level:.0f}")

                    # Agle candle ka open = entry (next 10 sec check mein milega)
                    opt_strike, opt_delta, opt_premium, opt_key = get_option_strike("CALL")
                    if opt_strike and opt_key:
                        # in_trade PEHLE True karo - duplicate order se bachao!
                        in_trade       = True
                        algo_direction = "CALL"
                        entry_price    = nifty_ltp
                        entry_premium  = opt_premium
                        hard_sl        = breakout_level
                        instrument_key = opt_key
                        strike         = opt_strike
                        entry_delta    = opt_delta
                        best_nifty     = nifty_ltp
                        trail_active   = False
                        trail_sl       = None
                        flip_done      = False
                        order_id = place_order(opt_key, "BUY")
                        if not order_id:
                            # Order fail hua - reset karo
                            in_trade = False
                            instrument_key = None

                            send_alert(
                                f"📈 <b>CALL ENTRY! (MC Body Breakout)</b>\n\n"
                                f"📊 Strike: {strike} CALL\n"
                                f"💰 Premium: ₹{entry_premium}\n"
                                f"📍 Body Top: {body_top} | Body Bottom: {body_bottom}\n"
                                f"🛑 SL: {hard_sl} (Candle close pe)\n"
                                f"🔄 Trail: {TRAIL_PTS} pts (Real time)\n"
                                f"📉 Delta: {entry_delta}\n"
                                f"📦 Qty: {LOT_SIZE}\n"
                                f"🕐 {now.strftime('%H:%M')} IST"
                            )

                elif candle_close < body_bottom:
                    # PUT setup
                    direction_done = True
                    trade_type     = "PUT"
                    breakout_level = candle_high  # PUT: Breakout candle ka High = SL

                    log(f"🎯 PUT Direction! Close:{candle_close:.0f} < Body Bottom:{body_bottom:.0f} | SL:{breakout_level:.0f}")

                    opt_strike, opt_delta, opt_premium, opt_key = get_option_strike("PUT")
                    if opt_strike and opt_key:
                        # in_trade PEHLE True karo - duplicate order se bachao!
                        in_trade       = True
                        algo_direction = "PUT"
                        entry_price    = nifty_ltp
                        entry_premium  = opt_premium
                        hard_sl        = breakout_level
                        instrument_key = opt_key
                        strike         = opt_strike
                        entry_delta    = opt_delta
                        best_nifty     = nifty_ltp
                        trail_active   = False
                        trail_sl       = None
                        flip_done      = False
                        order_id = place_order(opt_key, "BUY")
                        if not order_id:
                            # Order fail hua - reset karo
                            in_trade = False
                            instrument_key = None

                            send_alert(
                                f"📉 <b>PUT ENTRY! (MC Body Breakout)</b>\n\n"
                                f"📊 Strike: {strike} PUT\n"
                                f"💰 Premium: ₹{entry_premium}\n"
                                f"📍 Body Top: {body_top} | Body Bottom: {body_bottom}\n"
                                f"🛑 SL: {hard_sl} (Candle close pe)\n"
                                f"🔄 Trail: {TRAIL_PTS} pts (Real time)\n"
                                f"📉 Delta: {entry_delta}\n"
                                f"📦 Qty: {LOT_SIZE}\n"
                                f"🕐 {now.strftime('%H:%M')} IST"
                            )
                else:
                    log(f"[{now.strftime('%H:%M')}] Body ke andar - wait... Close:{candle_close:.0f}")

            log(f"[{now.strftime('%H:%M:%S')}] Trade:{in_trade} | Dir:{algo_direction} | Trail:{trail_active} | DayHigh:{day_high:.0f} | DayLow:{day_low:.0f}")

        except Exception as e:
            log(f"❌ Error: {e}")
            time.sleep(10)
            continue

        time.sleep(sleep_time)

if __name__ == "__main__":
    main()
