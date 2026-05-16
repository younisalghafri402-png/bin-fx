import MetaTrader5 as mt5
import requests
import time
import threading

# =========================================
# ACCOUNT
# =========================================
ACCOUNT_LOGIN = 192627270
ACCOUNT_PASSWORD = "Younis@9200"
ACCOUNT_SERVER = "Exness-MT5Trial"

SYMBOL = "XAUUSDm"

# =========================================
# TELEGRAM
# =========================================
TELEGRAM_TOKEN = "7974914269:AAEbras7Lbfr7WzTPRazg3O9C1VW2j3Rosg"
CHAT_ID = "-1002781710848"

# =========================================
# TIMEFRAMES
# =========================================
TIMEFRAMES = {
    mt5.TIMEFRAME_M10: "10M",
    mt5.TIMEFRAME_M15: "15M",
    mt5.TIMEFRAME_M30: "30M",
    mt5.TIMEFRAME_H1: "1H",
    mt5.TIMEFRAME_H4: "4H"
}

last_candle_time = {}

unsecured_trade_exists = False

# =========================================
# TELEGRAM
# =========================================
def send_msg(text):

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    try:

        requests.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": text
            },
            timeout=10
        )

    except Exception as e:
        print(e)

# =========================================
# CRT DETECTION
# =========================================
def check_crt_pattern(tf):

    rates = mt5.copy_rates_from_pos(
        SYMBOL,
        tf,
        0,
        5
    )

    if rates is None or len(rates) < 5:
        return None

    crt = rates[1]
    prev = rates[2]

    # =====================================
    # BUY CRT
    # =====================================
    if (
        crt['low'] < prev['low']
        and crt['close'] > prev['low']
        and crt['close'] > crt['open']
    ):

        return {
            "side": "BUY",
            "crt_high": crt['high'],
            "crt_low": crt['low'],
            "entry": crt['close']
        }

    # =====================================
    # SELL CRT
    # =====================================
    if (
        crt['high'] > prev['high']
        and crt['close'] < prev['high']
        and crt['close'] < crt['open']
    ):

        return {
            "side": "SELL",
            "crt_high": crt['high'],
            "crt_low": crt['low'],
            "entry": crt['close']
        }

    return None

# =========================================
# MSS + FVG
# M1
# =========================================
def get_mss_and_fvg(side):

    rates = mt5.copy_rates_from_pos(
        SYMBOL,
        mt5.TIMEFRAME_M1,
        0,
        20
    )

    if rates is None or len(rates) < 10:
        return None

    # =====================================
    # BUY
    # =====================================
    if side == "BUY":

        prev_high = rates[3]['high']
        current_high = rates[1]['high']

        # MSS
        if current_high > prev_high:

            candle1 = rates[3]
            candle2 = rates[2]
            candle3 = rates[1]

            # FVG
            if candle1['high'] < candle3['low']:

                fvg_low = candle1['high']
                fvg_high = candle3['low']

                entry = round(
                    (fvg_low + fvg_high) / 2,
                    2
                )

                return {
                    "entry": entry
                }

    # =====================================
    # SELL
    # =====================================
    if side == "SELL":

        prev_low = rates[3]['low']
        current_low = rates[1]['low']

        # MSS
        if current_low < prev_low:

            candle1 = rates[3]
            candle2 = rates[2]
            candle3 = rates[1]

            # FVG
            if candle1['low'] > candle3['high']:

                fvg_high = candle1['low']
                fvg_low = candle3['high']

                entry = round(
                    (fvg_high + fvg_low) / 2,
                    2
                )

                return {
                    "entry": entry
                }

    return None

# =========================================
# MONITOR TRADE
# =========================================
def monitor_trade_thread(side, entry, tp1, tp2, sl):

    global unsecured_trade_exists

    unsecured_trade_exists = True

    tp1_hit = False
    current_sl = sl

    while True:

        tick = mt5.symbol_info_tick(SYMBOL)

        if tick is None:
            continue

        price = tick.bid if side == "BUY" else tick.ask

        # =====================================
        # BUY
        # =====================================
        if side == "BUY":

            if not tp1_hit and price >= tp1:

                send_msg(
                    f"✅ TP1 HIT\n"
                    f"SL MOVED TO ENTRY : {entry}"
                )

                tp1_hit = True
                current_sl = entry

            if price >= tp2:

                send_msg("🏆 TP2 HIT")

                unsecured_trade_exists = False

                break

            if price <= current_sl:

                if tp1_hit:
                    send_msg("EXIT AT ENTRY")

                else:
                    send_msg("🛑 SL HIT")

                unsecured_trade_exists = False

                break

        # =====================================
        # SELL
        # =====================================
        if side == "SELL":

            if not tp1_hit and price <= tp1:

                send_msg(
                    f"✅ TP1 HIT\n"
                    f"SL MOVED TO ENTRY : {entry}"
                )

                tp1_hit = True
                current_sl = entry

            if price <= tp2:

                send_msg("🏆 TP2 HIT")

                unsecured_trade_exists = False

                break

            if price >= current_sl:

                if tp1_hit:
                    send_msg("EXIT AT ENTRY")

                else:
                    send_msg("🛑 SL HIT")

                unsecured_trade_exists = False

                break

        time.sleep(1)

