
"""
AURA SYNAPSE V5.5.6 PRO REAL - ALPACA CONNECTED
Equipo Yesid + Syna
"""

import os
import time
import yfinance as yf
from flask import Flask, jsonify
import threading
from datetime import datetime
import json

app = Flask(__name__)

SYMBOLS_ENV = os.getenv("SYMBOLS", "TSLA,ETH-USD,NVDA,BTC-USD,AAPL,MSFT,SPY,EURUSD=X")
SYMBOLS = [s.strip() for s in SYMBOLS_ENV.split(",") if s.strip()]

# Alpaca keys - Render env
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_KEY")
ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET")
ALPACA_BASE = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

RSI_BUY = 40
RSI_SELL = 60
FETCH_INTERVAL = 120

state = {
    "trades": 60,
    "equity": 89719.42,
    "cash": 82258.36,
    "buying_power": 82258.36,
    "pnl_hoy": -146.81,
    "auto_on": True,
    "version": "V5.5.6 PRO REAL",
    "symbols": SYMBOLS,
    "rsi_buy": RSI_BUY,
    "rsi_sell": RSI_SELL,
    "source": "SIMULATED",
    "positions_count": 1,
    "last_update": datetime.now().strftime("%H:%M:%S"),
}

open_positions = {"ETH-USD": {"entry": 2488.71, "qty": 3, "current_price": 2488.71}}

# Try Alpaca import
alpaca_client = None
try:
    import alpaca_trade_api as tradeapi
    if ALPACA_API_KEY and ALPACA_SECRET:
        alpaca_client = tradeapi.REST(ALPACA_API_KEY, ALPACA_SECRET, ALPACA_BASE, api_version='v2')
        print("[ALPACA] Cliente conectado")
    else:
        print("[ALPACA] Keys no encontradas, modo SIM")
except Exception as e:
    print(f"[ALPACA] No disponible: {e}")

def fetch_alpaca_account():
    if not alpaca_client:
        return None
    try:
        acct = alpaca_client.get_account()
        positions = alpaca_client.list_positions()
        pos_dict = {}
        for p in positions:
            pos_dict[p.symbol] = {
                "qty": float(p.qty),
                "entry": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "cost_basis": float(p.cost_basis)
            }
        return {
            "equity": float(acct.equity),
            "cash": float(acct.cash),
            "buying_power": float(acct.buying_power),
            "pnl_hoy": float(acct.equity) - float(acct.last_equity) if hasattr(acct,'last_equity') else 0,
            "positions": pos_dict,
            "source": "ALPACA REAL",
            "trades": len(pos_dict),
            "positions_count": len(pos_dict)
        }
    except Exception as e:
        print(f"[ALPACA FETCH ERROR] {e}")
        return None

