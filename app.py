"""
AURA SYNAPSE V4.1 - Team Yesid + Syna  (V4.0 corregida)

Variables de entorno:
  ALPACA_API_KEY / APCA_API_KEY_ID           (obligatoria)
  ALPACA_SECRET_KEY / APCA_API_SECRET_KEY    (obligatoria)
  ALPACA_BASE_URL / APCA_API_BASE_URL        (default: paper)
  DASHBOARD_TOKEN                            (obligatoria: protege el dashboard y /api)
  GOOGLE_SHEET_ID (o SHEET_ID), GOOGLE_CREDS_JSON   (opcionales: registro en Google Sheets)
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID           (opcionales: avisos)
  AUTO_TRADING  ("true" por defecto; "false" = solo analiza, no opera)
  AUTO_START    ("true" por defecto)

Despliegue en Render (UN solo worker):
  gunicorn app:app --workers 1 --threads 4 --bind 0.0.0.0:$PORT
"""
import os
import json
import time
import threading
from collections import deque
from datetime import datetime, timedelta

import pytz
import requests
import pandas as pd
from flask import Flask, jsonify, render_template_string, request, abort

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (MarketOrderRequest, GetOrdersRequest,
                                         TakeProfitRequest, StopLossRequest)
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass, QueryOrderStatus
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
    from alpaca.data.enums import DataFeed
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    ALPACA_OK = True
except Exception as e:
    print(f"Alpaca import error: {e}", flush=True)
    ALPACA_OK = False

app = Flask(__name__)


def env(key1, key2, default=""):
    return os.environ.get(key1, os.environ.get(key2, default)).strip()


# ===================== CONFIG =====================
API_KEY = env("ALPACA_API_KEY", "APCA_API_KEY_ID")
API_SECRET = env("ALPACA_SECRET_KEY", "APCA_API_SECRET_KEY")
BASE_URL = env("ALPACA_BASE_URL", "APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
IS_PAPER = "paper" in BASE_URL
AUTO_TRADING = env("AUTO_TRADING", "AUTO_TRADING", "true").lower() in ["true", "1", "yes"]
AUTO_START = env("AUTO_START", "AUTO_START", "true").lower() in ["true", "1", "yes"]
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "").strip()
SHEET_ID = env("GOOGLE_SHEET_ID", "SHEET_ID", "")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS_JSON", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

SYMBOLS = ["AAPL", "TSLA", "NVDA", "MSFT", "SPY"]
VELA_MIN = 5                  # velas de 5 min (menos ruido que las de 1 min)
RSI_PERIODO = 14
RSI_BUY = 40                  # compra si RSI < 40
RSI_SELL = 65                 # vende (solo si va en ganancia) si RSI > 65
SL_PERCENT = 2.0              # stop loss -2% (orden bracket)
TP_PERCENT = 3.0              # take profit +3% (orden bracket)
PCT_POR_TRADE = 0.05          # 5% del equity por operación (antes 19% del cash)
MAX_POSICIONES = 5
MAX_COMPRAS_POR_ESCANEO = 2
MAX_PERDIDA_DIA = 3.0         # % de pérdida del día que pausa las compras
MIN_SIN_OPERAR_APERTURA = 15  # minutos
MIN_PARA_CIERRE = 15          # minutos
COOLDOWN_MIN = 60
INTERVALO_ABIERTO = 120       # segundos entre escaneos con mercado abierto
INTERVALO_CERRADO = 180

TZ_COL = pytz.timezone("America/Bogota")
NY = pytz.timezone("America/New_York")

# ===================== ESTADO =====================
estado = {
    "version": "V4.1",
    "status": "Iniciando V4.1...",
    "equity": 0.0,
    "cash": 0.0,
    "ultimo_analisis": "Nunca",
    "ultimo_heartbeat": "",
    "log": [],
    "detalles": {},
    "posiciones": [],
    "vive_desde": datetime.now(TZ_COL).isoformat(),
    "trades_hoy": 0,
    "fecha_trades": None,
    "auto_trading": AUTO_TRADING,
    "google_sheet": bool(SHEET_ID and GOOGLE_CREDS_JSON),
    "google_status": "No configurado",
    "mercado_abierto": False,
    "pausa_riesgo": False,
    "running": False,
}
trading = None
data_client = None
logs_hist = deque(maxlen=60)
ultima_compra = {}      # symbol -> datetime UTC
conocidas = {}          # symbol -> {"entry", "side"}
ventas_propias = set()  # ventas hechas por el bot (para no avisar el cierre dos veces)
fallos = {"n": 0, "avisado": False, "pausa_fecha": None}
_lock_inicio = threading.Lock()
_lock_hoja = threading.Lock()
_hoja = {"ws": None}


