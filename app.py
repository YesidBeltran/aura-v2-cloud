# AURA SYNAPSE V9.0 EXPERT - STOCKS ONLY + TRAILING STOP DOBLE
# Yesid Fernando Beltran Lopez - Riqueza con proposito
import os, time, threading
from datetime import datetime
import pytz
from flask import Flask, render_template_string, jsonify
import yfinance as yf
import pandas as pd
import requests
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, TrailingStopOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderType, QueryOrderStatus

app = Flask(__name__)

# ========== CONFIG ==========
ALPACA_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY")
ALPACA_PAPER = True
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID")

# 6 TOP STOCKS ONLY - SIN FOREX
SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA"]

# V9.0 TRAILING CONFIG - COMPRA Y VENTA
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
ACTIVATE_TRAILING_PROFIT = 3.0 # Activa trailing sell cuando lleva +3%

NY_TZ = pytz.timezone('America/New_York')
BOGOTA_TZ = pytz.timezone('America/Bogota')

trading_client = TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=ALPACA_PAPER)

STATE = {
    "equity": 100000,
    "cash": 100000,
    "positions": {},
    "logs": [],
    "last_run": None,
    "flat_done_today": False,
    "trailing_active": {}
}

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT, "text": f"AURA V9.0: {msg}"}, timeout=5)
    except: pass

def log(msg):
    ts = datetime.now(BOGOTA_TZ).strftime("%H:%M:%S")
    STATE["logs"].insert(0, f"[{ts} BOG] {msg}")
    STATE["logs"] = STATE["logs"][:100]
    print(msg)

def get_rsi(symbol):
    try:
        df = yf.download(symbol, period="5d", interval="15m", progress=False)
        if len(df) < 14: return None
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1]), float(df['Close'].iloc[-1])
    except Exception as e:
        log(f"Error RSI {symbol}: {e}")
        return None

def get_open_orders(symbol=None):
    try:
        req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
        orders = trading_client.get_orders(filter=req)
        if symbol:
            return [o for o in orders if o.symbol == symbol]
        return orders
    except: return []

def is_market_open():
    now = datetime.now(NY_TZ)
    # Lunes-Viernes 09:30-16:00 NY
    if now.weekday() >= 5: return False
    return (now.hour > 9 or (now.hour == 9 and now.minute >= 30)) and now.hour < 16

def check_eod_flat():
    now = datetime.now(NY_TZ)
    if now.hour == 15 and now.minute >= 45 and not STATE["flat_done_today"]:
        if is_market_open():
            log(f"LOCK AURA CIERRE DEL DIA 15:45 - Cerrando {len(STATE['positions'])} posiciones")
            send_telegram(f"LOCK CIERRE 15:45 - Cerrando {len(STATE['positions'])} posiciones para FLAT")
            # Cancelar trailing pendientes y vender a mercado
            try:
                trading_client.cancel_orders()
                time.sleep(1)
            except: pass
            for sym in list(STATE["positions"].keys()):
                try:
                    trading_client.submit_order(MarketOrderRequest(symbol=sym, qty=STATE["positions"][sym]['qty'], side=OrderSide.SELL, time_in_force=TimeInForce.DAY))
                    log(f"FLAT {sym} cerrado a mercado")
                except Exception as e:
                    log(f"Error FLAT {sym}: {e}")
            STATE["flat_done_today"] = True
            send_telegram(f"FLAT OK - Todas cerradas. Equity {STATE['equity']:.2f} - En cash hasta mañana")
            STATE["positions"] = {}
            return True
    # Reset flag a las 9:30
    if now.hour == 9 and now.minute == 30:
        STATE["flat_done_today"] = False
    return False

