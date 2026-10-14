"""
AURA V10 - EDICION CONFIANZA (corregida)
Visual completa + Operativa + Auto-Flat 10 min antes del cierre (3:50pm NY normal)
Creado para Yesid - Ingreso para su familia

requirements.txt:
    streamlit
    alpaca-py
    requests
    tzdata
    streamlit-autorefresh
"""
import os, base64, requests, time, threading
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import streamlit as st
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

# --- CONFIG ---
st.set_page_config(page_title="AURA SYNAPSE V10", layout="wide", page_icon="🕯️")

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

WATCHLIST = ["QQQ", "SPY", "NVDA", "TSLA"]
MAX_POSITIONS = 4
MAX_SPREAD_PCT = 0.10
ENTRY_AFTER = dtime(9, 35)     # hora NY: no entrar antes
NO_ENTRY_LAST_MIN = 20         # no abrir nuevas entradas en los últimos 20 min
FLAT_LAST_MIN = 10             # cerrar todo 10 min antes del cierre (15:50 normal, 12:50 en medio día)
CYCLE_SECONDS = 15

ET = ZoneInfo("America/New_York")   # maneja horario de verano/invierno solo
BOG = ZoneInfo("America/Bogota")

trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=ALPACA_PAPER) if ALPACA_API_KEY else None
data_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY) if ALPACA_API_KEY else None


# --- UTILIDADES ---
def get_logo():
    for p in ["Aura.png", "aura.png", "logo.png", "/mnt/data/Aura.png"]:
        if os.path.exists(p):
            with open(p, "rb") as f:
                return base64.b64encode(f.read()).decode()
    return ""


def send_tg(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": text},
            timeout=4,
        )
    except Exception as e:
        print("Telegram error:", e)


def get_quote(sym):
    if not data_client:
        return None
    try:
        q = data_client.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=sym))[sym]
        bid, ask = float(q.bid_price or 0), float(q.ask_price or 0)
        if bid <= 0 or ask <= 0 or ask < bid:   # cotización inválida (fuera de horario, etc.)
            return None
        return bid, ask
    except Exception as e:
        print(f"Quote {sym} error:", e)
        return None


def get_account():
    if not trading_client:
        return None
    try:
        return trading_client.get_account()
    except Exception as e:
        print("Account error:", e)
        return None


def get_positions():
    if not trading_client:
        return []
    try:
        return trading_client.get_all_positions()
    except Exception as e:
        print("Positions error:", e)
        return []


def get_clock():
    if not trading_client:
        return None
    try:
        return trading_client.get_clock()
    except Exception as e:
        print("Clock error:", e)
        return None


# --- WORKER EN SEGUNDO PLANO ---
# Corre aunque no haya nadie mirando la página. Streamlit solo ejecuta el script
# cuando alguien interactúa, por eso el auto-flat NO puede vivir en el cuerpo de la página.
@st.cache_resource
def get_state():
    return {
        "lock": threading.Lock(),
        "auto_entries": True,     # el botón de cierre manual lo pone en False
        "last_flat_date": None,   # fecha NY del último auto-flat
        "log": [],
        "started": False,
    }


def log(state, msg):
    with state["lock"]:
        state["log"].append(f"{datetime.now(ET).strftime('%H:%M:%S')} {msg}")
        state["log"] = state["log"][-30:]


