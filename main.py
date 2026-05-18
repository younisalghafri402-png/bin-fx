import asyncio
import aiohttp
import aiohttp.resolver
from datetime import datetime

# =============================================
# 1. إعدادات التحليل
# =============================================
SYMBOL = "XAUUSDT"
DEPTH_LIMIT = 10
IMBALANCE_THRESHOLD = 0.80

# =============================================
# 2. إعدادات الهدف والستوب
# =============================================
TP_POINTS = 100
SL_POINTS = 100

# =============================================
# 3. إعدادات تليجرام
# =============================================
TELEGRAM_BOT_TOKEN = "8292443875:AAHVG6THkf9zL2r-1B2DVUcUl4yfWXS52zg"
TELEGRAM_CHAT_ID = "-1003952441740"

# =============================================
# 4. حالة البوت
# =============================================
current_trade = {
    "active": False,
    "type": None,
    "entry_price": 0,
    "tp": 0,
    "sl": 0,
    "tp_hit": False,
    "sl_hit": False
}

# =============================================
# 5. جلب البيانات من Binance (SPOT)
# =============================================
async def fetch_order_book(session):
    url = f"https://api.binance.com/api/v3/depth?symbol={SYMBOL}&limit={DEPTH_LIMIT}"
    try:
        async with session.get(url, timeout=5) as response:  # خفضت timeout
            if response.status == 200:
                return await response.json()
            else:
                text = await response.text()
                print(f"خطأ في العمق: HTTP {response.status} - {text[:200]}")
    except Exception as e:
        print(f"خطأ في جلب العمق: {e}")
    return None

async def get_current_price(session):
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={SYMBOL}"
    try:
        async with session.get(url, timeout=5) as response:  # خفضت timeout
            if response.status == 200:
                data = await response.json()
                return float(data['price'])
            else:
                text = await response.text()
                print(f"خطأ في السعر: HTTP {response.status} - {text[:200]}")
    except Exception as e:
        print(f"خطأ في جلب السعر: {e}")
    return None

def calculate_imbalance(order_book):
    bids = order_book.get('bids', [])
    asks = order_book.get('asks', [])
    total_bid = sum(float(b[1]) for b in bids[:5])
    total_ask = sum(float(a[1]) for a in asks[:5])
    total = total_bid + total_ask
    return total_bid / total if total > 0 else 0.5

def calculate_sltp(signal, current_price):
    if signal == "BUY":
        tp = current_price + TP_POINTS * 0.01
        sl = current_price - SL_POINTS * 0.01
    else:
        tp = current_price - TP_POINTS * 0.01
        sl = current_price + SL_POINTS * 0.01
    return round(sl, 2), round(tp, 2)

def generate_signal(imbalance, current_price):
    if imbalance > IMBALANCE_THRESHOLD:
        signal = "BUY"
        sl, tp = calculate_sltp("BUY", current_price)
        reason = f"DEMAND: {imbalance*100:.0f}% [Extreme]"
        return signal, sl, tp, reason
    elif imbalance < (1 - IMBALANCE_THRESHOLD):
        signal = "SELL"
        sl, tp = calculate_sltp("SELL", current_price)
        reason = f"SUPPLY: {(1-imbalance)*100:.0f}% [Extreme]"
        return signal, sl, tp, reason
    return None, None, None, None

