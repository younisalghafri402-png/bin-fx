import asyncio
import aiohttp
from datetime import datetime
from aiohttp import web
import json
import os

# =============================================
# 1. إعدادات التحليل
# =============================================
SYMBOL = "XAUUSDT"
DEPTH_LIMIT = 10
IMBALANCE_THRESHOLD = 0.80

# =============================================
# 2. إعدادات الهدف والستوب
# =============================================
TP_POINTS = 100  # نقاط
SL_POINTS = 100

# =============================================
# 3. إعدادات تليجرام
# =============================================
TELEGRAM_BOT_TOKEN = "8292443875:AAHVG6THkf9zL2r-1B2DVUcUl4yfWXS52zg"
TELEGRAM_CHAT_ID = "-1003952441740"

# =============================================
# 4. حالة البوت والإحصائيات
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

# إحصائيات عامة
stats = {
    "total_trades": 0,      # إجمالي الصفقات التي دخلناها
    "wins": 0,              # ضرب الهدف
    "losses": 0,            # ضرب الستوب
    "last_signal_time": None,
    "current_imbalance": 0.5,
    "current_price": 0.0,
    "bid_volume": 0.0,      # إجمالي حجم المشترين (أول 10 مستويات)
    "ask_volume": 0.0,      # إجمالي حجم البائعين
}

# =============================================
# 5. جلب البيانات من Binance Futures
# =============================================
async def fetch_order_book(session):
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

def calculate_imbalance_and_volumes(order_book):
    """حساب عدم التوازن وحجم المشترين والبائعين"""
    bids = order_book.get('bids', [])
    asks = order_book.get('asks', [])
    total_bid = sum(float(b[1]) for b in bids[:5])
    total_ask = sum(float(a[1]) for a in asks[:5])
    total = total_bid + total_ask
    imbalance = total_bid / total if total > 0 else 0.5
    
    # حساب الحجم الإجمالي لأول DEPTH_LIMIT مستوى
    full_bid_vol = sum(float(b[1]) for b in bids[:DEPTH_LIMIT])
    full_ask_vol = sum(float(a[1]) for a in asks[:DEPTH_LIMIT])
    
    return imbalance, full_bid_vol, full_ask_vol

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
    arrow = "🔥" if signal == "BUY" else "❄️"
    action = "شراء" if signal == "BUY" else "بيع"
    return f"""{arrow} *{symbol}* {action} {entry:.2f}

🥇 TP1: {tp:.2f}
🛑 SL: {sl:.2f}

📊 عدم توازن: {imbalance:.2f}
💡 {reason}"""

# =============================================
# 7. مراقبة الصفقة وتحديث الإحصائيات
# =============================================
async def monitor_current_trade(session):
    global current_trade, stats
    while True:
        if current_trade["active"]:
            current_price = await get_current_price(session)
            if current_price:
                trade_type = current_trade["type"]
                tp = current_trade["tp"]
                sl = current_trade["sl"]
                
                # ضرب الهدف
                if trade_type == "BUY" and current_price >= tp and not current_trade["tp_hit"]:
                    current_trade["tp_hit"] = True
                    current_trade["active"] = False
                    stats["wins"] += 1
                    await send_telegram_message(session, f"✅ *TP1 {SYMBOL} DONE* 🥇")
                    print(f"🎯 ضرب الهدف عند {current_price}")
                elif trade_type == "SELL" and current_price <= tp and not current_trade["tp_hit"]:
                    current_trade["tp_hit"] = True
                    current_trade["active"] = False
                    stats["wins"] += 1
                    await send_telegram_message(session, f"✅ *TP1 {SYMBOL} DONE* 🥇")
                    print(f"🎯 ضرب الهدف عند {current_price}")
                
                # ضرب الستوب
                if trade_type == "BUY" and current_price <= sl and not current_trade["sl_hit"]:
                    current_trade["sl_hit"] = True
                    current_trade["active"] = False
                    stats["losses"] += 1
                    await send_telegram_message(session, f"🛑 *SL Hit {SYMBOL}*")
                    print(f"🛑 ضرب الستوب عند {current_price}")
                elif trade_type == "SELL" and current_price >= sl and not current_trade["sl_hit"]:
                    current_trade["sl_hit"] = True
                    current_trade["active"] = False
                    stats["losses"] += 1
                    await send_telegram_message(session, f"🛑 *SL Hit {SYMBOL}*")
                    print(f"🛑 ضرب الستوب عند {current_price}")
        await asyncio.sleep(1)

