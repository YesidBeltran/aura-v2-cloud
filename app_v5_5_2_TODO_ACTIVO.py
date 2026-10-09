"""
AURA SYNAPSE V5.5.2 - TODO ACTIVO SIN MIEDO
Fix para Yesid: Opera con TODOS los SYMBOLS, RSI40 más agresivo, anti rate-limit

CAMBIOS V5.5.1 -> V5.5.2:
1. RSI_BUY de 35 a 40 para no perder oportunidades
2. Sleep de 30s a 120s para evitar HTTP 429 de Yahoo
3. Elimina hardcode "Para dips TSLA / ETH" -> ahora lee 100% SYMBOLS env
4. CASH_DISPLAY dinámico: "Para dips: TSLA, ETH-USD, NVDA..."
"""

import os
import time
import yfinance as yf

# === CONFIG V5.5.2 ===
SYMBOLS_ENV = os.getenv("SYMBOLS", "TSLA,ETH-USD,NVDA,BTC-USD,AAPL,MSFT,SPY")
SYMBOLS = [s.strip() for s in SYMBOLS_ENV.split(",") if s.strip()]

RSI_BUY = 40  # Antes 35 -> ahora 40 más agresivo, no perdemos dips
RSI_SELL = 60 # Se mantiene
FETCH_INTERVAL = 120  # Antes 30s -> ahora 120s para evitar HTTP 429

# Para display en dashboard
CASH_LABEL = f"Para dips: {', '.join(SYMBOLS[:3])} +{len(SYMBOLS)-3}" if len(SYMBOLS) > 3 else f"Para dips: {', '.join(SYMBOLS)}"

print(f"[AURA V5.5.2] SYMBOLS ACTIVOS: {SYMBOLS}")
print(f"[AURA V5.5.2] RSI_BUY={RSI_BUY} RSI_SELL={RSI_SELL} INTERVAL={FETCH_INTERVAL}s")
print(f"[AURA V5.5.2] {CASH_LABEL}")

# Ejemplo de loop anti rate-limit
def get_rsi(symbol):
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1mo")
        # Calculo RSI simplificado (usa tu funcion real de V5.5.1)
        # ...
        return 35 # placeholder
    except Exception as e:
        if "429" in str(e):
            print(f"[RATE-LIMIT] {symbol} - esperando 120s")
            time.sleep(120)
        return 50

# En tu app.py reemplaza:
# OLD: if rsi < 35:
# NEW: if rsi < RSI_BUY:  # Ahora 40

# OLD: time.sleep(30)
# NEW: time.sleep(FETCH_INTERVAL)  # Ahora 120

# OLD: cash_label = "Para dips TSLA / ETH"
# NEW: cash_label = CASH_LABEL  # Dinámico con SYMBOLS