async def send_telegram_message(session, message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        async with session.post(url, json=payload, timeout=5) as resp:
            if resp.status != 200:
                print(f"فشل الإرسال: {await resp.text()}")
    except Exception as e:
        print(f"خطأ في إرسال تلغرام: {e}")

def format_signal_message(symbol, signal, entry, sl, tp, reason, imbalance):
    if signal == "BUY":
        arrow = "⚡"
        action = "BUY"
        demand_supply = "🔥"
    else:
        arrow = "⚡"
        action = "SELL"
        demand_supply = "❄️"
    message = f"""{arrow} {symbol} (GOLD) — {action} {arrow}
──────────────────
📍 ENTRY  ➔  {entry:.2f}

🎯 TP1    ➔  {tp:.2f}

🛑 SL     ➔  {sl:.2f}
──────────────────
📊 IMBALANCE: {imbalance:.2f}
{demand_supply} {reason}"""
    return message

async def fetch_order_book_futures(session):
    url = f"https://fapi.binance.com/fapi/v1/depth?symbol={SYMBOL}&limit={DEPTH_LIMIT}"
    try:
        async with session.get(url, timeout=5) as response:
            if response.status == 200:
                return await response.json()
            else:
                text = await response.text()
                print(f"خطأ في العمق (futures): HTTP {response.status} - {text[:200]}")
    except Exception as e:
        print(f"خطأ في جلب العمق (futures): {e}")
    return None

async def get_current_price_futures(session):
    url = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={SYMBOL}"
    try:
        async with session.get(url, timeout=5) as response:
            if response.status == 200:
                data = await response.json()
                return float(data['price'])
            else:
                text = await response.text()
                print(f"خطأ في السعر (futures): HTTP {response.status} - {text[:200]}")
    except Exception as e:
        print(f"خطأ في جلب السعر (futures): {e}")
    return None

async def main():
    global current_trade
    print(f"🚀 بوت السكالبنج - {SYMBOL}")
    print(f"🎯 الهدف: {TP_POINTS} نقطة | 🛑 الستوب: {SL_POINTS} نقطة")
    print("📌 ينتظر إشارات قوية (عدم توازن 80%+)")
    print("⚡ سرعة التحديث: 0.5 ثانية (120 تحديث/دقيقة)\n")
    print("🔄 تم إعداد DNS مخصص (8.8.8.8) لتجنب مشاكل الاتصال...")
    
    resolver = aiohttp.resolver.AsyncResolver(nameservers=['8.8.8.8', '8.8.4.4'])
    connector = aiohttp.TCPConnector(resolver=resolver)
    
    current_get_price = get_current_price
    current_fetch_order_book = fetch_order_book
    
    async with aiohttp.ClientSession(connector=connector) as session:
        test_price = await current_get_price(session)
        if test_price is None:
            print("❌ فشل الاتصال بـ Binance. حاول مرة أخرى بعد ثوانٍ...")
            print("🔄 محاولة استخدام futures endpoint...")
            try:
                test_price = await get_current_price_futures(session)
                print(f"✅ نجح الاتصال عبر futures! السعر: {test_price:.2f}")
                current_get_price = get_current_price_futures
                current_fetch_order_book = fetch_order_book_futures
            except Exception as e:
                print(f"❌ فشل الاتصال نهائياً: {e}")
                return
        else:
            print(f"✅ الاتصال بـ Binance ناجح! السعر الحالي: {test_price:.2f}")
        
        async def get_price(s):
            return await current_get_price(s)
        
        async def fetch_ob(s):
            return await current_fetch_order_book(s)
        
        await send_telegram_message(session, f"🤖 <b>بوت السكالبنج قيد التشغيل</b>\n📊 {SYMBOL}\n🎯 الهدف: {TP_POINTS} نقطة | 🛑 الستوب: {SL_POINTS} نقطة\n⚡ سرعة التحديث: 0.5 ثانية")
        
        async def monitor_with_funcs():
            while True:
                if current_trade["active"]:
                    current_price = await get_price(session)
                    if current_price:
                        trade_type = current_trade["type"]
                        tp = current_trade["tp"]
                        sl = current_trade["sl"]
                        if trade_type == "BUY" and current_price >= tp:
                            if not current_trade["tp_hit"]:
                                current_trade["tp_hit"] = True
                                current_trade["active"] = False
                                await send_telegram_message(session, f"✅ <b>TP1 {SYMBOL} DONE</b> 🥇")
                                print(f"🎯 ضرب الهدف عند {current_price}")
                        elif trade_type == "SELL" and current_price <= tp:
                            if not current_trade["tp_hit"]:
                                current_trade["tp_hit"] = True
                                current_trade["active"] = False
                                await send_telegram_message(session, f"✅ <b>TP1 {SYMBOL} DONE</b> 🥇")
                                print(f"🎯 ضرب الهدف عند {current_price}")
                        if trade_type == "BUY" and current_price <= sl:
                            if not current_trade["sl_hit"]:
                                current_trade["sl_hit"] = True
                                current_trade["active"] = False
                                await send_telegram_message(session, f"🛑 <b>SL Hit {SYMBOL}</b>")
                                print(f"🛑 ضرب الستوب عند {current_price}")
                        elif trade_type == "SELL" and current_price >= sl:
                            if not current_trade["sl_hit"]:
                                current_trade["sl_hit"] = True
                                current_trade["active"] = False
                                await send_telegram_message(session, f"🛑 <b>SL Hit {SYMBOL}</b>")
                                print(f"🛑 ضرب الستوب عند {current_price}")
                await asyncio.sleep(0.5)  # ← كل 0.5 ثانية لفحص الهدف
        
        asyncio.create_task(monitor_with_funcs())
        
        last_signal_key = None
        while True:
            try:
                if current_trade["active"]:
                    await asyncio.sleep(0.5)  # ← كل 0.5 ثانية أثناء الصفقة
                    continue
                
                order_book = await fetch_ob(session)
                if not order_book:
                    await asyncio.sleep(0.5)  # ← كل 0.5 ثانية عند الخطأ
                    continue
                
                current_price = await get_price(session)
                if not current_price:
                    await asyncio.sleep(0.5)  # ← كل 0.5 ثانية عند الخطأ
                    continue
                
                imbalance = calculate_imbalance(order_book)
                signal, sl, tp, reason = generate_signal(imbalance, current_price)
                
                print(f"\n⏰ {datetime.now().strftime('%H:%M:%S')} | السعر: {current_price:.2f} | OBI: {imbalance:.2f}")
                
                if signal:
                    current_key = (signal, round(current_price, 1))
                    if current_key != last_signal_key:
                        msg = format_signal_message(SYMBOL, signal, current_price, sl, tp, reason, imbalance)
                        await send_telegram_message(session, msg)
                        print(f"🔔 إشارة {signal} | الدخل: {current_price:.2f} | TP: {tp:.2f} | SL: {sl:.2f}")
                        current_trade = {
                            "active": True,
                            "type": signal,
                            "entry_price": current_price,
                            "tp": tp,
                            "sl": sl,
                            "tp_hit": False,
                            "sl_hit": False
                        }
                        last_signal_key = current_key
                        await asyncio.sleep(1)  # استراحة قصيرة بعد الإشارة
                else:
                    print(f"⚖️ عدم توازن {imbalance:.2f} - انتظار {IMBALANCE_THRESHOLD}+")
                
                await asyncio.sleep(0.5)  # ← كل 0.5 ثانية (التحديث الرئيسي)
                
            except Exception as e:
                print(f"خطأ رئيسي: {e}")
                await asyncio.sleep(0.5)  # ← كل 0.5 ثانية عند الخطأ

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 تم إيقاف البوت")