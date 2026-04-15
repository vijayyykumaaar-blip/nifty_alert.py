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

# MCX NatGas Mini timings
MARKET_OPEN  = dtime(9, 0)
MARKET_CLOSE = dtime(23, 30)
EOD_EXIT     = dtime(23, 0)

# Trade settings
LOT_SIZE  = 250              # 1 lot = 250 units
FUT_KEY   = "MCX_FO|487466"  # NATGASMINI FUT 27 APR 26
OPTION_UL = "MCX_FO|487466"  # Option chain underlying
EXPIRY    = "2026-04-23"     # NatGas Mini expiry

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

def is_eod():
    return ist_time() >= EOD_EXIT

# =============================================
# 5 MIN CANDLES - NatGas Mini FUT
# =============================================
def get_candles_5min():
    try:
        encoded = FUT_KEY.replace("|", "%7C")
        url     = f"https://api.upstox.com/v3/historical-candle/intraday/{encoded}/minutes/5"
        r       = requests.get(url, headers=get_headers(), timeout=10)
        data    = r.json()
        if data.get('status') != 'success':
            log(f"❌ Candle API error: {data}")
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
        return result if len(result) >= 1 else None
    except Exception as e:
        log(f"❌ Candle error: {e}")
        return None

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

# =============================================
# OPTION STRIKE - ATM CALL
# =============================================
def get_natgas_call():
    try:
        spot = get_natgas_ltp()
        if not spot:
            log("❌ NatGas LTP nahi mila!")
            return None, None, None, None

        url  = f"https://api.upstox.com/v2/option/chain?instrument_key={OPTION_UL}&expiry_date={EXPIRY}"
        r    = requests.get(url, headers=get_headers(), timeout=10)
        data = r.json()

        if data.get('status') != 'success':
            log(f"❌ Option chain error: {data}")
            return None, None, None, None

        log(f"🔍 CALL | Spot:{spot:.2f}")

        best_strike = best_delta = best_premium = best_instrument = None
        best_diff   = float('inf')

        for option in data['data']:
            opt_data   = option.get('call_options', {})
            if not opt_data:
                continue
            premium    = opt_data.get('market_data', {}).get('ltp', 0)
            strike     = option.get('strike_price', 0)
            instrument = opt_data.get('instrument_key', '')
            delta      = opt_data.get('option_greeks', {}).get('delta', 0)

            if not instrument or premium <= 0:
                continue

            diff = abs(strike - spot)
            if diff < best_diff:
                best_diff       = diff
                best_strike     = strike
                best_delta      = delta
                best_premium    = premium
                best_instrument = instrument

        if best_strike is None:
            log(f"⚠️ CALL strike nahi mila!")
            return None, None, None, None

        log(f"✅ CALL | Strike:{best_strike} | Premium:₹{best_premium}")
        return best_strike, best_delta, best_premium, best_instrument

    except Exception as e:
        log(f"❌ Option chain error: {e}")
        return None, None, None, None

