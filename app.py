"""
AURA SYNAPSE V7.0 - Team Yesid + Syna  (fusión V6.3 + V7.0)

Variables de entorno:
  ALPACA_API_KEY / APCA_API_KEY_ID           (obligatoria)
  ALPACA_SECRET_KEY / APCA_API_SECRET_KEY    (obligatoria)
  ALPACA_BASE_URL / APCA_API_BASE_URL        (default: paper)
  DASHBOARD_TOKEN                            (obligatoria: protege el dashboard y /api)
  GOOGLE_SHEET_ID (o SHEET_ID), GOOGLE_CREDS_JSON   (opcionales: registro en Google Sheets)
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID           (opcionales: avisos)
  AUTO_TRADING  ("true" por defecto; "false" = solo analiza, no opera)
  AUTO_START    ("true" por defecto)
  AUTO_PROTEGER ("true" por defecto: pone stop/objetivo a posiciones que no tengan)

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
    from alpaca.trading.requests import (MarketOrderRequest, LimitOrderRequest, GetOrdersRequest,
                                         TakeProfitRequest, StopLossRequest,
                                         GetPortfolioHistoryRequest)
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
TREND_BARS = 780              # media móvil de ~10 días (filtro de tendencia: solo compra si el precio está sobre ella)
RIESGO_POR_TRADE = 0.001      # arriesgar como máximo 0.1% del equity si salta el stop
ATR_MULT = 1.0                # stop = 1 x volatilidad diaria (ATR 14 días)...
SL_MIN, SL_MAX = 1.5, 4.0     # ...limitado entre 1.5% y 4%
RR = 1.5                      # take profit = stop x 1.5
SL_DEFECTO, TP_DEFECTO = 2.0, 3.0   # si no hay datos de volatilidad / posiciones sin protección
AUTO_PROTEGER = env("AUTO_PROTEGER", "AUTO_PROTEGER", "true").lower() in ["true", "1", "yes"]
PCT_POR_TRADE = 0.05          # tope: 5% del equity por operación
MAX_POSICIONES = 5
MAX_COMPRAS_POR_ESCANEO = 2
MAX_PERDIDA_DIA = 3.0         # % de pérdida del día que pausa las compras
MIN_SIN_OPERAR_APERTURA = 15  # minutos
MIN_PARA_CIERRE = 15          # minutos
COOLDOWN_MIN = 60
INTERVALO_ABIERTO = 60        # segundos entre escaneos con mercado abierto
INTERVALO_CERRADO = 180

TZ_COL = pytz.timezone("America/Bogota")
NY = pytz.timezone("America/New_York")

# ===================== ESTADO =====================
estado = {
    "version": "V7.0",
    "status": "Iniciando V7.0...",
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
    "conectado": False,
    "ultimo_error": None,
    "bp": 0.0,
    "day_pl": 0.0,
    "pos_det": [],
    "chart": "",
    "cambio": "",
}
trading = None
data_client = None
logs_hist = deque(maxlen=60)
ultima_compra = {}      # symbol -> datetime UTC
conocidas = {}          # symbol -> {"entry", "side"}
ventas_propias = set()  # ventas hechas por el bot (para no avisar el cierre dos veces)
fallos = {"n": 0, "avisado": False, "pausa_fecha": None}
protegidas = set()      # symbols a los que el bot ya les puso protección
avisadas = set()        # symbols ya avisados como "sin protección"
atr_cache = {}          # symbol -> (fecha, atr%)
_chart = {"t": 0.0}
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
HEADERS = ["fecha_hora_col", "evento", "symbol", "lado", "qty", "precio", "rsi",
           "tendencia_alcista", "volatilidad_pct", "sl_pct", "tp_pct",
           "resultado_pct", "motivo_cierre", "equity", "cash", "mensaje", "trade_n_hoy"]


def sheets_activo():
    return bool(SHEET_ID and GOOGLE_CREDS_JSON)


def _obtener_hoja():
    """Abre (o crea) la pestaña 'Trades' y reutiliza la conexión."""
    if _hoja["ws"] is not None:
        return _hoja["ws"]
    import gspread
    client = gspread.service_account_from_dict(json.loads(GOOGLE_CREDS_JSON))
    sh = client.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet("Trades")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet("Trades", rows=1000, cols=len(HEADERS))
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


def fila(evento, symbol, **d):
    d.update(evento=evento, symbol=symbol,
             fecha_hora_col=get_time().strftime("%Y-%m-%d %H:%M:%S"),
             equity=f"{estado['equity']:.2f}", cash=f"{estado['cash']:.2f}",
             trade_n_hoy=estado["trades_hoy"])
    return [d.get(h, "") for h in HEADERS]


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


def atr_diario(sym):
    """Volatilidad diaria (ATR de 14 días) como % del precio. Se calcula una vez al día."""
    hoy = datetime.now(NY).date()
    c = atr_cache.get(sym)
    if c and c[0] == hoy:
        return c[1]
    try:
        req = StockBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Day,
                               start=datetime.now(pytz.utc) - timedelta(days=60), feed=DataFeed.IEX)
        d = data_client.get_stock_bars(req).df
        d = d.xs(sym, level=0) if isinstance(d.index, pd.MultiIndex) else d
        pc = d["close"].shift()
        tr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().iloc[-1]
        pct = float(atr / d["close"].iloc[-1] * 100)
        if pct != pct:
            return None
        atr_cache[sym] = (hoy, pct)
        return pct
    except Exception as e:
        log(f"Error volatilidad {sym}: {e}")
        return None


def analizar(sym):
    """Precio, RSI (última vela CERRADA de 5 min), tendencia y volatilidad. None si no hay datos."""
    try:
        req = StockBarsRequest(
            symbol_or_symbols=sym,
            timeframe=TimeFrame(VELA_MIN, TimeFrameUnit.Minute),
            start=datetime.now(pytz.utc) - timedelta(days=20),
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
        tendencia_ok = None                              # None = sin datos suficientes
        if len(df) >= TREND_BARS + 3:
            sma = df["close"].rolling(TREND_BARS).mean().iloc[-2]
            if sma == sma:
                tendencia_ok = bool(df["close"].iloc[-2] > sma)
        return {"precio": float(df["close"].iloc[-1]), "rsi": float(rsi),
                "tendencia_ok": tendencia_ok, "atr_pct": atr_diario(sym)}
    except Exception as e:
        log(f"Error analizando {sym}: {e}")
        return None


def parametros_riesgo(atr_pct):
    if atr_pct is None:
        return SL_DEFECTO, TP_DEFECTO
    sl = min(max(atr_pct * ATR_MULT, SL_MIN), SL_MAX)
    return round(sl, 2), round(sl * RR, 2)


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
    lado_txt = "largo" if lado == "long" else "corto"
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
                send_to_sheets(fila("CIERRE", sym, lado=lado_txt, qty=info.get("qty", ""),
                                    precio=round(salida, 2), resultado_pct=round(pct, 2),
                                    motivo_cierre=tipo, mensaje=msg))
                alerta(msg)
                return
    except Exception as e:
        log(f"Error buscando cierre de {sym}: {e}")
    msg = f"ℹ️ AURA: la posición en {sym} se cerró."
    send_to_sheets(fila("CIERRE", sym, lado=lado_txt, qty=info.get("qty", ""),
                        motivo_cierre="desconocido", mensaje=msg))
    alerta(msg)


def detectar_cierres(pos):
    for sym in list(conocidas):
        if sym not in pos:
            info = conocidas.pop(sym)
            protegidas.discard(sym)
            avisadas.discard(sym)
            if sym in ventas_propias:
                ventas_propias.discard(sym)   # ya se registró como VENTA
            else:
                avisar_cierre(sym, info)
    for sym, p in pos.items():
        conocidas[sym] = {"entry": float(p.avg_entry_price), "qty": float(p.qty),
                          "side": "short" if "short" in val(p.side) else "long"}


def en_cooldown(sym):
    t = ultima_compra.get(sym)
    return bool(t) and (datetime.now(pytz.utc) - t) < timedelta(minutes=COOLDOWN_MIN)


def ejecutar_compra(sym, a):
    """Compra con orden bracket. Stop y objetivo según la volatilidad del activo;
    cantidad limitada por el riesgo (0.1% del equity) y por el 5% del equity."""
    try:
        cuenta = trading.get_account()                    # saldo fresco en cada compra
        equity, cash = float(cuenta.equity), float(cuenta.cash)
        try:
            lt = data_client.get_stock_latest_trade(
                StockLatestTradeRequest(symbol_or_symbols=sym, feed=DataFeed.IEX))
            precio = float(lt[sym].price)
        except Exception:
            precio = a["precio"]
        sl, tp = parametros_riesgo(a["atr_pct"])
        por_monto = min(equity * PCT_POR_TRADE, cash) / precio
        por_riesgo = (equity * RIESGO_POR_TRADE) / (precio * sl / 100)
        qty = int(min(por_monto, por_riesgo))
        if qty < 1:
            log(f"{sym}: cantidad insuficiente (precio ${precio:.2f}, cash ${cash:.2f})")
            return None
        trading.submit_order(MarketOrderRequest(
            symbol=sym, qty=qty, side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC,                # con DAY, el TP/SL expira al cierre
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=round(precio * (1 + tp / 100), 2)),
            stop_loss=StopLossRequest(stop_price=round(precio * (1 - sl / 100), 2))))
        ultima_compra[sym] = datetime.now(pytz.utc)
        estado["trades_hoy"] += 1
        msg = f"🟢 COMPRA {sym} x{qty} @ ${precio:.2f} RSI {a['rsi']:.1f} · TP +{tp}% SL -{sl}%"
        send_to_sheets(fila("COMPRA", sym, lado="largo", qty=qty, precio=round(precio, 2),
                            rsi=round(a["rsi"], 1),
                            tendencia_alcista="SI" if a["tendencia_ok"] else "NO",
                            volatilidad_pct=round(a["atr_pct"], 2) if a["atr_pct"] is not None else "",
                            sl_pct=sl, tp_pct=tp, mensaje=msg))
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
        pl, plpc = float(pos.unrealized_pl), float(pos.unrealized_plpc) * 100
        msg = f"🔻 VENTA {sym} x{float(pos.qty):g} @ ${precio:.2f} RSI {rsi:.1f} · P/L ${pl:+.2f} ({plpc:+.2f}%)"
        send_to_sheets(fila("VENTA", sym, lado="largo", qty=float(pos.qty), precio=round(precio, 2),
                            rsi=round(rsi, 1), resultado_pct=round(plpc, 2),
                            motivo_cierre=f"RSI>{RSI_SELL}", mensaje=msg))
        alerta(f"AURA: {msg}")
        return msg
    except Exception as e:
        alerta(f"❌ AURA: error al vender {sym}: {e}")
        return None


def proteger_posiciones(posiciones, con_orden):
    """Si una posición no tiene órdenes de salida, avisa y (si AUTO_PROTEGER) le pone una
    orden OCO con take profit y stop loss."""
    for p in posiciones:
        sym = p.symbol
        if sym in con_orden or sym in protegidas or sym in avisadas:
            continue
        t = ultima_compra.get(sym)
        if t and (datetime.now(pytz.utc) - t) < timedelta(minutes=10):
            continue  # compra reciente: sus órdenes de salida pueden estar aún pendientes
        avisadas.add(sym)
        corto = "short" in val(p.side)
        qty = abs(float(p.qty))
        if not (AUTO_PROTEGER and AUTO_TRADING) or qty != int(qty):
            alerta(f"⚠️ AURA: {sym} está SIN stop loss. Protégela manualmente en Alpaca.")
            continue
        try:
            precio = float(p.current_price)
            sl, tp = SL_DEFECTO / 100, TP_DEFECTO / 100
            stop = precio * (1 + sl) if corto else precio * (1 - sl)
            objetivo = precio * (1 - tp) if corto else precio * (1 + tp)
            trading.submit_order(LimitOrderRequest(
                symbol=sym, qty=int(qty), side=OrderSide.BUY if corto else OrderSide.SELL,
                time_in_force=TimeInForce.GTC, limit_price=round(objetivo, 2),
                order_class=OrderClass.OCO,
                take_profit=TakeProfitRequest(limit_price=round(objetivo, 2)),
                stop_loss=StopLossRequest(stop_price=round(stop, 2))))
            protegidas.add(sym)
            alerta(f"🛡️ AURA: {sym} no tenía protección. Puse stop ${stop:.2f} y objetivo ${objetivo:.2f}.")
        except Exception as e:
            alerta(f"⚠️ AURA: {sym} está SIN stop loss y no pude protegerla ({type(e).__name__}). "
                   f"Hazlo manualmente en Alpaca.")


def refrescar_grafico():
    """Historial de equity del último mes, como máximo cada 10 minutos."""
    if time.time() - _chart["t"] < 600:
        return
    _chart["t"] = time.time()
    try:
        h = trading.get_portfolio_history(GetPortfolioHistoryRequest(period="1M", timeframe="1D"))
        pares = [(float(v), t) for v, t in zip(h.equity, h.timestamp) if v is not None]
        vals = [v for v, _ in pares]
        fechas = [datetime.fromtimestamp(t, NY).strftime("%d/%m") for _, t in pares]
        estado["chart"] = svg_linea(vals, fechas)
        if len(vals) >= 2 and vals[0]:
            d = vals[-1] - vals[0]
            estado["cambio"] = f"{d:+,.2f} USD ({d / vals[0] * 100:+.2f}%) desde {fechas[0]}"
    except Exception as e:
        log(f"Error historial equity: {e}")


def registrar_fallo():
    estado["conectado"] = False
    estado["ultimo_error"] = "No se pudo consultar Alpaca (reintentando)"
    fallos["n"] += 1
    if fallos["n"] >= 3 and not fallos["avisado"]:
        fallos["avisado"] = True
        telegram("⚠️ AURA: no logra comunicarse con Alpaca (3 intentos seguidos).")


def registrar_exito():
    if fallos["avisado"]:
        telegram("✅ AURA: la conexión con Alpaca se restableció.")
    fallos["n"], fallos["avisado"] = 0, False


C_COMPRA, C_VENTA, C_NEUTRO, C_AVISO = "#0B7A7A", "#6A2BA8", "#5B6B85", "#9A6200"


def escanear():
    cuenta = trading.get_account()
    estado["equity"], estado["cash"] = float(cuenta.equity), float(cuenta.cash)
    estado["bp"] = float(cuenta.buying_power)
    estado["day_pl"] = estado["equity"] - float(cuenta.last_equity)

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
    estado["pos_det"] = [{"symbol": p.symbol, "side": "Corto" if "short" in val(p.side) else "Largo",
                          "qty": p.qty, "entry": float(p.avg_entry_price),
                          "price": float(p.current_price), "pl": float(p.unrealized_pl),
                          "plpc": float(p.unrealized_plpc) * 100} for p in posiciones.values()]
    estado["conectado"], estado["ultimo_error"] = True, None
    registrar_exito()
    refrescar_grafico()

    abierto, mins_cierre, mins_apertura = mercado_info()
    estado["mercado_abierto"] = abierto
    if abierto:
        proteger_posiciones(list(posiciones.values()), con_orden)
    pausa = abierto and limite_diario_excedido(cuenta)
    ocupados = set(posiciones) | con_orden
    n_pos, compras = len(ocupados), 0
    puede_operar = abierto and AUTO_TRADING and not pausa and mins_apertura >= MIN_SIN_OPERAR_APERTURA

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
        precio, rsi, tend = a["precio"], a["rsi"], a["tendencia_ok"]
        en_pos = sym in posiciones
        if rsi < RSI_BUY and tend:
            dec, color, action = ("COMPRAR FUERTE" if rsi < 30 else "COMPRAR"), C_COMPRA, "BUY"
        elif rsi < RSI_BUY:
            dec, color, action = ("NO COMPRA: tendencia bajista" if tend is False
                                  else "NO COMPRA: sin datos de tendencia"), C_AVISO, "HOLD"
        elif rsi > RSI_SELL and en_pos:
            dec, color, action = ("VENDER FUERTE" if rsi > 75 else "VENDER"), C_VENTA, "SELL"
        elif rsi > RSI_SELL:
            dec, color, action = "SOBRECOMPRA", C_VENTA, "HOLD"
        else:
            dec, color, action = "NEUTRAL", C_NEUTRO, "HOLD"
        detalles[sym] = {"precio": precio, "rsi": round(rsi, 1), "decision": dec, "color": color,
                         "action": action, "tendencia_ok": tend,
                         "volatilidad_pct": round(a["atr_pct"], 2) if a["atr_pct"] is not None else None}
        logs.append(f"{sym} ${precio:.2f} RSI {rsi:.1f} · "
                    f"{'tendencia alcista' if tend else 'tendencia bajista' if tend is False else 'sin datos de tendencia'}"
                    f" -> {dec}" + ("" if abierto else " [Mercado cerrado]"))
        if not puede_operar:
            continue

        if action == "BUY":
            if (en_pos or sym in ocupados or en_cooldown(sym) or n_pos >= MAX_POSICIONES
                    or mins_cierre < MIN_PARA_CIERRE or compras >= MAX_COMPRAS_POR_ESCANEO):
                continue
            msg = ejecutar_compra(sym, a)
            if msg:
                logs.append(msg)
                compras += 1
                n_pos += 1
                ocupados.add(sym)
        elif action == "SELL":
            p = posiciones[sym]
            if "short" in val(p.side):
                continue
            if float(p.unrealized_pl) > 0:                # solo se vende con ganancia; el stop cubre las pérdidas
                msg = ejecutar_venta(sym, p, rsi, precio)
                if msg:
                    logs.append(msg)

    estado["detalles"] = detalles
    estado["log"] = logs[:25]
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
        log(f"AURA V7.0 conectada ({'PAPER' if IS_PAPER else 'REAL'}): equity ${estado['equity']:.2f}")
        telegram(f"🚀 AURA V7.0 iniciada ({'PAPER' if IS_PAPER else 'REAL'}). Vigilando {', '.join(SYMBOLS)}.")
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
    <span class="pill {{ 'ok' if sheets else 'warn' }}">● Sheets {{ 'activo' if sheets else 'sin configurar' }}</span>
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
  {% for s in symbols %}{% set d = detalles.get(s) %}
  <div class="card">
    <h3>{{ s }}{% if s in held %} · en posición{% endif %}</h3>
    {% if d %}
      <p class="big">{{ d.rsi }}</p>
      <div class="rsi"><span class="dot" style="left:{{ d.rsi }}%"></span></div>
      <div class="sub" style="color:{{ d.color }};font-weight:600">{{ d.decision }}</div>
      <div class="sub">Tendencia {{ 'alcista' if d.tendencia_ok else ('bajista' if d.tendencia_ok is false else 'sin datos') }}{% if d.volatilidad_pct %} · volatilidad {{ d.volatilidad_pct }}%{% endif %}</div>
    {% else %}<p class="sub">Esperando datos…</p>{% endif %}
  </div>
  {% endfor %}
</div>

<h2 style="margin-top:8px">Registro</h2>
<div class="logs">{% for l in logs %}<div>{{ l }}</div>{% else %}<div>Sin eventos todavía.</div>{% endfor %}</div>

<footer>Stop y objetivo según la volatilidad de cada activo (relación 1:{{ rr }}) · hasta {{ eq_pct }}% del equity por operación · pausa si el día pierde {{ max_loss }}%. Venta por RSI &gt; {{ rsi_sell }} solo con ganancia. Esto no es asesoría financiera.</footer>
</div>
<script>setTimeout(()=>location.reload(), 30000)</script>
</body></html>
"""