def run_aura():
    if not is_market_open():
        log("Mercado cerrado - AURA en espera")
        return

    if check_eod_flat():
        return

    STATE["last_run"] = datetime.now(BOGOTA_TZ).isoformat()
    log(f"--- AURA V9.0 SCAN {len(SYMBOLS)} symbols ---")

    # Actualizar cuenta
    try:
        acct = trading_client.get_account()
        STATE["equity"] = float(acct.equity)
        STATE["cash"] = float(acct.cash)
    except: pass

    for symbol in SYMBOLS:
        res = get_rsi(symbol)
        if not res: continue
        rsi, price = res
        config = TRAILING_CONFIG[symbol]
        open_orders = get_open_orders(symbol)
        has_trailing = any(o.type == 'trailing_stop' for o in open_orders)

        # --- LOGICA COMPRA CON TRAILING BUY ---
        if rsi < RSI_BUY and symbol not in STATE["positions"] and not has_trailing:
            try:
                # Trailing Buy: compra cuando rebota X% desde el minimo
                req = TrailingStopOrderRequest(
                    symbol=symbol,
                    qty=config["qty"],
                    side=OrderSide.BUY,
                    trail_percent=config["buy"],
                    time_in_force=TimeInForce.DAY
                )
                trading_client.submit_order(req)
                log(f"COMPRA TRAILING {symbol} RSI {rsi:.1f} Precio {price:.2f} TrailBuy {config['buy']}% Qty {config['qty']}")
                send_telegram(f"COMPRA TRAILING {symbol} RSI {rsi:.1f} Precio {price:.2f} Trail {config['buy']}% Qty {config['qty']} - Esperando rebote")
                STATE["trailing_active"][symbol] = "BUY"
            except Exception as e:
                log(f"Error Trailing Buy {symbol}: {e}")

        # --- LOGICA VENTA CON TRAILING SELL ---
        if symbol in STATE["positions"]:
            entry = STATE["positions"][symbol]['entry_price']
            profit_pct = ((price - entry) / entry) * 100

            # Si lleva +3% y no tiene trailing sell activo, activa trailing sell
            if profit_pct >= ACTIVATE_TRAILING_PROFIT and not has_trailing:
                try:
                    # Cancelar ordenes previas y poner trailing sell
                    for o in open_orders:
                        try: trading_client.cancel_order_by_id(o.id)
                        except: pass
                    time.sleep(0.5)
                    req = TrailingStopOrderRequest(
                        symbol=symbol,
                        qty=STATE["positions"][symbol]['qty'],
                        side=OrderSide.SELL,
                        trail_percent=config["sell"],
                        time_in_force=TimeInForce.DAY
                    )
                    trading_client.submit_order(req)
                    log(f"TRAILING SELL ACTIVADO {symbol} Profit +{profit_pct:.2f}% Entry {entry:.2f} Ahora {price:.2f} Trail {config['sell']}%")
                    send_telegram(f"TRAILING SELL {symbol} Activado +{profit_pct:.1f}% Trail {config['sell']}% - Asegurando ganancia")
                    STATE["trailing_active"][symbol] = "SELL"
                except Exception as e:
                    log(f"Error Trailing Sell {symbol}: {e}")

            # Venta clasica RSI >70 si no esta en trailing
            if rsi > RSI_SELL and not has_trailing:
                try:
                    trading_client.submit_order(MarketOrderRequest(symbol=symbol, qty=STATE["positions"][symbol]['qty'], side=OrderSide.SELL, time_in_force=TimeInForce.DAY))
                    log(f"VENTA RSI {symbol} RSI {rsi:.1f} Profit {profit_pct:.1f}%")
                    send_telegram(f"VENTA {symbol} RSI {rsi:.1f} Profit +{profit_pct:.1f}%")
                    del STATE["positions"][symbol]
                except Exception as e:
                    log(f"Error Venta RSI {symbol}: {e}")

    log(f"Scan terminado - Equity ${STATE['equity']:.2f}")

# ========== FLASK DASHBOARD ==========
HTML = """
<!DOCTYPE html>
<html><head><title>AURA V9.0 EXPERT</title>
<style>
body{font-family:Arial;background:#0f1117;color:#e0e0e0;padding:20px}
.card{background:#1a1d29;padding:20px;border-radius:12px;margin:10px 0;border-left:4px solid #00ff88}
h1{color:#00ff88}.green{color:#00ff88}.red{color:#ff4444}.yellow{color:#ffcc00}
table{width:100%;border-collapse:collapse;margin-top:10px}
th,td{padding:8px;border-bottom:1px solid #333;text-align:left}
</style></head><body>
<h1>🔥 AURA SYNAPSE V9.0 EXPERT - TRAILING STOP DOBLE</h1>
<div class="card">
<b>Equity:</b> <span class="green">${{equity}}</span> | <b>Cash:</b> ${{cash}} | <b>Positions:</b> {{pos_count}} | <b>Ultimo Scan:</b> {{last_run}}<br>
<b>Modo:</b> STOCKS ONLY + Trailing Buy {{buy_range}}% / Trailing Sell {{sell_range}}% | <b>EOD FLAT:</b> 15:45 NY / 14:45 BOG<br>
<b>Estado:</b> {% if flat_done %}<span class="yellow">LOCK EOD FLAT ejecutado hoy - En cash hasta mañana 09:30 NY</span>{% else %}<span class="green">Activo - Escaneando cada 5min</span>{% endif %}
</div>
<div class="card"><h3>Posiciones Activas + Trailing</h3>
<table><tr><th>Symbol</th><th>Qty</th><th>Entry</th><th>Trail</th></tr>
{% for s,p in positions.items() %}<tr><td>{{s}}</td><td>{{p.qty}}</td><td>${{p.entry_price}}</td><td>{{trailing.get(s,'-')}}</td></tr>{% endfor %}
</table></div>
<div class="card"><h3>Logs V9.0</h3>{% for l in logs %}<div>{{l}}</div>{% endfor %}</div>
<div class="card"><a href="/run" style="background:#00ff88;color:#000;padding:10px 20px;border-radius:8px;text-decoration:none">EJECUTAR SCAN AHORA</a> | Trailing Config: SPY 0.5/2% QQQ 0.75/2.5% AAPL/MSFT 1/3% NVDA/TSLA 1.5/4%</div>
</body></html>
"""

@app.route("/")
def dashboard():
    return render_template_string(HTML,
        equity=f"{STATE['equity']:.2f}", cash=f"{STATE['cash']:.2f}",
        pos_count=len(STATE["positions"]), last_run=STATE["last_run"] or "Nunca",
        positions=STATE["positions"], logs=STATE["logs"],
        trailing=STATE["trailing_active"], flat_done=STATE["flat_done_today"],
        buy_range="0.5-1.5", sell_range="2-4"
    )

@app.route("/run")
def run_now():
    threading.Thread(target=run_aura).start()
    return jsonify({"status":"AURA V9.0 scanning iniciado", "equity": STATE["equity"]})

@app.route("/health")
def health():
    return jsonify({"status":"ok","version":"V9.0 EXPERT TRAILING","equity":STATE["equity"],"positions":len(STATE["positions"])})

# Loop automatico cada 5 min (para UptimeRobot tambien pega a /run)
def loop():
    while True:
        try:
            run_aura()
        except Exception as e:
            log(f"Error loop: {e}")
        time.sleep(300)

threading.Thread(target=loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
