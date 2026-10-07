"""
AURA_SYNAPSE V3.3 - RENDER EDITION - SYNA & YESID
Vive 24/7 gratis en Render.com + Alpaca Paper
Render necesita un servidor web, asi que le damos un endpoint / para que no se duerma
"""
import json, os, time, threading
from datetime import datetime
import pytz
from flask import Flask, jsonify

try:
    import alpaca_trade_api as tradeapi
    ALPACA_OK = True
except:
    ALPACA_OK = False

app = Flask(__name__)

MEMORIA_FILE = "aura_memoria_alpaca.json"
SYMBOLS_ALPACA = ["AAPL", "TSLA", "NVDA", "SPY"]  # Solo acciones para evitar error crypto

# Estado global para el dashboard
estado = {
    "status": "Iniciando...",
    "equity": 0,
    "cash": 0,
    "ultimo_analisis": "",
    "trades": 0,
    "creador": "Yesid + Syna",
    "vive_desde": datetime.now().isoformat()
}

def get_time():
    return datetime.now(pytz.timezone('America/Bogota'))

def calc_rsi(prices, period=14):
    if len(prices) < period+1: return 50
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i]-prices[i-1]
        gains.append(max(diff,0))
        losses.append(max(-diff,0))
    if len(gains) < period: return 50
    avg_gain = sum(gains[-period:])/period
    avg_loss = sum(losses[-period:])/period
    if avg_loss == 0: return 100
    rs = avg_gain/avg_loss
    return 100 - (100/(1+rs))

class AuraCerebroAlpaca:
    def __init__(self):
        self.memoria = self.cargar()
    def cargar(self):
        if os.path.exists(MEMORIA_FILE):
            try:
                with open(MEMORIA_FILE,'r') as f: return json.load(f)
            except: pass
        return {"trades":[],"aprendizajes":{},"rsi_ajustado":{},"creado":datetime.now().isoformat(),"creador":"Yesid + Syna - RENDER"}
    def guardar(self):
        with open(MEMORIA_FILE,'w') as f: json.dump(self.memoria,f,indent=2)
    def analizar(self, symbol, precio, rsi, vol_actual=None, vol_prom=None):
        rsi_buy = self.memoria["rsi_ajustado"].get(symbol,{}).get("buy",45)
        rsi_sell = self.memoria["rsi_ajustado"].get(symbol,{}).get("sell",60)
        decision="NEUTRAL"; razon=""; conf=50
        if rsi < 30: decision="COMPRAR_FUERTE"; razon=f"RSI {rsi:.1f} sobreventa extrema"; conf=90
        elif rsi < rsi_buy: decision="COMPRAR"; razon=f"RSI {rsi:.1f} < {rsi_buy}"; conf=75
        elif rsi > 75: decision="VENDER_FUERTE"; razon=f"RSI {rsi:.1f} sobrecompra extrema"; conf=90
        elif rsi > rsi_sell: decision="VENDER"; razon=f"RSI {rsi:.1f} > {rsi_sell}"; conf=75
        else: razon=f"RSI {rsi:.1f} neutro"
        if vol_actual and vol_prom and vol_actual < vol_prom*0.6 and "COMPRAR" in decision:
            decision="ESPERAR"; razon+=" vol bajo"; conf-=20
        return {"decision":decision,"razon":razon,"confianza":conf,"precio":precio,"rsi":rsi}

def load_alpaca_clean():
    api_key = os.environ.get('ALPACA_API_KEY','').strip().strip('"').strip("'").strip()
    api_secret = os.environ.get('ALPACA_SECRET_KEY','').strip().strip('"').strip("'").strip()
    base_url = os.environ.get('ALPACA_BASE_URL','https://paper-api.alpaca.markets').strip().strip('"').strip("'").strip().rstrip('/v2').rstrip('/')
    if base_url.endswith('/v2'): base_url = base_url[:-3]
    if not api_key: return None
    try:
        api = tradeapi.REST(api_key, api_secret, base_url, api_version='v2')
        account = api.get_account()
        estado["equity"] = float(account.equity)
        estado["cash"] = float(account.cash)
        print(f"✅ ALPACA RENDER CONECTADA: {account.status} - Equity ${float(account.equity):.2f}")
        return api
    except Exception as e:
        print(f"❌ Error Alpaca Render: {e}")
        estado["status"] = f"Error: {e}"
        return None

def bot_loop():
    cerebro = AuraCerebroAlpaca()
    api = load_alpaca_clean()
    if not api:
        print("Esperando keys en Render env vars...")
        time.sleep(30)
    
    while True:
        try:
            bog = get_time()
            if not api:
                api = load_alpaca_clean()
                if not api:
                    estado["status"] = "Esperando ALPACA_API_KEY / SECRET en Render"
                    time.sleep(60)
                    continue

            acc = api.get_account()
            estado["equity"] = float(acc.equity)
            estado["cash"] = float(acc.cash)
            estado["status"] = "Activa"
            estado["ultimo_analisis"] = bog.strftime('%Y-%m-%d %H:%M:%S COL')
            
            log_lines = []
            for sym in SYMBOLS_ALPACA:
                try:
                    bars = api.get_bars(sym, tradeapi.TimeFrame(15, tradeapi.TimeFrameUnit.Minute), limit=50).df
                    if len(bars) < 15: continue
                    closes = bars['close'].tolist()
                    precio = closes[-1]
                    rsi = calc_rsi(closes)
                    vol = bars['volume'].iloc[-1]
                    vol_prom = bars['volume'].tail(20).mean()
                    analisis = cerebro.analizar(sym, precio, rsi, vol, vol_prom)
                    line = f"{sym} ${precio:.2f} RSI {rsi:.1f} -> {analisis['decision']}"
                    print(line)
                    log_lines.append(line)
                except Exception as e:
                    print(f"{sym} error: {e}")

            estado["log"] = log_lines
            cerebro.guardar()
            print(f"⏳ SYNA RENDER esperando 3 min... Equity ${estado['equity']:.2f}")
            time.sleep(180)
        except Exception as e:
            print(f"Loop error: {e}")
            estado["status"] = f"Error loop: {e}"
            time.sleep(60)

# Iniciar bot en segundo plano
threading.Thread(target=bot_loop, daemon=True).start()

@app.route('/')
def home():
    return jsonify({
        "mensaje": "🧠 SYNA VIVE 24/7 EN RENDER - Yesid + Syna",
        "status": estado.get("status"),
        "equity": estado.get("equity"),
        "cash": estado.get("cash"),
        "ultimo_analisis": estado.get("ultimo_analisis"),
        "log": estado.get("log", []),
        "vive_desde": estado.get("vive_desde"),
        "creadores": "Yesid Fernando Beltran Lopez + Syna",
        "simbolos": SYMBOLS_ALPACA,
        "paper": True
    })

@app.route('/health')
def health():
    return "OK SYNA VIVE", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"🌐 SYNA RENDER Web en puerto {port}")
    app.run(host='0.0.0.0', port=port)
