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
    if df is None or len(df) < 40:
        return None, None, None, None, 50, "Warten...", 0.0, 0.0

    current_price = live_price if live_price is not None else df["close"].iloc[-1]
    
    # 1. S/R Anker (Rolling High/Low)
    rolling_high = float(df["high"].iloc[-25:-2].max())
    rolling_low = float(df["low"].iloc[-25:-2].min())
    
    # 2. ATR Volatilität
    high_low = df["high"] - df["low"]
    high_close = np.abs(df["high"] - df["close"].shift())
    low_close = np.abs(df["low"] - df["close"].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(window=14).mean().iloc[-1]
    
    # 3. RSI & Money Flow Index (MFI) Berechnung
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    raw_money_flow = typical_price * df["volume"]
    tp_diff = typical_price.diff()
    pos_flow = raw_money_flow.where(tp_diff > 0, 0).rolling(window=14).sum()
    neg_flow = raw_money_flow.where(tp_diff < 0, 0).rolling(window=14).sum()
    df['mfi'] = 100 - (100 / (1 + (pos_flow / neg_flow)))
    
    # 4. Divergenz-Erkennung
    older_low_idx = df["low"].iloc[-35:-16].idxmin()
    price_lower_low = df["low"].iloc[-1] < df["low"].loc[older_low_idx]
    rsi_higher_low = df["rsi"].iloc[-1] > df["rsi"].loc[older_low_idx]
    bullish_divergence = price_lower_low and rsi_higher_low

    older_high_idx = df["high"].iloc[-35:-16].idxmax()
    price_higher_high = df["high"].iloc[-1] > df["high"].loc[older_high_idx]
    rsi_lower_high = df["rsi"].iloc[-1] < df["rsi"].loc[older_high_idx]
    bearish_divergence = price_higher_high and rsi_lower_high

    vol_sma = df["volume"].rolling(window=20).mean().iloc[-1]
    current_vol = df["volume"].iloc[-1]
    vol_spike = current_vol > (1.5 * vol_sma)
    
    last_high = df["high"].iloc[-1]
    last_low = df["low"].iloc[-1]
    last_close = current_price
    
    bullish_sweep = (last_low < rolling_low) and (last_close > rolling_low) and vol_spike
    bearish_sweep = (last_high > rolling_high) and (last_close < rolling_high) and vol_spike
    
    # Signal-Priorisierung mit Divergenzen & S/R-Ankern
    if bearish_sweep or bearish_divergence:
        signal_type = "Short (Bearish Divergence / Sweep)" if bearish_divergence else "Short (Liquidity Sweep)"
        stop_loss = rolling_high + (0.3 * atr)
        if stop_loss <= current_price:
            stop_loss = current_price + (1.0 * atr)
            
        risk_distance = stop_loss - current_price
        min_tp1_distance = risk_distance * 1.2
        natural_tp1 = rolling_low if rolling_low < current_price else current_price - min_tp1_distance
        
        tp1 = min(natural_tp1, current_price - min_tp1_distance)
        tp2 = tp1 - (risk_distance * 1.5)
        confidence = 85 if bearish_divergence else 78
        
    elif bullish_sweep or bullish_divergence:
        signal_type = "Long (Bullish Divergence / Sweep)" if bullish_divergence else "Long (Liquidity Sweep)"
        stop_loss = rolling_low - (0.3 * atr)
        if stop_loss >= current_price:
            stop_loss = current_price - (1.0 * atr)
            
        risk_distance = current_price - stop_loss
        min_tp1_distance = risk_distance * 1.2
        natural_tp1 = rolling_high if rolling_high > current_price else current_price + min_tp1_distance
        
        tp1 = max(natural_tp1, current_price + min_tp1_distance)
        tp2 = tp1 + (risk_distance * 1.5)
        confidence = 85 if bullish_divergence else 78
        
    else:
        # Standard S/R Trend-Setup mit CRV Schutz
        price_diff = current_price - df["close"].iloc[-6]
        is_up = price_diff >= 0
        signal_type = "Long Setup" if is_up else "Short Setup"
        
        if is_up:
            stop_loss = rolling_low - (0.3 * atr)
            if stop_loss >= current_price:
                stop_loss = current_price - (1.0 * atr)
                
            risk_distance = current_price - stop_loss
            min_tp1_distance = risk_distance * 1.2
            natural_tp1 = rolling_high if rolling_high > current_price else current_price + min_tp1_distance
            
            tp1 = max(natural_tp1, current_price + min_tp1_distance)
            tp2 = tp1 + (risk_distance * 1.5)
        else:
            stop_loss = rolling_high + (0.3 * atr)
            if stop_loss <= current_price:
                stop_loss = current_price + (1.0 * atr)
                
            risk_distance = stop_loss - current_price
            min_tp1_distance = risk_distance * 1.2
            natural_tp1 = rolling_low if rolling_low < current_price else current_price - min_tp1_distance
            
            tp1 = min(natural_tp1, current_price - min_tp1_distance)
            tp2 = tp1 - (risk_distance * 1.5)
        
        # Konfidenz basierend auf Trendstärke und MFI
        current_mfi = float(df['mfi'].iloc[-1])
        base_conf = 50 + abs(price_diff / atr) * 6
        if is_up and current_mfi > 50:
            base_conf += 10
        elif not is_up and current_mfi < 50:
            base_conf += 10
            
        confidence = int(min(80, max(30, base_conf)))

    return float(current_price), float(stop_loss), float(tp1), float(tp2), int(confidence), signal_type, rolling_high, rolling_low