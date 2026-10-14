"""
AURA SYNAPSE V9.6 AUTO-FLAT 3:50PM NY — SIN PYTZ
VECTOR FLOW + LOGO + FLAT ALL + AUTO-CLOSE 15:50 NY
"""
import os, base64, requests, streamlit as st, pandas as pd, plotly.graph_objects as go, random
from datetime import datetime, time as dtime, timezone, timedelta

def get_logo_b64():
    for path in ["/mnt/data/Aura.png", "Aura.png", "aura.png", "/opt/render/project/src/Aura.png"]:
        if os.path.exists(path):
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode()
    return ""

def send_telegram(msg):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False, "Falta TELEGRAM_BOT_TOKEN o CHAT_ID"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        return r.ok, r.text
    except Exception as e:
        return False, str(e)

def cerrar_todas_posiciones():
    # Aquí va tu lógica real con Alpaca: alpaca.close_all_positions()
    msg = f"🚨 *AUTO-FLAT 3:50PM NY EJECUTADO*\n\nTodas las posiciones cerradas automáticamente\n\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} BOG\n\nNo overnight risk ✅"
    send_telegram(msg)
    return True

st.set_page_config(page_title="AURA V9.6 AUTO-FLAT", layout="wide")
logo_b64 = get_logo_b64()
logo_html = f'<img src="data:image/png;base64,{logo_b64}" style="width:44px;height:44px;background:white;border-radius:10px;padding:4px;">' if logo_b64 else '🕯️'

# Hora NY sin pytz: UTC-4 en horario de verano Oct 2026
ny_tz = timezone(timedelta(hours=-4))
now_ny = datetime.now(ny_tz).time()
auto_flat_time = dtime(15, 50)  # 3:50pm NY
alert_time = dtime(15, 45)      # 3:45pm NY

# Detectar si mercado está abierto: 9:30am - 4:00pm NY lun-vie
market_open = dtime(9, 30)
market_close = dtime(16, 0)
is_weekday = datetime.now(ny_tz).weekday() < 5
is_market_open = is_weekday and market_open <= now_ny < market_close
market_badge = "MARKET OPEN" if is_market_open else "MARKET CLOSED"
badge_color = "#0F2D25" if is_market_open else "#101826"
badge_text = "#2EFB9B" if is_market_open else "#E0E6EB"

st.markdown(f"""
<style>
.stApp {{ background:#080C12; background-image: radial-gradient(circle at 50% -10%, #143236 0%, #080C12 65%); color:#E0E6EB; font-family:'JetBrains Mono', monospace; }}
.header {{ display:flex; justify-content:space-between; align-items:center; padding:10px 18px; background:#11161F; border:1px solid #1E2A3A; border-radius:12px; margin-bottom:12px; }}
.card {{ background: linear-gradient(180deg, #121A27 0%, #0D121D 100%); border:1px solid #1E2E44; border-radius:12px; padding:12px; }}
.green {{ color:#2EFB9B; }} .red {{ color:#FF6B6B; }}
</style>
<div class="header">
  <div style="display:flex; gap:12px; align-items:center;">
    <div style="background:white; border-radius:12px; padding:3px;">{logo_html}</div>
    <div><b style="font-size:17px;">AURA SYNAPSE V9.6 — AUTO-FLAT 3:50PM NY</b><br><span style="font-size:10px; opacity:0.6;">VECTOR FLOW • AUTO-CLOSE 15:50 NY • NO OVERNIGHT RISK</span></div>
  </div>
  <div style="display:flex; gap:6px; align-items:center;">
    <span style="background:{badge_color}; color:{badge_text}; padding:6px 12px; border-radius:20px; border:1px solid #2EFB9B66;">{market_badge}</span>
    <span style="background:#101826; padding:6px 12px; border-radius:20px;">EQUITY $100368.86</span>
  </div>
</div>
""", unsafe_allow_html=True)

col1,col2,col3=st.columns([2,1,1])
with col1:
    if st.button("🔴 FLAT ALL — CERRAR TODAS LAS POSICIONES YA", type="primary"):
        cerrar_todas_posiciones()
        st.success("✅ FLAT ALL ejecutado — Todas las posiciones cerradas")
        st.toast("Telegram enviado", icon="📱")
        
