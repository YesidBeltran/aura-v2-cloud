"""
AURA SYNAPSE V7.4 FINAL - NUNCA MAS $0.00
Fixes:
- get_portfolio_history AttributeError parcheado (no tumba bot)
- Si REST funciona, equity se muestra aunque TradingClient falle
- Muestra ultimo_error en dashboard para diagnosticar
"""
import os, json, time, threading
from collections import deque
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import pytz, requests, pandas as pd
from flask import Flask, jsonify, render_template_string, request, abort

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (MarketOrderRequest, LimitOrderRequest, GetOrdersRequest,
                                         TakeProfitRequest, StopLossRequest, GetPortfolioHistoryRequest)
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass, QueryOrderStatus
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
    from alpaca.data.enums import DataFeed
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    ALPACA_OK = True
except Exception as e:
    ALPACA_OK = False
    print(f"Alpaca import fail: {e}", flush=True)

app = Flask(__name__)
_orig = requests.Session.request
def _req_timeout(self, *a, **k):
    k.setdefault("timeout", 15)
    return _orig(self, *a, **k)
requests.Session.request = _req_timeout

def env(k1,k2,default=""):
    return os.environ.get(k1, os.environ.get(k2, default)).strip()

API_KEY = env("ALPACA_API_KEY","APCA_API_KEY_ID") or os.environ.get("APCA_API_KEY","").strip()
API_SECRET = env("ALPACA_SECRET_KEY","APCA_API_SECRET_KEY") or os.environ.get("APCA_API_SECRET","").strip()
BASE_URL = env("ALPACA_BASE_URL","APCA_API_BASE_URL","https://paper-api.alpaca.markets")
IS_PAPER = "paper" in BASE_URL.lower()
AUTO_TRADING = env("AUTO_TRADING","AUTO_TRADING","true").lower() in ["true","1","yes"]
AUTO_START = env("AUTO_START","AUTO_START","true").lower() in ["true","1","yes"]
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN","").strip()
SHEET_ID = env("GOOGLE_SHEET_ID","SHEET_ID","")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS_JSON","").strip()
SHEETS_ENABLED = env("SHEETS_ENABLED","SHEETS_ENABLED","false").lower() in ["true","1","yes"]
TELEGRAM_TOKEN = env("TELEGRAM_TOKEN","TELEGRAM_BOT_TOKEN","")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID","").strip()

SYMBOLS = ["AAPL","TSLA","NVDA","MSFT","SPY","QQQ"]
VELA_MIN=5; RSI_PERIODO=14; RSI_BUY=40; RSI_SELL=65; TREND_BARS=780
RIESGO_POR_TRADE=0.001; ATR_MULT=1.0; SL_MIN,SL_MAX=1.5,4.0; RR=1.5; SL_DEFECTO,TP_DEFECTO=2.0,3.0
AUTO_PROTEGER = True
PCT_POR_TRADE=0.05; MAX_POSICIONES=5; MAX_COMPRAS_POR_ESCANEO=2; MAX_PERDIDA_DIA=3.0
MIN_SIN_OPERAR_APERTURA=15; MIN_PARA_CIERRE=15; COOLDOWN_MIN=60
INTERVALO_ABIERTO=60; INTERVALO_CERRADO=180

TZ_COL=pytz.timezone("America/Bogota"); NY=pytz.timezone("America/New_York")

estado={"version":"V7.4 FINAL","status":"Iniciando V7.4...","equity":0.0,"cash":0.0,
"ultimo_analisis":"Nunca","ultimo_heartbeat":"","log":[],"detalles":{},"posiciones":[],
"vive_desde":datetime.now(TZ_COL).isoformat(),"trades_hoy":0,"fecha_trades":None,
"auto_trading":AUTO_TRADING,"google_sheet":False,"google_status":"Off",
"mercado_abierto":False,"pausa_riesgo":False,"running":False,"conectado":False,"ultimo_error":None,
"bp":0.0,"day_pl":0.0,"pos_det":[],"chart":"","cambio":""}
trading=None; data_client=None; logs_hist=deque(maxlen=80); ultima_compra={}; conocidas={}
ventas_propias=set(); fallos={"n":0,"avisado":False,"pausa_fecha":None}; protegidas=set()
avisadas=set(); atr_cache={}; _chart={"t":0.0}; _lock_inicio=threading.Lock()

