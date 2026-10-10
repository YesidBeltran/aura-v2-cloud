"""
Backtest de la estrategia RSI (largos y cortos) con velas de 5 min de Alpaca.

Uso (con tus mismas variables APCA_API_KEY_ID / APCA_API_SECRET_KEY):
    python backtest.py                 # 1 año, largos + cortos
    python backtest.py --dias 180
    python backtest.py --sin-cortos    # solo largos
    python backtest.py --rsi-buy 35 --rsi-sell 65 --tp 3 --sl 2

Reglas del simulador (conservadoras):
  - La señal sale del cierre de una vela; la entrada es en la APERTURA de la siguiente.
  - Si en una misma vela se tocan SL y TP, se asume que se tocó primero el SL.
  - Slippage por lado (por defecto 0.02%). Sin comisiones (Alpaca no cobra acciones).
  - Una posición por símbolo a la vez. Si no toca TP/SL en --max-bars velas, sale al cierre.
  - Largo:  compra si RSI <= rsi_buy.   TP arriba, SL abajo.
  - Corto:  vende si RSI >= rsi_sell.   TP abajo, SL arriba.
  - Con filtro de tendencia: largos solo si precio > media móvil; cortos solo si precio < media.
"""
import os
import argparse
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytz
from ta.momentum import RSIIndicator

SYMBOLS = ["AAPL", "TSLA", "NVDA", "MSFT", "SPY"]
EQUITY_POR_TRADE = 0.05
NY = pytz.timezone("America/New_York")


def cargar_barras(symbol, dias, feed):
    from alpaca_trade_api.rest import REST, TimeFrame, TimeFrameUnit
    api = REST(os.getenv("APCA_API_KEY_ID"), os.getenv("APCA_API_SECRET_KEY"),
               os.getenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets"),
               api_version="v2")
    ahora = datetime.now(pytz.utc)
    df = api.get_bars(
        symbol, TimeFrame(5, TimeFrameUnit.Minute),
        start=(ahora - timedelta(days=dias)).isoformat(),
        end=(ahora - timedelta(minutes=20)).isoformat(),
        feed=feed,
    ).df
    if df is None or df.empty:
        return pd.DataFrame()
    # solo horario regular (9:30-16:00 hora de Nueva York)
    idx = df.index.tz_convert(NY)
    hhmm = idx.hour * 60 + idx.minute
    return df[(hhmm >= 9 * 60 + 30) & (hhmm < 16 * 60)]


def simular(df, symbol, p, trend):
    if len(df) < max(trend, 14) + 50:
        return []
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    rsi = RSIIndicator(close=df["close"], window=14).rsi().to_numpy()
    sma = df["close"].rolling(trend).mean().to_numpy() if trend else None
    tiempos = df.index
    slip = p.slippage / 100
    n = len(df)
    trades = []
    i = max(trend, 14) + 1

    while i < n - 1:
        lado = None
        if rsi[i] <= p.rsi_buy and (not trend or c[i] > sma[i]):
            lado = "largo"
        elif p.cortos and rsi[i] >= p.rsi_sell and (not trend or c[i] < sma[i]):
            lado = "corto"
        if lado is None or np.isnan(rsi[i]):
            i += 1
            continue

        k = i + 1  # vela de entrada
        if lado == "largo":
            entrada = o[k] * (1 + slip)
            sl_px = entrada * (1 - p.sl / 100)
            tp_px = entrada * (1 + p.tp / 100)
        else:
            entrada = o[k] * (1 - slip)
            sl_px = entrada * (1 + p.sl / 100)
            tp_px = entrada * (1 - p.tp / 100)

        salida, motivo = None, None
        fin = min(n - 1, k + p.max_bars)
        j = k
        while j <= fin:
            if lado == "largo":
                if l[j] <= sl_px:
                    salida, motivo = sl_px * (1 - slip), "SL"; break
                if h[j] >= tp_px:
                    salida, motivo = tp_px, "TP"; break
            else:
                if h[j] >= sl_px:
                    salida, motivo = sl_px * (1 + slip), "SL"; break
                if l[j] <= tp_px:
                    salida, motivo = tp_px, "TP"; break
            j += 1
        if salida is None:
            j = fin
            salida = c[j] * ((1 - slip) if lado == "largo" else (1 + slip))
            motivo = "TIEMPO"

        ret = (salida - entrada) / entrada if lado == "largo" else (entrada - salida) / entrada
        trades.append({
            "symbol": symbol, "lado": lado, "entrada_t": tiempos[k],
            "salida_t": tiempos[j], "ret": ret, "motivo": motivo,
        })
        i = j + 1
    return trades


