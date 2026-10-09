"""
AURA SYNAPSE V5.5.3 - TODO ACTIVO + GESTIÓN DE RIESGO
Deploy para Aura v2 - Render + Flask
RSI40, SL -2%, TP +3%, Position Sizing 10%, 7 símbolos activos
Equipo Yesid + Syna
"""

import os
import time
import yfinance as yf
from flask import Flask, render_template_string, jsonify
import threading
from datetime import datetime

app = Flask(__name__)

# === CONFIG V5.5.3 ===
SYMBOLS_ENV = os.getenv("SYMBOLS", "TSLA,ETH-USD,NVDA,BTC-USD,AAPL,MSFT,SPY")
SYMBOLS = [s.strip() for s in SYMBOLS_ENV.split(",") if s.strip()]

RSI_BUY = 40  # Entra en sobreventa
RSI_SELL = 60  # Salida por RSI
FETCH_INTERVAL = 120  # Anti HTTP 429 - cada 2 min

# === GESTIÓN DE RIESGO NUEVA ===
STOP_LOSS_PCT = 0.02  # -2% corta pérdida automático
TAKE_PROFIT_PCT = 0.03  # +3% asegura ganancia
MAX_POSITION_PCT = 0.10  # Máx 10% del cash por trade

# Estado global
state = {
    "trades": 59,
    "equity": 100085.49,
    "cash": 89724.49,
    "pnl_hoy": -146.81,
    "auto_on": True,
    "version": "V5.5.3",
    "symbols": SYMBOLS,
    "rsi_buy": RSI_BUY,
    "rsi_sell": RSI_SELL,
    "stop_loss": f"{STOP_LOSS_PCT*100:.0f}%",
    "take_profit": f"{TAKE_PROFIT_PCT*100:.0f}%",
    "last_update": datetime.now().strftime("%H:%M:%S")
}

# Guarda posiciones abiertas: {"TSLA": {"entry": 250.5, "qty": 10}}
open_positions = {}

print(f"[AURA V5.5.3] SYMBOLS ACTIVOS: {SYMBOLS}")
print(f"[AURA V5.5.3] RSI_BUY={RSI_BUY} RSI_SELL={RSI_SELL} INTERVAL={FETCH_INTERVAL}s")
print(f"[AURA V5.5.3] SL={STOP_LOSS_PCT*100:.0f}% TP={TAKE_PROFIT_PCT*100:.0f}% POS_SIZE={MAX_POSITION_PCT*100:.0f}%")
print(f"[AURA V5.5.3] Para dips: {', '.join(SYMBOLS)}")

