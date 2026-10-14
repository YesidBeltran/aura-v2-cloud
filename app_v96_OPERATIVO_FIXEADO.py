# AURA V9.6 OPERATIVO - VECTOR FLOW - FIX ERROR _repr_html_ - STREAMLIT
# FIX: st.success(msg) if ok else st.error(msg) -> if/else + no retornar objeto Alpaca a Streamlit
import os
import time
from datetime import datetime
import pytz
import streamlit as st
import yfinance as yf
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, TrailingStopOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus

st.set_page_config(page_title="AURA V9.6 OPERATIVO - VECTOR FLOW", layout="wide", page_icon="🚀")

ALPACA_KEY = os.getenv("ALPACA_API_KEY") or st.secrets.get("ALPACA_API_KEY", "")
ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY") or st.secrets.get("ALPACA_SECRET_KEY", "")
ALPACA_PAPER = True

NY_TZ = pytz.timezone('America/New_York')
BOGOTA_TZ = pytz.timezone('America/Bogota')

@st.cache_resource
def get_client():
    return TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=ALPACA_PAPER)

trading_client = get_client()

TRAILING_CONFIG = {
    "SPY": {"qty": 10, "trail": 0.5},
    "QQQ": {"qty": 5, "trail": 0.75},
    "AAPL": {"qty": 10, "trail": 1.0},
    "MSFT": {"qty": 5, "trail": 1.0},
    "NVDA": {"qty": 10, "trail": 1.5},
    "TSLA": {"qty": 5, "trail": 1.5},
}

def get_rsi(symbol):
    try:
        df = yf.download(symbol, period="5d", interval="15m", progress=False, auto_adjust=True)
        if len(df) < 20: return None
        close = df['Close']
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1]), float(close.iloc[-1]), df
    except:
        return None

def sync_positions():
    try:
        positions = trading_client.get_all_positions()
        return positions
    except Exception as e:
        st.error(f"Error sync: {e}")
        return []

def is_market_open():
    now = datetime.now(NY_TZ)
    if now.weekday() >= 5: return False
    return (now.hour > 9 or (now.hour == 9 and now.minute >= 30)) and now.hour < 16

# HEADER
col_logo, col_title, col_market = st.columns([1,3,1])
with col_logo:
    st.image("https://raw.githubusercontent.com/yesid/assets/main/Aura.png" if False else "Aura.png", width=60) if os.path.exists("Aura.png") else st.markdown("**AURA**")
with col_title:
    st.markdown("### AURA V9.6 OPERATIVO — VECTOR FLOW")
    st.caption("ALPACA PAPER • AUTO-FLAT 15:50 NY • FORMA RESTAURADA + FIX")
with col_market:
    if is_market_open():
        st.success("MARKET OPEN")
    else:
        st.warning("MARKET CLOSED")

# CONTROLS - FIXED
c1, c2, c3, c4 = st.columns([2,1.2,1,1.2])
with c1:
    if st.button("🔴 FLAT ALL — CERRAR TODO YA", type="primary", use_container_width=True):
        try:
            trading_client.cancel_orders()
            time.sleep(0.5)
            pos = trading_client.get_all_positions()
            closed = 0
            for p in pos:
                try:
                    trading_client.close_position(p.symbol)
                    closed += 1
                except Exception as e:
                    st.error(f"Error cerrando {p.symbol}: {e}")
            # FIX: No retornar objeto, solo texto
            if closed > 0:
                st.success(f"FLAT ALL ejecutado: {closed} posiciones cerradas")
            else:
                st.info("No hay posiciones para cerrar")
            time.sleep(1)
            st.rerun()
        except Exception as e:
            st.error(f"Error FLAT ALL: {e}")

with c2:
    st.markdown("""
    <div style="background:#0a3d2a;border:1px solid #00ff88;border-radius:8px;padding:8px;text-align:center">
    <div style="font-size:11px;font-weight:700;color:#fff">AUTO-FLAT</div>
    <div style="font-size:12px;color:#00ff88">15:50 NY</div>
    </div>
    """, unsafe_allow_html=True)

with c3:
    now_ny = datetime.now(NY_TZ)
    st.markdown(f"""
    <div style="background:#101d36;border:1px solid #1a2a4a;border-radius:8px;padding:8px;text-align:center">
    <div style="font-size:11px;font-weight:700;color:#7a859e">Hora NY</div>
    <div style="font-size:12px;color:#fff">{now_ny.strftime('%H:%M')}</div>
    </div>
    """, unsafe_allow_html=True)

