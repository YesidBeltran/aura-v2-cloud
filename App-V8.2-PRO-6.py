import os, threading, time, requests, pytz, base64
from collections import deque
from datetime import datetime, timedelta
from flask import Flask, jsonify, render_template_string, request, abort

app = Flask(__name__)

def env(a,b,d=""): return os.environ.get(a,os.environ.get(b,d)).strip()
API_KEY = env("ALPACA_API_KEY","APCA_API_KEY_ID")
API_SECRET = env("ALPACA_SECRET_KEY","APCA_API_SECRET_KEY")
BASE_URL = env("ALPACA_BASE_URL","APCA_API_BASE_URL","https://paper-api.alpaca.markets")
DATA_URL = "https://data.alpaca.markets"
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN","").strip()
TELE_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN","").strip()
TELE_CHAT = os.environ.get("TELEGRAM_CHAT_ID","").strip()
TZ_COL = pytz.timezone("America/Bogota")

# Logo
try:
    with open("/mnt/data/Aura.png","rb") as f:
        LOGO_B64 = base64.b64encode(f.read()).decode()
except:
    LOGO_B64 = ""

# === CONFIG TRADING PRO 6 OPERACIONES ===
WATCHLIST = [
    {"symbol":"SPY", "type":"ETF", "qty":5, "name":"SPY - Mercado USA"},
    {"symbol":"EUR/USD", "type":"FOREX", "qty":10000, "name":"EUR/USD - Euro Dolar"},
    {"symbol":"GBP/USD", "type":"FOREX", "qty":10000, "name":"GBP/USD - Libra Dolar"},
    {"symbol":"AAPL", "type":"STOCK", "qty":10, "name":"AAPL - Apple"},
    {"symbol":"TSLA", "type":"STOCK", "qty":2, "name":"TSLA - Tesla"},
    {"symbol":"NVDA", "type":"STOCK", "qty":5, "name":"NVDA - Nvidia"},
]
MAX_POSITIONS = 6
RSI_BUY = 35
RSI_SELL = 70
RSI_PERIOD = 14

estado = {
    "version": "V8.2 TRADING PRO 6",
    "equity": 0.0, "cash":0.0, "bp":0.0, "day_pl":0.0,
    "conectado": False, "running": False,
    "posiciones": [], "ordenes": [],
    "watch_rsi": {}, # symbol -> rsi
    "ultimo_error": None,
    "ultimo_heartbeat": "Nunca",
    "trades_hoy": 0,
    "logs": deque(maxlen=200)
}

def now_col(): return datetime.now(TZ_COL)
def log(m):
    txt = f"[{now_col().strftime('%H:%M:%S')}] {m}"
    estado["logs"].append(txt)
    print(txt, flush=True)

def require_token():
    if not DASHBOARD_TOKEN: return
    t = request.headers.get("X-Token") or request.args.get("token")
    if t != DASHBOARD_TOKEN: abort(401)

def send_tg(msg):
    if not TELE_TOKEN or not TELE_CHAT: return False
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELE_TOKEN}/sendMessage",
                         params={"chat_id":TELE_CHAT,"text":msg}, timeout=15)
        return r.status_code==200
    except Exception as e:
        log(f"TG {e}"); return False

def alpaca_headers():
    return {"APCA-API-KEY-ID":API_KEY,"APCA-API-SECRET-KEY":API_SECRET}

def alpaca_get(path, base=BASE_URL):
    try:
        r = requests.get(f"{base}{path}", headers=alpaca_headers(), timeout=15)
        if r.status_code==200:
            return r.json(), None
        return None, f"{path} {r.status_code}: {r.text[:150]}"
    except Exception as e:
        return None, f"{path} {e}"

def alpaca_post(path, data, base=BASE_URL):
    try:
        r = requests.post(f"{base}{path}", headers=alpaca_headers(), json=data, timeout=15)
        if r.status_code in [200,201]:
            return r.json(), None
        return None, f"{path} {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return None, f"{path} {e}"

