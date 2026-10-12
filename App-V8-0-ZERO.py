import os, threading, time, requests, pytz
from collections import deque
from datetime import datetime, timedelta
from flask import Flask, jsonify, render_template_string, request, abort

app = Flask(__name__)

# ========= CONFIG =========
def env(a,b,d=""): return os.environ.get(a,os.environ.get(b,d)).strip()
API_KEY = env("ALPACA_API_KEY","APCA_API_KEY_ID")
API_SECRET = env("ALPACA_SECRET_KEY","APCA_API_SECRET_KEY")
BASE_URL = env("ALPACA_BASE_URL","APCA_API_BASE_URL","https://paper-api.alpaca.markets")
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN","").strip()
TELE_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN","").strip()
TELE_CHAT = os.environ.get("TELEGRAM_CHAT_ID","").strip()

TZ_COL = pytz.timezone("America/Bogota")
SYMBOLS_WATCH = ["AAPL","TSLA","NVDA","MSFT","SPY","QQQ"]

estado = {
    "version": "V8.0 ZERO",
    "equity": 0.0, "cash":0.0, "bp":0.0, "day_pl":0.0,
    "conectado": False, "running": False,
    "posiciones": [], "ordenes": [],
    "ultimo_error": None,
    "ultimo_heartbeat": "Nunca",
    "detalles": {},
    "logs": deque(maxlen=100)
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
                         params={"chat_id":TELE_CHAT,"text":msg}, timeout=10)
        return r.status_code==200
    except Exception as e:
        log(f"TG error {e}"); return False

def alpaca_get(path):
    """REST puro - no SDK, no se cuelga nunca"""
    try:
        h = {"APCA-API-KEY-ID":API_KEY,"APCA-API-SECRET-KEY":API_SECRET}
        r = requests.get(f"{BASE_URL}{path}", headers=h, timeout=15)
        if r.status_code==200:
            return r.json(), None
        return None, f"{path} {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return None, f"{path} {type(e).__name__}: {e}"

def cargar_todo():
    """Carga equity + posiciones + ordenes por REST"""
    acct, err = alpaca_get("/v2/account")
    if err:
        estado["conectado"]=False; estado["ultimo_error"]=err
        log(f"❌ Account {err}"); return False
    try:
        eq = float(acct.get("equity",0)); cash=float(acct.get("cash",0))
        bp=float(acct.get("buying_power",0)); last=float(acct.get("last_equity",eq))
        estado["equity"]=eq; estado["cash"]=cash; estado["bp"]=bp
        estado["day_pl"]=eq-last; estado["conectado"]=True; estado["ultimo_error"]=None
        log(f"💰 Equity ${eq:,.2f} Cash ${cash:,.2f} Day {eq-last:+.2f}")
    except Exception as e:
        log(f"Parse account {e}")

    pos, err = alpaca_get("/v2/positions")
    if pos is not None:
        estado["posiciones"]=pos
        log(f"📦 {len(pos)} posiciones cargadas")
        for p in pos[:5]: log(f"  {p.get('symbol')} {p.get('qty')} ${p.get('market_value')}")
    else:
        log(f"Posiciones {err}"); estado["posiciones"]=[]

    orders, err = alpaca_get("/v2/orders?status=all&limit=20")
    if orders is not None:
        estado["ordenes"]=orders
    return True

def loop_principal():
    while estado["running"]:
        try:
            cargar_todo()
            estado["ultimo_heartbeat"]=now_col().strftime("%H:%M:%S")
        except Exception as e:
            log(f"Loop {e}")
        time.sleep(30)  # actualiza cada 30 seg SIEMPRE

# Carga instantánea al arrancar
if API_KEY and API_SECRET:
    cargar_todo()
else:
    log("❌ Faltan ALPACA_API_KEY y ALPACA_SECRET_KEY en Environment")

def iniciar():
    if estado["running"]: return
    estado["running"]=True
    threading.Thread(target=loop_principal, daemon=True).start()
    log("✅ AURA V8.0 ZERO iniciada - 100% REST, no SDK")
    if TELE_TOKEN and TELE_CHAT:
        send_tg(f"✅ AURA V8.0 ZERO limpia instalada. Equity ${estado['equity']:,.2f}. Posiciones: {len(estado['posiciones'])}. Bot corriendo cada 30s.")

