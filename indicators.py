import os
import yaml
import numpy as np
import pandas as pd
from ta.trend import EMAIndicator, ADXIndicator
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange

# читаем параметры супер-тренда из config.yaml (если нет — используем дефолты)
ST_PERIOD = 10
ST_MULTIPLIER = 3.0
if os.path.exists("config.yaml"):
    try:
        _cfg = yaml.safe_load(open("config.yaml", "r"))
        ST_PERIOD = int(_cfg.get("supertrend_period", ST_PERIOD))
        ST_MULTIPLIER = float(_cfg.get("supertrend_multiplier", ST_MULTIPLIER))
    except Exception:
        pass


def _supertrend(df: pd.DataFrame, period: int, multiplier: float) -> pd.DataFrame:
    """
    Классический SuperTrend:
      1) ATR(period)
      2) basic_ub/lb = (high+low)/2 ± multiplier*ATR
      3) final_ub/lb (скользящие барьеры)
      4) линия supertrend и направление
    """
    st = df.copy()

    atr_ind = AverageTrueRange(
        high=st["high"], low=st["low"], close=st["close"], window=period, fillna=False
    )
    atr = atr_ind.average_true_range()

    hl2 = (st["high"] + st["low"]) / 2.0
    st["basic_ub"] = hl2 + multiplier * atr
    st["basic_lb"] = hl2 - multiplier * atr

    final_ub = st["basic_ub"].copy()
    final_lb = st["basic_lb"].copy()

    for i in range(1, len(st)):
        # верхняя
        if st["close"].iloc[i-1] > final_ub.iloc[i-1]:
            final_ub.iloc[i] = st["basic_ub"].iloc[i]
        else:
            final_ub.iloc[i] = min(st["basic_ub"].iloc[i], final_ub.iloc[i-1])

        # нижняя
        if st["close"].iloc[i-1] < final_lb.iloc[i-1]:
            final_lb.iloc[i] = st["basic_lb"].iloc[i]
        else:
            final_lb.iloc[i] = max(st["basic_lb"].iloc[i], final_lb.iloc[i-1])

    st_dir = np.ones(len(st), dtype=int)
    st_line = pd.Series(index=st.index, dtype="float64")

    for i in range(len(st)):
        if i == 0:
            st_line.iloc[i] = final_lb.iloc[i]
            st_dir[i] = 1
            continue

        prev_line = st_line.iloc[i-1]
        if (prev_line == final_ub.iloc[i-1]) and (st["close"].iloc[i] <= final_ub.iloc[i]):
            st_line.iloc[i] = final_ub.iloc[i]
            st_dir[i] = -1
        elif (prev_line == final_ub.iloc[i-1]) and (st["close"].iloc[i] > final_ub.iloc[i]):
            st_line.iloc[i] = final_lb.iloc[i]
            st_dir[i] = 1
        elif (prev_line == final_lb.iloc[i-1]) and (st["close"].iloc[i] >= final_lb.iloc[i]):
            st_line.iloc[i] = final_lb.iloc[i]
            st_dir[i] = 1
        elif (prev_line == final_lb.iloc[i-1]) and (st["close"].iloc[i] < final_lb.iloc[i]):
            st_line.iloc[i] = final_ub.iloc[i]
            st_dir[i] = -1
        else:
            st_line.iloc[i] = final_lb.iloc[i]
            st_dir[i] = 1

    out = pd.DataFrame(index=st.index)
    out["supertrend"] = st_line
    out["supertrend_upper"] = final_ub
    out["supertrend_lower"] = final_lb
    out["supertrend_dir"] = st_dir
    return out


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    EMA(9,21,50,200), RSI(14), ADX(14), ATR(14), VWAP, OBV, VolMA(20),
    + классический SuperTrend (параметры из config.yaml).
    """
    df = df.copy()

    # EMA
    for p in [9, 21, 50, 200]:
        df[f"ema_{p}"] = EMAIndicator(close=df["close"], window=p, fillna=False).ema_indicator()

    # RSI / ADX / ATR
    df["rsi"] = RSIIndicator(close=df["close"], window=14, fillna=False).rsi()
    adx_ind = ADXIndicator(high=df["high"], low=df["low"], close=df["close"], window=14, fillna=False)
    df["adx"] = adx_ind.adx()
    atr_ind = AverageTrueRange(high=df["high"], low=df["low"], close=df["close"], window=14, fillna=False)
    df["atr"] = atr_ind.average_true_range()

    # VWAP (простая кумулятивная)
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].replace(0, np.nan)
    df["vwap"] = (tp * vol).cumsum() / vol.cumsum()

    # OBV
    obv = [0.0]
    for i in range(1, len(df)):
        if df["close"].iloc[i] > df["close"].iloc[i-1]:
            obv.append(obv[-1] + df["volume"].iloc[i])
        elif df["close"].iloc[i] < df["close"].iloc[i-1]:
            obv.append(obv[-1] - df["volume"].iloc[i])
        else:
            obv.append(obv[-1])
    df["obv"] = obv

    # Volume MA
    df["vol_ma_20"] = df["volume"].rolling(20, min_periods=1).mean()

    # SuperTrend (классический)
    st = _supertrend(df[["high", "low", "close"]], period=ST_PERIOD, multiplier=ST_MULTIPLIER)
    for c in ["supertrend", "supertrend_upper", "supertrend_lower", "supertrend_dir"]:
        df[c] = st[c]

    return df
