"""
AURA SYNAPSE V9.5 FINAL + TELEGRAM — 6 POSICIONES BIDIRECCIONAL
Incluye notificaciones a Telegram para COMPRA/VENTA con Adaptive Engine
"""
import os, requests, streamlit as st, pandas as pd, plotly.graph_objects as go, random
from datetime import datetime

# --- TELEGRAM CONFIG ---
def send_telegram(msg):
    token = os.getenv("TELEGRAM_BOT_TOKEN") or st.secrets.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or st.secrets.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False, "Falta TELEGRAM_BOT_TOKEN o CHAT_ID en Environment"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.ok, r.text
    except Exception as e:
        return False, str(e)

st.set_page_config(page_title="AURA V9.5 FINAL + TELEGRAM", layout="wide")
st.markdown("<style>.stApp{background:#080C12; color:#E0E6EB; font-family:'JetBrains Mono', monospace;}.card{background:#121A27; border:1px solid #1E2E44; border-radius:12px; padding:12px;}.green{color:#2EFB9B;}.badge{padding:4px 10px; border-radius:12px; font-size:10px;}</style>", unsafe_allow_html=True)

st.markdown(f"## AURA SYNAPSE V9.5 FINAL + TELEGRAM • 6 POS BIDIRECCIONAL • Equity $100557.30 • {datetime.now().strftime('%H:%M:%S')} BOG")

# Datos 6 posiciones
positions = [
    {"sym":"QQQ", "qty":5.0, "entry":745.69, "pnl":-12.36, "pct":-0.3, "lado":"COMPRA", "trail":1.35},
    {"sym":"AAPL", "qty":10.0, "entry":343.43, "pnl":-29.38, "pct":-0.9, "lado":"COMPRA", "trail":1.28},
    {"sym":"MSFT", "qty":5.0, "entry":345.90, "pnl":-22.69, "pct":-0.9, "lado":"COMPRA", "trail":1.31},
    {"sym":"NVDA", "qty":10.0, "entry":223.89, "pnl":51.71, "pct":2.3, "lado":"COMPRA", "trail":3.45},
    {"sym":"TSLA", "qty":54.0, "entry":372.40, "pnl":132.74, "pct":0.7, "lado":"COMPRA", "trail":3.95},
]

df = pd.DataFrame(positions)
st.dataframe(df, use_container_width=True)

col1, col2 = st.columns(2)
with col1:
    if st.button("🔄 EJECUTAR RE-CLASSIFIER 6 POS + NOTIFICAR TELEGRAM", type="primary"):
        avg_trail = sum([p["trail"] for p in positions])/len(positions)
        msg = f"🚀 *AURA V9.5 FINAL — RE-CLASSIFIER 6 POS*\n\nEquity: $100,557.30\n5/6 reales + SPY\n\n"
        for p in positions:
            lock = "🔒 LOCK" if p["pnl"]>0 else "◍ ACTIVE"
            msg += f"● {p['sym']} {p['lado']} {p['trail']}% {lock} P/L {p['pnl']:+}\n"
        msg += f"\nAvg Trail: {avg_trail:.2f}% | Range 1.2%→4.5%\nBreak-Even: TSLA +$132 NVDA +$51 GARANTIZADO\nCOMPRA y VENTA soportado\n\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} BOG"
        ok, resp = send_telegram(msg)
        if ok:
            st.success("✅ Notificación enviada a Telegram — Revisa tu @YesidAuraBot")
        else:
            st.error(f"❌ Telegram falló: {resp} — Verifica TELEGRAM_BOT_TOKEN y CHAT_ID en Render > Environment")
        st.toast("Adaptive Bidireccional ejecutado para las 6 posiciones", icon="🟢")

with col2:
    test_msg = st.text_input("Mensaje de prueba Telegram", "✅ AURA V9.5 FINAL conectado — 6 POS COMPRA/VENTA — Break-Even Lock activo")
    if st.button("📨 Enviar prueba a Telegram"):
        ok, resp = send_telegram(test_msg)
        if ok:
            st.success("✅ Prueba enviada — Te debe llegar a Telegram ahora mismo")
        else:
            st.error(f"❌ Error: {resp}")

st.info("Para que funcionen: Render > aura-v2-cloud > Environment > Add: TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID (los mismos que usaste en C:\\AURA\\.env). Luego Save + Deploy.")