HTML = """
<!doctype html>
<html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>AURA V8.0 ZERO</title>
<style>
body{margin:0;background:#F5F7FB;color:#0F1A2B;font-family:Inter,system-ui,sans-serif;padding:16px}
.top{display:flex;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-bottom:16px;align-items:center}
.pill{font-size:12px;padding:6px 12px;border-radius:20px;border:1px solid #E3E9F2;background:#FFF}
.ok{color:#0B7A7A;background:#E8F6F2;border-color:#B8E6D8} .bad{color:#B4235A;background:#FDF0F4}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
.card{background:#FFF;border:1px solid #E3E9F2;border-radius:16px;padding:16px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
.big{font-size:28px;font-weight:800} .sub{font-size:12px;color:#5B6B85;margin-top:4px}
.table{width:100%;border-collapse:collapse;font-size:13px} .table th{text-align:left;color:#5B6B85;font-weight:600;padding:8px;border-bottom:1px solid #E3E9F2}
.table td{padding:8px;border-bottom:1px solid #F0F2F7}
.logbox{background:#0F1A2B;color:#C8D6E8;border-radius:12px;padding:12px;font-family:monospace;font-size:12px;max-height:320px;overflow:auto}
.btn{padding:8px 14px;border-radius:10px;border:1px solid #E3E9F2;background:#FFF;cursor:pointer}
</style></head><body>
<div style="max-width:1200px;margin:0 auto">
<div class=top>
<div><b style="font-size:20px">AURA SYNAPSE</b><div class=sub>V8.0 ZERO • PAPER • 100% REST • PULSO {{ heartbeat }}</div></div>
<div style="display:flex;gap:8px;flex-wrap:wrap">
<span class="pill {{ 'ok' if conectado else 'bad' }}">● {{ 'Alpaca conectada' if conectado else 'Desconectada' }}</span>
<span class="pill ok">● ${{ "{:,.2f}".format(equity) }}</span>
<span class="pill">{{ version }}</span>
</div></div>

{% if error %}<div class=card style="background:#FDF0F4;border-color:#F5B0C8;color:#B4235A">{{ error }}</div>{% endif %}

<div class=grid>
<div class=card><div class=sub>EQUITY TOTAL</div><div class=big>${{ "{:,.2f}".format(equity) }}</div><div class=sub style="color:{{ '#0B7A7A' if day_pl>=0 else '#B4235A' }}">{{ "%+.2f"|format(day_pl) }} hoy · Se actualiza cada 30s</div></div>
<div class=card><div class=sub>EFECTIVO</div><div class=big>${{ "{:,.2f}".format(cash) }}</div></div>
<div class=card><div class=sub>PODER DE COMPRA</div><div class=big>${{ "{:,.2f}".format(bp) }}</div></div>
<div class=card><div class=sub>ESTADO</div><div class=big style="font-size:18px">{{ heartbeat }}</div><div class=sub>{{ posiciones|length }} posiciones · {{ ordenes|length }} ordenes recientes</div></div>
</div>

<h3 style="margin:20px 0 10px">📦 Tus Posiciones Reales (Alpaca)</h3>
<div class=card>
{% if posiciones and posiciones|length>0 %}
<table class=table><tr><th>Symbol</th><th>Qty</th><th>Entrada</th><th>Actual</th><th>Valor</th><th>P/L</th></tr>
{% for p in posiciones %}<tr>
<td><b>{{ p.symbol }}</b></td><td>{{ p.qty }}</td><td>${{ p.avg_entry_price }}</td><td>${{ p.current_price }}</td>
<td>${{ p.market_value }}</td><td style="color:{{ '#0B7A7A' if (p.unrealized_pl|float)>=0 else '#B4235A' }}">${{ p.unrealized_pl }} ({{ p.unrealized_plpc }}%)</td>
</tr>{% endfor %}</table>
{% else %}
<div class=sub>Sin posiciones abiertas ahora. Cuando el bot compre, aquí aparecen AAPL, TSLA, etc. (esto ya carga directo de Alpaca por REST, no se congela)</div>
{% endif %}
</div>

<h3 style="margin:20px 0 10px">📋 Ordenes Recientes</h3>
<div class=card>
{% if ordenes and ordenes|length>0 %}
<table class=table><tr><th>Fecha</th><th>Symbol</th><th>Lado</th><th>Qty</th><th>Estado</th></tr>
{% for o in ordenes[:10] %}<tr><td>{{ o.created_at[:16] }}</td><td>{{ o.symbol }}</td><td>{{ o.side }}</td><td>{{ o.qty }}</td><td>{{ o.status }}</td></tr>{% endfor %}</table>
{% else %}<div class=sub>Sin ordenes recientes</div>{% endif %}
</div>

<h3 style="margin:20px 0 10px">Log en vivo</h3>
<div class=logbox>{% for l in logs %}<div>{{ l }}</div>{% endfor %}</div>
<div style="margin-top:12px"><button class=btn onclick="location.reload()">🔄 Refrescar</button> <span class=sub>Auto-refresh cada 15s</span></div>

</div><script>setTimeout(()=>location.reload(),15000)</script>
</body></html>
"""

@app.route("/")
def home():
    require_token()
    return render_template_string(HTML, conectado=estado["conectado"], equity=estado["equity"],
                                  cash=estado["cash"], bp=estado["bp"], day_pl=estado["day_pl"],
                                  posiciones=estado["posiciones"], ordenes=estado["ordenes"],
                                  logs=list(reversed(list(estado["logs"])))[-25:],
                                  heartbeat=estado["ultimo_heartbeat"], error=estado["ultimo_error"],
                                  version=estado["version"])

@app.route("/api/debug")
def debug():
    require_token()
    acct,_ = alpaca_get("/v2/account")
    return jsonify({
        "version": estado["version"],
        "conectado": estado["conectado"],
        "equity": estado["equity"],
        "posiciones_count": len(estado["posiciones"]),
        "posiciones": estado["posiciones"][:5],
        "error": estado["ultimo_error"],
        "heartbeat": estado["ultimo_heartbeat"],
        "has_keys": bool(API_KEY and API_SECRET),
        "alpaca_raw": acct
    })

@app.route("/health")
def health(): return f"OK {estado['version']} Equity {estado['equity']}", 200

iniciar()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))
