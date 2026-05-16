
import asyncio
import aiohttp
from datetime import datetime
from aiohttp import web
import threading
import os

# =============================================
# 1. إعدادات التحليل
# =============================================
SYMBOL = "XAUUSDT"
DEPTH_LIMIT = 10
IMBALANCE_THRESHOLD = 0.80 # 80% عتبة الدخول القوي

# =============================================
# 2. إعدادات الهدف والستوب (أوسع الآن)
# =============================================
# للذهب: 1 نقطة = 0.01 دولار (تقريباً)
TP_POINTS = 100 # الهدف 100 نقطة
SL_POINTS = 100 # الستوب 100 نقطة

# =============================================
# 3. إعدادات تليجرام
# =============================================
TELEGRAM_BOT_TOKEN = "8292443875:AAHVG6THkf9zL2r-1B2DVUcUl4yfWXS52zg"
TELEGRAM_CHAT_ID = "-1003952441740"

# =============================================
# 4. حالة البوت (تتبع الصفقة الحالية)
# =============================================
current_trade = {
    "active": False,
    "type": None, # "BUY" أو "SELL"
    "entry_price": 0,
    "tp": 0,
    "sl": 0,
    "tp_hit": False,
    "sl_hit": False
}

# =============================================
# 5. جلب البيانات من Binance Futures API
# =============================================
async def fetch_order_book(session):
    """جلب عمق السوق من Binance Futures"""
    url = f"https://fapi.binance.com/fapi/v1/depth?symbol={SYMBOL}&limit={DEPTH_LIMIT}"
    try:
        async with session.get(url, timeout=5) as response:
            if response.status == 200:
                return await response.json()
            else:
                print(f"خطأ في جلب العمق: HTTP {response.status}")
    except asyncio.TimeoutError:
        print("انتهى الوقت المحدد لجلب العمق")
    except Exception as e:
        print(f"خطأ في جلب العمق: {e}")
    return None

async def get_current_price(session):
    """جلب السعر الحالي من Binance Futures"""
    url = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={SYMBOL}"
    try:
        async with session.get(url, timeout=5) as response:
            if response.status == 200:
                data = await response.json()
                return float(data['price'])
            else:
                print(f"خطأ في جلب السعر: HTTP {response.status}")
    except asyncio.TimeoutError:
        print("انتهى الوقت المحدد لجلب السعر")
    except Exception as e:
        print(f"خطأ في جلب السعر: {e}")
    return None

def calculate_imbalance(order_book):
    """حساب عدم التوازن من أول 5 مستويات"""
    bids = order_book.get('bids', [])
    asks = order_book.get('asks', [])
    total_bid = sum(float(b[1]) for b in bids[:5])
    total_ask = sum(float(a[1]) for a in asks[:5])
    total = total_bid + total_ask
    return total_bid / total if total > 0 else 0.5

def calculate_sltp(signal, current_price):
    """حساب الهدف والستوب بنقاط ثابتة"""
    if signal == "BUY":
        tp = current_price + TP_POINTS * 0.01 # 100 نقطة هدف
        sl = current_price - SL_POINTS * 0.01 # 100 نقطة ستوب
    else: # SELL
        tp = current_price - TP_POINTS * 0.01
        sl = current_price + SL_POINTS * 0.01
    return round(sl, 2), round(tp, 2)

def generate_signal(imbalance, current_price):
    """توليد الإشارة فقط عند ظروف قوية"""
    if imbalance > IMBALANCE_THRESHOLD:
        signal = "BUY"
        sl, tp = calculate_sltp("BUY", current_price)
        reason = f"طلب مرتفع جداً {imbalance*100:.0f}%"
        return signal, sl, tp, reason
    elif imbalance < (1 - IMBALANCE_THRESHOLD):
        signal = "SELL"
        sl, tp = calculate_sltp("SELL", current_price)
        reason = f"عرض مرتفع جداً {(1-imbalance)*100:.0f}%"
        return signal, sl, tp, reason
    return None, None, None, None

