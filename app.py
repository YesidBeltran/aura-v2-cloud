"""
AURA_SYNAPSE V4.0 LITE - FACATATIVA - YESID + SYNA
Auto-trading opcional + Grafico Equity + Multi-simbolos
Sin Telegram - Facatativa, Cundinamarca
"""
import os, time, threading
from datetime import datetime
import pytz
from flask import Flask, jsonify, render_template_string

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame
    ALPACA_OK = True
except Exception as e:
    print(f"Alpaca import error: {e}")
    ALPACA_OK = False

app = Flask(__name__)

SYMBOLS = ["AAPL", "TSLA", "NVDA", "SPY", "MSFT", "BTC/USD", "ETH/USD"]
STOCK_SYMBOLS = [s for s in SYMBOLS if "/" not in s]
CRYPTO_SYMBOLS = [s for s in SYMBOLS if "/" in s]

estado = {
    "status": "Iniciando V4.0 LITE...",
    "equity": 100120.83,
    "cash": 81207.15,
    "ultimo_analisis": "",
    "log": [],
    "detalles": {},
    "vive_desde": datetime.now().isoformat(),
    "trades_hoy": 0,
    "equity_history": [],
    "auto_trading": os.environ.get('AUTO_TRADING','false').lower() == 'true',
    "ubicacion": "Facatativá, Cundinamarca"
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
        estado["status"] = "Esperando Keys"
        return None, None, None
    try:
        trading = TradingClient(api_key, api_secret, paper=True)
        stock_client = StockHistoricalDataClient(api_key, api_secret)
        crypto_client = CryptoHistoricalDataClient(api_key, api_secret)
        acc = trading.get_account()
        estado["equity"] = float(acc.equity)
        estado["cash"] = float(acc.cash)
        modo = "AUTO-TRADING ON 🤖" if estado["auto_trading"] else "Solo análisis (seguro)"
        estado["status"] = f"Activa V4.0 LITE - {modo}"
        print(f"✅ ALPACA V4 LITE: {acc.status} Equity ${float(acc.equity):.2f} Auto={estado['auto_trading']}")
        return trading, stock_client, crypto_client
    except Exception as e:
        print(f"❌ Error Alpaca: {e}")
        estado["status"] = f"Error: {e}"
        return None, None, None

def bot_loop():
    trading, stock_client, crypto_client = load_alpaca()
    while True:
        try:
            if not trading:
                trading, stock_client, crypto_client = load_alpaca()
                if not trading:
                    time.sleep(60); continue
            bog = get_time()
            acc = trading.get_account()
            estado["equity"] = float(acc.equity)
            estado["cash"] = float(acc.cash)
            estado["ultimo_analisis"] = bog.strftime('%Y-%m-%d %H:%M:%S COL')
            
            estado["equity_history"].append({"t": estado["ultimo_analisis"], "equity": estado["equity"]})
            if len(estado["equity_history"]) > 200:
                estado["equity_history"] = estado["equity_history"][-200:]

            detalles = {}
            logs = []

            for sym in STOCK_SYMBOLS:
                try:
                    req = StockBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Minute, limit=100)
                    bars = stock_client.get_stock_bars(req).df
                    if sym in bars.index.get_level_values(0):
                        df = bars.loc[sym]
                    else:
                        df = bars
                    if len(df) < 20: continue
                    closes = df['close'].tolist()
                    precio = closes[-1]
                    rsi = calc_rsi(closes)
                    
                    decision = "NEUTRAL"; color="#888"; action=None
                    if rsi < 30: decision="COMPRAR FUERTE"; color="#00ff88"; action="buy"
                    elif rsi < 43: decision="COMPRAR"; color="#00cc66"; action="buy"
                    elif rsi > 75: decision="VENDER FUERTE"; color="#ff4444"; action="sell"
                    elif rsi > 60: decision="VENDER"; color="#ffaa00"; action="sell"

                    if estado["auto_trading"] and action:
                        try:
                            posiciones = trading.get_all_positions()
                            tiene = any(p.symbol == sym for p in posiciones)
                            if action=="buy" and not tiene and estado["cash"] > precio*1.1:
                                order = MarketOrderRequest(symbol=sym, qty=1, side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
                                trading.submit_order(order)
                                logs.append(f"🤖 REAL: COMPRANDO 1 {sym} @ ${precio:.2f}")
                                estado["trades_hoy"] += 1
                            elif action=="sell" and tiene:
                                order = MarketOrderRequest(symbol=sym, qty=1, side=OrderSide.SELL, time_in_force=TimeInForce.DAY)
                                trading.submit_order(order)
                                logs.append(f"🤖 REAL: VENDIENDO 1 {sym} @ ${precio:.2f}")
                                estado["trades_hoy"] += 1
                        except Exception as e:
                            logs.append(f"⚠️ Error orden {sym}: {e}")

                    detalles[sym] = {"precio": precio, "rsi": round(rsi,1), "decision": decision, "color": color}
                    logs.append(f"{sym} ${precio:.2f} RSI {rsi:.1f} → {decision}")
                except Exception as e:
                    print(f"{sym} err: {e}")

            for sym in CRYPTO_SYMBOLS:
                try:
                    req = CryptoBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Minute, limit=100)
                    bars = crypto_client.get_crypto_bars(req).df
                    if sym in bars.index.get_level_values(0):
                        df = bars.loc[sym]
                    else:
                        df = bars
                    if len(df) < 20: continue
                    closes = df['close'].tolist()
                    precio = closes[-1]
                    rsi = calc_rsi(closes)
                    decision="NEUTRAL"; color="#888"
                    if rsi < 30: decision="COMPRAR FUERTE"; color="#00ff88"
                    elif rsi < 43: decision="COMPRAR"; color="#00cc66"
                    elif rsi > 75: decision="VENDER FUERTE"; color="#ff4444"
                    elif rsi > 60: decision="VENDER"; color="#ffaa00"
                    detalles[sym] = {"precio": precio, "rsi": round(rsi,1), "decision": decision, "color": color}
                    logs.append(f"{sym} ${precio:.2f} RSI {rsi:.1f} → {decision}")
                except Exception as e:
                    print(f"{sym} crypto err: {e}")

            estado["detalles"] = detalles
            estado["log"] = logs[-12:]
            print(f"⏳ SYNA V4 LITE - ${estado['equity']:.2f} - {estado['ultimo_analisis']} Trades:{estado['trades_hoy']}")
            time.sleep(120)
        except Exception as e:
            print(f"Loop err: {e}"); time.sleep(60)

