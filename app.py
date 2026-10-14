# AURA V9.6 OPERATIVO - VECTOR FLOW - FIX 2 - SYNTAX ERROR CORREGIDO
import os, time
from datetime import datetime
import pytz
import streamlit as st
import yfinance as yf
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

st.set_page_config(page_title="AURA V9.6 OPERATIVO FIX 2", layout="wide")

ALPACA_KEY = os.getenv("ALPACA_API_KEY") or st.secrets.get("ALPACA_API_KEY", "")
ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY") or st.secrets.get("ALPACA_SECRET_KEY", "")

NY_TZ = pytz.timezone('America/New_York')

@st.cache_resource
def get_client():
    return TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=True)

trading_client = get_client()

def get_rsi_simple(symbol):
    try:
        df = yf.download(symbol, period="5d", interval="15m", progress=False, auto_adjust=True)
        if len(df) < 20:
            return None
        close = df['Close']
        delta = close.diff()
        # FIX SIMPLE - sin lambda complejo
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        rsi_last = float(rsi.iloc[-1])
        price_last = float(close.iloc[-1])
        ma20 = float(close.rolling(20).mean().iloc[-1])
        # VECTOR FLOW SIMPLE
        rsi_prev = float(rsi.iloc[-3])
        vf = price_last > ma20 and rsi_last > 48 and rsi_last > rsi_prev
        return rsi_last, price_last, ma20, vf, rsi_prev
    except Exception as e:
        return None

def sync_positions():
    try:
        return trading_client.get_all_positions()
    except Exception as e:
        st.error(f"Error sync: {e}")
        return []

def is_market_open():
    now = datetime.now(NY_TZ)
    if now.weekday() >= 5:
        return False
    return (now.hour > 9 or (now.hour == 9 and now.minute >= 30)) and now.hour < 16

# HEADER
st.markdown("### AURA V9.6 OPERATIVO — VECTOR FLOW — FIX 2")
st.caption("ALPACA PAPER • AUTO-FLAT 15:50 NY • FIX _repr_html_ + SYNTAX")

c1, c2, c3, c4 = st.columns([2,1.2,1,1.2])
with c1:
    if st.button("FLAT ALL - CERRAR TODO YA", type="primary", use_container_width=True):
        try:
            trading_client.cancel_orders()
            time.sleep(0.5)
            pos = trading_client.get_all_positions()
            closed = 0
            for p in pos:
                try:
                    trading_client.close_position(p.symbol)
                    closed = closed + 1
                except Exception as ex:
                    st.error(f"Error {p.symbol}: {ex}")
            if closed > 0:
                st.success(f"FLAT ALL: {closed} cerradas")
            else:
                st.info("Sin posiciones")
            time.sleep(1)
            st.rerun()
        except Exception as e:
            st.error(f"Error FLAT ALL: {e}")

with c2:
    st.info("AUTO-FLAT\n15:50 NY")

with c3:
    now_ny = datetime.now(NY_TZ)
    st.info(f"Hora NY\n{now_ny.strftime('%H:%M')}")

with c4:
    if st.button("TEST BUY QQQ 1", use_container_width=True):
        try:
            req = MarketOrderRequest(symbol="QQQ", qty=1, side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
            order = trading_client.submit_order(req)
            oid = str(order.id)
            st.success(f"Orden enviada QQQ BUY 1 ID {oid}")
            time.sleep(1)
            st.rerun()
        except Exception as e:
            st.error(f"Error TEST BUY: {e}")

# AUTO-FLAT 15:50
now_ny = datetime.now(NY_TZ)
if now_ny.hour == 15 and now_ny.minute >= 50:
    if not st.session_state.get("flat_done", False):
        st.warning("AUTO-FLAT 15:50 ACTIVADO")
        try:
            trading_client.cancel_orders()
            pos = trading_client.get_all_positions()
            for p in pos:
                try:
                    trading_client.close_position(p.symbol)
                except:
                    pass
            st.session_state["flat_done"] = True
            st.success(f"AUTO-FLAT {len(pos)} cerradas")
            time.sleep(2)
            st.rerun()
        except Exception as e:
            st.error(f"Error auto-flat: {e}")
elif now_ny.hour == 9 and now_ny.minute == 30:
    st.session_state["flat_done"] = False

positions = sync_positions()
try:
    acct = trading_client.get_account()
    equity = float(acct.equity)
    st.markdown(f"**EQUITY ${equity:,.2f} | POS {len(positions)}/6 | HOY ${equity-100000:+.2f}**")
except Exception as e:
    st.error(f"Error cuenta: {e}")

st.subheader("Posiciones + VECTOR FLOW")
cols = st.columns(6)
symbols = ["SPY","QQQ","AAPL","MSFT","NVDA","TSLA"]
for i, sym in enumerate(symbols):
    data = get_rsi_simple(sym)
    pos = next((p for p in positions if p.symbol == sym), None)
    with cols[i % 6]:
        with st.container(border=True):
            st.markdown(f"**{sym}**")
            if data:
                rsi_val, price, ma20, vf, rsi_prev = data
                st.metric(f"RSI {rsi_val:.1f}", f"${price:.2f}", delta=f"VF {'OK' if vf else 'NO'}")
                st.caption(f"MA20 ${ma20:.1f} VF {'✓' if vf else '✗'}")
            else:
                st.metric(sym, "--")
            if pos:
                pl = float(pos.unrealized_pl)
                plpc = float(pos.unrealized_plpc)*100
                qty = float(pos.qty)
                entry = float(pos.avg_entry_price)
                if pl >= 0:
                    st.success(f"Qty {qty}\nEntry ${entry:.2f}\n+${pl:.2f} ({plpc:+.1f}%)")
                else:
                    st.error(f"Qty {qty}\nEntry ${entry:.2f}\n${pl:.2f} ({plpc:+.1f}%)")
            else:
                st.caption("Sin pos")

st.caption("V9.6 FIX 2 - Syntax corregido - Socios Syna & Yesid - Forma V9.5 restaurada pronto")
