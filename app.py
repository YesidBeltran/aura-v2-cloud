
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
    "version": "V6.0 AUTO REAL", "symbols": SYMBOLS,
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
<title>AURA V6.0 AUTO REAL</title>
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
<div class="header"><div style="display:flex;gap:12px;align-items:center"><div style="width:42px;height:42px;background:white;border-radius:10px;display:flex;align-items:center;justify-content:center;color:#0a0e14;font-weight:800">AURA</div><div><div class="logo">AURA SYNAPSE V6.0</div><div style="font-size:9px;color:#7a8aa0;letter-spacing:3px">AUTO TRADING REAL</div></div><span class="badge real" id="realTag">● ALPACA REAL</span><span class="badge auto">● AUTO TRADING ON</span></div><div class="badge real" id="timeTag">--</div></div>
<div style="background:#080c12;border-bottom:1px solid #162233;padding:6px 0;overflow:hidden;white-space:nowrap;font-family:JetBrains Mono;font-size:10px;color:#4a6080"><span id="ticker">V6.0 AUTO REAL • RSI BUY 40 • RSI SELL 60 • SL -2% • TP +3% • ORDENES REALES EN ALPACA PAPER • SCAN CADA 60s • </span></div>
<div class="grid">
<div class="card kpi"><div class="label">Equity Total • Alpaca</div><div class="val blue" id="equity">--</div><div class="label" id="eqSub">ALPACA REAL</div></div>
<div class="card kpi"><div class="label">Cash + Buying Power</div><div class="val" id="cash">--</div><div class="label green" id="bp">--</div></div>
<div class="card kpi"><div class="label">P/L Hoy • Real</div><div class="val" id="pnl">--</div><div class="label" id="sig">Esperando señal...</div></div>
<div class="card kpi"><div class="label">Posiciones • Trades</div><div class="val yellow" id="trades">--</div><div class="label" id="status">AUTO ON</div></div>
<div class="card wide"><div class="label">Activos Monitoreados — Auto Scan 60s</div><div id="syms" style="margin-top:10px"></div><div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px"><div style="background:#0a1220;border:1px solid #1a2d4a;border-radius:8px;padding:10px"><div class="label">Estrategia</div><div class="val green" style="font-size:14px">RSI 40/60 • SL -2% • TP +3%</div><div class="label" style="margin-top:4px">5% BP por trade, max 10 acciones</div></div><div style="background:#0a1220;border:1px solid #1a2d4a;border-radius:8px;padding:10px"><div class="label">Logs en vivo — Ordenes Reales</div><div class="log" id="logs">Iniciando auto trader...</div></div></div></div>
<div class="card wide"><div class="label">Posiciones Abiertas — Alpaca Real — Auto Gestionadas</div><div id="pos" style="margin-top:10px"></div></div>
</div>
<div style="text-align:center;padding:20px;color:#23344a;font-family:JetBrains Mono;font-size:9px;letter-spacing:2px">AURA SYNAPSE V6.0 AUTO REAL • TEAM YESID + SYNA • ORDENES REALES ALPACA PAPER • RENDER LIVE</div>
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
