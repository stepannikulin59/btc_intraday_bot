import numpy as np
import pandas as pd


def detect_regime(df: pd.DataFrame, metrics: dict) -> str:
    """
    Простой классификатор режима:
    - trend: ADX>25 и EMA9>EMA21>EMA50, basis>0, OI растет
    - mean-reversion: ADX<18 и basis≈0
    - иначе: neutral
    """
    if df.empty:
        return "neutral"

    last = df.iloc[-1]
    adx = float(last.get("adx", np.nan))
    ema9, ema21, ema50 = last.get("ema_9"), last.get("ema_21"), last.get("ema_50")
    basis = metrics.get("basis")
    oi_list = metrics.get("oi", [])

    trend_stack = (ema9 is not None and ema21 is not None and ema50 is not None and ema9 > ema21 > ema50)
    strong_adx = (not np.isnan(adx)) and adx > 25.0
    basis_pos = (basis is not None and basis > 0)

    oi_rising = False
    try:
        # для OI list может быть списком словарей/списков — нормализуем в float
        vals = []
        for x in oi_list[-10:]:
            # V5 обычно отдает dict c ключами, но бывает список
            if isinstance(x, dict):
                v = x.get("openInterest")
            else:
                v = x[1] if len(x) > 1 else None
            if v is not None:
                vals.append(float(v))
        if len(vals) >= 2 and vals[-1] > np.mean(vals[:-1]):
            oi_rising = True
    except Exception:
        pass

    if strong_adx and trend_stack and (basis_pos or oi_rising):
        return "trend"

    if (not np.isnan(adx)) and adx < 18.0 and (basis is None or abs(basis) < 1e-6):
        return "mean-reversion"

    return "neutral"