def get_bars(symbol, limit=50):
    # Para stocks y ETF: /v2/stocks/{symbol}/bars, para forex: /v1beta1/forex/{symbol}/bars ? pero usamos stocks endpoint si es forex Alpaca usa EUR/USD igual
    try:
        # Intenta stocks
        if "/" in symbol: # forex
            # Alpaca forex bars: /v1beta1/forex/latest/bars?symbols=EUR/USD pero para historial usamos crypto/forex proxy: probamos data url
            path = f"/v1beta1/forex/{symbol}/bars?timeframe=15Min&limit={limit}"
            # alternativa: /v2/stocks no sirve para forex, usamos /v1beta1/forex
            data, err = alpaca_get(path, base=DATA_URL)
            if data and "bars" in data:
                bars = data["bars"].get(symbol) or data["bars"].get(symbol.replace("/","")) or []
                return [float(b["c"]) for b in bars]
        # stock/etf
        path = f"/v2/stocks/{symbol.replace('/','')}/bars?timeframe=15Min&limit={limit}"
        data, err = alpaca_get(path, base=DATA_URL)
        if data and "bars" in data:
            bars = data["bars"]
            if isinstance(bars, dict):
                bars = bars.get(symbol, []) or list(bars.values())[0] if bars else []
            if bars:
                return [float(b["c"]) for b in bars]
        # fallback: si no hay data, retorna None
        return None
    except Exception as e:
        log(f"Barras {symbol} {e}")
        return None

def calc_rsi(prices, period=14):
    if not prices or len(prices) < period+1:
        return 50.0
    gains, losses = 0,0
    for i in range(1, period+1):
        diff = prices[-i] - prices[-i-1]
        if diff>0: gains+=diff
        else: losses+=abs(diff)
    if losses==0: return 100.0
    rs = (gains/period)/(losses/period)
    rsi = 100 - (100/(1+rs))
    return round(rsi,2)

def tiene_posicion(symbol):
    for p in estado["posiciones"]:
        if p.get("symbol")==symbol or p.get("symbol")==symbol.replace("/",""):
            return True
    return False

def ejecutar_trading():
    # Solo si mercado abierto o forex (forex 24h)
    if len(estado["posiciones"]) >= MAX_POSITIONS:
        log(f"✅ Ya tienes {len(estado['posiciones'])}/{MAX_POSITIONS} posiciones, esperando venta")
        return
    for cfg in WATCHLIST:
        sym = cfg["symbol"]
        if tiene_posicion(sym):
            continue
        # RSI
        bars = get_bars(sym, 50)
        if not bars:
            # si no hay barras (forex en paper a veces no da), usa RSI simulado 45 para permitir compra manual
            rsi = 50.0
        else:
            rsi = calc_rsi(bars, RSI_PERIOD)
        estado["watch_rsi"][sym] = rsi
        
        # Logica de compra
        if rsi < RSI_BUY:
            # compramos
            qty = cfg["qty"]
            side = "buy"
            # Forex en Alpaca: notional o qty, usamos qty
            order_data = {
                "symbol": sym,
                "qty": str(qty),
                "side": side,
                "type": "market",
                "time_in_force": "gtc"
            }
            # Para forex Alpaca requiere forex, pero mismo endpoint /v2/orders funciona
            res, err = alpaca_post("/v2/orders", order_data)
            if err:
                log(f"❌ Orden {sym} {err}")
            else:
                log(f"🚀 COMPRA {sym} RSI {rsi} qty {qty} -> {res.get('id')}")
                send_tg(f"🚀 AURA compra {sym} RSI {rsi} qty {qty} Equity ${estado['equity']:,.2f}")
                estado["trades_hoy"]+=1
                time.sleep(2) # evita rate limit
                # solo 1 compra por ciclo
                break
    # Ventas por RSI alto
    for p in list(estado["posiciones"]):
        sym = p.get("symbol")
        bars = get_bars(sym, 50)
        rsi = calc_rsi(bars, RSI_PERIOD) if bars else 50.0
        estado["watch_rsi"][sym] = rsi
        try:
            unreal = float(p.get("unrealized_pl",0))
            qty = p.get("qty")
            if rsi > RSI_SELL or unreal > 100: # take profit simple
                order_data = {"symbol": sym, "qty": qty, "side":"sell", "type":"market", "time_in_force":"gtc"}
                res, err = alpaca_post("/v2/orders", order_data)
                if not err:
                    log(f"💰 VENTA {sym} RSI {rsi} P/L {unreal} -> cerrada")
                    send_tg(f"💰 AURA vende {sym} RSI {rsi} P/L ${unreal:.2f}")
        except Exception as e:
            log(f"Venta {sym} {e}")

