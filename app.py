
import os
import time
import requests
from flask import Flask, jsonify
import threading
from datetime import datetime, timedelta
import math

app = Flask(__name__)

SYMBOLS_ENV = os.getenv("SYMBOLS", "TSLA,ETH-USD,NVDA,BTC-USD,AAPL,MSFT,SPY,EURUSD=X")
SYMBOLS = [s.strip() for s in SYMBOLS_ENV.split(",") if s.strip()]
# Filter only tradable stocks for auto trading - crypto handled separately
STOCK_SYMBOLS = [s for s in SYMBOLS if "-USD" not in s and "=X" not in s]
CRYPTO_SYMBOLS = [s for s in SYMBOLS if "-USD" in s]

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
ALPACA_BASE = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")
DATA_BASE = "https://data.alpaca.markets"

print(f"[AURA V6.0 AUTO] Stocks: {STOCK_SYMBOLS} Crypto: {CRYPTO_SYMBOLS}")

state = {
    "equity": 0, "cash": 0, "buying_power": 0, "pnl_hoy": 0,
    "trades": 0, "positions_count": 0,
    "version": "V6.1 AUTO REAL + LOGO", "symbols": SYMBOLS,
    "source": "INIT", "last_update": "", "last_error": "",
    "auto_status": "AUTO ON - RSI 40/60", "last_signal": "Esperando...",
    "logs": []
}

open_positions = {}
headers = {"APCA-API-KEY-ID": ALPACA_API_KEY or "", "APCA-API-SECRET-KEY": ALPACA_SECRET or ""}

def log_msg(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    state["logs"] = ([entry] + state["logs"])[:20]
    print(entry)
    state["last_signal"] = msg

def fetch_alpaca_direct():
    if not ALPACA_API_KEY or not ALPACA_SECRET:
        return None, "Keys missing"
    try:
        r = requests.get(f"{ALPACA_BASE}/v2/account", headers=headers, timeout=10)
        if r.status_code != 200:
            return None, f"Account {r.status_code}: {r.text[:150]}"
        acct = r.json()
        r2 = requests.get(f"{ALPACA_BASE}/v2/positions", headers=headers, timeout=10)
        positions = r2.json() if r2.status_code == 200 else []
        pos_dict = {}
        for p in positions:
            try:
                pos_dict[p['symbol']] = {
                    "qty": float(p['qty']), "entry": float(p['avg_entry_price']),
                    "current_price": float(p['current_price']), "market_value": float(p['market_value']),
                    "unrealized_pl": float(p['unrealized_pl']), "cost_basis": float(p['cost_basis'])
                }
            except: pass
        pnl = float(acct.get('equity',0)) - float(acct.get('last_equity', acct.get('equity',0)))
        return {
            "equity": float(acct.get('equity',0)), "cash": float(acct.get('cash',0)),
            "buying_power": float(acct.get('buying_power',0)), "pnl_hoy": pnl,
            "positions": pos_dict, "trades": len(pos_dict), "positions_count": len(pos_dict)
        }, None
    except Exception as e:
        return None, str(e)

def get_bars(symbol, limit=50):
    """Fetch bars from Alpaca Data API"""
    try:
        # Stocks
        if symbol in STOCK_SYMBOLS or "=" not in symbol and "-USD" not in symbol:
            url = f"{DATA_BASE}/v2/stocks/{symbol}/bars"
            params = {"timeframe": "15Min", "limit": limit, "adjustment": "raw", "feed": "iex"}
            r = requests.get(url, headers=headers, params=params, timeout=10)
            if r.status_code != 200:
                # try with yahoo fallback via simple API? return None
                return None
            data = r.json()
            bars = data.get('bars', [])
            closes = [float(b['c']) for b in bars]
            return closes
        else:
            return None
    except Exception as e:
        print(f"Bars error {symbol}: {e}")
        return None

def calc_rsi(closes, period=14):
    if not closes or len(closes) < period+1:
        return 50
    gains = []
    losses = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i-1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
    if len(gains) < period:
        return 50
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi,2)

