import os
from pathlib import Path
from typing import Dict, Optional
import pandas as pd

TRADES_CSV = Path("logs/trades.csv")


def _ensure_dir():
    TRADES_CSV.parent.mkdir(parents=True, exist_ok=True)


def load_trades(csv_path: Path = TRADES_CSV) -> pd.DataFrame:
    """Загрузить историю сделок из CSV."""
    if not csv_path.exists():
        return pd.DataFrame()
    df = pd.read_csv(csv_path)
    return df


def save_trade(trade: Dict, csv_path: Path = TRADES_CSV):
    """
    Сохраняем новую сделку (добавляем строку).
    Ожидаемые поля: ts, symbol, side, qty, price, event, sl, tp, score, regime, pnl
    Если ts не задан — проставим сейчас (UTC).
    """
    _ensure_dir()
    row = dict(trade)
    if "ts" not in row:
        row["ts"] = pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    df = load_trades(csv_path)
    new_df = pd.DataFrame([row])
    cols = ["ts","symbol","side","qty","price","event","sl","tp","score","regime","pnl"]
    for c in cols:
        if c not in new_df.columns:
            new_df[c] = None
    if not df.empty:
        for c in cols:
            if c not in df.columns:
                df[c] = None
        df = df[cols]
    out = pd.concat([df, new_df[cols]], ignore_index=True)
    out.to_csv(csv_path, index=False)


def daily_summary(csv_path: Path = TRADES_CSV) -> Dict[str, str]:
    """
    Краткий отчёт за сегодня: число записей, средний скор, суммарный PnL (если есть).
    """
    df = load_trades(csv_path)
    if df.empty or "ts" not in df.columns:
        return {"text": "Сегодня сделок не было."}

    try:
        df["date"] = pd.to_datetime(df["ts"]).dt.date
    except Exception:
        return {"text": "Сегодня сделок не было."}

    today = pd.Timestamp.utcnow().date()
    day_df = df[df["date"] == today]
    if day_df.empty:
        return {"text": "Сегодня сделок не было."}

    cnt = len(day_df)
    avg_score = day_df["score"].astype(float).fillna(0).mean() if "score" in day_df else 0.0
    pnl_sum = day_df["pnl"].astype(float).fillna(0).sum() if "pnl" in day_df else 0.0

    return {
        "text": f"Сделок: {cnt}\nСредний скоринг: {avg_score:.2f}\nСуммарный PnL (оценка): {pnl_sum:.2f} USDT"
    }
