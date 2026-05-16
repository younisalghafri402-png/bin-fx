import asyncio
import aiohttp
from aiohttp import web
import json
import os
from datetime import datetime

SYMBOL = "XAUUSDT"
DEPTH_LIMIT = 10

# بيانات حية
live_data = {
    "price": 0.0,
    "bid_volume": 0.0,
    "ask_volume": 0.0,
    "imbalance": 0.5,
    "last_update": "",
    "bids": [],
    "asks": []
}

async def fetch_binance_data(session):
    """جلب السعر وعمق السوق من Binance"""
    try:
        # جلب السعر
        async with session.get(f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={SYMBOL}", timeout=10) as resp:
            if resp.status == 200:
                price_data = await resp.json()
                live_data["price"] = float(price_data['price'])
        
        # جلب عمق السوق
        async with session.get(f"https://fapi.binance.com/fapi/v1/depth?symbol={SYMBOL}&limit={DEPTH_LIMIT}", timeout=10) as resp:
            if resp.status == 200:
                depth = await resp.json()
                bids = depth.get('bids', [])
                asks = depth.get('asks', [])
                
                live_data["bids"] = [[float(b[0]), float(b[1])] for b in bids[:5]]
                live_data["asks"] = [[float(a[0]), float(a[1])] for a in asks[:5]]
                
                # حساب الحجم
                total_bid = sum(float(b[1]) for b in bids[:5])
                total_ask = sum(float(a[1]) for a in asks[:5])
                live_data["bid_volume"] = total_bid
                live_data["ask_volume"] = total_ask
                live_data["imbalance"] = total_bid / (total_bid + total_ask) if (total_bid + total_ask) > 0 else 0.5
                live_data["last_update"] = datetime.now().strftime("%H:%M:%S")
                
        return True
    except Exception as e:
        print(f"خطأ في جلب البيانات: {e}")
        return False

async def background_updater():
    """تحديث البيانات كل ثانية"""
    async with aiohttp.ClientSession() as session:
        while True:
            await fetch_binance_data(session)
            await asyncio.sleep(1)

# =============================================
# واجهة الويب
# =============================================
async def index(request):
    html = """<!DOCTYPE html>
<html>
<head>
    <title>XAUUSD Live Data - Binance Futures</title>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="2">
    <style>
        body { font-family: Arial; background: #1a1a2e; color: white; padding: 20px; text-align: center; }
        .price { font-size: 48px; color: #ffd966; margin: 20px; }
        .volumes { display: flex; justify-content: center; gap: 40px; margin: 20px; }
        .bid { color: #4caf50; }
        .ask { color: #f44336; }
        .table { display: inline-block; margin: 20px; text-align: left; }
        th, td { padding: 8px 16px; border-bottom: 1px solid #333; }
        .time { color: #888; font-size: 12px; }
    </style>
</head>
<body>
    <h1>🤖 XAUUSD Live Data (Binance Futures)</h1>
    <div class="price" id="price">--</div>
    <div class="volumes">
        <div class="bid">🟢 مشترين: <span id="bidVol">--</span></div>
        <div class="ask">🔴 بائعين: <span id="askVol">--</span></div>
    </div>
    <div>📊 عدم توازن: <span id="imbalance">--</span></div>
    <div class="time">آخر تحديث: <span id="time">--</span></div>
    
    <div style="display: flex; justify-content: center;">
        <div class="table">
            <h3>طلبات الشراء (Bids)</h3>
            <tr><th>السعر</th><th>الكمية</th></tr>
            <tbody id="bids"></tbody>
        </table>
        </div>
        <div class="table">
            <h3>طلبات البيع (Asks)</h3>
            <td><th>السعر</th><th>الكمية</th></tr>
            <tbody id="asks"></tbody>
        </table>
        </div>
    </div>
    
    <script>
        async function update() {
            try {
                const res = await fetch('/data');
                const d = await res.json();
                document.getElementById('price').innerText = d.price.toFixed(2);
                document.getElementById('bidVol').innerText = d.bid_volume.toFixed(2);
                document.getElementById('askVol').innerText = d.ask_volume.toFixed(2);
                document.getElementById('imbalance').innerText = (d.imbalance * 100).toFixed(1) + '%';
                document.getElementById('time').innerText = d.last_update;
                
                let bidsHtml = '';
                d.bids.forEach(b => { bidsHtml += `<tr><td>${b[0]}</td><td>${b[1]}</td></tr>`; });
                document.getElementById('bids').innerHTML = bidsHtml;
                
                let asksHtml = '';
                d.asks.forEach(a => { asksHtml += `<tr><td>${a[0]}</td><td>${a[1]}</td></tr>`; });
                document.getElementById('asks').innerHTML = asksHtml;
            } catch(e) { console.error(e); }
        }
        update();
        setInterval(update, 1000);
    </script>
</body>
</html>"""
    return web.Response(text=html, content_type='text/html')

async def data_api(request):
    return web.json_response(live_data)

async def main():
    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/data', data_api)
    
    # تشغيل خادم الويب
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"✅ واجهة الويب تعمل على http://0.0.0.0:{port}")
    print(f"📡 جلب البيانات من Binance {SYMBOL}...")
    
    # تشغيل المهام المتزامنة
    await asyncio.gather(
        background_updater(),
        asyncio.Event().wait()  # يبقي الخادم مفتوحاً
    )

if __name__ == "__main__":
    asyncio.run(main())