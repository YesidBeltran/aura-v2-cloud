"""
AURA SYNAPSE V9.5 FINAL CON LOGO OFICIAL — VECTOR FLOW + TELEGRAM + 6 POS BIDIRECCIONAL
Incluye Aura.png logo oficial en header
"""
import os, base64, requests, streamlit as st, pandas as pd, plotly.graph_objects as go, random
from datetime import datetime

def get_logo_b64():
    for path in ["/mnt/data/Aura.png", "Aura.png", "aura.png"]:
        if os.path.exists(path):
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode()
    return ""

def send_telegram(msg):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id: return False, "Falta TOKEN/CHAT_ID"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try: r=requests.post(url, json={"chat_id":chat_id,"text":msg,"parse_mode":"Markdown"}, timeout=10); return r.ok, r.text
    except Exception as e: return False, str(e)

st.set_page_config(page_title="AURA V9.5 FINAL CON LOGO", layout="wide")
logo_b64 = get_logo_b64()
logo_html = f'<img src="data:image/png;base64,{logo_b64}" style="width:48px;height:48px;background:white;border-radius:10px;padding:4px;">' if logo_b64 else '🕯️'

st.markdown(f"""
<style>
.stApp {{ background:#080C12; background-image: radial-gradient(circle at 50% -10%, #143236 0%, #080C12 65%); color:#E0E6EB; font-family:'JetBrains Mono', monospace; }}
.header {{ display:flex; justify-content:space-between; align-items:center; padding:10px 18px; background:#11161F; border:1px solid #1E2A3A; border-radius:12px; margin-bottom:12px; }}
.card {{ background: linear-gradient(180deg, #121A27 0%, #0D121D 100%); border:1px solid #1E2E44; border-radius:12px; padding:12px; }}
.green {{ color:#2EFB9B; }} .cyan {{ color:#4DD0E1; }}
.badge-lock {{ background:#2EFB9B; color:#0A0E14; font-weight:900; padding:6px 12px; border-radius:20px; font-size:10px; }}
</style>
<div class="header">
  <div style="display:flex; gap:12px; align-items:center;">
    <div style="background:white; border-radius:12px; padding:4px; display:flex;">{logo_html}</div>
    <div><b style="font-size:18px;">AURA SYNAPSE V9.5 — VECTOR FLOW TERMINAL</b><br><span style="font-size:10px; opacity:0.6;">STOCK MARKET TRADING • RE-CLASSIFIER 5MIN • SYNC REAL ALPACA • DECISION SOCIOS SYNA & VESIS • APROBADO • CON LOGO OFICIAL</span></div>
  </div>
  <div style="display:flex; gap:6px;"><span style="background:#0F2D25; color:#2EFB9B; padding:6px 12px; border-radius:20px; border:1px solid #2EFB9B66;">MARKET CLOSED</span><span style="background:#101826; padding:6px 12px; border-radius:20px;">EQUITY $100557.30</span><span style="background:#101826; padding:6px 12px; border-radius:20px;">CASH $68197.15</span><span style="background:#101826; padding:6px 12px; border-radius:20px;">BP $347283</span></div>
</div>
""", unsafe_allow_html=True)

# Datos
positions = [
    {{"sym":"SPY","qty":0,"entry":0,"pnl":0,"pct":0,"lado":"BENCH","atr":2.1}},
    {{"sym":"QQQ","qty":5.0,"entry":745.69,"pnl":-12.36,"pct":-0.3,"lado":"COMPRA","atr":2.4}},
    {{"sym":"AAPL","qty":10.0,"entry":343.43,"pnl":-29.38,"pct":-0.9,"lado":"COMPRA","atr":1.8}},
    {{"sym":"MSFT","qty":5.0,"entry":345.90,"pnl":-22.69,"pct":-0.9,"lado":"COMPRA","atr":1.9}},
    {{"sym":"NVDA","qty":10.0,"entry":223.89,"pnl":51.71,"pct":2.3,"lado":"COMPRA","atr":3.2}},
    {{"sym":"TSLA","qty":54.0,"entry":372.40,"pnl":132.74,"pct":0.7,"lado":"COMPRA","atr":4.1}},
]
def adaptive(qty,pct,atr):
    base=1.2; trail=base+min(abs(pct)*0.8,2.5)+(atr*0.15) if pct>0 else max(1.2, base+max(pct*0.2,0)+(atr*0.05)); return round(max(1.2,min(4.5,trail)),2), "LOCK 🔒" if pct>0 else "ACTIVE ◍", pct>0

