import requests
import time
import os
import sys
import pandas as pd
from datetime import datetime, time as dtime, timedelta
import pytz

sys.stdout.flush()

# =============================================
# CONFIG
# =============================================
TELEGRAM_TOKEN = "8754909402:AAGiudQUtZQeG_LjzF4LcFJ5ca9ScUD7ZN0"
CHAT_ID        = "948684099"
UPSTOX_TOKEN   = os.environ.get("UPSTOX_TOKEN")

IST = pytz.timezone("Asia/Kolkata")

# Market timing
MARKET_OPEN  = dtime(8, 45)   # Script active
MARKET_CLOSE = dtime(15, 30)  # Script sleep
TRADE_START  = dtime(9, 45)   # 9:45 - pehli direction candle close hoti hai
NO_TRADE     = dtime(14, 30)  # 2:30 ke baad naya trade nahi
EOD_EXIT     = dtime(15, 0)   # 3:00 PM mandatory exit
TRAIL_TIME   = dtime(14, 15)  # 2:15 ke baad trail/profit

# Trade settings
LOT_SIZE       = 130    # 2 lots
TRAIL_PTS      = 10     # Trail step
BUDGET         = 14000  # Max budget
CALL_DELTA_MIN = 0.18   # CALL delta positive
CALL_DELTA_MAX = 0.30
PUT_DELTA_MIN  = -0.30  # PUT delta negative
PUT_DELTA_MAX  = -0.18
NEAR_PTS       = 40     # Profit booking buffer

# =============================================
# HELPERS
# =============================================
def log(msg):
    print(msg, flush=True)

def send_alert(msg):
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
        log("✅ Alert sent!")
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
    if ist_now().weekday() >= 5:  # Weekend
        return False
    return MARKET_OPEN <= ist_time() <= MARKET_CLOSE

def can_trade():
    t = ist_time()
    return TRADE_START <= t < NO_TRADE

def is_eod():
    return ist_time() >= EOD_EXIT

def is_trail_time():
    return ist_time() >= TRAIL_TIME

# =============================================
# DATA FETCH
# =============================================
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
        # Reverse - oldest pehle
        result = result[::-1]
        # 9:15 se filter
        result = [c for c in result if c['time'][11:16] >= '09:15']
        return result if len(result) >= 1 else None
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

# =============================================
# WEEKLY EXPIRY
# =============================================
def get_weekly_expiry():
    today      = ist_now()
    days_ahead = 3 - today.weekday()  # Thursday
    if days_ahead < 0:
        days_ahead += 7
    return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

# =============================================
# OPTION STRIKE - DELTA BASED
# =============================================
def get_option_strike(option_type="PUT"):
    try:
        expiry = get_weekly_expiry()
        url    = f"https://api.upstox.com/v2/option/chain?instrument_key=NSE_INDEX|Nifty%2050&expiry_date={expiry}"
        r      = requests.get(url, headers=get_headers(), timeout=10)
        data   = r.json()
        if data.get('status') != 'success':
            log(f"❌ Option chain error: {data}")
            return None, None, None, None

        best_strike = best_delta = best_premium = best_instrument = None

        for option in data['data']:
            # Upstox: call_options = positive delta, put_options = negative delta
            opt_data   = option.get('put_options' if option_type == "PUT" else 'call_options', {})
            if not opt_data:
                continue

            greeks     = opt_data.get('option_greeks', {})
            delta      = greeks.get('delta', 0)
            premium    = opt_data.get('market_data', {}).get('ltp', 0)
            strike     = option.get('strike_price', 0)
            instrument = opt_data.get('instrument_key', '')

            if not instrument or premium <= 0:
                continue

            # Budget check
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
            log(f"⚠️ {option_type} strike nahi mila!")
            send_alert(f"⚠️ <b>{option_type} Strike nahi mila!</b>\nDelta range ya budget check karo!")
            return None, None, None, None

        log(f"✅ {option_type} | Strike:{best_strike} | Delta:{best_delta:.2f} | Premium:₹{best_premium} | Cost:₹{round(best_premium*LOT_SIZE,0)}")
        return best_strike, best_delta, best_premium, best_instrument

    except Exception as e:
        log(f"❌ Option chain error: {e}")
        return None, None, None, None

