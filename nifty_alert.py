import requests
import time
import os
import sys
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
NO_TRADE     = dtime(14, 30)  # 2:30 ke baad naya trade nahi
EOD_EXIT     = dtime(15, 0)   # 3:00 PM mandatory exit
TRAIL_TIME   = dtime(14, 15)  # 2:15 ke baad trail/profit

# Trade settings
LOT_SIZE       = 65     # 1 lot
TRAIL_PTS      = 10     # Trail step
BUDGET         = 14000  # Max budget
CALL_DELTA_MIN = 0.10
CALL_DELTA_MAX = 0.50
PUT_DELTA_MIN  = -0.50
PUT_DELTA_MAX  = -0.10
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
    if ist_now().weekday() >= 5:
        return False
    return MARKET_OPEN <= ist_time() <= MARKET_CLOSE

def can_trade():
    t = ist_time()
    return t < NO_TRADE

def is_eod():
    return ist_time() >= EOD_EXIT

def is_trail_time():
    return ist_time() >= TRAIL_TIME

# =============================================
# 30 MIN CANDLES FETCH
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
    days_ahead = 3 - today.weekday()
    if days_ahead < 0:
        days_ahead += 7
    expiry = today + timedelta(days=days_ahead)
    # Agar aaj expiry hai to agle week ki expiry lo
    if expiry.date() == today.date():
        expiry = expiry + timedelta(days=7)
    return expiry.strftime("%Y-%m-%d")

# =============================================
# OPTION STRIKE
# =============================================
def get_option_strike(option_type="PUT"):
    try:
        # Pehle Nifty spot price lo
        spot = get_nifty_ltp()
        if not spot:
            log("❌ Nifty LTP nahi mila!")
            return None, None, None, None

        expiry = get_weekly_expiry()
        url    = f"https://api.upstox.com/v2/option/chain?instrument_key=NSE_INDEX|Nifty%2050&expiry_date={expiry}"
        r      = requests.get(url, headers=get_headers(), timeout=10)
        data   = r.json()
        if data.get('status') != 'success':
            log(f"❌ Option chain API error: {data}")
            return None, None, None, None

        best_strike = best_delta = best_premium = best_instrument = None
        best_diff   = float('inf')

        log(f"🔍 {option_type} scan | Spot:{spot:.0f} | Expiry:{expiry}")

        for option in data['data']:
            opt_data = option.get('put_options' if option_type == "PUT" else 'call_options', {})
            if not opt_data:
                continue

            greeks     = opt_data.get('option_greeks', {})
            delta      = greeks.get('delta', 0)
            premium    = opt_data.get('market_data', {}).get('ltp', 0)
            strike     = option.get('strike_price', 0)
            instrument = opt_data.get('instrument_key', '')

            if not instrument or premium <= 0:
                continue

            # Spot se distance check
            if option_type == "CALL":
                # CALL: Spot se 200-800 upar
                strike_ok = (spot + 200) <= strike <= (spot + 800)
                delta_ok  = CALL_DELTA_MIN <= delta <= CALL_DELTA_MAX
            else:
                # PUT: Spot se 200-800 neeche
                strike_ok = (spot - 800) <= strike <= (spot - 200)
                delta_ok  = PUT_DELTA_MIN <= delta <= PUT_DELTA_MAX

            in_range = delta_ok or strike_ok  # Dono mein se koi bhi

            log(f"   Strike:{strike} | Delta:{delta:.3f} | Premium:₹{premium} | StrikeOK:{strike_ok} | DeltaOK:{delta_ok}")

            if in_range:
                # Delta milta hai to delta se select karo
                # Nahi milta to spot distance se
                if delta_ok:
                    target = 0.24 if option_type == "CALL" else -0.24
                    diff = abs(delta - target)
                else:
                    # Spot ke sabse paas wala OTM strike
                    diff = abs(strike - spot)

                if diff < best_diff:
                    best_diff       = diff
                    best_strike     = strike
                    best_delta      = delta
                    best_premium    = premium
                    best_instrument = instrument

        if best_strike is None:
            log(f"⚠️ {option_type} delta range mein koi strike nahi mila!")
            log(f"⚠️ Spot:{spot:.0f} | Range: {'0.18-0.30' if option_type=='CALL' else '-0.30 to -0.18'}")
            send_alert(
                f"⚠️ <b>{option_type} Strike nahi mila!</b>\n"
                f"Spot: {spot:.0f}\n"
                f"Delta range: {'0.18-0.30' if option_type=='CALL' else '-0.30 to -0.18'}\n"
                f"Expiry: {expiry}"
            )
            return None, None, None, None

        log(f"✅ {option_type} | Strike:{best_strike} | Delta:{best_delta:.3f} | Premium:₹{best_premium}")
        return best_strike, best_delta, best_premium, best_instrument

    except Exception as e:
        log(f"❌ Option chain error: {e}")
        return None, None, None, None

