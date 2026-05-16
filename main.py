import asyncio
import os
from datetime import datetime
from typing import Optional, Dict, Any
from contextlib import asynccontextmanager

import aiohttp
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

# إعدادات
SYMBOL = os.getenv("SYMBOL", "XAUUSDT")
DEPTH_LIMIT = 10
IMBALANCE_THRESHOLD = float(os.getenv("IMBALANCE_THRESHOLD", "0.70"))
TP_POINTS = int(os.getenv("TP_POINTS", "50"))
SL_POINTS = int(os.getenv("SL_POINTS", "50"))
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "")
PORT = int(os.getenv("PORT", "8000"))

bot_running = False
current_trade: Dict[str, Any] = {
    "active": False,
    "type": None,
    "entry_price": 0,
    "tp": 0,
    "sl": 0,
    "tp_hit": False,
    "sl_hit": False,
}

live: Dict[str, Any] = {
    "price": 0.0,
    "imbalance": 0.5,
    "timestamp": "",
    "status": "stopped",
    "trade": {},
}

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

async def price_ticker():
    async with aiohttp.ClientSession() as session:
        while True:
            price = await fetch_price(session)
            ob = await fetch_order_book(session)
            if price and ob:
                imbalance = calc_imbalance(ob)
                live.update({
                    "price": price,
                    "imbalance": round(imbalance, 4),
                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                    "status": "running" if bot_running else "stopped",
                })
            await asyncio.sleep(1)

def calc_imbalance(ob: dict) -> float:
    bids = sum(float(b[1]) for b in ob.get("bids", [])[:5])
    asks = sum(float(a[1]) for a in ob.get("asks", [])[:5])
    total = bids + asks
    return bids / total if total > 0 else 0.5

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(price_ticker())
    yield

app = FastAPI(title="بوت السكالبنج", lifespan=lifespan)

HTML_CONTENT = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>بوت السكالبنج - XAUUSDT</title>
<style>
    body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
    .container { max-width: 600px; margin: 0 auto; padding: 20px; }
    h1 { text-align: center; }
    .status { display: flex; justify-content: space-between; margin: 10px 0; }
</style>
</head>
<body>
<div class="container">
    <h1>🤖 بوت السكالبنج - XAUUSDT</h1>
    <div class="status">
        <div>السعر الحالي: <span id="price">---</span></div>
        <div>الحالة: <span id="status">متوقف</span></div>
    </div>
    <button onclick="startBot()">تشغيل البوت</button>
    <button onclick="stopBot()">إيقاف البوت</button>
    <div id="imbalance">عدم التوازن: <span>0.5000</span></div>
</div>
<script>
async function fetchData() {
    const response = await fetch('/api/live');
    const data = await response.json();
    document.getElementById('price').innerText = data.price.toFixed(2);
    document.getElementById('imbalance').innerText = data.imbalance.toFixed(4);
    document.getElementById('status').innerText = data.status === 'running' ? 'يعمل' : 'متوقف';
}

setInterval(fetchData, 1000);

async function startBot() {
    await fetch('/bot/start', { method: 'POST' });
}

async function stopBot() {
    await fetch('/bot/stop', { method: 'POST' });
}
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return HTML_CONTENT

@app.get("/api/live")
async def get_live():
    return {
        "price": live["price"],
        "imbalance": live["imbalance"],
        "timestamp": live["timestamp"],
        "status": live["status"],
        "trade": current_trade.copy()
    }

@app.post("/bot/start")
async def start():
    global bot_running
    if not bot_running:
        bot_running = True
    return {"ok": True}

@app.post("/bot/stop")
async def stop():
    global bot_running
    bot_running = False
    return {"ok": True}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
