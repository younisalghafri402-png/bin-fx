
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

# Settings
SYMBOL = os.getenv("SYMBOL", "XAUUSDT")
DEPTH_LIMIT = 10
IMBALANCE_THRESHOLD = float(os.getenv("IMBALANCE_THRESHOLD", "0.80"))
TP_POINTS = int(os.getenv("TP_POINTS", "100"))
SL_POINTS = int(os.getenv("SL_POINTS", "100"))
SIGNAL_COOLDOWN = float(os.getenv("SIGNAL_COOLDOWN", "60"))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8292443875:AAHVG6THkf9zL2r-1B2DVUcUl4yfWXS52zg")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1003952441740")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "")
PORT = int(os.getenv("PORT", "8000"))

# State
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

async def tg(session: aiohttp.ClientSession, msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        async with session.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=aiohttp.ClientTimeout(total=8),
        ):
            print("Telegram sent")
    except Exception as e:
        print(f"Telegram error: {e}")

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
                    msg = f"✅ TP DONE {SYMBOL} price: {price:.2f}"
                    await tg(session, msg)
                    await broadcast({"type": "trade_closed", "result": "TP", "price": price})
                elif hit_sl and not t["sl_hit"]:
                    current_trade.update({"sl_hit": True, "active": False})
                    msg = f"🛑 SL Hit {SYMBOL} price: {price:.2f}"
                    await tg(session, msg)
                    await broadcast({"type": "trade_closed", "result": "SL", "price": price})
        await asyncio.sleep(0.5)

async def keep_alive():
    target = RENDER_URL or f"http://localhost:{PORT}"
    await asyncio.sleep(30)
    async with aiohttp.ClientSession() as s:
        while True:
            try:
                async with s.get(f"{target}/status", timeout=aiohttp.ClientTimeout(total=15)):
                    print(f"Keep-alive ping to {target}/status")
            except Exception as e:
                print(f"Keep-alive error: {e}")
            await asyncio.sleep(14 * 60)

async def run_bot():
    global bot_running, current_trade, live, signals_history
    print(f"Bot running {SYMBOL}")
    last_signal_time = 0.0
    async with aiohttp.ClientSession() as session:
        await tg(session, f"🤖 Bot started {SYMBOL} TP:{TP_POINTS} SL:{SL_POINTS}")
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
                    if time.time() - last_signal_time >= SIGNAL_COOLDOWN:
                        signal, sl, tp, reason = get_signal(imbalance, price)
                        if signal:
                            last_signal_time = time.time()
                            await tg(session, f"{signal} {SYMBOL} @ {price:.2f} TP:{tp:.2f} SL:{sl:.2f} OBI:{imbalance:.2f}")
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
                            print(f"Signal {signal} @ {price:.2f} OBI:{imbalance:.2f}")
                await asyncio.sleep(0.35)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Loop error: {e}")
                await asyncio.sleep(2)
    live["status"] = "stopped"
    await broadcast({"type": "status_change", "status": "stopped"})

async def price_ticker():
    print("Price ticker started")
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
            except Exception as e:
                print(f"Price ticker error: {e}")
            await asyncio.sleep(0.35)

async def auto_start_bot():
    global bot_running, bot_task
    await asyncio.sleep(3)
    if not bot_running:
        bot_running = True
        live["status"] = "running"
        bot_task = asyncio.create_task(run_bot())
        print("Bot auto-started")

HTML_CONTENT = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>بوت السكالبنج XAUUSDT</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0e1a;color:#e2e8f0;font-family:'Segoe UI',system-ui,sans-serif;padding:20px}
.container{max-width:1200px;margin:0 auto}
.card{background:#111827;border-radius:14px;padding:20px;margin-bottom:16px;border:1px solid #1f2d45}
.price{font-size:48px;color:#f59e0b;font-weight:700}
.obi{font-size:32px;color:#3b82f6;font-weight:700}
.bar-bg{height:10px;background:#1f2d45;border-radius:10px;margin-top:10px;overflow:hidden}
.bar-fill{height:100%;width:50%;border-radius:10px;transition:width 0.3s}
.btn{background:#10b981;border:none;padding:10px 24px;border-radius:8px;color:#fff;font-weight:700;cursor:pointer;margin:5px;font-size:16px}
.btn-stop{background:#ef4444}
.status{display:inline-block;padding:4px 12px;border-radius:20px;font-size:14px;font-weight:600}
.status-running{background:#064e3b;color:#10b981}
.status-stopped{background:#450a0a;color:#ef4444}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}
</style>
</head>
<body>
<div class="container">
<h1>🤖 بوت السكالبنج - XAUUSDT</h1>
<div class="grid">
<div class="card">
<div>السعر الحالي</div>
<div class="price" id="price">---</div>
<div>XAUUSDT Binance Futures</div>
</div>
<div class="card">
<div>عدم التوازن (OBI)</div>
<div class="obi" id="obi">0.5000</div>
<div class="bar-bg"><div class="bar-fill" id="obibar" style="width:50%;background:#3b82f6"></div></div>
</div>
</div>
<div class="card">
<div>حالة البوت</div>
<div><span id="status" class="status status-stopped">متوقف</span></div>
<div style="margin-top:15px">
<button class="btn" id="startBtn">▶ تشغيل</button>
<button class="btn btn-stop" id="stopBtn">⏹ إيقاف</button>
</div>
</div>
<div class="card">
<div>📊 آخر تحديث</div>
<div id="timestamp" style="color:#64748b">--:--:--</div>
</div>
</div>
<script>
let ws;
function connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onmessage = (e) => {
        const d = JSON.parse(e.data);
        if (d.type === 'tick') {
            if (d.price) document.getElementById('price').innerText = d.price.toFixed(2);
            if (d.imbalance) {
                const p = d.imbalance * 100;
                document.getElementById('obi').innerText = d.imbalance.toFixed(4);
                document.getElementById('obibar').style.width = p + '%';
                const bar = document.getElementById('obibar');
                if (p > 80) bar.style.background = '#10b981';
                else if (p < 20) bar.style.background = '#ef4444';
                else bar.style.background = '#3b82f6';
            }
            if (d.timestamp) document.getElementById('timestamp').innerText = d.timestamp;
            if (d.status) {
                const statusEl = document.getElementById('status');
                statusEl.innerText = d.status === 'running' ? 'يعمل' : 'متوقف';
                statusEl.className = 'status ' + (d.status === 'running' ? 'status-running' : 'status-stopped');
            }
        }
    };
    ws.onclose = () => setTimeout(connect, 3000);
}
connect();
document.getElementById('startBtn').onclick = async () => {
    await fetch('/bot/start', {method: 'POST'});
};
document.getElementById('stopBtn').onclick = async () => {
    await fetch('/bot/stop', {method: 'POST'});
};
</script>
</body>
</html>"""

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Starting server...")
    asyncio.create_task(price_ticker())
    asyncio.create_task(keep_alive())
    asyncio.create_task(auto_start_bot())
    yield
    print("Shutting down...")

app = FastAPI(title="Scalping Bot", lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return HTML_CONTENT

@app.get("/status")
async def status():
    return {"running": bot_running, "price": live["price"], "imbalance": live["imbalance"]}

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
    try:
        await ws.send_json({"type": "tick", **live})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if ws in clients:
            clients.remove(ws)

if __name__ == "__main__":
    print("=" * 50)
    print("XAUUSDT Scalping Bot")
    print(f"Dashboard: http://localhost:{PORT}")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=PORT)