# =============================================
# ORDER
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
            log(f"✅ Order placed! ID: {data['data']['order_id']}")
            return data['data']['order_id']
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
    pnl = round((entry_prem - curr_prem) * LOT_SIZE, 2) if direction == "PUT" else round((curr_prem - entry_prem) * LOT_SIZE, 2)
    emoji = "✅" if pnl > 0 else "❌"
    send_alert(
        f"{emoji} <b>{reason}</b>\n\n"
        f"📊 {strike_val} {direction}\n"
        f"💰 Entry: ₹{entry_prem} | Exit: ₹{curr_prem}\n"
        f"📈 P&L: ₹{pnl}\n"
        f"🕐 {now.strftime('%H:%M')} IST"
    )
    return pnl

def enter_trade(direction, opt_key, opt_strike, opt_delta, opt_premium, sl, nifty_ltp, flip=False):
    """Trade entry - returns trade state dict or None"""
    order_id = place_order(opt_key, "BUY")
    if not order_id:
        return None
    now = ist_now()
    trade = {
        'direction'     : direction,
        'entry_price'   : nifty_ltp,
        'entry_premium' : opt_premium,
        'hard_sl'       : sl,
        'instrument_key': opt_key,
        'strike'        : opt_strike,
        'entry_delta'   : opt_delta,
        'best_nifty'    : nifty_ltp,
        'trail_active'  : False,
        'trail_sl'      : None,
        'trade_high'    : nifty_ltp if direction == "CALL" else 0,
        'trade_low'     : nifty_ltp if direction == "PUT" else float('inf'),
    }
    flip_tag = "🔄 FLIP → " if flip else ""
    emoji    = "📈" if direction == "CALL" else "📉"
    send_alert(
        f"{emoji} <b>{flip_tag}{direction} ENTRY!</b>\n\n"
        f"📊 Strike: {opt_strike} {direction}\n"
        f"💰 Premium: ₹{opt_premium}\n"
        f"🛑 SL: {sl} (Candle close pe)\n"
        f"📉 Delta: {opt_delta:.2f}\n"
        f"📦 Qty: {LOT_SIZE} | Cost: ₹{round(opt_premium*LOT_SIZE,0)}\n"
        f"🕐 {now.strftime('%H:%M')} IST"
    )
    return trade

