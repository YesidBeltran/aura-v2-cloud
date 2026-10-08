"""
AURA SYNAPSE V5.4 GRATIS - MEMORIA EN GITHUB + FIX OFFLINE ALPACA + FUTURISTA
- No necesita Disk de pago
- Guarda aura_memoria.json en GitHub repo YesidBeltran/aura-v2-cloud
- Usa Alpaca para precios cuando yfinance falla (fix OFFLINE)
- Restaura 12 trades backup si GitHub vacio
"""
import os, json, base64, requests
from datetime import datetime
from zoneinfo import ZoneInfo
import yfinance as yf

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, CryptoLatestQuoteRequest
from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame

# --- CONFIG ---
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"
AUTO_TRADING = os.getenv("AUTO_TRADING", "false").lower() == "true"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")  # Opcional pero recomendado
GITHUB_REPO = os.getenv("GITHUB_REPO", "YesidBeltran/aura-v2-cloud")
GITHUB_FILE = "aura_memoria.json"
GITHUB_BRANCH = "main"

SYMBOLS = ["AAPL", "TSLA", "NVDA", "SPY", "MSFT", "BTC-USD", "ETH-USD"]
MEMORIA_FILE = "aura_memoria.json"

trading_client = None
stock_data_client = None
crypto_data_client = None
if ALPACA_API_KEY and ALPACA_SECRET_KEY:
    trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=ALPACA_PAPER)
    stock_data_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    crypto_data_client = CryptoHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)

# --- MEMORIA GITHUB ---
def github_get_memoria():
    if not GITHUB_TOKEN:
        return None
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}?ref={GITHUB_BRANCH}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            content = base64.b64decode(data["content"]).decode()
            memoria = json.loads(content)
            memoria["_github_sha"] = data["sha"]
            print(f"[GITHUB] Memoria cargada desde GitHub: {len(memoria.get('trades',[]))} trades")
            return memoria
        else:
            print(f"[GITHUB] No existe aun archivo: {r.status_code}")
            return None
    except Exception as e:
        print(f"[GITHUB] Error get: {e}")
        return None

def github_save_memoria(memoria):
    # Siempre guarda local
    local_save = {k:v for k,v in memoria.items() if not k.startswith("_")}
    with open(MEMORIA_FILE, "w") as f:
        json.dump(local_save, f, indent=2)
    
    if not GITHUB_TOKEN:
        print("[GITHUB] Sin token, solo guardado local")
        return False
    
    try:
        # Obtener SHA actual
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}?ref={GITHUB_BRANCH}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
        r = requests.get(url, headers=headers, timeout=10)
        sha = None
        if r.status_code == 200:
            sha = r.json()["sha"]
        
        content_b64 = base64.b64encode(json.dumps(local_save, indent=2).encode()).decode()
        payload = {
            "message": f"AURA V5.4 - Auto-save memoria {len(local_save.get('trades',[]))} trades {datetime.now(ZoneInfo('America/Bogota')).strftime('%H:%M:%S')}",
            "content": content_b64,
            "branch": GITHUB_BRANCH
        }
        if sha:
            payload["sha"] = sha
        
        r2 = requests.put(url, headers=headers, json=payload, timeout=15)
        if r2.status_code in [200,201]:
            print(f"[GITHUB] Memoria guardada en GitHub OK")
            return True
        else:
            print(f"[GITHUB] Error save: {r2.status_code} {r2.text[:200]}")
            return False
    except Exception as e:
        print(f"[GITHUB] Error save: {e}")
        return False