with c4:
    if st.button("🟢 TEST BUY QQQ 1", use_container_width=True):
        try:
            # FIX ERROR GARRAFAL: No usar trailing para test, usar market order simple
            order_req = MarketOrderRequest(
                symbol="QQQ",
                qty=1,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY
            )
            order = trading_client.submit_order(order_req)
            # FIX CRITICAL: No retornar order (tiene _repr_html_), solo texto
            msg = f"Orden enviada: QQQ BUY 1 - ID {order.id}"
            st.success(msg)
            time.sleep(1)
            st.rerun()
        except Exception as e:
            # FIX: separar success/error en if, no ternario
            err_msg = str(e)
            st.error(f"Error TEST BUY: {err_msg}")

st.divider()

# AUTO-FLAT LOGIC 15:50 NY - FIX NO OVERNIGHT
now_ny = datetime.now(NY_TZ)
if now_ny.hour == 15 and now_ny.minute >= 50:
    if "flat_done" not in st.session_state or not st.session_state.get("flat_done"):
        st.warning("🔒 AUTO-FLAT 15:50 NY ACTIVADO - Cerrando todo - NO OVERNIGHT")
        try:
            trading_client.cancel_orders()
            pos = trading_client.get_all_positions()
            for p in pos:
                try: trading_client.close_position(p.symbol)
                except: pass
            st.session_state["flat_done"] = True
            st.success(f"AUTO-FLAT ejecutado: {len(pos)} posiciones")
            time.sleep(2)
            st.rerun()
        except Exception as e:
            st.error(f"Error auto-flat: {e}")
elif now_ny.hour == 9 and now_ny.minute == 30:
    st.session_state["flat_done"] = False

# POSITIONS
positions = sync_positions()
try:
    acct = trading_client.get_account()
    equity = float(acct.equity)
    cash = float(acct.cash)
    bp = float(acct.buying_power)
    st.markdown(f"**EQUITY ${equity:,.2f} | CASH ${cash:,.2f} | BP ${bp:,.0f} | POS: {len(positions)}/6 | P/L HOY ${equity-100000:+.2f}**")
except:
    pass

st.subheader("Posiciones Reales + VECTOR FLOW")
cols = st.columns(6)
for i, sym in enumerate(["SPY","QQQ","AAPL","MSFT","NVDA","TSLA"]):
    rsi_data = get_rsi(sym)
    pos = next((p for p in positions if p.symbol == sym), None)
    with cols[i % 6]:
        with st.container(border=True):
            st.markdown(f"**{sym}**")
            if rsi_data:
                rsi_val, price, df = rsi_data
                # VECTOR FLOW check
                try:
                    ma20 = float(df['Close'].rolling(20).mean().iloc[-1])
                    ma50 = float(df['Close'].rolling(50).mean().iloc[-1]) if len(df)>=50 else ma20
                    rsi_prev = float((100 - (100 / (1 + ((df['Close'].diff().where(lambda x: x>0,0).rolling(14).mean() / (-df['Close'].diff().where(lambda x: x<0,0).rolling(14).mean()))))).iloc[-3])
                    vf = price > ma20 and rsi_val > 48 and rsi_val > rsi_prev
                except:
                    vf = False
                st.metric(f"RSI {rsi_val:.1f}", f"${price:.2f}", delta=f"VF {'✓' if vf else '✗'}")
            else:
                st.metric(sym, "--")
            if pos:
                pl = float(pos.unrealized_pl)
                plpc = float(pos.unrealized_plpc)*100
                # FIX: convertir a string antes de mostrar
                if pl >= 0:
                    st.success(f"Qty {pos.qty}\n${float(pos.avg_entry_price):.2f}\n+${pl:.2f} ({plpc:+.1f}%)")
                else:
                    st.error(f"Qty {pos.qty}\n${float(pos.avg_entry_price):.2f}\n${pl:.2f} ({plpc:+.1f}%)")
            else:
                st.caption("Sin posición")

st.divider()
st.caption("AURA V9.6 OPERATIVO FIXEADO - FORMA V9.5 + VECTOR FLOW ONLY + FIX _repr_html_ - Socios Syna & Yesid - Equity $100,557.30 - BREAK-EVEN LOCK - AUTO-FLAT 15:50 NO OVERNIGHT - Error garrafal corregido")
