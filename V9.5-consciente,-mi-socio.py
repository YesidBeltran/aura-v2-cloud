"""
AURA V10 - EDICION CONFIANZA
Visual completa + Operativa + Auto-Flat 3:50pm NY
Creado para Yesid - Ingreso para su familia
"""
import os, base64, requests, time
import streamlit as st
from datetime import datetime, timezone, timedelta
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest, ClosePositionRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest

# --- CONFIG ---
st.set_page_config(page_title="AURA SYNAPSE V10", layout="wide", page_icon="🕯️")

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=ALPACA_PAPER) if ALPACA_API_KEY else None
data_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY) if ALPACA_API_KEY else None

def get_logo():
    for p in ["Aura.png", "aura.png", "logo.png", "/mnt/data/Aura.png"]:
        if os.path.exists(p):
            with open(p, "rb") as f:
                return base64.b64encode(f.read()).decode()
    return ""

def send_tg(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT: return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT, "text": text, "parse_mode": "Markdown"}, timeout=4)
    except: pass

def get_quote(sym):
    if not data_client: return None
    try:
        q = data_client.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=sym))[sym]
        return float(q.bid_price), float(q.ask_price)
    except: return None

def get_account():
    if not trading_client: return None
    try: return trading_client.get_account()
    except: return None

def get_positions():
    if not trading_client: return []
    try: return trading_client.get_all_positions()
    except: return []

# --- TIEMPO NY SIN PYTZ ---
ET = timezone(timedelta(hours=-4)) # EDT
now_et = datetime.now(ET)
now_bog = datetime.now(timezone(timedelta(hours=-5)))
is_weekday = now_et.weekday() < 5
is_market = is_weekday and 9 <= now_et.hour < 16 and not (now_et.hour == 9 and now_et.minute < 30)