def calculate_rsi(symbol):
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="14d")
        if len(hist) < 14: return 50
        delta = hist['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return round(rsi.iloc[-1], 2)
    except Exception as e:
        print(f"[ERROR] {symbol}: {e}")
        if "429" in str(e): time.sleep(FETCH_INTERVAL)
        return 50

def trading_loop():
    while True:
        try:
            for symbol in SYMBOLS:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="14d")
                if len(hist) < 14: 
                    time.sleep(5)
                    continue
                
                current_price = hist['Close'].iloc[-1]
                rsi = calculate_rsi(symbol)
                print(f"[AURA] {symbol} RSI: {rsi} | Precio: ${current_price:.2f}")

                # === GESTIÓN DE POSICIONES ABIERTAS ===
                if symbol in open_positions:
                    entry = open_positions[symbol]["entry"]
                    qty = open_positions[symbol]["qty"]
                    pnl_pct = (current_price - entry) / entry
                    pnl_usd = (current_price - entry) * qty
                    
                    # Take-Profit +3%
                    if pnl_pct >= TAKE_PROFIT_PCT:
                        print(f"[TP +3%] {symbol} Venta {qty}x @ ${current_price:.2f} | Ganancia: +{pnl_pct*100:.1f}% = ${pnl_usd:.2f}")
                        state["cash"] += current_price * qty
                        state["pnl_hoy"] += pnl_usd
                        del open_positions[symbol]
                        state["trades"] += 1
                    
                    # Stop-Loss -2%
                    elif pnl_pct <= -STOP_LOSS_PCT:
                        print(f"[SL -2%] {symbol} Venta {qty}x @ ${current_price:.2f} | Pérdida: {pnl_pct*100:.1f}% = ${pnl_usd:.2f}")
                        state["cash"] += current_price * qty
                        state["pnl_hoy"] += pnl_usd
                        del open_positions[symbol]
                        state["trades"] += 1
                    
                    # RSI Sell 60
                    elif rsi > RSI_SELL:
                        print(f"[RSI 60] {symbol} Venta {qty}x @ ${current_price:.2f} | P/L: {pnl_pct*100:.1f}% = ${pnl_usd:.2f}")
                        state["cash"] += current_price * qty
                        state["pnl_hoy"] += pnl_usd
                        del open_positions[symbol]
                        state["trades"] += 1
                
                # === COMPRA: RSI < 40 ===
                elif rsi < RSI_BUY and symbol not in open_positions:
                    max_cash_por_trade = state["cash"] * MAX_POSITION_PCT
                    if state["cash"] > max_cash_por_trade and max_cash_por_trade > current_price:
                        qty = int(max_cash_por_trade / current_price)
                        cost = qty * current_price
                        if qty > 0:
                            open_positions[symbol] = {"entry": current_price, "qty": qty}
                            state["cash"] -= cost
                            print(f"[COMPRA] {symbol} {qty}x @ ${current_price:.2f} | RSI {rsi} | Costo: ${cost:.2f}")
                            state["trades"] += 1
                
                time.sleep(5)
            
            # Actualiza equity = cash + valor posiciones abiertas
            equity_posiciones = sum([open_positions[s]["qty"] * yf.Ticker(s).history(period="1d")['Close'].iloc[-1] 
                                     for s in open_positions]) if open_positions else 0
            state["equity"] = round(state["cash"] + equity_posiciones, 2)
            state["last_update"] = datetime.now().strftime("%H:%M:%S COL")
            time.sleep(FETCH_INTERVAL)
            
        except Exception as e:
            print(f"[ERROR LOOP] {e}")
            time.sleep(60)

@app.route('/')
def dashboard():
    cash_label = f"Para dips: {', '.join(SYMBOLS)}"
    posiciones_html = "<br>".join([f"{s}: {p['qty']}x @ ${p['entry']:.2f}" for s, p in open_positions.items()]) if open_positions else "Sin posiciones abiertas"
    
    html = f"""
    <html>
    <head><title>AURA SYNAPSE V5.5.3</title></head>
    <body style="background:#1a1a1a;color:#0f0;font-family:monospace;padding:20px;">
        <h1>AURA SYNAPSE {state['version']}</h1>
        <h2>Equity Total: ${state['equity']:,.2f}</h2>
        <p>Cash: ${state['cash']:,.2f}</p>
        <p><b>{cash_label}</b></p>
        <p>P/L Hoy: ${state['pnl_hoy']:,.2f}</p>
        <p>Trades: {state['trades']}</p>
        <p>RSI Buy: {RSI_BUY} | RSI Sell: {RSI_SELL}</p>
        <p>Stop-Loss: {state['stop_loss']} | Take-Profit: {state['take_profit']}</p>
        <p>Posiciones: {len(open_positions)} | {posiciones_html}</p>
        <p>Estado: AUTO ON | {state['last_update']}</p>
        <p>SYMBOLS: {', '.join(SYMBOLS)}</p>
    </body>
    </html>
    """
    return html

@app.route('/api/status')
def status():
    return jsonify({**state, "open_positions": open_positions})

if __name__ == '__main__':
    thread = threading.Thread(target=trading_loop, daemon=True)
    thread.start()
    port = int(os.getenv("PORT", 10000))
    print(f"[AURA] Serving Flask app on port {port}")
    app.run(host='0.0.0.0', port=port)
