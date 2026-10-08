"""
AURA SYNAPSE V5.3 FUTURISTA - UI Cyberpunk + Logo Oficial + Fix NAN
Design: Neon cyan #00FFD1, purple #7C3AED, dark glassmorphism
"""
import os, json, base64
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
    import math
    for t in memoria.get("trades", []):
        if isinstance(t.get("precio"), float) and (math.isnan(t["precio"]) or math.isinf(t["precio"])):
            t["precio"] = 0.0
    with open(MEMORIA_FILE, "w") as f:
        json.dump(memoria, f, indent=2)

def registrar_trade(memoria, simbolo, lado, precio, rsi, cash_antes, motivo):
    if not precio or precio == 0 or precio != precio:
        return None
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
        if data.empty or len(data) < 2:
            return 50, 0.0
        close_price = float(data['Close'].iloc[-1])
        if len(data) < period:
            return 50, close_price
        delta = data['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        rsi_val = float(rsi.iloc[-1])
        if rsi_val != rsi_val:
            rsi_val = 50.0
        return rsi_val, close_price
    except:
        return 50, 0.0

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
        return None
    try:
        sym = simbolo_real.replace("-", "/") if "USD" in simbolo_real else simbolo_real
        _, p = get_rsi(simbolo_real)
        if p == 0:
            return None
        order = MarketOrderRequest(
            symbol=sym,
            qty=qty,
            side=OrderSide.BUY if lado == "COMPRAR" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY
        )
        result = trading_client.submit_order(order)
        return result
    except Exception as e:
        print(f"Error orden {simbolo_real} {lado}: {e}")
        return None

def ciclo_trading():
    memoria = cargar_memoria()
    account = get_account()
    positions = get_positions()
    cash = float(account.cash) if account else 82496.43
    equity = float(account.equity) if account else 100206.27
    memoria["equity_historico"].append({
        "fecha": datetime.now(ZoneInfo("America/Bogota")).isoformat(),
        "equity": equity,
        "cash": cash
    })
    memoria["equity_historico"] = memoria["equity_historico"][-500:]
    logs = []
    mercados = []
    for sym in SYMBOLS:
        rsi, precio = get_rsi(sym)
        if precio == 0 or precio != precio:
            mercados.append({"sym": sym, "precio": 0, "rsi": rsi, "estado": "OFFLINE", "color": "gray"})
            continue
        has_position = False
        pos_qty = 0
        for k,v in positions.items():
            if sym.replace("-","") in k or sym.split("-")[0] in k or k in sym:
                has_position = True
                pos_qty = v
                break
        if rsi < 35:
            estado = "COMPRAR FUERTE" if rsi < 30 else "COMPRAR"
            color = "buy"
            if cash > precio and (not has_position or pos_qty < 5):
                motivo = f"RSI {rsi:.1f} sobreventa -> COMPRAR"
                logs.append(f"COMPRAR {sym} ${precio:.2f} RSI {rsi:.1f}")
                if ejecutar_orden(sym, "COMPRAR", 1):
                    registrar_trade(memoria, sym, "COMPRAR", precio, rsi, cash, motivo)
                    cash -= precio
        elif rsi > 60:
            estado = "VENDER FUERTE" if rsi > 70 else "VENDER"
            color = "sell"
            if has_position:
                motivo = f"RSI {rsi:.1f} sobrecompra -> VENDER {pos_qty}"
                logs.append(f"VENDER {sym} ${precio:.2f} RSI {rsi:.1f}")
                if ejecutar_orden(sym, "VENDER", 1):
                    registrar_trade(memoria, sym, "VENDER", precio, rsi, cash, motivo)
        else:
            estado = "NEUTRAL"
            color = "neutral"
        mercados.append({"sym": sym, "precio": precio, "rsi": round(rsi,1), "estado": estado, "color": color, "has_pos": has_position})
    guardar_memoria(memoria)
    return mercados, memoria, equity, cash

from flask import Flask, render_template_string, send_from_directory
app = Flask(__name__)

HTML_FUTUR = '''
<!DOCTYPE html>
<html>
<head>
<title>AURA SYNAPSE V5.3 FUTURISTA</title>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@500;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{--cyan:#00FFD1;--purple:#7C3AED;--bg:#05070A;--card:#0F1219;--buy:#00FF88;--sell:#FF3B5C}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(1200px 600px at 20% -10%, #1a1f3d 0%, var(--bg) 60%), var(--bg);color:#E2E8F0;font-family:'JetBrains Mono',monospace;overflow-x:hidden}
.header{position:sticky;top:0;z-index:10;display:flex;align-items:center;justify-content:space-between;padding:14px 28px;background:rgba(5,7,10,0.8);backdrop-filter:blur(12px);border-bottom:1px solid rgba(0,255,209,0.15)}
.logo-wrap{display:flex;align-items:center;gap:16px}
.logo{height:56px;width:56px;object-fit:contain;background:linear-gradient(180deg,#fff,#e6e6e6);border-radius:14px;padding:6px;box-shadow:0 0 30px rgba(0,255,209,0.35)}
.title{font-family:'Orbitron',sans-serif;letter-spacing:0.12em}
.title b{background:linear-gradient(90deg,var(--cyan),var(--purple));-webkit-background-clip:text;background-clip:text;color:transparent}
.badge{padding:6px 12px;border-radius:999px;background:rgba(0,255,209,0.1);border:1px solid rgba(0,255,209,0.3);font-size:11px;color:var(--cyan);box-shadow:0 0 20px rgba(0,255,209,0.25) inset}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;padding:22px 28px}
.card{position:relative;background:linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.02));border:1px solid rgba(255,255,255,0.08);border-radius:20px;padding:18px 20px;overflow:hidden}
.card::before{content:'';position:absolute;inset:-1px;border-radius:20px;padding:1px;background:linear-gradient(120deg,var(--cyan),transparent 30%,var(--purple));-webkit-mask:linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);-webkit-mask-composite:xor;mask-composite:exclude;opacity:0.6}
.label{font-size:10px;letter-spacing:0.2em;color:#94A3B8;text-transform:uppercase}
.value{font-family:'Orbitron',sans-serif;font-size:28px;font-weight:700;margin:8px 0}
.value.cyan{color:var(--cyan);text-shadow:0 0 20px rgba(0,255,209,0.6)}
.value.purple{color:#C4B5FD}
.value.green{color:var(--buy);text-shadow:0 0 18px rgba(0,255,136,0.5)}
.market{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px;padding:0 28px}
.m-card{background:var(--card);border-radius:16px;padding:14px 16px;border:1px solid rgba(255,255,255,0.06);display:flex;justify-content:space-between;align-items:center;transition:0.2s}
.m-card:hover{transform:translateY(-2px);border-color:rgba(0,255,209,0.25);box-shadow:0 8px 30px rgba(0,255,209,0.12)}
.m-left .sym{font-family:'Orbitron';font-weight:700;letter-spacing:0.08em}
.m-left .price{font-size:13px;color:#CBD5E1;margin-top:2px}
.rsi{width:46px;height:46px;border-radius:50%;display:grid;place-items:center;font-weight:700;font-size:13px;border:2px solid}
.rsi.buy{border-color:var(--buy);color:var(--buy);background:rgba(0,255,136,0.1);box-shadow:0 0 18px rgba(0,255,136,0.35)}
.rsi.sell{border-color:var(--sell);color:var(--sell);background:rgba(255,59,92,0.1);box-shadow:0 0 18px rgba(255,59,92,0.35)}
.rsi.neutral{border-color:#475569;color:#94A3B8;background:rgba(71,85,105,0.15)}
.tag{font-size:10px;padding:3px 8px;border-radius:999px;margin-top:6px;display:inline-block;letter-spacing:0.1em}
.tag.buy{background:rgba(0,255,136,0.12);color:var(--buy);border:1px solid rgba(0,255,136,0.3)}
.tag.sell{background:rgba(255,59,92,0.12);color:var(--sell);border:1px solid rgba(255,59,92,0.3)}
.tag.neutral{background:rgba(148,163,184,0.1);color:#94A3B8;border:1px solid rgba(148,163,184,0.2)}
.memory{margin:24px 28px;background:rgba(15,18,25,0.9);border-radius:18px;border:1px solid rgba(124,58,237,0.25);padding:18px}
.memory h3{font-family:'Orbitron';font-size:13px;letter-spacing:0.18em;color:var(--purple);margin:0 0 12px 0}
.t-row{display:flex;gap:12px;padding:8px 10px;border-radius:10px;font-size:12px;border-left:2px solid transparent}
.t-row:nth-child(odd){background:rgba(255,255,255,0.02)}
.t-row.buy{border-left-color:var(--buy)}
.t-row.sell{border-left-color:var(--sell)}
</style>
<meta http-equiv="refresh" content="30">
</head>
<body>
<div class="header">
  <div class="logo-wrap">
    <img src="/logo" class="logo" onerror="this.style.display='none'">
    <div>
      <div class="title" style="font-size:18px"><b>AURA SYNAPSE</b> V5.3</div>
      <div style="font-size:10px;color:#64748B;letter-spacing:0.2em">FUTURISTIC TRADING AI • FACATATIVÁ → RENDER 24/7</div>
    </div>
  </div>
  <div class="badge">● AUTO-TRADING {{ 'ON' if auto else 'OFF' }} • {{ fecha }} COL • {{ trades_count }} TRADES</div>
</div>

<div class="grid">
  <div class="card">
    <div class="label">Equity Total (Paper)</div>
    <div class="value green">${{ "%.2f"|format(equity) }}</div>
    <div style="font-size:11px;color:#64748B">Live • Alpaca Paper • V5.3 Futurista</div>
  </div>
  <div class="card">
    <div class="label">Cash Disponible</div>
    <div class="value cyan">${{ "%.2f"|format(cash) }}</div>
    <div style="font-size:11px;color:#64748B">Para comprar TSLA / ETH en dips</div>
  </div>
  <div class="card">
    <div class="label">Creadores / Memoria</div>
    <div class="value purple" style="font-size:18px">Yesid + Syna</div>
    <div style="font-size:11px;color:#94A3B8">{{ trades_count }} trades guardados en aura_memoria.json<br><a href="/memoria" style="color:var(--cyan)">Ver JSON →</a></div>
  </div>
</div>

<h3 style="padding:0 28px;font-family:Orbitron;font-size:12px;letter-spacing:0.22em;color:#94A3B8">MERCADO REAL • LOGICA V5.3: COMPRA RSI<35, VENDE RSI>60 • FUTURISTIC GRID</h3>
<div class="market">
{% for m in mercados %}
  <div class="m-card">
    <div class="m-left">
      <div class="sym">{{ m.sym }}</div>
      <div class="price">${{ "%.2f"|format(m.precio) if m.precio else '--' }} {% if m.has_pos %}• POS {% endif %}</div>
      <div class="tag {{ m.color }}">{{ m.estado }}</div>
    </div>
    <div class="rsi {{ m.color }}">{{ m.rsi }}</div>
  </div>
{% endfor %}
</div>

<div class="memory">
  <h3>📚 MEMORIA PERMANENTE - ULTIMOS 10 TRADES</h3>
  {% for t in memoria_trades[::-1][:10] %}
  <div class="t-row {{ 'buy' if t.lado=='COMPRAR' else 'sell' }}">
    <span style="color:#475569">{{ t.fecha[11:19] }}</span>
    <span style="font-weight:700;color:{% if t.lado=='COMPRAR' %}var(--buy){% else %}var(--sell){% endif %}">{{ t.lado }}</span>
    <span>{{ t.simbolo }} @ ${{ "%.2f"|format(t.precio) }}</span>
    <span style="color:#64748B">RSI {{ "%.1f"|format(t.rsi) }}</span>
    <span style="color:#94A3B8">{{ t.motivo[:60] }}</span>
  </div>
  {% endfor %}
</div>

</body>
</html>
'''

@app.route("/")
def dashboard():
    mercados, memoria, equity, cash = ciclo_trading()
    return render_template_string(HTML_FUTUR, 
        mercados=mercados,
        memoria_trades=memoria["trades"],
        equity=equity,
        cash=cash,
        trades_count=len(memoria["trades"]),
        fecha=datetime.now(ZoneInfo("America/Bogota")).strftime("%Y-%m-%d %H:%M:%S"),
        auto=AUTO_TRADING
    )

@app.route("/logo")
def logo():
    for fname in ["AURA_oficial_transparente.png", "Aura.png"]:
        if os.path.exists(fname):
            return send_from_directory(".", fname)
    return "", 404

@app.route("/memoria")
def memoria_json():
    memoria = cargar_memoria()
    html = "<html><body style='background:#05070A;color:#00FFD1;font-family:monospace;padding:20px'><h2 style='font-family:Orbitron'>AURA V5.3 MEMORIA FUTURISTA</h2><a href='/' style='color:#7C3AED'>← Volver Dashboard</a><pre style='background:#0F1219;padding:16px;border-radius:12px;border:1px solid #7C3AED'>"
    html += json.dumps(memoria, indent=2)
    html += "</pre></body></html>"
    return html

if __name__ == "__main__":
    print("AURA V5.3 FUTURISTA Iniciando")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
