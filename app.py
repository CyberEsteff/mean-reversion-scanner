import flet as ft
import pandas as pd
import asyncio
import os
import threading
from bybit.api import HTTP
from datetime import datetime, timezone
import ta as ta_lib

# === CONFIGURACIÓN ===
SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'RENDERUSDT']
from bybit.api import HTTP
client = HTTP()  # Bybit sin API key
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# === FUNCIONES TÉCNICAS ===
def fetch_closed_1h_candles(symbol, limit=50):
    """Obtiene velas 1H CERRADAS de Bybit"""
    try:
        response = client.get_kline(
            category="spot",
            symbol=symbol,
            interval="60",  # 1H
            limit=limit + 1
        )
        if not response['result']['list']:
            return None
        
        klines = response['result']['list']
        df = pd.DataFrame(klines, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'turnover', 'timestamp'
        ])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col])
        
        df['open_time'] = pd.to_numeric(df['open_time'])
        df['close_time'] = df['open_time'] + 3600000  # 1 hora en ms
        df['timestamp'] = pd.to_datetime(df['open_time'], unit='ms')
        
        now = int(datetime.now(timezone.utc()).timestamp() * 1000)
        df = df[df['close_time'] < now].tail(limit)
        return df if len(df) >= 2 else None
    except Exception as e:
        print(f"Error Bybit: {e}")
        return None

def detect_signal_with_projection(df):
    df = df.copy()
    bb = ta_lib.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
    df['bb_low'] = bb.bollinger_lband()
    df['sma20'] = bb.bollinger_mavg()
    rsi_indicator = ta_lib.momentum.RSIIndicator(close=df['close'], window=14)
    df['rsi'] = rsi_indicator.rsi()
    
    if len(df) < 2: return None
    prev, curr = df.iloc[-2], df.iloc[-1]
    bb_reentry = (prev['close'] < prev['bb_low']) and (curr['close'] > curr['bb_low'])
    rsi_conf = (prev['rsi'] < 35) and (curr['rsi'] >= 35)
    if not (bb_reentry and rsi_conf): return None

    price = curr['close']
    bb_low = curr['bb_low']
    sma20 = curr['sma20']
    rsi = curr['rsi']
    entry_aggressive = price
    entry_technical = bb_low
    entry_conservative = bb_low * 0.995

    def calc_projection(entry):
        return (sma20 - entry) / entry * 100 if entry > 0 else 0

    return {
        'symbol': "DUMMY",
        'price': price,
        'bb_low': bb_low,
        'sma20': sma20,
        'rsi': rsi,
        'entries': {
            'aggressive': entry_aggressive,
            'technical': entry_technical,
            'conservative': entry_conservative
        },
        'projections': {
            'aggressive': calc_projection(entry_aggressive),
            'technical': calc_projection(entry_technical),
            'conservative': calc_projection(entry_conservative)
        },
        'target_price': sma20
    }

# === TELEGRAM ===
async def send_telegram_alert(signal):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        from telegram import Bot
        bot = Bot(token=TELEGRAM_TOKEN)
        msg = (
            f"🟢 ¡Nueva señal!\n{signal['symbol']}\n"
            f"Entrada técnica: {signal['entries']['technical']:.4f}\n"
            f"Rebote: {signal['projections']['technical']:+.1f}%\n"
            f"Objetivo: {signal['target_price']:.4f}"
        )
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)
    except Exception as e:
        print(f"Error Telegram: {e}")

# === LOOP AUTOMÁTICO ===
async def auto_scan_loop():
    while True:
        print("🔍 Auto-scan iniciado...")
        for symbol in SYMBOLS:
            df = fetch_closed_1h_candles(symbol)
            if df is not None:
                signal = detect_signal_with_projection(df)
                if signal:
                    signal['symbol'] = symbol
                    print(f"✅ Señal detectada: {symbol}")
                    await send_telegram_alert(signal)
        print("😴 Próximo escaneo en 1 hora...")
        await asyncio.sleep(3600)

# === INTERFAZ WEB ===
def main(page: ft.Page):
    page.title = "Mean Reversion Scanner"
    page.scroll = "auto"
    results_col = ft.Column()

    def on_scan(e):
        results_col.controls.clear()
        for symbol in SYMBOLS:
            df = fetch_closed_1h_candles(symbol)
            if df is None:
                results_col.controls.append(ft.Text(f"⚠️ {symbol}: Sin datos", color="orange"))
                continue
            signal = detect_signal_with_projection(df)
            if signal:
                signal['symbol'] = symbol
                asyncio.run(send_telegram_alert(signal))
                e = signal['entries']
                p = signal['projections']
                card = ft.Card(content=ft.Container(
                    content=ft.Column([
                        ft.Text(f"🟢 {symbol}", color="green800", weight="bold"),
                        ft.Text(f"Precio: {signal['price']:.4f} | RSI: {signal['rsi']:.1f}"),
                        ft.Text(f"• Técnica: {e['technical']:.4f} → {p['technical']:+.1f}%"),
                        ft.Text(f"🎯 Objetivo: {signal['target_price']:.4f}", color="blue800")
                    ]),
                    padding=10
                ))
                results_col.controls.append(card)
            else:
                results_col.controls.append(ft.Text(f"❌ {symbol}: Sin señal", color="red700"))
        page.update()

    page.add(
        ft.Text("Mean Reversion Scanner", size=24, weight="bold"),
        ft.ElevatedButton("🔍 Escanear Ahora", on_click=on_scan),
        results_col
    )

# === EJECUCIÓN ===
if __name__ == "__main__":
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        loop_thread = threading.Thread(target=lambda: asyncio.run(auto_scan_loop()), daemon=True)
        loop_thread.start()
        print("🔄 Loop automático iniciado en segundo plano")

    ft.app(target=main, view=ft.WEB_BROWSER, port=int(os.environ.get("PORT", 8000)))
