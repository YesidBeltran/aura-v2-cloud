"""
AURA SYNAPSE V7.2 ANTI-CUELGUE - Fix definitivo $0.00
- No se cuelga en get_account()
- Timeout 25s con ThreadPool
- Fallback REST directo para diagnosticar keys
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
    print(f"Alpaca import error: {e}", flush=True)
    ALPACA_OK = False

app = Flask(__name__)

# Timeout global requests 15s
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

SYMBOLS = ["AAPL","TSLA","NVDA","MSFT","SPY"]
VELA_MIN=5; RSI_PERIODO=14; RSI_BUY=40; RSI_SELL=65; TREND_BARS=780
RIESGO_POR_TRADE=0.001; ATR_MULT=1.0; SL_MIN,SL_MAX=1.5,4.0; RR=1.5; SL_DEFECTO,TP_DEFECTO=2.0,3.0
AUTO_PROTEGER = env("AUTO_PROTEGER","AUTO_PROTEGER","true").lower() in ["true","1","yes"]
PCT_POR_TRADE=0.05; MAX_POSICIONES=5; MAX_COMPRAS_POR_ESCANEO=2; MAX_PERDIDA_DIA=3.0
MIN_SIN_OPERAR_APERTURA=15; MIN_PARA_CIERRE=15; COOLDOWN_MIN=60
INTERVALO_ABIERTO=60; INTERVALO_CERRADO=180

TZ_COL=pytz.timezone("America/Bogota"); NY=pytz.timezone("America/New_York")

estado={"version":"V7.2 ANTI-CUELGUE","status":"Iniciando V7.2...","equity":0.0,"cash":0.0,
"ultimo_analisis":"Nunca","ultimo_heartbeat":"","log":[],"detalles":{},"posiciones":[],
"vive_desde":datetime.now(TZ_COL).isoformat(),"trades_hoy":0,"fecha_trades":None,
"auto_trading":AUTO_TRADING,"google_sheet":bool(SHEETS_ENABLED and SHEET_ID and GOOGLE_CREDS_JSON),
"google_status":"Desactivado" if not SHEETS_ENABLED else "No configurado","google_detalle":None,
"mercado_abierto":False,"pausa_riesgo":False,"running":False,"conectado":False,"ultimo_error":None,
"bp":0.0,"day_pl":0.0,"pos_det":[],"chart":"","cambio":""}
trading=None; data_client=None; logs_hist=deque(maxlen=60); ultima_compra={}; conocidas={}
ventas_propias=set(); fallos={"n":0,"avisado":False,"pausa_fecha":None}; protegidas=set()
avisadas=set(); atr_cache={}; _chart={"t":0.0}; _lock_inicio=threading.Lock()
_lock_hoja=threading.Lock(); _hoja={"ws":None}

def get_time(): return datetime.now(TZ_COL)
def log(msg):
    entry=f"[{get_time().strftime('%H:%M:%S')}] {msg}"
    logs_hist.append(entry); print(entry,flush=True)
def telegram(msg):
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID): return
    def _send():
        try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",json={"chat_id":TELEGRAM_CHAT_ID,"text":msg},timeout=8)
        except Exception as e: print(f"Telegram err: {type(e).__name__}",flush=True)
    threading.Thread(target=_send,daemon=True).start()
def alerta(m): log(m); telegram(m)
def val(x): return str(getattr(x,"value",x)).lower()
HEADERS=["fecha_hora_col","evento","symbol","lado","qty","precio","rsi","tendencia_alcista","volatilidad_pct","sl_pct","tp_pct","resultado_pct","motivo_cierre","equity","cash","mensaje","trade_n_hoy"]
def sheets_activo(): return bool(SHEETS_ENABLED and SHEET_ID and GOOGLE_CREDS_JSON)
def _obtener_hoja():
    if _hoja["ws"] is not None: return _hoja["ws"]
    import gspread
    if not GOOGLE_CREDS_JSON: raise ValueError("GOOGLE_CREDS_JSON vacío")
    creds_dict=json.loads(GOOGLE_CREDS_JSON)
    client=gspread.service_account_from_dict(creds_dict)
    sh=client.open_by_key(SHEET_ID)
    try: ws=sh.worksheet("Trades")
    except gspread.WorksheetNotFound: ws=sh.add_worksheet("Trades",rows=1000,cols=len(HEADERS))
    if not ws.row_values(1): ws.append_row(HEADERS)
    _hoja["ws"]=ws; return ws
def send_to_sheets(row):
    if not sheets_activo(): return
    def _escribir():
        try:
            with _lock_hoja: _obtener_hoja().append_row(row,value_input_option="USER_ENTERED")
            estado["google_status"]="Conectado"
        except Exception as e:
            _hoja["ws"]=None; estado["google_status"]="Error al escribir"; estado["google_detalle"]=f"{type(e).__name__}: {str(e)[:250]}"
    threading.Thread(target=_escribir,daemon=True).start()
def init_sheet():
    if not sheets_activo(): estado["google_status"]="Desactivado - Trading OK"; return
    try:
        with _lock_hoja: n=len(_obtener_hoja().get_all_values())-1
        estado["google_status"]=f"Conectado ({max(n,0)} registros)"; log("Google Sheets conectado")
    except Exception as e: _hoja["ws"]=None; estado["google_status"]="Error conexión"; estado["google_detalle"]=f"{type(e).__name__}: {str(e)[:250]}"
def fila(evento,symbol,**d):
    d.update(evento=evento,symbol=symbol,fecha_hora_col=get_time().strftime("%Y-%m-%d %H:%M:%S"),equity=f"{estado['equity']:.2f}",cash=f"{estado['cash']:.2f}",trade_n_hoy=estado["trades_hoy"])
    return [d.get(h,"") for h in HEADERS]
def require_token():
    if not DASHBOARD_TOKEN: return
    token=request.headers.get("X-Token") or request.args.get("token")
    if token!=DASHBOARD_TOKEN: abort(401)

def rsi_wilder(close,period=RSI_PERIODO):
    delta=close.diff(); gain=delta.clip(lower=0); loss=-delta.clip(upper=0)
    avg_gain=gain.ewm(alpha=1/period,adjust=False,min_periods=period).mean()
    avg_loss=loss.ewm(alpha=1/period,adjust=False,min_periods=period).mean()
    rs=avg_gain/loss.where(loss!=0) if False else avg_gain/avg_loss.where(avg_loss!=0)
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
    except Exception as e: log(f"Error volatilidad {sym}: {e}"); return None

def analizar(sym):
    try:
        req=StockBarsRequest(symbol_or_symbols=sym,timeframe=TimeFrame(VELA_MIN,TimeFrameUnit.Minute),start=datetime.now(pytz.utc)-timedelta(days=20),feed=DataFeed.IEX)
        bars=data_client.get_stock_bars(req).df
        if bars is None or bars.empty: return None
        df=bars.xs(sym,level=0) if isinstance(bars.index,pd.MultiIndex) else bars
        idx=df.index.tz_convert(NY); mins=idx.hour*60+idx.minute
        df=df[(mins>=570)&(mins<960)]
        if len(df)<RSI_PERIODO*2: return None
        rsi=rsi_wilder(df["close"]).iloc[-2]
        if rsi!=rsi: return None
        tendencia_ok=None
        if len(df)>=TREND_BARS+3:
            sma=df["close"].rolling(TREND_BARS).mean().iloc[-2]
            if sma==sma: tendencia_ok=bool(df["close"].iloc[-2]>sma)
        return {"precio":float(df["close"].iloc[-1]),"rsi":float(rsi),"tendencia_ok":tendencia_ok,"atr_pct":atr_diario(sym)}
    except Exception as e: log(f"Error analizando {sym}: {e}"); return None

def parametros_riesgo(atr_pct):
    if atr_pct is None: return SL_DEFECTO,TP_DEFECTO
    sl=min(max(atr_pct*ATR_MULT,SL_MIN),SL_MAX); return round(sl,2),round(sl*RR,2)

def mercado_info():
    try:
        clock=trading.get_clock()
        if not clock.is_open: return False,0,0
        return True,(clock.next_close-clock.timestamp).total_seconds()/60,0
    except Exception as e:
        bog=get_time(); hora=bog.hour+bog.minute/60
        abierto=bog.weekday()<5 and (8.5<=hora<=15.0)
        return abierto,0,0

def limite_diario_excedido(cuenta):
    try: eq,last=float(cuenta.equity),float(cuenta.last_equity); pct=(eq-last)/last*100 if last else 0.0
    except: return estado["pausa_riesgo"]
    pausa=pct<=-MAX_PERDIDA_DIA; estado["pausa_riesgo"]=pausa
    hoy=datetime.now(NY).date()
    if pausa and fallos["pausa_fecha"]!=hoy:
        fallos["pausa_fecha"]=hoy; alerta(f"🛑 AURA: cae {abs(pct):.2f}% hoy (límite {MAX_PERDIDA_DIA}%). Pausa compras hasta mañana.")
    return pausa

def detectar_cierres(pos):
    for sym in list(conocidas):
        if sym not in pos:
            conocidas.pop(sym,None); protegidas.discard(sym); avisadas.discard(sym)
    for sym,p in pos.items():
        conocidas[sym]={"entry":float(p.avg_entry_price),"qty":float(p.qty),"side":"short" if "short" in val(p.side) else "long"}

def en_cooldown(sym):
    t=ultima_compra.get(sym); return bool(t) and (datetime.now(pytz.utc)-t)<timedelta(minutes=COOLDOWN_MIN)

def ejecutar_compra(sym,a):
    try:
        cuenta=trading.get_account(); equity,cash=float(cuenta.equity),float(cuenta.cash)
        try:
            lt=data_client.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=sym,feed=DataFeed.IEX))
            precio=float(lt[sym].price)
        except: precio=a["precio"]
        sl,tp=parametros_riesgo(a["atr_pct"])
        por_monto=min(equity*PCT_POR_TRADE,cash)/precio
        por_riesgo=(equity*RIESGO_POR_TRADE)/(precio*sl/100)
        qty=int(min(por_monto,por_riesgo))
        if qty<1: log(f"{sym}: qty insuficiente"); return None
        trading.submit_order(MarketOrderRequest(symbol=sym,qty=qty,side=OrderSide.BUY,time_in_force=TimeInForce.GTC,order_class=OrderClass.BRACKET,take_profit=TakeProfitRequest(limit_price=round(precio*(1+tp/100),2)),stop_loss=StopLossRequest(stop_price=round(precio*(1-sl/100),2))))
        ultima_compra[sym]=datetime.now(pytz.utc); estado["trades_hoy"]+=1
        msg=f"🟢 COMPRA {sym} x{qty} @ ${precio:.2f} RSI {a['rsi']:.1f} TP +{tp}% SL -{sl}%"
        send_to_sheets(fila("COMPRA",sym,lado="largo",qty=qty,precio=round(precio,2),rsi=round(a['rsi'],1),tendencia_alcista="SI" if a["tendencia_ok"] else "NO",volatilidad_pct=round(a["atr_pct"],2) if a["atr_pct"] else "",sl_pct=sl,tp_pct=tp,mensaje=msg))
        alerta(f"AURA: {msg}"); return msg
    except Exception as e: alerta(f"❌ Error comprar {sym}: {e}"); return None

def ejecutar_venta(sym,pos,rsi,precio):
    try:
        abiertas=trading.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN,symbols=[sym],limit=50))
        for o in abiertas: trading.cancel_order_by_id(o.id)
        time.sleep(1); trading.close_position(sym); ventas_propias.add(sym); estado["trades_hoy"]+=1
        pl,plpc=float(pos.unrealized_pl),float(pos.unrealized_plpc)*100
        msg=f"🔻 VENTA {sym} x{float(pos.qty):g} @ ${precio:.2f} RSI {rsi:.1f} P/L ${pl:+.2f} ({plpc:+.2f}%)"
        send_to_sheets(fila("VENTA",sym,lado="largo",qty=float(pos.qty),precio=round(precio,2),rsi=round(rsi,1),resultado_pct=round(plpc,2),motivo_cierre=f"RSI>{RSI_SELL}",mensaje=msg))
        alerta(f"AURA: {msg}"); return msg
    except Exception as e: alerta(f"❌ Error vender {sym}: {e}"); return None

def proteger_posiciones(posiciones,con_orden):
    for p in posiciones:
        sym=p.symbol
        if sym in con_orden or sym in protegidas or sym in avisadas: continue
        t=ultima_compra.get(sym)
        if t and (datetime.now(pytz.utc)-t)<timedelta(minutes=10): continue
        avisadas.add(sym)
        if not AUTO_PROTEGER: alerta(f"⚠️ {sym} SIN stop loss. Protégela manual."); continue
        try:
            precio=float(p.current_price); qty=abs(float(p.qty)); sl,TP=SL_DEFECTO/100,TP_DEFECTO/100
            corto="short" in val(p.side); stop=precio*(1+sl) if corto else precio*(1-sl); objetivo=precio*(1-TP) if corto else precio*(1+TP)
            trading.submit_order(LimitOrderRequest(symbol=sym,qty=int(qty),side=OrderSide.BUY if corto else OrderSide.SELL,time_in_force=TimeInForce.GTC,limit_price=round(objetivo,2),order_class=OrderClass.OCO,take_profit=TakeProfitRequest(limit_price=round(objetivo,2)),stop_loss=StopLossRequest(stop_price=round(stop,2))))
            protegidas.add(sym); alerta(f"🛡️ {sym} sin protección. Stop ${stop:.2f} TP ${objetivo:.2f}")
        except Exception as e: alerta(f"⚠️ {sym} SIN stop y no pude protegerla ({type(e).__name__})")

def refrescar_grafico():
    if time.time()-_chart["t"]<600: return
    _chart["t"]=time.time()
    try:
        h=trading.get_portfolio_history(GetPortfolioHistoryRequest(period="1M",timeframe="1D"))
        pares=[(float(v),t) for v,t in zip(h.equity,h.timestamp) if v is not None]
        vals=[v for v,_ in pares]; fechas=[datetime.fromtimestamp(t,NY).strftime("%d/%m") for _,t in pares]
        estado["chart"]=svg_linea(vals,fechas)
        if len(vals)>=2 and vals[0]:
            d=vals[-1]-vals[0]; estado["cambio"]=f"{d:+,.2f} USD ({d/vals[0]*100:+.2f}%) desde {fechas[0]}"
    except Exception as e: log(f"Error equity hist: {e}")

def registrar_fallo():
    estado["conectado"]=False; estado["ultimo_error"]="Fallo temporal Alpaca (reintentando)"; fallos["n"]+=1
    if fallos["n"]>=3 and not fallos["avisado"]:
        fallos["avisado"]=True; telegram("⚠️ AURA: 3 fallos seguidos con Alpaca, reintentando...")
    if fallos["n"]>=2:
        # Fuerza reconexión total
        global trading, data_client
        trading=None; data_client=None
        log("♻️ Forzando reconexión total con Alpaca...")

def registrar_exito():
    if fallos["avisado"]: telegram("✅ AURA: conexión Alpaca restablecida.")
    fallos["n"],fallos["avisado"]=0,False

C_COMPRA,C_VENTA,C_NEUTRO,C_AVISO="#0B7A7A","#6A2BA8","#5B6B85","#9A6200"

def escanear():
    cuenta=trading.get_account()
    estado["equity"],estado["cash"]=float(cuenta.equity),float(cuenta.cash)
    estado["bp"]=float(cuenta.buying_power); estado["day_pl"]=estado["equity"]-float(cuenta.last_equity)
    hoy=datetime.now(NY).date()
    if estado["fecha_trades"]!=hoy: estado["fecha_trades"],estado["trades_hoy"]=hoy,0
    posiciones={p.symbol:p for p in trading.get_all_positions()}
    abiertas=trading.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN,limit=100))
    con_orden={o.symbol for o in abiertas}
    detectar_cierres(posiciones)
    estado["posiciones"]=[{"symbol":p.symbol,"qty":float(p.qty),"market_value":float(p.market_value),"avg_entry":float(p.avg_entry_price)} for p in posiciones.values()]
    estado["pos_det"]=[{"symbol":p.symbol,"side":"Corto" if "short" in val(p.side) else "Largo","qty":p.qty,"entry":float(p.avg_entry_price),"price":float(p.current_price),"pl":float(p.unrealized_pl),"plpc":float(p.unrealized_plpc)*100} for p in posiciones.values()]
    estado["conectado"],estado["ultimo_error"]=True,None; registrar_exito(); refrescar_grafico()
    abierto,mins_cierre,_=mercado_info(); estado["mercado_abierto"]=abierto
    if abierto: proteger_posiciones(list(posiciones.values()),con_orden)
    pausa=abierto and limite_diario_excedido(cuenta)
    ocupados=set(posiciones)|con_orden; n_pos,compras=len(ocupados),0
    puede_operar=abierto and AUTO_TRADING and not pausa
    detalles,logs={},[]
    if not abierto: logs.append(f"⏸️ Mercado cerrado - Standby {get_time().strftime('%H:%M:%S COL')}")
    for sym in SYMBOLS:
        a=analizar(sym)
        if a is None:
            detalles[sym]={"precio":0,"rsi":"-","decision":"Esperando datos…","color":C_NEUTRO,"tendencia_ok":None,"volatilidad_pct":None}; continue
        precio,rsi,tend=a["precio"],a["rsi"],a["tendencia_ok"]; en_pos=sym in posiciones
        if rsi<RSI_BUY and tend: dec,color,action=("COMPRAR FUERTE" if rsi<30 else "COMPRAR"),C_COMPRA,"BUY"
        elif rsi<RSI_BUY: dec,color,action="NO COMPRA: tendencia bajista" if tend is False else "NO COMPRA: sin datos",C_AVISO,"HOLD"
        elif rsi>RSI_SELL and en_pos: dec,color,action=("VENDER FUERTE" if rsi>75 else "VENDER"),C_VENTA,"SELL"
        elif rsi>RSI_SELL: dec,color,action="SOBRECOMPRA",C_VENTA,"HOLD"
        else: dec,color,action="NEUTRAL",C_NEUTRO,"HOLD"
        detalles[sym]={"precio":precio,"rsi":round(rsi,1),"decision":dec,"color":color,"action":action,"tendencia_ok":tend,"volatilidad_pct":round(a["atr_pct"],2) if a["atr_pct"] is not None else None}
        logs.append(f"{sym} ${precio:.2f} RSI {rsi:.1f} -> {dec}")
        if not puede_operar: continue
        if action=="BUY" and not (en_pos or sym in ocupados or en_cooldown(sym) or n_pos>=MAX_POSICIONES or compras>=MAX_COMPRAS_POR_ESCANEO):
            msg=ejecutar_compra(sym,a)
            if msg: logs.append(msg); compras+=1; n_pos+=1; ocupados.add(sym)
        elif action=="SELL" and en_pos and float(posiciones[sym].unrealized_pl)>0:
            msg=ejecutar_venta(sym,posiciones[sym],rsi,precio)
            if msg: logs.append(msg)
    estado["detalles"]=detalles; estado["log"]=logs[:25]; estado["ultimo_analisis"]=get_time().strftime("%Y-%m-%d %H:%M:%S COL")
    estado["status"]=f"Activa • {'Paper' if IS_PAPER else 'REAL'} • Auto:{'ON' if AUTO_TRADING else 'OFF'} • Sheets:{'ON' if sheets_activo() else 'OFF'}"

def get_account_rest_direct():
    """Llamada REST directa para diagnosticar si son las keys o alpaca-py"""
    try:
        headers={"APCA-API-KEY-ID":API_KEY,"APCA-API-SECRET-KEY":API_SECRET}
        r=requests.get(f"{BASE_URL}/v2/account",headers=headers,timeout=10)
        log(f"REST directo status: {r.status_code}")
        if r.status_code==200:
            data=r.json()
            log(f"REST OK: equity {data.get('equity')}")
            return float(data.get('equity',0)), float(data.get('cash',0)), float(data.get('buying_power',0)), float(data.get('last_equity',0))
        else:
            log(f"REST error body: {r.text[:300]}")
            return None
    except Exception as e:
        log(f"REST exception: {type(e).__name__}: {e}")
        return None

def cargar_clientes():
    global trading, data_client
    if not ALPACA_OK:
        estado["status"]="Error: alpaca-py no importado"; estado["ultimo_error"]="Falta alpaca-py en requirements.txt"; return False
    if not (API_KEY and API_SECRET):
        estado["status"]="Esperando keys"; estado["ultimo_error"]="Faltan ALPACA_API_KEY y SECRET en Render"; log("❌ Faltan API Keys"); return False
    try:
        log(f"Conectando con Alpaca... Key: {API_KEY[:4]}...{API_KEY[-4:] if len(API_KEY)>8 else ''} Paper:{IS_PAPER} URL:{BASE_URL}")
        # Paso 1: Intento REST directo (rápido) para validar keys
        rest=get_account_rest_direct()
        if rest is None:
            log("⚠️ REST directo falló, intentando con TradingClient igual...")
        else:
            eq,cash,bp,last=rest
            log(f"✅ REST directo OK: equity ${eq:.2f} cash ${cash:.2f}")
            # Si REST funciona, TradingClient debe funcionar
        # Paso 2: TradingClient con timeout en thread
        log("Creando TradingClient...")
        def _crear():
            tc=TradingClient(API_KEY,API_SECRET,paper=IS_PAPER)
            return tc
        with ThreadPoolExecutor(max_workers=1) as ex:
            try:
                trading_temp=ex.submit(_crear).result(timeout=20)
            except FutureTimeout:
                log("❌ Timeout creando TradingClient (20s)"); raise TimeoutError("Timeout TradingClient")
        log("TradingClient creado, creando DataClient...")
        def _crear_data():
            return StockHistoricalDataClient(API_KEY,API_SECRET)
        with ThreadPoolExecutor(max_workers=1) as ex:
            try:
                data_temp=ex.submit(_crear_data).result(timeout=20)
            except FutureTimeout:
                log("❌ Timeout DataClient"); raise TimeoutError("Timeout DataClient")
        log("Clientes creados, pidiendo cuenta con timeout 25s...")
        def _get_acc():
            return trading_temp.get_account()
        with ThreadPoolExecutor(max_workers=1) as ex:
            try:
                cuenta=ex.submit(_get_acc).result(timeout=25)
            except FutureTimeout:
                log("❌ Timeout get_account (25s) - Alpaca no responde"); raise TimeoutError("get_account timeout 25s")
        # Éxito
        trading_global=trading_temp; data_global=data_temp
        globals()['trading']=trading_global; globals()['data_client']=data_global
        estado["equity"],estado["cash"]=float(cuenta.equity),float(cuenta.cash)
        estado["bp"]=float(cuenta.buying_power); estado["day_pl"]=float(cuenta.equity)-float(cuenta.last_equity)
        estado["conectado"]=True; estado["ultimo_error"]=None; fallos["aviso_conexion"]=False
        log(f"✅ AURA V7.2 conectada ({'PAPER' if IS_PAPER else 'REAL'}): equity ${estado['equity']:.2f} cash ${estado['cash']:.2f}")
        telegram(f"🚀 AURA V7.2 conectada ({'PAPER' if IS_PAPER else 'REAL'}): ${estado['equity']:.2f}. Vigilando {', '.join(SYMBOLS)}.")
        threading.Thread(target=init_sheet,daemon=True).start()
        return True
    except Exception as e:
        # Limpia
        globals()['trading']=None; globals()['data_client']=None
        err_msg=f"{type(e).__name__}: {str(e)[:400]}"
        log(f"❌ Error conectando Alpaca: {err_msg}")
        estado["status"]=f"Error Alpaca: {type(e).__name__}"; estado["ultimo_error"]=err_msg; estado["conectado"]=False
        if not fallos.get("aviso_conexion"):
            fallos["aviso_conexion"]=True
            telegram(f"⚠️ AURA: no pude conectar Alpaca ({type(e).__name__}): {str(e)[:200]}")
        return False

def bot_loop():
    while estado["running"]:
        pausa_s=INTERVALO_CERRADO
        try:
            if trading is None and not cargar_clientes():
                time.sleep(60); continue
            estado["ultimo_heartbeat"]=get_time().strftime("%H:%M:%S")
            escanear()
            pausa_s=INTERVALO_ABIERTO if estado["mercado_abierto"] else INTERVALO_CERRADO
        except Exception as e:
            log(f"Error loop: {type(e).__name__}: {e}"); estado["status"]="Error temporal (reintentando)"; registrar_fallo()
            estado["ultimo_error"]=f"{type(e).__name__}: {str(e)[:200]}"; pausa_s=60
        time.sleep(pausa_s)

def iniciar_bot():
    with _lock_inicio:
        if estado["running"]: return False
        estado["running"]=True
        threading.Thread(target=bot_loop,daemon=True).start()
        log("✅ Bot iniciado - V7.2 ANTI-CUELGUE"); return True

def svg_linea(vals,fechas,w=720,h=220):
    if len(vals)<2: return ""
    lo,hi=min(vals),max(vals); rng=max(hi-lo,hi*0.005) or 1.0; mid=(hi+lo)/2; top,bot=mid+rng/2,mid-rng/2
    x0,x1,y0,y1=78,w-14,14,h-34
    px=lambda i: x0+i*(x1-x0)/(len(vals)-1); py=lambda v: y0+(top-v)/rng*(y1-y0)
    pts=" ".join(f"{px(i):.1f},{py(v):.1f}" for i,v in enumerate(vals))
    color="#0E8C8C" if vals[-1]>=vals[0] else "#6A2BA8"
    t='font-size="10" fill="#5B6B85" font-family="DM Sans,Arial,sans-serif"'
    return f'<svg viewBox="0 0 {w} {h}" style="width:100%;height:auto;display:block"><defs><linearGradient id="g" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="{color}" stop-opacity=".22"/><stop offset="1" stop-color="{color}" stop-opacity="0"/></linearGradient></defs><line x1="{x0}" x2="{x1}" y1="{py(hi):.1f}" y2="{py(hi):.1f}" stroke="#E3E9F2"/><line x1="{x0}" x2="{x1}" y1="{py(lo):.1f}" y2="{py(lo):.1f}" stroke="#E3E9F2"/><text x="{x0-8}" y="{py(hi)+3:.1f}" text-anchor="end" {t}>${hi:,.0f}</text><text x="{x0-8}" y="{py(lo)+3:.1f}" text-anchor="end" {t}>${lo:,.0f}</text><text x="{x0}" y="{h-10}" {t}>{fechas[0]}</text><text x="{x1}" y="{h-10}" text-anchor="end" {t}>{fechas[-1]}</text><polygon points="{x0},{y1} {pts} {x1},{y1}" fill="url(#g)"/><polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/></svg>'

DASHBOARD_HTML="""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AURA V7.2</title><style>@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;600;700&display=swap');:root{--bg:#F5F7FB;--card:#FFFFFF;--bd:#E3E9F2;--tx:#0F1A2B;--mut:#5B6B85;--ok:#0B7A7A;--bad:#B4235A}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font-family:"DM Sans",system-ui,sans-serif;padding:18px}header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:18px}.pill{font-size:11px;padding:5px 10px;border-radius:20px;border:1px solid var(--bd);background:var(--card)}.pill.ok{color:var(--ok);border-color:#B6E6D9;background:#E8F6F2}.pill.bad{color:var(--bad);border-color:#F5B0C8;background:#FDF0F4}.pill.warn{color:#9A6200;border-color:#F5D78A;background:#FFF8E1}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-bottom:18px}.card{background:var(--card);border:1px solid var(--bd);border-radius:16px;padding:18px}.card h3{margin:0 0 6px;font-size:11px;letter-spacing:1px;text-transform:uppercase;color:var(--mut)}.big{font-size:26px;font-weight:700;margin:4px 0}.sub{font-size:12px;color:var(--mut);line-height:1.4}.ok{color:var(--ok)}.bad{color:var(--bad)}.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:13px}th{font-size:11px;text-align:left;color:var(--mut);padding:8px 6px;border-bottom:1px solid var(--bd)}td{padding:8px 6px;border-bottom:1px solid #F0F3F9}.rsi{height:6px;background:#E3E9F2;border-radius:3px;position:relative;margin:10px 0}.dot{position:absolute;top:-3px;width:12px;height:12px;border-radius:50%;background:var(--tx);transform:translateX(-50%)}.logs{background:#0F1A2B;color:#C8D6E8;border-radius:12px;padding:14px;font-family:monospace;font-size:12px;max-height:300px;overflow-y:auto}.logs div{padding:3px 0;border-bottom:1px solid #1E2A3F}footer{margin-top:24px;font-size:11px;color:var(--mut);text-align:center;line-height:1.6}</style></head><body><div style="max-width:1100px;margin:0 auto"><header><div style="display:flex;align-items:center;gap:12px"><div><div style="font-weight:700;font-size:18px">AURA SYNAPSE</div><div class="sub">V7.2 ANTI-CUELGUE • {{ 'PAPER' if paper else 'REAL' }} • {{ 'corriendo' if running else 'detenido' }}</div></div></div><div style="display:flex;gap:8px;flex-wrap:wrap"><span class="pill {{ 'ok' if paper else 'bad' }}">● {{ 'PAPER' if paper else 'REAL' }}</span><span class="pill {{ 'ok' if connected else 'bad' }}">● {{ 'Alpaca conectada' if connected else 'Alpaca sin conexión' }}</span><span class="pill {{ 'ok' if running else 'bad' }}">● {{ 'Bot corriendo' if running else 'detenido' }}</span><span class="pill {{ 'ok' if trading else '' }}">● Compras {{ 'activas' if trading else 'pausadas' }}</span>{% if pausa %}<span class="pill bad">● Pausa riesgo</span>{% endif %}<span class="pill {{ 'ok' if telegram else 'warn' }}">● Telegram {{ 'activo' if telegram else 'off' }}</span><span class="pill {{ 'ok' if sheets else '' }}">● Sheets {{ 'activo' if sheets else 'off' }}</span></div></header><div class="grid"><div class="card"><h3>Equity total</h3><p class="big">${{ "{:,.2f}".format(equity) }}</p><div class="sub {{ 'ok' if day_pl >= 0 else 'bad' }}">{{ "%+.2f"|format(day_pl) }} hoy</div></div><div class="card"><h3>Efectivo</h3><p class="big">${{ "{:,.2f}".format(cash) }}</p></div><div class="card"><h3>Poder compra</h3><p class="big">${{ "{:,.2f}".format(bp) }}</p></div><div class="card"><h3>Posiciones</h3><p class="big">{{ positions|length }} / {{ max_pos }}</p><div class="sub">Escaneo: {{ last_scan or '—' }} · Pulso: {{ heartbeat or '—' }}</div></div></div>{% if last_error %}<div class="card" style="margin-bottom:16px;border-color:#F5B0C8;background:#FDF0F4"><span class="bad">⚠ {{ last_error }}</span><div class="sub" style="margin-top:6px">Mira Render Logs y /api/debug?token=...</div></div>{% endif %}<div class="card" style="margin-bottom:16px"><h2 style="margin:0 0 8px;font-size:16px">Evolución equity · último mes</h2>{% if cambio %}<p class="sub" style="margin:-8px 0 12px">{{ cambio }}</p>{% endif %}{% if chart %}{{ chart|safe }}{% else %}<p class="sub">Aún no hay historial suficiente.</p>{% endif %}</div><div class="card" style="margin-bottom:16px"><h2 style="margin:0 0 12px;font-size:16px">Posiciones abiertas</h2>{% if positions %}<div class="scroll"><table><tr><th>Símbolo</th><th>Lado</th><th>Cant</th><th>Entrada</th><th>Actual</th><th>P/L</th></tr>{% for p in positions %}<tr><td><strong>{{ p.symbol }}</strong></td><td>{{ p.side }}</td><td>{{ p.qty }}</td><td>${{ "%.2f"|format(p.entry) }}</td><td>${{ "%.2f"|format(p.price) }}</td><td class="{{ 'ok' if p.pl >= 0 else 'bad' }}">{{ "%+.2f"|format(p.pl) }} ({{ "%+.2f"|format(p.plpc) }}%)</td></tr>{% endfor %}</table></div>{% else %}<p class="sub">Sin posiciones abiertas.</p>{% endif %}</div><h2 style="font-size:14px">Activos vigilados · RSI (5 min)</h2><div class="grid">{% for s in symbols %}{% set d = detalles.get(s) %}<div class="card"><h3>{{ s }}{% if s in held %} · en posición{% endif %}</h3>{% if d and d.rsi != '-' %}<p class="big">{{ d.rsi }}</p><div class="rsi"><span class="dot" style="left:{{ d.rsi }}%"></span></div><div class="sub" style="color:{{ d.color }};font-weight:600">{{ d.decision }}</div><div class="sub">Tendencia {{ 'alcista' if d.tendencia_ok else ('bajista' if d.tendencia_ok is false else 'sin datos') }}{% if d.volatilidad_pct %} · {{ d.volatilidad_pct }}%{% endif %}</div>{% else %}<p class="sub">Esperando datos… {{ d.decision if d else '' }}</p>{% endif %}</div>{% endfor %}</div><h2 style="margin-top:8px;font-size:14px">Registro</h2><div class="logs">{% for l in logs %}<div>{{ l }}</div>{% else %}<div>Sin eventos</div>{% endfor %}</div><footer>RR 1:{{ rr }} · {{ eq_pct }}% equity por operación · pausa {{ max_loss }}%. Venta RSI > {{ rsi_sell }} con ganancia. URL: /api/debug?token=...</footer></div><script>setTimeout(()=>location.reload(), {{ 30000 if connected else 8000 }})</script></body></html>"""

