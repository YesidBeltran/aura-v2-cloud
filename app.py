"""
AURA V10.2 - Vector Flow Terminal (Optimizado para Rendimiento)
requirements.txt: streamlit, alpaca-py, requests, tzdata, streamlit-autorefresh, pandas
"""
import os, json, uuid, base64, requests, time, threading
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import altair as alt
import streamlit as st
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest, TakeProfitRequest, StopLossRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus, OrderClass, OrderStatus, OrderType
try:
    import gspread
except ImportError:
    gspread = None
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

st.set_page_config(page_title="AURA SYNAPSE V10.2", layout="wide", page_icon="🕯️")

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
REGISTRO_DESDE = os.getenv("REGISTRO_DESDE", "2026-10-16")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "")

WATCHLIST = ["QQQ", "SPY", "NVDA", "TSLA"]
DASH_SYMBOLS = ("SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA")
MAX_POSITIONS, MAX_SPREAD_PCT = 4, 0.10
TAKE_PROFIT_PCT = 2.0    # Ajustado a +2.0% para mejorar R/R
STOP_LOSS_PCT = 1.0      # Limitado a -1.0%
COOLDOWN_MIN = 15        # Reducido a 15 minutos de espera tras salida
ALLOW_SHORTS = True      
ENTRY_AFTER, NO_ENTRY_LAST_MIN, FLAT_LAST_MIN, CYCLE_SECONDS = dtime(10, 0), 20, 10, 15
ET, BOG = ZoneInfo("America/New_York"), ZoneInfo("America/Bogota")

trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=ALPACA_PAPER) if ALPACA_API_KEY else None
data_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY) if ALPACA_API_KEY else None


# ---------- UTILIDADES ----------
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
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json={"chat_id": TELEGRAM_CHAT, "text": text}, timeout=4)
    except Exception as e:
        print("Telegram error:", e)


def get_quote(sym):
    if not data_client:
        return None
    try:
        q = data_client.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=sym))[sym]
        bid, ask = float(q.bid_price or 0), float(q.ask_price or 0)
        return (bid, ask) if bid > 0 and ask >= bid else None
    except Exception as e:
        print(f"Quote {sym} error:", e)
        return None


def safe(fn, default=None):
    try:
        return fn()
    except Exception as e:
        print("API error:", e)
        return default


def get_account():   return safe(lambda: trading_client.get_account()) if trading_client else None
def get_positions(): return safe(lambda: trading_client.get_all_positions(), []) if trading_client else []
def get_clock():     return safe(lambda: trading_client.get_clock()) if trading_client else None


@st.cache_data(ttl=60)
def get_bars(symbols, days=5):
    if not data_client:
        return pd.DataFrame()
    try:
        req = StockBarsRequest(symbol_or_symbols=list(symbols), timeframe=TimeFrame(5, TimeFrameUnit.Minute),
                               start=datetime.now(ET) - timedelta(days=days), feed=DataFeed.IEX)
        return data_client.get_stock_bars(req).df
    except Exception as e:
        print("Bars error:", e)
        return pd.DataFrame()


def calc_rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    if len(s) <= n or not dn.iloc[-1]:
        return None
    return float(100 - 100 / (1 + up.iloc[-1] / dn.iloc[-1]))


def calc_atr(df, n=14):
    if len(df) <= n:
        return None
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return float(tr.ewm(alpha=1 / n, adjust=False).mean().iloc[-1])


def symbol_df(bars, sym):
    if bars.empty or sym not in bars.index.get_level_values("symbol"):
        return pd.DataFrame()
    return bars.xs(sym, level="symbol")


# ---------- WORKER EN SEGUNDO PLANO ----------
@st.cache_resource
def get_state():
    return {"lock": threading.Lock(), "auto_entries": True, "last_flat_date": None, "log": [], "started": False}


def log(state, msg):
    with state["lock"]:
        state["log"] = (state["log"] + [f"{datetime.now(ET).strftime('%H:%M:%S')} {msg}"])[-30:]