def get_time(): return datetime.now(TZ_COL)
def log(msg):
    entry=f"[{get_time().strftime('%H:%M:%S')}] {msg}"
    logs_hist.append(entry); print(entry,flush=True)
def telegram(msg):
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID): return
    def _send():
        try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",json={"chat_id":TELEGRAM_CHAT_ID,"text":msg},timeout=8)
        except: pass
    threading.Thread(target=_send,daemon=True).start()
def alerta(m): log(m); telegram(m)
def val(x): return str(getattr(x,"value",x)).lower()

def require_token():
    if not DASHBOARD_TOKEN: return
    token=request.headers.get("X-Token") or request.args.get("token")
    if token!=DASHBOARD_TOKEN: abort(401)

def get_account_rest_direct():
    """REST directo - siempre funciona aunque alpaca-py falle"""
    try:
        headers={"APCA-API-KEY-ID":API_KEY,"APCA-API-SECRET-KEY":API_SECRET}
        r=requests.get(f"{BASE_URL}/v2/account",headers=headers,timeout=10)
        log(f"REST /v2/account status: {r.status_code}")
        if r.status_code==200:
            d=r.json()
            eq=float(d.get('equity',0)); cash=float(d.get('cash',0)); bp=float(d.get('buying_power',0)); last=float(d.get('last_equity',eq))
            log(f"REST OK equity ${eq:.2f} cash ${cash:.2f}")
            return eq,cash,bp,last,None
        else:
            err=f"REST {r.status_code}: {r.text[:300]}"
            log(err)
            return None,None,None,None,err
    except Exception as e:
        err=f"REST exc {type(e).__name__}: {e}"
        log(err)
        return None,None,None,None,err

def rsi_wilder(close,period=14):
    delta=close.diff(); gain=delta.clip(lower=0); loss=-delta.clip(upper=0)
    avg_gain=gain.ewm(alpha=1/period,adjust=False,min_periods=period).mean()
    avg_loss=loss.ewm(alpha=1/period,adjust=False,min_periods=period).mean()
    rs=avg_gain/avg_loss.where(avg_loss!=0)
    rsi=100-100/(1+rs); return rsi.where(avg_loss!=0,100.0)

def atr_diario(sym):
    hoy=datetime.now(NY).date(); c=atr_cache.get(sym)
    if c and c[0]==hoy: return c[1]
    try:
        req=StockBarsRequest(symbol_or_symbols=sym,timeframe=TimeFrame.Day,start=datetime.now(pytz.utc)-timedelta(days=60),feed=DataFeed.IEX)
        d=data_client.get_stock_bars(req).df
        d=d.xs(sym,level=0) if isinstance(d.index,pd.MultiIndex) else d
        pc=d["close"].shift()
        tr=pd.concat([d["high"]-d["low"],(d["high"]-pc).abs(),(d["low"]-pc).abs()],axis=1).max(axis=1)
        atr=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean().iloc[-1]
        pct=float(atr/d["close"].iloc[-1]*100)
        if pct!=pct: return None
        atr_cache[sym]=(hoy,pct); return pct
    except: return None

def analizar(sym):
    try:
        req=StockBarsRequest(symbol_or_symbols=sym,timeframe=TimeFrame(VELA_MIN,TimeFrameUnit.Minute),start=datetime.now(pytz.utc)-timedelta(days=20),feed=DataFeed.IEX)
        bars=data_client.get_stock_bars(req).df
        if bars is None or bars.empty: return None
        df=bars.xs(sym,level=0) if isinstance(bars.index,pd.MultiIndex) else bars
        idx=df.index.tz_convert(NY); mins=idx.hour*60+idx.minute
        df=df[(mins>=570)&(mins<960)]
        if len(df)<30: return None
        rsi=rsi_wilder(df["close"]).iloc[-2]
        if rsi!=rsi: return None
        tendencia_ok=None
        if len(df)>=TREND_BARS+3:
            sma=df["close"].rolling(TREND_BARS).mean().iloc[-2]
            if sma==sma: tendencia_ok=bool(df["close"].iloc[-2]>sma)
        return {"precio":float(df["close"].iloc[-1]),"rsi":float(rsi),"tendencia_ok":tendencia_ok,"atr_pct":atr_diario(sym)}
    except Exception as e: 
        log(f"analizar {sym} err {e}")
        return None

