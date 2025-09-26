import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

def fetch_kline(session, symbol: str, interval: str = "1", limit: int = 200) -> List[list]:
    try:
        resp = session.get_kline(category="linear", symbol=symbol, interval=interval, limit=limit)
        if resp.get("retCode") == 0:
            return resp.get("result", {}).get("list", []) or []
    except Exception as e:
        logger.warning(f"⚠️ fetch_kline error: {e}")
    return []

def fetch_open_interest(session, symbol: str, interval_time: str = "5min") -> List[dict]:
    try:
        resp = session.get_open_interest(category="linear", symbol=symbol, intervalTime=interval_time)
        if resp.get("retCode") == 0:
            return resp.get("result", {}).get("list", []) or []
    except Exception as e:
        logger.warning(f"⚠️ fetch_open_interest error: {e}")
    return []

def fetch_funding_rate(session, symbol: str) -> Optional[float]:
    try:
        resp = session.get_funding_rate_history(category="linear", symbol=symbol, limit=1)
        if resp.get("retCode") == 0:
            lst = resp.get("result", {}).get("list", [])
            if lst:
                fr = lst[0].get("fundingRate")
                return float(fr) if fr is not None else None
    except Exception as e:
        logger.warning(f"⚠️ fetch_funding_rate error: {e}")
    return None

def fetch_basis(session, symbol: str, interval: str = "5") -> Optional[float]:
    try:
        resp = session.get_premium_index_price_kline(category="linear", symbol=symbol, interval=interval, limit=1)
        if resp.get("retCode") == 0:
            lst = resp.get("result", {}).get("list", [])
            if lst:
                close_val = lst[0][4]
                return float(close_val)
    except Exception as e:
        logger.warning(f"⚠️ fetch_basis error: {e}")
    return None

def fetch_long_short_ratio(session, symbol: str, period: str = "5min") -> List[Dict[str, Any]]:
    try:
        resp = session.get_long_short_ratio(category="linear", symbol=symbol, period=period)
        if resp.get("retCode") == 0:
            return resp.get("result", {}).get("list", []) or []
    except Exception as e:
        logger.warning(f"⚠️ fetch_long_short_ratio error: {e}")
    return []

def fetch_wallet_equity(session) -> Optional[float]:
    try:
        resp = session.get_wallet_balance(accountType="UNIFIED")
        if resp.get("retCode") == 0:
            lst = resp.get("result", {}).get("list", [])
            if lst:
                eq = lst[0].get("totalEquity")
                return float(eq) if eq is not None else None
    except Exception as e:
        logger.warning(f"⚠️ fetch_wallet_equity error: {e}")
    return None

def fetch_instrument_info(session, symbol: str) -> Dict[str, Any]:
    try:
        resp = session.get_instruments_info(category="linear", symbol=symbol)
        if resp.get("retCode") == 0:
            lst = resp.get("result", {}).get("list", [])
            return lst[0] if lst else {}
    except Exception as e:
        logger.warning(f"⚠️ fetch_instrument_info error: {e}")
    return {}

def fetch_available_balance(session, coin: str = "USDT") -> float:
    """
    Доступные средства (available) по монете.
    V5 wallet-balance: result.list[0].coin[].availableToTrade.walletBalance
    """
    try:
        resp = session.get_wallet_balance(accountType="UNIFIED")
        if resp.get("retCode") == 0:
            lst = (resp.get("result") or {}).get("list", [])
            if lst:
                coins = lst[0].get("coin", []) or []
                for c in coins:
                    if c.get("coin") == coin:
                        at = (c.get("availableToTrade") or {}).get("walletBalance")
                        return float(at or 0.0)
    except Exception as e:
        logger.warning(f"⚠️ fetch_available_balance error: {e}")
    return 0.0