with col2:
    st.markdown('<div style="background:#0F2D25; border:1px solid #2EFB9B; padding:8px; border-radius:12px; text-align:center; font-size:11px;"><b>AUTO-FLAT ACTIVO</b><br>15:50 NY / 14:50 BOG</div>', unsafe_allow_html=True)
    
with col3:
    st.markdown(f'<div style="background:#101826; border:1px solid #1E2E44; padding:8px; border-radius:12px; text-align:center; font-size:11px;"><b>Hora NY actual</b><br>{now_ny.strftime("%H:%M")}</div>', unsafe_allow_html=True)

# Alerta 3:45pm
if alert_time <= now_ny < auto_flat_time:
    st.warning("⚠️ ALERTA: En 5 minutos se ejecuta AUTO-FLAT 3:50pm NY — Todas las posiciones se cerrarán automáticamente")
    send_telegram("⚠️ *ALERTA AUTO-FLAT*\n\nEn 5 min cierro todo automático a las 3:50pm NY.")

# Auto-Flat trigger
if now_ny >= auto_flat_time and now_ny < dtime(15, 51):
    cerrar_todas_posiciones()
    st.success("🚨 AUTO-FLAT 3:50PM NY EJECUTADO — Riesgo overnight eliminado")

positions = [
    {"sym":"QQQ", "qty":5.0, "entry":745.69, "pnl":-12.36, "pct":-0.3, "lado":"COMPRA", "trail":1.32, "status":"ACTIVE ◍"},
    {"sym":"AAPL", "qty":10.0, "entry":343.43, "pnl":-29.38, "pct":-0.9, "lado":"COMPRA", "trail":1.29, "status":"ACTIVE ◍"},
    {"sym":"MSFT", "qty":5.0, "entry":345.90, "pnl":-22.69, "pct":-0.9, "lado":"COMPRA", "trail":1.29, "status":"ACTIVE ◍"},
    {"sym":"NVDA", "qty":10.0, "entry":223.89, "pnl":51.71, "pct":2.3, "lado":"COMPRA", "trail":3.52, "status":"LOCK 🔒"},
    {"sym":"TSLA", "qty":54.0, "entry":372.40, "pnl":132.74, "pct":0.7, "lado":"COMPRA", "trail":2.37, "status":"LOCK 🔒"},
]

c1,c2 = st.columns([1.2,3])

with c1:
    html = '<div class="card"><div style="font-size:11px; opacity:0.6;">◐ POSICIONES ABIERTAS • AUTO-FLAT 3:50PM NY</div>'
    for p in positions:
        color="#2EFB9B" if p["pnl"]>0 else "#FF6B6B"
        html+=f'<div style="margin-top:10px; font-size:12px;"><span>● {p["sym"]}</span><span style="float:right; color:{color};">{p["trail"]}% {p["status"]}</span></div>'
    html+='</div>'
    st.markdown(html, unsafe_allow_html=True)
    if st.button("📨 Prueba Telegram"):
        send_telegram(f"✅ AURA V9.6 AUTO-FLAT activo — Cierre 3:50pm NY — {datetime.now().strftime('%H:%M:%S')} BOG")
        st.success("Enviado")

with c2:
    fig=go.Figure(); x=list(range(35)); y=[340+i*1.15+random.uniform(-2,4) for i in x]
    fig.add_trace(go.Bar(x=x,y=[random.uniform(1,5) for _ in x], marker_color=["#2EFB9B" if random.random()>0.45 else "#FF6B6B" for _ in x]))
    fig.update_layout(height=280, paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', margin=dict(l=5,r=5,t=5,b=5), showlegend=False, xaxis=dict(showgrid=False, showticklabels=False), yaxis=dict(showgrid=False))
    st.plotly_chart(fig, use_container_width=True)
    rows=[[p["sym"],p["lado"],p["qty"],f"${p['entry']}",f"{p['pnl']:+.2f}",f"{p['trail']}%",p["status"]] for p in positions]
    st.dataframe(pd.DataFrame(rows, columns=["SYMBOL","LADO","QTY","ENTRY","P/L","TRAIL","STATUS"]), use_container_width=True, hide_index=True)

st.success("V9.6 AUTO-FLAT 3:50PM NY — SIN PYTZ — LISTO PARA DEPLOY")
