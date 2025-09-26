import math
import logging
from typing import Optional, Dict, Any, Callable

from state import get_state, set_state

logger = logging.getLogger(__name__)


def _round_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return math.floor(value / step) * step


def compute_position_size(
    equity: float,
    price: float,
    risk_pct: float,
    min_qty: float = 0.001,
    qty_step: float = 0.001,
    min_order_value: float = 5.0
) -> float:
    if price <= 0 or equity <= 0:
        return 0.0
    risk_capital = equity * risk_pct
    raw_qty = max(risk_capital / (price * 0.01), min_qty)
    if raw_qty * price < min_order_value:
        raw_qty = min_order_value / price
    qty = max(_round_step(raw_qty, qty_step), min_qty)
    return round(qty, 6)


def place_market_order(
    session,
    symbol: str,
    side: str,
    qty: float,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
) -> Dict[str, Any]:
    try:
        params = dict(
            category="linear",
            symbol=symbol,
            side=side,
            orderType="Market",
            qty=str(qty),
            timeInForce="GoodTillCancel",
            reduceOnly=False,
        )
        if stop_loss is not None:
            params["stopLoss"] = str(stop_loss)
        if take_profit is not None:
            params["takeProfit"] = str(take_profit)

        resp = session.place_order(**params)
        logger.info(f"✅ place_market_order: {resp}")
        return resp
    except Exception as e:
        logger.error(f"❌ place_market_order error: {e}")
        return {"error": str(e)}


def compute_initial_sl_tp(
    price: float,
    side: str,
    atr: Optional[float],
    atr_k_sl: float,
    tp1_k: float,
    tp2_k: float,
    fb_sl_pct: float,
    fb_tp_pct: float
) -> Dict[str, float]:
    if atr and atr > 0:
        sl = price - atr_k_sl * atr if side == "Buy" else price + atr_k_sl * atr
        tp1 = price + tp1_k * atr if side == "Buy" else price - tp1_k * atr
        tp2 = price + tp2_k * atr if side == "Buy" else price - tp2_k * atr
    else:
        sl = price * (1 - fb_sl_pct) if side == "Buy" else price * (1 + fb_sl_pct)
        tp1 = price * (1 + fb_tp_pct) if side == "Buy" else price * (1 - fb_tp_pct)
        tp2 = price * (1 + 2 * fb_tp_pct) if side == "Buy" else price * (1 - 2 * fb_tp_pct)
    return {"sl": round(sl, 2), "tp1": round(tp1, 2), "tp2": round(tp2, 2)}


def should_add_position(
    symbol: str,
    side: str,
    price: float,
    last_row: Any,
    mode: str,
    trail_k_atr: float
) -> bool:
    st = get_state(symbol)
    prev_sl = st.get("last_sl")
    if prev_sl is None:
        return False

    if mode == "atr":
        atr_val = float(last_row.get("atr")) if last_row.get("atr") is not None else None
        if not atr_val or atr_val <= 0:
            return False
        new_sl = (price - trail_k_atr * atr_val) if side == "Buy" else (price + trail_k_atr * atr_val)
    else:
        if side == "Buy":
            new_sl = float(last_row.get("supertrend_lower", prev_sl))
        else:
            new_sl = float(last_row.get("supertrend_upper", prev_sl))

    return (new_sl >= prev_sl) if side == "Buy" else (new_sl <= prev_sl)


