from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import pandas as pd
import asyncio
import os
import threading
import requests
from datetime import datetime, timezone
import ta as ta_lib

# === CONFIGURACIÓN ===
SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'RENDERUSDT']
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# === FUNCIONES TÉCNICAS (igual que antes) ===
def fetch_closed_1h_candles(symbol, limit=50):
    try:
        url = "https://api.bybit.com/v5/market/kline"
        params = {"category": "spot", "symbol": symbol, "interval": "60", "limit": limit + 1}
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        if data.get("retCode") != 0 or not data.get("result", {}).get("list"):
            return None
        klines = data["result"]["list"]
        df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col])
        df['timestamp'] = pd.to_numeric(df['timestamp'])
        df['close_time'] = df['timestamp'] + 3600000
        now = int(datetime.now(timezone.utc()).timestamp() * 1000)
        df = df[df['close_time'] < now].tail(limit)
        return df if len(df) >= 2 else None
    except Exception as e:
        print(f"Error Bybit API: {e}")
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
    entry_tech = bb_low
    proj = (sma20 - bb_low) / bb_low * 100

    return {
        'symbol': "DUMMY",
        'price': price,
        'bb_low': bb_low,
        'sma20': sma20,
        'rsi': curr['rsi'],
        'entry_technical': entry_tech,
        'projection': proj
    }

async def send_telegram_alert(signal):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        msg = f"🟢 ¡Nueva señal!\n{signal['symbol']}\nEntrada: {signal['entry_technical']:.4f}\nRebote: {signal['projection']:+.1f}%\nObjetivo: {signal['sma20']:.4f}"
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
    except Exception as e:
        print(f"Telegram error: {e}")

# === APP WEB ===
app = FastAPI()
templates = Jinja2Templates(directory=".")

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/scan", response_class=HTMLResponse)
async def scan(request: Request):
    results = []
    for symbol in SYMBOLS:
        df = fetch_closed_1h_candles(symbol)
        if df is None:
            results.append({"symbol": symbol, "status": "error"})
            continue
        signal = detect_signal_with_projection(df)
        if signal:
            signal["symbol"] = symbol
            await send_telegram_alert(signal)
            results.append({"symbol": symbol, "status": "signal", **signal})
        else:
            results.append({"symbol": symbol, "status": "no_signal"})
    
    html = '<div style="font-family: Arial; padding: 20px;">'
    html += '<h2>Mean Reversion Scanner</h2>'
    html += '<a href="/" style="display: inline-block; margin-bottom: 20px; padding: 8px 16px; background: #007bff; color: white; text-decoration: none; border-radius: 4px;">← Volver</a>'
    for r in results:
        if r["status"] == "signal":
            html += f'''
            <div style="border: 2px solid #28a745; border-radius: 8px; padding: 12px; margin: 10px 0; background: #f8fff9;">
                <h3 style="color: #28a745;">🟢 {r["symbol"]}</h3>
                <p>Precio: {r["price"]:.4f} | RSI: {r["rsi"]:.1f}</p>
                <p>• Técnica: {r["entry_technical"]:.4f} → Rebote: {r["projection"]:+.1f}%</p>
                <p><strong>🎯 Objetivo: {r["sma20"]:.4f}</strong></p>
            </div>
            '''
        elif r["status"] == "no_signal":
            html += f'<p>❌ {r["symbol"]}: Sin señal</p>'
        else:
            html += f'<p>⚠️ {r["symbol"]}: Sin datos</p>'
    html += '</div>'
    return HTMLResponse(html)