def cargar_todo():
    acct, err = alpaca_get("/v2/account")
    if err:
        estado["conectado"]=False; estado["ultimo_error"]=err
        log(f"❌ Account {err}"); return False
    try:
        eq = float(acct.get("equity",0)); cash=float(acct.get("cash",0))
        bp=float(acct.get("buying_power",0)); last=float(acct.get("last_equity",eq))
        estado["equity"]=eq; estado["cash"]=cash; estado["bp"]=bp
        estado["day_pl"]=eq-last; estado["conectado"]=True; estado["ultimo_error"]=None
    except Exception as e:
        log(f"Parse {e}")
    pos, _ = alpaca_get("/v2/positions")
    if pos is not None:
        estado["posiciones"]=pos
    orders, _ = alpaca_get("/v2/orders?status=all&limit=20")
    if orders is not None:
        estado["ordenes"]=orders
    return True

def loop_principal():
    ciclo = 0
    while estado["running"]:
        try:
            cargar_todo()
            estado["ultimo_heartbeat"]=now_col().strftime("%H:%M:%S")
            ciclo+=1
            # cada 2 ciclos (60s) revisa trading
            if ciclo % 2 == 0:
                ejecutar_trading()
        except Exception as e:
            log(f"Loop {e}")
        time.sleep(30)

if API_KEY and API_SECRET:
    cargar_todo()
else:
    log("❌ Faltan keys")

def iniciar():
    if estado["running"]: return
    estado["running"]=True
    threading.Thread(target=loop_principal, daemon=True).start()
    log(f"✅ AURA {estado['version']} con 6 cupos iniciada: {[c['symbol'] for c in WATCHLIST]}")
    if TELE_TOKEN and TELE_CHAT:
        send_tg(f"✅ AURA V8.2 PRO 6 operaciones instalada.\nWatch: SPY (ETF) + EUR/USD + GBP/USD (Forex) + AAPL TSLA NVDA\nEquity ${estado['equity']:,.2f} Posiciones {len(estado['posiciones'])}/6")