# =============================================
# 6. إرسال إلى تليجرام
# =============================================
async def send_telegram_message(session, message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        async with session.post(url, json=payload, timeout=5) as resp:
            if resp.status != 200:
                print(f"فشل الإرسال: {await resp.text()}")
    except Exception as e:
        print(f"خطأ في إرسال التليجرام: {e}")

def format_signal_message(symbol, signal, entry, sl, tp, reason, imbalance):
    """تنسيق التوصية"""
    if signal == "BUY":
        arrow = "🔥"
        action = "شراء"
    else:
        arrow = "❄️"
        action = "بيع"
    
    message = f"""{arrow} *{symbol}* {action} {entry:.2f}

🥇 TP1: {tp:.2f}
🛑 SL: {sl:.2f}

📊 عدم توازن: {imbalance:.2f}
💡 {reason}"""
    return message

# =============================================
# 7. مراقبة الصفقة الحالية
# =============================================
async def monitor_current_trade(session):
    global current_trade
    while True:
        if current_trade["active"]:
            current_price = await get_current_price(session)
            if current_price:
                trade_type = current_trade["type"]
                tp = current_trade["tp"]
                sl = current_trade["sl"]
                
                if trade_type == "BUY" and current_price >= tp:
                    if not current_trade["tp_hit"]:
                        current_trade["tp_hit"] = True
                        current_trade["active"] = False
                        await send_telegram_message(session, f"✅ *TP1 {SYMBOL} DONE* 🥇")
                        print(f"🎯 ضرب الهدف عند {current_price}")
                elif trade_type == "SELL" and current_price <= tp:
                    if not current_trade["tp_hit"]:
                        current_trade["tp_hit"] = True
                        current_trade["active"] = False
                        await send_telegram_message(session, f"✅ *TP1 {SYMBOL} DONE* 🥇")
                        print(f"🎯 ضرب الهدف عند {current_price}")
                
                if trade_type == "BUY" and current_price <= sl:
                    if not current_trade["sl_hit"]:
                        current_trade["sl_hit"] = True
                        current_trade["active"] = False
                        await send_telegram_message(session, f"🛑 *SL Hit {SYMBOL}*")
                        print(f"🛑 ضرب الستوب عند {current_price}")
                elif trade_type == "SELL" and current_price >= sl:
                    if not current_trade["sl_hit"]:
                        current_trade["sl_hit"] = True
                        current_trade["active"] = False
                        await send_telegram_message(session, f"🛑 *SL Hit {SYMBOL}*")
                        print(f"🛑 ضرب الستوب عند {current_price}")
        await asyncio.sleep(1)

# =============================================
# 8. الحلقة الرئيسية للتداول
# =============================================
async def main():
    global current_trade
    print(f"🚀 بوت السكالبنج - {SYMBOL}")
    print(f"🎯 الهدف: {TP_POINTS} نقطة | 🛑 الستوب: {SL_POINTS} نقطة")
    print("📌 ينتظر إشارات عند نهاية شمعة 1 دقيقة (عدم توازن 80%+)\n")
    
    async with aiohttp.ClientSession() as session:
        await send_telegram_message(session, f"🤖 *بوت السكالبنج قيد التشغيل*\n📊 {SYMBOL}\n🎯 الهدف: {TP_POINTS} نقطة | 🛑 الستوب: {SL_POINTS} نقطة\n⏱️ الإشارات عند انتهاء شمعة 1 دقيقة")
        
        asyncio.create_task(monitor_current_trade(session))
        
        last_minute = None
        last_signal_key = None
        
        while True:
            try:
                if not current_trade["active"]:
                    order_book = await fetch_order_book(session)
                    current_price = await get_current_price(session)
                    
                    if order_book is None or current_price is None:
                        await asyncio.sleep(0.5)
                        continue
                    
                    imbalance = calculate_imbalance(order_book)
                    now = datetime.now()
                    current_minute = now.replace(second=0, microsecond=0)
                    
                    # عرض التحديث المستمر
                    print(f"\r⏰ {now.strftime('%H:%M:%S')} | السعر: {current_price:.2f} | OBI: {imbalance:.2f}", end="")
                    
                    # عند انتهاء شمعة الدقيقة
                    if last_minute is not None and current_minute != last_minute:
                        print(f"\n🔔 نهاية الشمعة {last_minute.strftime('%H:%M')} - التحقق من الإشارة...")
                        # جلب آخر بيانات لضمان الدقة
                        final_order_book = await fetch_order_book(session)
                        final_price = await get_current_price(session)
                        if final_order_book and final_price:
                            final_imbalance = calculate_imbalance(final_order_book)
                            signal, sl, tp, reason = generate_signal(final_imbalance, final_price)
                            if signal:
                                current_key = (signal, round(final_price, 1))
                                if current_key != last_signal_key:
                                    msg = format_signal_message(SYMBOL, signal, final_price, sl, tp, reason, final_imbalance)
                                    await send_telegram_message(session, msg)
                                    print(f"\n🔔 إشارة {signal} | الدخل: {final_price:.2f} | TP: {tp:.2f} | SL: {sl:.2f}")
                                    current_trade = {
                                        "active": True,
                                        "type": signal,
                                        "entry_price": final_price,
                                        "tp": tp,
                                        "sl": sl,
                                        "tp_hit": False,
                                        "sl_hit": False
                                    }
                                    last_signal_key = current_key
                            else:
                                print(f"⚖️ عدم توازن {final_imbalance:.2f} - لم يتجاوز العتبة {IMBALANCE_THRESHOLD}")
                    
                    last_minute = current_minute
                
                await asyncio.sleep(0.5) # تحديث مرتين في الثانية
                
            except Exception as e:
                print(f"\n⚠️ خطأ رئيسي: {e}")
                await asyncio.sleep(2)

# =============================================
# 9. خادم ويب صحي لـ Render
# =============================================
async def health(request):
    return web.Response(text="Bot is running")

def run_web():
    app = web.Application()
    app.router.add_get('/', health)
    port = int(os.environ.get("PORT", 10000))
    web.run_app(app, host='0.0.0.0', port=port)

# =============================================
# 10. تشغيل البوت مع خادم الويب في الخلفية
# =============================================
if __name__ == "__main__":
    # تشغيل خادم الويب في ثريد منفصل
    threading.Thread(target=run_web, daemon=True).start()
    
    # تشغيل بوت التداول الرئيسي
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 تم إيقاف البوت")
