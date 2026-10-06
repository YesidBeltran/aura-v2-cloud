"""
AURA V2 - $3 DOLARES - VERSION RENDER CLOUD 24/7
Adaptado para correr sin PC - Lee keys de ENV VARS
"""
import time, json, os, requests
from datetime import datetime
import pytz
import yfinance as yf
from binance.client import Client
from binance.exceptions import BinanceAPIException

# === CONFIG ===
BINANCE_PAPER = False
CANTIDAD_CRYPTO_USD = 3
MINIMO_BINANCE = 1

# TELEGRAM
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8935047777:AAGqwzW6kT8zErB34IzEDUnr2si3rDh4g5s")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "650125067")

# ALPACA - lee de ENV VARS (Render)
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "PKXBY6NZL3SKAFZSZYDWFYU3KM")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "2ZRfdF4DK9ntwHM45ZxeAfhKKTso9EPxbZg4qAt4SHiz")

# BINANCE - primero ENV VARS, si no existe archivo local (para compatibilidad)
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "")

if not BINANCE_API_KEY:
    try:
        with open("aura_binance_keys.json", 'r') as f:
            d = json.load(f)
            BINANCE_API_KEY = d["BINANCE_API_KEY"].strip()
            BINANCE_SECRET_KEY = d["BINANCE_SECRET_KEY"].strip()
            print("Keys leidas de archivo local")
    except:
        print("❌ NO HAY BINANCE KEYS en ENV VARS ni archivo!")
        print("Configura BINANCE_API_KEY y BINANCE_SECRET_KEY en Render")

print("="*70)
print(" AURA V2 - $3 REALES - RENDER CLOUD 24/7")
print("="*70)
print(f" TELEGRAM_CHAT: {TELEGRAM_CHAT_ID}")
print(f" CANTIDAD: ${CANTIDAD_CRYPTO_USD}")
print("="*70)

from alpaca.trading.client import TradingClient
try:
    alpaca_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)
    alpaca_account = alpaca_client.get_account()
    print(f"✅ Alpaca OK Equity ${float(alpaca_account.equity):.2f}")
except Exception as e:
    print(f"❌ Alpaca error: {e}")
    alpaca_client = None

# BINANCE
binance_client = None
if BINANCE_API_KEY and BINANCE_SECRET_KEY:
    try:
        binance_client = Client(BINANCE_API_KEY, BINANCE_SECRET_KEY)
        bin_acc = binance_client.get_account()
        balances = {b['asset']: float(b['free']) for b in bin_acc['balances'] if float(b['free'])>0.00001}
        print(f"✅ Binance conectado:")
        for k,v in balances.items():
            print(f"   {k}: {v}")
        usdt_spot = balances.get('USDT', 0)
        print(f"   >>> USDT SPOT: {usdt_spot}")
    except Exception as e:
        print(f"❌ Binance error: {e}")
else:
    print("❌ Binance keys faltan")

def enviar_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=8)
        return r.status_code
    except Exception as e:
        print(f"Telegram error: {e}")
        return 0

# Mensaje de inicio cloud
if binance_client:
    try:
        usdt_now = float(binance_client.get_asset_balance(asset='USDT')['free'])
        enviar_telegram(f"☁️ AURA V2 $3 CLOUD INICIADO en RENDER\nUSDT: {usdt_now:.4f}\nYa trabaja sin tu PC 24/7\nConfig ${CANTIDAD_CRYPTO_USD}")
    except:
        pass

def rsi_from_prices(prices, period=14):
    if len(prices) < period+1: return 50
    deltas = [prices[i]-prices[i-1] for i in range(1,len(prices))]
    gains = [max(0,d) for d in deltas]
    losses = [max(0,-d) for d in deltas]
    avg_gain = sum(gains[:period])/period
    avg_loss = sum(losses[:period])/period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain*(period-1)+gains[i])/period
        avg_loss = (avg_loss*(period-1)+losses[i])/period
    if avg_loss == 0: return 100
    rs = avg_gain/avg_loss
    return 100 - (100/(1+rs))

