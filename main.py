"""
╔══════════════════════════════════════════════════════════╗
║         بوت السكالبنج — XAUUSDT / Binance Futures       ║
║  ملف واحد: بوت + واجهة ويب + تليجرام + منع النوم 24/7   ║
║  مع HTTP polling بديل WebSocket للعمل على Render        ║
║  تشغيل: python bot.py                                    ║
╚══════════════════════════════════════════════════════════╝
"""

import asyncio
import time
import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

import aiohttp
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import uvicorn

# ══════════════════════════════════════════════════════════
#  الإعدادات
# ══════════════════════════════════════════════════════════
SYMBOL = os.getenv("SYMBOL", "XAUUSDT")
DEPTH_LIMIT = 10
IMBALANCE_THRESHOLD = float(os.getenv("IMBALANCE_THRESHOLD", "0.70"))
TP_POINTS = int(os.getenv("TP_POINTS", "50"))
SL_POINTS = int(os.getenv("SL_POINTS", "50"))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8292443875:AAHVG6THkf9zL2r-1B2DVUcUl4yfWXS52zg")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1003952441740")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "")
PORT = int(os.getenv("PORT", "8000"))

# ══════════════════════════════════════════════════════════
#  حالة البوت
# ══════════════════════════════════════════════════════════
bot_running = False
bot_task: Optional[asyncio.Task] = None

current_trade: Dict[str, Any] = {
    "active": False,
    "type": None,
    "entry_price": 0,
    "tp": 0,
    "sl": 0,
    "tp_hit": False,
    "sl_hit": False,
}

signals_history: List[Dict] = []

live: Dict[str, Any] = {
    "price": 0.0,
    "imbalance": 0.5,
    "timestamp": "",
    "status": "stopped",
    "trade": {},
}

clients: List[WebSocket] = []

# ══════════════════════════════════════════════════════════
#  WebSocket - بث للجميع (يعمل في حالة وجود اتصال)
# ══════════════════════════════════════════════════════════
async def broadcast(data: dict):
    dead = []
    for ws in clients:
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in clients:
            clients.remove(ws)

# ══════════════════════════════════════════════════════════
#  Binance API
# ══════════════════════════════════════════════════════════
async def fetch_price(session: aiohttp.ClientSession) -> Optional[float]:
    url = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={SYMBOL}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
            if r.status == 200:
                return float((await r.json())["price"])
    except Exception:
        pass
    return None

async def fetch_order_book(session: aiohttp.ClientSession) -> Optional[dict]:
    url = f"https://fapi.binance.com/fapi/v1/depth?symbol={SYMBOL}&limit={DEPTH_LIMIT}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
            if r.status == 200:
                return await r.json()
    except Exception:
        pass
    return None

# ══════════════════════════════════════════════════════════
#  منطق الإشارات
# ══════════════════════════════════════════════════════════
def calc_imbalance(ob: dict) -> float:
    bids = sum(float(b[1]) for b in ob.get("bids", [])[:5])
    asks = sum(float(a[1]) for a in ob.get("asks", [])[:5])
    total = bids + asks
    return bids / total if total > 0 else 0.5

def calc_sltp(signal: str, price: float):
    if signal == "BUY":
        return round(price - SL_POINTS * 0.01, 2), round(price + TP_POINTS * 0.01, 2)
    return round(price + SL_POINTS * 0.01, 2), round(price - TP_POINTS * 0.01, 2)

def get_signal(imbalance: float, price: float):
    if imbalance > IMBALANCE_THRESHOLD:
        sl, tp = calc_sltp("BUY", price)
        return "BUY", sl, tp, f"طلب مرتفع {imbalance * 100:.0f}%"
    if imbalance < (1 - IMBALANCE_THRESHOLD):
        sl, tp = calc_sltp("SELL", price)
        return "SELL", sl, tp, f"عرض مرتفع {(1 - imbalance) * 100:.0f}%"
    return None, None, None, None

# ══════════════════════════════════════════════════════════
#  تليجرام
# ══════════════════════════════════════════════════════════
async def tg(session: aiohttp.ClientSession, msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        async with session.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=aiohttp.ClientTimeout(total=8),
        ):
            print("✅ تم إرسال إشعار تليجرام")
    except Exception as e:
        print(f"⚠️ تليجرام خطأ: {e}")