def get_signal(sym):
    """Genera señal optimizada evitando bloqueos estrictos de RSI."""
    try:
        req = StockBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame(5, TimeFrameUnit.Minute),
                               start=datetime.now(ET) - timedelta(days=4), feed=DataFeed.IEX)
        d = symbol_df(data_client.get_stock_bars(req).df, sym)
        c = d["close"]
    except Exception as e:
        print(f"Signal {sym} error:", e)
        return None
    r = calc_rsi(c) if len(c) >= 30 else None
    if r is None:
        return None
    fast, slow = c.ewm(span=9, adjust=False).mean().iloc[-1], c.ewm(span=21, adjust=False).mean().iloc[-1]
    ctx = {"rsi": r, "diff": (fast - slow) / slow * 100, "atr": calc_atr(d)}
    
    # Filtros de RSI mejorados para capturar tendencia sin perder momentum
    if fast > slow and 50 < r < 80:
        return "buy", ctx
    if ALLOW_SHORTS and fast < slow and 20 < r < 50:
        return "sell", ctx
    return None


def close_everything():
    trading_client.cancel_orders()
    for _ in range(10):
        if not trading_client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN)):
            break
        time.sleep(1)
    trading_client.close_all_positions(cancel_orders=False)
    time.sleep(3)
    return [p.symbol for p in get_positions()]


def flat_all(state, now):
    before = get_positions()
    if not before:
        state["last_flat_date"] = now.date()
        return
    try:
        left = close_everything()
    except Exception as e:
        log(state, f"Error auto-flat: {e}")
        left = [p.symbol for p in get_positions()]
    state["flat_tries"] = state.get("flat_tries", 0) + 1
    if not left:
        state["last_flat_date"], state["flat_tries"] = now.date(), 0
        msg = f"AUTO-FLAT {now.strftime('%H:%M')} NY - {len(before)} posiciones cerradas y verificadas."
        log(state, msg)
        send_tg("🚨 " + msg)
    else:
        msg = f"AUTO-FLAT INCOMPLETO: siguen abiertas {', '.join(left)}. Reintentando."
        log(state, msg)


def run_cycle(state):
    if not trading_client:
        return
    clock = get_clock()
    if not clock or not clock.is_open:
        return
    now = clock.timestamp.astimezone(ET)
    mins_to_close = (clock.next_close - clock.timestamp).total_seconds() / 60

    if mins_to_close <= FLAT_LAST_MIN:
        if state["last_flat_date"] != now.date():
            flat_all(state, now)
        return

    if not state["auto_entries"] or now.time() < ENTRY_AFTER or mins_to_close <= NO_ENTRY_LAST_MIN:
        state["prev_busy"] = set()
        return

    positions = get_positions()
    open_orders = trading_client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
    busy = {p.symbol for p in positions} | {o.symbol for o in open_orders}
    for s in state.get("prev_busy", set()) - busy:
        state.setdefault("last_exit", {})[s] = time.time()
    state["prev_busy"] = busy
    slots = MAX_POSITIONS - len(busy)
    
    for sym in WATCHLIST:
        if slots <= 0:
            break
        last = state.get("last_exit", {}).get(sym)
        if sym in busy or (last and time.time() - last < COOLDOWN_MIN * 60):
            continue
        quote = get_quote(sym)
        if not quote:
            continue
        bid, ask = quote
        spread_pct = (ask - bid) / bid * 100
        sig = get_signal(sym) if spread_pct < MAX_SPREAD_PCT else None
        side, ctx = sig if sig else (None, None)
        if side:
            try:
                buy = side == "buy"
                ref = ask if buy else bid
                tp = round(ref * (1 + (TAKE_PROFIT_PCT if buy else -TAKE_PROFIT_PCT) / 100), 2)
                sl = round(ref * (1 - (STOP_LOSS_PCT if buy else -STOP_LOSS_PCT) / 100), 2)
                trading_client.submit_order(MarketOrderRequest(
                    symbol=sym, qty=1, side=OrderSide.BUY if buy else OrderSide.SELL, time_in_force=TimeInForce.DAY,
                    order_class=OrderClass.BRACKET,
                    client_order_id=f"aura|{ctx['rsi']:.1f}|{ctx['diff']:.3f}|{ctx['atr'] or 0:.3f}|{spread_pct:.3f}|{uuid.uuid4().hex[:8]}",
                    take_profit=TakeProfitRequest(limit_price=tp),
                    stop_loss=StopLossRequest(stop_price=sl)))
                state.setdefault("last_entry", {})[sym] = time.time()
                slots -= 1
                msg = f"ENTRY {sym} {'BUY' if buy else 'SHORT'} 1 @ ~${ref:.2f} TP ${tp} SL ${sl}"
                log(state, msg)
                send_tg("🟢 AURA " + msg)
            except Exception as e:
                log(state, f"Error entrada {sym}: {e}")


