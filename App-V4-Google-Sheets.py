"""
AURA SYNAPSE V4.0 GOOGLE SHEETS INTEGRADO - Team Yesid + Syna
PASO FINAL - Con gspread real
"""
import os, time, threading, json
from datetime import datetime
import pytz
from flask import Flask, jsonify, render_template_string

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    ALPACA_OK = True
except Exception as e:
    print(f"Alpaca import error: {e}")
    ALPACA_OK = False

app = Flask(__name__)

def env(key1, key2, default=""):
    return os.environ.get(key1, os.environ.get(key2, default)).strip()

API_KEY = env('ALPACA_API_KEY', 'APCA_API_KEY_ID')
API_SECRET = env('ALPACA_SECRET_KEY', 'APCA_API_SECRET_KEY')
BASE_URL = env('ALPACA_BASE_URL', 'APCA_API_BASE_URL', 'https://paper-api.alpaca.markets')
AUTO_TRADING = env('AUTO_TRADING', 'AUTO_TRADING', 'true').lower() in ['true','1','yes']
SHEET_ID = env('GOOGLE_SHEET_ID', 'SHEET_ID', '')
GOOGLE_CREDS_JSON = os.environ.get('GOOGLE_CREDS_JSON','').strip()

SYMBOLS = ["AAPL", "TSLA", "NVDA", "MSFT", "SPY"]

estado = {
    "version": "V4.0 GOOGLE",
    "status": "Iniciando V4.0 GOOGLE...",
    "equity": 100146.55,
    "cash": 81207.15,
    "ultimo_analisis": "Nunca",
    "ultimo_heartbeat": "",
    "log": [],
    "detalles": {},
    "posiciones": [],
    "vive_desde": datetime.now(pytz.timezone('America/Bogota')).isoformat(),
    "trades_hoy": 0,
    "auto_trading": AUTO_TRADING,
    "google_sheet": bool(SHEET_ID and GOOGLE_CREDS_JSON),
    "google_status": "No configurado"
}

def get_time():
    return datetime.now(pytz.timezone('America/Bogota'))

def calc_rsi(prices, period=14):
    if len(prices) < period+1: return 50
    gains=[]; losses=[]
    for i in range(1,len(prices)):
        d=prices[i]-prices[i-1]
        gains.append(max(d,0)); losses.append(max(-d,0))
    if len(gains)<period: return 50
    avg_gain=sum(gains[-period:])/period
    avg_loss=sum(losses[-period:])/period
    if avg_loss==0: return 100
    rs=avg_gain/avg_loss
    return 100-(100/(1+rs))

def get_gspread_client():
    if not GOOGLE_CREDS_JSON or not SHEET_ID:
        return None
    try:
        import gspread
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        client = gspread.service_account_from_dict(creds_dict)
        return client
    except Exception as e:
        print(f"❌ gspread client error: {e}")
        estado["google_status"]=f"Error creds: {e}"
        return None

def init_sheet():
    """Crea encabezados si la hoja está vacía"""
    try:
        client = get_gspread_client()
        if not client: return
        sh = client.open_by_key(SHEET_ID)
        worksheet = sh.sheet1
        # Si está vacía, crea headers
        values = worksheet.get_all_values()
        if len(values)==0:
            headers = ["Fecha COL", "Símbolo", "Acción", "Precio", "RSI", "Equity", "Cash", "Mensaje", "Trade #"]
            worksheet.append_row(headers)
            estado["google_status"]="✅ Conectado y cabeceras creadas"
            print("✅ Google Sheet headers creados")
        else:
            estado["google_status"]=f"✅ Conectado - {len(values)-1} trades registrados"
    except Exception as e:
        print(f"init_sheet error: {e}")
        estado["google_status"]=f"Error init: {e}"

def send_to_sheets(row):
    if not SHEET_ID or not GOOGLE_CREDS_JSON:
        return
    try:
        import gspread
        client = get_gspread_client()
        if not client: return
        sh = client.open_by_key(SHEET_ID)
        sh.sheet1.append_row(row)
        print(f"📊 SHEETS OK: {row}")
        # Actualiza contador
        try:
            count = len(sh.sheet1.get_all_values())-1
            estado["google_status"]=f"✅ Conectado - {count} trades"
        except:
            pass
    except Exception as e:
        print(f"Sheets append error: {e}")
        estado["google_status"]=f"Error append: {e}"

