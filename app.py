"""
AURA / SYNAPSE V6.3 - Bot RSI con Alpaca (paper por defecto)

Variables de entorno:
  APCA_API_KEY_ID, APCA_API_SECRET_KEY   (obligatorias)
  APCA_API_BASE_URL    (default: https://paper-api.alpaca.markets)
  DASHBOARD_TOKEN      (obligatoria: protege dashboard y /api/*)
  AUTO_START           ("true" por defecto; "false" para no iniciar el bot solo)
  TRADING_ENABLED      ("true" por defecto; "false" = solo escanea, no compra)

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
DASHBOARD_HTML = """
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>AURA V6.3</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body{background:#0a0a0a;color:#fff;font-family:Arial;padding:20px}
  .card{background:#1a1a1a;padding:20px;margin:10px 0;border-radius:10px}
  .green{color:#00ff88}.red{color:#ff4466}.blue{color:#00aaff}.yellow{color:#ffaa00}
  .badge{background:#00ff88;color:#000;padding:5px 10px;border-radius:5px;font-size:12px}
  .badge.live{background:#ff4466;color:#fff}
</style></head><body>
<h1>⚡ SYNAPSE V6.3
  <span class="badge {{ '' if paper else 'live' }}">{{ 'PAPER' if paper else 'LIVE' }}</span>
</h1>
<div class="card">
  <h2>ALPACA
    {% if connected %}<span class="green">● CONECTADO</span>
    {% else %}<span class="red">● SIN CONEXIÓN</span>{% endif %}
  </h2>
  <p>BOT
    {% if running %}<span class="green">● CORRIENDO</span>
    {% else %}<span class="red">● DETENIDO</span>{% endif %}
    &nbsp;|&nbsp; COMPRAS
    {% if trading %}<span class="green">● ACTIVAS</span>
    {% else %}<span class="yellow">● SOLO ESCANEO</span>{% endif %}
  </p>
  <p style="font-size:12px">Último escaneo: {{ last_scan or '-' }} · Heartbeat: {{ heartbeat or '-' }}</p>
  {% if last_error %}<p class="red" style="font-size:12px">⚠ {{ last_error }}</p>{% endif %}
</div>
<div class="card"><h3>EQUITY TOTAL</h3><h2 class="blue">${{ "%.2f"|format(equity) }}</h2></div>
<div class="card">
  <h3>CASH / BUYING POWER</h3>
  <h2>${{ "%.2f"|format(cash) }}</h2>
  <p class="green">BP: ${{ "%.2f"|format(bp) }}</p>
</div>
<div class="card">
  <h3>POSICIONES</h3>
  <h2 class="yellow">{{ positions|length }} / {{ max_pos }}</h2>
  {% for p in positions %}
    <p style="font-size:13px">{{ p.symbol }} · {{ p.qty }} · P/L ${{ "%.2f"|format(p.unrealized_pl|float) }}</p>
  {% endfor %}
  <p>RSI compra ≤ {{ rsi_buy }} · TP +{{ tp }}% · SL {{ sl }}%</p>
</div>
<div class="card"><h3>ACTIVOS</h3><p>{{ symbols|join(' • ') }}</p></div>
<div class="card">
  <h3>LOGS</h3>
  {% for l in logs %}<p style="font-size:12px">{{ l }}</p>{% endfor %}
</div>
<script>setTimeout(()=>location.reload(), 30000)</script>
</body></html>
"""


@app.route("/")
def dashboard():
    require_token()
    connected = True
    try:
        account = api.get_account()
        positions = api.list_positions()
        equity = float(account.equity)
        cash = float(account.cash)
        bp = float(account.buying_power)
    except Exception as e:
        log(f"Error dashboard: {e}")
        connected = False
        equity = cash = bp = 0.0
        positions = []

    return render_template_string(
        DASHBOARD_HTML,
        connected=connected, paper=IS_PAPER,
        running=bot_status["running"], trading=TRADING_ENABLED,
        last_scan=bot_status["last_scan"], heartbeat=bot_status["last_heartbeat"],
        last_error=bot_status["last_error"],
        equity=equity, cash=cash, bp=bp, positions=positions,
        max_pos=MAX_POSICIONES, symbols=SYMBOLS,
        rsi_buy=RSI_BUY, tp=TP_PERCENT, sl=SL_PERCENT,
        logs=list(logs)[-10:][::-1],
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
