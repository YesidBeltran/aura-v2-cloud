"""
AURA SYNAPSE V9.6 AUTO-FLAT — CONECTADA A ALPACA REAL
Lee posiciones reales + FLAT ALL ejecuta de verdad
"""
import os, base64, requests, streamlit as st
from datetime import datetime, time as dtime, timezone, timedelta
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

# === CONEXIÓN ALPACA ===
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"

if ALPACA_API_KEY and ALPACA_SECRET_KEY:
    trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=ALPACA_PAPER)
else:
    trading_client = None
    st.error("⚠️ Faltan ALPACA_API_KEY y ALPACA_SECRET_KEY en Environment")

def get_logo_b64():
    for path in ["Aura.png", "aura.png"]:
        if os.path.exists(path):
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode()
    return ""

def send_telegram(msg):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id: return
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", 
                     json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"})
    except: pass

def get_posiciones_reales():
    if not trading_client: return []
    try:
        positions = trading_client.get_all_positions()
        return [{
            "SYMBOL": p.symbol,
            "QTY": float(p.qty),
            "ENTRY": round(float(p.avg_entry_price), 2),
            "P/L": round(float(p.unrealized_pl), 2),
            "TRAIL": f"{round(float(p.unrealized_plpc)*100, 2)}%",
            "STATUS": "LOCK" if float(p.unrealized_pl) > 0 else "ACTIVE"
        } for p in positions]
    except: return []

def cerrar_todas_posiciones_real():
    if not trading_client:
        st.error("No hay conexión a Alpaca")
        return False
    try:
        trading_client.close_all_positions(cancel_orders=True)
        msg = f"🚨 *AUTO-FLAT EJECUTADO*\n\nTodas las posiciones cerradas en Alpaca\n\n{datetime.now().strftime('%H:%M:%S')} BOG"
        send_telegram(msg)
        return True
    except Exception as e:
        st.error(f"Error cerrando: {e}")
        return False

st.set_page_config(page_title="AURA V9.6 REAL", layout="wide")
logo_b64 = get_logo_b64()
logo_html = f'<img src="data:image/png;base64,{logo_b64}" style="width:44px;height:44px;background:white;border-radius:10px;">' if logo_b64 else '🕯️'

ny_tz = timezone(timedelta(hours=-4))
now_ny = datetime.now(ny_tz).time()
market_open = dtime(9, 30) <= now_ny < dtime(16, 0) and datetime.now(ny_tz).weekday() < 5

st.markdown(f"""
<div style="display:flex; justify-content:space-between; background:#11161F; padding:10px; border-radius:12px; margin-bottom:12px;">
  <div style="display:flex; gap:10px; align-items:center;">
    <div style="background:white; border-radius:10px; padding:3px;">{logo_html}</div>
    <div><b>AURA V9.6 — AUTO-FLAT 3:50PM NY — REAL</b><br><span style="font-size:10px;">CONECTADA A ALPACA {'PAPER' if ALPACA_PAPER else 'LIVE'}</span></div>
  </div>
  <span style="background:{'#0F2D25' if market_open else '#101826'}; color:{'#2EFB9B' if market_open else '#E0E6EB'}; padding:6px 12px; border-radius:20px;">{'MARKET OPEN' if market_open else 'MARKET CLOSED'}</span>
</div>
""", unsafe_allow_html=True)

col1,col2,col3=st.columns([2,1,1])
with col1:
    if st.button("🔴 FLAT ALL — CERRAR TODO EN ALPACA YA", type="primary"):
        if cerrar_todas_posiciones_real():
            st.success("✅ FLAT ALL ejecutado en Alpaca — Posiciones cerradas")
            st.rerun()

with col2:
    st.markdown('<div style="background:#0F2D25; border:1px solid #2EFB9B; padding:8px; border-radius:12px; text-align:center; font-size:11px;"><b>AUTO-FLAT ACTIVO</b><br>15:50 NY / 14:50 BOG</div>', unsafe_allow_html=True)
    
with col3:
    st.markdown(f'<div style="background:#101826; border:1px solid #1E2E44; padding:8px; border-radius:12px; text-align:center; font-size:11px;"><b>Hora NY</b><br>{now_ny.strftime("%H:%M")}</div>', unsafe_allow_html=True)

# Auto-Flat 3:50pm NY
if dtime(15, 45) <= now_ny < dtime(15, 50):
    st.warning("⚠️ En 5 min AUTO-FLAT")
    send_telegram("⚠️ AUTO-FLAT en 5 min")

if dtime(15, 50) <= now_ny < dtime(15, 51):
    if cerrar_todas_posiciones_real():
        st.success("🚨 AUTO-FLAT 3:50PM EJECUTADO")

# Tabla con posiciones REALES de Alpaca
positions = get_posiciones_reales()
if positions:
    st.dataframe(positions, use_container_width=True, hide_index=True)
    total_pl = sum([p["P/L"] for p in positions])
    st.metric("P/L Total Abierto", f"${total_pl:+.2f}")
else:
    st.info("No hay posiciones abiertas en Alpaca" if trading_client else "Conecta tus API keys de Alpaca en Environment")

st.success("V9.6 REAL — CONECTADA A ALPACA — AUTO-FLAT 3:50PM NY")
