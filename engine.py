import requests
import pandas as pd
import numpy as np
import time
import os
import streamlit as st

def send_telegram_alert(message):
    bot_token = "8572342754:AAFLDHnyb96GTD0kI99pJ8X7UBRBz-rstlc"
    chat_id = "1688952472"

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
    }
    try:
        response = requests.post(url, json=payload, timeout=5)
        data = response.json()
        return data.get("ok", False)
    except Exception as e:
        print(f"Telegram Sende-Fehler: {e}")
        return False

def get_blofin_ticker(symbol):
    url = f"https://openapi.blofin.com/api/v1/market/ticker?instId={symbol}-USDT"
    try:
        response = requests.get(url, timeout=3.0)
        data = response.json()
        if "data" in data and len(data["data"]) > 0:
            return float(data["data"][0]["last"])
    except Exception as e:
        print(f"Ticker API-Fehler: {e}")
    return None

def get_blofin_candles(symbol, timeframe="15m", limit=100):
    tf_mapping = {
        "15m": "15m", 
        "30m": "30m",
        "1h": "1H", 
        "4h": "4H", 
        "6h": "6H", 
        "24h": "1D", 
        "24m": "1D", 
        "1d": "1D"
    }
    bar = tf_mapping.get(timeframe, "1H")
    timestamp_param = int(time.time() * 1000)
    url = f"https://openapi.blofin.com/api/v1/market/candles?instId={symbol}-USDT&bar={bar}&limit={limit}&_t={timestamp_param}"
    try:
        response = requests.get(url, timeout=4.0)
        data = response.json()
        if "data" in data and len(data["data"]) > 0:
            df = pd.DataFrame(data["data"], columns=["timestamp", "open", "high", "low", "close", "volume", "volCcy", "volCcyQuote", "confirm"])
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col])
            return df[::-1].reset_index(drop=True)
    except Exception as e:
        print(f"Kerzen API-Fehler: {e}")
    return None

def calculate_liquidity_and_sweep_logic(df, live_price=None):
    if df is None or len(df) < 40:
        return None, None, None, None, 50, "Warten...", 0.0, 0.0

    current_price = live_price if live_price is not None else float(df["close"].iloc[-1])
    
    # 1. S/R Anker (Rolling High/Low)
    rolling_high = float(df["high"].iloc[-25:-2].max())
    rolling_low = float(df["low"].iloc[-25:-2].min())
    
    # 2. ATR Volatilität
    high_low = df["high"] - df["low"]
    high_close = np.abs(df["high"] - df["close"].shift())
    low_close = np.abs(df["low"] - df["close"].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(window=14).mean().iloc[-1]
    
    # 3. VuManChu Cipher A / WaveTrend & EMA Ribbon Logik
    hlc3 = (df['high'] + df['low'] + df['close']) / 3
    chlen, avg_len, malen = 9, 13, 3
    esa = hlc3.ewm(span=chlen, adjust=False).mean()
    de = (abs(hlc3 - esa)).ewm(span=chlen, adjust=False).mean()
    ci = (hlc3 - esa) / (0.015 * de)
    wt1 = ci.ewm(span=avg_len, adjust=False).mean()
    wt2 = wt1.rolling(window=malen).mean()
    
    close = df['close']
    ema1 = close.ewm(span=5, adjust=False).mean()
    ema2 = close.ewm(span=11, adjust=False).mean()
    ema8 = close.ewm(span=34, adjust=False).mean()
    
    curr_wt1 = wt1.iloc[-1]
    curr_wt2 = wt2.iloc[-1]
    prev_wt1 = wt1.iloc[-2]
    prev_wt2 = wt2.iloc[-2]
    
    # Cipher A Signal-Bedingungen
    wt_cross_down = (prev_wt1 >= prev_wt2) and (curr_wt1 < curr_wt2) and (curr_wt2 >= 53)
    wt_cross_up = (prev_wt1 <= prev_wt2) and (curr_wt1 > curr_wt2) and (curr_wt2 <= -53)
    
    red_cross = (ema1.iloc[-2] >= ema2.iloc[-2]) and (ema1.iloc[-1] < ema2.iloc[-1])
    red_diamond = wt_cross_down
    blood_diamond = red_diamond and red_cross
    
    long_ema = (ema2.iloc[-2] <= ema8.iloc[-2]) and (ema2.iloc[-1] > ema8.iloc[-1])
    short_ema = (ema8.iloc[-2] <= ema2.iloc[-2]) and (ema8.iloc[-1] > ema2.iloc[-1])
    
    signal_type = "Neutral"
    confidence = 50
    
    # Priorisierung der Cipher A Muster & Sweeps
    if blood_diamond:
        signal_type = "Blood Diamond Short 🔴"
        confidence = 85
    elif red_diamond:
        signal_type = "Red Diamond Short 🔻"
        confidence = 78
    elif short_ema:
        signal_type = "EMA Short Cross 📉"
        confidence = 72
    elif long_ema:
        signal_type = "EMA Long Cross 📈"
        confidence = 76
    elif wt_cross_up:
        signal_type = "WaveTrend Long 🟢"
        confidence = 74
    else:
        # Fallback auf Liquiditäts-Sweeps / Trend
        vol_sma = df["volume"].rolling(window=20).mean().iloc[-1]
        current_vol = df["volume"].iloc[-1]
        vol_spike = current_vol > (1.5 * vol_sma)
        
        last_high = df["high"].iloc[-1]
        last_low = df["low"].iloc[-1]
        
        if (last_high > rolling_high) and (current_price < rolling_high) and vol_spike:
            signal_type = "Short (Liquidity Sweep)"
            confidence = 70
        elif (last_low < rolling_low) and (current_price > rolling_low) and vol_spike:
            signal_type = "Long (Liquidity Sweep)"
            confidence = 70
        else:
            price_diff = current_price - df["close"].iloc[-6]
            is_up = price_diff >= 0
            signal_type = "Long Setup" if is_up else "Short Setup"
            confidence = 58

    # 4. SL & TP Berechnung mit dynamischem CRV-Schutz
    is_short_signal = "Short" in signal_type or "Diamond" in signal_type and "Short" in signal_type
    
    if is_short_signal:
        stop_loss = rolling_high + (0.3 * atr)
        if stop_loss <= current_price:
            stop_loss = current_price + (1.0 * atr)
            
        risk_distance = stop_loss - current_price
        min_tp1_distance = risk_distance * 1.2
        natural_tp1 = rolling_low if rolling_low < current_price else current_price - min_tp1_distance
        
        tp1 = min(natural_tp1, current_price - min_tp1_distance)
        tp2 = tp1 - (risk_distance * 1.5)
    else:
        stop_loss = rolling_low - (0.3 * atr)
        if stop_loss >= current_price:
            stop_loss = current_price - (1.0 * atr)
            
        risk_distance = current_price - stop_loss
        min_tp1_distance = risk_distance * 1.2
        natural_tp1 = rolling_high if rolling_high > current_price else current_price + min_tp1_distance
        
        tp1 = max(natural_tp1, current_price + min_tp1_distance)
        tp2 = tp1 + (risk_distance * 1.5)

    return float(current_price), float(stop_loss), float(tp1), float(tp2), int(confidence), signal_type, float(rolling_high), float(rolling_low)