def update_stops_and_partials(
    session,
    symbol: str,
    side: str,
    entry_price: float,
    position_qty: float,
    price: float,
    last_row: Any,
    cfg: Dict[str, Any],
    lot_step: float,
    on_partial: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> None:
    atr = float(last_row.get("atr")) if last_row.get("atr") is not None else None
    st_lower = float(last_row.get("supertrend_lower")) if last_row.get("supertrend_lower") is not None else None
    st_upper = float(last_row.get("supertrend_upper")) if last_row.get("supertrend_upper") is not None else None

    state = get_state(symbol)
    if "entry_price" not in state:
        set_state(symbol, "entry_price", entry_price)
        state = get_state(symbol)

    if "took_tp1" not in state:
        set_state(symbol, "took_tp1", False); state = get_state(symbol)
    if "took_tp2" not in state:
        set_state(symbol, "took_tp2", False); state = get_state(symbol)

    last_sl = state.get("last_sl")

    tp1_k = float(cfg.get("atr_k_tp1", 1.0))
    tp2_k = float(cfg.get("atr_k_tp2", 2.0))
    be_k  = float(cfg.get("atr_k_be", 0.5))
    trailing = str(cfg.get("trailing", "supertrend"))
    trail_k_atr = float(cfg.get("trailing_k_atr", 1.0))
    ptp1 = float(cfg.get("partial_tp1_pct", 0.30))
    ptp2 = float(cfg.get("partial_tp2_pct", 0.30))

    if not atr or atr <= 0:
        return

    be_trigger = entry_price + be_k * atr if side == "Buy" else entry_price - be_k * atr
    tp1_price = entry_price + tp1_k * atr if side == "Buy" else entry_price - tp1_k * atr
    tp2_price = entry_price + tp2_k * atr if side == "Buy" else entry_price - tp2_k * atr

    desired_sl = last_sl
    if (side == "Buy" and price >= be_trigger) or (side == "Sell" and price <= be_trigger):
        breakeven = round(entry_price, 2)
        desired_sl = max(desired_sl, breakeven) if side == "Buy" else (min(desired_sl, breakeven) if desired_sl is not None else breakeven)

    if trailing == "atr":
        trail_sl = (price - trail_k_atr * atr) if side == "Buy" else (price + trail_k_atr * atr)
    else:
        trail_sl = st_lower if side == "Buy" else st_upper

    if trail_sl is not None:
        if desired_sl is None:
            desired_sl = trail_sl
        else:
            desired_sl = max(desired_sl, trail_sl) if side == "Buy" else min(desired_sl, trail_sl)

    if desired_sl is not None:
        new_sl = round(desired_sl, 2)
        if (last_sl is None) or (side == "Buy" and new_sl > last_sl) or (side == "Sell" and new_sl < last_sl):
            try:
                resp = session.set_trading_stop(category="linear", symbol=symbol, stopLoss=str(new_sl))
                logger.info(f"🔧 SL update → {new_sl}: {resp}")
                set_state(symbol, "last_sl", new_sl)
            except Exception as e:
                logger.warning(f"⚠️ set_trading_stop SL error: {e}")

    def _reduce_only(qty: float) -> None:
        q = max(_round_step(qty, lot_step), 0.0)
        if q <= 0:
            return
        params = dict(
            category="linear",
            symbol=symbol,
            side="Sell" if side == "Buy" else "Buy",
            orderType="Market",
            qty=str(q),
            reduceOnly=True,
            timeInForce="GoodTillCancel",
        )
        try:
            r = session.place_order(**params)
            logger.info(f"🎯 Partial TP filled qty={q}: {r}")
            if on_partial:
                on_partial({
                    "symbol": symbol, "side": "Sell" if side == "Buy" else "Buy",
                    "qty": float(q), "event": "partial_take_profit"
                })
        except Exception as e:
            logger.warning(f"⚠️ partial TP error: {e}")

    took_tp1 = state.get("took_tp1", False)
    took_tp2 = state.get("took_tp2", False)

    if not took_tp1 and ((side == "Buy" and price >= tp1_price) or (side == "Sell" and price <= tp1_price)):
        _reduce_only(position_qty * ptp1)
        set_state(symbol, "took_tp1", True)

    if not took_tp2 and ((side == "Buy" and price >= tp2_price) or (side == "Sell" and price <= tp2_price)):
        _reduce_only(position_qty * ptp2)
        set_state(symbol, "took_tp2", True)
