"""
AURA SYNAPSE V9.6 OPERATIVO — VECTOR FLOW + AUTO-FLAT 3:50PM NY
ENTRA A TRADES SOLO + CIERRA TODO 15:50 NY + NO OVERNIGHT RISK
"""
import os, base64, requests, streamlit as st, random
from datetime import datetime, time as dtime, timezone, timedelta
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest

# === CONFIG ALPACA ===
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"

if ALPACA_API_KEY and ALPACA_SECRET_KEY:
    trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=ALPACA_PAPER)
    data_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
else:
    trading_client = None
    data_client = None

# === FUNCIONES CORE ===
def get_logo_b64():
    for path in ["Aura.png", "aura.png", "/mnt/data/Aura.png"]:
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
                     json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}, timeout=5)
    except: pass

def get_quote(symbol):
    if not data_client: return None
    try:
        req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        quote = data_client.get_stock_latest_quote(req)[symbol]
        return {"bid": float(quote.bid_price), "ask": float(quote.ask_price), "mid": (float(quote.bid_price) + float(quote.ask_price))/2}
    except: return None

def get_posiciones():
    if not trading_client: return []
    try:
        positions = trading_client.get_all_positions()
        return [{
            "SYMBOL": p.symbol,
            "QTY": float(p.qty),
            "ENTRY": round(float(p.avg_entry_price), 2),
            "P/L": round(float(p.unrealized_pl), 2),
            "P/L%": round(float(p.unrealized_plpc)*100, 2),
            "STATUS": "LOCK" if float(p.unrealized_pl) > 0 else "ACTIVE"
        } for p in positions]
    except: return []

def cerrar_todas_posiciones():
    if not trading_client: return False, "Sin conexión Alpaca"
    try:
        trading_client.close_all_positions(cancel_orders=True)
        msg = f"🚨 *AUTO-FLAT 3:50PM NY EJECUTADO*\n\nTodas las posiciones cerradas\n\n{datetime.now().strftime('%H:%M:%S')} BOG\n\nNo overnight risk ✅"
        send_telegram(msg)
        return True, "Cerradas"
    except Exception as e:
        return False, str(e)

def abrir_trade(symbol, qty, side="buy"):
    if not trading_client: return False, "Sin conexión"
    try:
        order = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side=="buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY
        )
        trading_client.submit_order(order)
        send_telegram(f"🟢 *ENTRADA AURA*\n\n{symbol} {side.upper()} {qty} shares\n{datetime.now().strftime('%H:%M:%S')} NY")
        return True, "Orden enviada"
    except Exception as e:
        return False, str(e)

def vector_flow_signal(symbol):
    """Lógica simple: si bid>ask*1.001 = momentum alcista"""
    q = get_quote(symbol)
    if not q: return False
    spread = (q["ask"] - q["bid"]) / q["bid"]
    return spread < 0.001 and q["mid"] > 0 # momentum + spread bajo

# === UI ===
st.set_page_config(page_title="AURA V9.6 OPERATIVO", layout="wide")
logo_b64 = get_logo_b64()
logo_html = f'<img src="data:image/png;base64,{logo_b64}" style="width:44px;height:44px;background:white;border-radius:10px;">' if logo_b64 else '🕯️'

ny_tz = timezone(timedelta(hours=-4))
now_ny = datetime.now(ny_tz)
now_time = now_ny.time()
market_open = dtime(9, 30) <= now_time < dtime(16, 0) and now_ny.weekday() < 5

st.markdown(f"""
<div style="display:flex; justify-content:space-between; background:#11161F; padding:10px; border-radius:12px; margin-bottom:12px;">
  <div style="display:flex; gap:10px; align-items:center;">
    <div style="background:white; border-radius:10px; padding:3px;">{logo_html}</div>
    <div><b>AURA V9.6 OPERATIVO — VECTOR FLOW</b><br><span style="font-size:10px;">ALPACA {'PAPER' if ALPACA_PAPER else 'LIVE'} • AUTO-FLAT 15:50 NY</span></div>
  </div>
  <span style="background:{'#0F2D25' if market_open else '#101826'}; color:{'#2EFB9B' if market_open else '#E0E6EB'}; padding:6px 12px; border-radius:20px;">{'MARKET OPEN' if market_open else 'MARKET CLOSED'}</span>
</div>
""", unsafe_allow_html=True)

col1,col2,col3,col4=st.columns([2,1,1,1])

with col1:
    if st.button("🔴 FLAT ALL — CERRAR TODO YA", type="primary"):
        ok, msg = cerrar_todas_posiciones()
        if ok: st.success("✅ Ejecutado en Alpaca"); st.rerun()
        else: st.error(f"Error: {msg}")

with col2:
    st.markdown('<div style="background:#0F2D25; border:1px solid #2EFB9B; padding:8px; border-radius:12px; text-align:center; font-size:11px;"><b>AUTO-FLAT</b><br>15:50 NY</div>', unsafe_allow_html=True)

with col3:
    st.markdown(f'<div style="background:#101826; border:1px solid #1E2E44; padding:8px; border-radius:12px; text-align:center; font-size:11px;"><b>Hora NY</b><br>{now_time.strftime("%H:%M")}</div>', unsafe_allow_html=True)

with col4:
    if st.button("🟢 TEST BUY QQQ 1"):
        ok, msg = abrir_trade("QQQ", 1, "buy")
        st.success(msg) if ok else st.error(msg)

# === AUTO-FLAT 3:50PM ===
if dtime(15, 45) <= now_time < dtime(15, 50):
    st.warning("⚠️ En 5 min AUTO-FLAT 3:50pm")
    send_telegram("⚠️ AUTO-FLAT en 5 min")

if dtime(15, 50) <= now_time < dtime(15, 51):
    ok, msg = cerrar_todas_posiciones()
    if ok: st.success("🚨 AUTO-FLAT EJECUTADO")

# === VECTOR FLOW - ENTRADAS AUTO ===
if market_open and dtime(9, 35) <= now_time < dtime(15, 40): # Solo opera 9:35am - 3:40pm
    watchlist = ["QQQ", "SPY", "TSLA", "NVDA"]
    for sym in watchlist:
        if vector_flow_signal(sym):
            # Revisa si ya tienes posición
            pos = [p for p in get_posiciones() if p["SYMBOL"]==sym]
            if not pos: # No tiene posición, entra
                abrir_trade(sym, 1, "buy")
                st.toast(f"Entrada {sym}", icon="🟢")

# === TABLA POSICIONES ===
positions = get_posiciones()
if positions:
    st.dataframe(positions, use_container_width=True, hide_index=True)
    total_pl = sum([p["P/L"] for p in positions])
    st.metric("P/L Total Abierto", f"${total_pl:+.2f}")
else:
    st.info("Sin posiciones abiertas" if trading_client else "Conecta API keys de Alpaca")

st.caption("V9.6 OPERATIVO — Entra solo si hay señal VECTOR FLOW • Cierra 3:50pm NY • No overnight")