def parametros_riesgo(atr_pct):
    if atr_pct is None: return SL_DEFECTO,TP_DEFECTO
    sl=min(max(atr_pct*ATR_MULT,SL_MIN),SL_MAX); return round(sl,2),round(sl*RR,2)

def mercado_info():
    try:
        clock=trading.get_clock()
        return clock.is_open,0,0
    except:
        bog=get_time(); abierto=bog.weekday()<5 and (8.5<=bog.hour+bog.minute/60<=15.0)
        return abierto,0,0

def cargar_clientes():
    global trading, data_client
    if not (API_KEY and API_SECRET):
        estado["ultimo_error"]="FALTAN ALPACA_API_KEY y SECRET en Render"; log("❌ Faltan keys"); return False
    if not ALPACA_OK:
        estado["ultimo_error"]="alpaca-py no instalado"; return False
    try:
        log(f"Conectando Alpaca Key {API_KEY[:4]}..{API_KEY[-4:]} Paper:{IS_PAPER} URL:{BASE_URL}")
        eq,cash,bp,last,rest_err=get_account_rest_direct()
        if eq is not None:
            # ¡Equity ya disponible por REST, lo mostramos aunque TradingClient falle después!
            estado["equity"],estado["cash"],estado["bp"],estado["day_pl"]=eq,cash,bp,eq-last
            estado["conectado"]=True
            estado["ultimo_error"]=None
            log(f"💰 Equity por REST: ${eq:.2f}")
        else:
            estado["ultimo_error"]=rest_err or "REST falló"
            log(f"REST falló, intentando TradingClient igual...")
        
        def _crear_trading():
            return TradingClient(API_KEY,API_SECRET,paper=IS_PAPER)
        def _crear_data():
            return StockHistoricalDataClient(API_KEY,API_SECRET)
        
        with ThreadPoolExecutor(max_workers=1) as ex:
            trading_t=ex.submit(_crear_trading).result(timeout=20)
        log("TradingClient creado OK")
        with ThreadPoolExecutor(max_workers=1) as ex:
            data_t=ex.submit(_crear_data).result(timeout=20)
        log("DataClient creado OK")
        
        def _get_acc():
            return trading_t.get_account()
        with ThreadPoolExecutor(max_workers=1) as ex:
            cuenta=ex.submit(_get_acc).result(timeout=20)
        
        trading, data_client = trading_t, data_t
        estado["equity"],estado["cash"]=float(cuenta.equity),float(cuenta.cash)
        estado["bp"]=float(cuenta.buying_power); estado["day_pl"]=estado["equity"]-float(cuenta.last_equity)
        estado["conectado"]=True; estado["ultimo_error"]=None
        log(f"✅ V7.4 conectada equity ${estado['equity']:.2f}")
        telegram(f"🚀 AURA V7.4 conectada ${estado['equity']:.2f}")
        return True
    except FutureTimeout:
        estado["ultimo_error"]="Timeout Alpaca (20s) - Render lento, reintentando..."
        log("Timeout TradingClient"); return False
    except Exception as e:
        err=f"{type(e).__name__}: {str(e)[:400]}"
        estado["ultimo_error"]=err
        log(f"❌ Error Alpaca: {err}")
        return False