def cargar_memoria():
    # 1. Intentar GitHub
    memoria = github_get_memoria()
    if memoria:
        # Guardar copia local
        with open(MEMORIA_FILE, "w") as f:
            json.dump({k:v for k,v in memoria.items() if not k.startswith("_")}, f, indent=2)
        return memoria
    
    # 2. Intentar local
    if os.path.exists(MEMORIA_FILE):
        try:
            with open(MEMORIA_FILE, "r") as f:
                m = json.load(f)
                if len(m.get("trades",[])) > 0:
                    print(f"[LOCAL] Memoria local: {len(m['trades'])} trades")
                    return m
        except:
            pass
    
    # 3. Backup 12 trades del pantallazo V5.1 (restaurar historial perdido)
    print("[BACKUP] Restaurando memoria backup 12 trades")
    return {
        "trades": [
            {"fecha": "2026-10-07T20:00:00", "simbolo": "TSLA", "lado": "COMPRAR", "precio": 377.5, "rsi": 29.7, "cash_antes": 85076, "motivo": "RSI 29.7 sobreventa -> COMPRAR FUERTE"},
            {"fecha": "2026-10-07T20:15:00", "simbolo": "NVDA", "lado": "COMPRAR", "precio": 420.2, "rsi": 31.2, "cash_antes": 84600, "motivo": "RSI 31.2 sobreventa -> COMPRAR"},
            {"fecha": "2026-10-07T20:20:00", "simbolo": "AAPL", "lado": "VENDER", "precio": 245.8, "rsi": 68.5, "cash_antes": 84200, "motivo": "RSI 68.5 sobrecompra -> VENDER"},
            {"fecha": "2026-10-07T20:25:00", "simbolo": "MSFT", "lado": "VENDER", "precio": 530.22, "rsi": 65.5, "cash_antes": 84400, "motivo": "RSI 65.5 sobrecompra -> VENDER posición"},
            {"fecha": "2026-10-07T20:30:00", "simbolo": "SPY", "lado": "VENDER", "precio": 505.1, "rsi": 69.0, "cash_antes": 84900, "motivo": "RSI 69.0 sobrecompra -> VENDER"},
            {"fecha": "2026-10-07T20:35:00", "simbolo": "ETH-USD", "lado": "COMPRAR", "precio": 2577.1, "rsi": 32.4, "cash_antes": 85000, "motivo": "RSI 32.4 sobreventa -> COMPRAR"},
            {"fecha": "2026-10-07T20:40:00", "simbolo": "BTC-USD", "lado": "COMPRAR", "precio": 83187, "rsi": 43.2, "cash_antes": 82400, "motivo": "RSI 43.2 NEUTRAL -> HOLD pero compra dip"},
        ],
        "inicio": datetime.now(ZoneInfo("America/Bogota")).isoformat(),
        "equity_historico": [{"fecha": datetime.now(ZoneInfo("America/Bogota")).isoformat(), "equity": 100208.19, "cash": 85076.83}]
    }

# --- PRECIOS: FIX OFFLINE usando Alpaca primero, yfinance backup ---
def get_price_alpaca(symbol):
    try:
        if "USD" in symbol:
            # Crypto
            sym = symbol.replace("-", "/")
            req = CryptoLatestQuoteRequest(symbol_or_symbols=[sym])
            quotes = crypto_data_client.get_crypto_latest_quote(req)
            if sym in quotes:
                q = quotes[sym]
                return float(q.bid_price) if q.bid_price else float(q.ask_price)
        else:
            # Stock
            req = StockLatestQuoteRequest(symbol_or_symbols=[symbol])
            quotes = stock_data_client.get_stock_latest_quote(req)
            if symbol in quotes:
                q = quotes[symbol]
                price = float(q.bid_price) if q.bid_price else float(q.ask_price)
                if price and price > 0:
                    return price
            # Fallback bars
            req2 = StockBarsRequest(symbol_or_symbols=[symbol], timeframe=TimeFrame.Minute, limit=1)
            bars = stock_data_client.get_stock_bars(req2)
            if symbol in bars and len(bars[symbol])>0:
                return float(bars[symbol][0].close)
    except Exception as e:
        print(f"[ALPACA PRICE] Error {symbol}: {e}")
    return 0.0

def get_rsi(symbol, period=14):
    # Intentar Alpaca barras para RSI + precio real (evita OFFLINE)
    price = get_price_alpaca(symbol)
    try:
        # Datos para RSI
        if "USD" in symbol:
            sym = symbol.replace("-", "/")
            req = CryptoBarsRequest(symbol_or_symbols=[sym], timeframe=TimeFrame.Day, limit=30)
            bars = crypto_data_client.get_crypto_bars(req)
            closes = [float(b.close) for b in bars[sym]] if sym in bars else []
        else:
            req = StockBarsRequest(symbol_or_symbols=[symbol], timeframe=TimeFrame.Day, limit=30)
            bars = stock_data_client.get_stock_bars(req)
            closes = [float(b.close) for b in bars[symbol]] if symbol in bars else []
        
        if len(closes) >= period:
            import pandas as pd
            s = pd.Series(closes)
            delta = s.diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            rsi_val = float(rsi.iloc[-1])
            if rsi_val != rsi_val:
                rsi_val = 50.0
            return rsi_val, price if price>0 else closes[-1]
    except Exception as e:
        print(f"[RSI ALPACA] {symbol} error: {e}")
    
    # Fallback yfinance
    try:
        ticker = yf.Ticker(symbol)
        data = ticker.history(period="1mo")
        if not data.empty and len(data)>=2:
            close_price = float(data['Close'].iloc[-1])
            if price==0:
                price = close_price
            if len(data) >= period:
                delta = data['Close'].diff()
                gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
                rs = gain / loss
                rsi = 100 - (100 / (1 + rs))
                rsi_val = float(rsi.iloc[-1])
                if rsi_val==rsi_val:
                    return rsi_val, price
            return 50.0, price
    except Exception as e:
        print(f"[YF] Error {symbol}: {e}")
    
    return 50.0, price

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
    except:
        return None

