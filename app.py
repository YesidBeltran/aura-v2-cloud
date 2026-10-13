# AURA SYNAPSE V9.1 EXPERT - TRAILING DOBLE + DISEÑO V8.3.2 BONITO
import os, time, threading
from datetime import datetime
import pytz
from flask import Flask, render_template_string, jsonify
import yfinance as yf
import pandas as pd
import requests
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, TrailingStopOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus

app = Flask(__name__)

ALPACA_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY")
ALPACA_PAPER = True
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID")

SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA"]
TRAILING_CONFIG = {
    "SPY": {"buy": 0.5, "sell": 2.0, "qty": 10},
    "QQQ": {"buy": 0.75, "sell": 2.5, "qty": 5},
    "AAPL": {"buy": 1.0, "sell": 3.0, "qty": 10},
    "MSFT": {"buy": 1.0, "sell": 3.0, "qty": 5},
    "NVDA": {"buy": 1.5, "sell": 4.0, "qty": 10},
    "TSLA": {"buy": 1.5, "sell": 4.0, "qty": 5},
}
RSI_BUY = 35
RSI_SELL = 70
ACTIVATE_TRAILING_PROFIT = 3.0

NY_TZ = pytz.timezone('America/New_York')
BOGOTA_TZ = pytz.timezone('America/Bogota')
trading_client = TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=ALPACA_PAPER)

STATE = {"equity": 100000, "cash": 100000, "positions": {}, "logs": [], "last_run": None, "flat_done_today": False, "trailing_active": {}, "rsi_data": {}}

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT: return
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data={"chat_id": TELEGRAM_CHAT, "text": f"AURA V9.1: {msg}"}, timeout=5)
    except: pass
def log(msg):
    ts = datetime.now(BOGOTA_TZ).strftime("%H:%M:%S")
    STATE["logs"].insert(0, f"[{ts} BOG] {msg}")
    STATE["logs"] = STATE["logs"][:150]
    print(msg)
def get_rsi(symbol):
    try:
        df = yf.download(symbol, period="5d", interval="15m", progress=False)
        if len(df) < 14: return None
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + (gain/loss)))
        return float(rsi.iloc[-1]), float(df['Close'].iloc[-1])
    except Exception as e:
        log(f"Error RSI {symbol}: {e}")
        return None
def get_open_orders(symbol=None):
    try:
        req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
        orders = trading_client.get_orders(filter=req)
        if symbol: return [o for o in orders if o.symbol == symbol]
        return orders
    except: return []
def is_market_open():
    now = datetime.now(NY_TZ)
    if now.weekday() >= 5: return False
    return (now.hour > 9 or (now.hour == 9 and now.minute >= 30)) and now.hour < 16
def check_eod_flat():
    now = datetime.now(NY_TZ)
    if now.hour == 15 and now.minute >= 45 and not STATE["flat_done_today"]:
        if is_market_open():
            log(f"LOCK AURA CIERRE 15:45 - Cerrando {len(STATE['positions'])} pos")
            send_telegram(f"LOCK CIERRE 15:45 - Cerrando {len(STATE['positions'])} - FLAT")
            try: trading_client.cancel_orders(); time.sleep(1)
            except: pass
            for sym in list(STATE["positions"].keys()):
                try: trading_client.submit_order(MarketOrderRequest(symbol=sym, qty=STATE["positions"][sym]['qty'], side=OrderSide.SELL, time_in_force=TimeInForce.DAY))
                except: pass
            STATE["flat_done_today"]=True
            send_telegram(f"FLAT OK Equity {STATE['equity']:.2f} - Cash hasta mañana")
            STATE["positions"]={}
            return True
    if now.hour == 9 and now.minute == 30: STATE["flat_done_today"]=False
    return False