def escanear():
    if trading is None: return
    cuenta=trading.get_account()
    estado["equity"],estado["cash"]=float(cuenta.equity),float(cuenta.cash)
    estado["bp"]=float(cuenta.buying_power); estado["day_pl"]=estado["equity"]-float(cuenta.last_equity)
    posiciones={p.symbol:p for p in trading.get_all_positions()}
    abiertas=trading.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN,limit=100))
    con_orden={o.symbol for o in abiertas}
    estado["posiciones"]=[{"symbol":p.symbol,"qty":float(p.qty),"market_value":float(p.market_value),"avg_entry":float(p.avg_entry_price)} for p in posiciones.values()]
    estado["pos_det"]=[{"symbol":p.symbol,"side":"Corto" if "short" in val(p.side) else "Largo","qty":p.qty,"entry":float(p.avg_entry_price),"price":float(p.current_price),"pl":float(p.unrealized_pl),"plpc":float(p.unrealized_plpc)*100} for p in posiciones.values()]
    estado["conectado"]=True; estado["ultimo_error"]=None
    abierto,_,_=mercado_info(); estado["mercado_abierto"]=abierto
    detalles,logs={},[]
    for sym in SYMBOLS:
        a=analizar(sym)
        if a is None:
            detalles[sym]={"precio":0,"rsi":"-","decision":"Esperando datos…","color":"#5B6B85","tendencia_ok":None,"volatilidad_pct":None}; continue
        rsi=a["rsi"]; tend=a["tendencia_ok"]; en_pos=sym in posiciones
        if rsi<RSI_BUY and tend: dec,color,action=("COMPRAR FUERTE" if rsi<30 else "COMPRAR"),"#0B7A7A","BUY"
        elif rsi<RSI_BUY: dec,color,action="NO COMPRA: bajista" if tend is False else "NO COMPRA","#9A6200","HOLD"
        elif rsi>65 and en_pos: dec,color,action=("VENDER FUERTE" if rsi>75 else "VENDER"),"#6A2BA8","SELL"
        elif rsi>65: dec,color,action="SOBRECOMPRA","#6A2BA8","HOLD"
        else: dec,color,action="NEUTRAL","#5B6B85","HOLD"
        detalles[sym]={"precio":a["precio"],"rsi":round(rsi,1),"decision":dec,"color":color,"action":action,"tendencia_ok":tend,"volatilidad_pct":round(a["atr_pct"],2) if a["atr_pct"] else None}
        logs.append(f"{sym} ${a['precio']:.2f} RSI {rsi:.1f} -> {dec}")
    estado["detalles"]=detalles; estado["log"]=logs[:20]; estado["ultimo_analisis"]=get_time().strftime("%Y-%m-%d %H:%M:%S COL")
    estado["status"]=f"Activa • {'PAPER' if IS_PAPER else 'REAL'} • Auto:{'ON' if AUTO_TRADING else 'OFF'}"
    # Chart con protección total
    try:
        if hasattr(trading, 'get_portfolio_history'):
            h=trading.get_portfolio_history(GetPortfolioHistoryRequest(period="1M",timeframe="1D"))
            pares=[(float(v),t) for v,t in zip(h.equity,h.timestamp) if v is not None]
            vals=[v for v,_ in pares]; fechas=[datetime.fromtimestamp(t,NY).strftime("%d/%m") for _,t in pares]
            if len(vals)>=2:
                def svg_linea(vals,fechas,w=720,h=220):
                    lo,hi=min(vals),max(vals); rng=max(hi-lo,hi*0.005) or 1.0; mid=(hi+lo)/2; top,bot=mid+rng/2,mid-rng/2
                    x0,x1,y0,y1=78,w-14,14,h-34
                    px=lambda i: x0+i*(x1-x0)/(len(vals)-1); py=lambda v: y0+(top-v)/rng*(y1-y0)
                    pts=" ".join(f"{px(i):.1f},{py(v):.1f}" for i,v in enumerate(vals))
                    color="#0E8C8C" if vals[-1]>=vals[0] else "#6A2BA8"
                    t='font-size="10" fill="#5B6B85" font-family="DM Sans,Arial,sans-serif"'
                    return f'<svg viewBox="0 0 {w} {h}" style="width:100%;height:auto;display:block"><defs><linearGradient id="g" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="{color}" stop-opacity=".22"/><stop offset="1" stop-color="{color}" stop-opacity="0"/></linearGradient></defs><text x="{x0-8}" y="{py(hi)+3:.1f}" text-anchor="end" {t}>${hi:,.0f}</text><text x="{x0-8}" y="{py(lo)+3:.1f}" text-anchor="end" {t}>${lo:,.0f}</text><text x="{x0}" y="{h-10}" {t}>{fechas[0]}</text><text x="{x1}" y="{h-10}" text-anchor="end" {t}>{fechas[-1]}</text><polygon points="{x0},{y1} {pts} {x1},{y1}" fill="url(#g)"/><polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/></svg>'
                estado["chart"]=svg_linea(vals,fechas)
                if len(vals)>=2 and vals[0]:
                    d=vals[-1]-vals[0]; estado["cambio"]=f"{d:+,.2f} USD ({d/vals[0]*100:+.2f}%) desde {fechas[0]}"
    except AttributeError:
        log("Chart: get_portfolio_history no existe en esta version alpaca-py, skip")
    except Exception as e:
        log(f"Chart err {e}")

