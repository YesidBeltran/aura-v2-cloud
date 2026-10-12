"""
AURA V7.5 - REST SIEMPRE GANA
Si REST da equity, muestra equity aunque TradingClient se cuelgue en Render
"""
import os, json, time, threading
from collections import deque
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import pytz, requests, pandas as pd
from flask import Flask, jsonify, render_template_string, request, abort

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest, GetOrdersRequest, TakeProfitRequest, StopLossRequest, GetPortfolioHistoryRequest
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass, QueryOrderStatus
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
    from alpaca.data.enums import DataFeed
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    ALPACA_OK=True
except Exception as e:
    ALPACA_OK=False

app=Flask(__name__)
_orig=requests.Session.request
def _req(self,*a,**k): k.setdefault("timeout",15); return _orig(self,*a,**k)
requests.Session.request=_req

def env(a,b,d=""): return os.environ.get(a,os.environ.get(b,d)).strip()
API_KEY=env("ALPACA_API_KEY","APCA_API_KEY_ID")
API_SECRET=env("ALPACA_SECRET_KEY","APCA_API_SECRET_KEY")
BASE_URL=env("ALPACA_BASE_URL","APCA_API_BASE_URL","https://paper-api.alpaca.markets")
IS_PAPER="paper" in BASE_URL.lower()
DASHBOARD_TOKEN=os.environ.get("DASHBOARD_TOKEN","").strip()
TELEGRAM_TOKEN=env("TELEGRAM_TOKEN","TELEGRAM_BOT_TOKEN","")
TELEGRAM_CHAT_ID=os.environ.get("TELEGRAM_CHAT_ID","").strip()
AUTO_TRADING=env("AUTO_TRADING","AUTO_TRADING","true").lower() in ["true","1","yes"]

SYMBOLS=["AAPL","TSLA","NVDA","MSFT","SPY","QQQ"]
TZ_COL=pytz.timezone("America/Bogota"); NY=pytz.timezone("America/New_York")

estado={"version":"V7.5 REST GANA","equity":0.0,"cash":0.0,"bp":0.0,"day_pl":0.0,"conectado":False,"running":False,"ultimo_error":None,"status":"Iniciando V7.5","detalles":{},"pos_det":[],"log":[],"chart":"","cambio":"","ultimo_analisis":"Nunca","ultimo_heartbeat":""}
trading=None; data_client=None; logs_hist=deque(maxlen=80)

def get_time(): return datetime.now(TZ_COL)
def log(m):
    e=f"[{get_time().strftime('%H:%M:%S')}] {m}"; logs_hist.append(e); print(e,flush=True)