def run_aura():
    if not is_market_open(): log("Mercado cerrado"); return
    if check_eod_flat(): return
    STATE["last_run"]=datetime.now(BOGOTA_TZ).strftime("%Y-%m-%d %H:%M:%S BOG")
    log(f"--- AURA V9.1 SCAN 6 symbols ---")
    try:
        acct=trading_client.get_account()
        STATE["equity"]=float(acct.equity); STATE["cash"]=float(acct.cash)
    except: pass
    for symbol in SYMBOLS:
        res=get_rsi(symbol)
        if not res: continue
        rsi, price = res
        STATE["rsi_data"][symbol]={"rsi":rsi,"price":price}
        config=TRAILING_CONFIG[symbol]
        open_orders=get_open_orders(symbol)
        has_trailing=any(o.type=='trailing_stop' for o in open_orders)
        if rsi < RSI_BUY and symbol not in STATE["positions"] and not has_trailing:
            try:
                trading_client.submit_order(TrailingStopOrderRequest(symbol=symbol, qty=config["qty"], side=OrderSide.BUY, trail_percent=config["buy"], time_in_force=TimeInForce.DAY))
                log(f"COMPRA TRAILING {symbol} RSI {rsi:.1f} ${price:.2f} TrailBuy {config['buy']}%")
                send_telegram(f"COMPRA TRAILING {symbol} RSI {rsi:.1f} ${price:.2f} Trail {config['buy']}% - Esperando rebote")
                STATE["trailing_active"][symbol]=f"BUY {config['buy']}%"
            except Exception as e: log(f"Error Trail Buy {symbol}: {e}")
        if symbol in STATE["positions"]:
            entry=STATE["positions"][symbol]['entry_price']
            profit_pct=((price-entry)/entry)*100
            if profit_pct >= ACTIVATE_TRAILING_PROFIT and not has_trailing:
                try:
                    for o in open_orders:
                        try: trading_client.cancel_order_by_id(o.id)
                        except: pass
                    time.sleep(0.5)
                    trading_client.submit_order(TrailingStopOrderRequest(symbol=symbol, qty=STATE["positions"][symbol]['qty'], side=OrderSide.SELL, trail_percent=config["sell"], time_in_force=TimeInForce.DAY))
                    log(f"TRAILING SELL ON {symbol} +{profit_pct:.2f}% Trail {config['sell']}%")
                    send_telegram(f"TRAILING SELL {symbol} +{profit_pct:.1f}% Trail {config['sell']}%")
                    STATE["trailing_active"][symbol]=f"SELL {config['sell']}% (+{profit_pct:.1f}%)"
                except Exception as e: log(f"Error Trail Sell {symbol}: {e}")
            if rsi > RSI_SELL and not has_trailing:
                try:
                    trading_client.submit_order(MarketOrderRequest(symbol=symbol, qty=STATE["positions"][symbol]['qty'], side=OrderSide.SELL, time_in_force=TimeInForce.DAY))
                    log(f"VENTA RSI {symbol} RSI {rsi:.1f} +{profit_pct:.1f}%")
                    del STATE["positions"][symbol]
                except: pass
    log(f"Scan fin - Equity ${STATE['equity']:.2f}")