@app.route("/")
def home():
    require_token()
    return render_template_string(DASHBOARD_HTML,connected=estado["conectado"],paper=IS_PAPER,running=estado["running"],trading=AUTO_TRADING,last_scan=estado["ultimo_analisis"] if estado["ultimo_analisis"]!="Nunca" else None,heartbeat=estado["ultimo_heartbeat"],last_error=estado["ultimo_error"],equity=estado["equity"],cash=estado["cash"],bp=estado["bp"],day_pl=estado["day_pl"],positions=estado["pos_det"],held={p["symbol"] for p in estado["pos_det"]},max_pos=MAX_POSICIONES,symbols=SYMBOLS,detalles=dict(estado["detalles"]),rsi_buy=RSI_BUY,rsi_sell=RSI_SELL,eq_pct=round(PCT_POR_TRADE*100),rr=RR,max_loss=MAX_PERDIDA_DIA,pausa=estado["pausa_riesgo"],telegram=bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID),sheets=sheets_activo(),chart=estado["chart"],cambio=estado["cambio"],logs=list(reversed(list(logs_hist)))[:12])

@app.route("/api")
def api():
    require_token()
    return jsonify({"mensaje":"🧠 AURA V7.2 VIVE","version":estado["version"],"status":estado["status"],"equity":estado["equity"],"cash":estado["cash"],"ultimo_analisis":estado["ultimo_analisis"],"ultimo_heartbeat":estado["ultimo_heartbeat"],"log":estado["log"],"detalles":estado["detalles"],"posiciones":estado["posiciones"],"vive_desde":estado["vive_desde"],"simbolos":SYMBOLS,"trades_hoy":estado["trades_hoy"],"auto_trading":AUTO_TRADING,"google_sheet":estado["google_sheet"],"google_status":estado["google_status"],"paper":IS_PAPER,"conectado":estado["conectado"],"ultimo_error":estado["ultimo_error"]})