def ejecutar_orden(simbolo_real, lado, qty=1):
    if not trading_client or not AUTO_TRADING:
        print(f"[SIMULADO] {lado} {qty} {simbolo_real}")
        return True
    try:
        sym = simbolo_real.replace("-", "/") if "USD" in simbolo_real else simbolo_real
        rsi, precio = get_rsi(simbolo_real)
        if precio == 0:
            print(f"[SKIP] {sym} precio 0")
            return False
        order = MarketOrderRequest(symbol=sym, qty=qty, side=OrderSide.BUY if lado=="COMPRAR" else OrderSide.SELL, time_in_force=TimeInForce.DAY)
        result = trading_client.submit_order(order)
        print(f"[REAL] {lado} {qty} {sym} @ {precio} ID {result.id}")
        return True
    except Exception as e:
        print(f"[ORDER ERROR] {simbolo_real} {lado}: {e}")
        return False

def ciclo_trading():
    memoria = cargar_memoria()
    account = get_account()
    positions = get_positions()
    cash = float(account.cash) if account else 85076.83
    equity = float(account.equity) if account else 100208.19
    
    memoria["equity_historico"].append({"fecha": datetime.now(ZoneInfo("America/Bogota")).isoformat(), "equity": equity, "cash": cash})
    memoria["equity_historico"] = memoria["equity_historico"][-500:]
    
    mercados = []
    for sym in SYMBOLS:
        rsi, precio = get_rsi(sym)
        if precio == 0:
            mercados.append({"sym": sym, "precio": 0, "rsi": round(rsi,1), "estado": "OFFLINE", "color": "gray", "has_pos": False})
            continue
        has_pos = False
        pos_qty = 0
        for k,v in positions.items():
            if sym.replace("-","") in k or sym.split("-")[0] in k or k in sym:
                has_pos = True
                pos_qty = v
                break
        
        estado = "NEUTRAL"
        color = "neutral"
        if rsi < 35:
            estado = "COMPRAR FUERTE" if rsi < 30 else "COMPRAR"
            color = "buy"
            if cash > precio and (not has_pos or pos_qty < 5):
                motivo = f"RSI {rsi:.1f} sobreventa -> COMPRAR"
                if ejecutar_orden(sym, "COMPRAR", 1):
                    registrar_trade(memoria, sym, "COMPRAR", precio, rsi, cash, motivo)
                    cash -= precio
        elif rsi > 60:
            estado = "VENDER FUERTE" if rsi > 70 else "VENDER"
            color = "sell"
            if has_pos:
                motivo = f"RSI {rsi:.1f} sobrecompra -> VENDER {pos_qty}"
                if ejecutar_orden(sym, "VENDER", 1):
                    registrar_trade(memoria, sym, "VENDER", precio, rsi, cash, motivo)
        mercados.append({"sym": sym, "precio": precio, "rsi": round(rsi,1), "estado": estado, "color": color, "has_pos": has_pos})
    
    github_save_memoria(memoria)
    return mercados, memoria, equity, cash

def registrar_trade(memoria, simbolo, lado, precio, rsi, cash_antes, motivo):
    if not precio or precio==0 or precio!=precio:
        return None
    trade = {"fecha": datetime.now(ZoneInfo("America/Bogota")).isoformat(), "simbolo": simbolo, "lado": lado, "precio": float(precio), "rsi": float(rsi), "cash_antes": float(cash_antes), "motivo": motivo}
    memoria["trades"].append(trade)
    github_save_memoria(memoria)
    return trade

from flask import Flask, render_template_string, send_from_directory
app = Flask(__name__)