# =============================================
# 8. واجهة الويب (داشبورد وإحصائيات)
# =============================================
async def handle_index(request):
    """تقديم صفحة الويب الرئيسية"""
    html_content = """
<!DOCTYPE html>
<html>
<head>
    <title>Gold Scalping Bot Dashboard</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #1e1e2f; color: #f0f0f0; margin: 0; padding: 20px; }
        .container { max-width: 1200px; margin: auto; }
        h1 { text-align: center; color: #ffd966; }
        .card { background: #2d2d3a; border-radius: 12px; padding: 20px; margin: 15px 0; box-shadow: 0 4px 8px rgba(0,0,0,0.2); }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; }
        .stat-box { background: #3a3a4a; border-radius: 10px; padding: 15px; text-align: center; }
        .stat-value { font-size: 28px; font-weight: bold; color: #ffd966; }
        .stat-label { font-size: 14px; opacity: 0.8; }
        .trade-active { border-left: 5px solid #4caf50; background: #2a3a2a; }
        .trade-inactive { border-left: 5px solid #f44336; }
        .badge { display: inline-block; padding: 4px 8px; border-radius: 20px; font-size: 12px; font-weight: bold; }
        .badge-buy { background: #4caf50; color: white; }
        .badge-sell { background: #f44336; color: white; }
        footer { text-align: center; margin-top: 30px; font-size: 12px; opacity: 0.6; }
    </style>
</head>
<body>
<div class="container">
    <h1>🤖 XAUUSD Scalping Bot</h1>
    <div class="card">
        <div class="stats-grid">
            <div class="stat-box"><div class="stat-value" id="price">--</div><div class="stat-label">السعر الحالي</div></div>
            <div class="stat-box"><div class="stat-value" id="imbalance">--</div><div class="stat-label">عدم التوازن (OBI)</div></div>
            <div class="stat-box"><div class="stat-value" id="bidVol">--</div><div class="stat-label">حجم المشترين</div></div>
            <div class="stat-box"><div class="stat-value" id="askVol">--</div><div class="stat-label">حجم البائعين</div></div>
        </div>
    </div>
    <div class="card">
        <h3>📊 إحصائيات الصفقات</h3>
        <div class="stats-grid">
            <div class="stat-box"><div class="stat-value" id="totalTrades">0</div><div class="stat-label">إجمالي الصفقات</div></div>
            <div class="stat-box"><div class="stat-value" id="wins">0</div><div class="stat-label">صفقات رابحة</div></div>
            <div class="stat-box"><div class="stat-value" id="losses">0</div><div class="stat-label">صفقات خاسرة</div></div>
            <div class="stat-box"><div class="stat-value" id="winRate">0%</div><div class="stat-label">نسبة النجاح</div></div>
        </div>
    </div>
    <div class="card" id="tradeCard">
        <h3>🔄 الصفقة الحالية</h3>
        <div id="tradeStatus">لا توجد صفقة مفتوحة</div>
    </div>
    <footer>تحديث تلقائي كل 1 ثانية | البوت يعمل 24/7</footer>
</div>
<script>
    async function fetchStats() {
        try {
            const response = await fetch('/stats');
            const data = await response.json();
            document.getElementById('price').innerText = data.current_price.toFixed(2);
            document.getElementById('imbalance').innerText = (data.current_imbalance * 100).toFixed(1) + '%';
            document.getElementById('bidVol').innerText = data.bid_volume.toFixed(2);
            document.getElementById('askVol').innerText = data.ask_volume.toFixed(2);
            document.getElementById('totalTrades').innerText = data.total_trades;
            document.getElementById('wins').innerText = data.wins;
            document.getElementById('losses').innerText = data.losses;
            let winRate = data.total_trades > 0 ? (data.wins / data.total_trades * 100).toFixed(1) : 0;
            document.getElementById('winRate').innerText = winRate + '%';
            
            const tradeCard = document.getElementById('tradeCard');
            const tradeDiv = document.getElementById('tradeStatus');
            if (data.active_trade) {
                tradeCard.className = 'card trade-active';
                tradeDiv.innerHTML = `
                    <p><strong>نوع الصفقة:</strong> <span class="badge badge-${data.trade_type.toLowerCase()}">${data.trade_type}</span></p>
                    <p><strong>سعر الدخول:</strong> ${data.entry_price}</p>
                    <p><strong>الهدف (TP):</strong> ${data.tp}</p>
                    <p><strong>الستوب (SL):</strong> ${data.sl}</p>
                `;
            } else {
                tradeCard.className = 'card trade-inactive';
                tradeDiv.innerHTML = '<p>🚫 لا توجد صفقة مفتوحة حاليًا</p>';
            }
        } catch(e) { console.error(e); }
    }
    fetchStats();
    setInterval(fetchStats, 1000);
</script>
</body>
</html>
    """
    return web.Response(text=html_content, content_type='text/html')

