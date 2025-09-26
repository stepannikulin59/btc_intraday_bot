import os
import math
import yaml
import time
import asyncio
import logging
from logging.handlers import RotatingFileHandler
import pandas as pd
from typing import Optional, Tuple, Dict, Any
from dotenv import load_dotenv
from pybit.unified_trading import HTTP

import telegram_bot  # ВАЖНО: импортируем модуль целиком, чтобы видеть актуальный TRADING_ACTIVE

from state import load_state, set_state, get_state
from bybit_data import (
    fetch_kline, fetch_open_interest, fetch_funding_rate, fetch_basis,
    fetch_long_short_ratio, fetch_wallet_equity, fetch_instrument_info,
    fetch_available_balance
)
from indicators import calculate_indicators
from scoring import score_signal
from regime import detect_regime
from risk import (
    compute_position_size, place_market_order,
    compute_initial_sl_tp, update_stops_and_partials, should_add_position
)

# --------- logging ----------
os.makedirs("logs", exist_ok=True)
root = logging.getLogger()
root.setLevel(logging.INFO)
fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
ch = logging.StreamHandler(); ch.setFormatter(fmt); root.addHandler(ch)
fh1 = RotatingFileHandler("logs/exec.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
fh1.setFormatter(fmt); fh1.setLevel(logging.INFO); root.addHandler(fh1)
fh2 = RotatingFileHandler("logs/errors.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
fh2.setFormatter(fmt); fh2.setLevel(logging.ERROR); root.addHandler(fh2)
logger = logging.getLogger("bot")

# --------- env / config ----------
load_dotenv()
with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)

SYMBOL = config.get("symbol", "BTCUSDT")
TESTNET = bool(config.get("testnet", False))
RISK_PCT = float(config.get("risk_pct", 0.01))
SIGNAL_THRESHOLD = float(config.get("signal_threshold", 1.8))
LOWER_TF = str(config.get("lower_tf", "1"))
COOLDOWN_SEC = int(config.get("cooldown_sec", 30))

ATR_K_SL  = float(config.get("atr_k_sl", 1.0))
ATR_K_TP1 = float(config.get("atr_k_tp1", 1.0))
ATR_K_TP2 = float(config.get("atr_k_tp2", 2.0))
ATR_K_BE  = float(config.get("atr_k_be", 0.5))
TRAILING  = str(config.get("trailing", "supertrend"))
TRAIL_K_ATR = float(config.get("trailing_k_atr", 1.0))

FALLBACK_SL_PCT = float(config.get("fallback_sl_pct", 0.008))
FALLBACK_TP_PCT = float(config.get("fallback_tp_pct", 0.012))

LAST_ENTRY_TS: Optional[float] = None
LAST_ADD_TS: Optional[float] = None

# Для фиксации «полного выхода»
_prev_has_pos = False
_prev_side = None
_prev_size = 0.0
_prev_entry = None

# --------- session ----------
session = HTTP(
    testnet=TESTNET,
    api_key=os.getenv("BYBIT_API_KEY"),
    api_secret=os.getenv("BYBIT_API_SECRET"),
)

# --------- analytics safe import ----------
def _safe_save_trade(row: Dict[str, Any]) -> None:
    try:
        from analytics import save_trade  # type: ignore
        save_trade(row)
    except Exception:
        import csv
        os.makedirs("logs", exist_ok=True)
        fn = "logs/trades.csv"
        header = ["ts","symbol","side","qty","price","event","sl","tp","score","regime","pnl"]
        write_header = not os.path.exists(fn)
        with open(fn, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=header)
            if write_header:
                w.writeheader()
            w.writerow({
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": row.get("symbol"),
                "side": row.get("side"),
                "qty": row.get("qty"),
                "price": row.get("price"),
                "event": row.get("event"),
                "sl": row.get("sl"),
                "tp": row.get("tp"),
                "score": row.get("score"),
                "regime": row.get("regime"),
                "pnl": row.get("pnl"),
            })


def _ensure_leverage():
    try:
        session.set_leverage(category="linear", symbol=SYMBOL, buyLeverage="10", sellLeverage="10")
    except Exception as e:
        logger.info(f"set_leverage: {e}")


def candles_to_df(candles: list) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame()
    df = pd.DataFrame(candles, columns=["timestamp","open","high","low","close","volume","turnover"])
    df["timestamp"] = pd.to_datetime(pd.to_numeric(df["timestamp"], errors="coerce"), unit="ms")
    for c in ["open","high","low","close","volume","turnover"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def _has_open_position(sess: HTTP, symbol: str) -> Tuple[bool, Optional[dict]]:
    try:
        resp = sess.get_positions(category="linear", symbol=symbol)
        if resp.get("retCode") != 0:
            logger.warning(f"⚠️ get_positions retCode={resp.get('retCode')} retMsg={resp.get('retMsg')}")
            return False, None
        lst = (resp.get("result") or {}).get("list", []) or []
        for p in lst:
            sz = p.get("size")
            if sz is None:
                continue
            try:
                if abs(float(sz)) > 0:
                    return True, p
            except Exception:
                continue
        return False, None
    except Exception as e:
        logger.warning(f"⚠️ get_positions error: {e}")
        return True, None


async def analyze_once() -> dict | None:
    candles = fetch_kline(session, SYMBOL, LOWER_TF, 200)
    if not candles:
        logger.warning("⚠️ Нет свечей от Bybit")
        return None
    df = candles_to_df(candles)
    df = calculate_indicators(df)

    metrics = {
        "oi": fetch_open_interest(session, SYMBOL),
        "funding": fetch_funding_rate(session, SYMBOL),
        "basis": fetch_basis(session, SYMBOL),
        "lsr": fetch_long_short_ratio(session, SYMBOL),
    }
    total, breakdown = score_signal(df, metrics)
    regime = detect_regime(df, metrics)
    last_price = float(df.iloc[-1]["close"])
    equity = fetch_wallet_equity(session) or 1000.0
    return {
        "df": df, "metrics": metrics, "score": total, "breakdown": breakdown,
        "regime": regime, "price": last_price, "equity": equity,
    }


def _round_down(v: float, step: float) -> float:
    if step <= 0:
        return v
    return math.floor(v / step) * step


async def main_loop():
    global LAST_ENTRY_TS, LAST_ADD_TS
    global _prev_has_pos, _prev_side, _prev_size, _prev_entry

    load_state()
    _ensure_leverage()
    await telegram_bot.send_telegram_message("🚀 Торговый цикл запущен (старт в СТОПЕ — включай /on)")

    while True:
        try:
            # читаем флаг напрямую из модуля telegram_bot
            if not telegram_bot.TRADING_ACTIVE:
                await asyncio.sleep(5)
                continue

            res = await analyze_once()
            if not res:
                await asyncio.sleep(10)
                continue

            df = res["df"]; price = res["price"]; equity = res["equity"]
            score = res["score"]; br = res["breakdown"]; regime = res["regime"]

            logger.info(f"Score={score:+.2f} | TA={br['TA']:+.2f} | Data={br['BybitData']:+.2f} | Volume={br['Volume']:+.2f} | Volatility={br['Volatility']:+.2f} | Regime={regime}")

            info = fetch_instrument_info(session, SYMBOL)
            lot_step = float(info.get("lotSizeFilter", {}).get("qtyStep", 0.001)) if info else 0.001
            min_qty  = float(info.get("lotSizeFilter", {}).get("minOrderQty", 0.001)) if info else 0.001
            min_val  = float(info.get("lotSizeFilter", {}).get("minOrderAmt", 5.0)) if info else 5.0

            avail = fetch_available_balance(session, "USDT")

            has_pos, pos = _has_open_position(session, SYMBOL)

            # --------- Полный выход (была позиция → нет позиции) ----------
            if _prev_has_pos and not has_pos:
                st = get_state(SYMBOL)
                entry_price = st.get("entry_price") or _prev_entry or price
                pnl = None
                try:
                    if _prev_side == "Buy":
                        pnl = (_prev_size * (price - float(entry_price)))
                    elif _prev_side == "Sell":
                        pnl = (_prev_size * (float(entry_price) - price))
                except Exception:
                    pnl = None

                _safe_save_trade({
                    "symbol": SYMBOL, "side": "Close", "qty": _prev_size,
                    "price": price, "event": "exit", "sl": None, "tp": None,
                    "score": score, "regime": regime, "pnl": pnl
                })
                await telegram_bot.send_telegram_message(
                    f"🔚 Полный выход {SYMBOL}. Примерный PnL: {pnl:.2f} USDT" if pnl is not None else "🔚 Полный выход из позиции."
                )
                set_state(SYMBOL, "entry_price", None)
                set_state(SYMBOL, "last_sl", None)
                set_state(SYMBOL, "took_tp1", False)
                set_state(SYMBOL, "took_tp2", False)

            # --------- Есть позиция: сопровождаем и (возможно) добираем ----------
            if has_pos and pos:
                from risk import update_stops_and_partials, should_add_position, compute_position_size, place_market_order, compute_initial_sl_tp
                side_pos = pos.get("side")
                size_pos = float(pos.get("size") or 0)
                entry = float(pos.get("avgPrice") or price)

                def _on_partial(row: Dict[str, Any]):
                    row.update({"score": score, "regime": regime})
                    _safe_save_trade(row)
                    asyncio.create_task(telegram_bot.send_telegram_message(
                        f"🎯 Partial {row['event']}: {row['side']} {row['symbol']} qty={row['qty']}"
                    ))

                update_stops_and_partials(
                    session, SYMBOL, side_pos, entry, size_pos, price, df.iloc[-1],
                    config, lot_step, on_partial=_on_partial
                )

                now = time.time()
                can_cooldown = (LAST_ADD_TS is None) or (now - LAST_ADD_TS >= COOLDOWN_SEC)
                if score > SIGNAL_THRESHOLD and avail >= min_val and can_cooldown:
                    if should_add_position(SYMBOL, side_pos, price, df.iloc[-1], TRAILING, TRAIL_K_ATR):
                        raw_qty = compute_position_size(
                            equity=equity, price=price, risk_pct=RISK_PCT,
                            min_qty=min_qty, qty_step=lot_step, min_order_value=min_val
                        )
                        max_qty = max(0.0, avail / price)
                        qty = max(min(raw_qty, max_qty), 0.0)
                        qty = max(_round_down(qty, lot_step), 0.0)

                        if qty * price >= min_val and qty > 0:
                            resp = place_market_order(session, SYMBOL, side_pos, qty)
                            if isinstance(resp, dict) and resp.get("retCode") == 0:
                                LAST_ADD_TS = now
                                await telegram_bot.send_telegram_message(f"➕ Добор: {side_pos} {SYMBOL} qty={qty}")
                                _safe_save_trade({
                                    "symbol": SYMBOL, "side": side_pos, "qty": qty,
                                    "price": price, "event": "add", "sl": None, "tp": None,
                                    "score": score, "regime": regime, "pnl": None
                                })
                        else:
                            logger.info("Добор отменён: недостаточно средств для минимального ордера.")

                _prev_has_pos, _prev_side, _prev_size, _prev_entry = True, side_pos, size_pos, entry
                await asyncio.sleep(15)
                continue

            # --------- Нет позиции: возможен новый вход ----------
            if score > SIGNAL_THRESHOLD:
                if COOLDOWN_SEC > 0 and LAST_ENTRY_TS:
                    rest = COOLDOWN_SEC - (time.time() - LAST_ENTRY_TS)
                    if rest > 0:
                        logger.info(f"Cooldown {rest:.0f}s — пропускаем вход")
                        await asyncio.sleep(15)
                        _prev_has_pos = False
                        continue

                if avail < min_val:
                    logger.warning(f"⚠️ Недостаточно средств: доступно {avail:.2f} USDT, нужно ≥ {min_val:.2f}")
                    await asyncio.sleep(15)
                    _prev_has_pos = False
                    continue

                from risk import compute_position_size, place_market_order, compute_initial_sl_tp
                raw_qty = compute_position_size(
                    equity=equity, price=price, risk_pct=RISK_PCT,
                    min_qty=min_qty, qty_step=lot_step, min_order_value=min_val
                )
                max_qty = max(0.0, avail / price)
                qty = max(min(raw_qty, max_qty), 0.0)
                qty = max(_round_down(qty, lot_step), 0.0)
                if qty * price < min_val or qty <= 0:
                    logger.warning("⚠️ Недостаточно средств для минимального ордера.")
                    await asyncio.sleep(15)
                    _prev_has_pos = False
                    continue

                side = "Buy" if br["TA"] >= 0 else "Sell"
                last = df.iloc[-1]
                atr = float(last.get("atr")) if pd.notna(last.get("atr")) else None

                levels = compute_initial_sl_tp(
                    price, side, atr, ATR_K_SL, ATR_K_TP1, ATR_K_TP2,
                    FALLBACK_SL_PCT, FALLBACK_TP_PCT
                )
                sl = levels["sl"]
                tp = levels["tp2"]

                logger.info(f"Вход: side={side} qty={qty} price≈{price:.2f} SL={sl} TP={tp} (avail≈{avail:.2f})")
                resp = place_market_order(session, SYMBOL, side, qty, stop_loss=sl, take_profit=tp)
                if isinstance(resp, dict) and resp.get("retCode") == 0:
                    LAST_ENTRY_TS = time.time()

                    _, p = _has_open_position(session, SYMBOL)
                    avg = float(p.get("avgPrice")) if p and p.get("avgPrice") else price

                    set_state(SYMBOL, "entry_price", avg)
                    set_state(SYMBOL, "last_sl", sl)
                    set_state(SYMBOL, "took_tp1", False)
                    set_state(SYMBOL, "took_tp2", False)

                    await telegram_bot.send_telegram_message(
                        f"✅ Ордер: {side} {SYMBOL}\n"
                        f"Qty: {qty}\nPrice≈ {avg:.2f}\nSL: {sl} | TP(общ): {tp}\n"
                        f"Score: {score:+.2f} (TA {br['TA']:+.2f}, Data {br['BybitData']:+.2f}, Vol {br['Volume']:+.2f}, Vola {br['Volatility']:+.2f})\n"
                        f"Regime: {regime}"
                    )
                    _safe_save_trade({
                        "symbol": SYMBOL, "side": side, "qty": qty,
                        "price": avg, "event": "entry", "sl": sl, "tp": tp,
                        "score": score, "regime": regime, "pnl": None
                    })

                    _prev_has_pos, _prev_side, _prev_size, _prev_entry = True, side, qty, avg
                else:
                    _prev_has_pos = False
            else:
                logger.info("Нет условия для входа (скор ниже порога)")
                _prev_has_pos = has_pos

        except Exception as e:
            logger.error(f"❌ main_loop error: {e}")
            try:
                await telegram_bot.send_telegram_message(f"❌ Ошибка: {e}")
            except Exception:
                pass

        await asyncio.sleep(15)


if __name__ == "__main__":
    async def start():
        await asyncio.gather(telegram_bot.init_telegram(), main_loop())
    asyncio.run(start())
