import streamlit as st
import pandas as pd
import time
from datetime import datetime, timedelta
from engine import get_blofin_ticker, get_blofin_candles, calculate_liquidity_and_sweep_logic, send_telegram_alert

st.set_page_config(page_title="MyCipher - Quant Dashboard", layout="wide", page_icon="⚡")

st.markdown("""
    <style>
    .main { background-color: #05070a; color: #f0f6fc; }
    .stMetric { 
        background: linear-gradient(135deg, #0d1117 0%, #161b22 100%);
        padding: 10px; 
        border-radius: 8px; 
        border: 1px solid #30363d; 
    }
    .macro-regime-box {
        background: linear-gradient(135deg, #161b22 0%, #0d1117 100%);
        padding: 14px 18px;
        border-radius: 8px;
        border: 1px solid #30363d;
        margin-bottom: 16px;
    }
    section[data-testid="stSidebar"] { background-color: #080b10; border-right: 1px solid #21262d; }
    </style>
""", unsafe_allow_html=True)

# --- SESSION STATE ---
if "signal_history" not in st.session_state:
    st.session_state.signal_history = []
if "stable_signal_type" not in st.session_state:
    st.session_state.stable_signal_type = "Warten..."
if "signal_counter" not in st.session_state:
    st.session_state.signal_counter = 0
if "last_sent_signal" not in st.session_state:
    st.session_state.last_sent_signal = None

# --- SEITENLEISTE (Risiko-Einstellungen) ---
st.sidebar.title("⚡ MyCipher Quant")
asset = st.sidebar.selectbox("ASSET", ["BTC", "ETH", "SOL", "XRP"])
exchange = st.sidebar.selectbox("EXCHANGE", ["Blofin"])
selected_tf_mode = st.sidebar.selectbox("TIMEFRAME", ["15m", "30m", "1h", "4h", "6h", "1d"])

st.sidebar.markdown("---")
st.sidebar.markdown("<p style='font-size: 11px; color: #8b949e;'>RISIKO & PORTFOLIO</p>", unsafe_allow_html=True)
account_size = st.sidebar.number_input("Kontogröße (USDT)", value=10000.0, step=500.0)
risk_pct = st.sidebar.slider("Kontorisiko pro Trade (%)", min_value=0.5, max_value=3.0, value=1.0, step=0.5)
leverage = st.sidebar.selectbox("Hebel", [1, 5, 10, 15, 20], index=3)

min_probability = st.sidebar.slider("MIN CONFIDENCE (%)", min_value=50, max_value=85, value=70, step=5)
multi_tf_enabled = st.sidebar.toggle("🌐 Multi-TF Konfluenz-Scan", value=False)
auto_refresh = st.sidebar.toggle("🔄 Auto-Live-Loop (60s)", value=True)
enable_telegram = st.sidebar.toggle("📱 Telegram Push", value=True)

if st.sidebar.button("▶ RUN / REFRESH", use_container_width=True):
    st.rerun()

# --- HAUPTBEREICH LAYOUT OPTIMIERUNG ---
placeholder = st.empty()

