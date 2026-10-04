import streamlit as st
import pandas as pd
import math
import time
from datetime import datetime, timedelta
from engine import get_blofin_ticker, get_blofin_candles, calculate_liquidity_and_sweep_logic, send_telegram_alert

# Seitenkonfiguration im Dark Mode Design
st.set_page_config(page_title="MyCipher - Orderflow & Liquidity Engine", layout="wide", page_icon="⚡")

# Modernisiertes Cyber-Terminal CSS
st.markdown("""
    <style>
    .main { background-color: #05070a; color: #f0f6fc; }
    .stMetric { 
        background: linear-gradient(135deg, #0d1117 0%, #161b22 100%);
        padding: 12px; 
        border-radius: 10px; 
        border: 1px solid #30363d; 
        box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
    }
    .stMetric [data-testid="stMetricValue"] { 
        font-size: 20px !important; 
        font-weight: 700;
        color: #ffffff;
    }
    .stMetric [data-testid="stMetricLabel"] {
        font-size: 10px !important;
        letter-spacing: 1px;
        color: #8b949e;
    }
    .reasoning-box { 
        background: linear-gradient(135deg, #0d1117 0%, #111622 100%);
        padding: 20px; 
        border-radius: 10px; 
        border: 1px solid #30363d; 
        height: 100%;
    }
    .macro-regime-box {
        background: linear-gradient(135deg, #161b22 0%, #0d1117 100%);
        padding: 16px 20px;
        border-radius: 10px;
        border: 1px solid #30363d;
        margin-bottom: 20px;
        box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5);
    }
    .history-pill { 
        background: #0d1117; 
        border: 1px solid #30363d; 
        color: #8b949e; 
        padding: 6px 12px; 
        border-radius: 6px; 
        font-size: 11px; 
        font-family: 'Courier New', monospace; 
    }
    section[data-testid="stSidebar"] {
        background-color: #080b10;
        border-right: 1px solid #21262d;
    }
    </style>
""", unsafe_allow_html=True)

# --- SESSION STATE INITIALISIERUNG ---
if "signal_history" not in st.session_state:
    st.session_state.signal_history = []

if "prev_confidence" not in st.session_state:
    st.session_state.prev_confidence = 50

if "stable_signal_type" not in st.session_state:
    st.session_state.stable_signal_type = "Warten..."

if "signal_counter" not in st.session_state:
    st.session_state.signal_counter = 0

if "last_sent_signal" not in st.session_state:
    st.session_state.last_sent_signal = None

# --- HILFSFUNKTION FÜR KERZEN-COUNTDOWN ---
def get_candle_countdown(tf_str):
    now = datetime.now()
    minutes_map = {
        "1m": 1, "3m": 3, "6m": 6, "12m": 12, "15m": 15, 
        "24m": 24, "30m": 30, "1h": 60, "4h": 240, 
        "6h": 360, "12h": 720, "24h": 1440, "1d": 1440
    }
    tf_mins = minutes_map.get(tf_str, 15)
    
    total_mins_day = now.hour * 60 + now.minute
    rem_mins = tf_mins - (total_mins_day % tf_mins)
    rem_secs = 60 - now.second
    if rem_secs == 60:
        rem_secs = 0
        rem_mins -= 1
    if rem_mins < 0:
        rem_mins = 0
        
    next_candle_time = (now + timedelta(minutes=rem_mins, seconds=rem_secs)).strftime('%H:%M:%S')
    return f"~{rem_mins}m {rem_secs}s", next_candle_time

# --- SEITENLEISTE ---
st.sidebar.title("⚡ MyCipher")
st.sidebar.caption("AI-Driven Signals. Autonomous Management.")

asset = st.sidebar.selectbox("ASSET", ["BTC", "ETH", "SOL", "XRP"])
exchange = st.sidebar.selectbox("EXCHANGE", ["Blofin", "BloFin"])
margin_mode = st.sidebar.selectbox("MARGIN MODE", ["Isolated", "Cross"])
selected_tf_mode = st.sidebar.selectbox("TIMEFRAME", ["6m", "12m", "15m", "24m", "30m", "1h", "4h", "6h", "12h", "1d"])

min_probability = st.sidebar.slider("MIN PROBABILITY", min_value=50, max_value=85, value=70, step=5)

st.sidebar.markdown("---")
st.sidebar.markdown("<p style='font-size: 11px; color: #8b949e; margin-bottom: 4px; letter-spacing: 0.5px;'>MULTI-TIMEFRAME SCANNER</p>", unsafe_allow_html=True)
multi_tf_enabled = st.sidebar.toggle("🌐 Alle TFs gleichzeitig scannen", value=False)