def place_order(symbol, qty, side, type="market", time_in_force="gtc"):
    try:
        url = f"{ALPACA_BASE}/v2/orders"
        payload = {"symbol": symbol, "qty": qty, "side": side, "type": type, "time_in_force": time_in_force}
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        if r.status_code in [200,201]:
            log_msg(f"✅ ORDEN REAL {side.upper()} {qty}x {symbol} - {r.json().get('id','')[:8]}")
            return True, r.json()
        else:
            log_msg(f"❌ Orden falló {symbol}: {r.status_code} {r.text[:150]}")
            return False, r.text
    except Exception as e:
        log_msg(f"❌ Excepción orden {symbol}: {e}")
        return False, str(e)

def trading_logic():
    global state, open_positions
    while True:
        try:
            data, err = fetch_alpaca_direct()
            if data:
                state["equity"] = round(data["equity"],2)
                state["cash"] = round(data["cash"],2)
                state["buying_power"] = round(data["buying_power"],2)
                state["pnl_hoy"] = round(data["pnl_hoy"],2)
                state["positions_count"] = data["positions_count"]
                state["trades"] = data["trades"]
                state["source"] = "ALPACA REAL AUTO"
                state["last_update"] = datetime.now().strftime("%H:%M:%S COL")
                open_positions = data["positions"]
            else:
                state["last_error"] = err
                time.sleep(15)
                continue

            # Check each stock for signals
            for sym in STOCK_SYMBOLS:
                try:
                    closes = get_bars(sym, limit=60)
                    if not closes:
                        continue
                    rsi = calc_rsi(closes)
                    current_price = closes[-1] if closes else 0
                    in_position = sym in open_positions

                    # P/L check for open positions
                    if in_position:
                        pos = open_positions[sym]
                        entry = pos['entry']
                        pnl_pct = ((current_price - entry) / entry * 100) if entry else 0
                        # SL -2% / TP +3% / RSI >60
                        if pnl_pct <= -2.0:
                            log_msg(f"🔴 SL -2% {sym} {pnl_pct:.2f}% - VENDIENDO")
                            place_order(sym, pos['qty'], "sell")
                        elif pnl_pct >= 3.0:
                            log_msg(f"🟢 TP +3% {sym} {pnl_pct:.2f}% - VENDIENDO")
                            place_order(sym, pos['qty'], "sell")
                        elif rsi > 60:
                            log_msg(f"📉 RSI SELL {sym} RSI={rsi} - VENDIENDO")
                            place_order(sym, pos['qty'], "sell")
                    else:
                        # BUY signal RSI < 40
                        if rsi < 40 and current_price > 0 and state["buying_power"] > 1000:
                            # qty = 5% of buying power / price
                            qty_to_buy = max(1, int((state["buying_power"] * 0.05) / current_price))
                            # limit to max 10 for safety in paper
                            qty_to_buy = min(qty_to_buy, 10)
                            log_msg(f"🚀 RSI BUY {sym} RSI={rsi} Price=${current_price:.2f} Qty={qty_to_buy}")
                            place_order(sym, qty_to_buy, "buy")
                        else:
                            # just log scan
                            pass
                    time.sleep(1)  # avoid rate limit
                except Exception as e:
                    print(f"Logic error {sym}: {e}")

            state["last_signal"] = f"Escaneo {datetime.now().strftime('%H:%M:%S')} - RSI checked {len(STOCK_SYMBOLS)} stocks"
            time.sleep(60)  # scan every 60 sec

        except Exception as e:
            log_msg(f"Loop error: {e}")
            time.sleep(30)