def analizar_crypto_real():
    if not binance_client:
        print("Binance no conectado, saltando crypto")
        return
    simbolos = ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
    print(f"\n  ₿ BINANCE CRYPTO 24/7 - REAL ${CANTIDAD_CRYPTO_USD}:")
    for sym in simbolos:
        try:
            klines = binance_client.get_klines(symbol=sym, interval=Client.KLINE_INTERVAL_1HOUR, limit=50)
            closes = [float(k[4]) for k in klines]
            rsi = rsi_from_prices(closes)
            precio = closes[-1]
            senal = "NEUTRAL"
            if rsi < 35: senal = "COMPRAR FUERTE"
            elif rsi < 45: senal = "COMPRAR"
            elif rsi > 65: senal = "VENDER FUERTE"
            elif rsi > 55: senal = "VENDER"
            print(f"    {sym} ${precio:.2f} {senal} RSI {rsi:.1f}")
            if "FUERTE" in senal:
                usdt_balance = float(binance_client.get_asset_balance(asset='USDT')['free'])
                print(f"      USDT: {usdt_balance:.4f}")
                if usdt_balance < MINIMO_BINANCE:
                    print(f"      ❌ USDT bajo")
                    enviar_telegram(f"⚠️ {sym} {senal} RSI {rsi:.1f} USDT bajo {usdt_balance:.4f}")
                    continue
                cantidad_a_usar = min(CANTIDAD_CRYPTO_USD, usdt_balance)
                if usdt_balance < CANTIDAD_CRYPTO_USD:
                    cantidad_a_usar = usdt_balance
                print(f"      Intentando COMPRAR ${cantidad_a_usar:.4f}")
                try:
                    if "COMPRAR" in senal:
                        try:
                            order = binance_client.order_market_buy(symbol=sym, quoteOrderQty=round(cantidad_a_usar, 2))
                        except BinanceAPIException as e:
                            if "MIN_NOTIONAL" in str(e):
                                order = binance_client.order_market_buy(symbol=sym, quoteOrderQty=round(usdt_balance, 2))
                            else:
                                raise e
                        print(f"      ✅ REAL {sym} COMPRAR ${cantidad_a_usar:.2f} ID {order.get('orderId')}")
                        enviar_telegram(f"✅ CLOUD REAL {sym} COMPRAR ${cantidad_a_usar:.2f}\nPrecio ${precio:.2f} RSI {rsi:.1f}\nOrderId {order.get('orderId')}\n¡Ejecutado desde la nube sin PC!")
                    else:
                        asset = sym.replace("USDT","")
                        bal = float(binance_client.get_asset_balance(asset=asset)['free'])
                        if bal * precio < 1:
                            continue
                        order = binance_client.order_market_sell(symbol=sym, quantity=bal)
                        enviar_telegram(f"✅ CLOUD VENTA {sym}")
                except BinanceAPIException as be:
                    print(f"      ❌ Error Binance: {be}")
                    enviar_telegram(f"❌ Cloud Binance {sym}: {be}")
        except Exception as e:
            print(f"    {sym} error {e}")

def analizar_forex():
    pares = ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "EURJPY=X", "AUDUSD=X"]
    print("\n  📈 FOREX:")
    for par in pares:
        try:
            ticker = yf.Ticker(par)
            hist = ticker.history(period="1mo", interval="1h")
            if len(hist) < 20: continue
            close = hist['Close'].tolist()
            rsi = rsi_from_prices(close)
            precio = close[-1]
            senal = "NEUTRAL"
            if rsi < 30: senal = "COMPRAR FUERTE"
            elif rsi < 40: senal = "COMPRAR"
            elif rsi > 70: senal = "VENDER FUERTE"
            elif rsi > 60: senal = "VENDER"
            print(f"    {par} {precio:.5f} {senal} RSI {rsi:.1f}")
        except: pass

ny_tz = pytz.timezone('America/New_York')
while True:
    try:
        now_ny = datetime.now(ny_tz)
        usdt_actual = 0
        try:
            if binance_client:
                usdt_actual = float(binance_client.get_asset_balance(asset='USDT')['free'])
        except:
            pass
        print(f"\n[{now_ny.strftime('%Y-%m-%d %H:%M:%S NY')}] AURA CLOUD ${CANTIDAD_CRYPTO_USD} USDT {usdt_actual:.4f}")
        analizar_forex()
        analizar_crypto_real()
        print(f"  ⏳ 5 min... CLOUD")
        time.sleep(300)
    except Exception as e:
        print(f"Error loop: {e}")
        time.sleep(30)