@app.route("/api/debug")
def debug():
    require_token()
    return jsonify({"status":"ALPACA_" + ("PAPER" if IS_PAPER else "REAL") if API_KEY else "SIN_KEYS","version":"V7.2","running":estado["running"],"has_keys":bool(API_KEY),"key_prefix":API_KEY[:4]+"…"+API_KEY[-4:] if API_KEY and len(API_KEY)>8 else None,"base_url":BASE_URL,"is_paper":IS_PAPER,"auto":AUTO_TRADING,"google_configured":sheets_activo(),"google_status":estado["google_status"],"alpaca_py_ok":ALPACA_OK,"conectado":estado["conectado"],"ultimo_error":estado["ultimo_error"],"ultimo_heartbeat":estado["ultimo_heartbeat"],"modo":"PAPER" if IS_PAPER else "REAL","registro":list(logs_hist)[-15:],"env_check":{"ALPACA_API_KEY":bool(os.environ.get("ALPACA_API_KEY")),"APCA_API_KEY_ID":bool(os.environ.get("APCA_API_KEY_ID")),"SECRET":bool(API_SECRET),"DASHBOARD_TOKEN":bool(DASHBOARD_TOKEN)}})

@app.route("/health")
def health(): return "OK AURA V7.2 ANTI-CUELGUE",200

if AUTO_START: iniciar_bot()
if __name__=="__main__":
    port=int(os.environ.get("PORT",10000)); print(f"🚀 AURA V7.2 puerto {port} Keys:{'OK' if API_KEY else 'FALTAN'} Auto:{AUTO_TRADING}",flush=True); app.run(host="0.0.0.0",port=port)