def load_alpaca():
    if not API_KEY:
        estado["status"]="Esperando Keys - Configura ALPACA_API_KEY"
        return None,None
    try:
        trading=TradingClient(API_KEY, API_SECRET, paper=True)
        data_client=StockHistoricalDataClient(API_KEY, API_SECRET)
        acc=trading.get_account()
        estado["equity"]=float(acc.equity)
        estado["cash"]=float(acc.cash)
        estado["status"]=f"Activa • Paper • Auto:{'ON' if AUTO_TRADING else 'OFF'} • Sheets:{'ON' if estado['google_sheet'] else 'OFF'}"
        print(f"✅ AURA V4.0 GOOGLE CONECTADA: Equity ${float(acc.equity):.2f}")
        try:
            pos=trading.get_all_positions()
            estado["posiciones"]=[{"symbol":p.symbol,"qty":float(p.qty),"market_value":float(p.market_value),"avg_entry":float(p.avg_entry_price)} for p in pos]
        except:
            estado["posiciones"]=[]
        # Init sheet en hilo aparte para no bloquear
        if SHEET_ID and GOOGLE_CREDS_JSON:
            threading.Thread(target=init_sheet, daemon=True).start()
        return trading,data_client
    except Exception as e:
        print(f"❌ Error Alpaca: {e}")
        estado["status"]=f"Error: {e}"
        return None,None