# ===== DASHBOARD BONITO V8.3.2 RESTAURADO =====
HTML = """
<!DOCTYPE html>
<html><head><title>AURA V9.1 EXPERT</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Space+Grotesk:wght@500;700&display=swap');
body{font-family:'Space Grotesk', sans-serif;background:#070a12;color:#e6e8f0;margin:0;padding:0}
.header{padding:24px 32px;display:flex;align-items:center;gap:16px;background:linear-gradient(90deg,#0f172a,#101a36);border-bottom:1px solid #1e293b;position:sticky;top:0;z-index:10}
.logo{width:48px;height:48px;background:radial-gradient(circle at 30% 30%,#00ff88,#00b4ff);border-radius:14px;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:22px;color:#000;box-shadow:0 0 20px rgba(0,255,136,.4)}
.title h1{margin:0;font-size:20px;letter-spacing:.5px}
.title p{margin:2px 0 0 0;opacity:.7;font-size:12px;font-family:'JetBrains Mono'}
.grid{display:grid;grid-template-columns:1.2fr.8fr;gap:16px;padding:20px;max-width:1400px;margin:auto}
@media(max-width:900px){.grid{grid-template-columns:1fr}}
.card{background:rgba(17,24,39,.8);backdrop-filter:blur(10px);border:1px solid #1f2937;border-radius:16px;padding:18px;box-shadow:0 10px 30px rgba(0,0,0,.3)}
.card h3{margin:0 0 12px 0;font-size:13px;letter-spacing:1px;text-transform:uppercase;color:#94a3b8}
.metric{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px dashed #1e293b;font-family:'JetBrains Mono';font-size:13px}
.metric b{color:#e2e8f0}
.green{color:#00ff88}.yellow{color:#ffcc00}.red{color:#ff5a5a}
table{width:100%;border-collapse:collapse;font-family:'JetBrains Mono';font-size:12px}
th{color:#64748b;text-align:left;padding:10px 6px;border-bottom:1px solid #1e293b;font-size:11px;text-transform:uppercase;letter-spacing:.8px}
td{padding:10px 6px;border-bottom:1px solid #111827}
.badge{padding:4px 8px;border-radius:20px;font-size:10px;background:#0b1220;border:1px solid #1e293b}
.btn{background:linear-gradient(90deg,#00ff88,#00d1ff);color:#000;font-weight:700;border:none;padding:12px 18px;border-radius:12px;cursor:pointer;font-family:'Space Grotesk';box-shadow:0 0 20px rgba(0,255,136,.3);transition:.2s}
.btn:hover{transform:translateY(-1px);box-shadow:0 0 30px rgba(0,255,136,.5)}
.log{font-family:'JetBrains Mono';font-size:11px;line-height:1.6;max-height:380px;overflow:auto}
.rsi-bar{height:6px;background:#1e293b;border-radius:10px;overflow:hidden;margin-top:4px}
.rsi-fill{height:100%;border-radius:10px}
</style></head><body>
<div class="header">
<div class="logo">A</div>
<div class="title">
<h1>AURA SYNAPSE V9.1 EXPERT — TRAILING STOP DOBLE</h1>
<p>STOCKS ONLY • SPY QQQ AAPL MSFT NVDA TSLA • Trailing Buy 0.5-1.5% / Sell 2-4% • EOD FLAT 15:45 NY</p>
</div>
<div style="margin-left:auto;display:flex;gap:10px">
<a href="/run" style="text-decoration:none"><button class="btn">EJECUTAR SCAN AHORA</button></a>
</div>
</div>

<div class="grid">
<div style="display:flex;flex-direction:column;gap:16px">
<div class="card">
<h3>📊 Estado Cuenta — V9.1 LIVE</h3>
<div class="metric"><span>Equity</span><b class="green">${{equity}}</b></div>
<div class="metric"><span>Cash Disponible</span><b>${{cash}}</b></div>
<div class="metric"><span>Posiciones</span><b>{{pos_count}} / 6</b></div>
<div class="metric"><span>Último Scan</span><b>{{last_run}}</b></div>
<div class="metric"><span>Estado</span><b class="{% if flat_done %}yellow{% else %}green{% endif %}">{% if flat_done %}LOCK FLAT Hoy — En cash{% else %}Activo — Cada 5min{% endif %}</b></div>
<div class="metric"><span>Modo</span><b>Trailing Buy + Sell Nativo Alpaca</b></div>
</div>

<div class="card">
<h3>📈 Posiciones Activas + Trailing</h3>
<table><tr><th>Symbol</th><th>Qty</th><th>Entry</th><th>RSI Actual</th><th>Trail</th></tr>
{% for s in symbols %}
<tr>
<td><b>{{s}}</b></td>
<td>{{positions.get(s,{}).get('qty','-')}}</td>
<td>{% if s in positions %}${{positions[s].entry_price}}{% else %}-{% endif %}</td>
<td>
{% if s in rsi_data %}
{{"%.1f"|format(rsi_data[s].rsi)}}
<div class="rsi-bar"><div class="rsi-fill" style="width:{{rsi_data[s].rsi}}%;background:{% if rsi_data[s].rsi<35 %}#00ff88{% elif rsi_data[s].rsi>70 %}#ff5a5a{% else %}#3b82f6{% endif %}"></div></div>
{% else %}-{% endif %}
</td>
<td><span class="badge">{{trailing.get(s,'-')}}</span></td>
</tr>
{% endfor %}
</table>
<p style="font-size:11px;color:#64748b;margin-top:10px">Trailing Config: SPY 0.5/2% QQQ 0.75/2.5% AAPL/MSFT 1/3% NVDA/TSLA 1.5/4% — Activa sell al +3%</p>
</div>
</div>

<div style="display:flex;flex-direction:column;gap:16px">
<div class="card">
<h3>🧠 Logs V9.1 — Trailing Doble</h3>
<div class="log">
{% for l in logs %}<div style="padding:2px 0;border-bottom:1px solid #0f172a">{{l}}</div>{% endfor %}
</div>
</div>

<div class="card" style="background:linear-gradient(180deg,#0f172a,#0a101f)">
<h3>⚡ Acciones Rápidas</h3>
<div style="display:flex;flex-direction:column;gap:10px">
<a href="/run" style="text-decoration:none"><button class="btn" style="width:100%">🚀 FORZAR SCAN TRAILING AHORA</button></a>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-family:'JetBrains Mono';font-size:11px">
<div class="metric"><span>Equity Hoy</span><b class="green">+${{(equity|float - 100000)|round(2)}}</b></div>
<div class="metric"><span>Token</span><b>?token=aura2026</b></div>
</div>
<p style="font-size:10px;color:#475569">Diseño V8.3.2 restaurado + Motor V9.0 Trailing. EOD FLAT 15:45 NY / 14:45 BOG. UptimeRobot 5min.</p>
</div>
</div>
</div>
</div>
</body></html>
"""

@app.route("/")
def dashboard():
    return render_template_string(HTML, equity=f"{STATE['equity']:.2f}", cash=f"{STATE['cash']:.2f}", pos_count=len(STATE["positions"]), last_run=STATE["last_run"] or "Nunca", positions=STATE["positions"], logs=STATE["logs"], trailing=STATE["trailing_active"], flat_done=STATE["flat_done_today"], symbols=SYMBOLS, rsi_data=STATE["rsi_data"])

@app.route("/run")
def run_now():
    threading.Thread(target=run_aura).start()
    return jsonify({"status":"V9.1 scan iniciado","equity":STATE["equity"]})
@app.route("/health")
def health(): return jsonify({"status":"ok","version":"V9.1 EXPERT TRAILING + DESIGN","equity":STATE["equity"]})

def loop():
    while True:
        try: run_aura()
        except Exception as e: log(f"Error loop: {e}")
        time.sleep(300)
threading.Thread(target=loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