def get_ws(state):
    if state.get("ws") is None and gspread and GOOGLE_SHEET_ID and GOOGLE_CREDENTIALS_JSON \
            and time.time() > state.get("ws_retry", 0):
        try:
            gc = gspread.service_account_from_dict(json.loads(GOOGLE_CREDENTIALS_JSON))
            ws = gc.open_by_key(GOOGLE_SHEET_ID).sheet1
            state["logged"] = set(ws.col_values(10)[1:])
            state["ws"] = ws
            log(state, "Google Sheets conectado")
        except Exception as e:
            state["ws_retry"] = time.time() + 300
            log(state, f"Error Sheets: {e}")
    return state.get("ws")


def sync_sheet(state):
    ws = get_ws(state)
    if not ws or not trading_client:
        return
    start = max(datetime.now(ET) - timedelta(days=7),
                datetime.fromisoformat(REGISTRO_DESDE).replace(tzinfo=ET))
    orders = trading_client.get_orders(GetOrdersRequest(status=QueryOrderStatus.CLOSED, after=start, nested=True, limit=500))
    filled = [o for o in orders if o.status == OrderStatus.FILLED]
    for e in sorted((o for o in filled if o.order_class == OrderClass.BRACKET), key=lambda o: o.filled_at):
        if str(e.id) in state["logged"]:
            continue
        ex = next((l for l in (e.legs or []) if l.status == OrderStatus.FILLED), None)
        if ex:
            tp, sl, flat = ex.order_type == OrderType.LIMIT, ex.order_type != OrderType.LIMIT, False
        else:
            c = [o for o in filled if o.symbol == e.symbol and o.side != e.side
                 and o.order_class != OrderClass.BRACKET and o.filled_at > e.filled_at]
            ex = min(c, key=lambda o: o.filled_at) if c else None
            tp, sl, flat = False, False, True
        if not ex:
            continue
        buy = e.side == OrderSide.BUY
        d, x, qty = float(e.filled_avg_price), float(ex.filled_avg_price), float(e.filled_qty)
        pnl = round((x - d) * qty * (1 if buy else -1), 2)
        mark = lambda v: "SI" if v else ""
        parts = (e.client_order_id or "").split("|")
        ctx = []
        for v in (parts[1:5] if parts[0] == "aura" and len(parts) >= 5 else []):
            try:
                ctx.append(float(v))
            except ValueError:
                ctx.append("")
        ctx += [""] * (4 - len(ctx))
        ws.append_row([ex.filled_at.astimezone(BOG).strftime("%Y-%m-%d %H:%M"), e.symbol,
                       "COMPRA" if buy else "CORTO", d, x, pnl, mark(tp), mark(sl), mark(flat), str(e.id)]
                      + ctx + [e.filled_at.astimezone(BOG).strftime("%H:%M")],
                      value_input_option="USER_ENTERED")
        state["logged"].add(str(e.id))
        log(state, f"Registrado {e.symbol} {pnl:+.2f}")


def worker_loop(state):
    while True:
        try:
            run_cycle(state)
        except Exception as e:
            log(state, f"Error ciclo: {e}")
        try:
            if time.time() - state.get("last_sync", 0) >= 60:
                state["last_sync"] = time.time()
                sync_sheet(state)
        except Exception as e:
            log(state, f"Error registro: {e}")
        time.sleep(CYCLE_SECONDS)


