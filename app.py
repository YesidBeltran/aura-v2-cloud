"""
AURA_SYNAPSE V3.4 - RENDER + ALPACA-PY (Python 3.11) - SYNA & YESID
Fix PyYAML error - usa alpaca-py nueva, no alpaca-trade-api vieja
"""
import json, os, time, threading
from datetime import datetime
import pytz
from flask import Flask, jsonify

try:
    from alpaca.trading.client import TradingClient
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    ALPACA_OK = True
except Exception as e:
    print(f"Alpaca import error: {e}")
    ALPACA_OK = False

app = Flask(__name__)

SYMBOLS = ["AAPL", "TSLA", "NVDA", "SPY"]
estado = {
    "status": "Iniciando...",
    "equity": 0,
    "cash": 0,
    "ultimo_analisis": "",
    "log": [],
    "vive_desde": datetime.now().isoformat(),
    "creador": "Yesid + Syna"
}

def get_time():
    return datetime.now(pytz.timezone('America/Bogota'))

def calc_rsi(prices, period=14):
    if len(prices) < period+1: return 50
    gains = []; losses = []
    for i in range(1, len(prices)):
        d = prices[i]-prices[i-1]
        gains.append(max(d,0)); losses.append(max(-d,0))
    if len(gains) < period: return 50
    avg_gain = sum(gains[-period:])/period
    avg_loss = sum(losses[-period:])/period
    if avg_loss == 0: return 100
    rs = avg_gain/avg_loss
    return 100 - (100/(1+rs))

def load_alpaca():
    api_key = os.environ.get('ALPACA_API_KEY','').strip()
    api_secret = os.environ.get('ALPACA_SECRET_KEY','').strip()
    if not api_key:
        estado["status"] = "Esperando ALPACA_API_KEY"
        return None, None
    try:
        trading = TradingClient(api_key, api_secret, paper=True)
        data_client = StockHistoricalDataClient(api_key, api_secret)
        acc = trading.get_account()
        estado["equity"] = float(acc.equity)
        estado["cash"] = float(acc.cash)
        estado["status"] = "Activa"
        print(f"✅ ALPACA-PY CONECTADA: {acc.status} Equity ${float(acc.equity):.2f}")
        return trading, data_client
    except Exception as e:
        print(f"❌ Error Alpaca: {e}")
        estado["status"] = f"Error: {e}"
        return None, None

def bot_loop():
    trading, data_client = load_alpaca()
    while True:
        try:
            if not trading:
                trading, data_client = load_alpaca()
                if not trading:
                    time.sleep(60); continue
            bog = get_time()
            acc = trading.get_account()
            estado["equity"] = float(acc.equity)
            estado["cash"] = float(acc.cash)
            estado["ultimo_analisis"] = bog.strftime('%Y-%m-%d %H:%M:%S COL')
            
            logs = []
            for sym in SYMBOLS:
                try:
                    req = StockBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Minute, limit=100)
                    bars = data_client.get_stock_bars(req).df
                    # Filtrar solo simbolo
                    if sym in bars.index.get_level_values(0):
                        df = bars.loc[sym]
                    else:
                        df = bars
                    if len(df) < 20: continue
                    closes = df['close'].tolist()
                    precio = closes[-1]
                    rsi = calc_rsi(closes)
                    if rsi < 30: dec = "COMPRAR_FUERTE"
                    elif rsi < 45: dec = "COMPRAR"
                    elif rsi > 75: dec = "VENDER_FUERTE"
                    elif rsi > 60: dec = "VENDER"
                    else: dec = "NEUTRAL"
                    line = f"{sym} ${precio:.2f} RSI {rsi:.1f} -> {dec}"
                    print(line); logs.append(line)
                except Exception as e:
                    print(f"{sym} err: {e}")
                    logs.append(f"{sym} error {e}")
            estado["log"] = logs
            print(f"⏳ SYNA V3.4 esperando 3 min - Equity ${estado['equity']:.2f}")
            time.sleep(180)
        except Exception as e:
            print(f"Loop err: {e}"); time.sleep(60)

threading.Thread(target=bot_loop, daemon=True).start()

@app.route('/')
def home():
    return jsonify({
        "mensaje": "🧠 SYNA VIVE 24/7 EN RENDER V3.4 - Yesid + Syna",
        "status": estado.get("status"),
        "equity": estado.get("equity"),
        "cash": estado.get("cash"),
        "ultimo_analisis": estado.get("ultimo_analisis"),
        "log": estado.get("log", []),
        "vive_desde": estado.get("vive_desde"),
        "simbolos": SYMBOLS,
        "paper": True,
        "url": "https://aura-synapse-yesid.onrender.com"
    })

@app.route('/health')
def health():
    return "OK SYNA VIVE", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