def resumen(t, titulo):
    if t.empty:
        return f"{titulo}: sin operaciones"
    gan = t[t.ret > 0].ret
    per = t[t.ret <= 0].ret
    pf = gan.sum() / abs(per.sum()) if per.sum() != 0 else float("inf")
    return (f"{titulo}: {len(t)} trades | aciertos {len(gan)/len(t)*100:.1f}% | "
            f"ret medio {t.ret.mean()*100:+.3f}% | factor de beneficio {pf:.2f} | "
            f"mejor {t.ret.max()*100:+.2f}% peor {t.ret.min()*100:+.2f}%")


def curva(t):
    """Equity compuesto usando 5% por trade, ordenado por fecha de salida."""
    t = t.sort_values("salida_t")
    eq = (1 + EQUITY_POR_TRADE * t.ret).cumprod()
    pico = eq.cummax()
    dd = ((eq - pico) / pico).min()
    return eq.iloc[-1] - 1, dd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=365)
    ap.add_argument("--rsi-buy", type=float, default=40)
    ap.add_argument("--rsi-sell", type=float, default=60)
    ap.add_argument("--tp", type=float, default=3.0)
    ap.add_argument("--sl", type=float, default=2.0, help="positivo, ej. 2.0")
    ap.add_argument("--max-bars", type=int, default=390, help="velas máx. por trade (390 ~ 5 días)")
    ap.add_argument("--slippage", type=float, default=0.02, help="%% por lado")
    ap.add_argument("--trend", type=int, default=780, help="velas de la media móvil (780 ~ 10 días)")
    ap.add_argument("--feed", default="iex")
    ap.add_argument("--sin-cortos", dest="cortos", action="store_false")
    p = ap.parse_args()

    datos = {}
    for s in SYMBOLS:
        print(f"Descargando {s}...", flush=True)
        df = cargar_barras(s, p.dias, p.feed)
        if not df.empty:
            datos[s] = df
    if not datos:
        print("No se pudieron descargar datos."); return

    for trend, nombre in ((0, "SIN filtro de tendencia"), (p.trend, f"CON filtro de tendencia (SMA {p.trend})")):
        todos = []
        for s, df in datos.items():
            todos += simular(df, s, p, trend)
        t = pd.DataFrame(todos)
        print("\n" + "=" * 70)
        print(nombre)
        print("=" * 70)
        if t.empty:
            print("Sin operaciones."); continue
        print(resumen(t, "TOTAL "))
        print(resumen(t[t.lado == "largo"], "Largos"))
        if p.cortos:
            print(resumen(t[t.lado == "corto"], "Cortos"))
        print("Por símbolo:")
        for s in datos:
            print("  " + resumen(t[t.symbol == s], s))
        print("Salidas:", t.motivo.value_counts().to_dict())
        total, dd = curva(t)
        print(f"Curva (5% por trade, sin límite de posiciones): retorno {total*100:+.1f}% | "
              f"drawdown máx {dd*100:.1f}%")

    print("\nOJO: el pasado no garantiza el futuro. Un factor de beneficio < 1.2 o pocos "
          "trades por símbolo no es evidencia suficiente.")


if __name__ == "__main__":
    main()