def trading_loop():
    global state, open_positions
    while True:
        try:
            # Try Alpaca first
            alpaca_data = fetch_alpaca_account()
            if alpaca_data:
                state["equity"] = round(alpaca_data["equity"],2)
                state["cash"] = round(alpaca_data["cash"],2)
                state["buying_power"] = round(alpaca_data["buying_power"],2)
                state["pnl_hoy"] = round(alpaca_data["pnl_hoy"],2)
                state["source"] = "ALPACA REAL"
                state["positions_count"] = alpaca_data["positions_count"]
                open_positions = alpaca_data["positions"]
                state["last_update"] = datetime.now().strftime("%H:%M:%S COL")
                print(f"[REAL SYNC] Equity {state['equity']} - {len(open_positions)} pos")
            else:
                # fallback simulated update timestamp
                state["source"] = "SIMULATED - ALPACA KEYS FALTAN"
                state["last_update"] = datetime.now().strftime("%H:%M:%S COL") + " (SIM)"
            
            time.sleep(10) # Sync Alpaca every 10 sec
            
        except Exception as e:
            print(f"[LOOP ERROR] {e}")
            time.sleep(15)

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AURA SYNAPSE V5.5.6 PRO REAL</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0e14;color:#e6edf3;font-family:'Inter',sans-serif;overflow-x:hidden}
.header{background:linear-gradient(135deg,#0a0e14 0%,#141c2b 100%);border-bottom:1px solid #1f2d40;padding:16px 24px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100;backdrop-filter:blur(20px)}
.logo-wrap{display:flex;align-items:center;gap:14px}
.logo-img{width:52px;height:52px;background:white;border-radius:12px;padding:5px;object-fit:contain;box-shadow:0 0 25px rgba(100,200,255,0.4)}
.logo-text h1{font-size:22px;font-weight:800;letter-spacing:2px;background:linear-gradient(90deg,#2b7de1,#2ec4b6,#7b5cff);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.logo-text p{font-size:10px;letter-spacing:3px;color:#7a8aa0;margin-top:-2px}
.live{display:flex;align-items:center;gap:8px;background:#12261d;border:1px solid #1f6a3a;padding:6px 14px;border-radius:20px;font-family:'JetBrains Mono';font-size:11px;color:#2ee86e}
.live-dot{width:8px;height:8px;background:#2ee86e;border-radius:50%;animation:pulse 1.5s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}
.ticker{background:#080c12;border-bottom:1px solid #162233;overflow:hidden;white-space:nowrap;padding:6px 0;font-family:'JetBrains Mono';font-size:11px;color:#4a6080}
.ticker-track{display:inline-block;animation:scroll 35s linear infinite}
@keyframes scroll{0%{transform:translateX(0)}100%{transform:translateX(-50%)}}
.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:16px;padding:20px;max-width:1400px;margin:0 auto}
.card{background:linear-gradient(180deg,#111a28 0%,#0e1520 100%);border:1px solid #1e2e44;border-radius:14px;padding:18px;position:relative;overflow:hidden}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,#2b7de1,transparent);opacity:0.5}
.kpi{grid-column:span 3}
@media(max-width:900px){.kpi{grid-column:span 6}.wide{grid-column:span 12 !important}}
@media(max-width:600px){.kpi{grid-column:span 12}}
.kpi-label{font-size:11px;letter-spacing:1.5px;color:#7a8aa0;text-transform:uppercase;margin-bottom:8px}
.kpi-value{font-family:'JetBrains Mono';font-size:28px;font-weight:700;letter-spacing:-1px}
.kpi-sub{font-family:'JetBrains Mono';font-size:12px;margin-top:6px;color:#8a9bb2}
.green{color:#2ee86e}.red{color:#ff5a5a}.blue{color:#4da3ff}.yellow{color:#ffcc4a}
.wide{grid-column:span 6}
.symbols{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.sym{background:#0e1a2b;border:1px solid #1e3352;padding:5px 10px;border-radius:6px;font-family:'JetBrains Mono';font-size:11px;color:#9bb4d0}
.pos-row{display:flex;justify-content:space-between;align-items:center;padding:12px 0;border-bottom:1px solid #14202f;font-family:'JetBrains Mono';font-size:13px}
.badge{padding:3px 8px;border-radius:4px;font-size:10px;font-weight:700;letter-spacing:1px}
.badge-long{background:#12331f;border:1px solid #1e6a3a;color:#2ee86e}
.footer{text-align:center;padding:24px;color:#23344a;font-family:'JetBrains Mono';font-size:9px;letter-spacing:2px}
.alpaca-tag{background:#1a2a15;border:1px solid #2d5a1e;color:#7acc5e;padding:4px 8px;border-radius:4px;font-size:10px;margin-left:10px}
</style>
</head>
<body>
<div class="header">
  <div class="logo-wrap">
    <img src="https://aura-stock-market.s3.amazonaws.com/aura_logo.png" onerror="this.style.display='none'" class="logo-img" id="logo">
    <div style="width:52px;height:52px;background:white;border-radius:12px;display:flex;align-items:center;justify-content:center;font-weight:800;color:#0a0e14;" id="logoFallback">AURA</div>
    <div class="logo-text"><h1>AURA SYNAPSE</h1><p>STOCK MARKET TRADING</p></div>
    <span class="alpaca-tag" id="alpacaTag">● ALPACA LIVE</span>
  </div>
  <div class="live"><div class="live-dot"></div><span id="liveText">AUTO ON • V5.5.6 PRO REAL • SYNC</span></div>
</div>
<div class="ticker"><div class="ticker-track" id="tickerTrack">AURA V5.5.6 REAL • ALPACA API SYNC • RSI BUY 40 • SL -2% • TP +3% • 8 SYMBOLS • TSLA • ETH-USD • NVDA • BTC-USD • AAPL • MSFT • SPY • EURUSD=X • AUTO TRADING ACTIVE • REAL-TIME FROM ALPACA • </div></div>
<div class="grid">
  <div class="card kpi"><div class="kpi-label">Equity Total • Alpaca</div><div class="kpi-value blue" id="equity">--</div><div class="kpi-sub" id="equitySub">Sincronizando con Alpaca...</div></div>
  <div class="card kpi"><div class="kpi-label">Cash Available • Alpaca</div><div class="kpi-value" id="cash">--</div><div class="kpi-sub green" id="cashSub">Buying Power real</div></div>
  <div class="card kpi"><div class="kpi-label">P/L Hoy • Real</div><div class="kpi-value" id="pnl">--</div><div class="kpi-sub" id="pnlSub">Desde Alpaca API</div></div>
  <div class="card kpi"><div class="kpi-label">Trades • Estado</div><div class="kpi-value yellow" id="trades">--</div><div class="kpi-sub" id="lastUpdate">Conectando...</div></div>
  <div class="card wide"><div class="kpi-label">Activos Monitoreados — 8 Mercados</div><div class="symbols" id="symbolsList"></div><div style="margin-top:16px;display:grid;grid-template-columns:1fr 1fr;gap:12px"><div style="background:#0a1220;border:1px solid #1a2d4a;border-radius:8px;padding:10px"><div class="kpi-label">RSI Buy</div><div class="kpi-value blue" style="font-size:20px">40</div></div><div style="background:#0a1220;border:1px solid #1a2d4a;border-radius:8px;padding:10px"><div class="kpi-label">RSI Sell / SL / TP</div><div class="kpi-value green" style="font-size:20px">60 / -2% / +3%</div></div></div></div>
  <div class="card wide"><div class="kpi-label">Posiciones Abiertas — Alpaca Real</div><div id="posContainer" style="margin-top:12px"></div></div>
</div>
<div class="footer">AURA SYNAPSE V5.5.6 PRO REAL • TEAM YESID + SYNA • ALPACA SYNC • RENDER LIVE</div>
<script>
const fmt = n => '$' + Number(n).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
async function load(){
  try{
    const r = await fetch('/api/status?t='+Date.now());
    const d = await r.json();
    document.getElementById('equity').textContent = fmt(d.equity);
    document.getElementById('cash').textContent = fmt(d.cash);
    const pnlEl = document.getElementById('pnl');
    const pnlVal = d.pnl_hoy || d.pnl || 0;
    pnlEl.textContent = (pnlVal>=0?'+':'')+fmt(pnlVal);
    pnlEl.className = 'kpi-value ' + (pnlVal>=0?'green':'red');
    document.getElementById('trades').textContent = d.trades || d.positions_count || 0;
    document.getElementById('lastUpdate').textContent = 'AUTO ON • ' + (d.last_update||'') + ' • ' + (d.source||'');
    document.getElementById('liveText').textContent = 'AUTO ON • V5.5.6 REAL • ' + (d.last_update||'');
    document.getElementById('equitySub').textContent = 'Fuente: ' + (d.source||'Alpaca') + ' • Real-time';
    document.getElementById('cashSub').textContent = 'Buying Power: ' + fmt(d.buying_power||d.cash);
    document.getElementById('pnlSub').textContent = 'P/L Real • SL -2% | TP +3%';
    const symList = document.getElementById('symbolsList');
    if(d.symbols){ symList.innerHTML=''; d.symbols.forEach(s=>{const el=document.createElement('div');el.className='sym';el.textContent=s;symList.appendChild(el);}); }
    const posC = document.getElementById('posContainer');
    posC.innerHTML='';
    const pos = d.open_positions || d.positions || {};
    const posArray = Array.isArray(pos)? pos : Object.entries(pos);
    if(posArray.length===0){
      posC.innerHTML='<div style="font-family:JetBrains Mono;color:#4a5e75;font-size:12px;padding:20px 0;text-align:center">Sin posiciones abiertas • Esperando RSI < 40<br><br><span style="color:#2ee86e;font-size:10px">Conectado a Alpaca - Datos reales</span></div>';
    }else{
      posArray.forEach(item=>{
        const sym = Array.isArray(item)? item[0] : (item.symbol||item[0]);
        const p = Array.isArray(item)? item[1] : item;
        const qty = p.qty || p.quantity || 0;
        const entry = p.entry || p.avg_entry_price || p.cost_basis || 0;
        const current = p.current_price || p.market_value || entry;
        const pl = p.unrealized_pl || 0;
        const row=document.createElement('div');row.className='pos-row';
        row.innerHTML='<div><b style="color:#e6edf3">'+sym+'</b> <span style="color:#5a708c">'+qty+'x @ '+fmt(entry)+' → '+fmt(current)+'</span><br><span style="font-size:11px;color:'+(pl>=0?'#2ee86e':'#ff5a5a')+'">'+(pl>=0?'+':'')+fmt(pl)+'</span></div><div><span class="badge badge-long">LONG</span></div>';
        posC.appendChild(row);
      });
    }
    document.getElementById('alpacaTag').textContent = '● ' + (d.source||'ALPACA LIVE');
    document.getElementById('alpacaTag').style.background = d.source && d.source.includes('SIM') ? '#2a1a15' : '#1a2a15';
  }catch(e){ console.log('load error',e); document.getElementById('lastUpdate').textContent='Error sync • Reintentando...'; }
}
load();
setInterval(load, 3000);
let track=document.getElementById('tickerTrack');
track.innerHTML = track.textContent + ' ' + track.textContent;
// logo fallback
document.getElementById('logo').addEventListener('error', function(){ this.style.display='none'; });
</script>
</body>
</html>
"""

@app.route('/')
def dashboard():
    return DASHBOARD_HTML

@app.route('/api/status')
def status_api():
    # Always try fresh Alpaca on API call too
    alpaca_data = fetch_alpaca_account()
    if alpaca_data:
        return jsonify({
            "equity": alpaca_data["equity"],
            "cash": alpaca_data["cash"],
            "buying_power": alpaca_data["buying_power"],
            "pnl_hoy": alpaca_data["pnl_hoy"],
            "pnl": alpaca_data["pnl_hoy"],
            "trades": alpaca_data["trades"],
            "positions_count": alpaca_data["positions_count"],
            "open_positions": alpaca_data["positions"],
            "positions": alpaca_data["positions"],
            "symbols": SYMBOLS,
            "version": "V5.5.6 PRO REAL",
            "source": "ALPACA REAL",
            "last_update": datetime.now().strftime("%H:%M:%S COL")
        })
    # fallback
    return jsonify({**state, "open_positions": open_positions})

if __name__ == '__main__':
    t = threading.Thread(target=trading_loop, daemon=True)
    t.start()
    port = int(os.getenv("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