async def handle_stats(request):
    """إرجاع الإحصائيات بشكل JSON"""
    global current_trade, stats
    data = {
        "current_price": stats["current_price"],
        "current_imbalance": stats["current_imbalance"],
        "bid_volume": stats["bid_volume"],
        "ask_volume": stats["ask_volume"],
        "total_trades": stats["total_trades"],
        "wins": stats["wins"],
        "losses": stats["losses"],
        "active_trade": current_trade["active"],
        "trade_type": current_trade["type"],
        "entry_price": current_trade["entry_price"],
        "tp": current_trade["tp"],
        "sl": current_trade["sl"]
    }
    return web.json_response(data)

async def start_web_server():
    """تشغيل خادم الويب على المنفذ المحدد (بدون حظر)"""
    app = web.Application()
    app.router.add_get('/', handle_index)
    app.router.add_get('/stats', handle_stats)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"✅ واجهة الويب تعمل على http://0.0.0.0:{port}")

# =============================================
# 9. الحلقة الرئيسية للتداول
# =============================================
async def main():
    global current_trade, stats
    print(f"🚀 بوت السكالبنج - {SYMBOL}")
    print(f"🎯 الهدف: {TP_POINTS} نقطة | 🛑 الستوب: {SL_POINTS} نقطة")
    print("📌 ينتظر إشارات عند نهاية شمعة 1 دقيقة (عدم توازن 80%+)\n")
    
    async with aiohttp.ClientSession() as session:
        # إشعار بدء التشغيل في تليجرام
        await send_telegram_message(session, f"🤖 *بوت السكالبنج قيد التشغيل*\n📊 {SYMBOL}\n🎯 الهدف: {TP_POINTS} نقطة | 🛑 الستوب: {SL_POINTS} نقطة\n⏱️ الإشارات عند انتهاء شمعة 1 دقيقة")
        
        # تشغيل مهام الخلفية
        asyncio.create_task(monitor_current_trade(session))
        
        last_minute = None
        last_signal_key = None
        
        while True:
            try:
                # جلب البيانات الحالية للسوق
                order_book = await fetch_order_book(session)
                current_price = await get_current_price(session)
                
                if order_book is None or current_price is None:
                    await asyncio.sleep(0.5)
                    continue
                
                # تحديث الإحصائيات الحية
                imbalance, bid_vol, ask_vol = calculate_imbalance_and_volumes(order_book)
                stats["current_price"] = current_price
                stats["current_imbalance"] = imbalance
                stats["bid_volume"] = bid_vol
                stats["ask_volume"] = ask_vol
                
                now = datetime.now()
                current_minute = now.replace(second=0, microsecond=0)
                
                # عرض في الطرفية
                print(f"\r⏰ {now.strftime('%H:%M:%S')} | السعر: {current_price:.2f} | OBI: {imbalance:.2f}", end="")
                
                # عند انتهاء شمعة الدقيقة
                if last_minute is not None and current_minute != last_minute and not current_trade["active"]:
                    print(f"\n🔔 نهاية الشمعة {last_minute.strftime('%H:%M')} - التحقق من الإشارة...")
                    # جلب بيانات نهائية للشمعة
                    final_book = await fetch_order_book(session)
                    final_price = await get_current_price(session)
                    if final_book and final_price:
                        final_imbalance, _, _ = calculate_imbalance_and_volumes(final_book)
                        signal, sl, tp, reason = generate_signal(final_imbalance, final_price)
                        if signal:
                            current_key = (signal, round(final_price, 1))
                            if current_key != last_signal_key:
                                # تحديث الإحصائيات
                                stats["total_trades"] += 1
                                stats["last_signal_time"] = now.isoformat()
                                # فتح الصفقة
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
                                # إرسال إلى تليجرام
                                msg = format_signal_message(SYMBOL, signal, final_price, sl, tp, reason, final_imbalance)
                                await send_telegram_message(session, msg)
                                print(f"\n🔔 إشارة {signal} | الدخل: {final_price:.2f} | TP: {tp:.2f} | SL: {sl:.2f}")
                        else:
                            print(f"⚖️ عدم توازن {final_imbalance:.2f} - لم يتجاوز العتبة {IMBALANCE_THRESHOLD}")
                
                last_minute = current_minute
                await asyncio.sleep(0.5)  # تحديث مرتين في الثانية
                
            except Exception as e:
                print(f"\n⚠️ خطأ رئيسي: {e}")
                await asyncio.sleep(2)

# =============================================
# 10. التشغيل المتزامن (التداول + الويب)
# =============================================
async def main_with_web():
    # تشغيل خادم الويب في مهمة خلفية
    web_task = asyncio.create_task(start_web_server())
    # تشغيل بوت التداول
    await main()

if __name__ == "__main__":
    try:
        asyncio.run(main_with_web())
    except KeyboardInterrupt:
        print("\n🛑 تم إيقاف البوت")