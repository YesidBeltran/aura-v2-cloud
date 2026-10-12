import os, threading, time, requests, pytz
from collections import deque
from datetime import datetime, timedelta
from flask import Flask, jsonify, render_template_string, request, abort
try:
    from alpaca.trading.client import TradingClient
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    from alpaca.data.enums import DataFeed
    ALPACA_OK=True
except: ALPACA_OK=False
app=Flask(__name__)
def env(a,b,d=""): return os.environ.get(a,os.environ.get(b,d)).strip()
API_KEY=env("ALPACA_API_KEY","APCA_API_KEY_ID"); API_SECRET=env("ALPACA_SECRET_KEY","APCA_API_SECRET_KEY")
BASE_URL=env("ALPACA_BASE_URL","APCA_API_BASE_URL","https://paper-api.alpaca.markets")
IS_PAPER="paper" in BASE_URL.lower()
DASHBOARD_TOKEN=os.environ.get("DASHBOARD_TOKEN","").strip()
TELE_TOKEN=os.environ.get("TELEGRAM_BOT_TOKEN","").strip()
TELE_CHAT=os.environ.get("TELEGRAM_CHAT_ID","").strip()
TZ_COL=pytz.timezone("America/Bogota"); TZ_NY=pytz.timezone("America/New_York")
SYMBOLS=["AAPL","TSLA","NVDA","MSFT","SPY","QQQ"]
estado={"version":"V7.6 DUAL","equity":0.0,"cash":0.0,"bp":0.0,"day_pl":0.0,"conectado":False,"running":False,"ultimo_error":None,"detalles":{},"pos_det":[],"ultimo_analisis":"Nunca","ultimo_heartbeat":"","telegram_ok":False}
logs_hist=deque(maxlen=80)
def get_time(): return datetime.now(TZ_COL)
def log(m): e=f"[{get_time().strftime('%H:%M:%S')}] {m}"; logs_hist.append(e); print(e,flush=True)
def require_token():
    if not DASHBOARD_TOKEN: return
    t=request.headers.get("X-Token") or request.args.get("token")
    if t!=DASHBOARD_TOKEN: abort(401)
def send_telegram(msg):
    if not TELE_TOKEN or not TELE_CHAT: return False
    try:
        r=requests.get(f"https://api.telegram.org/bot{TELE_TOKEN}/sendMessage",params={"chat_id":TELE_CHAT,"text":msg},timeout=8)
        return r.status_code==200
    except Exception as e: log(f"TG fail {e}"); return False
def get_rest():
    try:
        h={"APCA-API-KEY-ID":API_KEY,"APCA-API-SECRET-KEY":API_SECRET}
        r=requests.get(f"{BASE_URL}/v2/account",headers=h,timeout=10)
        if r.status_code==200:
            d=r.json(); return float(d.get('equity',0)),float(d.get('cash',0)),float(d.get('buying_power',0)),float(d.get('last_equity',0)),None
        return None,None,None,None,f"REST {r.status_code}"
    except Exception as e: return None,None,None,None,f"{type(e).__name__}: {e}"
# HILO 1 - EQUITY NUNCA SE CONGELA
def hilo_equity():
    mercado_notificado=False
    while estado["running"]:
        try:
            eq,cash,bp,last,err=get_rest()
            if eq is not None:
                estado["equity"],estado["cash"],estado["bp"],estado["day_pl"]=eq,cash,bp,eq-last
                estado["conectado"]=True; estado["ultimo_error"]=None
                estado["ultimo_heartbeat"]=get_time().strftime('%H:%M:%S')
                # Detectar apertura 9:30 NY = 8:30 COL
                ny=datetime.now(TZ_NY)
                es_apertura = ny.weekday()<5 and ny.hour==9 and ny.minute in [30,31,32]
                if es_apertura and not mercado_notificado:
                    ok=send_telegram(f"🚀 AURA V7.6: Mercado abierto NY. Equity ${eq:,.2f}. Escaneando {', '.join(SYMBOLS)}... Dashboard: https://aura-synapse-yesid-l1nf.onrender.com/?token={DASHBOARD_TOKEN}")
                    log(f"🔔 Apertura notificada TG:{ok} Equity ${eq:.2f}")
                    estado["telegram_ok"]=ok
                    mercado_notificado=True
                if ny.hour!=9: mercado_notificado=False
            else:
                estado["ultimo_error"]=err; log(f"REST error {err}")
        except Exception as e: log(f"HiloEquity {e}")
        time.sleep(30)
# HILO 2 - RSI aparte, si se cuelga no afecta equity
def hilo_rsi():
    trading=None; data_client=None
    while estado["running"]:
        try:
            if ALPACA_OK and data_client is None:
                try:
                    trading=TradingClient(API_KEY,API_SECRET,paper=IS_PAPER)
                    data_client=StockHistoricalDataClient(API_KEY,API_SECRET)
                    log("✅ DataClient OK")
                except Exception as e: log(f"DataClient retry {e}"); time.sleep(30); continue
            if data_client:
                detalles={}
                for sym in SYMBOLS:
                    try:
                        req=StockBarsRequest(symbol_or_symbols=sym,timeframe=TimeFrame(5,TimeFrameUnit.Minute),start=datetime.now(pytz.utc)-timedelta(days=3),feed=DataFeed.IEX)
                        bars=data_client.get_stock_bars(req).df
                        df=bars.xs(sym,level=0) if hasattr(bars.index,'names') and 'symbol' in str(bars.index.names) else bars
                        if len(df)>20:
                            close=df["close"]; delta=close.diff(); gain=delta.clip(lower=0); loss=-delta.clip(upper=0)
                            ag=gain.ewm(alpha=1/14,adjust=False,min_periods=14).mean(); al=loss.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
                            rsi=100-100/(1+ag/al); rv=float(rsi.iloc[-2])
                            detalles[sym]={"precio":float(close.iloc[-1]),"rsi":round(rv,1),"decision":"COMPRAR" if rv<40 else "VENDER" if rv>65 else "NEUTRAL","color":"#0B7A7A" if rv<40 else "#6A2BA8" if rv>65 else "#5B6B85"}
                        else: detalles[sym]={"precio":0,"rsi":"-","decision":"Cargando...","color":"#5B6B85"}
                    except Exception as e: detalles[sym]={"precio":0,"rsi":"-","decision":f"Espera {type(e).__name__}","color":"#5B6B85"}
                estado["detalles"]=detalles; estado["ultimo_analisis"]=get_time().strftime("%H:%M:%S")
        except Exception as e: log(f"HiloRSI {e}")
        time.sleep(60)