def get_time():
    return datetime.now(TZ_COL)


def log(msg):
    entry = f"[{get_time().strftime('%H:%M:%S')}] {msg}"
    logs_hist.append(entry)
    print(entry, flush=True)


def telegram(msg):
    """Aviso por Telegram sin bloquear. No imprime el error completo (la URL lleva el token)."""
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        return

    def _send():
        try:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                          json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=8)
        except Exception as e:
            print(f"Error Telegram: {type(e).__name__}", flush=True)

    threading.Thread(target=_send, daemon=True).start()


def alerta(msg):
    log(msg)
    telegram(msg)


def val(x):
    """Texto en minúscula de un enum o valor (para comparar side/type)."""
    return str(getattr(x, "value", x)).lower()


# ===================== GOOGLE SHEETS =====================
HEADERS = ["Fecha COL", "Símbolo", "Acción", "Precio", "RSI", "Equity", "Cash", "Mensaje", "Trade #"]


def sheets_activo():
    return bool(SHEET_ID and GOOGLE_CREDS_JSON)


def _obtener_hoja():
    """Conecta una sola vez y reutiliza la hoja (antes se reconectaba en cada fila)."""
    if _hoja["ws"] is not None:
        return _hoja["ws"]
    import gspread
    client = gspread.service_account_from_dict(json.loads(GOOGLE_CREDS_JSON))
    ws = client.open_by_key(SHEET_ID).sheet1
    if not ws.row_values(1):
        ws.append_row(HEADERS)
    _hoja["ws"] = ws
    return ws


def send_to_sheets(row):
    """Escribe en segundo plano: el bot no espera a Google."""
    if not sheets_activo():
        return

    def _escribir():
        try:
            with _lock_hoja:
                _obtener_hoja().append_row(row, value_input_option="USER_ENTERED")
            estado["google_status"] = "Conectado"
        except Exception as e:
            _hoja["ws"] = None
            estado["google_status"] = "Error al escribir (revisa permisos)"
            print(f"Sheets error: {type(e).__name__}: {str(e)[:150]}", flush=True)

    threading.Thread(target=_escribir, daemon=True).start()


def init_sheet():
    if not sheets_activo():
        return
    try:
        with _lock_hoja:
            n = len(_obtener_hoja().get_all_values()) - 1
        estado["google_status"] = f"Conectado ({max(n, 0)} registros)"
        log("Google Sheets conectado")
    except Exception as e:
        _hoja["ws"] = None
        estado["google_status"] = "Error de conexión (revisa credenciales y permisos)"
        alerta(f"⚠️ AURA: no pude conectar con Google Sheets ({type(e).__name__}).")


def fila_sheet(accion, sym, precio, rsi, mensaje):
    return [get_time().strftime("%Y-%m-%d %H:%M:%S"), sym, accion,
            f"{precio:.2f}" if precio != "" else "",
            f"{rsi:.1f}" if rsi != "" else "",
            f"{estado['equity']:.2f}", f"{estado['cash']:.2f}", mensaje, estado["trades_hoy"]]


# ===================== SEGURIDAD =====================
def require_token():
    if not DASHBOARD_TOKEN:
        abort(503, "DASHBOARD_TOKEN no configurado")
    token = request.headers.get("X-Token") or request.args.get("token")
    if token != DASHBOARD_TOKEN:
        abort(401)