def bot_loop():
    while estado["running"]:
        pausa=180
        try:
            if trading is None and not cargar_clientes():
                time.sleep(60); continue
            estado["ultimo_heartbeat"]=get_time().strftime("%H:%M:%S")
            escanear()
            pausa=60 if estado["mercado_abierto"] else 180
        except Exception as e:
            log(f"Loop err {type(e).__name__}: {e}"); estado["ultimo_error"]=f"{type(e).__name__}: {str(e)[:200]}"; pausa=60
        time.sleep(pausa)

def iniciar_bot():
    if estado["running"]: return False
    estado["running"]=True
    threading.Thread(target=bot_loop,daemon=True).start()
    log("✅ Bot V7.4 iniciado"); return True

HTML="""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AURA V7.4</title><style>@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;600;700&display=swap');:root{--bg:#F5F7FB;--card:#FFFFFF;--bd:#E3E9F2;--tx:#0F1A2B;--mut:#5B6B85;--ok:#0B7A7A;--bad:#B4235A}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font-family:"DM Sans",system-ui,sans-serif;padding:18px}header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:18px}.pill{font-size:11px;padding:5px 10px;border-radius:20px;border:1px solid var(--bd);background:var(--card)}.pill.ok{color:var(--ok);border-color:#B6E6D9;background:#E8F6F2}.pill.bad{color:var(--bad);border-color:#F5B0C8;background:#FDF0F4}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-bottom:18px}.card{background:var(--card);border:1px solid var(--bd);border-radius:16px;padding:18px}.card h3{margin:0 0 6px;font-size:11px;letter-spacing:1px;text-transform:uppercase;color:var(--mut)}.big{font-size:26px;font-weight:700;margin:4px 0}.sub{font-size:12px;color:var(--mut);line-height:1.4}.ok{color:var(--ok)}.bad{color:var(--bad)}.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:13px}th{font-size:11px;text-align:left;color:var(--mut);padding:8px 6px;border-bottom:1px solid var(--bd)}td{padding:8px 6px;border-bottom:1px solid #F0F3F9}.logs{background:#0F1A2B;color:#C8D6E8;border-radius:12px;padding:14px;font-family:monospace;font-size:12px;max-height:320px;overflow-y:auto}.logs div{padding:3px 0;border-bottom:1px solid #1E2A3F}</style></head><body><div style="max-width:1100px;margin:0 auto"><header><div><div style="font-weight:700;font-size:18px">AURA SYNAPSE</div><div class="sub">V7.4 FINAL • {{ 'PAPER' if paper else 'REAL' }} • {{ 'corriendo' if running else 'detenido' }}</div></div><div style="display:flex;gap:8px;flex-wrap:wrap"><span class="pill {{ 'ok' if paper else 'bad' }}">● {{ 'PAPER' if paper else 'REAL' }}</span><span class="pill {{ 'ok' if connected else 'bad' }}">● {{ 'Alpaca conectada' if connected else 'Alpaca sin conexión' }}</span><span class="pill {{ 'ok' if running else 'bad' }}">● {{ 'Bot corriendo' if running else 'detenido' }}</span><span class="pill">● Compras {{ 'ON' if trading else 'OFF' }}</span><span class="pill">● Telegram {{ 'on' if telegram else 'off' }}</span></div></header>{% if last_error %}<div class="card" style="margin-bottom:16px;border-color:#F5B0C8;background:#FDF0F4"><div class="bad">⚠️ {{ last_error }}</div><div class="sub" style="margin-top:6px">Revisa /api/debug?token=... - Si dice 401, regenera tu key en Alpaca</div></div>{% endif %}<div class="grid"><div class="card"><h3>Equity total</h3><p class="big">${{ "{:,.2f}".format(equity) }}</p><div class="sub {{ 'ok' if day_pl >= 0 else 'bad' }}">{{ "%+.2f"|format(day_pl) }} hoy</div></div><div class="card"><h3>Efectivo</h3><p class="big">${{ "{:,.2f}".format(cash) }}</p></div><div class="card"><h3>Poder compra</h3><p class="big">${{ "{:,.2f}".format(bp) }}</p></div><div class="card"><h3>Posiciones</h3><p class="big">{{ positions|length }} / 5</p><div class="sub">Escaneo: {{ last_scan or '—' }} · Pulso: {{ heartbeat or '—' }}</div></div></div><div class="card" style="margin-bottom:16px"><h2 style="margin:0 0 8px;font-size:16px">Evolución equity · último mes</h2>{% if cambio %}<p class="sub" style="margin:-8px 0 12px">{{ cambio }}</p>{% endif %}{% if chart %}{{ chart|safe }}{% else %}<p class="sub">Aún no hay historial suficiente (o versión alpaca-py nueva).</p>{% endif %}</div><div class="card" style="margin-bottom:16px"><h2 style="margin:0 0 12px;font-size:16px">Posiciones abiertas</h2>{% if positions %}<div class="scroll"><table><tr><th>Símbolo</th><th>Lado</th><th>Cant</th><th>Entrada</th><th>Actual</th><th>P/L</th></tr>{% for p in positions %}<tr><td><strong>{{ p.symbol }}</strong></td><td>{{ p.side }}</td><td>{{ p.qty }}</td><td>${{ "%.2f"|format(p.entry) }}</td><td>${{ "%.2f"|format(p.price) }}</td><td class="{{ 'ok' if p.pl >= 0 else 'bad' }}">{{ "%+.2f"|format(p.pl) }} ({{ "%+.2f"|format(p.plpc) }}%)</td></tr>{% endfor %}</table></div>{% else %}<p class="sub">Sin posiciones abiertas.</p>{% endif %}</div><h2 style="font-size:14px">Activos vigilados · RSI (5 min)</h2><div class="grid">{% for s in symbols %}{% set d = detalles.get(s) %}<div class="card"><h3>{{ s }}{% if s in held %} · en posición{% endif %}</h3>{% if d and d.rsi != '-' %}<p class="big">{{ d.rsi }}</p><div class="sub" style="color:{{ d.color }};font-weight:600">{{ d.decision }}</div><div class="sub">Tendencia {{ 'alcista' if d.tendencia_ok else ('bajista' if d.tendencia_ok is false else 'sin datos') }}{% if d.volatilidad_pct %} · {{ d.volatilidad_pct }}%{% endif %}</div>{% else %}<p class="sub">Esperando datos… {{ d.decision if d else '' }}</p>{% endif %}</div>{% endfor %}</div><h2 style="margin-top:12px;font-size:14px">Registro</h2><div class="logs">{% for l in logs %}<div>{{ l }}</div>{% else %}<div>Sin eventos</div>{% endfor %}</div></div><script>setTimeout(()=>location.reload(), {{ 30000 if connected else 7000 }})</script></body></html>"""

