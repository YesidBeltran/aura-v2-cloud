"""
AURA / SYNAPSE V6.3 - Bot RSI con Alpaca (paper por defecto)

Variables de entorno:
  APCA_API_KEY_ID, APCA_API_SECRET_KEY   (obligatorias)
  APCA_API_BASE_URL    (default: https://paper-api.alpaca.markets)
  DASHBOARD_TOKEN      (obligatoria: protege dashboard y /api/*)
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID   (opcionales: avisos al celular)
  AUTO_START           ("true" por defecto; "false" para no iniciar el bot solo)
  AUTO_PROTEGER        ("true" por defecto: protege posiciones sin stop con una orden OCO)
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
import requests
from flask import Flask, jsonify, render_template_string, request, abort
from alpaca_trade_api.rest import REST, TimeFrame, TimeFrameUnit
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange

app = Flask(__name__)

# ===================== CONFIG =====================
API_KEY = os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("APCA_API_SECRET_KEY")
BASE_URL = os.getenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TRADING_ENABLED = os.getenv("TRADING_ENABLED", "true").lower() == "true"
AUTO_START = os.getenv("AUTO_START", "true").lower() == "true"
AUTO_PROTEGER = os.getenv("AUTO_PROTEGER", "true").lower() == "true"
IS_PAPER = "paper" in BASE_URL

SYMBOLS = ["AAPL", "TSLA", "NVDA", "MSFT", "SPY"]
RSI_BUY = 40
RSI_WINDOW = 14
TREND_BARS = 780             # media móvil de ~10 días en velas de 5 min (filtro de tendencia)
EQUITY_POR_TRADE = 0.05      # tope: 5% del equity por operación
RIESGO_POR_TRADE = 0.001     # arriesgar como máximo 0.1% del equity si salta el stop
ATR_MULT = 1.0               # stop = 1 x volatilidad diaria (ATR 14 días)...
SL_MIN, SL_MAX = 1.5, 4.0    # ...limitado entre 1.5% y 4%
RR = 1.5                     # take profit = stop x 1.5
SL_PERCENT = -2.0            # valores por defecto (posiciones sin protección)
TP_PERCENT = 3.0
MAX_POSICIONES = 5
MAX_COMPRAS_POR_ESCANEO = 2  # evita entrar en todo a la vez si el mercado entero cae
MAX_PERDIDA_DIA = 3.0        # % de pérdida del día que pausa las compras
MIN_SIN_OPERAR_APERTURA = 15 # no abrir posiciones en los primeros 15 min
MIN_MIN_PARA_CIERRE = 15     # ni en los últimos 15 min
SCAN_INTERVAL = 60
COOLDOWN_MIN = 60
DATA_FEED = "iex"            # "iex" (gratis) o "sip" (requiere plan pago)

TZ_COL = pytz.timezone("America/Bogota")
NY = pytz.timezone("America/New_York")

if not API_KEY or not API_SECRET:
    raise RuntimeError("Faltan APCA_API_KEY_ID / APCA_API_SECRET_KEY")

api = REST(API_KEY, API_SECRET, BASE_URL, api_version="v2")

# ===================== ESTADO =====================
bot_status = {
    "running": False, "last_scan": None, "last_heartbeat": None,
    "last_error": None, "pausa_riesgo": False, "pausa_fecha": None,
}
logs = deque(maxlen=50)
ultima_compra = {}           # symbol -> datetime UTC
ultimo_rsi = {}              # symbol -> último RSI (dashboard)
conocidas = {}               # symbol -> {"entry", "side"} para detectar cierres
protegidas = set()           # symbols a los que el bot ya les puso protección
avisadas = set()             # symbols ya avisados como "sin protección"
fallos = {"n": 0, "avisado": False}
_start_lock = threading.Lock()


def ahora_col():
    return datetime.now(TZ_COL).strftime("%H:%M:%S")


def log(msg):
    entry = f"[{ahora_col()}] {msg}"
    logs.append(entry)
    print(entry, flush=True)


def telegram(msg):
    """Envía un aviso por Telegram sin bloquear el bot. No registra el error
    completo porque la URL contiene el token."""
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        return

    def _send():
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=8)
        except Exception as e:
            print(f"Error Telegram: {type(e).__name__}", flush=True)

    threading.Thread(target=_send, daemon=True).start()


def alerta(msg):
    log(msg)
    telegram(msg)


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
    """(abierto, min_para_cierre, min_desde_apertura). Usa el reloj real de Alpaca."""
    clock = api.get_clock()
    if not clock.is_open:
        return False, 0, 0
    mins_cierre = (clock.next_close - clock.timestamp).total_seconds() / 60
    ny = clock.timestamp.astimezone(NY)
    mins_apertura = ny.hour * 60 + ny.minute - (9 * 60 + 30)
    return True, mins_cierre, mins_apertura


def barras_5m(symbol):
    start = (datetime.now(pytz.utc) - timedelta(days=20)).isoformat()
    df = api.get_bars(symbol, TimeFrame(5, TimeFrameUnit.Minute),
                      start=start, limit=2500, feed=DATA_FEED).df
    if df is None or df.empty:
        return df
    idx = df.index.tz_convert(NY)
    mins = idx.hour * 60 + idx.minute
    return df[(mins >= 9 * 60 + 30) & (mins < 16 * 60)]  # solo horario regular


def analizar(symbol):
    """RSI y tendencia (última vela CERRADA de 5 min) + volatilidad diaria (ATR%).
    Devuelve dict o None si no hay datos suficientes."""
    try:
        df = barras_5m(symbol)
        if df is None or len(df) < TREND_BARS + 3:
            log(f"{symbol}: datos insuficientes para el filtro de tendencia")
            return None
        rsi = RSIIndicator(close=df["close"], window=RSI_WINDOW).rsi().iloc[-2]
        sma = df["close"].rolling(TREND_BARS).mean().iloc[-2]
        cierre = df["close"].iloc[-2]
        if rsi != rsi or sma != sma:
            return None

        d = api.get_bars(symbol, TimeFrame.Day,
                         start=(datetime.now(pytz.utc) - timedelta(days=60)).isoformat(),
                         limit=100, feed=DATA_FEED).df
        atr = AverageTrueRange(high=d["high"], low=d["low"], close=d["close"],
                               window=14).average_true_range().iloc[-1]
        atr_pct = float(atr / d["close"].iloc[-1] * 100)
        return {"rsi": round(float(rsi), 2), "tendencia_ok": bool(cierre > sma),
                "atr_pct": atr_pct}
    except Exception as e:
        log(f"Error analizando {symbol}: {e}")
        return None


def parametros_riesgo(atr_pct):
    sl = min(max(atr_pct * ATR_MULT, SL_MIN), SL_MAX)
    return round(sl, 2), round(sl * RR, 2)


def estado_cuenta():
    """(posiciones, símbolos con órdenes abiertas) o None si la API falla."""
    try:
        posiciones = api.list_positions()
        con_orden = {o.symbol for o in api.list_orders(status="open", limit=100)}
        return posiciones, con_orden
    except Exception as e:
        log(f"Error consultando posiciones/órdenes: {e}")
        return None


def registrar_fallo():
    fallos["n"] += 1
    if fallos["n"] >= 3 and not fallos["avisado"]:
        fallos["avisado"] = True
        telegram("⚠️ AURA: no logra comunicarse con Alpaca (3 intentos seguidos). Revisa el servicio.")


def registrar_exito():
    if fallos["avisado"]:
        telegram("✅ AURA: la conexión con Alpaca se restableció.")
    fallos["n"], fallos["avisado"] = 0, False


# ===================== RIESGO =====================
def limite_diario_excedido():
    """True si el equity cae más de MAX_PERDIDA_DIA% respecto al cierre anterior."""
    try:
        acc = api.get_account()
        eq, last = float(acc.equity), float(acc.last_equity)
        pct = (eq - last) / last * 100 if last else 0.0
    except Exception as e:
        log(f"Error calculando pérdida del día: {e}")
        return bot_status["pausa_riesgo"]
    pausa = pct <= -MAX_PERDIDA_DIA
    bot_status["pausa_riesgo"] = pausa
    hoy = datetime.now(NY).date()
    if pausa and bot_status["pausa_fecha"] != hoy:
        bot_status["pausa_fecha"] = hoy
        alerta(f"🛑 AURA: el equity cae {abs(pct):.2f}% hoy (límite {MAX_PERDIDA_DIA}%). "
               f"No se abrirán más posiciones hasta mañana.")
    return pausa


# ===================== POSICIONES =====================
def avisar_cierre(symbol, info):
    entrada, lado = info["entry"], info["side"]
    cierre_lado = "sell" if lado == "long" else "buy"
    try:
        for o in api.list_orders(status="closed", limit=10, symbols=[symbol]):
            if o.filled_at and o.side == cierre_lado and o.filled_avg_price:
                salida = float(o.filled_avg_price)
                pct = (salida / entrada - 1) * 100 * (1 if lado == "long" else -1)
                tipo = "Take profit" if o.type == "limit" else "Stop loss" if "stop" in o.type else "Cierre"
                icono = "✅" if pct >= 0 else "🔻"
                alerta(f"{icono} AURA: {tipo} en {symbol} · entrada ${entrada:.2f} → "
                       f"salida ${salida:.2f} ({pct:+.2f}%)")
                return
    except Exception as e:
        log(f"Error buscando cierre de {symbol}: {e}")
    alerta(f"ℹ️ AURA: la posición en {symbol} se cerró.")


def detectar_cierres(pos_dict):
    for sym in list(conocidas):
        if sym not in pos_dict:
            info = conocidas.pop(sym)
            protegidas.discard(sym)
            avisadas.discard(sym)
            avisar_cierre(sym, info)
    for sym, p in pos_dict.items():
        conocidas[sym] = {"entry": float(p.avg_entry_price),
                          "side": "short" if p.side == "short" else "long"}


def proteger_posiciones(posiciones, con_orden):
    """Si una posición no tiene órdenes de salida, avisa y (si AUTO_PROTEGER) le pone
    una orden OCO con take profit y stop loss."""
    for p in posiciones:
        sym = p.symbol
        if sym in con_orden or sym in protegidas or sym in avisadas:
            continue
        t = ultima_compra.get(sym)
        if t and (datetime.now(pytz.utc) - t) < timedelta(minutes=10):
            continue  # compra reciente: sus órdenes de salida pueden estar aún pendientes
        avisadas.add(sym)
        corto = p.side == "short"
        qty = abs(float(p.qty))
        if not (AUTO_PROTEGER and TRADING_ENABLED) or qty != int(qty):
            alerta(f"⚠️ AURA: {sym} está SIN stop loss. Protégela manualmente en Alpaca.")
            continue
        try:
            precio = float(p.current_price)
            sl, tp = abs(SL_PERCENT) / 100, TP_PERCENT / 100
            stop = precio * (1 + sl) if corto else precio * (1 - sl)
            objetivo = precio * (1 - tp) if corto else precio * (1 + tp)
            api.submit_order(
                symbol=sym, qty=int(qty), side="buy" if corto else "sell",
                type="limit", time_in_force="gtc", order_class="oco",
                take_profit={"limit_price": round(objetivo, 2)},
                stop_loss={"stop_price": round(stop, 2)},
            )
            protegidas.add(sym)
            alerta(f"🛡️ AURA: {sym} no tenía protección. Puse stop ${stop:.2f} y objetivo ${objetivo:.2f}.")
        except Exception as e:
            alerta(f"⚠️ AURA: {sym} está SIN stop loss y no pude protegerla ({type(e).__name__}). "
                   f"Hazlo manualmente en Alpaca.")


def ejecutar_compra(symbol, a):
    try:
        account = api.get_account()
        equity, disponible = float(account.equity), float(account.cash)
        precio = float(api.get_latest_trade(symbol, feed=DATA_FEED).price)
        sl, tp = parametros_riesgo(a["atr_pct"])

        por_monto = min(equity * EQUITY_POR_TRADE, disponible) / precio
        por_riesgo = (equity * RIESGO_POR_TRADE) / (precio * sl / 100)
        qty = int(min(por_monto, por_riesgo))
        if qty < 1:
            log(f"{symbol}: qty insuficiente (precio ${precio:.2f})")
            return False

        api.submit_order(
            symbol=symbol, qty=qty, side="buy", type="market",
            time_in_force="gtc",  # con "day", el TP/SL expira al cierre
            order_class="bracket",
            take_profit={"limit_price": round(precio * (1 + tp / 100), 2)},
            stop_loss={"stop_price": round(precio * (1 - sl / 100), 2)},
        )
        ultima_compra[symbol] = datetime.now(pytz.utc)
        alerta(f"🟢 AURA: COMPRA {symbol} · {qty} @ ~${precio:.2f} · RSI {a['rsi']} · "
               f"TP +{tp}% · SL -{sl}%")
        return True
    except Exception as e:
        alerta(f"❌ AURA: error al comprar {symbol}: {e}")
        return False


def en_cooldown(symbol):
    t = ultima_compra.get(symbol)
    return bool(t) and (datetime.now(pytz.utc) - t) < timedelta(minutes=COOLDOWN_MIN)


def escanear_mercado():
    estado = estado_cuenta()
    if estado is None:
        bot_status["last_error"] = "No se pudo consultar Alpaca"
        registrar_fallo()
        return
    registrar_exito()
    posiciones, con_orden = estado
    pos_dict = {p.symbol: p for p in posiciones}
    detectar_cierres(pos_dict)

    try:
        abierto, mins_cierre, mins_apertura = mercado_info()
    except Exception as e:
        bot_status["last_error"] = "No se pudo consultar el reloj de Alpaca"
        log(f"Error reloj: {e}")
        registrar_fallo()
        return
    if not abierto:
        bot_status["last_scan"] = "Mercado cerrado"
        return

    proteger_posiciones(posiciones, con_orden)
    ocupados = set(pos_dict) | con_orden
    n_pos = len(ocupados)
    bot_status["last_scan"] = ahora_col()
    bot_status["last_error"] = None

    if limite_diario_excedido():
        log("Compras en pausa por el límite de pérdida diaria")
        return
    if mins_apertura < MIN_SIN_OPERAR_APERTURA:
        log(f"Apertura del mercado: esperando {MIN_SIN_OPERAR_APERTURA - mins_apertura:.0f} min más")
        return

    log(f"ESCANEO RSI - Ocupados: {n_pos}/{MAX_POSICIONES}")
    compras = 0
    for symbol in SYMBOLS:
        if symbol in ocupados or en_cooldown(symbol):
            continue
        a = analizar(symbol)
        if a is None:
            continue
        ultimo_rsi[symbol] = a["rsi"]
        log(f"{symbol} RSI: {a['rsi']} | tendencia {'alcista' if a['tendencia_ok'] else 'bajista'} "
            f"| volatilidad {a['atr_pct']:.1f}%")

        if a["rsi"] > RSI_BUY:
            continue
        if not a["tendencia_ok"]:
            log(f"{symbol}: señal ignorada, el precio está bajo su media (tendencia bajista)")
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
        if compras >= MAX_COMPRAS_POR_ESCANEO:
            log(f"{symbol}: señal aplazada, máximo de compras por escaneo alcanzado")
            continue

        log(f"SEÑAL COMPRA {symbol} - RSI {a['rsi']} <= {RSI_BUY}")
        if ejecutar_compra(symbol, a):
            compras += 1
            n_pos += 1
            ocupados.add(symbol)


def loop_principal():
    modo = "PAPER" if IS_PAPER else "LIVE"
    log(f"BOT INICIADO ({modo}) | Activos: {', '.join(SYMBOLS)} | Trading: {TRADING_ENABLED}")
    telegram(f"🚀 AURA iniciado ({modo}). Vigilando {', '.join(SYMBOLS)}.")
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
    """Equity diario del último mes: (valores, fechas dd/mm). Listas vacías si falla."""
    try:
        h = api.get_portfolio_history(period="1M", timeframe="1D")
        pares = [(float(v), t) for v, t in zip(h.equity, h.timestamp) if v is not None]
        vals = [v for v, _ in pares]
        fechas = [datetime.fromtimestamp(t, NY).strftime("%d/%m") for _, t in pares]
        return vals, fechas
    except Exception as e:
        log(f"Error historial equity: {e}")
        return [], []


def svg_linea(vals, fechas, w=720, h=220):
    """Gráfico con ejes rotulados (mínimo, máximo y fechas) para leer valores reales."""
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    # rango mínimo (0.5% del equity) para que variaciones pequeñas no parezcan saltos enormes
    rng = max(hi - lo, hi * 0.005) or 1.0
    mid = (hi + lo) / 2
    top, bot = mid + rng / 2, mid - rng / 2
    x0, x1, y0, y1 = 78, w - 14, 14, h - 34
    px = lambda i: x0 + i * (x1 - x0) / (len(vals) - 1)
    py = lambda v: y0 + (top - v) / rng * (y1 - y0)
    pts = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(vals))
    color = "#0E8C8C" if vals[-1] >= vals[0] else "#6A2BA8"
    t = 'font-size="10" fill="#5B6B85" font-family="DM Sans,Arial,sans-serif"'
    return (
        f'<svg viewBox="0 0 {w} {h}" style="width:100%;height:auto;display:block" role="img" '
        f'aria-label="Evolución del equity en el último mes">'
        f'<defs><linearGradient id="g" x1="0" x2="0" y1="0" y2="1">'
        f'<stop offset="0" stop-color="{color}" stop-opacity=".22"/>'
        f'<stop offset="1" stop-color="{color}" stop-opacity="0"/></linearGradient></defs>'
        f'<line x1="{x0}" x2="{x1}" y1="{py(hi):.1f}" y2="{py(hi):.1f}" stroke="#E3E9F2"/>'
        f'<line x1="{x0}" x2="{x1}" y1="{py(lo):.1f}" y2="{py(lo):.1f}" stroke="#E3E9F2"/>'
        f'<text x="{x0-8}" y="{py(hi)+3:.1f}" text-anchor="end" {t}>${hi:,.0f}</text>'
        f'<text x="{x0-8}" y="{py(lo)+3:.1f}" text-anchor="end" {t}>${lo:,.0f}</text>'
        f'<text x="{x0}" y="{h-10}" {t}>{fechas[0]}</text>'
        f'<text x="{x1}" y="{h-10}" text-anchor="end" {t}>{fechas[-1]}</text>'
        f'<polygon points="{x0},{y1} {pts} {x1},{y1}" fill="url(#g)"/>'
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
header img{height:110px;width:auto}
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
    {% if pausa %}<span class="pill bad">● Pausa por límite diario</span>{% endif %}
    <span class="pill {{ 'ok' if telegram else 'warn' }}">● Telegram {{ 'activo' if telegram else 'sin configurar' }}</span>
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
  {% if cambio %}<p class="sub" style="margin:-8px 0 12px">{{ cambio }}</p>{% endif %}
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

<footer>Stop y objetivo según la volatilidad de cada activo (relación 1:{{ rr }}) · hasta {{ eq_pct }}% del equity por operación · pausa si el día pierde {{ max_loss }}%. Esto no es asesoría financiera; operar implica riesgo de pérdida.</footer>
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

    chart, cambio = "", ""
    if connected:
        vals, fechas = historial_equity()
        chart = svg_linea(vals, fechas)
        if len(vals) >= 2 and vals[0]:
            d = vals[-1] - vals[0]
            cambio = f"{d:+,.2f} USD ({d / vals[0] * 100:+.2f}%) desde {fechas[0]}"

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
        eq_pct=round(EQUITY_POR_TRADE * 100), rr=RR, max_loss=MAX_PERDIDA_DIA,
        pausa=bot_status["pausa_riesgo"], telegram=bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID),
        chart=chart, cambio=cambio,
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
