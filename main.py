"""
╔══════════════════════════════════════════════════════════╗
║         بوت السكالبنج — XAUUSDT / Binance Futures       ║
║  ملف واحد: بوت + واجهة ويب + تليجرام + منع النوم 24/7   ║
║  تشغيل: python bot.py                                    ║
╚══════════════════════════════════════════════════════════╝
"""

import asyncio
import time
import os
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

import aiohttp
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import uvicorn


# ══════════════════════════════════════════════════════════
#  الإعدادات — يمكن تغييرها عبر متغيرات البيئة
# ══════════════════════════════════════════════════════════
SYMBOL              = os.getenv("SYMBOL",             "XAUUSDT")
DEPTH_LIMIT         = 10
IMBALANCE_THRESHOLD = float(os.getenv("IMBALANCE_THRESHOLD", "0.80"))   # 80%
TP_POINTS           = int(os.getenv("TP_POINTS",      "100"))            # نقاط الهدف
SL_POINTS           = int(os.getenv("SL_POINTS",      "100"))            # نقاط الستوب
SIGNAL_COOLDOWN     = float(os.getenv("SIGNAL_COOLDOWN", "60"))          # ثواني بين إشارتين
TELEGRAM_TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN", "8292443875:AAHVG6THkf9zL2r-1B2DVUcUl4yfWXS52zg")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID",   "-1003952441740")
RENDER_URL          = os.getenv("RENDER_EXTERNAL_URL", "")
PORT                = int(os.getenv("PORT",           "8000"))


# ══════════════════════════════════════════════════════════
#  حالة البوت (في الذاكرة)
# ══════════════════════════════════════════════════════════
bot_running  = False
bot_task: Optional[asyncio.Task] = None

current_trade: Dict[str, Any] = {
    "active":     False,
    "type":       None,
    "entry_price": 0,
    "tp":         0,
    "sl":         0,
    "tp_hit":     False,
    "sl_hit":     False,
}

signals_history: List[Dict] = []

live: Dict[str, Any] = {
    "price":     0.0,
    "imbalance": 0.5,
    "timestamp": "",
    "status":    "stopped",
    "trade":     {},
}

clients: List[WebSocket] = []


# ══════════════════════════════════════════════════════════
#  WebSocket — بث للجميع
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
    bids  = sum(float(b[1]) for b in ob.get("bids", [])[:5])
    asks  = sum(float(a[1]) for a in ob.get("asks", [])[:5])
    total = bids + asks
    return bids / total if total > 0 else 0.5


def calc_sltp(signal: str, price: float):
    if signal == "BUY":
        return round(price - SL_POINTS * 0.01, 2), round(price + TP_POINTS * 0.01, 2)
    return round(price + SL_POINTS * 0.01, 2), round(price - TP_POINTS * 0.01, 2)


def get_signal(imbalance: float, price: float):
    if imbalance > IMBALANCE_THRESHOLD:
        sl, tp = calc_sltp("BUY", price)
        return "BUY",  sl, tp, f"طلب مرتفع {imbalance * 100:.0f}%"
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
            pass
    except Exception as e:
        print(f"⚠️ تليجرام خطأ: {e}")


# ══════════════════════════════════════════════════════════
#  مراقبة الصفقة (TP / SL)
# ══════════════════════════════════════════════════════════
async def watch_trade(session: aiohttp.ClientSession):
    global current_trade
    while bot_running:
        if current_trade["active"]:
            price = await fetch_price(session)
            if price:
                t      = current_trade
                hit_tp = (t["type"] == "BUY"  and price >= t["tp"]) or \
                         (t["type"] == "SELL" and price <= t["tp"])
                hit_sl = (t["type"] == "BUY"  and price <= t["sl"]) or \
                         (t["type"] == "SELL" and price >= t["sl"])

                if hit_tp and not t["tp_hit"]:
                    current_trade.update({"tp_hit": True, "active": False})
                    msg = f"✅ *TP DONE — {SYMBOL}* 🥇\n💰 السعر: {price:.2f}"
                    await tg(session, msg)
                    await broadcast({"type": "trade_closed", "result": "TP", "price": price})
                    print(f"✅ TP hit @ {price:.2f}")

                elif hit_sl and not t["sl_hit"]:
                    current_trade.update({"sl_hit": True, "active": False})
                    msg = f"🛑 *SL Hit — {SYMBOL}*\n💸 السعر: {price:.2f}"
                    await tg(session, msg)
                    await broadcast({"type": "trade_closed", "result": "SL", "price": price})
                    print(f"🛑 SL hit @ {price:.2f}")

        await asyncio.sleep(0.5)