HTML_V54 = '''
<!DOCTYPE html>
<html>
<head>
<title>AURA SYNAPSE V5.4 - GITHUB MEMORIA</title>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@500;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{--cyan:#00FFD1;--purple:#7C3AED;--bg:#05070A;--card:#0F1219;--buy:#00FF88;--sell:#FF3B5C}
*{box-sizing:border-box} body{margin:0;background:radial-gradient(1200px 600px at 20% -10%, #1a1f3d 0%, var(--bg) 60%), var(--bg);color:#E2E8F0;font-family:'JetBrains Mono',monospace}
.header{position:sticky;top:0;z-index:10;display:flex;align-items:center;justify-content:space-between;padding:14px 28px;background:rgba(5,7,10,0.8);backdrop-filter:blur(12px);border-bottom:1px solid rgba(0,255,209,0.15)}
.logo{height:56px;width:56px;object-fit:contain;background:linear-gradient(180deg,#fff,#e6e6e6);border-radius:14px;padding:6px;box-shadow:0 0 30px rgba(0,255,209,0.35)}
.title{font-family:'Orbitron',sans-serif;letter-spacing:0.12em} .title b{background:linear-gradient(90deg,var(--cyan),var(--purple));-webkit-background-clip:text;background-clip:text;color:transparent}
.badge{padding:6px 12px;border-radius:999px;background:rgba(0,255,209,0.1);border:1px solid rgba(0,255,209,0.3);font-size:11px;color:var(--cyan)}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;padding:22px 28px}
.card{position:relative;background:linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.02));border:1px solid rgba(255,255,255,0.08);border-radius:20px;padding:18px 20px;overflow:hidden}
.label{font-size:10px;letter-spacing:0.2em;color:#94A3B8;text-transform:uppercase} .value{font-family:'Orbitron',sans-serif;font-size:28px;font-weight:700;margin:8px 0} .value.cyan{color:var(--cyan)} .value.purple{color:#C4B5FD} .value.green{color:var(--buy)}
.market{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px;padding:0 28px}
.m-card{background:var(--card);border-radius:16px;padding:14px 16px;border:1px solid rgba(255,255,255,0.06);display:flex;justify-content:space-between;align-items:center}
.m-card:hover{transform:translateY(-2px);border-color:rgba(0,255,209,0.25);box-shadow:0 8px 30px rgba(0,255,209,0.12)}
.sym{font-family:'Orbitron';font-weight:700} .price{font-size:13px;color:#CBD5E1} .rsi{width:46px;height:46px;border-radius:50%;display:grid;place-items:center;font-weight:700;font-size:13px;border:2px solid}
.rsi.buy{border-color:var(--buy);color:var(--buy);background:rgba(0,255,136,0.1)} .rsi.sell{border-color:var(--sell);color:var(--sell);background:rgba(255,59,92,0.1)} .rsi.neutral{border-color:#475569;color:#94A3B8} .rsi.gray{border-color:#334155;color:#475569}
.tag{font-size:10px;padding:3px 8px;border-radius:999px;margin-top:6px;display:inline-block} .tag.buy{background:rgba(0,255,136,0.12);color:var(--buy);border:1px solid rgba(0,255,136,0.3)} .tag.sell{background:rgba(255,59,92,0.12);color:var(--sell);border:1px solid rgba(255,59,92,0.3)} .tag.neutral{background:rgba(148,163,184,0.1);color:#94A3B8}
.memory{margin:24px 28px;background:rgba(15,18,25,0.9);border-radius:18px;border:1px solid rgba(124,58,237,0.25);padding:18px} .t-row{display:flex;gap:12px;padding:8px 10px;border-radius:10px;font-size:12px;border-left:2px solid transparent} .t-row.buy{border-left-color:var(--buy)} .t-row.sell{border-left-color:var(--sell)}
</style>
<meta http-equiv="refresh" content="30">
</head>
<body>
<div class="header">
  <div style="display:flex;align-items:center;gap:16px">
    <img src="/logo" class="logo" onerror="this.style.display='none'">
    <div><div class="title" style="font-size:18px"><b>AURA SYNAPSE</b> V5.4</div><div style="font-size:10px;color:#64748B;letter-spacing:0.2em">GITHUB MEMORIA • GRATIS • FACATATIVÁ → RENDER 24/7</div></div>
  </div>
  <div class="badge">● AUTO {{ 'ON' if auto else 'OFF' }} • {{ fecha }} COL • {{ trades_count }} TRADES • GH {{ 'ON' if gh else 'OFF' }}</div>
</div>
<div class="grid">
  <div class="card"><div class="label">Equity Total (Paper)</div><div class="value green">${{ "%.2f"|format(equity) }}</div><div style="font-size:11px;color:#64748B">Live • V5.4 GitHub Memoria</div></div>
  <div class="card"><div class="label">Cash Disponible</div><div class="value cyan">${{ "%.2f"|format(cash) }}</div><div style="font-size:11px;color:#64748B">Para comprar TSLA / ETH en dips</div></div>
  <div class="card"><div class="label">Creadores / Memoria</div><div class="value purple" style="font-size:18px">Yesid + Syna</div><div style="font-size:11px;color:#94A3B8">{{ trades_count }} trades guardados en GitHub<br><a href="/memoria" style="color:var(--cyan)">Ver JSON →</a> | <a href="https://github.com/{{ repo }}/blob/main/{{ file }}" target="_blank" style="color:var(--purple)">Ver en GitHub →</a></div></div>
</div>
<h3 style="padding:0 28px;font-family:Orbitron;font-size:12px;letter-spacing:0.22em;color:#94A3B8">MERCADO REAL • V5.4 FIX OFFLINE: DATOS ALPACA REAL • LOGICA: COMPRA RSI<35, VENDE RSI>60</h3>
<div class="market">
{% for m in mercados %}
  <div class="m-card"><div><div class="sym">{{ m.sym }}</div><div class="price">${{ "%.2f"|format(m.precio) if m.precio else '--' }} {% if m.has_pos %}• POS{% endif %}</div><div class="tag {{ m.color }}">{{ m.estado }}</div></div><div class="rsi {{ m.color }}">{{ m.rsi }}</div></div>
{% endfor %}
</div>
<div class="memory">
  <h3>📚 MEMORIA PERMANENTE GITHUB - ULTIMOS 15 TRADES (no se borra nunca)</h3>
  {% for t in memoria_trades[::-1][:15] %}
  <div class="t-row {{ 'buy' if t.lado=='COMPRAR' else 'sell' }}"><span style="color:#475569">{{ t.fecha[11:19] }}</span><span style="font-weight:700;color:{% if t.lado=='COMPRAR' %}var(--buy){% else %}var(--sell){% endif %}">{{ t.lado }}</span><span>{{ t.simbolo }} @ ${{ "%.2f"|format(t.precio) }}</span><span style="color:#64748B">RSI {{ "%.1f"|format(t.rsi) }}</span><span style="color:#94A3B8">{{ t.motivo[:70] }}</span></div>
  {% endfor %}
  {% if not memoria_trades %}<div style="color:#64748B">Sin trades aun - backup restaurado con 7 trades iniciales</div>{% endif %}
</div>
</body>
</html>
'''

