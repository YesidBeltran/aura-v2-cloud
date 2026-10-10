import os
import time
import pytz
import yfinance as yf
import pandas as pd
from datetime import datetime
from flask import Flask, jsonify, render_template_string
from alpaca_trade_api.rest import REST, TimeFrame
from ta.momentum import RSIIndicator
import threading

app = Flask(__name__)

# ===== CONFIG TEAM YESID + SYNA V6.2 =====
API_KEY = os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("APCA_API_SECRET_KEY")
BASE_URL = os.getenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")

api = REST(API_KEY, API_SECRET, BASE_URL, api_version='v2')

# DECISIÓN TEAM: 5 EXPERTAS SOLO ALPACA
SYMBOLS = ["AAPL", "TSLA", "NVDA", "MSFT", "SPY"]
RSI_BUY = 40
RSI_SELL = 60
SL_PERCENT = -2.0
TP_PERCENT = 3.0
BP_POR_TRADE = 0.05  # 5% del buying power
MAX_POSICIONES = 5   # Una por símbolo
HORA_INICIO = "08:30"  # COL
HORA_FIN = "15:00"     # COL
SCAN_INTERVAL = 60     # segundos

# Estado global
bot_status = {
    "running": False,
    "last_scan": None,
    "last_heartbeat": None,
    "logs": []
}

def log(msg):
    timestamp = datetime.now(pytz.timezone('America/Bogota')).strftime("%H:%M:%S")
    entry = f"[{timestamp}] {msg}"
    bot_status["logs"].append(entry)
    if len(bot_status["logs"]) > 50:
        bot_status["logs"] = bot_status["logs"][-50:]
    print(entry)

def es_horario_trading():
    tz = pytz.timezone('America/Bogota')
    ahora = datetime.now(tz)
    if ahora.weekday() >= 5:  # 5=sabado, 6=domingo
        return False
    hora_actual = ahora.strftime("%H:%M")
    return HORA_INICIO <= hora_actual <= HORA_FIN

def calcular_rsi(symbol):
    try:
        data = yf.download(symbol, period="5d", interval="5m", progress=False)
        if data.empty or len(data) < 15:
            return None
        rsi = RSIIndicator(close=data['Close'], window=14).rsi()
        return round(rsi.iloc[-1], 2)
    except Exception as e:
        log(f"Error RSI {symbol}: {str(e)}")
        return None

def obtener_posiciones_actuales():
    try:
        positions = api.list_positions()
        return {p.symbol: float(p.qty) for p in positions}
    except:
        return {}

def ejecutar_compra(symbol, rsi):
    try:
        account = api.get_account()
        bp = float(account.buying_power)
        precio = float(api.get_latest_trade(symbol).price)
        qty = int((bp * BP_POR_TRADE) / precio)
        
        if qty < 1:
            log(f"{symbol}: Qty insuficiente. BP: ${bp:.2f}")
            return
            
        api.submit_order(
            symbol=symbol,
            qty=qty,
            side='buy',
            type='market',
            time_in_force='day',
            order_class='bracket',
            take_profit={'limit_price': round(precio * (1 + TP_PERCENT/100), 2)},
            stop_loss={'stop_price': round(precio * (1 + SL_PERCENT/100), 2)}
        )
        log(f"COMPRA {symbol}: {qty} @ ${precio} | RSI: {rsi} | TP: +{TP_PERCENT}% SL: {SL_PERCENT}%")
    except Exception as e:
        log(f"Error compra {symbol}: {str(e)}")

def escanear_mercado():
    if not es_horario_trading():
        bot_status["last_scan"] = "Mercado cerrado"
        return
    
    posiciones = obtener_posiciones_actuales()
    log(f"ESCANEO RSI - Posiciones: {len(posiciones)}/{MAX_POSICIONES}")
    
    for symbol in SYMBOLS:
        if symbol in posiciones:
            continue  # Ya tenemos posición
            
        rsi = calcular_rsi(symbol)
        if rsi is None:
            continue
            
        log(f"{symbol} RSI: {rsi}")
        
        if rsi <= RSI_BUY:
            log(f"SEÑAL COMPRA {symbol} - RSI {rsi} <= {RSI_BUY}")
            ejecutar_compra(symbol, rsi)
        
        time.sleep(2)  # Rate limit yfinance
    
    bot_status["last_scan"] = datetime.now(pytz.timezone('America/Bogota')).strftime("%H:%M:%S")