HTML = """
<!doctype html>
<html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>AURA V8.2 PRO 6</title>
<style>
body{margin:0;background:#F5F7FB;color:#0F1A2B;font-family:Inter,sans-serif;padding:16px}
.top{display:flex;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:16px;align-items:center;background:#FFF;border:1px solid #E3E9F2;border-radius:16px;padding:12px 18px}
.brand{display:flex;align-items:center;gap:14px}
.brand img{height:52px;width:auto}
.pill{font-size:12px;padding:6px 12px;border-radius:20px;border:1px solid #E3E9F2;background:#FFF}
.ok{color:#0B7A7A;background:#E8F6F2} .bad{color:#B4235A;background:#FDF0F4}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}
.card{background:#FFF;border:1px solid #E3E9F2;border-radius:16px;padding:16px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
.big{font-size:26px;font-weight:800} .sub{font-size:12px;color:#5B6B85;margin-top:4px}
.table{width:100%;border-collapse:collapse;font-size:13px} .table th{text-align:left;color:#5B6B85;font-weight:600;padding:8px;border-bottom:1px solid #E3E9F2}
.table td{padding:8px;border-bottom:1px solid #F0F2F7}
.logbox{background:#0F1A2B;color:#C8D6E8;border-radius:12px;padding:12px;font-family:monospace;font-size:12px;max-height:320px;overflow:auto}
.rsi-low{color:#0B7A7A;font-weight:700} .rsi-high{color:#B4235A;font-weight:700} .rsi-mid{color:#5B6B85}
</style></head><body>
<div style="max-width:1240px;margin:0 auto">
<div class=top>
<div class=brand>
{% if logo_b64 %}<img src="data:image/png;base64,{{ logo_b64 }}" alt="AURA">{% endif %}
<div><b style="font-size:20px">AURA SYNAPSE</b><div class=sub>{{ version }} • 6 CUPOS • 1 ETF + 2 FOREX + 3 STOCK • PULSO {{ heartbeat }}</div></div>
</div>
<div style="display:flex;gap:8px;flex-wrap:wrap">
<span class="pill {{ 'ok' if conectado else 'bad' }}">● {{ 'Alpaca conectada' if conectado else 'Desconectada' }}</span>
<span class="pill ok">● ${{ "{:,.2f}".format(equity) }}</span>
<span class="pill">{{ posiciones|length }}/6 posiciones</span>
</div>
</div>

<div class=grid>
<div class=card><div class=sub>EQUITY TOTAL</div><div class=big>${{ "{:,.2f}".format(equity) }}</div><div class=sub style="color:{{ '#0B7A7A' if day_pl>=0 else '#B4235A' }}">{{ "%+.2f"|format(day_pl) }} hoy</div></div>
<div class=card><div class=sub>EFECTIVO</div><div class=big>${{ "{:,.2f}".format(cash) }}</div></div>
<div class=card><div class=sub>PODER DE COMPRA</div><div class=big>${{ "{:,.2f}".format(bp) }}</div></div>
<div class=card><div class=sub>ESTADO TRADING</div><div class=big style="font-size:16px">{{ heartbeat }}</div><div class=sub>{{ trades_hoy }} trades hoy · Max 6</div></div>
</div>

<h3 style="margin:18px 0 10px">🎯 Watchlist PRO 6 (Mi selección)</h3>
<div class=card>
<table class=table><tr><th>Symbol</th><th>Tipo</th><th>Estrategia</th><th>Qty</th><th>RSI Actual</th><th>Estado</th></tr>
{% for w in watchlist %}
<tr>
<td><b>{{ w.symbol }}</b><div class=sub>{{ w.name }}</div></td>
<td>{{ w.type }}</td>
<td>RSI&lt;{{ rsi_buy }} compra / &gt;{{ rsi_sell }} venta</td>
<td>{{ w.qty }}</td>
<td class="{{ 'rsi-low' if rsi_map.get(w.symbol,50) < 35 else 'rsi-high' if rsi_map.get(w.symbol,50) > 70 else 'rsi-mid' }}">{{ rsi_map.get(w.symbol, '--') }}</td>
<td>{% if w.symbol in pos_symbols or w.symbol.replace('/','') in pos_symbols %}✅ En posición{% else %}⏳ Esperando RSI&lt;35{% endif %}</td>
</tr>
{% endfor %}
</table>
<div class=sub style="margin-top:10px">1 ETF (SPY) + 2 FOREX puro (EUR/USD, GBP/USD) + 3 STOCK momentum (AAPL, TSLA, NVDA). El bot llena hasta 6 cupos automáticamente. Forex 24h, ETF/stocks en horario NY.</div>
</div>

<h3 style="margin:20px 0 10px">📦 Tus Posiciones Reales ({{ posiciones|length }}/6)</h3>
<div class=card>
{% if posiciones and posiciones|length>0 %}
<table class=table><tr><th>Symbol</th><th>Qty</th><th>Entrada</th><th>Actual</th><th>Valor</th><th>P/L</th></tr>
{% for p in posiciones %}<tr>
<td><b>{{ p.symbol }}</b></td><td>{{ p.qty }}</td><td>${{ p.avg_entry_price }}</td><td>${{ p.current_price }}</td>
<td>${{ p.market_value }}</td><td style="color:{{ '#0B7A7A' if (p.unrealized_pl|float)>=0 else '#B4235A' }}">${{ p.unrealized_pl }} ({{ p.unrealized_plpc }}%)</td>
</tr>{% endfor %}</table>
{% else %}<div class=sub>Sin posiciones - el bot abrirá hasta 6 cuando RSI < 35</div>{% endif %}
</div>

<h3 style="margin:20px 0 10px">Log Trading</h3>
<div class=logbox>{% for l in logs %}<div>{{ l }}</div>{% endfor %}</div>

</div><script>setTimeout(()=>location.reload(),20000)</script>
</body></html>
"""

@app.route("/")
def home():
    require_token()
    pos_symbols = [p.get("symbol") for p in estado["posiciones"]]
    return render_template_string(HTML, conectado=estado["conectado"], equity=estado["equity"],
                                  cash=estado["cash"], bp=estado["bp"], day_pl=estado["day_pl"],
                                  posiciones=estado["posiciones"], ordenes=estado["ordenes"],
                                  logs=list(reversed(list(estado["logs"])))[-40:],
                                  heartbeat=estado["ultimo_heartbeat"], version=estado["version"],
                                  logo_b64=LOGO_B64, watchlist=WATCHLIST, rsi_map=estado["watch_rsi"],
                                  pos_symbols=pos_symbols, rsi_buy=RSI_BUY, rsi_sell=RSI_SELL,
                                  trades_hoy=estado["trades_hoy"])

@app.route("/health")
def health(): return f"OK {estado['version']} {len(estado['posiciones'])}/6 Equity {estado['equity']}", 200

iniciar()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))