# ══════════════════════════════════════════════════════════
#  مراقبة الصفقة (TP / SL) مع رسائل واضحة
# ══════════════════════════════════════════════════════════
async def watch_trade(session: aiohttp.ClientSession):
    global current_trade
    while bot_running:
        if current_trade["active"]:
            price = await fetch_price(session)
            if price:
                t = current_trade
                hit_tp = (t["type"] == "BUY" and price >= t["tp"]) or \
                         (t["type"] == "SELL" and price <= t["tp"])
                hit_sl = (t["type"] == "BUY" and price <= t["sl"]) or \
                         (t["type"] == "SELL" and price >= t["sl"])
                
                if hit_tp and not t["tp_hit"]:
                    current_trade.update({"tp_hit": True, "active": False})
                    # حساب الربح
                    if t["type"] == "BUY":
                        profit = (price - t["entry_price"]) / 0.01
                    else:
                        profit = (t["entry_price"] - price) / 0.01
                    
                    msg = f"✅ *ضرب الهدف - {SYMBOL}*\n"
                    msg += f"💰 السعر: {price:.2f}\n"
                    msg += f"🎯 الربح: {profit:.0f} نقطة"
                    
                    await tg(session, msg)
                    await broadcast({"type": "trade_closed", "result": "TP", "price": price, "profit": profit})
                    print(f"✅ TP hit @ {price:.2f} | ربح: {profit:.0f} نقطة")

                elif hit_sl and not t["sl_hit"]:
                    current_trade.update({"sl_hit": True, "active": False})
                    # حساب الخسارة
                    if t["type"] == "BUY":
                        loss = (t["entry_price"] - price) / 0.01
                    else:
                        loss = (price - t["entry_price"]) / 0.01
                    
                    msg = f"❌ *ضرب الستوب - {SYMBOL}*\n"
                    msg += f"💸 السعر: {price:.2f}\n"
                    msg += f"📉 الخسارة: {loss:.0f} نقطة"
                    
                    await tg(session, msg)
                    await broadcast({"type": "trade_closed", "result": "SL", "price": price, "loss": loss})
                    print(f"🛑 SL hit @ {price:.2f} | خسارة: {loss:.0f} نقطة")

        await asyncio.sleep(0.5)

# ══════════════════════════════════════════════════════════
#  Keep-alive
# ══════════════════════════════════════════════════════════
async def keep_alive():
    target = RENDER_URL or f"http://localhost:{PORT}"
    await asyncio.sleep(30)
    async with aiohttp.ClientSession() as s:
        while True:
            try:
                async with s.get(f"{target}/status", timeout=aiohttp.ClientTimeout(total=15)):
                    print(f"✅ Keep-alive ping → {target}/status")
            except Exception as e:
                print(f"⚠️ Keep-alive خطأ: {e}")
            await asyncio.sleep(14 * 60)

# ══════════════════════════════════════════════════════════
#  تحديث الأسعار المستمر (يعمل حتى لو البوت متوقف)
# ══════════════════════════════════════════════════════════
async def price_ticker():
    print("🔄 تشغيل تحديث الأسعار المستمر...")
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                price = await fetch_price(session)
                ob = await fetch_order_book(session)
                if price and ob:
                    imbalance = calc_imbalance(ob)
                    live.update({
                        "price": price,
                        "imbalance": round(imbalance, 4),
                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                        "status": "running" if bot_running else "stopped",
                        "trade": current_trade.copy(),
                    })
                    await broadcast({"type": "tick", **live})
                    # طباعة في الكونسول كل 10 ثواني
                    if int(time.time()) % 10 == 0:
                        print(f"📊 السعر: {price:.2f} | OBI: {imbalance:.3f}")
            except Exception as e:
                print(f"⚠️ خطأ في تحديث الأسعار: {e}")
            await asyncio.sleep(0.35)