def loop_principal():
    bot_status["running"] = True
    log("AURA V6.2 SYNAPSE TEAM EDITION - INICIADA")
    log(f"Activos expertos: {', '.join(SYMBOLS)}")
    log(f"Horario: {HORA_INICIO}-{HORA_FIN} COL Lun-Vie")
    
    while bot_status["running"]:
        bot_status["last_heartbeat"] = datetime.now(pytz.timezone('America/Bogota')).strftime("%H:%M:%S")
        try:
            escanear_mercado()
        except Exception as e:
            log(f"Error en loop: {str(e)}")
        time.sleep(SCAN_INTERVAL)

@app.route('/')
def dashboard():
    try:
        account = api.get_account()
        positions = api.list_positions()
        equity = float(account.equity)
        cash = float(account.cash)
        bp = float(account.buying_power)
    except:
        equity = cash = bp = 0
        positions = []
    
    html = """
    <!DOCTYPE html>
    <html><head><meta charset="UTF-8"><title>AURA V6.2</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body{background:#0a0a0a;color:#fff;font-family:Arial;padding:20px}
        .card{background:#1a1a1a;padding:20px;margin:10px 0;border-radius:10px}
        .green{color:#00ff88}.blue{color:#00aaff}.yellow{color:#ffaa00}
        .badge{background:#00ff88;color:#000;padding:5px 10px;border-radius:5px;font-size:12px}
    </style></head><body>
    <h1>⚡ SYNAPSE V6.2 <span class="badge">TEAM EDITION</span></h1>
    <div class="card">
        <h2>ALPACA REAL <span class="green">● CONECTADO</span></h2>
        <p>AUTO TRADING <span class="green">● ON</span></p>
    </div>
    <div class="card">
        <h3>EQUITY TOTAL</h3>
        <h2 class="blue">${{ "%.2f"|format(equity) }}</h2>
    </div>
    <div class="card">
        <h3>CASH + BUYING POWER</h3>
        <h2>${{ "%.2f"|format(cash) }}</h2>
        <p class="green">BP: ${{ "%.2f"|format(bp) }}</p>
    </div>
    <div class="card">
        <h3>POSICIONES • TRADES</h3>
        <h2 class="yellow">{{ positions|length }} posiciones</h2>
        <p>ALPACA REAL AUTO • RSI {{ rsi_buy }}/{{ rsi_sell }}</p>
    </div>
    <div class="card">
        <h3>ACTIVOS EXPERTOS — AUTO SCAN 60S</h3>
        <p>{{ symbols|join(' • ') }}</p>
    </div>
    <div class="card">
        <h3>LOGS EN VIVO</h3>
        {% for log in logs[-10:] %}
        <p style="font-size:12px">{{ log }}</p>
        {% endfor %}
    </div>
    <script>setTimeout(()=>location.reload(), 30000)</script>
    </body></html>
    """
    return render_template_string(html, 
        equity=equity, cash=cash, bp=bp, positions=positions,
        symbols=SYMBOLS, rsi_buy=RSI_BUY, rsi_sell=RSI_SELL,
        logs=bot_status["logs"])

@app.route('/api/debug')
def debug():
    try:
        account = api.get_account()
        return jsonify({
            "status": "ALPACA_REAL",
            "equity": float(account.equity),
            "buying_power": float(account.buying_power),
            "last_heartbeat": bot_status["last_heartbeat"],
            "last_scan": bot_status["last_scan"],
            "symbols": SYMBOLS,
            "running": bot_status["running"]
        })
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/api/start')
def start():
    if not bot_status["running"]:
        thread = threading.Thread(target=loop_principal, daemon=True)
        thread.start()
    return jsonify({"status": "started"})

if __name__ == '__main__':
    # Auto-start en Render
    thread = threading.Thread(target=loop_principal, daemon=True)
    thread.start()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