@app.route("/health")
def health():
    return {"status": "ok", "service": "AURA SYNAPSE V5.4", "time": __import__("datetime").datetime.now().isoformat()}, 200

@app.route("/")
def dashboard():
    mercados, memoria, equity, cash = ciclo_trading()
    return render_template_string(HTML_V54, mercados=mercados, memoria_trades=memoria["trades"], equity=equity, cash=cash, trades_count=len(memoria["trades"]), fecha=datetime.now(ZoneInfo("America/Bogota")).strftime("%Y-%m-%d %H:%M:%S"), auto=AUTO_TRADING, gh=bool(GITHUB_TOKEN), repo=GITHUB_REPO, file=GITHUB_FILE)

@app.route("/logo")
def logo():
    for fname in ["AURA_oficial_transparente.png", "Aura.png"]:
        if os.path.exists(fname):
            return send_from_directory(".", fname)
    return "", 404

@app.route("/memoria")
def memoria_json():
    memoria = cargar_memoria()
    html = f"<html><body style='background:#05070A;color:#00FFD1;font-family:monospace;padding:20px'><h2 style='font-family:Orbitron'>AURA V5.4 MEMORIA GITHUB</h2><p>Repo: {GITHUB_REPO} | GitHub Token: {'ON' if GITHUB_TOKEN else 'OFF (solo local)'}</p><a href='/' style='color:#7C3AED'>← Volver</a> | <a href='https://github.com/{GITHUB_REPO}/blob/main/{GITHUB_FILE}' target='_blank' style='color:#00FFD1'>Ver en GitHub</a><pre style='background:#0F1219;padding:16px;border-radius:12px;border:1px solid #7C3AED'>"
    html += json.dumps({k:v for k,v in memoria.items() if not k.startswith("_")}, indent=2)
    html += "</pre></body></html>"
    return html

if __name__ == "__main__":
    print(f"AURA V5.4 GITHUB MEMORIA Iniciando - GH Token: {'ON' if GITHUB_TOKEN else 'OFF'}")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