# =========================================
# SEND SIGNAL
# =========================================
def send_trade(side, tf_name, entry, tp1, tp2, sl):

    send_msg(

        f"🔥 CRT {side} [{tf_name}]\n\n"

        f"ENTRY : {entry}\n"

        f"TP1 : {tp1}\n"

        f"TP2 : {tp2}\n"

        f"SL : {sl}"

    )

# =========================================
# RUN BOT
# =========================================
def run_bot():

    global last_candle_time

    print("CRT BOT ONLINE")

    while True:

        for tf in TIMEFRAMES:

            rates = mt5.copy_rates_from_pos(
                SYMBOL,
                tf,
                0,
                1
            )

            if rates is None:
                continue

            current_time = rates[0]['time']

            if tf not in last_candle_time:

                last_candle_time[tf] = current_time
                continue

            # =====================================
            # NEW CANDLE
            # =====================================
            if current_time != last_candle_time[tf]:

                last_candle_time[tf] = current_time

                crt = check_crt_pattern(tf)

                if not crt:
                    continue

                side = crt['side']

                crt_high = crt['crt_high']
                crt_low = crt['crt_low']

                tf_name = TIMEFRAMES[tf]

                # =====================================
                # SEND CRT ALERT
                # =====================================
                send_msg(
                    f"⚡ CRT DETECTED [{tf_name}]\n\n"
                    f"SIDE : {side}"
                )

                # =====================================
                # IF 10M
                # DIRECT ENTRY
                # =====================================
                if tf == mt5.TIMEFRAME_M10:

                    if unsecured_trade_exists:
                        continue

                    entry = round(crt['entry'], 2)

                    range_size = crt_high - crt_low

                    # BUY
                    if side == "BUY":

                        sl = round(crt_low, 2)

                        tp1 = round(
                            entry + (range_size * 0.5),
                            2
                        )

                        tp2 = round(
                            crt_high,
                            2
                        )

                        send_trade(
                            "BUY",
                            tf_name,
                            entry,
                            tp1,
                            tp2,
                            sl
                        )

                        threading.Thread(
                            target=monitor_trade_thread,
                            args=(
                                "BUY",
                                entry,
                                tp1,
                                tp2,
                                sl
                            )
                        ).start()

                    # SELL
                    elif side == "SELL":

                        sl = round(crt_high, 2)

                        tp1 = round(
                            entry - (range_size * 0.5),
                            2
                        )

                        tp2 = round(
                            crt_low,
                            2
                        )

                        send_trade(
                            "SELL",
                            tf_name,
                            entry,
                            tp1,
                            tp2,
                            sl
                        )

                        threading.Thread(
                            target=monitor_trade_thread,
                            args=(
                                "SELL",
                                entry,
                                tp1,
                                tp2,
                                sl
                            )
                        ).start()

                # =====================================
                # OTHER TIMEFRAMES
                # NEED MSS + FVG
                # =====================================
                else:

                    if unsecured_trade_exists:
                        continue

                    mss_fvg = get_mss_and_fvg(side)

                    if not mss_fvg:
                        continue

                    entry = mss_fvg['entry']

                    range_size = crt_high - crt_low

                    # BUY
                    if side == "BUY":

                        sl = round(crt_low, 2)

                        tp1 = round(
                            entry + (range_size * 0.5),
                            2
                        )

                        tp2 = round(
                            crt_high,
                            2
                        )

                        send_trade(
                            "BUY",
                            tf_name,
                            entry,
                            tp1,
                            tp2,
                            sl
                        )

                        threading.Thread(
                            target=monitor_trade_thread,
                            args=(
                                "BUY",
                                entry,
                                tp1,
                                tp2,
                                sl
                            )
                        ).start()

                    # SELL
                    elif side == "SELL":

                        sl = round(crt_high, 2)

                        tp1 = round(
                            entry - (range_size * 0.5),
                            2
                        )

                        tp2 = round(
                            crt_low,
                            2
                        )

                        send_trade(
                            "SELL",
                            tf_name,
                            entry,
                            tp1,
                            tp2,
                            sl
                        )

                        threading.Thread(
                            target=monitor_trade_thread,
                            args=(
                                "SELL",
                                entry,
                                tp1,
                                tp2,
                                sl
                            )
                        ).start()

        time.sleep(2)

# =========================================
# MAIN
# =========================================
if __name__ == "__main__":

    if not mt5.initialize():

        print("MT5 INIT FAILED")
        quit()

    authorized = mt5.login(
        ACCOUNT_LOGIN,
        password=ACCOUNT_PASSWORD,
        server=ACCOUNT_SERVER
    )

    if not authorized:

        print("LOGIN FAILED")
        quit()

    try:

        run_bot()

    except KeyboardInterrupt:

        print("BOT STOPPED")

    finally:

        mt5.shutdown()