# ══════════════════════════════════════════════════════════
#  Keep-alive — يمنع النوم على Render free tier
# ══════════════════════════════════════════════════════════
async def keep_alive():
    target = RENDER_URL or f"http://localhost:{PORT}"
    await asyncio.sleep(30)
    async with aiohttp.ClientSession() as s:
        while True:
            try:
                async with s.get(
                    f"{target}/status",
                    timeout=aiohttp.ClientTimeout(total=15),
                ):
                    print(f"✅ Keep-alive ping → {target}/status")
            except Exception as e:
                print(f"⚠️ Keep-alive خطأ: {e}")
            await asyncio.sleep(14 * 60)


# ══════════════════════════════════════════════════════════
#  الحلقة الرئيسية للبوت
# ══════════════════════════════════════════════════════════
async def run_bot():
    global bot_running, current_trade, live, signals_history

    print(f"🚀 البوت يعمل — {SYMBOL}")
    last_signal_time = 0.0

    async with aiohttp.ClientSession() as session:
        await tg(
            session,
            f"🤖 *بوت السكالبنج قيد التشغيل*\n"
            f"📊 {SYMBOL}\n"
            f"🎯 الهدف: {TP_POINTS} نقطة | 🛑 الستوب: {SL_POINTS} نقطة\n"
            f"⚡ مسح كل 0.35 ثانية — إشارات فورية",
        )

        asyncio.create_task(watch_trade(session))

        while bot_running:
            try:
                ob    = await fetch_order_book(session)
                price = await fetch_price(session)

                if not ob or not price:
                    await asyncio.sleep(0.35)
                    continue

                imbalance = calc_imbalance(ob)
                now       = datetime.now()

                # ── تحديث البيانات الحية (3 مرات في الثانية) ──
                live.update({
                    "price":     price,
                    "imbalance": round(imbalance, 4),
                    "timestamp": now.strftime("%H:%M:%S"),
                    "status":    "running",
                    "trade":     current_trade.copy(),
                })
                await broadcast({"type": "tick", **live})

                # ── فحص الإشارة الفوري مع كول-داون ──
                if not current_trade["active"]:
                    if time.time() - last_signal_time >= SIGNAL_COOLDOWN:
                        signal, sl, tp, reason = get_signal(imbalance, price)
                        if signal:
                            last_signal_time = time.time()
                            arrow  = "🔥" if signal == "BUY" else "❄️"
                            action = "شراء" if signal == "BUY" else "بيع"

                            await tg(
                                session,
                                f"{arrow} *{SYMBOL}* {action} @ {price:.2f}\n\n"
                                f"🥇 TP1: {tp:.2f}\n"
                                f"🛑 SL:  {sl:.2f}\n\n"
                                f"📊 OBI: {imbalance:.2f}\n"
                                f"💡 {reason}",
                            )

                            entry = {
                                "id":        len(signals_history) + 1,
                                "type":      signal,
                                "entry":     price,
                                "tp":        tp,
                                "sl":        sl,
                                "imbalance": round(imbalance, 4),
                                "reason":    reason,
                                "time":      now.strftime("%H:%M:%S"),
                                "date":      now.strftime("%Y-%m-%d"),
                            }
                            signals_history.insert(0, entry)
                            if len(signals_history) > 50:
                                signals_history.pop()

                            current_trade.update({
                                "active":      True,
                                "type":        signal,
                                "entry_price": price,
                                "tp":          tp,
                                "sl":          sl,
                                "tp_hit":      False,
                                "sl_hit":      False,
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
#  Ticker الدائم — يجلب السعر حتى وإن كان البوت متوقفاً
# ══════════════════════════════════════════════════════════
async def price_ticker():
    async with aiohttp.ClientSession() as session:
        while True:
            if not bot_running:
                try:
                    price = await fetch_price(session)
                    ob    = await fetch_order_book(session)
                    if price and ob:
                        imbalance = calc_imbalance(ob)
                        live.update({
                            "price":     price,
                            "imbalance": round(imbalance, 4),
                            "timestamp": datetime.now().strftime("%H:%M:%S"),
                            "status":    "stopped",
                        })
                        await broadcast({"type": "tick", **live})
                except Exception:
                    pass
            await asyncio.sleep(0.35)


# ══════════════════════════════════════════════════════════
#  واجهة الويب — داشبورد عربي كامل
# ══════════════════════════════════════════════════════════
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>بوت السكالبنج – XAUUSDT</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#0a0e1a;--panel:#111827;--border:#1f2d45;
  --text:#e2e8f0;--muted:#64748b;
  --green:#10b981;--green-dim:#064e3b;
  --red:#ef4444;--red-dim:#450a0a;
  --gold:#f59e0b;--blue:#3b82f6;
}
body{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;min-height:100vh}

header{
  background:var(--panel);border-bottom:1px solid var(--border);
  padding:14px 24px;display:flex;align-items:center;
  justify-content:space-between;position:sticky;top:0;z-index:10;
}
.logo{display:flex;align-items:center;gap:10px;font-size:1.1rem;font-weight:700}
.logo-sub{font-size:.75rem;color:var(--gold);font-weight:500;letter-spacing:1px}
.header-right{display:flex;align-items:center;gap:14px}
.pill{
  display:flex;align-items:center;gap:8px;
  background:var(--bg);border:1px solid var(--border);
  border-radius:999px;padding:6px 14px;font-size:.8rem;font-weight:600;
}
.dot{width:8px;height:8px;border-radius:50%;background:var(--muted);transition:background .3s}
.dot.on{background:var(--green);box-shadow:0 0 8px var(--green);animation:pulse 1.5s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.btn{
  border:none;border-radius:8px;padding:8px 20px;
  font-size:.85rem;font-weight:700;cursor:pointer;
  transition:all .2s;font-family:inherit;
}
.btn-on{background:var(--green);color:#fff}
.btn-on:hover{background:#059669;transform:translateY(-1px)}
.btn-off{background:var(--red);color:#fff}
.btn-off:hover{background:#dc2626;transform:translateY(-1px)}
.btn:disabled{opacity:.4;cursor:not-allowed;transform:none}

main{max-width:1300px;margin:0 auto;padding:24px;display:grid;gap:20px}
.row4{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:20px}
@media(max-width:900px){.row4{grid-template-columns:repeat(2,1fr)}.row2{grid-template-columns:1fr}}
@media(max-width:500px){.row4{grid-template-columns:1fr}main{padding:14px}}

.card{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:20px}
.card.buy{border-color:var(--green)}
.card.sell{border-color:var(--red)}
.lbl{font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px}
.val{font-size:2rem;font-weight:700;line-height:1;letter-spacing:-.5px}
.val.gold{color:var(--gold)}
.val.blue{color:var(--blue)}
.sub{font-size:.75rem;color:var(--muted);margin-top:6px}
.obi-bg{height:10px;background:var(--border);border-radius:999px;margin-top:12px;overflow:hidden}
.obi-fill{height:100%;border-radius:999px;transition:width .4s,background .4s}

.thead{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
.ttitle{font-size:1rem;font-weight:700}
.badge{border-radius:6px;padding:4px 12px;font-size:.78rem;font-weight:700;letter-spacing:1px}
.b-buy{background:var(--green-dim);color:var(--green)}
.b-sell{background:var(--red-dim);color:var(--red)}
.b-idle{background:var(--border);color:var(--muted)}
.rows{display:flex;flex-direction:column;gap:10px}
.row{display:flex;justify-content:space-between;align-items:center;padding:10px 14px;background:var(--bg);border-radius:8px}
.row-l{font-size:.8rem;color:var(--muted)}
.row-v{font-size:.95rem;font-weight:600}
.row-v.tp{color:var(--green)}
.row-v.sl{color:var(--red)}
.row-v.g{color:var(--gold)}

.sec-title{font-size:.9rem;font-weight:700;margin-bottom:16px}
.tw{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:.83rem}
thead th{
  text-align:right;padding:10px 12px;font-size:.72rem;
  font-weight:600;color:var(--muted);letter-spacing:.5px;
  border-bottom:1px solid var(--border);
}
tbody tr{border-bottom:1px solid var(--border);transition:background .15s}
tbody tr:hover{background:rgba(255,255,255,.03)}
tbody td{padding:11px 12px;vertical-align:middle}
.st{display:inline-block;padding:3px 10px;border-radius:5px;font-weight:700;font-size:.75rem;letter-spacing:1px}
.sb{background:var(--green-dim);color:var(--green)}
.ss{background:var(--red-dim);color:var(--red)}
.empty td{text-align:center;color:var(--muted);padding:40px;font-size:.9rem}

#toast{
  position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(80px);
  background:var(--panel);border:1px solid var(--border);border-radius:10px;
  padding:12px 24px;font-size:.85rem;font-weight:600;
  transition:transform .3s;z-index:100;white-space:nowrap;
}
#toast.show{transform:translateX(-50%) translateY(0)}
#toast.g{border-color:var(--green);color:var(--green)}
#toast.r{border-color:var(--red);color:var(--red)}
#toast.o{border-color:var(--gold);color:var(--gold)}
</style>
</head>
<body>

<header>
  <div class="logo">
    <span style="font-size:1.4rem">📊</span>
    <div>
      <div>بوت السكالبنج</div>
      <div class="logo-sub">XAUUSDT · Binance Futures</div>
    </div>
  </div>
  <div class="header-right">
    <div class="pill">
      <div class="dot" id="dot"></div>
      <span id="stxt">متوقف</span>
    </div>
    <button class="btn btn-on"  id="btnOn"  onclick="startBot()">تشغيل</button>
    <button class="btn btn-off" id="btnOff" onclick="stopBot()" disabled>إيقاف</button>
  </div>
</header>

<main>
  <div class="row4">
    <div class="card">
      <div class="lbl">السعر الحالي</div>
      <div class="val gold" id="price">---.--</div>
      <div class="sub">XAUUSDT · Binance Futures</div>
    </div>
    <div class="card">
      <div class="lbl">عدم التوازن (OBI)</div>
      <div class="val blue" id="obiV">0.5000</div>
      <div class="obi-bg"><div class="obi-fill" id="obiB" style="width:50%;background:#3b82f6"></div></div>
    </div>
    <div class="card">
      <div class="lbl">إجمالي الإشارات</div>
      <div class="val" id="sigN">0</div>
      <div class="sub">منذ بدء التشغيل</div>
    </div>
    <div class="card">
      <div class="lbl">آخر تحديث</div>
      <div class="val" style="font-size:1.3rem" id="ts">--:--:--</div>
      <div class="sub">وقت الخادم</div>
    </div>
  </div>

  <div class="row2">
    <div class="card" id="tradeCard">
      <div class="thead">
        <div class="ttitle">الصفقة الحالية</div>
        <span class="badge b-idle" id="tbadge">لا توجد صفقة</span>
      </div>
      <div class="rows">
        <div class="row"><span class="row-l">الحالة</span>     <span class="row-v"    id="tstat">في انتظار الإشارة</span></div>
        <div class="row"><span class="row-l">سعر الدخول</span> <span class="row-v g"  id="tentry">--</span></div>
        <div class="row"><span class="row-l">الهدف (TP)</span> <span class="row-v tp" id="ttp">--</span></div>
        <div class="row"><span class="row-l">الستوب (SL)</span><span class="row-v sl" id="tsl">--</span></div>
      </div>
    </div>
    <div class="card">
      <div class="sec-title">إعدادات البوت</div>
      <div class="rows">
        <div class="row"><span class="row-l">الزوج</span>             <span class="row-v g">XAUUSDT</span></div>
        <div class="row"><span class="row-l">عتبة الدخول (OBI)</span> <span class="row-v">80%</span></div>
        <div class="row"><span class="row-l">الهدف</span>             <span class="row-v tp">100 نقطة</span></div>
        <div class="row"><span class="row-l">الستوب</span>            <span class="row-v sl">100 نقطة</span></div>
        <div class="row"><span class="row-l">كول-داون الإشارة</span>  <span class="row-v">60 ثانية</span></div>
        <div class="row"><span class="row-l">سرعة المسح</span>        <span class="row-v">~3x / ثانية</span></div>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="sec-title">سجل الإشارات</div>
    <div class="tw">
      <table>
        <thead><tr>
          <th>#</th><th>التاريخ</th><th>الوقت</th><th>النوع</th>
          <th>الدخول</th><th>الهدف</th><th>الستوب</th><th>OBI</th><th>السبب</th>
        </tr>
        </thead>
        <tbody id="tBody">
          <tr class="empty"><td colspan="9">لا توجد إشارات — شغّل البوت للبدء</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</main>

<div id="toast"></div>

<script>
let ws, timer, running = false, sigs = [];

function conn() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen  = () => clearTimeout(timer);
  ws.onclose = () => { timer = setTimeout(conn, 3000); };
  ws.onmessage = e => {
    const d = JSON.parse(e.data);
    if (d.type === 'tick') {
      applyTick(d);
    } else if (d.type === 'signal') {
      sigs.unshift(d);
      render();
      toast(`إشارة ${d.type === 'BUY' ? 'شراء 🔥' : 'بيع ❄️'} @ ${d.entry.toFixed(2)}`, d.type === 'BUY' ? 'g' : 'r');
    } else if (d.type === 'trade_closed') {
      const win = d.result === 'TP';
      toast(win ? `✅ TP @ ${d.price.toFixed(2)}` : `🛑 SL @ ${d.price.toFixed(2)}`, win ? 'g' : 'r');
      applyTrade({ active: false });
    } else if (d.type === 'status_change') {
      running = d.status === 'running';
      setCtrl(running);
      setPill(running);
    }
  };
}

function applyTick(d) {
  if (d.price)     el('price').textContent = d.price.toFixed(2);
  if (d.timestamp) el('ts').textContent    = d.timestamp;
  if (d.imbalance !== undefined) {
    const p = d.imbalance * 100;
    el('obiV').textContent  = d.imbalance.toFixed(4);
    el('obiB').style.width  = p + '%';
    el('obiB').style.background = p > 80 ? '#10b981' : p < 20 ? '#ef4444' : '#3b82f6';
  }
  if (d.trade)   applyTrade(d.trade);
  if (d.status)  { running = d.status === 'running'; setPill(running); setCtrl(running); }
}

function applyTrade(t) {
  if (!t) return;
  const card  = el('tradeCard');
  const badge = el('tbadge');
  if (t.active) {
    const buy = t.type === 'BUY';
    card.className  = `card ${buy ? 'buy' : 'sell'}`;
    badge.className = `badge ${buy ? 'b-buy' : 'b-sell'}`;
    badge.textContent = buy ? 'شراء نشط' : 'بيع نشط';
    el('tstat').textContent  = 'صفقة مفتوحة';
    el('tentry').textContent = t.entry_price?.toFixed(2) || '--';
    el('ttp').textContent    = t.tp?.toFixed(2)          || '--';
    el('tsl').textContent    = t.sl?.toFixed(2)          || '--';
  } else {
    card.className  = 'card';
    badge.className = 'badge b-idle';
    badge.textContent = 'لا توجد صفقة';
    el('tstat').textContent = 'في انتظار الإشارة';
    ['tentry','ttp','tsl'].forEach(id => el(id).textContent = '--');
  }
}

function render() {
  el('sigN').textContent = sigs.length;
  const b = el('tBody');
  if (!sigs.length) {
    b.innerHTML = '<tr class="empty"><td colspan="9">لا توجد إشارات — شغّل البوت للبدء</td></tr>';
    return;
  }
  b.innerHTML = sigs.map(s => `<tr>
    <td style="color:var(--muted)">${s.id}</td>
    <td style="color:var(--muted);font-size:.78rem">${s.date}</td>
    <td style="font-family:monospace;color:var(--muted)">${s.time}</td>
    <td><span class="st ${s.type === 'BUY' ? 'sb' : 'ss'}">${s.type === 'BUY' ? 'شراء' : 'بيع'}</span></td>
    <td style="font-weight:600">${s.entry.toFixed(2)}</td>
    <td style="color:var(--green);font-weight:600">${s.tp.toFixed(2)}</td>
    <td style="color:var(--red);font-weight:600">${s.sl.toFixed(2)}</td>
    <td style="color:var(--blue)">${(s.imbalance * 100).toFixed(1)}%</td>
    <td style="color:var(--muted);font-size:.78rem">${s.reason}</td>
  </tr>`).join('');
}

function setPill(on) {
  el('dot').className        = 'dot' + (on ? ' on' : '');
  el('stxt').textContent     = on ? 'يعمل' : 'متوقف';
}
function setCtrl(on) {
  el('btnOn').disabled  = on;
  el('btnOff').disabled = !on;
}
function el(id) { return document.getElementById(id); }

let toastT;
function toast(msg, cls = '') {
  const t = el('toast');
  t.textContent = msg;
  t.className   = 'show ' + cls;
  clearTimeout(toastT);
  toastT = setTimeout(() => t.className = '', 4000);
}

async function startBot() {
  setCtrl(true);
  const ok = await fetch('/bot/start', { method: 'POST' }).then(r => r.ok).catch(() => false);
  if (ok) { setPill(true);  toast('تم تشغيل البوت ✅', 'g'); }
  else    { setCtrl(false); toast('فشل التشغيل ❌', 'r'); }
}
async function stopBot() {
  setCtrl(false);
  await fetch('/bot/stop', { method: 'POST' }).catch(() => {});
  setPill(false);
  toast('تم الإيقاف', 'o');
}

conn();
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════════════
#  FastAPI — المسارات
# ══════════════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(price_ticker())
    asyncio.create_task(keep_alive())
    yield


app = FastAPI(title="بوت السكالبنج", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML


@app.get("/status")
async def status():
    return {
        "running":   bot_running,
        "symbol":    SYMBOL,
        "threshold": IMBALANCE_THRESHOLD,
        "tp_points": TP_POINTS,
        "sl_points": SL_POINTS,
        **live,
    }


@app.get("/signals")
async def signals():
    return signals_history


@app.post("/bot/start")
async def start():
    global bot_running, bot_task
    if not bot_running:
        bot_running = True
        live["status"] = "running"
        bot_task = asyncio.create_task(run_bot())
        await broadcast({"type": "status_change", "status": "running"})
    return {"ok": True, "running": True}


@app.post("/bot/stop")
async def stop():
    global bot_running, bot_task
    bot_running = False
    if bot_task:
        bot_task.cancel()
        bot_task = None
    live["status"] = "stopped"
    await broadcast({"type": "status_change", "status": "stopped"})
    return {"ok": True, "running": False}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    clients.append(ws)
    try:
        await ws.send_json({
            "type":      "tick",
            "running":   bot_running,
            "signals":   signals_history,
            **live,
        })
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if ws in clients:
            clients.remove(ws)


# ══════════════════════════════════════════════════════════
#  تشغيل الخادم
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 54)
    print("🤖  بوت السكالبنج — XAUUSDT")
    print(f"🌐  الداشبورد: http://localhost:{PORT}")
    print(f"🎯  الهدف: {TP_POINTS} نقطة  |  🛑 الستوب: {SL_POINTS} نقطة")
    print(f"📊  عتبة OBI: {IMBALANCE_THRESHOLD * 100:.0f}%")
    print(f"⚡  مسح كل 0.35 ثانية (~3x/ثانية)")
    print(f"🔔  تليجرام: {'✅ مفعّل' if TELEGRAM_TOKEN else '❌ غير مضبوط'}")
    print("=" * 54)
    uvicorn.run(app, host="0.0.0.0", port=PORT)