# ══════════════════════════════════════════════════════════
#  الحلقة الرئيسية للبوت
# ══════════════════════════════════════════════════════════
async def run_bot():
    global bot_running, current_trade, live, signals_history
    print(f"🚀 البوت يعمل — {SYMBOL}")
    async with aiohttp.ClientSession() as session:
        await tg(session, f"🤖 *بوت السكالبنج قيد التشغيل*\n📊 {SYMBOL}\n🎯 الهدف: {TP_POINTS} نقطة\n🛑 الستوب: {SL_POINTS} نقطة")
        asyncio.create_task(watch_trade(session))
        while bot_running:
            try:
                price = live.get("price", 0)
                imbalance = live.get("imbalance", 0.5)
                if price == 0:
                    await asyncio.sleep(0.35)
                    continue
                now = datetime.now()
                if not current_trade["active"]:
                    signal, sl, tp, reason = get_signal(imbalance, price)
                    if signal:
                        action = "شراء" if signal == "BUY" else "بيع"
                        arrow = "🔥" if signal == "BUY" else "❄️"
                        
                        msg = f"{arrow} *إشارة {action} - {SYMBOL}*\n"
                        msg += f"💰 سعر الدخول: {price:.2f}\n"
                        msg += f"🎯 الهدف: {tp:.2f} (+{TP_POINTS} نقطة)\n"
                        msg += f"🛑 الستوب: {sl:.2f} (-{SL_POINTS} نقطة)\n"
                        msg += f"📊 OBI: {imbalance:.2f}\n"
                        msg += f"💡 {reason}"
                        
                        await tg(session, msg)
                        entry = {
                            "id": len(signals_history) + 1,
                            "type": signal,
                            "entry": price,
                            "tp": tp,
                            "sl": sl,
                            "imbalance": round(imbalance, 4),
                            "reason": reason,
                            "time": now.strftime("%H:%M:%S"),
                            "date": now.strftime("%Y-%m-%d"),
                        }
                        signals_history.insert(0, entry)
                        if len(signals_history) > 50:
                            signals_history.pop()
                        current_trade.update({
                            "active": True,
                            "type": signal,
                            "entry_price": price,
                            "tp": tp,
                            "sl": sl,
                            "tp_hit": False,
                            "sl_hit": False,
                        })
                        await broadcast({"type": "signal", **entry})
                        print(f"📡 إشارة {signal} @ {price:.2f} | OBI: {imbalance:.2f}")
                await asyncio.sleep(0.35)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ خطأ في الحلقة: {e}")
                await asyncio.sleep(2)
    live["status"] = "stopped"
    await broadcast({"type": "status_change", "status": "stopped"})
    print("🛑 البوت متوقف")

# ══════════════════════════════════════════════════════════
#  التشغيل التلقائي
# ══════════════════════════════════════════════════════════
async def auto_start_bot():
    global bot_running, bot_task
    await asyncio.sleep(3)
    if not bot_running:
        bot_running = True
        live["status"] = "running"
        bot_task = asyncio.create_task(run_bot())
        print("🤖 البوت بدأ تلقائياً!")