state = get_state()
if not state["started"] and trading_client:
    state["started"] = True
    threading.Thread(target=worker_loop, args=(state,), daemon=True).start()
    log(state, "Worker iniciado")
    send_tg(f"🕯️ AURA worker iniciado\n{datetime.now(ET).strftime('%H:%M NY')} / {datetime.now(BOG).strftime('%H:%M BOG')}")

if st_autorefresh:
    st_autorefresh(interval=10_000, key="aura_refresh")

# ---------- INTERFAZ STREAMLIT ----------
now_et, now_bog = datetime.now(ET), datetime.now(BOG)
clock = get_clock()
is_market = bool(clock and clock.is_open)
acc, positions = get_account(), get_positions()
bars = get_bars(DASH_SYMBOLS)

st.markdown("""<style>
.stApp{background:#080C14;color:#E6EDF7}.block-container{padding-top:1rem;max-width:1500px}
[data-testid=stVerticalBlockBorderWrapper]{background:#0F1219;border-color:#1E2E44!important;border-radius:14px}
.row{display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid #1A2335;font-size:13px}
.g{color:#2EFB9B}.r{color:#FF5C6C}.y{color:#FBB02E}.m{color:#8B9BB4;font-size:11px}
.chip{background:#101826;border:1px solid #1E2E44;border-radius:20px;padding:5px 12px;font-size:11px;margin-left:6px}
.ttl{font-size:11px;color:#8B9BB4;letter-spacing:1px;margin-bottom:6px}
</style>""", unsafe_allow_html=True)

logo = get_logo()
logo_html = f"<img src='data:image/png;base64,{logo}' style='width:40px;height:40px;'/>" if logo else "🕯️"
eq_txt = f"${float(acc.equity):,.2f}" if acc else "—"
bp_txt = f"${float(acc.buying_power):,.2f}" if acc else "—"
mk = "<span class='chip g' style='border-color:#2EFB9B'>● MARKET OPEN</span>" if is_market else "<span class='chip'>● MARKET CLOSED</span>"

st.markdown(f"""<div style="background:#0F1219;border:1px solid #1E2E44;border-radius:16px;padding:12px 18px;display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
<div style="display:flex;gap:14px;align-items:center"><div style="background:white;border-radius:12px;padding:4px;width:48px;height:48px;display:flex;align-items:center;justify-content:center">{logo_html}</div>
<div><div style="font-size:18px;font-weight:800">AURA SYNAPSE V10.2 <span class="g" style="font-size:12px">VECTOR FLOW TERMINAL</span></div>
<div class="m">DATOS REALES ALPACA • OPTIMIZADO PARA RENTABILIDAD</div></div></div>
<div>{mk}<span class="chip">EQUITY {eq_txt}</span><span class="chip">BP {bp_txt}</span><span class="chip">NY {now_et.strftime('%H:%M:%S')}</span><span class="chip">BOG {now_bog.strftime('%I:%M %p')}</span></div></div>""", unsafe_allow_html=True)

colL, colC, colR = st.columns([1, 2.4, 1.2])

with colL:
    with st.container(border=True):
        st.markdown("<div class='ttl'>WATCHLIST • ALPACA IEX • RSI 5MIN</div>", unsafe_allow_html=True)
        rows = ""
        for s in DASH_SYMBOLS:
            d = symbol_df(bars, s)
            if d.empty:
                rows += f"<div class='row'><b>{s}</b><span class='m'>sin datos</span></div>"
                continue
            last = float(d["close"].iloc[-1])
            day = d[d.index.date == d.index[-1].date()]
            prev = d[d.index.date < d.index[-1].date()]
            ref = float(prev["close"].iloc[-1]) if not prev.empty else float(day["open"].iloc[0])
            chg = (last / ref - 1) * 100
            r = calc_rsi(d["close"])
            rc = "r" if r and r > 70 else "g" if r and r < 30 else ""
            rows += (f"<div class='row'><b>{s}</b><span>${last:,.2f}</span>"
                     f"<span class='{'g' if chg >= 0 else 'r'}'>{chg:+.2f}%</span>"
                     f"<span class='{rc}'>RSI {r:.0f}</span></div>" if r else
                     f"<div class='row'><b>{s}</b><span>${last:,.2f}</span><span class='{'g' if chg >= 0 else 'r'}'>{chg:+.2f}%</span><span class='m'>RSI —</span></div>")
        st.markdown(rows, unsafe_allow_html=True)
    with st.container(border=True):
        st.markdown("<div class='ttl'>CUENTA</div>", unsafe_allow_html=True)
        if acc:
            pl = float(acc.equity) - float(acc.last_equity)
            st.markdown(f"<div class='row'><span>Equity</span><b>${float(acc.equity):,.2f}</b></div>"
                        f"<div class='row'><span>P/L hoy</span><b class='{'g' if pl >= 0 else 'r'}'>{pl:+,.2f}</b></div>"
                        f"<div class='row'><span>Cash</span><b>${float(acc.cash):,.2f}</b></div>"
                        f"<div class='row'><span>Posiciones</span><b>{len(positions)}/{MAX_POSITIONS}</b></div>",
                        unsafe_allow_html=True)

