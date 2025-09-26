import math
import os
from typing import Dict, Tuple, Any, List

import pandas as pd
import yaml


# === Конфиг весов и порогов ===
_DEFAULT_CFG = {
    "weights": {
        "TA": 0.45,
        "BybitData": 0.25,
        "Volume": 0.15,
        "Volatility": 0.15,
    },
    # пороги/параметры для частных сигналов
    "volume": {
        "surge_hi": 1.5,   # volume / vol_ma_20 выше — считаем всплеском
        "surge_lo": 0.7,   # ниже — «болото»
        "score_hi": 0.6,   # сколько дать в субскор за сильный всплеск
        "score_lo": -0.4,  # штраф за низкий объём
    },
    "volatility": {
        "atr_ma_window": 20,
        "hot_ratio": 1.2,  # atr / atr_ma выше — рынок «горячий»
        "cold_ratio": 0.8, # ниже — «холодный»
        "score_hot": 0.3,
        "score_cold": -0.3,
        "z_momentum_hi": 0.6,   # (close-ema21)/atr
        "z_momentum_lo": -0.6,
        "score_z_hi": 0.2,
        "score_z_lo": -0.2,
    },
    "ta": {
        "ema_stack_bonus": 0.4,   # EMA9>EMA21>EMA50 (бычий) / обратное — -0.4
        "adx_trend": 25.0,        # ADX выше — тренд
        "adx_score": 0.2,         # вклад, если тренд сильный
        "rsi_hot": 70.0,          # RSI экстремумы
        "rsi_cold": 30.0,
        "rsi_score": -0.1,        # легкий штраф за экстремумы (перекуп/перепрод)
        "vwap_alignment": 0.1,    # цена выше VWAP при Long / ниже при Short — маленький бонус
    },
    "bybit": {
        "funding_pos": 0.05,  # funding > 0 — небольшой «бычий» перекос
        "funding_neg": -0.05,
        "basis_pos": 0.1,     # положительный базис — «бычий»
        "basis_neg": -0.1,
        "lsr_pos": 0.1,       # long/short ratio > 1 — «бычий», <1 — «медвежий»
        "lsr_neg": -0.1,
    },
}


def _load_cfg() -> dict:
    try:
        if os.path.exists("config.yaml"):
            with open("config.yaml", "r") as f:
                cfg = yaml.safe_load(f) or {}
            weights = (cfg.get("weights") or {}).copy()
            # вложенные секции можем частично переопределять
            merged = _DEFAULT_CFG.copy()
            for k in ["weights", "volume", "volatility", "ta", "bybit"]:
                merged[k] = {**_DEFAULT_CFG[k], **(cfg.get(k, {}) or {})}
            return merged
    except Exception:
        pass
    return _DEFAULT_CFG


_CFG = _load_cfg()


# === Утилиты ===
def _safe_last_float(x: Any, default: float = 0.0) -> float:
    try:
        if isinstance(x, list) and x:
            x = x[-1]
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _ema_stack_score(df: pd.DataFrame) -> float:
    """Бонус/штраф за порядок EMA (тренд)."""
    last = df.iloc[-1]
    s = _CFG["ta"]["ema_stack_bonus"]
    if last["ema_9"] > last["ema_21"] > last["ema_50"]:
        return +s
    if last["ema_9"] < last["ema_21"] < last["ema_50"]:
        return -s
    return 0.0


def _adx_score(df: pd.DataFrame) -> float:
    last = df.iloc[-1]
    if pd.isna(last.get("adx")):
        return 0.0
    if last["adx"] >= _CFG["ta"]["adx_trend"]:
        return _CFG["ta"]["adx_score"]
    return 0.0


def _rsi_score(df: pd.DataFrame) -> float:
    last = df.iloc[-1]
    rsi = last.get("rsi")
    if pd.isna(rsi):
        return 0.0
    if rsi >= _CFG["ta"]["rsi_hot"] or rsi <= _CFG["ta"]["rsi_cold"]:
        return _CFG["ta"]["rsi_score"]  # лёгкий штраф за экстремумы
    return 0.0


def _vwap_alignment_score(df: pd.DataFrame) -> float:
    last = df.iloc[-1]
    # маленький бонус за согласие с VWAP
    if pd.isna(last.get("vwap")):
        return 0.0
    return _CFG["ta"]["vwap_alignment"] if last["close"] >= last["vwap"] else -_CFG["ta"]["vwap_alignment"]


def _ta_subscore(df: pd.DataFrame) -> float:
    s = 0.0
    s += _ema_stack_score(df)
    s += _adx_score(df)
    s += _rsi_score(df)
    s += _vwap_alignment_score(df)
    # нормировка (суммарно ожидаем диапазон около [-1; +1])
    return max(min(s, 1.0), -1.0)


