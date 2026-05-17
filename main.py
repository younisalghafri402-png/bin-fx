import asyncio
import aiohttp
from datetime import datetime

# =============================================
# 1. إعدادات التحليل
# =============================================
SYMBOL = "XAUUSDT"
DEPTH_LIMIT = 10
IMBALANCE_THRESHOLD = 0.80  # 80% عتبة الدخول القوي

# =============================================
# 2. إعدادات الهدف والستوب (أوسع الآن)
# =============================================
# للذهب: 1 نقطة = 0.01 دولار (تقريباً)
# الهدف: 5-10 نقاط، الستوب: 8-12 نقطة
TP_POINTS = 100      # الهدف 8 نقاط
SL_POINTS = 100     # الستوب 10 نقاط

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
    "type": None,       # "BUY" أو "SELL"
    "entry_price": 0,
    "tp": 0,
    "sl": 0,
    "tp_hit": False,
    "sl_hit": False
}

# =============================================
# 5. جلب البيانات من Binance (بدون API Key)
# =============================================
async def fetch_order_book(session):
    url = f"https://fapi.binance.com/fapi/v1/depth?symbol={SYMBOL}&limit={DEPTH_LIMIT}"
    try:
        async with session.get(url, timeout=5) as response:
            if response.status == 200:
                return await response.json()
    except Exception as e:
        print(f"خطأ في جلب العمق: {e}")
    return None

async def get_current_price(session):
    url = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={SYMBOL}"
    try:
        async with session.get(url, timeout=5) as response:
            data = await response.json()
            return float(data['price'])
    except:
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
    """حساب الهدف والستوب بنقاط ثابتة (أوسع الآن)"""
    if signal == "BUY":
        tp = current_price + TP_POINTS * 0.01   # 8 نقاط هدف
        sl = current_price - SL_POINTS * 0.01   # 10 نقاط ستوب
    else:  # SELL
        tp = current_price - TP_POINTS * 0.01   # 8 نقاط هدف
        sl = current_price + SL_POINTS * 0.01   # 10 نقاط ستوب
    
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
# 6. إرسال إلى تليجرام (بالتنسيق الصحيح)
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
        print(f"خطأ: {e}")

def format_signal_message(symbol, signal, entry, sl, tp, reason, imbalance):
    """تنسيق التوصية بالشكل الذي تريده"""
    if signal == "BUY":
        arrow = "🔥"
        action = "شراء"
    else:
        arrow = "❄️"
        action = "بيع"
    
    # ملاحظة: TP و SL معكوسين في البيع لأن الهدف أقل من سعر الدخول
    if signal == "SELL":
        message = f"""{arrow} *{symbol}* {action} {entry:.2f}

🥇 TP1: {tp:.2f}
🛑 SL: {sl:.2f}

📊 عدم توازن: {imbalance:.2f}
💡 {reason}"""
    else:
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
                
                # التحقق من ضرب الهدف
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
                
                # التحقق من ضرب الستوب
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
# 8. الحلقة الرئيسية
# =============================================
async def main():
    global current_trade
    
    print(f"🚀 بوت السكالبنج - {SYMBOL}")
    print(f"🎯 الهدف: {TP_POINTS} نقطة | 🛑 الستوب: {SL_POINTS} نقطة")
    print("📌 ينتظر إشارات قوية (عدم توازن 65%+)\n")
    
    async with aiohttp.ClientSession() as session:
        await send_telegram_message(session, f"🤖 *بوت السكالبنج قيد التشغيل*\n📊 {SYMBOL}\n🎯 الهدف: {TP_POINTS} نقطة | 🛑 الستوب: {SL_POINTS} نقطة")
        
        asyncio.create_task(monitor_current_trade(session))
        
        last_signal_key = None
        
        while True:
            try:
                if current_trade["active"]:
                    await asyncio.sleep(2)
                    continue
                
                order_book = await fetch_order_book(session)
                if not order_book:
                    await asyncio.sleep(1)
                    continue
                
                current_price = await get_current_price(session)
                if not current_price:
                    await asyncio.sleep(1)
                    continue
                
                imbalance = calculate_imbalance(order_book)
                signal, sl, tp, reason = generate_signal(imbalance, current_price)
                
                # طباعة في الطرفية
                bids = order_book.get('bids', [])
                asks = order_book.get('asks', [])
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
                        await asyncio.sleep(3)
                else:
                    print(f"⚖️ عدم توازن {imbalance:.2f} - انتظار {IMBALANCE_THRESHOLD}+")
                
                await asyncio.sleep(2)
                
            except Exception as e:
                print(f"خطأ رئيسي: {e}")
                await asyncio.sleep(2)

# =============================================
# 9. التشغيل
# =============================================
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 تم إيقاف البوت")