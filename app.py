"""
AURA SYNAPSE V5.5.2 - TODO ACTIVO SIN MIEDO
Versión completa para Render + Flask
Fix para Yesid: RSI40, anti rate-limit 120s, 7 símbolos activos
"""

import os
import time
import yfinance as yf
from flask import Flask, render_template_string, jsonify
import threading
from datetime import datetime

app = Flask(__name__)

# === CONFIG V5.5.2 ===
SYMBOLS_ENV = os.getenv("SYMBOLS", "TSLA,ETH-USD,NVDA,BTC-USD,AAPL,MSFT,SPY")
SYMBOLS = [s.strip() for s in SYMBOLS_ENV.split(",") if s.strip()]

RSI_BUY = 40  # V5.5.2: Antes 35 -> ahora 40 más agresivo
RSI_SELL = 60
FETCH_INTERVAL = 120  # V5.5.2: Antes 30s -> ahora 120s para evitar HTTP 429

# Estado global
state = {
    "trades": 12,
    "equity": 100085.49,
    "cash": 89724.49,
    "pnl_hoy": -146.81,
    "auto_on": True,
    "version": "V5.5.2",
    "symbols": SYMBOLS,
    "rsi_buy": RSI_BUY,
    "last_update": datetime.now().strftime("%H:%M:%S")
}

print(f"[AURA V5.5.2] SYMBOLS ACTIVOS: {SYMBOLS}")
print(f"[AURA V5.5.2] RSI_BUY={RSI_BUY} RSI_SELL={RSI_SELL} INTERVAL={FETCH_INTERVAL}s")
print(f"[AURA V5.5.2] Para dips: {', '.join(SYMBOLS[:3])} +{len(SYMBOLS)-3}" if len(SYMBOLS) > 3 else f"[AURA V5.5.2] Para dips: {', '.join(SYMBOLS)}")

def calculate_rsi(symbol):
    """Calcula RSI para un símbolo"""
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="14d")
        if len(hist) < 14:
            return 50
        delta = hist['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return round(rsi.iloc[-1], 2)
    except Exception as e:
        print(f"[ERROR] {symbol}: {e}")
        if "429" in str(e):
            print(f"[RATE-LIMIT] Esperando {FETCH_INTERVAL}s...")
            time.sleep(FETCH_INTERVAL)
        return 50

def trading_loop():
    """Loop principal de trading - corre en background"""
    while True:
        try:
            for symbol in SYMBOLS:
                rsi = calculate_rsi(symbol)
                print(f"[AURA] {symbol} RSI: {rsi}")
                
                if rsi < RSI_BUY and state["cash"] > 1000:
                    print(f"[COMPRA] {symbol} RSI {rsi} < {RSI_BUY}")
                    state["trades"] += 1
                    # Aquí va tu lógica de compra real si la tienes
                    
                time.sleep(5)  # Pausa entre símbolos para evitar rate-limit
                
            state["last_update"] = datetime.now().strftime("%H:%M:%S COL")
            time.sleep(FETCH_INTERVAL)
            
        except Exception as e:
            print(f"[ERROR LOOP] {e}")
            time.sleep(60)

@app.route('/')
def dashboard():
    cash_label = f"Para dips: {', '.join(SYMBOLS)}"
    html = f"""
    <html>
    <head><title>AURA SYNAPSE V5.5.2</title></head>
    <body style="background:#1a1a1a;color:#fff;font-family:Arial;padding:20px;">
        <h1>AURA SYNAPSE {state['version']}</h1>
        <h2>Equity Total: ${state['equity']:,.2f}</h2>
        <p>Cash: ${state['cash']:,.2f}</p>
        <p><b>{cash_label}</b></p>
        <p>P/L Hoy: ${state['pnl_hoy']:,.2f}</p>
        <p>Trades: {state['trades']}</p>
        <p>RSI Buy: {RSI_BUY} | RSI Sell: {RSI_SELL}</p>
        <p>Estado: {'AUTO ON' if state['auto_on'] else 'AUTO OFF'} | {state['last_update']}</p>
        <p>SYMBOLS: {', '.join(SYMBOLS)}</p>
    </body>
    </html>
    """
    return html

@app.route('/api/status')
def status():
    return jsonify(state)

if __name__ == '__main__':
    # Arranca el loop de trading en background
    thread = threading.Thread(target=trading_loop, daemon=True)
    thread.start()
    
    # Arranca Flask - ESTA LÍNEA EVITA EL "Application exited early"
    port = int(os.getenv("PORT", 10000))
    print(f"[AURA] Serving Flask app on port {port}")
    app.run(host='0.0.0.0', port=port)
