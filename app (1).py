"""
AURA / SYNAPSE V6.3 - Bot RSI con Alpaca (paper por defecto)

Variables de entorno:
  APCA_API_KEY_ID, APCA_API_SECRET_KEY   (obligatorias)
  APCA_API_BASE_URL    (default: https://paper-api.alpaca.markets)
  DASHBOARD_TOKEN      (obligatoria: protege dashboard y /api/*)
  AUTO_START           ("true" por defecto; "false" para no iniciar el bot solo)
  TRADING_ENABLED      ("true" por defecto; "false" = solo escanea, no compra)

Logo: coloca el archivo en static/logo.png (Flask lo sirve solo).

Despliegue (Render) - IMPORTANTE usar UN solo worker:
  gunicorn app:app --workers 1 --threads 4 --bind 0.0.0.0:$PORT
"""
import os
import time
import threading
from collections import deque
from datetime import datetime, timedelta

import pytz
from flask import Flask, jsonify, render_template_string, request, abort
from alpaca_trade_api.rest import REST, TimeFrame, TimeFrameUnit
from ta.momentum import RSIIndicator

app = Flask(__name__)

# ===================== CONFIG =====================
API_KEY = os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("APCA_API_SECRET_KEY")
BASE_URL = os.getenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN")
TRADING_ENABLED = os.getenv("TRADING_ENABLED", "true").lower() == "true"
AUTO_START = os.getenv("AUTO_START", "true").lower() == "true"
IS_PAPER = "paper" in BASE_URL

SYMBOLS = ["AAPL", "TSLA", "NVDA", "MSFT", "SPY"]
RSI_BUY = 40
RSI_WINDOW = 14
SL_PERCENT = -2.0
TP_PERCENT = 3.0
EQUITY_POR_TRADE = 0.05      # 5% del equity (no del buying power)
MAX_POSICIONES = 5
SCAN_INTERVAL = 60           # segundos
MIN_MIN_PARA_CIERRE = 15     # no abrir posiciones en los últimos 15 min
COOLDOWN_MIN = 60            # no recomprar el mismo símbolo en 60 min
DATA_FEED = "iex"            # "iex" (gratis) o "sip" (requiere plan pago)

TZ_COL = pytz.timezone("America/Bogota")

if not API_KEY or not API_SECRET:
    raise RuntimeError("Faltan APCA_API_KEY_ID / APCA_API_SECRET_KEY")

api = REST(API_KEY, API_SECRET, BASE_URL, api_version="v2")

# ===================== ESTADO =====================
bot_status = {
    "running": False,
    "last_scan": None,
    "last_heartbeat": None,
    "last_error": None,
}
logs = deque(maxlen=50)
ultima_compra = {}           # symbol -> datetime UTC
ultimo_rsi = {}              # symbol -> último RSI calculado (para el dashboard)
_start_lock = threading.Lock()


def ahora_col():
    return datetime.now(TZ_COL).strftime("%H:%M:%S")


def log(msg):
    entry = f"[{ahora_col()}] {msg}"
    logs.append(entry)
    print(entry, flush=True)


# ===================== SEGURIDAD =====================
def require_token():
    """Token por header X-Token o query ?token=. Sin DASHBOARD_TOKEN, todo se bloquea."""
    if not DASHBOARD_TOKEN:
        abort(503, "DASHBOARD_TOKEN no configurado")
    token = request.headers.get("X-Token") or request.args.get("token")
    if token != DASHBOARD_TOKEN:
        abort(401)


# ===================== MERCADO =====================
def mercado_info():
    """Devuelve (abierto, minutos_para_cierre) usando el reloj real de Alpaca
    (cubre feriados y cambios de horario). Lanza excepción si falla la API."""
    clock = api.get_clock()
    if not clock.is_open:
        return False, 0
    mins = (clock.next_close - clock.timestamp).total_seconds() / 60
    return True, mins