def telegram(m):
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID): return
    threading.Thread(target=lambda: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",json={"chat_id":TELEGRAM_CHAT_ID,"text":m},timeout=8),daemon=True).start()
def require_token():
    if not DASHBOARD_TOKEN: return
    t=request.headers.get("X-Token") or request.args.get("token")
    if t!=DASHBOARD_TOKEN: abort(401)

def get_account_rest_direct():
    try:
        h={"APCA-API-KEY-ID":API_KEY,"APCA-API-SECRET-KEY":API_SECRET}
        r=requests.get(f"{BASE_URL}/v2/account",headers=h,timeout=10)
        if r.status_code==200:
            d=r.json(); return float(d.get('equity',0)),float(d.get('cash',0)),float(d.get('buying_power',0)),float(d.get('last_equity',0)),None
        else:
            return None,None,None,None,f"REST {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return None,None,None,None,f"REST {type(e).__name__}: {e}"

def cargar_clientes():
    global trading,data_client
    if not (API_KEY and API_SECRET):
        estado["ultimo_error"]="Faltan ALPACA_API_KEY/SECRET"; return False
    eq,cash,bp,last,rest_err=get_account_rest_direct()
    if eq is not None:
        estado["equity"],estado["cash"],estado["bp"],estado["day_pl"]=eq,cash,bp,eq-last
        estado["conectado"]=True; estado["ultimo_error"]=None
        log(f"💰 REST OK equity ${eq:.2f} -> dashboard ya en verde")
    else:
        estado["conectado"]=False; estado["ultimo_error"]=rest_err; log(f"❌ REST falló {rest_err}"); return False
    
    # Intentar TradingClient pero NO bloquear si falla
    try:
        def _t(): return TradingClient(API_KEY,API_SECRET,paper=IS_PAPER)
        def _d(): return StockHistoricalDataClient(API_KEY,API_SECRET)
        with ThreadPoolExecutor(max_workers=1) as ex: trading_tmp=ex.submit(_t).result(timeout=10)
        with ThreadPoolExecutor(max_workers=1) as ex: data_tmp=ex.submit(_d).result(timeout=10)
        trading,data_client=trading_tmp,data_tmp
        # Probar get_account con timeout corto
        def _acc(): return trading.get_account()
        with ThreadPoolExecutor(max_workers=1) as ex: acc=ex.submit(_acc).result(timeout=10)
        log(f"✅ TradingClient OK equity ${float(acc.equity):.2f}")
        return True
    except FutureTimeout:
        log("⚠️ TradingClient timeout 10s - pero REST ya mostró equity, sigo")
        estado["ultimo_error"]="TradingClient lento en Render, pero REST OK (dashboard funciona)"
        return True  # IMPORTANTE: retornar True para que escanear siga aunque TradingClient falle
    except Exception as e:
        log(f"⚠️ TradingClient err {type(e).__name__}: {str(e)[:200]} - REST sigue mostrando equity")
        estado["ultimo_error"]=f"TradingClient {type(e).__name__} pero REST OK ${estado['equity']:.2f}"
        return True

def escanear():
    # Si tenemos trading, escanear completo, si no, solo mantener conectado True por REST
    eq,cash,bp,last,_=get_account_rest_direct()
    if eq is not None:
        estado["equity"],estado["cash"],estado["bp"],estado["day_pl"]=eq,cash,bp,eq-last
        estado["conectado"]=True
    if trading is None: 
        estado["ultimo_analisis"]=get_time().strftime("%H:%M:%S")
        estado["detalles"]={s:{"precio":0,"rsi":"-","decision":"Modo REST - TradingClient reiniciando...","color":"#5B6B85"} for s in SYMBOLS}
        return
    try:
        posiciones={p.symbol:p for p in trading.get_all_positions()}
        estado["pos_det"]=[{"symbol":p.symbol,"side":"Corto" if "short" in str(getattr(p.side,"value",p.side)).lower() else "Largo","qty":str(p.qty),"entry":float(p.avg_entry_price),"price":float(p.current_price),"pl":float(p.unrealized_pl),"plpc":float(p.unrealized_plpc)*100} for p in posiciones.values()]
        # RSI simple sin bloquear
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        from alpaca.data.enums import DataFeed
        detalles={}
        for sym in SYMBOLS:
            try:
                req=StockBarsRequest(symbol_or_symbols=sym,timeframe=TimeFrame(5,TimeFrameUnit.Minute),start=datetime.now(pytz.utc)-timedelta(days=2),feed=DataFeed.IEX)
                bars=data_client.get_stock_bars(req).df
                df=bars.xs(sym,level=0) if hasattr(bars.index,'names') else bars
                if len(df)>20:
                    close=df["close"]; delta=close.diff(); gain=delta.clip(lower=0); loss=-delta.clip(upper=0)
                    avg_g=gain.ewm(alpha=1/14,adjust=False,min_periods=14).mean(); avg_l=loss.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
                    rs=avg_g/avg_l; rsi=100-100/(1+rs)
                    rv=float(rsi.iloc[-2]); price=float(close.iloc[-1])
                    if rv<40: dec,col="COMPRAR","#0B7A7A"
                    elif rv>65: dec,col="VENDER" if sym in posiciones else "SOBRECOMPRA","#6A2BA8"
                    else: dec,col="NEUTRAL","#5B6B85"
                    detalles[sym]={"precio":price,"rsi":round(rv,1),"decision":dec,"color":col}
                else:
                    detalles[sym]={"precio":0,"rsi":"-","decision":"Esperando datos...","color":"#5B6B85"}
            except Exception as e:
                detalles[sym]={"precio":0,"rsi":"-","decision":f"Err {type(e).__name__}","color":"#5B6B85"}
        estado["detalles"]=detalles
        estado["ultimo_analisis"]=get_time().strftime("%Y-%m-%d %H:%M:%S COL")
    except Exception as e:
        log(f"escanear err {e}")

def bot_loop():
    while estado["running"]:
        try:
            if not estado["conectado"]: cargar_clientes()
            else:
                # refrescar equity por REST cada ciclo (nunca deja $0)
                eq,cash,bp,last,_=get_account_rest_direct()
                if eq is not None:
                    estado["equity"],estado["cash"],estado["bp"],estado["day_pl"]=eq,cash,bp,eq-last
                    estado["conectado"]=True
                if trading: escanear()
            estado["ultimo_heartbeat"]=get_time().strftime("%H:%M:%S")
        except Exception as e:
            log(f"Loop {e}")
        time.sleep(60)

def iniciar_bot():
    if estado["running"]: return
    estado["running"]=True
    threading.Thread(target=bot_loop,daemon=True).start()
    log("✅ Bot V7.5 iniciado REST GANA")

HTML="""<!doctype html><html lang=es><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>AURA V7.5</title><style>@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;600;700&display=swap');body{margin:0;background:#F5F7FB;color:#0F1A2B;font-family:"DM Sans",sans-serif;padding:18px}.pill{font-size:11px;padding:5px 10px;border-radius:20px;border:1px solid #E3E9F2;background:#FFF}.ok{color:#0B7A7A;border-color:#B6E6D9;background:#E8F6F2}.bad{color:#B4235A;border-color:#F5B0C8;background:#FDF0F4}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-bottom:18px}.card{background:#FFF;border:1px solid #E3E9F2;border-radius:16px;padding:18px}.big{font-size:26px;font-weight:700}.sub{font-size:12px;color:#5B6B85}.logs{background:#0F1A2B;color:#C8D6E8;border-radius:12px;padding:14px;font-family:monospace;font-size:12px;max-height:300px;overflow-y:auto}</style></head><body><div style="max-width:1100px;margin:0 auto"><div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:18px"><div><div style="font-weight:700;font-size:18px">AURA SYNAPSE</div><div class=sub>V7.5 REST GANA • {{ 'PAPER' if paper else 'REAL' }} • {{ 'corriendo' if running else 'detenido' }}</div></div><div style="display:flex;gap:8px;flex-wrap:wrap"><span class="pill {{ 'ok' if paper else 'bad' }}">● {{ 'PAPER' if paper else 'REAL' }}</span><span class="pill {{ 'ok' if connected else 'bad' }}">● {{ 'Alpaca conectada' if connected else 'Alpaca sin conexión' }}</span><span class="pill {{ 'ok' if running else 'bad' }}">● {{ 'Bot corriendo' if running else 'off' }}</span></div></div>{% if last_error %}<div class=card style="border-color:#F5B0C8;background:#FDF0F4;margin-bottom:16px"><div style="color:#B4235A;font-weight:600">⚠️ {{ last_error }}</div></div>{% endif %}<div class=grid><div class=card><div class=sub>EQUITY TOTAL</div><div class=big>${{ "{:,.2f}".format(equity) }}</div><div class="sub {{ 'ok' if day_pl>=0 else 'bad' }}" style="color:{{ '#0B7A7A' if day_pl>=0 else '#B4235A' }}">{{ "%+.2f"|format(day_pl) }} hoy</div></div><div class=card><div class=sub>EFECTIVO</div><div class=big>${{ "{:,.2f}".format(cash) }}</div></div><div class=card><div class=sub>PODER COMPRA</div><div class=big>${{ "{:,.2f}".format(bp) }}</div></div><div class=card><div class=sub>POSICIONES</div><div class=big>{{ positions|length }} / 5</div><div class=sub>Pulso: {{ heartbeat or '—' }}</div></div></div><h3 style="font-size:14px">Activos vigilados · RSI (5 min)</h3><div class=grid>{% for s in symbols %}{% set d=detalles.get(s) %}<div class=card><div class=sub>{{ s }}</div>{% if d and d.rsi!='-' %}<div class=big>{{ d.rsi }}</div><div class=sub style="color:{{ d.color }};font-weight:600">{{ d.decision }} ${{ "%.2f"|format(d.precio) if d.precio else '' }}</div>{% else %}<div class=sub>{{ d.decision if d else 'Esperando datos...' }}</div>{% endif %}</div>{% endfor %}</div><h3 style="font-size:14px">Registro</h3><div class=logs>{% for l in logs %}<div>{{ l }}</div>{% else %}<div>Sin eventos</div>{% endfor %}</div></div><script>setTimeout(()=>location.reload(),15000)</script></body></html>"""

@app.route("/")
def home():
    require_token()
    return render_template_string(HTML,paper=IS_PAPER,running=estado["running"],connected=estado["conectado"],equity=estado["equity"],cash=estado["cash"],bp=estado["bp"],day_pl=estado["day_pl"],positions=estado["pos_det"],symbols=SYMBOLS,detalles=estado["detalles"],logs=list(reversed(list(logs_hist)))[-15:],heartbeat=estado["ultimo_heartbeat"],last_error=estado["ultimo_error"])

@app.route("/api/debug")
def debug():
    require_token()
    eq,cash,bp,last,err=get_account_rest_direct()
    return jsonify({"version":"V7.5","rest_equity":eq,"rest_error":err,"estado_equity":estado["equity"],"conectado":estado["conectado"],"running":estado["running"],"ultimo_error":estado["ultimo_error"],"has_keys":bool(API_KEY),"key_prefix":API_KEY[:4]+".."+API_KEY[-4:] if API_KEY else None,"base_url":BASE_URL,"logs":list(logs_hist)[-15:]})

@app.route("/health")
def health(): return "OK V7.5",200

iniciar_bot()
if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",10000)))