def run_cycle(state):
    if not trading_client:
        return
    clock = get_clock()
    if not clock or not clock.is_open:      # cubre fines de semana y feriados
        return

    now = clock.timestamp.astimezone(ET)
    mins_to_close = (clock.next_close - clock.timestamp).total_seconds() / 60
    today = now.date()

    # AUTO-FLAT
    if mins_to_close <= FLAT_LAST_MIN:
        if state["last_flat_date"] != today:
            positions = get_positions()
            trading_client.close_all_positions(cancel_orders=True)
            state["last_flat_date"] = today
            msg = f"AUTO-FLAT {now.strftime('%H:%M')} NY - {len(positions)} posiciones cerradas. Sin riesgo overnight."
            log(state, msg)
            send_tg("🚨 " + msg)
        return

    # ENTRADAS
    if not state["auto_entries"]:
        return
    if now.time() < ENTRY_AFTER or mins_to_close <= NO_ENTRY_LAST_MIN:
        return

    positions = get_positions()
    open_orders = trading_client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
    busy = {p.symbol for p in positions} | {o.symbol for o in open_orders}   # evita duplicados por órdenes pendientes
    slots = MAX_POSITIONS - len(busy)

    for sym in WATCHLIST:
        if slots <= 0:
            break
        if sym in busy:
            continue
        quote = get_quote(sym)
        if not quote:
            continue
        bid, ask = quote
        spread_pct = (ask - bid) / bid * 100
        if spread_pct < MAX_SPREAD_PCT:
            try:
                trading_client.submit_order(
                    MarketOrderRequest(symbol=sym, qty=1, side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
                )
                slots -= 1
                msg = f"ENTRY {sym} BUY 1 @ ~${ask:.2f} spread {spread_pct:.3f}%"
                log(state, msg)
                send_tg("🟢 AURA " + msg)
            except Exception as e:
                log(state, f"Error entrada {sym}: {e}")


def worker_loop(state):
    while True:
        try:
            run_cycle(state)
        except Exception as e:
            log(state, f"Error ciclo: {e}")
        time.sleep(CYCLE_SECONDS)


state = get_state()
if not state["started"] and trading_client:
    state["started"] = True
    threading.Thread(target=worker_loop, args=(state,), daemon=True).start()
    log(state, "Worker iniciado")
    send_tg(f"🕯️ AURA worker iniciado\n{datetime.now(ET).strftime('%H:%M NY')} / {datetime.now(BOG).strftime('%H:%M BOG')}")

# Refresca la vista cada 10 s (solo visual; la operativa corre en el worker)
if st_autorefresh:
    st_autorefresh(interval=10_000, key="aura_refresh")

# --- TIEMPO ---
now_et = datetime.now(ET)
now_bog = datetime.now(BOG)
clock = get_clock()
is_market = bool(clock and clock.is_open)

# --- UI HEADER ---
logo = get_logo()
logo_html = f"<img src='data:image/png;base64,{logo}' style='width:40px; height:40px;'/>" if logo else "🕯️"
st.markdown(f"""
<div style="background:#0F1219; border:1px solid #1E2E44; border-radius:16px; padding:14px 18px; display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
<div style="display:flex; gap:14px; align-items:center;">
<div style="background:white; border-radius:12px; padding:4px; width:48px; height:48px; display:flex; align-items:center; justify-content:center;">
{logo_html}
</div>
<div>
<div style="font-size:18px; font-weight:800; color:white; letter-spacing:0.5px;">AURA SYNAPSE V10 <span style="color:#2EFB9B; font-size:12px;">EDICION CONFIANZA</span></div>
<div style="font-size:11px; color:#8B9BB4;">VECTOR FLOW • AUTO-FLAT 10 MIN ANTES DEL CIERRE • PARA LA FAMILIA BELTRAN</div>
</div>
</div>
<div style="display:flex; gap:8px; align-items:center;">
<div style="background:{'#0F2D25' if is_market else '#1A1E2A'}; border:1px solid {'#2EFB9B' if is_market else '#2A3447'}; color:{'#2EFB9B' if is_market else '#8B9BB4'}; padding:6px 14px; border-radius:20px; font-size:11px; font-weight:700;">{"● MARKET OPEN" if is_market else "● MARKET CLOSED"}</div>
<div style="background:#101826; border:1px solid #1E2E44; color:white; padding:6px 12px; border-radius:10px; font-size:11px;">NY {now_et.strftime('%H:%M:%S')}</div>
</div>
</div>
""", unsafe_allow_html=True)

# --- METRICAS CUENTA ---
acc = get_account()
positions = get_positions()

c1, c2, c3, c4 = st.columns(4)
if acc:
    eq = float(acc.equity)
    pl_day = eq - float(acc.last_equity)
    buying = float(acc.buying_power)
    c1.metric("EQUITY", f"${eq:,.2f}", f"{pl_day:+.2f} hoy")
    c2.metric("BUYING POWER", f"${buying:,.2f}")
    c3.metric("POSICIONES", len(positions))
    c4.metric("HORA BOG", now_bog.strftime('%I:%M:%S %p'))
else:
    c1.warning("Conecta ALPACA_API_KEY y ALPACA_SECRET_KEY en Render Environment")

st.divider()

# --- ACCIONES ---
colA, colB, colC = st.columns([1.5, 1, 1])
with colA:
    if st.button("🔴 CERRAR TODO - FLAT NOW", type="primary", use_container_width=True):
        if trading_client:
            try:
                trading_client.close_all_positions(cancel_orders=True)
                state["auto_entries"] = False   # evita que el bot vuelva a entrar enseguida
                log(state, "FLAT MANUAL - entradas automáticas pausadas")
                send_tg(f"🔴 FLAT MANUAL EJECUTADO\n{now_et.strftime('%H:%M NY')} / {now_bog.strftime('%H:%M BOG')}\nTodas las posiciones cerradas. Entradas automáticas pausadas.")
                st.success("✅ Orden de cierre enviada. Entradas automáticas pausadas.")
            except Exception as e:
                st.error(f"Error: {e}")
        else:
            st.error("Sin conexión Alpaca")

with colB:
    if st.button("🟢 TEST ENTRADA QQQ 1 SHARE", use_container_width=True):
        if trading_client:
            try:
                trading_client.submit_order(MarketOrderRequest(symbol="QQQ", qty=1, side=OrderSide.BUY, time_in_force=TimeInForce.DAY))
                send_tg("🟢 TEST BUY QQQ 1 enviado")
                st.success("Orden QQQ enviada" + ("" if is_market else " (mercado cerrado: queda en cola)"))
            except Exception as e:
                st.error(str(e))

with colC:
    if state["auto_entries"]:
        st.markdown("<div style='background:#0F2D25; border:1px solid #2EFB9B; border-radius:10px; padding:10px; text-align:center;'><div style='font-size:10px; color:#2EFB9B;'>ENTRADAS AUTO: ACTIVAS</div><div style='font-weight:800; color:white;'>AUTO-FLAT ON</div></div>", unsafe_allow_html=True)
    else:
        st.markdown("<div style='background:#2D1F0F; border:1px solid #FBB02E; border-radius:10px; padding:10px; text-align:center;'><div style='font-size:10px; color:#FBB02E;'>ENTRADAS AUTO: PAUSADAS</div></div>", unsafe_allow_html=True)
        if st.button("▶ Reanudar entradas", use_container_width=True):
            state["auto_entries"] = True
            log(state, "Entradas automáticas reanudadas")
            st.rerun()

# --- AVISO PREVIO AL AUTO-FLAT ---
if clock and clock.is_open:
    mins_left = (clock.next_close - clock.timestamp).total_seconds() / 60
    if FLAT_LAST_MIN < mins_left <= FLAT_LAST_MIN + 5:
        st.warning(f"⚠️ AUTO-FLAT en {mins_left - FLAT_LAST_MIN:.0f} min. Prepara cierre.")

# --- TABLA VISIBILIDAD TOTAL ---
st.markdown("### 📊 Posiciones Abiertas - Visibilidad Total")
if positions:
    data = []
    total_pl = 0
    for p in positions:
        pl = float(p.unrealized_pl)
        total_pl += pl
        side = p.side.value if hasattr(p.side, "value") else str(p.side)
        data.append({
            "SYMBOL": p.symbol,
            "QTY": float(p.qty),
            "SIDE": side.upper(),
            "ENTRY $": round(float(p.avg_entry_price), 2),
            "MARKET $": round(float(p.current_price), 2) if p.current_price else 0,
            "P/L $": round(pl, 2),
            "P/L %": f"{float(p.unrealized_plpc) * 100:.2f}%",
            "STATUS": "🟢 LOCK" if pl > 0 else "🟡 ACTIVE" if pl > -20 else "🔴 WATCH",
        })
    st.dataframe(data, use_container_width=True, hide_index=True)
    st.metric("P/L TOTAL FLOTANTE", f"${total_pl:+.2f}", delta=f"{len(positions)} posiciones")
else:
    st.info("Sin posiciones abiertas - AURA esperando señal VECTOR FLOW 9:35am NY hasta 20 min antes del cierre")

# --- ESTADO VECTOR FLOW ---
st.markdown("### ⚡ Vector Flow - Entradas Automáticas")
st.caption("Opera desde 9:35am NY hasta 20 min antes del cierre si spread < 0.10%. Solo 1 posición por símbolo, máx. 4. Sin overnight.")
if is_market:
    st.success(f"AURA escaneando... {now_et.strftime('%H:%M:%S')} - {len(positions)}/{MAX_POSITIONS} posiciones")
else:
    st.caption("Mercado cerrado. El bot espera la próxima apertura (9:35am NY).")

with st.expander("📜 Registro del bot"):
    with state["lock"]:
        entries = list(state["log"])
    st.code("\n".join(reversed(entries)) if entries else "Sin eventos todavía")

st.divider()
st.markdown("<div style='text-align:center; font-size:11px; color:#5A6B84;'>AURA SYNAPSE V10 • Edición Confianza • Construido con corazón para la familia Beltran • No overnight risk</div>", unsafe_allow_html=True)
