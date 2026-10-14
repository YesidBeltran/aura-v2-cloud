"""
AURA SYNAPSE V9.6 AUTO-FLAT — SIN PANDAS NI PYTZ
"""
import os, base64, requests, streamlit as st, random
from datetime import datetime, time as dtime, timezone, timedelta

def get_logo_b64():
    for path in ["Aura.png", "aura.png"]:
        if os.path.exists(path):
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode()
    return ""

def send_telegram(msg):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id: return False
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", 
                     json={"chat_id": chat_id, "text": msg})
        return True
    except: return False

def cerrar_todas_posiciones():
    send_telegram(f"🚨 *AUTO-FLAT 3:50PM NY*\nCerradas todas\n{datetime.now().strftime('%H:%M:%S')}")

st.set_page_config(page_title="AURA V9.6", layout="wide")
logo_b64 = get_logo_b64()
logo_html = f'<img src="data:image/png;base64,{logo_b64}" style="width:44px;height:44px;background:white;border-radius:10px;">' if logo_b64 else '🕯️'

ny_tz = timezone(timedelta(hours=-4))
now_ny = datetime.now(ny_tz).time()
market_open = dtime(9, 30) <= now_ny < dtime(16, 0) and datetime.now(ny_tz).weekday() < 5

st.markdown(f"""
<div style="display:flex; justify-content:space-between; background:#11161F; padding:10px; border-radius:12px;">
  <div style="display:flex; gap:10px; align-items:center;">
    <div style="background:white; border-radius:10px; padding:3px;">{logo_html}</div>
    <b>AURA V9.6 — AUTO-FLAT 3:50PM NY</b>
  </div>
  <span style="background:{'#0F2D25' if market_open else '#101826'}; color:{'#2EFB9B' if market_open else '#E0E6EB'}; padding:6px 12px; border-radius:20px;">{'MARKET OPEN' if market_open else 'MARKET CLOSED'}</span>
</div>
""", unsafe_allow_html=True)

if st.button("🔴 FLAT ALL — CERRAR TODO YA", type="primary"):
    cerrar_todas_posiciones()
    st.success("✅ Ejecutado")

if dtime(15, 45) <= now_ny < dtime(15, 50):
    st.warning("⚠️ En 5 min AUTO-FLAT 3:50pm")
    send_telegram("⚠️ AUTO-FLAT en 5 min")

if dtime(15, 50) <= now_ny < dtime(15, 51):
    cerrar_todas_posiciones()
    st.success("🚨 AUTO-FLAT EJECUTADO")

positions = [
    {"SYMBOL":"QQQ","QTY":5,"ENTRY":745.69,"P/L":-12.36,"TRAIL":"1.32%","STATUS":"ACTIVE"},
    {"SYMBOL":"AAPL","QTY":10,"ENTRY":343.43,"P/L":-29.38,"TRAIL":"1.29%","STATUS":"ACTIVE"},
    {"SYMBOL":"MSFT","QTY":5,"ENTRY":345.90,"P/L":-22.69,"TRAIL":"1.29%","STATUS":"ACTIVE"},
    {"SYMBOL":"NVDA","QTY":10,"ENTRY":223.89,"P/L":51.71,"TRAIL":"3.52%","STATUS":"LOCK"},
    {"SYMBOL":"TSLA","QTY":54,"ENTRY":372.40,"P/L":132.74,"TRAIL":"2.37%","STATUS":"LOCK"},
]

st.dataframe(positions, use_container_width=True, hide_index=True)
st.success("V9.6 AUTO-FLAT — SIN PANDAS — LISTO")