# ══════════════════════════════════════════════════════════
#  واجهة الويب - مع HTTP polling بديل WebSocket
# ══════════════════════════════════════════════════════════
HTML_CONTENT = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>بوت السكالبنج - XAUUSDT</title>
<style>
    * {
        margin: 0;
        padding: 0;
        box-sizing: border-box;
    }
    body {
        background: linear-gradient(135deg, #0a0e1a 0%, #0f1420 100%);
        color: #e2e8f0;
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        min-height: 100vh;
        padding: 20px;
    }
    .container {
        max-width: 1300px;
        margin: 0 auto;
    }
    h1 {
        text-align: center;
        margin-bottom: 30px;
        font-size: 2rem;
        background: linear-gradient(135deg, #f59e0b, #e2e8f0);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }
    .grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
        gap: 20px;
        margin-bottom: 20px;
    }
    .card {
        background: rgba(17, 24, 39, 0.9);
        backdrop-filter: blur(10px);
        border-radius: 20px;
        padding: 25px;
        border: 1px solid rgba(31, 45, 69, 0.5);
        transition: all 0.3s ease;
    }
    .card:hover {
        border-color: #f59e0b;
        transform: translateY(-2px);
    }
    .card-title {
        font-size: 0.85rem;
        text-transform: uppercase;
        letter-spacing: 2px;
        color: #64748b;
        margin-bottom: 10px;
    }
    .price {
        font-size: 3.5rem;
        font-weight: 700;
        color: #f59e0b;
        line-height: 1;
    }
    .obi {
        font-size: 2.5rem;
        font-weight: 700;
        color: #3b82f6;
    }
    .bar-container {
        background: #1e293b;
        border-radius: 10px;
        height: 12px;
        margin-top: 15px;
        overflow: hidden;
    }
    .bar-fill {
        height: 100%;
        border-radius: 10px;
        transition: width 0.2s ease;
    }
    .status {
        display: inline-block;
        padding: 6px 15px;
        border-radius: 20px;
        font-size: 0.85rem;
        font-weight: 600;
    }
    .status-running {
        background: #064e3b;
        color: #10b981;
        border: 1px solid #10b981;
    }
    .status-stopped {
        background: #450a0a;
        color: #ef4444;
        border: 1px solid #ef4444;
    }
    .btn {
        border: none;
        padding: 12px 30px;
        border-radius: 10px;
        font-size: 1rem;
        font-weight: 600;
        cursor: pointer;
        transition: all 0.2s;
        margin: 5px;
    }
    .btn-start {
        background: #10b981;
        color: white;
    }
    .btn-start:hover {
        background: #059669;
        transform: scale(1.02);
    }
    .btn-stop {
        background: #ef4444;
        color: white;
    }
    .btn-stop:hover {
        background: #dc2626;
        transform: scale(1.02);
    }
    .btn:disabled {
        opacity: 0.5;
        cursor: not-allowed;
    }
    .trade-info {
        margin-top: 15px;
        padding-top: 15px;
        border-top: 1px solid #1f2d45;
    }
    .trade-row {
        display: flex;
        justify-content: space-between;
        padding: 8px 0;
    }
    .trade-label {
        color: #64748b;
        font-size: 0.85rem;
    }
    .trade-value {
        font-weight: 600;
    }
    .trade-value.tp {
        color: #10b981;
    }
    .trade-value.sl {
        color: #ef4444;
    }
    .timestamp {
        font-size: 0.8rem;
        color: #64748b;
        text-align: center;
        margin-top: 20px;
    }
    .signals-table {
        background: rgba(17, 24, 39, 0.9);
        border-radius: 20px;
        padding: 20px;
        margin-top: 20px;
    }
    .signals-table h3 {
        margin-bottom: 15px;
        color: #f59e0b;
    }
    table {
        width: 100%;
        border-collapse: collapse;
    }
    th, td {
        padding: 12px;
        text-align: right;
        border-bottom: 1px solid #1f2d45;
    }
    th {
        color: #64748b;
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 1px;
    }
    .signal-buy {
        color: #10b981;
        font-weight: 600;
    }
    .signal-sell {
        color: #ef4444;
        font-weight: 600;
    }
    .last-update {
        font-size: 0.7rem;
        color: #64748b;
        text-align: center;
        margin-top: 15px;
    }
    @media (max-width: 768px) {
        .price { font-size: 2rem; }
        .obi { font-size: 1.5rem; }
        .grid { grid-template-columns: 1fr; }
    }
</style>
</head>
<body>
<div class="container">
    <h1>🤖 بوت السكالبنج - XAUUSDT</h1>
    
    <div class="grid">
        <div class="card">
            <div class="card-title">💰 السعر الحالي</div>
            <div class="price" id="price">---</div>
            <div class="card-title" style="margin-top: 10px;">Binance Futures</div>
        </div>
        
        <div class="card">
            <div class="card-title">📊 عدم التوازن (OBI)</div>
            <div class="obi" id="obi">0.5000</div>
            <div class="bar-container">
                <div class="bar-fill" id="obibar" style="width: 50%; background: #3b82f6;"></div>
            </div>
        </div>
        
        <div class="card">
            <div class="card-title">⚡ حالة البوت</div>
            <div><span class="status status-stopped" id="status">متوقف</span></div>
            <div style="margin-top: 15px;">
                <button class="btn btn-start" id="startBtn">▶ تشغيل</button>
                <button class="btn btn-stop" id="stopBtn">⏹ إيقاف</button>
            </div>
        </div>
    </div>
    
    <div class="grid">
        <div class="card">
            <div class="card-title">📈 الصفقة الحالية</div>
            <div id="tradeStatus">في انتظار إشارة</div>
            <div class="trade-info">
                <div class="trade-row">
                    <span class="trade-label">سعر الدخول:</span>
                    <span class="trade-value" id="entryPrice">---</span>
                </div>
                <div class="trade-row">
                    <span class="trade-label">الهدف (TP):</span>
                    <span class="trade-value tp" id="tp">---</span>
                </div>
                <div class="trade-row">
                    <span class="trade-label">الستوب (SL):</span>
                    <span class="trade-value sl" id="sl">---</span>
                </div>
            </div>
        </div>
        
        <div class="card">
            <div class="card-title">⚙️ الإعدادات</div>
            <div class="trade-row">
                <span class="trade-label">عتبة OBI:</span>
                <span class="trade-value">70%</span>
            </div>
            <div class="trade-row">
                <span class="trade-label">الهدف:</span>
                <span class="trade-value tp">50 نقطة</span>
            </div>
            <div class="trade-row">
                <span class="trade-label">الستوب:</span>
                <span class="trade-value sl">50 نقطة</span>
            </div>
            <div class="trade-row">
                <span class="trade-label">سرعة التحديث:</span>
                <span class="trade-value">~3x/ثانية</span>
            </div>
        </div>
    </div>
    
    <div class="signals-table">
        <h3>📋 سجل الإشارات</h3>
        <div style="overflow-x: auto;">
            <table>
                <thead>
                    <tr>
                        <th>#</th><th>الوقت</th><th>النوع</th><th>الدخول</th><th>TP</th><th>SL</th><th>OBI</th>
                    </tr>
                </thead>
                <tbody id="signalsBody">
                    <tr><td colspan="7" style="text-align: center;">لا توجد إشارات بعد</td></tr>
                </tbody>
            </table>
        </div>
    </div>
    
    <div class="timestamp" id="timestamp">آخر تحديث: --:--:--</div>
    <div class="last-update" id="lastPoll">جاري التحديث...</div>
</div>

<script>
let signals = [];
let pollInterval;

// دالة جلب البيانات عبر HTTP polling
async function fetchData() {
    try {
        const response = await fetch('/api/live');
        const data = await response.json();
        
        // تحديث السعر
        if (data.price) {
            document.getElementById('price').innerText = data.price.toFixed(2);
        }
        
        // تحديث OBI
        if (data.imbalance !== undefined) {
            const obi = data.imbalance;
            const percent = obi * 100;
            document.getElementById('obi').innerText = obi.toFixed(4);
            const bar = document.getElementById('obibar');
            bar.style.width = percent + '%';
            if (percent > 70) bar.style.background = '#10b981';
            else if (percent < 30) bar.style.background = '#ef4444';
            else bar.style.background = '#3b82f6';
        }
        
        // تحديث الوقت
        if (data.timestamp) {
            document.getElementById('timestamp').innerText = 'آخر تحديث: ' + data.timestamp;
        }
        document.getElementById('lastPoll').innerHTML = '🔄 تحديث تلقائي كل ثانية - ' + new Date().toLocaleTimeString();
        
        // تحديث الصفقة الحالية
        if (data.trade) {
            if (data.trade.active) {
                document.getElementById('tradeStatus').innerHTML = data.trade.type === 'BUY' ? '🔴 صفقة شراء نشطة' : '🔵 صفقة بيع نشطة';
                document.getElementById('entryPrice').innerHTML = data.trade.entry_price?.toFixed(2) || '---';
                document.getElementById('tp').innerHTML = data.trade.tp?.toFixed(2) || '---';
                document.getElementById('sl').innerHTML = data.trade.sl?.toFixed(2) || '---';
            } else {
                document.getElementById('tradeStatus').innerHTML = 'في انتظار إشارة';
                document.getElementById('entryPrice').innerHTML = '---';
                document.getElementById('tp').innerHTML = '---';
                document.getElementById('sl').innerHTML = '---';
            }
        }
        
        // تحديث حالة البوت
        if (data.status === 'running') {
            document.getElementById('status').innerText = 'يعمل';
            document.getElementById('status').className = 'status status-running';
            document.getElementById('startBtn').disabled = true;
            document.getElementById('stopBtn').disabled = false;
        } else {
            document.getElementById('status').innerText = 'متوقف';
            document.getElementById('status').className = 'status status-stopped';
            document.getElementById('startBtn').disabled = false;
            document.getElementById('stopBtn').disabled = true;
        }
        
    } catch (error) {
        console.error('خطأ في جلب البيانات:', error);
        document.getElementById('lastPoll').innerHTML = '⚠️ خطأ في الاتصال - إعادة المحاولة...';
    }
}

// جلب سجل الإشارات
async function fetchSignals() {
    try {
        const response = await fetch('/signals');
        const data = await response.json();
        if (Array.isArray(data)) {
            signals = data;
            updateSignalsTable();
        }
    } catch (error) {
        console.error('خطأ في جلب الإشارات:', error);
    }
}

function updateSignalsTable() {
    const tbody = document.getElementById('signalsBody');
    if (signals.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align: center;">لا توجد إشارات بعد</td></tr>';
        return;
    }
    tbody.innerHTML = signals.slice(0, 30).map(s => `
        <tr>
            <td>${s.id}</td>
            <td>${s.time}</td>
            <td class="${s.type === 'BUY' ? 'signal-buy' : 'signal-sell'}">${s.type === 'BUY' ? 'شراء 🔥' : 'بيع ❄️'}</td>
            <td>${s.entry.toFixed(2)}</td>
            <td class="signal-buy">${s.tp.toFixed(2)}</td>
            <td class="signal-sell">${s.sl.toFixed(2)}</td>
            <td>${(s.imbalance * 100).toFixed(1)}%</td>
        </tr>
    `).join('');
}

// التحكم في البوت
document.getElementById('startBtn').onclick = async () => {
    await fetch('/bot/start', { method: 'POST' });
    setTimeout(fetchData, 500);
};

document.getElementById('stopBtn').onclick = async () => {
    await fetch('/bot/stop', { method: 'POST' });
    setTimeout(fetchData, 500);
};

// بدء التحديث الدوري
fetchData();
fetchSignals();
pollInterval = setInterval(() => {
    fetchData();
    fetchSignals();
}, 1000);
</script>
</body>
</html>"""

# ══════════════════════════════════════════════════════════
#  FastAPI
# ══════════════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 بدء تشغيل الخادم...")
    asyncio.create_task(price_ticker())
    asyncio.create_task(keep_alive())
    asyncio.create_task(auto_start_bot())
    yield
    print("🛑 إيقاف الخادم")

app = FastAPI(title="بوت السكالبنج", lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return HTML_CONTENT

@app.get("/status")
async def status():
    return {
        "running": bot_running,
        "symbol": SYMBOL,
        "price": live["price"],
        "imbalance": live["imbalance"],
        "timestamp": live["timestamp"],
    }

@app.get("/api/live")
async def get_live():
    """API بديل لسحب البيانات - يعمل بدون WebSocket"""
    return {
        "price": live["price"],
        "imbalance": live["imbalance"],
        "timestamp": live["timestamp"],
        "status": live["status"],
        "trade": current_trade.copy()
    }

@app.get("/signals")
async def get_signals():
    return signals_history

@app.post("/bot/start")
async def start():
    global bot_running, bot_task
    if not bot_running:
        bot_running = True
        live["status"] = "running"
        bot_task = asyncio.create_task(run_bot())
        await broadcast({"type": "status_change", "status": "running"})
    return {"ok": True}

@app.post("/bot/stop")
async def stop():
    global bot_running, bot_task
    bot_running = False
    if bot_task:
        bot_task.cancel()
        bot_task = None
    live["status"] = "stopped"
    await broadcast({"type": "status_change", "status": "stopped"})
    return {"ok": True}

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    clients.append(ws)
    print(f"✅ WebSocket متصل - العملاء: {len(clients)}")
    try:
        await ws.send_json({"type": "tick", **live})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        print(f"❌ WebSocket قطع الاتصال")
    finally:
        if ws in clients:
            clients.remove(ws)
            print(f"📡 WebSocket removed - العملاء: {len(clients)}")

# ══════════════════════════════════════════════════════════
#  التشغيل
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("🤖 بوت السكالبنج - XAUUSDT (نسخة متطورة)")
    print(f"🌐 الداشبورد: http://localhost:{PORT}")
    print(f"🎯 الهدف: {TP_POINTS} نقطة | 🛑 الستوب: {SL_POINTS} نقطة")
    print(f"📊 عتبة OBI: {IMBALANCE_THRESHOLD * 100:.0f}%")
    print(f"⚡ تحديث الأسعار كل 0.35 ثانية")
    print(f"🌐 واجهة الويب: HTTP polling كل 1 ثانية (بديل WebSocket)")
    print(f"🔔 تليجرام: {'✅ مفعّل' if TELEGRAM_TOKEN else '❌ غير مضبوط'}")
    print(f"🔄 البوت يبدأ تلقائياً")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=PORT)