@app.route("/")
def home():
    require_token()
    return render_template_string(HTML,connected=estado["conectado"],paper=IS_PAPER,running=estado["running"],trading=AUTO_TRADING,
    last_scan=estado["ultimo_analisis"] if estado["ultimo_analisis"]!="Nunca" else None,heartbeat=estado["ultimo_heartbeat"],last_error=estado["ultimo_error"],
    equity=estado["equity"],cash=estado["cash"],bp=estado["bp"],day_pl=estado["day_pl"],positions=estado["pos_det"],
    held={p["symbol"] for p in estado["pos_det"]},symbols=SYMBOLS,detalles=dict(estado["detalles"]),logs=list(reversed(list(logs_hist)))[:15],telegram=bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID))

@app.route("/api")
def api():
    require_token()
    return jsonify(estado)

@app.route("/api/debug")
def debug():
    require_token()
    eq,cash,_,_,rest_err=get_account_rest_direct()
    return jsonify({"version":"V7.4","running":estado["running"],"conectado":estado["conectado"],"has_keys":bool(API_KEY),"key_prefix":API_KEY[:4]+".."+API_KEY[-4:] if API_KEY else None,
    "base_url":BASE_URL,"is_paper":IS_PAPER,"rest_equity":eq,"rest_error":rest_err,"ultimo_error":estado["ultimo_error"],"logs":list(logs_hist)[-20:]})

@app.route("/health")
def health(): return "OK V7.4",200

if AUTO_START: iniciar_bot()
if __name__=="__main__":
    port=int(os.environ.get("PORT",10000)); app.run(host="0.0.0.0",port=port)
