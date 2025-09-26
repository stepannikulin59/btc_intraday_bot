import os
import pandas as pd

def update_trades_with_pnl():
    if not os.path.exists("trades.csv"):
        return None
    try:
        df = pd.read_csv("trades.csv")
        if df.empty or "exit_price" not in df.columns:
            return None
        updated = 0
        for i, row in df.iterrows():
            if pd.isna(row.get("exit_price")):
                continue
            entry = float(row["entry"]); exit_p = float(row["exit_price"]); qty = float(row["qty"])
            pnl_usd = (exit_p - entry) * qty if row["side"] == "long" else (entry - exit_p) * qty
            pnl_pct = (pnl_usd / (entry * qty)) * 100
            df.at[i, "pnl_usd"] = pnl_usd
            df.at[i, "pnl_pct"] = pnl_pct
            df.at[i, "result"] = "win" if pnl_usd > 0 else "loss"
            updated += 1
        if updated > 0:
            df.to_csv("trades.csv", index=False)
            return f"Обновлено {updated} сделок"
    except Exception as e:
        return f"Ошибка: {e}"