def calcular_rsi(symbol):
    """RSI sobre la última vela CERRADA de 5 min, con datos de Alpaca."""
    try:
        start = (datetime.now(pytz.utc) - timedelta(days=4)).isoformat()
        bars = api.get_bars(
            symbol,
            TimeFrame(5, TimeFrameUnit.Minute),
            start=start,
            limit=1000,
            feed=DATA_FEED,
        ).df
        if bars is None or len(bars) < RSI_WINDOW + 3:
            return None
        rsi = RSIIndicator(close=bars["close"], window=RSI_WINDOW).rsi()
        valor = rsi.iloc[-2]  # -1 es la vela en curso (incompleta)
        if valor != valor:    # NaN
            return None
        return round(float(valor), 2)
    except Exception as e:
        log(f"Error RSI {symbol}: {e}")
        return None


def simbolos_ocupados():
    """Símbolos con posición abierta u orden pendiente.
    Devuelve None si la API falla (para NO asumir que no hay nada)."""
    try:
        pos = {p.symbol for p in api.list_positions()}
        ordenes = {o.symbol for o in api.list_orders(status="open", limit=100)}
        return pos | ordenes
    except Exception as e:
        log(f"Error consultando posiciones/órdenes: {e}")
        return None


def ejecutar_compra(symbol, rsi):
    try:
        account = api.get_account()
        equity = float(account.equity)
        disponible = float(account.cash)
        precio = float(api.get_latest_trade(symbol, feed=DATA_FEED).price)

        monto = min(equity * EQUITY_POR_TRADE, disponible)
        qty = int(monto / precio)
        if qty < 1:
            log(f"{symbol}: qty insuficiente (monto ${monto:.2f}, precio ${precio:.2f})")
            return False

        api.submit_order(
            symbol=symbol,
            qty=qty,
            side="buy",
            type="market",
            time_in_force="gtc",  # con "day", el TP/SL expira al cierre y la posición queda sin protección
            order_class="bracket",
            take_profit={"limit_price": round(precio * (1 + TP_PERCENT / 100), 2)},
            stop_loss={"stop_price": round(precio * (1 + SL_PERCENT / 100), 2)},
        )
        ultima_compra[symbol] = datetime.now(pytz.utc)
        log(f"COMPRA {symbol}: {qty} @ ~${precio:.2f} | RSI {rsi} | "
            f"TP +{TP_PERCENT}% SL {SL_PERCENT}%")
        return True
    except Exception as e:
        log(f"Error compra {symbol}: {e}")
        return False


def en_cooldown(symbol):
    t = ultima_compra.get(symbol)
    if not t:
        return False
    return (datetime.now(pytz.utc) - t) < timedelta(minutes=COOLDOWN_MIN)


def escanear_mercado():
    try:
        abierto, mins_cierre = mercado_info()
    except Exception as e:
        bot_status["last_error"] = "No se pudo consultar el reloj de Alpaca"
        log(f"Error reloj: {e}")
        return

    if not abierto:
        bot_status["last_scan"] = "Mercado cerrado"
        return

    ocupados = simbolos_ocupados()
    if ocupados is None:
        return  # ante la duda, no operar

    n_pos = len(ocupados)
    log(f"ESCANEO RSI - Ocupados: {n_pos}/{MAX_POSICIONES}")

    for symbol in SYMBOLS:
        if symbol in ocupados or en_cooldown(symbol):
            continue

        rsi = calcular_rsi(symbol)
        if rsi is None:
            continue
        ultimo_rsi[symbol] = rsi
        log(f"{symbol} RSI: {rsi}")

        if rsi > RSI_BUY:
            continue

        if n_pos >= MAX_POSICIONES:
            log(f"{symbol}: señal ignorada, máximo de posiciones alcanzado")
            continue
        if mins_cierre < MIN_MIN_PARA_CIERRE:
            log(f"{symbol}: señal ignorada, cerca del cierre")
            continue
        if not TRADING_ENABLED:
            log(f"{symbol}: señal ignorada (TRADING_ENABLED=false)")
            continue

        log(f"SEÑAL COMPRA {symbol} - RSI {rsi} <= {RSI_BUY}")
        if ejecutar_compra(symbol, rsi):
            n_pos += 1
            ocupados.add(symbol)

    bot_status["last_scan"] = ahora_col()
    bot_status["last_error"] = None


