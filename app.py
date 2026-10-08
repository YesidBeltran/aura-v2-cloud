"""
AURA SYNAPSE V5.1 FIX - COMPRA + VENTA + MEMORIA
Fix: sin triple quotes anidadas
"""
import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo
import yfinance as yf

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"
AUTO_TRADING = os.getenv("AUTO_TRADING", "false").lower() == "true"

SYMBOLS = ["AAPL", "TSLA", "NVDA", "SPY", "MSFT", "BTC-USD", "ETH-USD"]
MEMORIA_FILE = "aura_memoria.json"

trading_client = None
if ALPACA_API_KEY and ALPACA_SECRET_KEY:
    trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=ALPACA_PAPER)

def cargar_memoria():
    if os.path.exists(MEMORIA_FILE):
        try:
            with open(MEMORIA_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return {"trades": [], "inicio": datetime.now(ZoneInfo("America/Bogota")).isoformat(), "equity_historico": []}

def guardar_memoria(memoria):
    with open(MEMORIA_FILE, "w") as f:
        json.dump(memoria, f, indent=2)

def registrar_trade(memoria, simbolo, lado, precio, rsi, cash_antes, motivo):
    trade = {
        "fecha": datetime.now(ZoneInfo("America/Bogota")).isoformat(),
        "simbolo": simbolo,
        "lado": lado,
        "precio": float(precio),
        "rsi": float(rsi),
        "cash_antes": float(cash_antes),
        "motivo": motivo
    }
    memoria["trades"].append(trade)
    guardar_memoria(memoria)
    return trade

def get_rsi(symbol, period=14):
    try:
        ticker = yf.Ticker(symbol)
        data = ticker.history(period="1mo")
        if len(data) < period:
            return 50, float(data['Close'].iloc[-1])
        delta = data['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1]), float(data['Close'].iloc[-1])
    except Exception as e:
        print(f"Error RSI {symbol}: {e}")
        return 50, 0

def get_positions():
    if not trading_client:
        return {}
    try:
        positions = trading_client.get_all_positions()
        return {p.symbol: float(p.qty) for p in positions}
    except:
        return {}

def get_account():
    if not trading_client:
        return None
    try:
        return trading_client.get_account()
    except Exception as e:
        print(f"Error account: {e}")
        return None

def ejecutar_orden(simbolo_real, lado, qty=1):
    if not trading_client or not AUTO_TRADING:
        print(f"[SIMULADO] {lado} {qty} {simbolo_real}")
        return None
    try:
        sym = simbolo_real.replace("-", "/") if "USD" in simbolo_real else simbolo_real
        order = MarketOrderRequest(
            symbol=sym,
            qty=qty,
            side=OrderSide.BUY if lado == "COMPRAR" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY
        )
        result = trading_client.submit_order(order)
        print(f"REAL: {lado} {qty} {sym} @ market - ID {result.id}")
        return result
    except Exception as e:
        print(f"Error orden {simbolo_real} {lado}: {e}")
        return None

def ciclo_trading():
    memoria = cargar_memoria()
    account = get_account()
    positions = get_positions()
    
    cash = float(account.cash) if account else 85000
    equity = float(account.equity) if account else 100200
    
    memoria["equity_historico"].append({
        "fecha": datetime.now(ZoneInfo("America/Bogota")).isoformat(),
        "equity": equity,
        "cash": cash
    })
    memoria["equity_historico"] = memoria["equity_historico"][-500:]
    
    logs = []
    for sym in SYMBOLS:
        rsi, precio = get_rsi(sym)
        has_position = False
        pos_qty = 0
        for k,v in positions.items():
            if sym.replace("-","") in k or sym.split("-")[0] in k:
                has_position = True
                pos_qty = v
                break
        
        if rsi < 35:
            accion = "COMPRAR FUERTE" if rsi < 30 else "COMPRAR"
            if cash > precio and precio > 0:
                if not has_position or pos_qty < 5:
                    motivo = f"RSI {rsi:.1f} sobreventa -> COMPRAR con ${cash:.2f} disponible"
                    logs.append(f"🟢 {sym} ${precio:.2f} RSI {rsi:.1f} -> {accion} | {motivo}")
                    ejecutar_orden(sym, "COMPRAR", 1)
                    registrar_trade(memoria, sym, "COMPRAR", precio, rsi, cash, motivo)
                    cash -= precio
                else:
                    logs.append(f"⏸️ {sym} ${precio:.2f} RSI {rsi:.1f} -> Ya tiene posicion")
            else:
                logs.append(f"⚠️ {sym} ${precio:.2f} RSI {rsi:.1f} -> Quiere COMPRAR pero cash insuficiente")
        elif rsi > 60:
            accion = "VENDER FUERTE" if rsi > 70 else "VENDER"
            if has_position:
                motivo = f"RSI {rsi:.1f} sobrecompra -> VENDER posicion {pos_qty}"
                logs.append(f"🔴 {sym} ${precio:.2f} RSI {rsi:.1f} -> {accion} | {motivo}")
                ejecutar_orden(sym, "VENDER", 1)
                registrar_trade(memoria, sym, "VENDER", precio, rsi, cash, motivo)
            else:
                logs.append(f"⏸️ {sym} ${precio:.2f} RSI {rsi:.1f} -> {accion} pero no tiene posicion")
        else:
            logs.append(f"⚪ {sym} ${precio:.2f} RSI {rsi:.1f} -> NEUTRAL")
    
    guardar_memoria(memoria)
    return logs, memoria, equity, cash

from flask import Flask, jsonify, render_template_string
app = Flask(__name__)

HTML_V5 = '''
<!DOCTYPE html>
<html>
<head>
<title>AURA V5.1 - COMPRA+VENTA+MEMORIA</title>
<style>
body{background:#0a0a0f;color:#e0e0e0;font-family:monospace;padding:20px}
.card{background:#15151f;border-radius:12px;padding:15px;margin:10px;display:inline-block;min-width:220px}
.log{font-size:12px;margin:4px 0}
</style>
<meta http-equiv="refresh" content="30">
</head>
<body>
<h2 style="color:#00ff88">AURA SYNAPSE V5.1 - COMPRA + VENTA + MEMORIA FIX</h2>
<p>AUTO-TRADING {{ 'ON' if auto else 'OFF' }} | {{ fecha }} COL | Trades: {{ trades_count }}</p>
<div class="card">EQUITY<br><span style="font-size:26px">${{ "%.2f"|format(equity) }}</span></div>
<div class="card">CASH<br><span style="font-size:26px;color:#00aaff">${{ "%.2f"|format(cash) }}</span></div>
<div class="card">Creadores<br><b>Yesid + Syna</b><br>Memoria: {{ trades_count }} trades</div>
<h3>MERCADO REAL - V5.1 Logica: Compra RSI<35, Vende RSI>60</h3>
{% for l in logs %}
<div class="log">{{ l }}</div>
{% endfor %}
<h3>MEMORIA - Ultimos 7 trades</h3>
{% for t in memoria_trades[::-1][:7] %}
<div class="log">{{ t.fecha[11:19] }} - {{ t.lado }} {{ t.simbolo }} @ ${{ t.precio }} RSI {{ "%.1f"|format(t.rsi) }} - {{ t.motivo }}</div>
{% endfor %}
</body>
</html>
'''

@app.route("/")
def dashboard():
    logs, memoria, equity, cash = ciclo_trading()
    return render_template_string(HTML_V5, 
        logs=logs, 
        memoria_trades=memoria["trades"],
        equity=equity,
        cash=cash,
        trades_count=len(memoria["trades"]),
        fecha=datetime.now(ZoneInfo("America/Bogota")).strftime("%Y-%m-%d %H:%M:%S"),
        auto=AUTO_TRADING
    )

@app.route("/memoria")
def memoria_json():
    return jsonify(cargar_memoria())

if __name__ == "__main__":
    print("AURA V5.1 Iniciando - Facatativa -> Render")
    print(f"AUTO_TRADING={AUTO_TRADING}")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