# ===================== INDICADORES Y MERCADO =====================
def rsi_wilder(close, period=RSI_PERIODO):
    """RSI estándar (suavizado de Wilder), el mismo que muestran las plataformas."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.where(avg_loss != 0)
    rsi = 100 - 100 / (1 + rs)
    return rsi.where(avg_loss != 0, 100.0)


def analizar(sym):
    """Precio y RSI (de la última vela CERRADA) con datos de Alpaca. None si no hay datos."""
    try:
        req = StockBarsRequest(
            symbol_or_symbols=sym,
            timeframe=TimeFrame(VELA_MIN, TimeFrameUnit.Minute),
            start=datetime.now(pytz.utc) - timedelta(days=5),
            feed=DataFeed.IEX,
        )
        bars = data_client.get_stock_bars(req).df
        if bars is None or bars.empty:
            return None
        df = bars.xs(sym, level=0) if isinstance(bars.index, pd.MultiIndex) else bars
        idx = df.index.tz_convert(NY)
        mins = idx.hour * 60 + idx.minute
        df = df[(mins >= 570) & (mins < 960)]          # solo horario regular
        if len(df) < RSI_PERIODO * 2:
            return None
        rsi = rsi_wilder(df["close"]).iloc[-2]          # -1 es la vela en curso
        if rsi != rsi:
            return None
        return {"precio": float(df["close"].iloc[-1]), "rsi": float(rsi)}
    except Exception as e:
        log(f"Error analizando {sym}: {e}")
        return None


def mercado_info():
    """(abierto, min_para_cierre, min_desde_apertura) con el reloj real de Alpaca
    (cubre feriados y el cambio de horario)."""
    clock = trading.get_clock()
    if not clock.is_open:
        return False, 0, 0
    ny = clock.timestamp.astimezone(NY)
    return (True, (clock.next_close - clock.timestamp).total_seconds() / 60,
            ny.hour * 60 + ny.minute - 570)


def limite_diario_excedido(cuenta):
    try:
        eq, last = float(cuenta.equity), float(cuenta.last_equity)
        pct = (eq - last) / last * 100 if last else 0.0
    except Exception:
        return estado["pausa_riesgo"]
    pausa = pct <= -MAX_PERDIDA_DIA
    estado["pausa_riesgo"] = pausa
    hoy = datetime.now(NY).date()
    if pausa and fallos["pausa_fecha"] != hoy:
        fallos["pausa_fecha"] = hoy
        alerta(f"🛑 AURA: el equity cae {abs(pct):.2f}% hoy (límite {MAX_PERDIDA_DIA}%). "
               f"No se abrirán más posiciones hasta mañana.")
    return pausa


# ===================== OPERACIONES =====================
def avisar_cierre(sym, info):
    entrada, lado = info["entry"], info["side"]
    cierre_lado = "sell" if lado == "long" else "buy"
    try:
        cerradas = trading.get_orders(filter=GetOrdersRequest(
            status=QueryOrderStatus.CLOSED, symbols=[sym], limit=10))
        for o in cerradas:
            if o.filled_at and o.filled_avg_price and val(o.side) == cierre_lado:
                salida = float(o.filled_avg_price)
                pct = (salida / entrada - 1) * 100 * (1 if lado == "long" else -1)
                tipo_o = val(getattr(o, "order_type", None) or getattr(o, "type", ""))
                tipo = "Take profit" if "limit" in tipo_o else "Stop loss" if "stop" in tipo_o else "Cierre"
                msg = (f"{'✅' if pct >= 0 else '🔻'} AURA: {tipo} en {sym} · entrada ${entrada:.2f} → "
                       f"salida ${salida:.2f} ({pct:+.2f}%)")
                send_to_sheets(fila_sheet("CIERRE", sym, salida, "", msg))
                alerta(msg)
                return
    except Exception as e:
        log(f"Error buscando cierre de {sym}: {e}")
    msg = f"ℹ️ AURA: la posición en {sym} se cerró."
    send_to_sheets(fila_sheet("CIERRE", sym, "", "", msg))
    alerta(msg)


def detectar_cierres(pos):
    for sym in list(conocidas):
        if sym not in pos:
            info = conocidas.pop(sym)
            if sym in ventas_propias:
                ventas_propias.discard(sym)   # ya se registró como VENTA
            else:
                avisar_cierre(sym, info)
    for sym, p in pos.items():
        conocidas[sym] = {"entry": float(p.avg_entry_price),
                          "side": "short" if "short" in val(p.side) else "long"}


def en_cooldown(sym):
    t = ultima_compra.get(sym)
    return bool(t) and (datetime.now(pytz.utc) - t) < timedelta(minutes=COOLDOWN_MIN)


def ejecutar_compra(sym, precio_ref, rsi):
    """Compra con orden bracket (entrada + take profit + stop loss en una sola orden)."""
    try:
        cuenta = trading.get_account()                    # saldo fresco en cada compra
        equity, cash = float(cuenta.equity), float(cuenta.cash)
        try:
            lt = data_client.get_stock_latest_trade(
                StockLatestTradeRequest(symbol_or_symbols=sym, feed=DataFeed.IEX))
            precio = float(lt[sym].price)
        except Exception:
            precio = precio_ref
        qty = int(min(equity * PCT_POR_TRADE, cash) // precio)
        if qty < 1:
            log(f"{sym}: cantidad insuficiente (precio ${precio:.2f}, cash ${cash:.2f})")
            return None
        tp = round(precio * (1 + TP_PERCENT / 100), 2)
        sl = round(precio * (1 - SL_PERCENT / 100), 2)
        trading.submit_order(MarketOrderRequest(
            symbol=sym, qty=qty, side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC,                # con DAY, el TP/SL expira al cierre
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=tp),
            stop_loss=StopLossRequest(stop_price=sl)))
        ultima_compra[sym] = datetime.now(pytz.utc)
        estado["trades_hoy"] += 1
        msg = (f"🟢 COMPRA {sym} x{qty} @ ${precio:.2f} RSI {rsi:.1f} · "
               f"TP +{TP_PERCENT}% SL -{SL_PERCENT}%")
        send_to_sheets(fila_sheet("COMPRA", sym, precio, rsi, msg))
        alerta(f"AURA: {msg}")
        return msg
    except Exception as e:
        alerta(f"❌ AURA: error al comprar {sym}: {e}")
        return None


def ejecutar_venta(sym, pos, rsi, precio):
    """Cierra una posición en ganancia: primero cancela sus órdenes TP/SL y luego la cierra."""
    try:
        abiertas = trading.get_orders(filter=GetOrdersRequest(
            status=QueryOrderStatus.OPEN, symbols=[sym], limit=50))
        for o in abiertas:
            trading.cancel_order_by_id(o.id)
        time.sleep(1)
        trading.close_position(sym)
        ventas_propias.add(sym)
        estado["trades_hoy"] += 1
        pl = float(pos.unrealized_pl)
        msg = f"🔻 VENTA {sym} x{float(pos.qty)} @ ${precio:.2f} RSI {rsi:.1f} · P/L ${pl:+.2f}"
        send_to_sheets(fila_sheet("VENTA", sym, precio, rsi, msg))
        alerta(f"AURA: {msg}")
        return msg
    except Exception as e:
        alerta(f"❌ AURA: error al vender {sym}: {e}")
        return None


def registrar_fallo():
    fallos["n"] += 1
    if fallos["n"] >= 3 and not fallos["avisado"]:
        fallos["avisado"] = True
        telegram("⚠️ AURA: no logra comunicarse con Alpaca (3 intentos seguidos).")


def registrar_exito():
    if fallos["avisado"]:
        telegram("✅ AURA: la conexión con Alpaca se restableció.")
    fallos["n"], fallos["avisado"] = 0, False


def escanear():
    cuenta = trading.get_account()
    estado["equity"], estado["cash"] = float(cuenta.equity), float(cuenta.cash)

    hoy = datetime.now(NY).date()
    if estado["fecha_trades"] != hoy:                      # el contador se reinicia cada día
        estado["fecha_trades"], estado["trades_hoy"] = hoy, 0

    posiciones = {p.symbol: p for p in trading.get_all_positions()}
    abiertas = trading.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=100))
    con_orden = {o.symbol for o in abiertas}
    detectar_cierres(posiciones)
    estado["posiciones"] = [{"symbol": p.symbol, "qty": float(p.qty),
                             "market_value": float(p.market_value),
                             "avg_entry": float(p.avg_entry_price)} for p in posiciones.values()]
    registrar_exito()

    abierto, mins_cierre, mins_apertura = mercado_info()
    estado["mercado_abierto"] = abierto
    pausa = abierto and limite_diario_excedido(cuenta)
    ocupados = set(posiciones) | con_orden
    n_pos, compras = len(ocupados), 0

    detalles, logs = {}, []
    if not abierto:
        logs.append(f"⏸️ Mercado cerrado - Standby {get_time().strftime('%H:%M:%S COL')}")
    elif pausa:
        logs.append("🛑 Compras en pausa por el límite de pérdida diaria")
    elif mins_apertura < MIN_SIN_OPERAR_APERTURA:
        logs.append(f"⏳ Apertura: esperando {MIN_SIN_OPERAR_APERTURA - mins_apertura:.0f} min más")

    for sym in SYMBOLS:
        a = analizar(sym)
        if a is None:
            continue
        precio, rsi = a["precio"], a["rsi"]
        if rsi < 30: dec, color, action = "COMPRAR FUERTE", "#00ff88", "BUY"
        elif rsi < RSI_BUY: dec, color, action = "COMPRAR", "#00cc66", "BUY"
        elif rsi > 75: dec, color, action = "VENDER FUERTE", "#ff4444", "SELL"
        elif rsi > RSI_SELL: dec, color, action = "VENDER", "#ffaa00", "SELL"
        else: dec, color, action = "NEUTRAL", "#888", "HOLD"
        detalles[sym] = {"precio": precio, "rsi": round(rsi, 1), "decision": dec,
                         "color": color, "action": action}
        logs.append(f"{sym} ${precio:.2f} RSI {rsi:.1f} -> {dec}" + ("" if abierto else " [Mercado cerrado]"))

        if not (abierto and AUTO_TRADING) or pausa or mins_apertura < MIN_SIN_OPERAR_APERTURA:
            continue

        if action == "BUY" and rsi < RSI_BUY:
            if (sym in ocupados or en_cooldown(sym) or n_pos >= MAX_POSICIONES
                    or mins_cierre < MIN_PARA_CIERRE or compras >= MAX_COMPRAS_POR_ESCANEO):
                continue
            msg = ejecutar_compra(sym, precio, rsi)
            if msg:
                logs.append(msg)
                compras += 1
                n_pos += 1
                ocupados.add(sym)
        elif action == "SELL" and rsi > RSI_SELL and sym in posiciones:
            p = posiciones[sym]
            if "short" in val(p.side):
                continue
            if float(p.unrealized_pl) > 0:                # solo se vende con ganancia; el stop cubre las pérdidas
                msg = ejecutar_venta(sym, p, rsi, precio)
                if msg:
                    logs.append(msg)

    estado["detalles"] = detalles
    estado["log"] = (logs + list(reversed(list(logs_hist)[-10:])))[:25]
    estado["ultimo_analisis"] = get_time().strftime("%Y-%m-%d %H:%M:%S COL")
    estado["status"] = (f"Activa • {'Paper' if IS_PAPER else 'REAL'} • Auto:{'ON' if AUTO_TRADING else 'OFF'}"
                        f" • Sheets:{'ON' if sheets_activo() else 'OFF'}"
                        f"{' • PAUSA RIESGO' if estado['pausa_riesgo'] else ''}")


def cargar_clientes():
    global trading, data_client
    if not ALPACA_OK:
        estado["status"] = "Error: no se pudo importar alpaca-py"
        return False
    if not (API_KEY and API_SECRET):
        estado["status"] = "Esperando keys - configura ALPACA_API_KEY y ALPACA_SECRET_KEY"
        return False
    try:
        trading = TradingClient(API_KEY, API_SECRET, paper=IS_PAPER)
        data_client = StockHistoricalDataClient(API_KEY, API_SECRET)
        cuenta = trading.get_account()
        estado["equity"], estado["cash"] = float(cuenta.equity), float(cuenta.cash)
        log(f"AURA V4.1 conectada ({'PAPER' if IS_PAPER else 'REAL'}): equity ${estado['equity']:.2f}")
        telegram(f"🚀 AURA V4.1 iniciada ({'PAPER' if IS_PAPER else 'REAL'}). Vigilando {', '.join(SYMBOLS)}.")
        threading.Thread(target=init_sheet, daemon=True).start()
        return True
    except Exception as e:
        trading = data_client = None
        log(f"Error conectando con Alpaca: {e}")
        estado["status"] = "Error de conexión con Alpaca"
        return False


def bot_loop():
    while estado["running"]:
        pausa_s = INTERVALO_CERRADO
        try:
            if trading is None and not cargar_clientes():
                time.sleep(60)
                continue
            estado["ultimo_heartbeat"] = get_time().strftime("%H:%M:%S")
            escanear()
            pausa_s = INTERVALO_ABIERTO if estado["mercado_abierto"] else INTERVALO_CERRADO
        except Exception as e:
            log(f"Error en el loop: {e}")
            estado["status"] = "Error temporal (reintentando)"
            registrar_fallo()
            pausa_s = 60
        time.sleep(pausa_s)


def iniciar_bot():
    """Arranca el hilo una sola vez por proceso (también funciona con gunicorn)."""
    with _lock_inicio:
        if estado["running"]:
            return False
        estado["running"] = True
        threading.Thread(target=bot_loop, daemon=True).start()
        return True


DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AURA V4.1 - Yesid + Syna</title>
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
const TOKEN=new URLSearchParams(location.search).get('token')||'';
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
setInterval(()=>{fetch('/api?token='+encodeURIComponent(TOKEN)).then(r=>r.json()).then(d=>{
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
   logHtml+=`<div class="log-line" style="color:${c}">${esc(l)}</div>`;
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
 <div style="display:flex;align-items:center;gap:14px"><span style="background:#fff;border-radius:12px;padding:6px 10px;display:inline-flex"><img src="/static/logo.png" alt="AURA" style="height:56px;width:auto" onerror="this.parentNode.style.display='none'"></span><div class="logo">AURA V4.1</div></div>
 <div style="font-family:'JetBrains Mono',monospace;font-size:12px"><span class="status-dot"></span><span id="status" class="live">Cargando...</span> • <span id="time">...</span> • HB <span id="heartbeat">--</span> • <span id="trades">0 hoy</span></div>
</div>
<div class="cards">
 <div class="card"><div class="card-label">Equity Total (Paper)</div><div class="card-value" id="equity">$0.00</div><div class="card-sub">Alpaca Paper • V4.1 • Live<br><a href="/health">Health</a></div><div id="pos" style="margin-top:12px"></div></div>
 <div class="card"><div class="card-label">Cash Disponible</div><div class="card-value" id="cash" style="color:#00aaff">$0.00</div><div class="card-sub">{{pct}}% del equity por trade • Auto: {{auto}}<br>Google: <span id="google">Cargando...</span><br><a href="https://docs.google.com/spreadsheets/d/{{sheet_id}}" target="_blank" style="color:#00ff88">Abrir mi Sheet ↗</a></div></div>
 <div class="card"><div class="card-label">Creadores • Sistema</div><div class="card-value" style="font-size:16px">Yesid + Syna<br><span style="font-size:11px;color:#888">Madrid, COL → Render 24/7<br>5 Expertas: AAPL TSLA NVDA MSFT SPY<br>Vive: {{vive}}<br>RSI&lt;40 Compra • RSI&gt;65 Venta (solo en ganancia)</span></div></div>
</div>
<h3 style="margin-bottom:12px;color:#888;font-size:13px;letter-spacing:1px;text-transform:uppercase">Mercado en Tiempo Real - 5 Expertas RSI</h3>
<div id="symbols" class="grid-symbols"><div style="color:#555">Cargando 5 símbolos...</div></div>
<h3 style="margin-bottom:12px;color:#888;font-size:13px;letter-spacing:1px;text-transform:uppercase">Log de Syna + Trades Reales → Google Sheets</h3>
<div id="log" class="log">Iniciando cerebro V4.1...</div>
<div class="footer">AURA SYNAPSE V4.1 SHEETS • RENDER FREE • PAPER TRADING • AUTO-TRADING • NO ES ASESORÍA FINANCIERA • HECHO CON 💚 EN MADRID<br>URL: https://aura-synapse-yesid.onrender.com • Estrategia: RSI 14 en velas de 5 min • Horario según el reloj de Alpaca</div>
</body>
</html>
"""