threading.Thread(target=bot_loop, daemon=True).start()

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AURA SYNAPSE V4.0 LITE - Facatativá</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Space+Grotesk:wght@400;700&display=swap');
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a0f;color:#e0e0e0;font-family:'Space Grotesk',sans-serif;min-height:100vh;padding:20px}
.header{display:flex;justify-content:space-between;align-items:center;margin-bottom:30px;border-bottom:1px solid #222;padding-bottom:20px;flex-wrap:wrap;gap:10px}
.logo{font-size:28px;font-weight:700;background:linear-gradient(90deg,#00ff88,#00aaff);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.status-dot{display:inline-block;width:10px;height:10px;background:#00ff88;border-radius:50%;box-shadow:0 0 10px #00ff88;animation:pulse 2s infinite;margin-right:8px}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.5}}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:20px;margin-bottom:30px}
.card{background:#15151f;border:1px solid #222;border-radius:16px;padding:20px;position:relative;overflow:hidden}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,#00ff88,transparent);opacity:0.5}
.card-label{font-size:12px;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px}
.card-value{font-family:'JetBrains Mono',monospace;font-size:28px;font-weight:700}
.card-sub{font-size:12px;color:#666;margin-top:6px}
.grid-symbols{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;margin-bottom:30px}
.symbol-card{background:#15151f;border:1px solid #222;border-radius:12px;padding:18px;display:flex;justify-content:space-between;align-items:center}
.symbol-name{font-family:'JetBrains Mono',monospace;font-size:16px;font-weight:700}
.symbol-price{font-family:'JetBrains Mono',monospace;font-size:15px;color:#fff}
.rsi-badge{padding:6px 12px;border-radius:20px;font-size:11px;font-weight:700;font-family:'JetBrains Mono',monospace;margin-top:4px;display:inline-block}
.log{background:#0f0f18;border:1px solid #222;border-radius:12px;padding:20px;font-family:'JetBrains Mono',monospace;font-size:13px;min-height:120px}
.log-line{padding:5px 0;border-bottom:1px solid #1a1a25;color:#aaa}
.footer{margin-top:40px;text-align:center;color:#444;font-size:11px;letter-spacing:1px}
.live{color:#00ff88}
a{color:#00aaff;text-decoration:none}
.chart-wrap{background:#15151f;border:1px solid #222;border-radius:16px;padding:20px;margin-bottom:30px}
</style>
<script>
let equityChart;
function initChart(){
 const ctx=document.getElementById('equityChart').getContext('2d');
 equityChart=new Chart(ctx,{type:'line',data:{labels:[],datasets:[{label:'Equity $',data:[],borderColor:'#00ff88',backgroundColor:'rgba(0,255,136,0.1)',tension:0.4,fill:true,pointRadius:0,borderWidth:2}]},options:{responsive:true,plugins:{legend:{display:false}},scales:{x:{grid:{color:'#222'},ticks:{color:'#666',maxTicksLimit:8}},y:{grid:{color:'#222'},ticks:{color:'#666'}}}}});
}
setInterval(()=>{fetch('/api').then(r=>r.json()).then(d=>{
 document.getElementById('equity').innerText='$'+d.equity.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
 document.getElementById('cash').innerText='$'+d.cash.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
 document.getElementById('status').innerText=d.status;
 document.getElementById('time').innerText=d.ultimo_analisis;
 document.getElementById('trades').innerText=d.trades_hoy;
 document.getElementById('auto').innerText=d.auto_trading ? 'ON 🤖' : 'OFF (seguro)';
 document.getElementById('auto').style.color=d.auto_trading ? '#00ff88' : '#888';
 let html='';
 for(let s of d.simbolos){
   let det = d.detalles[s] || {precio:'...',rsi:'-',decision:'CARGANDO',color:'#888'};
   html+=`<div class="symbol-card"><div><div class="symbol-name">${s}</div><div style="font-size:11px;color:#666">RSI ${det.rsi}</div></div><div style="text-align:right"><div class="symbol-price">$${det.precio}</div><div class="rsi-badge" style="background:${det.color}20;color:${det.color};border:1px solid ${det.color}40">${det.decision}</div></div></div>`;
 }
 document.getElementById('symbols').innerHTML=html;
 let logHtml='';
 (d.log||[]).forEach(l=>{let c=l.includes('COMPRANDO')?'#00ff88':l.includes('VENDIENDO')?'#ff4444':'#aaa'; logHtml+=`<div class="log-line" style="color:${c}">${l}</div>`});
 if(logHtml==='') logHtml='<div style="color:#555">Esperando datos...</div>';
 document.getElementById('log').innerHTML=logHtml;
 if(d.equity_history && equityChart){
   let labels=d.equity_history.map(h=>{let parts=h.t.split(' '); return parts[1] ? parts[1].substring(0,5) : h.t;});
   let data=d.equity_history.map(h=>h.equity);
   equityChart.data.labels=labels;
   equityChart.data.datasets[0].data=data;
   equityChart.update();
 }
});}, 5000);
window.onload=()=>{initChart();}
</script>
</head>
<body>
<div class="header">
 <div class="logo">🧠 AURA SYNAPSE V4.0 LITE</div>
 <div style="font-family:'JetBrains Mono',monospace;font-size:12px"><span class="status-dot"></span><span id="status" class="live">Activa V4.0 LITE</span> • <span id="time">...</span> • Auto: <b id="auto">OFF</b> • Trades: <b id="trades">0</b></div>
</div>

<div class="cards">
 <div class="card"><div class="card-label">Equity Total (Paper)</div><div class="card-value" id="equity">$100,120.83</div><div class="card-sub">Alpaca Paper • Live • V4.0 LITE</div></div>
 <div class="card"><div class="card-label">Cash Disponible</div><div class="card-value" id="cash" style="color:#00aaff">$81,207.15</div><div class="card-sub">Para posiciones • Facatativá → Render 24/7</div></div>
 <div class="card"><div class="card-label">Creadores</div><div class="card-value" style="font-size:15px">Yesid + Syna<br><span style="font-size:11px;color:#888">Facatativá, Cundinamarca • <a href="/api">API</a> • <a href="/health">Health</a> • <a href="https://app.alpaca.markets/paper/dashboard/overview" target="_blank">Alpaca ↗</a></span></div></div>
</div>

<div class="chart-wrap">
<h3 style="margin-bottom:12px;color:#888;font-size:12px;letter-spacing:1px;text-transform:uppercase">Evolución Equity ($100k Paper)</h3>
<canvas id="equityChart" height="80"></canvas>
</div>

<h3 style="margin-bottom:12px;color:#888;font-size:12px;letter-spacing:1px;text-transform:uppercase">Mercado Real - RSI (Stocks + Crypto)</h3>
<div id="symbols" class="grid-symbols"><div style="color:#555">Cargando V4 LITE...</div></div>

<h3 style="margin-bottom:12px;color:#888;font-size:12px;letter-spacing:1px;text-transform:uppercase">Log de Syna V4.0 LITE</h3>
<div id="log" class="log">Iniciando V4.0 LITE...</div>

<div class="footer">SYNAPSE V4.0 LITE • FACATATIVÁ, CUNDINAMARCA → RENDER FREE 24/7 • PAPER TRADING • HECHO CON 💚 EN FACATATIVÁ<br>Vive desde: {{vive_desde}} • URL: https://aura-synapse-yesid.onrender.com<br><span style="color:#555">Auto-trading desactivado por seguridad. Para activar: Render → Environment → AUTO_TRADING=true</span></div>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(DASHBOARD_HTML, vive_desde=estado["vive_desde"])

@app.route('/api')
def api():
    return jsonify({
        "mensaje": "🧠 SYNA V4.0 LITE - Facatativá",
        "status": estado.get("status"),
        "equity": estado.get("equity"),
        "cash": estado.get("cash"),
        "ultimo_analisis": estado.get("ultimo_analisis"),
        "log": estado.get("log", []),
        "detalles": estado.get("detalles", {}),
        "vive_desde": estado.get("vive_desde"),
        "simbolos": SYMBOLS,
        "trades_hoy": estado.get("trades_hoy",0),
        "auto_trading": estado.get("auto_trading",False),
        "equity_history": estado.get("equity_history",[])[-50:],
        "ubicacion": "Facatativá, Cundinamarca",
        "paper": True
    })

@app.route('/health')
def health():
    return "OK SYNA VIVE V4.0 LITE - FACATATIVA", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