eq,cash,bp,last,err=get_rest()
if eq is not None:
    estado["equity"],estado["cash"],estado["bp"],estado["day_pl"]=eq,cash,bp,eq-last; estado["conectado"]=True
    log(f"💰 INIT ${eq:.2f} - V7.6 DUAL listo")
def iniciar_bot():
    if estado["running"]: return
    estado["running"]=True
    threading.Thread(target=hilo_equity,daemon=True).start()
    threading.Thread(target=hilo_rsi,daemon=True).start()
    log("✅ Bot V7.6 iniciado - equity cada 30s + RSI aparte + TG apertura")
    if TELE_TOKEN and TELE_CHAT:
        send_telegram(f"✅ AURA V7.6 conectada. Equity ${estado['equity']:,.2f}. Bot corriendo en DUAL mode. Te aviso a las 8:30 AM COL apertura.")
HTML="""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>AURA V7.6</title><style>body{margin:0;background:#F5F7FB;color:#0F1A2B;font-family:sans-serif;padding:18px}.pill{font-size:11px;padding:5px 10px;border-radius:20px;border:1px solid #E3E9F2;background:#FFF}.ok{color:#0B7A7A;background:#E8F6F2}.bad{color:#B4235A;background:#FDF0F4}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}.card{background:#FFF;border:1px solid #E3E9F2;border-radius:16px;padding:18px}.big{font-size:26px;font-weight:700}.sub{font-size:12px;color:#5B6B85}.logs{background:#0F1A2B;color:#C8D6E8;border-radius:12px;padding:14px;font-family:monospace;font-size:12px;max-height:300px;overflow-y:auto}</style></head><body><div style="max-width:1100px;margin:0 auto"><div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:18px"><div><b>AURA SYNAPSE</b><div class=sub>V7.6 DUAL • PAPER • {{ 'TG OK' if tg_ok else 'TG sin config' }}</div></div><div style="display:flex;gap:8px"><span class="pill {{ 'ok' if connected else 'bad' }}">● {{ 'Alpaca conectada' if connected else 'sin conexión' }}</span><span class="pill ok">● ${{ "{:,.2f}".format(equity) }}</span></div></div>{% if last_error %}<div class=card style="background:#FDF0F4;border-color:#F5B0C8"><div style="color:#B4235A">{{ last_error }}</div></div>{% endif %}<div class=grid><div class=card><div class=sub>EQUITY (actualiza cada 30s)</div><div class=big>${{ "{:,.2f}".format(equity) }}</div><div class=sub style="color:{{ '#0B7A7A' if day_pl>=0 else '#B4235A' }}">{{ "%+.2f"|format(day_pl) }} hoy vs Alpaca $100,332.31</div></div><div class=card><div class=sub>EFECTIVO</div><div class=big>${{ "{:,.2f}".format(cash) }}</div></div><div class=card><div class=sub>PODER COMPRA</div><div class=big>${{ "{:,.2f}".format(bp) }}</div></div><div class=card><div class=sub>PULSO</div><div class=big>{{ heartbeat or '—' }}</div><div class=sub>{{ last_scan }}</div></div></div><h3>RSI 5min</h3><div class=grid>{% for s in symbols %}{% set d=detalles.get(s) %}<div class=card><div class=sub>{{ s }}</div>{% if d and d.rsi!='-' %}<div class=big>{{ d.rsi }}</div><div class=sub style="color:{{ d.color }}">{{ d.decision }} ${{ "%.2f"|format(d.precio) if d.precio else '' }}</div>{% else %}<div class=sub>{{ d.decision if d else 'Cargando DataClient...' }}</div>{% endif %}</div>{% endfor %}</div><h3>Log</h3><div class=logs>{% for l in logs %}<div>{{ l }}</div>{% endfor %}</div></div><script>setTimeout(()=>location.reload(),15000)</script></body></html>"""
@app.route("/")
def home():
    require_token(); return render_template_string(HTML,connected=estado["conectado"],equity=estado["equity"],cash=estado["cash"],bp=estado["bp"],day_pl=estado["day_pl"],symbols=SYMBOLS,detalles=estado["detalles"],logs=list(reversed(list(logs_hist)))[-20:],heartbeat=estado["ultimo_heartbeat"],last_scan=estado["ultimo_analisis"],last_error=estado["ultimo_error"],tg_ok=estado["telegram_ok"])
@app.route("/api/debug")
def debug():
    require_token(); eq,_,_,_,err=get_rest(); return jsonify({"version":"V7.6","rest_equity":eq,"rest_error":err,"estado_equity":estado["equity"],"conectado":estado["conectado"],"running":estado["running"],"telegram_ok":bool(TELE_TOKEN and TELE_CHAT),"logs":list(logs_hist)[-20:]})
@app.route("/health")
def health(): return "OK V7.6",200
iniciar_bot()
if __name__=="__main__": app.run(host="0.0.0.0",port=int(os.environ.get("PORT",10000)))