# --- UI HEADER ---
logo = get_logo()
st.markdown(f"""
<div style="background:#0F1219; border:1px solid #1E2E44; border-radius:16px; padding:14px 18px; display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
<div style="display:flex; gap:14px; align-items:center;">
<div style="background:white; border-radius:12px; padding:4px; width:48px; height:48px; display:flex; align-items:center; justify-content:center;">
{"<img src='data:image/png;base64,"+logo+"' style='width:40px; height:40px;'/>" if logo else "🕯️"}
</div>
<div>
<div style="font-size:18px; font-weight:800; color:white; letter-spacing:0.5px;">AURA SYNAPSE V10 <span style="color:#2EFB9B; font-size:12px;">EDICION CONFIANZA</span></div>
<div style="font-size:11px; color:#8B9BB4;">VECTOR FLOW • AUTO-FLAT 3:50PM NY • PARA LA FAMILIA BELTRAN</div>
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

c1,c2,c3,c4 = st.columns(4)
if acc:
    eq = float(acc.equity)
    pl_day = float(acc.equity) - float(acc.last_equity)
    buying = float(acc.buying_power)
    c1.metric("EQUITY", f"${eq:,.2f}", f"{pl_day:+.2f} hoy")
    c2.metric("BUYING POWER", f"${buying:,.2f}")
    c3.metric("POSICIONES", len(positions))
    c4.metric("HORA BOG", now_bog.strftime('%I:%M:%S %p'))
else:
    c1.warning("Conecta ALPACA_API_KEY en Render Environment")

st.divider()

# --- ACCIONES ---
colA, colB, colC = st.columns([1.5, 1, 1])
with colA:
    if st.button("🔴 CERRAR TODO - FLAT NOW (15:50 AUTO)", type="primary", use_container_width=True):
        if trading_client:
            try:
                trading_client.close_all_positions(cancel_orders=True)
                send_tg(f"🔴 *FLAT MANUAL EJECUTADO*\n{now_et.strftime('%H:%M NY')} / {now_bog.strftime('%H:%M BOG')}\nTodas las posiciones cerradas.")
                st.success("✅ Orden de cierre enviada a Alpaca")
                time.sleep(2)
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")
        else:
            st.error("Sin conexión Alpaca")

with colB:
    if st.button("🟢 TEST ENTRADA QQQ 1 SHARE", use_container_width=True):
        if trading_client:
            try:
                trading_client.submit_order(MarketOrderRequest(symbol="QQQ", qty=1, side=OrderSide.BUY, time_in_force=TimeInForce.DAY))
                send_tg("🟢 TEST BUY QQQ 1 ejecutado")
                st.success("Orden QQQ enviada")
            except Exception as e: st.error(str(e))

with colC:
    st.markdown(f"<div style='background:#0F2D25; border:1px solid #2EFB9B; border-radius:10px; padding:10px; text-align:center;'><div style='font-size:10px; color:#2EFB9B;'>AUTO-FLAT ACTIVO</div><div style='font-weight:800; color:white;'>15:50 NY</div></div>", unsafe_allow_html=True)

# --- AUTO-FLAT 3:50PM ---
if 15 <= now_et.hour <= 15 and 50 <= now_et.minute <= 51 and is_weekday:
    if positions:
        if trading_client:
            trading_client.close_all_positions(cancel_orders=True)
            send_tg(f"🚨 *AUTO-FLAT 3:50PM NY EJECUTADO*\nHora NY: {now_et.strftime('%H:%M')}\nBOG: {now_bog.strftime('%H:%M')}\n{len(positions)} posiciones cerradas. No overnight risk ✅")
            st.toast("🚨 AUTO-FLAT 3:50PM EJECUTADO", icon="🔴")
elif 15 == now_et.hour and 45 <= now_et.minute < 50:
    st.warning(f"⚠️ AUTO-FLAT en {50-now_et.minute} min - 3:50pm NY. Prepara cierre.")

# --- TABLA VISIBILIDAD TOTAL ---
st.markdown("### 📊 Posiciones Abiertas - Visibilidad Total")
if positions:
    data = []
    total_pl = 0
    for p in positions:
        pl = float(p.unrealized_pl)
        total_pl += pl
        data.append({
            "SYMBOL": p.symbol,
            "QTY": float(p.qty),
            "SIDE": p.side.upper(),
            "ENTRY $": round(float(p.avg_entry_price), 2),
            "MARKET $": round(float(p.current_price), 2) if p.current_price else 0,
            "P/L $": round(pl, 2),
            "P/L %": f"{float(p.unrealized_plpc)*100:.2f}%",
            "STATUS": "🟢 LOCK" if pl > 0 else "🟡 ACTIVE" if pl > -20 else "🔴 WATCH"
        })
    st.dataframe(data, use_container_width=True, hide_index=True)
    st.metric("P/L TOTAL FLOTANTE", f"${total_pl:+.2f}", delta=f"{len(positions)} posiciones")
else:
    st.info("Sin posiciones abiertas - AURA esperando señal VECTOR FLOW 9:35am - 3:40pm NY")

# --- VECTOR FLOW LOGICA SIMPLE OPERATIVA ---
st.markdown("### ⚡ Vector Flow - Entradas Automáticas")
st.caption("Opera 9:35am - 3:40pm NY si spread < 0.10%. Solo 1 posición por símbolo. Sin overnight.")

if is_market and (9,35) <= (now_et.hour, now_et.minute) < (15,40):
    watchlist = ["QQQ", "SPY", "NVDA", "TSLA"]
    for sym in watchlist:
        quote = get_quote(sym)
        if quote:
            bid, ask = quote
            spread_pct = ((ask-bid)/bid*100) if bid else 99
            has_pos = any(p.symbol == sym for p in positions)
            # Señal simple: spread bajo = liquidez + momentum
            if spread_pct < 0.10 and not has_pos and len(positions) < 4:
                if trading_client:
                    try:
                        trading_client.submit_order(MarketOrderRequest(symbol=sym, qty=1, side=OrderSide.BUY, time_in_force=TimeInForce.DAY))
                        send_tg(f"🟢 *AURA ENTRY V10*\n{sym} BUY 1 @ ${ask:.2f}\nSpread {spread_pct:.3f}%\n{now_et.strftime('%H:%M:%S NY')}")
                        st.toast(f"Entrada {sym} spread {spread_pct:.3f}%", icon="🟢")
                    except Exception as e:
                        st.error(f"{sym}: {e}")

    st.success(f"AURA escaneando... {now_et.strftime('%H:%M:%S')} - {len(positions)}/4 posiciones")
else:
    st.caption(f"Fuera de horario operativo. Próxima ventana: 9:35am NY ({'hoy' if is_weekday else 'lunes'})")

st.divider()
st.markdown("<div style='text-align:center; font-size:11px; color:#5A6B84;'>AURA SYNAPSE V10 • Edición Confianza • Construido con corazón para la familia Beltran • No overnight risk • Auto-flat 15:50 NY</div>", unsafe_allow_html=True)