@app.route("/")
def home():
    require_token()
    return render_template_string(DASHBOARD_HTML, vive=estado["vive_desde"],
                                  auto="ON" if AUTO_TRADING else "OFF",
                                  sheet_id=SHEET_ID, pct=round(PCT_POR_TRADE * 100))


@app.route("/api")
def api():
    require_token()
    return jsonify({
        "mensaje": "🧠 AURA V4.1 VIVE",
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
        "paper": IS_PAPER,
    })


@app.route("/api/debug")
def debug():
    require_token()
    return jsonify({
        "status": "ALPACA_" + ("PAPER" if IS_PAPER else "REAL") if API_KEY else "SIN_KEYS",
        "version": "V4.1",
        "running": estado["running"],
        "symbols": SYMBOLS,
        "has_keys": bool(API_KEY),
        "auto": AUTO_TRADING,
        "google_configured": sheets_activo(),
        "google_status": estado["google_status"],
        "pausa_riesgo": estado["pausa_riesgo"],
    })


@app.route("/health")
def health():
    return "OK AURA V4.1", 200


# Arranque compatible con gunicorn (con --workers 1 solo se crea un bot)
if AUTO_START:
    iniciar_bot()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"🚀 AURA V4.1 puerto {port} Keys:{'OK' if API_KEY else 'FALTAN'} Auto:{AUTO_TRADING}", flush=True)
    app.run(host="0.0.0.0", port=port)