@app.route("/")
def home():
    require_token()
    return render_template_string(
        DASHBOARD_HTML,
        connected=estado["conectado"], paper=IS_PAPER, running=estado["running"], trading=AUTO_TRADING,
        last_scan=estado["ultimo_analisis"] if estado["ultimo_analisis"] != "Nunca" else None,
        heartbeat=estado["ultimo_heartbeat"], last_error=estado["ultimo_error"],
        equity=estado["equity"], cash=estado["cash"], bp=estado["bp"], day_pl=estado["day_pl"],
        positions=estado["pos_det"], held={p["symbol"] for p in estado["pos_det"]},
        max_pos=MAX_POSICIONES, symbols=SYMBOLS, detalles=dict(estado["detalles"]),
        rsi_buy=RSI_BUY, rsi_sell=RSI_SELL, eq_pct=round(PCT_POR_TRADE * 100), rr=RR,
        max_loss=MAX_PERDIDA_DIA, pausa=estado["pausa_riesgo"],
        telegram=bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID), sheets=sheets_activo(),
        chart=estado["chart"], cambio=estado["cambio"],
        logs=list(reversed(list(logs_hist)))[:12],
    )


@app.route("/api")
def api():
    require_token()
    return jsonify({
        "mensaje": "🧠 AURA V7.0 VIVE",
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
        "version": "V7.0",
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
    return "OK AURA V7.0", 200


# Arranque compatible con gunicorn (con --workers 1 solo se crea un bot)
if AUTO_START:
    iniciar_bot()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"🚀 AURA V7.0 puerto {port} Keys:{'OK' if API_KEY else 'FALTAN'} Auto:{AUTO_TRADING}", flush=True)
    app.run(host="0.0.0.0", port=port)