def bot_loop():
    trading,data_client=load_alpaca()
    while True:
        try:
            if not trading:
                trading,data_client=load_alpaca()
                if not trading:
                    time.sleep(60); continue
            bog=get_time()
            estado["ultimo_heartbeat"]=bog.strftime('%H:%M:%S')
            acc=trading.get_account()
            estado["equity"]=float(acc.equity)
            estado["cash"]=float(acc.cash)
            estado["ultimo_analisis"]=bog.strftime('%Y-%m-%d %H:%M:%S COL')
            es_laborable=bog.weekday()<5
            hora=bog.hour+bog.minute/60
            mercado_abierto=es_laborable and (8.5 <= hora <= 15.0)

            detalles={}; logs=[]
            for sym in SYMBOLS:
                try:
                    req=StockBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Minute, limit=100)
                    bars=data_client.get_stock_bars(req).df
                    if len(bars)==0: continue
                    if sym in bars.index.get_level_values(0):
                        df=bars.loc[sym]
                    else:
                        df=bars
                    if len(df)<20: continue
                    closes=df['close'].tolist()
                    precio=closes[-1]
                    rsi=calc_rsi(closes)
                    if rsi<30: dec="COMPRAR FUERTE"; color="#00ff88"; action="BUY"
                    elif rsi<40: dec="COMPRAR"; color="#00cc66"; action="BUY"
                    elif rsi>75: dec="VENDER FUERTE"; color="#ff4444"; action="SELL"
                    elif rsi>65: dec="VENDER"; color="#ffaa00"; action="SELL"
                    else: dec="NEUTRAL"; color="#888"; action="HOLD"
                    detalles[sym]={"precio":precio,"rsi":round(rsi,1),"decision":dec,"color":color,"action":action}
                    if mercado_abierto:
                        logs.append(f"{sym} ${precio:.2f} RSI {rsi:.1f} -> {dec}")
                        if AUTO_TRADING and action in ["BUY","SELL"] and (rsi<40 or rsi>65):
                            try:
                                positions={p.symbol: p for p in trading.get_all_positions()}
                                has_position=sym in positions
                                if action=="BUY" and not has_position and rsi<40 and estado["cash"]>1000:
                                    qty=max(1,int((estado["cash"]*0.19)//precio))
                                    order=MarketOrderRequest(symbol=sym, qty=qty, side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
                                    trading.submit_order(order)
                                    msg=f"✅ COMPRA REAL {sym} x{qty} @ ${precio:.2f} RSI {rsi:.1f}"
                                    logs.append(msg)
                                    estado["trades_hoy"]+=1
                                    row=[bog.strftime('%Y-%m-%d %H:%M:%S'), sym, "COMPRA", f"{precio:.2f}", f"{rsi:.1f}", f"{estado['equity']:.2f}", f"{estado['cash']:.2f}", msg, estado["trades_hoy"]]
                                    send_to_sheets(row)
                                elif action=="SELL" and has_position and rsi>65:
                                    qty=float(positions[sym].qty)
                                    order=MarketOrderRequest(symbol=sym, qty=qty, side=OrderSide.SELL, time_in_force=TimeInForce.DAY)
                                    trading.submit_order(order)
                                    msg=f"🔻 VENTA REAL {sym} x{qty} @ ${precio:.2f} RSI {rsi:.1f}"
                                    logs.append(msg)
                                    estado["trades_hoy"]+=1
                                    row=[bog.strftime('%Y-%m-%d %H:%M:%S'), sym, "VENTA", f"{precio:.2f}", f"{rsi:.1f}", f"{estado['equity']:.2f}", f"{estado['cash']:.2f}", msg, estado["trades_hoy"]]
                                    send_to_sheets(row)
                            except Exception as trade_e:
                                logs.append(f"⚠️ {sym} trade error: {trade_e}")
                    else:
                        logs.append(f"{sym} ${precio:.2f} RSI {rsi:.1f} -> {dec} [Mercado cerrado]")
                except Exception as e:
                    print(f"{sym} err: {e}")
            if not mercado_abierto:
                logs.insert(0, f"⏸️ Mercado cerrado - Standby {bog.strftime('%H:%M:%S COL')} - Apertura 8:30 AM COL Lun")
            estado["detalles"]=detalles
            estado["log"]=logs[:25]
            print(f"🧠 V4.0 GOOGLE {estado['ultimo_analisis']} Equity ${estado['equity']:.2f} Trades:{estado['trades_hoy']} Sheets:{estado['google_status']}")
            time.sleep(120 if mercado_abierto else 180)
        except Exception as e:
            print(f"Loop err: {e}"); time.sleep(60)

threading.Thread(target=bot_loop, daemon=True).start()

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AURA V4.0 GOOGLE - Yesid + Syna</title>
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
.card-value{font-family:'JetBrains Mono',monospace;font-size:24px;font-weight:700}
.card-sub{font-size:11px;color:#666;margin-top:6px;line-height:1.5;word-break:break-word}
.grid-symbols{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;margin-bottom:30px}
.symbol-card{background:#15151f;border:1px solid #222;border-radius:12px;padding:18px;display:flex;justify-content:space-between;align-items:center;transition:0.2s}
.symbol-card:hover{border-color:#444;transform:translateY(-2px)}
.symbol-name{font-family:'JetBrains Mono',monospace;font-size:20px;font-weight:700}
.rsi-badge{padding:6px 12px;border-radius:20px;font-size:11px;font-weight:700;font-family:'JetBrains Mono',monospace;margin-top:6px;display:inline-block}
.log{background:#0f0f18;border:1px solid #222;border-radius:12px;padding:20px;font-family:'JetBrains Mono',monospace;font-size:12px;min-height:140px;max-height:320px;overflow-y:auto}
.log-line{padding:5px 0;border-bottom:1px solid #1a1a25;color:#aaa}
.footer{margin-top:40px;text-align:center;color:#444;font-size:11px;letter-spacing:1px;line-height:1.6}
.live{color:#00ff88}
a{color:#00aaff;text-decoration:none}
.badge{font-size:10px;padding:3px 8px;border-radius:10px;background:#00ff8815;color:#00ff88;border:1px solid #00ff8830;margin-left:8px}
.pos{font-family:'JetBrains Mono',monospace;font-size:11px;color:#aaa;margin-top:3px}
</style>
<script>
setInterval(()=>{fetch('/api').then(r=>r.json()).then(d=>{
 document.getElementById('equity').innerText='$'+Number(d.equity).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
 document.getElementById('cash').innerText='$'+Number(d.cash).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
 document.getElementById('status').innerText=d.status;
 document.getElementById('time').innerText=d.ultimo_analisis;
 document.getElementById('heartbeat').innerText=d.ultimo_heartbeat||'--';
 document.getElementById('trades').innerText=d.trades_hoy+' hoy';
 document.getElementById('google').innerText=d.google_status||'No configurado';
 let html='';
 for(let s of d.simbolos){
   let det=d.detalles[s]||{precio:'...',rsi:'-',decision:'CARGANDO',color:'#888',action:'HOLD'};
   html+=`<div class="symbol-card"><div><div class="symbol-name">${s} <span class="badge">${det.action}</span></div><div style="font-size:11px;color:#666">RSI ${det.rsi} • ${det.precio!=='...'?'$'+Number(det.precio).toFixed(2):det.precio}</div></div><div style="text-align:right"><div class="rsi-badge" style="background:${det.color}20;color:${det.color};border:1px solid ${det.color}40">${det.decision}</div></div></div>`;
 }
 document.getElementById('symbols').innerHTML=html;
 let logHtml='';
 (d.log||[]).forEach(l=>{
   let c='#aaa'; if(l.includes('COMPRA REAL')) c='#00ff88'; if(l.includes('VENTA REAL')) c='#ff4444'; if(l.includes('Mercado cerrado')||l.includes('Standby')) c='#666';
   logHtml+=`<div class="log-line" style="color:${c}">${l}</div>`;
 });
 document.getElementById('log').innerHTML=logHtml||'<div style="color:#555">Cargando...</div>';
 let posHtml='';
 if(d.posiciones && d.posiciones.length>0){
   d.posiciones.forEach(p=>{posHtml+=`<div class="pos">• ${p.symbol} x${p.qty} @ $${p.avg_entry.toFixed(2)} → $${p.market_value.toFixed(2)}</div>`});
 } else {posHtml='<div class="pos" style="color:#555">Sin posiciones abiertas</div>'}
 document.getElementById('pos').innerHTML=posHtml;
});}, 4000);
</script>
</head>
<body>
<div class="header">
 <div class="logo">🧠 AURA V4.0 GOOGLE</div>
 <div style="font-family:'JetBrains Mono',monospace;font-size:12px"><span class="status-dot"></span><span id="status" class="live">Cargando...</span> • <span id="time">...</span> • HB <span id="heartbeat">--</span> • <span id="trades">0 hoy</span></div>
</div>
<div class="cards">
 <div class="card"><div class="card-label">Equity Total (Paper)</div><div class="card-value" id="equity">$100,146</div><div class="card-sub">Alpaca Paper • V4.0 GOOGLE • Live<br><a href="/api/debug">Debug</a> | <a href="/health">Health</a> | <a href="/api">API JSON</a></div><div id="pos" style="margin-top:12px"></div></div>
 <div class="card"><div class="card-label">Cash Disponible</div><div class="card-value" id="cash" style="color:#00aaff">$81,207</div><div class="card-sub">19% por trade • Auto: {{auto}}<br>Google: <span id="google">Cargando...</span><br><a href="https://docs.google.com/spreadsheets/d/{{sheet_id}}" target="_blank" style="color:#00ff88">Abrir mi Sheet ↗</a></div></div>
 <div class="card"><div class="card-label">Creadores • Sistema</div><div class="card-value" style="font-size:16px">Yesid + Syna<br><span style="font-size:11px;color:#888">Madrid, COL → Render 24/7<br>5 Expertas: AAPL TSLA NVDA MSFT SPY<br>Vive: {{vive}}<br>RSI<40 Compra • RSI>65 Venta</span></div></div>
</div>
<h3 style="margin-bottom:12px;color:#888;font-size:13px;letter-spacing:1px;text-transform:uppercase">Mercado en Tiempo Real - 5 Expertas RSI</h3>
<div id="symbols" class="grid-symbols"><div style="color:#555">Cargando 5 símbolos...</div></div>
<h3 style="margin-bottom:12px;color:#888;font-size:13px;letter-spacing:1px;text-transform:uppercase">Log de Syna + Trades Reales → Google Sheets</h3>
<div id="log" class="log">Iniciando cerebro V4.0 GOOGLE...</div>
<div class="footer">AURA SYNAPSE V4.0 GOOGLE SHEETS • RENDER FREE • PAPER TRADING • AUTO-TRADING • NO ES ASESORÍA FINANCIERA • HECHO CON 💚 EN MADRID<br>URL: https://aura-synapse-yesid.onrender.com • Estrategia: RSI 14 • 08:30-15:00 COL Lun-Vie</div>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(DASHBOARD_HTML, vive=estado["vive_desde"], auto="ON" if AUTO_TRADING else "OFF", sheet_id=SHEET_ID)

@app.route('/api')
def api():
    return jsonify({
        "mensaje": "🧠 AURA V4.0 GOOGLE VIVE",
        "version": estado["version"],
        "status": estado["status"],
        "equity": estado["equity"],
        "cash": estado["cash"],
        "ultimo_analisis": estado["ultimo_analisis"],
        "ultimo_heartbeat": estado["ultimo_heartbeat"],
        "log": estado["log"],
        "detalles": estado["detalles"],
        "posiciones": estado["posiciones"],
        "vive_desde": estado["vive_desde"],
        "simbolos": SYMBOLS,
        "trades_hoy": estado["trades_hoy"],
        "auto_trading": AUTO_TRADING,
        "google_sheet": estado["google_sheet"],
        "google_status": estado["google_status"],
        "paper": True
    })

@app.route('/api/debug')
def debug():
    return jsonify({
        "status": "ALPACA_REAL" if API_KEY else "SIN_KEYS",
        "version": "V4.0 GOOGLE",
        "running": True,
        "symbols": SYMBOLS,
        "equity": estado["equity"],
        "has_keys": bool(API_KEY),
        "auto": AUTO_TRADING,
        "google_configured": bool(SHEET_ID and GOOGLE_CREDS_JSON),
        "google_status": estado["google_status"]
    })

@app.route('/health')
def health():
    return "OK AURA V4.0 GOOGLE", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"🚀 AURA V4.0 GOOGLE puerto {port} Keys:{'OK' if API_KEY else 'FALTAN'} Auto:{AUTO_TRADING} Sheets:{bool(SHEET_ID)}")
    app.run(host='0.0.0.0', port=port)