def loop_principal():
    log(f"BOT INICIADO ({'PAPER' if IS_PAPER else 'LIVE'}) | "
        f"Activos: {', '.join(SYMBOLS)} | Trading: {TRADING_ENABLED}")
    while bot_status["running"]:
        bot_status["last_heartbeat"] = ahora_col()
        try:
            escanear_mercado()
        except Exception as e:
            bot_status["last_error"] = "Error en el loop principal"
            log(f"Error en loop: {e}")
        time.sleep(SCAN_INTERVAL)


def iniciar_bot():
    """Arranca el hilo una sola vez por proceso."""
    with _start_lock:
        if bot_status["running"]:
            return False
        bot_status["running"] = True
        threading.Thread(target=loop_principal, daemon=True).start()
        return True


# ===================== WEB =====================

def historial_equity():
    """Equity diario del último mes para el gráfico (lista vacía si falla)."""
    try:
        h = api.get_portfolio_history(period="1M", timeframe="1D")
        return [float(v) for v in h.equity if v is not None]
    except Exception as e:
        log(f"Error historial equity: {e}")
        return []


def svg_linea(vals, w=720, h=200):
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    px = lambda i: 12 + i * (w - 24) / (len(vals) - 1)
    py = lambda v: 14 + (hi - v) / rng * (h - 28)
    pts = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(vals))
    color = "#0E8C8C" if vals[-1] >= vals[0] else "#6A2BA8"
    return (
        f'<svg viewBox="0 0 {w} {h}" style="width:100%;height:auto;display:block" role="img" '
        f'aria-label="Evolución del equity en el último mes">'
        f'<defs><linearGradient id="g" x1="0" x2="0" y1="0" y2="1">'
        f'<stop offset="0" stop-color="{color}" stop-opacity=".22"/>'
        f'<stop offset="1" stop-color="{color}" stop-opacity="0"/></linearGradient></defs>'
        f'<polygon points="12,{h-14} {pts} {w-12},{h-14}" fill="url(#g)"/>'
        f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.5" '
        f'stroke-linejoin="round" stroke-linecap="round"/></svg>'
    )


DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><title>AURA · Panel</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="icon" href="/static/logo.png">
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@600;700;800&family=DM+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{--ink:#0E1B33;--muted:#5B6B85;--blue:#1F4FA3;--teal:#0B7A7A;--purple:#6A2BA8;--line:#E3E9F2;--bg:#F6F8FB}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:'DM Sans',Arial,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:20px 24px 40px}
header{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:16px;margin-bottom:24px}
header img{height:84px;width:auto}
.pills{display:flex;flex-wrap:wrap;gap:8px}
.pill{padding:7px 14px;border-radius:999px;font-size:13px;font-weight:600;background:#fff;border:1px solid var(--line)}
.ok{color:var(--teal)}.bad{color:#B3263E}.warn{color:#9A6200}
.paper{background:#E4ECF9;color:var(--blue);border-color:#E4ECF9}.live{background:#B3263E;color:#fff;border-color:#B3263E}
.grid{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));margin-bottom:16px}
.card{background:#fff;border:1px solid var(--line);border-radius:18px;padding:22px;box-shadow:0 8px 28px rgba(31,79,163,.06)}
.card h3{margin:0 0 10px;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:600}
.big{font-family:Montserrat,sans-serif;font-weight:800;font-size:30px;margin:0}
.sub{font-size:13px;color:var(--muted);margin-top:6px}
h2{font-family:Montserrat,sans-serif;font-size:20px;margin:0 0 14px}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;color:var(--muted);font-weight:600;font-size:12px;letter-spacing:.06em;text-transform:uppercase;padding:8px 10px;border-bottom:1px solid var(--line)}
td{padding:12px 10px;border-bottom:1px solid var(--line)}
.scroll{overflow-x:auto}
.rsi{margin-top:12px;position:relative;height:10px;border-radius:99px;background:linear-gradient(90deg,#CDEBEB 0 40%,#ECEFF5 40% 60%,#E5D9F2 60% 100%)}
.dot{position:absolute;top:-4px;width:18px;height:18px;border-radius:50%;background:var(--blue);border:3px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,.25);transform:translateX(-50%)}
.logs{background:var(--ink);color:#C9D4E6;border-radius:18px;padding:20px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;line-height:1.7}
footer{margin-top:24px;font-size:12px;color:var(--muted);line-height:1.6}
</style></head><body><div class="wrap">

<header>
  <img src="/static/logo.png" alt="AURA Stock Market Trading" onerror="this.style.display='none'">
  <div class="pills">
    <span class="pill {{ 'paper' if paper else 'live' }}">{{ 'MODO PAPER' if paper else 'CUENTA REAL' }}</span>
    <span class="pill {{ 'ok' if connected else 'bad' }}">● Alpaca {{ 'conectado' if connected else 'sin conexión' }}</span>
    <span class="pill {{ 'ok' if running else 'bad' }}">● Bot {{ 'corriendo' if running else 'detenido' }}</span>
    <span class="pill {{ 'ok' if trading else 'warn' }}">● {{ 'Compras activas' if trading else 'Solo escaneo' }}</span>
  </div>
</header>

<div class="grid">
  <div class="card"><h3>Equity total</h3><p class="big">${{ "{:,.2f}".format(equity) }}</p>
    <div class="sub {{ 'ok' if day_pl >= 0 else 'bad' }}">{{ "%+.2f"|format(day_pl) }} hoy</div></div>
  <div class="card"><h3>Efectivo</h3><p class="big">${{ "{:,.2f}".format(cash) }}</p></div>
  <div class="card"><h3>Poder de compra</h3><p class="big">${{ "{:,.2f}".format(bp) }}</p></div>
  <div class="card"><h3>Posiciones</h3><p class="big">{{ positions|length }} / {{ max_pos }}</p>
    <div class="sub">Escaneo: {{ last_scan or '—' }} · Pulso: {{ heartbeat or '—' }}</div></div>
</div>
{% if last_error %}<div class="card" style="margin-bottom:16px"><span class="bad">⚠ {{ last_error }}</span></div>{% endif %}

<div class="card" style="margin-bottom:16px">
  <h2>Evolución del equity · último mes</h2>
  {% if chart %}{{ chart|safe }}{% else %}<p class="sub">Aún no hay historial suficiente.</p>{% endif %}
</div>

<div class="card" style="margin-bottom:16px">
  <h2>Posiciones abiertas</h2>
  {% if positions %}
  <div class="scroll"><table>
    <tr><th>Símbolo</th><th>Lado</th><th>Cantidad</th><th>Entrada</th><th>Actual</th><th>P/L</th></tr>
    {% for p in positions %}
    <tr><td><strong>{{ p.symbol }}</strong></td><td>{{ p.side }}</td><td>{{ p.qty }}</td>
      <td>${{ "%.2f"|format(p.entry) }}</td><td>${{ "%.2f"|format(p.price) }}</td>
      <td class="{{ 'ok' if p.pl >= 0 else 'bad' }}">{{ "%+.2f"|format(p.pl) }} ({{ "%+.2f"|format(p.plpc) }}%)</td></tr>
    {% endfor %}
  </table></div>
  {% else %}<p class="sub">Sin posiciones abiertas.</p>{% endif %}
</div>

<h2>Activos vigilados · RSI (5 min)</h2>
<div class="grid">
  {% for s in symbols %}
  <div class="card">
    <h3>{{ s }}</h3>
    {% if s in held %}<p class="big" style="font-size:20px">En posición</p>
    {% elif rsi.get(s) is not none %}
      <p class="big">{{ rsi[s] }}</p>
      <div class="rsi"><span class="dot" style="left:{{ rsi[s] }}%"></span></div>
      <div class="sub">≤ {{ rsi_buy }} zona de compra</div>
    {% else %}<p class="sub">Esperando datos…</p>{% endif %}
  </div>
  {% endfor %}
</div>

<h2 style="margin-top:8px">Registro</h2>
<div class="logs">{% for l in logs %}<div>{{ l }}</div>{% else %}<div>Sin eventos todavía.</div>{% endfor %}</div>

<footer>Take profit +{{ tp }}% · Stop loss {{ sl }}% · {{ eq_pct }}% del equity por operación. Esto no es asesoría financiera; operar implica riesgo de pérdida.</footer>
</div>
<script>setTimeout(()=>location.reload(), 30000)</script>
</body></html>
"""


@app.route("/")
def dashboard():
    require_token()
    connected = True
    pos_list = []
    try:
        account = api.get_account()
        equity = float(account.equity)
        cash = float(account.cash)
        bp = float(account.buying_power)
        day_pl = equity - float(account.last_equity)
        for p in api.list_positions():
            pos_list.append({
                "symbol": p.symbol, "side": "Corto" if p.side == "short" else "Largo",
                "qty": p.qty, "entry": float(p.avg_entry_price),
                "price": float(p.current_price), "pl": float(p.unrealized_pl),
                "plpc": float(p.unrealized_plpc) * 100,
            })
    except Exception as e:
        log(f"Error dashboard: {e}")
        connected = False
        equity = cash = bp = day_pl = 0.0

    return render_template_string(
        DASHBOARD_HTML,
        connected=connected, paper=IS_PAPER,
        running=bot_status["running"], trading=TRADING_ENABLED,
        last_scan=bot_status["last_scan"], heartbeat=bot_status["last_heartbeat"],
        last_error=bot_status["last_error"],
        equity=equity, cash=cash, bp=bp, day_pl=day_pl,
        positions=pos_list, held={p["symbol"] for p in pos_list},
        max_pos=MAX_POSICIONES, symbols=SYMBOLS, rsi=dict(ultimo_rsi),
        rsi_buy=RSI_BUY, tp=TP_PERCENT, sl=SL_PERCENT,
        eq_pct=round(EQUITY_POR_TRADE * 100),
        chart=svg_linea(historial_equity()) if connected else "",
        logs=list(logs)[-12:][::-1],
    )


@app.route("/api/debug")
def debug():
    require_token()
    try:
        account = api.get_account()
        return jsonify({
            "mode": "PAPER" if IS_PAPER else "LIVE",
            "equity": float(account.equity),
            "buying_power": float(account.buying_power),
            "last_heartbeat": bot_status["last_heartbeat"],
            "last_scan": bot_status["last_scan"],
            "symbols": SYMBOLS,
            "running": bot_status["running"],
            "trading_enabled": TRADING_ENABLED,
        })
    except Exception as e:
        log(f"Error debug: {e}")
        return jsonify({"error": "No se pudo consultar Alpaca"}), 502


@app.route("/api/start", methods=["POST"])
def start():
    require_token()
    return jsonify({"status": "started" if iniciar_bot() else "already_running"})


@app.route("/health")
def health():
    # Sin datos sensibles: útil para el health check de Render
    return jsonify({"ok": True, "running": bot_status["running"]})


# Arranque compatible con gunicorn (el bloque __main__ no corre con gunicorn).
# Con --workers 1 solo se crea un bot.
if AUTO_START:
    iniciar_bot()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