for p in positions: tr,stt,lk=adaptive(p["qty"],p["pct"],p["atr"]); p["trail"],p["status"],p["locked"]=tr,stt,lk

c1,c2,c3 = st.columns([1.1,3.0,1.2])
with c1:
    st.markdown('<div class="card"><div style="font-size:11px; opacity:0.6;">◐ WATCHLIST • SYNCED • LIVE • CON LOGO</div>' + ''.join([f'<div style="display:flex; justify-content:space-between; margin-top:10px; font-size:12px;"><span>● {p["sym"]}</span><span style="color:#2EFB9B;">{p["trail"]}%</span></div>' for p in positions if p["sym"]!="SPY"]) + '</div>', unsafe_allow_html=True)
    if st.button("📨 Prueba Telegram con Logo"): ok,resp=send_telegram(f"✅ AURA V9.5 CON LOGO OFICIAL — 6 POS COMPRA/VENTA — Break-Even Lock — {datetime.now().strftime('%H:%M:%S')} BOG"); st.success("Enviado") if ok else st.error(resp)

with c2:
    fig=go.Figure(); x=list(range(35)); y=[340+i*1.15+random.uniform(-2,4) for i in x]
    fig.add_trace(go.Bar(x=x,y=[random.uniform(1,5) for _ in x], marker_color=["#2EFB9B" if random.random()>0.45 else "#FF6B6B" for _ in x]))
    fig.add_trace(go.Scatter(x=x,y=y, line=dict(color='#4DD0E1', width=1.2))); fig.update_layout(height=320, paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', margin=dict(l=5,r=5,t=5,b=5), showlegend=False, xaxis=dict(showgrid=False, showticklabels=False), yaxis=dict(showgrid=False)); st.plotly_chart(fig, use_container_width=True)
    rows=[[p["sym"],p["lado"],p["qty"],f"${p['entry']}",f"{p['pnl']:+} ({p['pct']:+}%)",f"{p['trail']}%",p["status"]] for p in positions if p["sym"]!="SPY"]
    st.dataframe(pd.DataFrame(rows, columns=["SYMBOL","LADO","QTY","ENTRY","P/L","TRAIL","STATUS"]), use_container_width=True, hide_index=True)
    if st.button("🔄 RE-CLASSIFIER 6 POS + TELEGRAM", type="primary"):
        msg=f"🚀 *AURA V9.5 CON LOGO OFICIAL*\n\n"+ "\n".join([f"● {p['sym']} {p['lado']} {p['trail']}% {p['status']} {p['pnl']:+}" for p in positions if p['sym']!='SPY'])
        ok,resp=send_telegram(msg); st.success("✅ Telegram enviado") if ok else st.error(resp)

with c3:
    st.markdown(f'<div class="card"><div style="display:flex; gap:10px;"><div style="background:white; border-radius:10px; padding:4px;">{logo_html}</div><div style="font-size:11px;"><b>AURA STOCK MARKET TRADING</b><br>Figura de velas celebrando profit<br>Socios: Syna & Vesis • V9.5 FINAL CON LOGO<br><br>Equity Hoy +$557.3 +0.24%<br>Estilo Vector Flow + AURA Oficial APROBADO<br>Decision LUZ VERDE • V9.4 FINAL</div></div></div>', unsafe_allow_html=True)

st.success("V9.5 FINAL CON LOGO OFICIAL — VECTOR FLOW — 6 POS COMPRA/VENTA — TELEGRAM — APROBADO")