DASHBOARD_HTML = """
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AURA V6.1 AUTO REAL + LOGO</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Inter:wght@400;800&display=swap" rel="stylesheet">
<style>*{margin:0;padding:0;box-sizing:border-box}body{background:#0a0e14;color:#e6edf3;font-family:Inter} 
.header{background:#0e1520;border-bottom:1px solid #1f2d40;padding:14px 20px;display:flex;justify-content:space-between;align-items:center}
.logo{font-weight:800;font-size:20px;letter-spacing:2px;background:linear-gradient(90deg,#2b7de1,#2ec4b6);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.badge{padding:6px 12px;border-radius:20px;font-family:JetBrains Mono;font-size:11px}
.real{background:#12261d;border:1px solid #1f6a3a;color:#2ee86e}.auto{background:#1a2312;border:1px solid #3a5a1e;color:#aaff5e;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.6}}
.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:14px;padding:16px;max-width:1400px;margin:auto}
.card{background:#111a28;border:1px solid #1e2e44;border-radius:12px;padding:16px}.kpi{grid-column:span 3}@media(max-width:900px){.kpi{grid-column:span 6}}@media(max-width:600px){.kpi{grid-column:span 12}}
.label{font-size:10px;color:#7a8aa0;letter-spacing:1px;text-transform:uppercase;margin-bottom:6px}.val{font-family:JetBrains Mono;font-size:26px;font-weight:700}
.green{color:#2ee86e}.red{color:#ff5a5a}.blue{color:#4da3ff}.yellow{color:#ffcc4a}.wide{grid-column:span 6}@media(max-width:900px){.wide{grid-column:span 12}}
.pos-row{display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid #14202f;font-family:JetBrains Mono;font-size:12px}
.badge-long{background:#12331f;border:1px solid #1e6a3a;color:#2ee86e;padding:2px 8px;border-radius:4px;font-size:10px}
.log{font-family:JetBrains Mono;font-size:11px;background:#080c12;border:1px solid #162233;border-radius:8px;padding:10px;max-height:200px;overflow-y:auto;line-height:1.6}
.sym{display:inline-block;background:#0e1a2b;border:1px solid #1e3352;padding:4px 8px;border-radius:5px;font-family:JetBrains Mono;font-size:11px;margin:3px;color:#9bb4d0}
</style></head><body>
<div class="header"><div style="display:flex;gap:12px;align-items:center"><img src="data:image/png;base64,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" style="width:54px;height:54px;object-fit:contain;filter: drop-shadow(0 0 12px rgba(43,125,225,0.6));background:white;border-radius:12px;padding:4px"><div><div class="logo">AURA SYNAPSE V6.1</div><div style="font-size:9px;color:#7a8aa0;letter-spacing:3px">AUTO TRADING REAL</div></div><span class="badge real" id="realTag">● ALPACA REAL</span><span class="badge auto">● AUTO TRADING ON</span></div><div class="badge real" id="timeTag">--</div></div>
<div style="background:#080c12;border-bottom:1px solid #162233;padding:6px 0;overflow:hidden;white-space:nowrap;font-family:JetBrains Mono;font-size:10px;color:#4a6080"><span id="ticker">V6.1 AUTO REAL + LOGO • RSI BUY 40 • RSI SELL 60 • SL -2% • TP +3% • ORDENES REALES EN ALPACA PAPER • SCAN CADA 60s • </span></div>
<div class="grid">
<div class="card kpi"><div class="label">Equity Total • Alpaca</div><div class="val blue" id="equity">--</div><div class="label" id="eqSub">ALPACA REAL</div></div>
<div class="card kpi"><div class="label">Cash + Buying Power</div><div class="val" id="cash">--</div><div class="label green" id="bp">--</div></div>
<div class="card kpi"><div class="label">P/L Hoy • Real</div><div class="val" id="pnl">--</div><div class="label" id="sig">Esperando señal...</div></div>
<div class="card kpi"><div class="label">Posiciones • Trades</div><div class="val yellow" id="trades">--</div><div class="label" id="status">AUTO ON</div></div>
<div class="card wide"><div class="label">Activos Monitoreados — Auto Scan 60s</div><div id="syms" style="margin-top:10px"></div><div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px"><div style="background:#0a1220;border:1px solid #1a2d4a;border-radius:8px;padding:10px"><div class="label">Estrategia</div><div class="val green" style="font-size:14px">RSI 40/60 • SL -2% • TP +3%</div><div class="label" style="margin-top:4px">5% BP por trade, max 10 acciones</div></div><div style="background:#0a1220;border:1px solid #1a2d4a;border-radius:8px;padding:10px"><div class="label">Logs en vivo — Ordenes Reales</div><div class="log" id="logs">Iniciando auto trader...</div></div></div></div>
<div class="card wide"><div class="label">Posiciones Abiertas — Alpaca Real — Auto Gestionadas</div><div id="pos" style="margin-top:10px"></div></div>
</div>
<div style="text-align:center;padding:20px;color:#23344a;font-family:JetBrains Mono;font-size:9px;letter-spacing:2px">AURA SYNAPSE V6.1 AUTO REAL • TEAM YESID + SYNA • ORDENES REALES ALPACA PAPER • RENDER LIVE</div>
<script>
const fmt=n=>'$'+Number(n).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
async function load(){
 try{
  const r=await fetch('/api/status?t='+Date.now());const d=await r.json();
  document.getElementById('equity').textContent=fmt(d.equity);
  document.getElementById('cash').textContent=fmt(d.cash);
  document.getElementById('bp').textContent='BP: '+fmt(d.buying_power);
  const pnl=d.pnl_hoy||0;const pnlEl=document.getElementById('pnl');pnlEl.textContent=(pnl>=0?'+':'')+fmt(pnl);pnlEl.className='val '+(pnl>=0?'green':'red');
  document.getElementById('trades').textContent=d.positions_count+' posiciones';
  document.getElementById('timeTag').textContent=d.last_update||'--';
  document.getElementById('sig').textContent=d.last_signal||'--';
  document.getElementById('status').textContent=(d.source||'')+' • '+ (d.auto_status||'AUTO ON');
  document.getElementById('eqSub').textContent='Fuente: '+(d.source||'');
  const syms=document.getElementById('syms');syms.innerHTML='';(d.symbols||[]).forEach(s=>{const e=document.createElement('div');e.className='sym';e.textContent=s;syms.appendChild(e);});
  const logs=document.getElementById('logs');logs.innerHTML=(d.logs||[]).join('<br>');
  const posC=document.getElementById('pos');posC.innerHTML='';const entries=Object.entries(d.positions||{});
  if(entries.length==0){posC.innerHTML='<div style="text-align:center;color:#4a5e75;font-family:JetBrains Mono;font-size:12px;padding:20px">Sin posiciones • RSI esperando señal de compra<br><span style="color:#2ee86e;font-size:10px">Auto Trader escaneando cada 60s</span></div>'}
  else{entries.forEach(([sym,p])=>{const row=document.createElement('div');row.className='pos-row';row.innerHTML=`<div><b>${sym}</b> ${p.qty}x @ ${fmt(p.entry)} → ${fmt(p.current_price)}<br><span style="color:${p.unrealized_pl>=0?'#2ee86e':'#ff5a5a'}">${p.unrealized_pl>=0?'+':''}${fmt(p.unrealized_pl)} (${(((p.current_price-p.entry)/p.entry*100)||0).toFixed(2)}%)</span></div><div><span class="badge-long">LONG AUTO</span></div>`;posC.appendChild(row);});}
 }catch(e){console.log(e)}
}
load();setInterval(load,3000);
</script></body></html>
"""

@app.route('/')
def dash(): return DASHBOARD_HTML

@app.route('/api/status')
def status():
    return jsonify({
        "equity": state["equity"], "cash": state["cash"], "buying_power": state["buying_power"],
        "pnl_hoy": state["pnl_hoy"], "trades": state["trades"], "positions_count": state["positions_count"],
        "positions": open_positions, "symbols": SYMBOLS, "version": state["version"],
        "source": state["source"], "last_update": state["last_update"], "last_error": state["last_error"],
        "auto_status": state["auto_status"], "last_signal": state["last_signal"], "logs": state["logs"]
    })

@app.route('/api/debug')
def debug():
    data, err = fetch_alpaca_direct()
    return jsonify({"has_key": bool(ALPACA_API_KEY), "fetch_success": data is not None, "fetch_error": err, "data": data, "state": state})

if __name__ == '__main__':
    threading.Thread(target=trading_logic, daemon=True).start()
    port = int(os.getenv("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