# =============================================
# ORDER
# =============================================
def place_order(instrument_key, transaction_type="BUY"):
    try:
        url     = "https://api.upstox.com/v2/order/place"
        payload = {
            "quantity"          : LOT_SIZE,
            "product"           : "D",
            "validity"          : "DAY",
            "price"             : 0,
            "tag"               : "NatGasAlgo",
            "instrument_key"    : instrument_key,
            "order_type"        : "MARKET",
            "transaction_type"  : transaction_type,
            "disclosed_quantity": 0,
            "trigger_price"     : 0,
            "is_amo"            : False
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

# =============================================
# MAIN
# =============================================
def main():
    log("🚀 NatGas Mini 5Min Green Candle Algo Started!")
    send_alert(
        "🚀 <b>NatGas Mini Algo Started!</b>\n\n"
        "📊 Strategy: 5 Min Green Candle\n"
        "🟢 Green candle = CALL BUY\n"
        "⏱ Hold: 5 minutes then EXIT\n"
        "📦 Lot: 250 units\n"
        "1️⃣ 1 Trade per day only"
    )

    last_reset        = None
    traded_today      = False
    trade             = None
    entry_time        = None
    prev_candle_count = 0

    while True:
        try:
            now   = ist_now()
            today = now.date()

            # Daily reset
            if last_reset != today:
                traded_today      = False
                trade             = None
                entry_time        = None
                prev_candle_count = 0
                last_reset        = today
                log(f"🔄 Daily reset! {today}")

            # Market closed
            if not is_market_open():
                log(f"[{now.strftime('%H:%M')}] Market closed. Sleep 15 min...")
                time.sleep(900)
                continue

            sleep_time = 10

            # Candles fetch
            candles = get_candles_5min()
            if not candles:
                log(f"[{now.strftime('%H:%M')}] No candle data!")
                time.sleep(sleep_time)
                continue

            # Naya 5 min candle close hua?
            new_candle = len(candles) > prev_candle_count
            if new_candle:
                prev_candle_count = len(candles)
                log(f"✅ Naya 5 min candle! Total:{len(candles)}")

            last_closed  = candles[-1]
            candle_open  = last_closed['open']
            candle_close = last_closed['close']

            ltp = get_natgas_ltp()

            # EOD EXIT
            if trade is not None and is_eod():
                curr_prem = get_current_premium(trade['instrument_key']) or trade['entry_premium']
                pnl       = round((curr_prem - trade['entry_premium']) * LOT_SIZE, 2)
                place_order(trade['instrument_key'], "SELL")
                send_alert(
                    f"⏰ <b>EOD EXIT!</b>\n\n"
                    f"📊 {trade['strike']} CALL\n"
                    f"💰 Entry:₹{trade['entry_premium']} Exit:₹{curr_prem}\n"
                    f"📈 P&L: ₹{pnl}\n"
                    f"🕐 {now.strftime('%H:%M')} IST"
                )
                trade = None
                time.sleep(sleep_time)
                continue

            # 5 MIN BAAD EXIT
            if trade is not None and entry_time is not None:
                elapsed = (now - entry_time).total_seconds()
                log(f"[{now.strftime('%H:%M:%S')}] In trade | {elapsed:.0f}s / 300s | LTP:{ltp}")

                if elapsed >= 300:
                    curr_prem = get_current_premium(trade['instrument_key']) or trade['entry_premium']
                    pnl       = round((curr_prem - trade['entry_premium']) * LOT_SIZE, 2)
                    emoji     = "✅" if pnl > 0 else "❌"
                    place_order(trade['instrument_key'], "SELL")
                    send_alert(
                        f"{emoji} <b>5 MIN EXIT!</b>\n\n"
                        f"📊 {trade['strike']} CALL\n"
                        f"💰 Entry:₹{trade['entry_premium']} Exit:₹{curr_prem}\n"
                        f"📈 P&L: ₹{pnl}\n"
                        f"🕐 {now.strftime('%H:%M')} IST"
                    )
                    trade      = None
                    entry_time = None
                    time.sleep(sleep_time)
                    continue

            # GREEN CANDLE PE CALL BUY
            elif trade is None and not traded_today and new_candle:
                is_green = candle_close > candle_open

                if is_green:
                    log(f"🟢 Green! O:{candle_open} C:{candle_close} | CALL ENTRY!")
                    opt_strike, opt_delta, opt_premium, opt_key = get_natgas_call()
                    if opt_strike and opt_key:
                        order_id = place_order(opt_key, "BUY")
                        if order_id:
                            trade = {
                                'instrument_key': opt_key,
                                'strike'        : opt_strike,
                                'entry_premium' : opt_premium,
                            }
                            entry_time   = now
                            traded_today = True
                            send_alert(
                                f"📈 <b>CALL ENTRY! (Green Candle)</b>\n\n"
                                f"📊 Strike: {opt_strike} CALL\n"
                                f"🕯 Open:{candle_open} → Close:{candle_close}\n"
                                f"💰 Premium: ₹{opt_premium}\n"
                                f"⏱ Exit: 5 min baad\n"
                                f"📦 Qty: {LOT_SIZE}\n"
                                f"🕐 {now.strftime('%H:%M')} IST"
                            )
                    else:
                        log("⚠️ Strike nahi mila!")
                else:
                    log(f"[{now.strftime('%H:%M')}] Red candle - skip | O:{candle_open} C:{candle_close}")

            log(f"[{now.strftime('%H:%M:%S')}] Trade:{trade is not None} | Traded:{traded_today}")

        except Exception as e:
            log(f"❌ Error: {e}")
            time.sleep(10)
            continue

        time.sleep(sleep_time)

if __name__ == "__main__":
    while True:
        try:
            main()
        except Exception as e:
            log(f"❌ Main crashed: {e}")
            log("🔄 Restarting in 60 seconds...")
            time.sleep(60)
