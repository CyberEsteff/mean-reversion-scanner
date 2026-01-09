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
    """
    Obtiene datos históricos de CoinGecko (equivalente a velas 1H cerradas).
    Soporta: BTC, ETH, SOL, RENDER → mapeados a IDs de CoinGecko.
    """
    # Mapeo de símbolos a IDs de CoinGecko
    symbol_map = {
        'BTCUSDT': 'bitcoin',
        'ETHUSDT': 'ethereum',
        'SOLUSDT': 'solana',
        'RENDERUSDT': 'render-token'
    }
    
    if symbol not in symbol_map:
        return None
    
    coin_id = symbol_map[symbol]
    try:
        # CoinGecko: obtener datos por horas (máx 90 días, pero usamos últimos 50)
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
        params = {
            "vs_currency": "usd",
            "days": str(limit // 24 + 2),  # asegurar suficientes datos
            "interval": "hourly"
        }
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        if "prices" not in data or len(data["prices"]) < 2:
            return None
        
        # Extraer precios (timestamp, precio)
        prices = data["prices"]
        volumes = data.get("total_volumes", [[p[0], 0] for p in prices])
        
        # Crear DataFrame con formato similar a Binance
        df_data = []
        for i in range(1, len(prices)):
            timestamp_prev = prices[i-1][0]
            timestamp_curr = prices[i][0]
            open_price = prices[i-1][1]
            high_price = max(prices[i-1][1], prices[i][1])
            low_price = min(prices[i-1][1], prices[i][1])
            close_price = prices[i][1]
            volume = volumes[i][1] if i < len(volumes) else 0
            
            # Asegurar que el intervalo sea ~1 hora (3600000 ms)
            if timestamp_curr - timestamp_prev >= 3500000:
                df_data.append({
                    'timestamp': timestamp_prev,
                    'open': open_price,
                    'high': high_price,
                    'low': low_price,
                    'close': close_price,
                    'volume': volume,
                    'close_time': timestamp_curr
                })
        
        if len(df_data) < 2:
            return None
        
        df = pd.DataFrame(df_data[-limit:])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col])
        
        now = int(datetime.now(timezone.utc()).timestamp() * 1000)
        df = df[df['close_time'] < now].tail(limit)
        return df if len(df) >= 2 else None
        
    except Exception as e:
        print(f"Error CoinGecko para {symbol}: {e}")
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