# =============================================
# ORDER PLACEMENT - V3 API
# =============================================
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
            order_id = data['data']['order_id']
            log(f"✅ Order placed! ID: {order_id}")
            return order_id
        else:
            log(f"❌ Order failed: {data}")
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

def do_exit(instrument_key, direction, entry_prem, strike_val, reason):
    curr_prem = get_current_premium(instrument_key) or entry_prem
    place_order(instrument_key, "SELL")
    now = ist_now()
    if direction == "PUT":
        pnl = round((entry_prem - curr_prem) * LOT_SIZE, 2)
    else:
        pnl = round((curr_prem - entry_prem) * LOT_SIZE, 2)
    emoji = "✅" if pnl > 0 else "❌"
    send_alert(
        f"{emoji} <b>{reason}</b>\n\n"
        f"📊 {strike_val} {direction}\n"
        f"💰 Entry: ₹{entry_prem} | Exit: ₹{curr_prem}\n"
        f"📈 P&L: ₹{pnl}\n"
        f"🕐 {now.strftime('%H:%M')} IST"
    )
    return pnl

# =============================================
# MAIN
# =============================================
def main():
    log("🚀 MC Body Breakout + Flip ALGO Started!")
    send_alert(
        "🚀 <b>MC Body Breakout Algo Started!</b>\n"
        "⏰ Mother Candle: 9:15 AM\n"
        "📊 Direction: 9:45 AM onwards\n"
        "🛑 No new trade after: 2:30 PM\n"
        "💰 Trail/Profit: 2:15 PM onwards\n"
        "⏰ EOD Exit: 3:00 PM"
    )

    last_reset     = None

    # Daily state
    mother_candle  = None
    body_top       = None
    body_bottom    = None
    direction_done = False
    day_high       = 0
    day_low        = float('inf')

    # Trade state
    in_trade        = False
    algo_direction  = None
    entry_price     = None
    entry_premium   = None
    hard_sl         = None
    instrument_key  = None
    strike          = None
    entry_delta     = None
    trail_active    = False
    trail_sl        = None
    best_nifty      = None
    flip_done       = False
    trade_day_high  = 0              # Entry ke baad ka high (CALL ke liye)
    trade_day_low   = float('inf')   # Entry ke baad ka low (PUT ke liye)

    while True:
        try:
            now   = ist_now()
            today = now.date()

            # =============================================
            # DAILY RESET
            # =============================================
            if last_reset != today:
                mother_candle  = None
                body_top       = None
                body_bottom    = None
                direction_done = False
                day_high       = 0
                day_low        = float('inf')
                in_trade        = False
                algo_direction  = None
                entry_price     = None
                entry_premium   = None
                hard_sl         = None
                instrument_key  = None
                strike          = None
                entry_delta     = None
                trail_active    = False
                trail_sl        = None
                best_nifty      = None
                flip_done       = False
                trade_day_high  = 0
                trade_day_low   = float('inf')
                last_reset      = today
                log(f"🔄 Daily reset! {today}")

            # =============================================
            # MARKET CLOSED CHECK - IST
            # =============================================
            if not is_market_open():
                # Agar trade open hai to EOD exit karo pehle
                if in_trade and instrument_key:
                    log(f"[{now.strftime('%H:%M')}] Market band - Force EOD exit!")
                    do_exit(instrument_key, algo_direction, entry_premium, strike, "⏰ EOD EXIT (Market Close)")
                    in_trade     = False
                    trail_active = False
                    trail_sl     = None
                log(f"[{now.strftime('%H:%M')}] Market closed. Sleep 15 min...")
                time.sleep(900)
                continue

            # Market open - 10 sec polling
            sleep_time = 10

            # =============================================
            # CANDLES FETCH
            # =============================================
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

            # =============================================
            # MOTHER CANDLE SET - 9:15 AM
            # =============================================
            if mother_candle is None:
                first_candle = candles[0]
                if first_candle['time'][11:16] == '09:15':
                    mother_candle = first_candle
                    body_top      = round(max(first_candle['open'], first_candle['close']), 2)
                    body_bottom   = round(min(first_candle['open'], first_candle['close']), 2)
                    log(f"✅ Mother Candle! Body Top:{body_top} Bottom:{body_bottom}")
                    send_alert(
                        f"📊 <b>Mother Candle Set!</b>\n\n"
                        f"🔴 Body Top: {body_top}\n"
                        f"🟢 Body Bottom: {body_bottom}\n"
                        f"🕐 {now.strftime('%H:%M')} IST"
                    )

            # Mother candle abhi nahi bani
            if mother_candle is None or body_top is None:
                log(f"[{now.strftime('%H:%M')}] Mother candle ka wait...")
                time.sleep(sleep_time)
                continue

            # Last CLOSED candle
            last_closed  = candles[-2] if len(candles) >= 2 else candles[-1]
            candle_close = last_closed['close']
            candle_high  = last_closed['high']
            candle_low   = last_closed['low']

            # Nifty LTP
            nifty_ltp = get_nifty_ltp()

            # =============================================
            # EOD EXIT - 3:00 PM
            # =============================================
            if in_trade and is_eod():
                log(f"[{now.strftime('%H:%M')}] EOD exit!")
                do_exit(instrument_key, algo_direction, entry_premium, strike, "⏰ EOD EXIT (3 PM)")
                in_trade     = False
                trail_active = False
                trail_sl     = None
                time.sleep(sleep_time)
                continue

            # =============================================
            # TRADE MONITOR
            # =============================================
            if in_trade and instrument_key and nifty_ltp:
                # Trade ke baad ka high/low update karo
                if nifty_ltp > trade_day_high:
                    trade_day_high = nifty_ltp
                if nifty_ltp < trade_day_low:
                    trade_day_low = nifty_ltp

                log(f"[{now.strftime('%H:%M:%S')}] IN TRADE | LTP:{nifty_ltp} | SL:{hard_sl} | Trail:{trail_sl} | TradeHigh:{trade_day_high} | TradeLow:{trade_day_low}")

                # 2:15 PM ke baad profit booking + trail
                if is_trail_time():

                    if algo_direction == "PUT":
                        # PUT: Trade Low ke 40 pts paas - real time
                        if nifty_ltp <= trade_day_low + NEAR_PTS:
                            log(f"💰 PUT Profit book! LTP:{nifty_ltp} near Trade Low:{trade_day_low}")
                            do_exit(instrument_key, algo_direction, entry_premium, strike, "💰 PROFIT BOOK (Near Trade Low)")
                            in_trade = False; trail_active = False; trail_sl = None
                            time.sleep(sleep_time)
                            continue

                        # Trail update
                        if nifty_ltp < best_nifty:
                            best_nifty = nifty_ltp
                            if trail_active:
                                trail_sl = round(best_nifty + TRAIL_PTS, 2)

                        # Trail activate
                        if not trail_active and nifty_ltp < entry_price:
                            trail_active = True
                            trail_sl     = round(nifty_ltp + TRAIL_PTS, 2)
                            send_alert(f"🎯 <b>TRAIL ON!</b>\nTrail SL: {trail_sl}\n🕐 {now.strftime('%H:%M')} IST")

                        # Trail hit - real time
                        if trail_active and trail_sl and nifty_ltp >= trail_sl:
                            do_exit(instrument_key, algo_direction, entry_premium, strike, "✅ TRAIL EXIT")
                            in_trade = False; trail_active = False; trail_sl = None
                            time.sleep(sleep_time)
                            continue

                    else:  # CALL
                        # CALL: Trade High ke 40 pts paas - real time
                        if nifty_ltp >= trade_day_high - NEAR_PTS:
                            log(f"💰 CALL Profit book! LTP:{nifty_ltp} near Trade High:{trade_day_high}")
                            do_exit(instrument_key, algo_direction, entry_premium, strike, "💰 PROFIT BOOK (Near Trade High)")
                            in_trade = False; trail_active = False; trail_sl = None
                            time.sleep(sleep_time)
                            continue

                        # Trail update
                        if nifty_ltp > best_nifty:
                            best_nifty = nifty_ltp
                            if trail_active:
                                trail_sl = round(best_nifty - TRAIL_PTS, 2)

                        # Trail activate
                        if not trail_active and nifty_ltp > entry_price:
                            trail_active = True
                            trail_sl     = round(nifty_ltp - TRAIL_PTS, 2)
                            send_alert(f"🎯 <b>TRAIL ON!</b>\nTrail SL: {trail_sl}\n🕐 {now.strftime('%H:%M')} IST")

                        # Trail hit - real time
                        if trail_active and trail_sl and nifty_ltp <= trail_sl:
                            do_exit(instrument_key, algo_direction, entry_premium, strike, "✅ TRAIL EXIT")
                            in_trade = False; trail_active = False; trail_sl = None
                            time.sleep(sleep_time)
                            continue

                # SL check - CANDLE CLOSE pe (hamesha)
                if algo_direction == "CALL" and candle_close < hard_sl:
                    log(f"🛑 CALL SL hit! Close:{candle_close} < SL:{hard_sl}")
                    do_exit(instrument_key, algo_direction, entry_premium, strike, "🛑 SL HIT")
                    in_trade = False; trail_active = False; trail_sl = None

                    # FLIP to PUT - sirf ek baar, 2:30 se pehle
                    if not flip_done and can_trade() and nifty_ltp:
                        flip_done = True
                        opt_strike, opt_delta, opt_premium, opt_key = get_option_strike("PUT")
                        if opt_strike and opt_key:
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
                            # hard_sl SAME rehta hai!
                            order_id = place_order(opt_key, "BUY")
                            if not order_id:
                                in_trade = False; instrument_key = None
                            else:
                                send_alert(
                                    f"🔄 <b>FLIP → PUT!</b>\n\n"
                                    f"📊 Strike: {strike} PUT\n"
                                    f"💰 Premium: ₹{entry_premium}\n"
                                    f"🛑 SL: {hard_sl} (Same level)\n"
                                    f"🕐 {now.strftime('%H:%M')} IST"
                                )

                elif algo_direction == "PUT" and candle_close > hard_sl:
                    log(f"🛑 PUT SL hit! Close:{candle_close} > SL:{hard_sl}")
                    do_exit(instrument_key, algo_direction, entry_premium, strike, "🛑 SL HIT")
                    in_trade = False; trail_active = False; trail_sl = None

                    # FLIP to CALL - sirf ek baar, 2:30 se pehle
                    if not flip_done and can_trade() and nifty_ltp:
                        flip_done = True
                        opt_strike, opt_delta, opt_premium, opt_key = get_option_strike("CALL")
                        if opt_strike and opt_key:
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
                            # hard_sl SAME rehta hai!
                            order_id = place_order(opt_key, "BUY")
                            if not order_id:
                                in_trade = False; instrument_key = None
                            else:
                                send_alert(
                                    f"🔄 <b>FLIP → CALL!</b>\n\n"
                                    f"📊 Strike: {strike} CALL\n"
                                    f"💰 Premium: ₹{entry_premium}\n"
                                    f"🛑 SL: {hard_sl} (Same level)\n"
                                    f"🕐 {now.strftime('%H:%M')} IST"
                                )

            # =============================================
            # ENTRY CHECK - MC BODY BREAKOUT
            # =============================================
            elif not in_trade and not direction_done and can_trade():

                if candle_close > body_top:
                    # CALL breakout
                    direction_done = True
                    hard_sl        = candle_low   # SL = Breakout candle Low
                    log(f"🎯 CALL! Close:{candle_close} > Body Top:{body_top} | SL:{hard_sl}")

                    opt_strike, opt_delta, opt_premium, opt_key = get_option_strike("CALL")
                    if opt_strike and opt_key:
                        in_trade       = True  # PEHLE set karo - duplicate se bachao
                        algo_direction = "CALL"
                        entry_price    = nifty_ltp
                        entry_premium  = opt_premium
                        instrument_key = opt_key
                        strike         = opt_strike
                        entry_delta    = opt_delta
                        best_nifty     = nifty_ltp
                        trail_active   = False
                        trail_sl       = None
                        flip_done      = False

                        order_id = place_order(opt_key, "BUY")
                        if not order_id:
                            in_trade = False; instrument_key = None
                        else:
                            send_alert(
                                f"📈 <b>CALL ENTRY!</b>\n\n"
                                f"📊 Strike: {strike} CALL\n"
                                f"💰 Premium: ₹{entry_premium}\n"
                                f"📍 Body Top: {body_top}\n"
                                f"🛑 SL: {hard_sl} (Candle close)\n"
                                f"📉 Delta: {entry_delta:.2f}\n"
                                f"📦 Qty: {LOT_SIZE} | Cost: ₹{round(entry_premium*LOT_SIZE,0)}\n"
                                f"🕐 {now.strftime('%H:%M')} IST"
                            )

                elif candle_close < body_bottom:
                    # PUT breakout
                    direction_done = True
                    hard_sl        = candle_high  # SL = Breakout candle High
                    log(f"🎯 PUT! Close:{candle_close} < Body Bottom:{body_bottom} | SL:{hard_sl}")

                    opt_strike, opt_delta, opt_premium, opt_key = get_option_strike("PUT")
                    if opt_strike and opt_key:
                        in_trade       = True  # PEHLE set karo - duplicate se bachao
                        algo_direction = "PUT"
                        entry_price    = nifty_ltp
                        entry_premium  = opt_premium
                        instrument_key = opt_key
                        strike         = opt_strike
                        entry_delta    = opt_delta
                        best_nifty     = nifty_ltp
                        trail_active   = False
                        trail_sl       = None
                        flip_done      = False

                        order_id = place_order(opt_key, "BUY")
                        if not order_id:
                            in_trade = False; instrument_key = None
                        else:
                            send_alert(
                                f"📉 <b>PUT ENTRY!</b>\n\n"
                                f"📊 Strike: {strike} PUT\n"
                                f"💰 Premium: ₹{entry_premium}\n"
                                f"📍 Body Bottom: {body_bottom}\n"
                                f"🛑 SL: {hard_sl} (Candle close)\n"
                                f"📉 Delta: {entry_delta:.2f}\n"
                                f"📦 Qty: {LOT_SIZE} | Cost: ₹{round(entry_premium*LOT_SIZE,0)}\n"
                                f"🕐 {now.strftime('%H:%M')} IST"
                            )
                else:
                    log(f"[{now.strftime('%H:%M')}] Body ke andar - wait | Close:{candle_close} | Top:{body_top} | Bottom:{body_bottom}")

            log(f"[{now.strftime('%H:%M:%S')}] Trade:{in_trade} | Dir:{algo_direction} | Trail:{trail_active} | High:{day_high} | Low:{day_low}")

        except Exception as e:
            log(f"❌ Main loop error: {e}")
            time.sleep(10)
            continue

        time.sleep(sleep_time)

if __name__ == "__main__":
    main()