with placeholder.container():
    live_price = get_blofin_ticker(asset)
    
    # Makro-Regime (Daily Trend)
    df_macro = get_blofin_candles(asset, "1d")
    macro_trend, macro_color, macro_badge = "Neutral / Seitwärts", "#8b949e", "⚖️ RANGE"
    if df_macro is not None and len(df_macro) > 20:
        df_macro['SMA20'] = df_macro['close'].rolling(window=20).mean()
        if df_macro['close'].iloc[-1] > df_macro['SMA20'].iloc[-1]:
            macro_trend, macro_color, macro_badge = "BULLRUN (Aufwärtstrend)", "#3fb950", "🚀 BULL"
        else:
            macro_trend, macro_color, macro_badge = "BÄRRUN (Abwärtstrend)", "#f85149", "🐻 BEAR"

    # Multi-TF oder Single-TF Auswertung
    if multi_tf_enabled:
        scan_tfs = ["15m", "30m", "1h", "4h", "6h", "1d"]
        mtf_results = []
        for tf in scan_tfs:
            df_c = get_blofin_candles(asset, tf)
            ep, sl, t1, t2, conf, stype, rh, rl, req_m, pos_n = calculate_liquidity_and_sweep_logic(df_c, live_price, account_size, risk_pct, leverage)
            if ep is not None:
                pos = "Long" if "Long" in stype or t1 > ep else "Short"
                mtf_results.append({"Timeframe": tf, "Richtung": pos, "Konfidenz": f"{conf}%", "Signal": stype, "Entry": f"{ep:,.1f}"})
        st.markdown("### 🌐 Multi-Timeframe Matrix")
        st.dataframe(pd.DataFrame(mtf_results), use_container_width=True, hide_index=True)

    df_candles = get_blofin_candles(asset, selected_tf_mode)
    entry_price, stop_loss, tp1, tp2, confidence, signal_type, r_high, r_low, req_margin, pos_notional = calculate_liquidity_and_sweep_logic(
        df_candles, live_price, account_size, risk_pct, leverage
    )

    if entry_price is None:
        entry_price, stop_loss, tp1, tp2, confidence, signal_type, req_margin, pos_notional = 90000.0, 91000.0, 88000.0, 85000.0, 50, "Keine Daten", 0, 0

    is_long = "Long" in signal_type or tp1 > entry_price
    pos_text = "Long" if is_long else "Short"
    pos_color = "#3fb950" if is_long else "#f85149"

    # --- Sektion 1: Makro & Top Alarm (Fokus ganz oben) ---
    st.markdown(f"""
    <div class="macro-regime-box">
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <div>
                <span style="color: #8b949e; font-size: 10px; font-weight: bold; letter-spacing: 1px;">MAKRO-MARKTREGIME (DAILY)</span><br>
                <span style="color: {macro_color}; font-size: 15px; font-weight: 800;">{macro_trend}</span>
            </div>
            <div style="background: {macro_color}22; border: 1px solid {macro_color}; padding: 4px 12px; border-radius: 6px; color: {macro_color}; font-weight: bold; font-size: 11px;">
                {macro_badge}
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # --- Sektion 2: Kernmetriken & Risiko-Manager (Zentrale Sektion) ---
    st.markdown(f"### {asset}USDT <span style='font-size:14px; color:#8b949e;'>@{exchange} ({selected_tf_mode})</span>", unsafe_allow_html=True)
    
    m_cols = st.columns(4)
    m_cols[0].metric("SIGNAL", pos_text, signal_type)
    m_cols[1].metric("ENTRY", f"{entry_price:,.1f}")
    m_cols[2].metric("STOP LOSS", f"{stop_loss:,.1f}")
    m_cols[3].metric("KONFIDENZ", f"{confidence}%")

    r_cols = st.columns(4)
    r_cols[0].metric("TP 1", f"{tp1:,.1f}")
    r_cols[1].metric("TP 2", f"{tp2:,.1f}")
    r_cols[2].metric("BENÖTIGTE MARGIN", f"{req_margin:,.1f} USDT", f"{leverage}x Hebel")
    r_cols[3].metric("POS-GRÖßE (Notional)", f"{pos_notional:,.1f} USDT", f"Risiko: {risk_pct}%")

    st.write("")

    # --- Sektion 3: Reasoning & Untergeordnete Details (Ausklappbar / Clean) ---
    with st.expander("📊 Quant Reasoning & Technische Details anzeigen", expanded=True):
        st.markdown(f"""
        - **Risikomanagement:** Bei einer Kontogröße von `{account_size:,.1f} USDT` und einem erlaubten Risiko von `{risk_pct}%` ist die Positionsgröße exakt auf den ATR-basierten Stop-Loss (`{stop_loss:,.1f}`) geeicht.
        - **Cipher A & B Logik:** Die Auswertung gewichtet Oszillatoren-Crosses ($\pm 53$ Level) zusammen mit Trend-EMAs.
        - **Liquiditäts-Anker:** Abgesichert über Rolling Highs (`{r_high:,.1f}`) und Lows (`{r_low:,.1f}`).
        """)

if auto_refresh:
    time.sleep(60)
    st.rerun()