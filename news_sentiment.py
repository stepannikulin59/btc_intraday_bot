# news_sentiment.py
import os
import requests
import time
from dotenv import load_dotenv

load_dotenv()

CRYPTOPANIC_KEY = os.getenv("CRYPTOPANIC_KEY")
BASE_URL = "https://cryptopanic.com/api/developer/v2/posts/"

# кэш новостей
_last_fetch_time = 0
_cached_news_signal = "neutral"

def get_news_signal(symbol: str = "BTC") -> str:
    """
    Получает новостной сигнал по BTC с CryptoPanic.
    Возвращает: 'bullish', 'bearish', 'neutral'.
    Ограничение: не чаще 1 раза в 5 минут (чтобы не словить 429).
    """
    global _last_fetch_time, _cached_news_signal

    if not CRYPTOPANIC_KEY:
        return "neutral"

    now = time.time()
    if now - _last_fetch_time < 300:  # 5 минут
        return _cached_news_signal

    try:
        params = {
            "auth_token": CRYPTOPANIC_KEY,
            "currencies": symbol,
            "public": "true",
            "kind": "news",
            "limit": 50
        }
        resp = requests.get(BASE_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        posts = data.get("results", [])
        score = 0
        for post in posts:
            title = (post.get("title") or "").lower()
            if any(word in title for word in ["surge", "bull", "positive", "growth", "rally"]):
                score += 1
            if any(word in title for word in ["drop", "bear", "negative", "crash", "fear"]):
                score -= 1

        if score > 2:
            signal = "bullish"
        elif score < -2:
            signal = "bearish"
        else:
            signal = "neutral"

        _cached_news_signal = signal
        _last_fetch_time = now
        return signal

    except Exception as e:
        print(f"⚠️ Ошибка получения новостей: {e}")
        return _cached_news_signal