def _volume_subscore(df: pd.DataFrame) -> float:
    last = df.iloc[-1]
    vol = float(last.get("volume") or 0)
    vma = float(last.get("vol_ma_20") or 0)
    if vma <= 0:
        return 0.0
    surge = vol / max(vma, 1e-9)
    if surge >= _CFG["volume"]["surge_hi"]:
        return _CFG["volume"]["score_hi"]
    if surge <= _CFG["volume"]["surge_lo"]:
        return _CFG["volume"]["score_lo"]
    # интерполяция в нейтральной зоне
    # между 0.7 и 1.5 плавно движемся к нулю
    mid = 1.0
    if surge >= mid:
        frac = (surge - mid) / (max(_CFG["volume"]["surge_hi"] - mid, 1e-9))
        return frac * _CFG["volume"]["score_hi"]
    else:
        frac = (mid - surge) / (max(mid - _CFG["volume"]["surge_lo"], 1e-9))
        return -frac * abs(_CFG["volume"]["score_lo"])


def _volatility_subscore(df: pd.DataFrame) -> float:
    win = int(_CFG["volatility"]["atr_ma_window"])
    if "atr" not in df.columns or df["atr"].isna().all():
        return 0.0
    atr = float(df.iloc[-1]["atr"])
    if atr <= 0:
        return 0.0
    atr_ma = df["atr"].rolling(win, min_periods=1).mean().iloc[-1]
    ratio = atr / max(atr_ma, 1e-9)

    s = 0.0
    if ratio >= _CFG["volatility"]["hot_ratio"]:
        s += _CFG["volatility"]["score_hot"]
    elif ratio <= _CFG["volatility"]["cold_ratio"]:
        s += _CFG["volatility"]["score_cold"]

    # ATR-нормированный момент относительно EMA21
    ema21 = float(df.iloc[-1].get("ema_21") or df.iloc[-1]["close"])
    z = (df.iloc[-1]["close"] - ema21) / max(atr, 1e-9)
    if z >= _CFG["volatility"]["z_momentum_hi"]:
        s += _CFG["volatility"]["score_z_hi"]
    elif z <= _CFG["volatility"]["z_momentum_lo"]:
        s += _CFG["volatility"]["score_z_lo"]

    return max(min(s, 1.0), -1.0)


def _bybit_data_subscore(metrics: Dict[str, Any]) -> float:
    s = 0.0
    # funding
    funding = _safe_last_float(metrics.get("funding"))
    if funding > 0:
        s += _CFG["bybit"]["funding_pos"]
    elif funding < 0:
        s += _CFG["bybit"]["funding_neg"]

    # basis
    basis = _safe_last_float(metrics.get("basis"))
    if basis > 0:
        s += _CFG["bybit"]["basis_pos"]
    elif basis < 0:
        s += _CFG["bybit"]["basis_neg"]

    # long/short ratio (lsr)
    # metrics["lsr"] ожидаем списком словарей или значений
    lsr_list: List[Any] = metrics.get("lsr") or []
    lsr_val = None
    if lsr_list:
        try:
            last = lsr_list[-1]
            if isinstance(last, dict):
                # возможные ключи: "longShortRatio" или похожие
                for k in ["longShortRatio", "ratio", "value"]:
                    if k in last:
                        lsr_val = float(last[k]); break
            else:
                lsr_val = float(last)
        except Exception:
            lsr_val = None
    if lsr_val is not None:
        if lsr_val > 1.0:
            s += _CFG["bybit"]["lsr_pos"]
        elif lsr_val < 1.0:
            s += _CFG["bybit"]["lsr_neg"]

    return max(min(s, 1.0), -1.0)


def score_signal(df: pd.DataFrame, metrics: Dict[str, Any]) -> Tuple[float, Dict[str, float]]:
    """
    Возвращает:
      total_score: float
      breakdown: {"TA":..., "BybitData":..., "Volume":..., "Volatility":...}
    Каждая компонента ограничена [-1; +1], затем взвешивается весами из конфига.
    """
    ta = _ta_subscore(df)
    volm = _volume_subscore(df)
    vola = _volatility_subscore(df)
    byb = _bybit_data_subscore(metrics)

    w = _CFG["weights"]
    total = ta * w["TA"] + byb * w["BybitData"] + volm * w["Volume"] + vola * w["Volatility"]

    breakdown = {
        "TA": round(ta, 3),
        "BybitData": round(byb, 3),
        "Volume": round(volm, 3),
        "Volatility": round(vola, 3),
    }
    return round(total, 2), breakdown