st.sidebar.markdown("---")
st.sidebar.markdown("<p style='font-size: 11px; color: #8b949e; margin-bottom: 4px; letter-spacing: 0.5px;'>SYSTEM SETTINGS</p>", unsafe_allow_html=True)
auto_refresh = st.sidebar.toggle("🔄 Auto-Live-Loop (60s)", value=True)
enable_telegram = st.sidebar.toggle("📱 Telegram Push", value=True)

st.sidebar.markdown("---")

col_btn1, col_btn2 = st.sidebar.columns(2)
with col_btn1:
    run_clicked = st.button("▶ RUN", use_container_width=True)
with col_btn2:
    test_clicked = st.button("🧪 TEST", use_container_width=True)

if run_clicked:
    st.rerun()

if test_clicked:
    test_msg = "🟢 *TEST-NACHRICHT*\n\nDein MyCipher Telegram-Bot funktioniert einwandfrei! 🚀"
    success = send_telegram_alert(test_msg)
    if success:
        st.sidebar.success("Gesendet!")
    else:
        st.sidebar.error("Fehler!")

# --- HAUPTBEREICH CONTAINER FÜR LIVE-UPDATES ---
placeholder = st.empty()

with placeholder.container():
    live_price = get_blofin_ticker(asset)
    
    # --- MACRO REGIME ERKENNUNG (DAILY & 12h TREND) ---
    df_macro = get_blofin_candles(asset, "1d")
    macro_trend = "Neutral / Seitwärts"
    macro_color = "#8b949e"
    macro_badge = "⚖️ RANGE MARKT"
    
    if df_macro is not None and len(df_macro) > 20:
        # Gleitender Durchschnitt (SMA 20 auf Daily Basis als Trendfilter)
        df_macro['SMA20'] = df_macro['close'].rolling(window=20).mean()
        last_close = df_macro['close'].iloc[-1]
        last_sma = df_macro['SMA20'].iloc[-1]
        
        if last_close > last_sma:
            macro_trend = "BULLRUN (Struktureller Aufwärtstrend)"
            macro_color = "#3fb950"
            macro_badge = "🚀 MACRO BULLRUN"
        else:
            macro_trend = "BÄRRUN (Distribution / Abwärtstrend)"
            macro_color = "#f85149"
            macro_badge = "🐻 MACRO BÄRRUN"

    # --- MULTI-TIMEFRAME LOGIK MIT GEWINN- & TREND-FILTERUNG ---
    master_signal_text = ""
    if multi_tf_enabled:
        scan_timeframes = ["12m", "15m", "24m", "30m", "1h", "4h", "6h", "24h"]
        mtf_results = []
        
        for tf in scan_timeframes:
            df_c = get_blofin_candles(asset, tf)
            ep, sl, t1, t2, conf, stype, rh, rl = calculate_liquidity_and_sweep_logic(df_c, live_price)
            if ep is not None:
                is_l = "Long" in stype or t1 > ep
                pos = "Long" if is_l else "Short"
                
                # Trend-Alignment Check mit dem Macro-Regime
                alignment = "✅ Trend-Konform"
                if "BULLRUN" in macro_trend and pos == "Short":
                    alignment = "⚠️ Gegen-Trend (Korrektur)"
                elif "BÄRRUN" in macro_trend and pos == "Long":
                    alignment = "⚠️ Gegen-Trend (Korrektur)"
                
                mtf_results.append({
                    "Timeframe": tf,
                    "Richtung": pos,
                    "Konfidenz_Val": conf,
                    "Konfidenz": f"{conf}%",
                    "Signal-Typ": stype,
                    "Macro-Alignment": alignment,
                    "Entry": f"{ep:,.1f}"
                })
            else:
                mtf_results.append({
                    "Timeframe": tf,
                    "Richtung": "Keine Daten",
                    "Konfidenz_Val": 0,
                    "Konfidenz": "0%",
                    "Signal-Typ": "N/A",
                    "Macro-Alignment": "-",
                    "Entry": "-"
                })
        
        # Sortiere nach höchster Konfidenz
        mtf_results = sorted(mtf_results, key=lambda x: x["Konfidenz_Val"], reverse=True)
        
        if mtf_results and mtf_results[0]["Konfidenz_Val"] >= min_probability:
            top_res = mtf_results[0]
            master_signal_text = f"🎯 **Top Swing-Setup:** {top_res['Richtung']} auf **{top_res['Timeframe']}** ({top_res['Konfidenz']} Konfidenz) | Status: {top_res['Macro-Alignment']}"
        
        df_candles = get_blofin_candles(asset, selected_tf_mode)
        entry_price, stop_loss, tp1, tp2, confidence, signal_type, r_high, r_low = calculate_liquidity_and_sweep_logic(df_candles, live_price)
    else:
        df_candles = get_blofin_candles(asset, selected_tf_mode)
        entry_price, stop_loss, tp1, tp2, confidence, signal_type, r_high, r_low = calculate_liquidity_and_sweep_logic(df_candles, live_price)

    if entry_price is None:
        entry_price, stop_loss, tp1, tp2, confidence, signal_type = 90000.0, 91000.0, 88000.0, 85000.0, 50, "Keine Daten"

    countdown_str, next_time_str = get_candle_countdown(selected_tf_mode)

    # --- FILTER-LOGIK ---
    if confidence < min_probability:
        raw_position_text = "Warten..."
        pos_color = "#8b949e"
        dot_icon = "⏳"
    else:
        raw_is_long = "Long" in signal_type or tp1 > entry_price
        raw_position_text = "Long" if raw_is_long else "Short"
        pos_color = "#3fb950" if raw_is_long else "#f85149"
        dot_icon = "🟢" if raw_is_long else "🔴"

    # --- SIGNAL-STABILISIERUNG ---
    if raw_position_text != st.session_state.stable_signal_type:
        st.session_state.signal_counter += 1
        if st.session_state.signal_counter >= 2 or raw_position_text == "Warten...":
            st.session_state.stable_signal_type = raw_position_text
            st.session_state.signal_counter = 0
    else:
        st.session_state.signal_counter = 0

    position_text = st.session_state.stable_signal_type
    is_active_signal = position_text in ["Long", "Short"]

    # --- TELEGRAM VERSAND ---
    if enable_telegram and is_active_signal and confidence >= min_probability:
        signal_key = f"{asset}_{selected_tf_mode}_{position_text}_{entry_price:.1f}"
        if st.session_state.last_sent_signal != signal_key:
            alert_msg = (
                f"🚨 *MYCIPHER SWING SIGNAL* 🚨\n\n"
                f"• **Asset:** {asset}USDT ({exchange.upper()})\n"
                f"• **Macro-Regime:** {macro_trend}\n"
                f"• **Timeframe:** {selected_tf_mode}\n"
                f"• **Richtung:** {position_text}\n"
                f"• **Konfidenz:** {confidence}%\n"
                f"• **Entry:** `{entry_price:,.1f}`\n"
                f"• **Stop Loss:** `{stop_loss:,.1f}`\n"
                f"• **TP1:** `{tp1:,.1f}`\n"
                f"• **TP2:** `{tp2:,.1f}`"
            )
            success = send_telegram_alert(alert_msg)
            if success:
                st.session_state.last_sent_signal = signal_key

    prev_conf = st.session_state.prev_confidence
    st.session_state.prev_confidence = confidence

    tp1_diff_pct = ((tp1 - entry_price) / entry_price) * 100 if is_active_signal else 0.0
    tp2_diff_pct = ((tp2 - entry_price) / entry_price) * 100 if is_active_signal else 0.0
    tp1_pct = f"+{tp1_diff_pct:.1f}%" if tp1_diff_pct >= 0 else f"{tp1_diff_pct:.1f}%"
    tp2_pct = f"+{tp2_diff_pct:.1f}%" if tp2_diff_pct >= 0 else f"{tp2_diff_pct:.1f}%"

    current_time_str = pd.Timestamp.now().strftime('%H:%M:%S')

    # --- HISTORIE ---
    history_string = f"{dot_icon} {asset} {selected_tf_mode} [{current_time_str}]"
    if not st.session_state.signal_history or st.session_state.signal_history[-1].split("]")[0] != history_string.split("]")[0]:
        st.session_state.signal_history.append(history_string)
        if len(st.session_state.signal_history) > 4:
            st.session_state.signal_history.pop(0)

    pill_cols = st.columns(4)
    history_items = st.session_state.signal_history[-4:]
    while len(history_items) < 4:
        history_items.insert(0, "⏳ Warte auf Sync...")

    for i, col in enumerate(pill_cols):
        with col:
            st.markdown(f"<div class='history-pill' style='text-align:center;'>{history_items[i]}</div>", unsafe_allow_html=True)

    st.write("")

    # --- TITEL & MAKRO-REGIME DASHBOARD ---
    st.markdown(f"<h2 style='margin:0; font-weight:800; letter-spacing:-0.5px;'>{asset}USDT <span style='font-size:15px; color:#8b949e; font-weight:400;'>@ {exchange.upper()} (SWING)</span></h2>", unsafe_allow_html=True)
    st.write("")

    # Professionelles Macro-Regime UI Panel
    st.markdown(f"""
    <div class="macro-regime-box">
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <div>
                <span style="color: #8b949e; font-size: 11px; font-weight: 600; letter-spacing: 1px; display: block; margin-bottom: 2px;">LANGFRISTIGER STRUKTUR-SCAN (DAILY / WEEKLY)</span>
                <span style="color: {macro_color}; font-size: 16px; font-weight: 800;">{macro_trend}</span>
            </div>
            <div style="background: {macro_color}22; border: 1px solid {macro_color}; padding: 6px 14px; border-radius: 6px; color: {macro_color}; font-weight: bold; font-size: 12px;">
                {macro_badge}
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # --- MULTI-TIMEFRAME MATRIX ---
    if multi_tf_enabled:
        if master_signal_text:
            st.markdown(f"""
            <div style="background: #0d1117; padding: 12px 18px; border-radius: 8px; border: 1px solid #30363d; margin-bottom: 16px; font-size: 13px; color: #f0f6fc;">
                {master_signal_text}
            </div>
            """, unsafe_allow_html=True)
            
        st.markdown("### 🌐 Multi-Timeframe Konfluenz-Matrix (Mit Makro-Trend-Abgleich)")
        df_mtf_display = pd.DataFrame(mtf_results)[["Timeframe", "Richtung", "Konfidenz", "Signal-Typ", "Macro-Alignment", "Entry"]]
        st.dataframe(df_mtf_display, use_container_width=True, hide_index=True)
        st.write("")

    # --- METRIKEN ---
    r1_cols = st.columns(4)
    r1_cols[0].metric("TIMEFRAME", selected_tf_mode)
    r1_cols[1].metric("POSITION", position_text)
    r1_cols[2].metric("LEVERAGE", "15x")
    r1_cols[3].metric("ENTRY", f"{entry_price:,.1f}" if is_active_signal else "Warten...")

    r2_cols = st.columns(3)
    r2_cols[0].metric("STOP", f"{stop_loss:,.1f}" if is_active_signal else "-")
    r2_cols[1].metric("TP1", f"{tp1:,.1f}" if is_active_signal else "-", tp1_pct if is_active_signal else None)
    r2_cols[2].metric("TP2", f"{tp2:,.1f}" if is_active_signal else "-", tp2_pct if is_active_signal else None)

    st.write("")

    # --- UNTERER BEREICH: REASONING & STATUS ---
    col_left, col_right = st.columns([2.5, 1])

    with col_left:
        reasoning_text = (
            f"<b>Telegram-Alarm:</b> Bot im Standby (wartet auf >= {min_probability}%)."
            if not is_active_signal else
            f"<b>Telegram-Alarm:</b> Signal aktiv! Push-Benachrichtigung gesendet."
        )
        mc_score_text = (
            f"<b>Makro-Status:</b> Ausgerichtet auf {macro_trend}."
        )

        st.markdown(f"""
        <div class="reasoning-box">
            <div style="font-size: 12px; color: #8b949e; margin-bottom: 6px;">
                {reasoning_text}
            </div>
            <div style="font-size: 12px; color: #8b949e; margin-bottom: 12px;">
                {mc_score_text}
            </div>
            <h4 style="margin-top: 0; font-size: 15px; color: #f0f6fc; letter-spacing: -0.3px;">Reasoning & Macro-Auswertung:</h4>
            <ul style="color: #8b949e; font-size: 13px; margin-bottom: 0; line-height: 1.6;">
                <li><b>Trend-Filter:</b> Das System bewertet den übergeordneten Kursverlauf auf Basis von Tagesstrukturen.</li>
                <li><b>Swing-Management:</b> Setups, die mit dem Haupttrend laufen, erhalten die höchste Priorität für saubere Positionsaufstockungen.</li>
                <li><b>Empfehlung:</b> Nutze die Matrix, um primär TFs zu traden, die ein <i>Trend-Konform</i>-Alignment aufweisen.</li>
            </ul>
        </div>
        """, unsafe_allow_html=True)

    with col_right:
        is_ready = confidence >= min_probability
        status_color = "#3fb950" if is_ready else "#8b949e"
        status_label = "🟢 Trigger Bereit" if is_ready else "⏳ Wartet auf Trigger"

        with st.container():
            st.markdown(f"""
            <div style="background: linear-gradient(135deg, #0d1117 0%, #161b22 100%); padding: 18px; border-radius: 10px; border: 1px solid #30363d; height: 100%; box-shadow: 0 4px 16px rgba(0,0,0,0.4);">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                    <span style="color: #8b949e; font-size: 11px; font-weight: 600; letter-spacing: 1px;">BOT-SCHWELLENWERT</span>
                    <span style="color: #3fb950; font-size: 13px; font-weight: bold;">&gt;= {min_probability}%</span>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px;">
                    <div>
                        <span style="font-size: 9px; color: #8b949e; letter-spacing: 1px; display: block;">AKTUELL</span>
                        <span style="font-size: 28px; font-weight: 800; color: {pos_color};">{confidence}%</span>
                    </div>
                    <div style="text-align: right;">
                        <span style="font-size: 10px; color: {status_color}; font-weight: 600;">{status_label}</span>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            st.progress(min(100, max(0, confidence)) / 100)

if auto_refresh:
    time.sleep(60)
    st.rerun()