with colC:
    with st.container(border=True):
        sym = st.selectbox("Símbolo", DASH_SYMBOLS, index=5, label_visibility="collapsed")
        d = symbol_df(bars, sym)
        if not d.empty:
            r, a = calc_rsi(d["close"]), calc_atr(d)
            vol = d[d.index.date == d.index[-1].date()]["volume"].sum()
            ch = d["close"].reset_index()
            ch.columns = ["t", "close"]
            st.altair_chart(
                alt.Chart(ch).mark_line(color="#2EFB9B").encode(
                    x=alt.X("t:T", title=None),
                    y=alt.Y("close:Q", scale=alt.Scale(zero=False), title=None),
                ).properties(height=300),
                use_container_width=True,
            )
    with st.container(border=True):
        st.markdown("<div class='ttl'>POSICIONES REALES ALPACA</div>", unsafe_allow_html=True)
        if positions:
            data, total = [], 0.0
            for p in positions:
                pl = float(p.unrealized_pl)
                total += pl
                side = p.side.value if hasattr(p.side, "value") else str(p.side)
                data.append({"SYMBOL": p.symbol, "QTY": float(p.qty), "SIDE": side.upper(),
                             "ENTRY $": round(float(p.avg_entry_price), 2),
                             "P/L $": round(pl, 2), "P/L %": f"{float(p.unrealized_plpc) * 100:.2f}%"})
            st.dataframe(data, use_container_width=True, hide_index=True)
            st.markdown(f"P/L total flotante: <b class='{'g' if total >= 0 else 'r'}'>${total:+,.2f}</b>", unsafe_allow_html=True)
        else:
            st.info("Sin posiciones abiertas. AURA esperando señales a partir de las 10:00 AM NY.")

with colR:
    with st.container(border=True):
        st.markdown("<div class='ttl'>CONTROL DEL BOT</div>", unsafe_allow_html=True)
        if state["auto_entries"]:
            st.markdown(f"<div class='row'><span>Entradas automáticas</span><b class='g'>ACTIVAS</b></div>"
                        f"<div class='row'><span>Take profit / Stop loss</span><b>+{TAKE_PROFIT_PCT}% / -{STOP_LOSS_PCT}%</b></div>", unsafe_allow_html=True)
        else:
            if st.button("▶ Reanudar entradas", use_container_width=True):
                state["auto_entries"] = True
                log(state, "Entradas automáticas reanudadas")
                st.rerun()
        if st.button("🔴 CERRAR TODO - FLAT NOW", type="primary", use_container_width=True):
            try:
                close_everything()
                state["auto_entries"] = False
                log(state, "FLAT MANUAL - entradas pausadas")
                st.success("Cierre ejecutado con éxito.")
            except Exception as e:
                st.error(f"Error: {e}")
    with st.container(border=True):
        st.markdown("<div class='ttl'>LOG DEL BOT</div>", unsafe_allow_html=True)
        with state["lock"]:
            entries = list(state["log"])
        st.code("\n".join(reversed(entries[-12:])) if entries else "Sin eventos todavía")