# =============================================
# MAIN
# =============================================
def main():
    log("🚀 MC Body Breakout + Flip ALGO Started!")
    send_alert(
        "🚀 <b>MC Body Breakout Algo Started!</b>\n\n"
        "📊 Strategy: 30 Min Candle\n"
        "⏰ Mother Candle: 9:15-9:45 AM\n"
        "📊 Direction: 9:45 AM ke baad\n"
        "🛑 No new trade: 2:30 PM ke baad\n"
        "💰 Trail/Profit: 2:15 PM ke baad\n"
        "⏰ EOD Exit: 3:00 PM"
    )

    last_reset     = None
    mother_candle  = None
    body_top       = None
    body_bottom    = None
    direction_done = False
    flip_done      = False
    prev_candle_count = 0  # Naya candle detect karne ke liye

    # Trade state
    trade = None  # None = no trade, dict = active trade

    while True:
        try:
            now   = ist_now()
            today = now.date()

            # =============================================
            # DAILY RESET
            # =============================================
            if last_reset != today:
                mother_candle     = None
                body_top          = None
                body_bottom       = None
                flip_done         = False
                prev_candle_count = 0
                trade             = None
                last_reset        = today
                log(f"🔄 Daily reset! {today}")

            # =============================================
            # MARKET CLOSED
            # =============================================
            if not is_market_open():
                if trade is not None:
                    log(f"[{now.strftime('%H:%M')}] Market band - Force EOD exit!")
                    do_exit(trade['instrument_key'], trade['direction'],
                            trade['entry_premium'], trade['strike'], "⏰ EOD EXIT")
                    trade = None
                log(f"[{now.strftime('%H:%M')}] Market closed. Sleep 15 min...")
                time.sleep(900)
                continue

            sleep_time = 10

            # =============================================
            # 30 MIN CANDLES FETCH
            # =============================================
            candles = get_candles_30min()
            if not candles:
                log(f"[{now.strftime('%H:%M')}] No candle data! Sleep 15 min...")
                time.sleep(900)
                continue

            # Naya 30 min candle close hua?
            new_candle = len(candles) > prev_candle_count
            if new_candle:
                prev_candle_count = len(candles)
                log(f"✅ Naya 30 min candle close hua! Total: {len(candles)}")

            # Last CLOSED candle
            last_closed  = candles[-1]
            candle_time  = last_closed['time'][11:16]
            candle_close = last_closed['close']
            candle_high  = last_closed['high']
            candle_low   = last_closed['low']

            nifty_ltp = get_nifty_ltp()

            # =============================================
            # MOTHER CANDLE SET - 9:45 pe close hoti hai
            # =============================================
            if mother_candle is None and candle_time >= '09:45':
                mother_candle = last_closed
                body_top      = round(max(last_closed['open'], last_closed['close']), 2)
                body_bottom   = round(min(last_closed['open'], last_closed['close']), 2)
                log(f"✅ Mother Candle Set! Body Top:{body_top} Bottom:{body_bottom}")
                send_alert(
                    f"📊 <b>Mother Candle Set!</b>\n\n"
                    f"🔴 Body Top: {body_top}\n"
                    f"🟢 Body Bottom: {body_bottom}\n"
                    f"🕐 {now.strftime('%H:%M')} IST"
                )
                time.sleep(sleep_time)
                continue

            if mother_candle is None:
                log(f"[{now.strftime('%H:%M')}] Mother candle ka wait... (9:45 pe set hogi)")
                time.sleep(sleep_time)
                continue

            # =============================================
            # EOD EXIT - 3:00 PM
            # =============================================
            if trade is not None and is_eod():
                log(f"[{now.strftime('%H:%M')}] EOD exit!")
                do_exit(trade['instrument_key'], trade['direction'],
                        trade['entry_premium'], trade['strike'], "⏰ EOD EXIT (3 PM)")
                trade = None
                time.sleep(sleep_time)
                continue

            # =============================================
            # TRADE MONITOR - Real time
            # =============================================
            if trade is not None and nifty_ltp:
                direction = trade['direction']

                # Trade high/low update - real time
                if nifty_ltp > trade['trade_high']:
                    trade['trade_high'] = nifty_ltp
                if nifty_ltp < trade['trade_low']:
                    trade['trade_low'] = nifty_ltp

                log(f"[{now.strftime('%H:%M:%S')}] {direction} | LTP:{nifty_ltp} | SL:{trade['hard_sl']} | Trail:{trade['trail_sl']} | H:{trade['trade_high']:.0f} | L:{trade['trade_low']:.0f}")

                # 2:15 PM ke baad PROFIT BOOKING + TRAIL
                if is_trail_time():
                    if direction == "PUT":
                        # PUT: Trade Low ke 40 pts paas → Profit book
                        if nifty_ltp <= trade['trade_low'] + NEAR_PTS:
                            do_exit(trade['instrument_key'], direction, trade['entry_premium'],
                                    trade['strike'], "💰 PROFIT BOOK (Near Trade Low)")
                            trade = None
                            time.sleep(sleep_time)
                            continue

                        # Trail activate
                        if not trade['trail_active'] and nifty_ltp < trade['entry_price']:
                            trade['trail_active'] = True
                            trade['trail_sl']     = round(nifty_ltp + TRAIL_PTS, 2)
                            trade['best_nifty']   = nifty_ltp
                            send_alert(f"🎯 <b>TRAIL ON!</b>\nTrail SL: {trade['trail_sl']}\n🕐 {now.strftime('%H:%M')} IST")

                        # Trail update
                        if trade['trail_active'] and nifty_ltp < trade['best_nifty']:
                            trade['best_nifty'] = nifty_ltp
                            trade['trail_sl']   = round(nifty_ltp + TRAIL_PTS, 2)

                        # Trail hit - real time
                        if trade['trail_active'] and trade['trail_sl'] and nifty_ltp >= trade['trail_sl']:
                            do_exit(trade['instrument_key'], direction, trade['entry_premium'],
                                    trade['strike'], "✅ TRAIL EXIT")
                            trade = None
                            time.sleep(sleep_time)
                            continue

                    else:  # CALL
                        # CALL: Trade High ke 40 pts paas → Profit book
                        if nifty_ltp >= trade['trade_high'] - NEAR_PTS:
                            do_exit(trade['instrument_key'], direction, trade['entry_premium'],
                                    trade['strike'], "💰 PROFIT BOOK (Near Trade High)")
                            trade = None
                            time.sleep(sleep_time)
                            continue

                        # Trail activate
                        if not trade['trail_active'] and nifty_ltp > trade['entry_price']:
                            trade['trail_active'] = True
                            trade['trail_sl']     = round(nifty_ltp - TRAIL_PTS, 2)
                            trade['best_nifty']   = nifty_ltp
                            send_alert(f"🎯 <b>TRAIL ON!</b>\nTrail SL: {trade['trail_sl']}\n🕐 {now.strftime('%H:%M')} IST")

                        # Trail update
                        if trade['trail_active'] and nifty_ltp > trade['best_nifty']:
                            trade['best_nifty'] = nifty_ltp
                            trade['trail_sl']   = round(nifty_ltp - TRAIL_PTS, 2)

                        # Trail hit - real time
                        if trade['trail_active'] and trade['trail_sl'] and nifty_ltp <= trade['trail_sl']:
                            do_exit(trade['instrument_key'], direction, trade['entry_premium'],
                                    trade['strike'], "✅ TRAIL EXIT")
                            trade = None
                            time.sleep(sleep_time)
                            continue

                # SL CHECK - naya 30 min CANDLE CLOSE pe
                if new_candle:
                    if direction == "CALL" and candle_close < trade['hard_sl']:
                        log(f"🛑 CALL SL! Close:{candle_close} < SL:{trade['hard_sl']}")
                        do_exit(trade['instrument_key'], direction, trade['entry_premium'],
                                trade['strike'], "🛑 SL HIT")
                        old_sl = trade['hard_sl']
                        trade  = None

                        # FLIP to PUT - sirf ek baar, 2:30 se pehle
                        if not flip_done and can_trade() and nifty_ltp:
                            flip_done = True
                            opt_strike, opt_delta, opt_premium, opt_key = get_option_strike("PUT")
                            if opt_strike and opt_key:
                                trade = enter_trade("PUT", opt_key, opt_strike, opt_delta,
                                                   opt_premium, old_sl, nifty_ltp, flip=True)

                    elif direction == "PUT" and candle_close > trade['hard_sl']:
                        log(f"🛑 PUT SL! Close:{candle_close} > SL:{trade['hard_sl']}")
                        do_exit(trade['instrument_key'], direction, trade['entry_premium'],
                                trade['strike'], "🛑 SL HIT")
                        old_sl = trade['hard_sl']
                        trade  = None

                        # FLIP to CALL - sirf ek baar, 2:30 se pehle
                        if not flip_done and can_trade() and nifty_ltp:
                            flip_done = True
                            opt_strike, opt_delta, opt_premium, opt_key = get_option_strike("CALL")
                            if opt_strike and opt_key:
                                trade = enter_trade("CALL", opt_key, opt_strike, opt_delta,
                                                   opt_premium, old_sl, nifty_ltp, flip=True)

            # =============================================
            # ENTRY CHECK - Naya 30 min candle close pe
            # =============================================
            elif trade is None and can_trade() and new_candle:

                if candle_close > body_top:
                    hard_sl = candle_low
                    log(f"🎯 CALL! Close:{candle_close} > Body Top:{body_top} | SL:{hard_sl}")
                    opt_strike, opt_delta, opt_premium, opt_key = get_option_strike("CALL")
                    if opt_strike and opt_key:
                        trade = enter_trade("CALL", opt_key, opt_strike, opt_delta,
                                           opt_premium, hard_sl, nifty_ltp)
                    else:
                        log(f"⚠️ CALL strike nahi mila - next candle pe try karenge!")

                elif candle_close < body_bottom:
                    hard_sl = candle_high
                    log(f"🎯 PUT! Close:{candle_close} < Body Bottom:{body_bottom} | SL:{hard_sl}")
                    opt_strike, opt_delta, opt_premium, opt_key = get_option_strike("PUT")
                    if opt_strike and opt_key:
                        trade = enter_trade("PUT", opt_key, opt_strike, opt_delta,
                                           opt_premium, hard_sl, nifty_ltp)
                    else:
                        log(f"⚠️ PUT strike nahi mila - next candle pe try karenge!")

                else:
                    log(f"[{now.strftime('%H:%M')}] Body ke andar - wait | Close:{candle_close} | Top:{body_top} | Bottom:{body_bottom}")

            status = f"Trade:{trade is not None}"
            if trade:
                status += f" | Dir:{trade['direction']} | Trail:{trade['trail_active']}"
            log(f"[{now.strftime('%H:%M:%S')}] {status}")

        except Exception as e:
            log(f"❌ Error: {e}")
            time.sleep(10)
            continue

        time.sleep(sleep_time)

if __name__ == "__main__":
    main()
