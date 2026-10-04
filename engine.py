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
    # Mapping angepasst an Streamlit Auswahl ("15m", "1h", "4h", "1d")
    tf_mapping = {"15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D"}
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
    if df is None or len(df) < 30:
        return None, None, None, None, 50, "Warten...", 0.0, 0.0

    current_price = live_price if live_price is not None else df["close"].iloc[-1]
    
    rolling_high = float(df["high"].iloc[-25:-2].max())
    rolling_low = float(df["low"].iloc[-25:-2].min())
    
    high_low = df["high"] - df["low"]
    high_close = np.abs(df["high"] - df["close"].shift())
    low_close = np.abs(df["low"] - df["close"].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(window=14).mean().iloc[-1]
    
    vol_sma = df["volume"].rolling(window=20).mean().iloc[-1]
    current_vol = df["volume"].iloc[-1]
    vol_spike = current_vol > (1.5 * vol_sma)
    
    last_high = df["high"].iloc[-1]
    last_low = df["low"].iloc[-1]
    last_close = current_price
    
    bullish_sweep = (last_low < rolling_low) and (last_close > rolling_low) and vol_spike
    bearish_sweep = (last_high > rolling_high) and (last_close < rolling_high) and vol_spike
    
    if bearish_sweep:
        signal_type = "Short (Liquidity Sweep & Rejection)"
        stop_loss = last_high + (0.5 * atr)
        tp1 = current_price - (2.0 * atr)
        tp2 = current_price - (3.5 * atr)
        confidence = 88
    elif bullish_sweep:
        signal_type = "Long (Liquidity Sweep & Rejection)"
        stop_loss = last_low - (0.5 * atr)
        tp1 = current_price + (2.0 * atr)
        tp2 = current_price + (3.5 * atr)
        confidence = 88
    else:
        # Trend-Bestimmung
        price_diff = current_price - df["close"].iloc[-6]
        is_up = price_diff >= 0
        signal_type = "Long Setup" if is_up else "Short Setup"
        
        if is_up:
            # LONG: SL unter echten Support (rolling_low), TP1 beim letzten Widerstand (rolling_high)
            stop_loss = rolling_low - (0.3 * atr)
            tp1 = rolling_high
            tp2 = rolling_high + (1.5 * atr)
        else:
            # SHORT: SL über echten Widerstand (rolling_high), TP1 beim letzten Support (rolling_low)
            stop_loss = rolling_high + (0.3 * atr)
            tp1 = rolling_low
            tp2 = rolling_low - (1.5 * atr)
        
        # Dynamische Konfidenz zwischen 30% und 78%
        confidence = int(min(78, max(30, 50 + abs(price_diff / atr) * 8)))

    return float(current_price), float(stop_loss), float(tp1), float(tp2), int(confidence), signal_type, rolling_high, rolling_low