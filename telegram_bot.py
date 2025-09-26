import os
import asyncio
import yaml
import pandas as pd
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

from pybit.unified_trading import HTTP
from bybit_data import (
    fetch_kline,
    fetch_instrument_info,
    fetch_wallet_equity,
    fetch_available_balance,
)
from indicators import calculate_indicators
from scoring import score_signal
from regime import detect_regime

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN not set in .env")

bot = Bot(token=TELEGRAM_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

with open("config.yaml", "r") as f:
    _cfg = yaml.safe_load(f)

SYMBOL = _cfg.get("symbol", "BTCUSDT")
LOWER_TF = _cfg.get("lower_tf", "1")
TEST_TRADE_USDT = float(_cfg.get("test_trade_size", 10))
# Для статуса — но работаем всегда в LIVE
TESTNET = bool(_cfg.get("testnet", False))

# Бот стартует в «стопе» — включай /on
TRADING_ACTIVE = False


def _make_session_live() -> HTTP:
    # Всегда live
    return HTTP(
        testnet=False,
        api_key=os.getenv("BYBIT_API_KEY"),
        api_secret=os.getenv("BYBIT_API_SECRET"),
    )


async def send_telegram_message(text: str):
    if TELEGRAM_CHAT_ID:
        try:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text)
        except Exception as e:
            print(f"Telegram send error: {e}")


@dp.message(Command("start"))
async def start_cmd(m: types.Message):
    await m.answer(
        "🤖 <b>Bybit V5 Intraday Bot (LIVE)</b>\n\n"
        "Команды:\n"
        "/on – включить торговлю\n"
        "/off – выключить торговлю\n"
        "/status – статус бота\n"
        "/price – текущая цена\n"
        "/why – объяснить текущее решение\n"
        "/balance – баланс и доступные средства\n"
        "/testtrade – ЛАЙВ: купить и сразу продать (быстрый круг) на сумму из config.yaml\n"
    )


@dp.message(Command("on"))
async def on_cmd(m: types.Message):
    global TRADING_ACTIVE
    TRADING_ACTIVE = True
    await m.answer("✅ Торговля включена (LIVE).")


@dp.message(Command("off"))
async def off_cmd(m: types.Message):
    global TRADING_ACTIVE
    TRADING_ACTIVE = False
    await m.answer("⏸️ Торговля выключена.")


@dp.message(Command("status"))
async def status_cmd(m: types.Message):
    status = "🟢 Активна" if TRADING_ACTIVE else "🔴 Выключена"
    mode = "LIVE" if not TESTNET else "TESTNET"
    await m.answer(f"ℹ️ Статус: {status}\nРежим: {mode}\nСимвол: {SYMBOL}\nТФ: {LOWER_TF}m")


@dp.message(Command("price"))
async def price_cmd(m: types.Message):
    session = _make_session_live()
    kl = fetch_kline(session, SYMBOL, LOWER_TF, 2)
    if not kl:
        await m.answer("⚠️ Нет свежих свечей")
        return
    last_close = float(kl[-1][4])
    await m.answer(f"💰 {SYMBOL} = <b>{last_close:.2f}</b>")


@dp.message(Command("balance"))
async def balance_cmd(m: types.Message):
    """
    Показывает:
      • Total Equity (UNIFIED)
      • Available USDT (availableToTrade.walletBalance)
      • Краткую сводку по позиции по SYMBOL (если открыта)
    """
    session = _make_session_live()
    equity = fetch_wallet_equity(session)
    available = fetch_available_balance(session, "USDT")

    # Сводка по позиции
    pos_txt = "позиции нет"
    try:
        resp = session.get_positions(category="linear", symbol=SYMBOL)
        if resp.get("retCode") == 0:
            lst = (resp.get("result") or {}).get("list", []) or []
            for p in lst:
                sz = float(p.get("size") or 0)
                if abs(sz) > 0:
                    side = p.get("side")
                    avg = float(p.get("avgPrice") or 0)
                    upnl = float(p.get("unrealisedPnl") or 0)
                    pos_txt = f"{side} {SYMBOL} | size={sz} | avg={avg:.2f} | uPnL={upnl:.2f} USDT"
                    break
    except Exception as e:
        pos_txt = f"⚠️ не удалось получить позицию: {e}"

    eq_txt = f"{equity:.2f} USDT" if equity is not None else "—"
    av_txt = f"{available:.2f} USDT" if available is not None else "—"

    await m.answer(
        "🧾 <b>Баланс</b>\n"
        f"Total Equity: <b>{eq_txt}</b>\n"
        f"Available (USDT): <b>{av_txt}</b>\n"
        f"Позиция: {pos_txt}"
    )


@dp.message(Command("why"))
async def why_cmd(m: types.Message):
    session = _make_session_live()
    kl = fetch_kline(session, SYMBOL, LOWER_TF, 200)
    if not kl:
        await m.answer("❌ Пока нет данных для анализа — нет свечей.")
        return
    df = pd.DataFrame(kl, columns=["timestamp","open","high","low","close","volume","turnover"])
    df["timestamp"] = pd.to_datetime(pd.to_numeric(df["timestamp"], errors="coerce"), unit="ms")
    for c in ["open","high","low","close","volume","turnover"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df.sort_values("timestamp", inplace=True); df.reset_index(drop=True, inplace=True)
    df = calculate_indicators(df)

    # Упрощённо: счёт по текущим данным
    metrics = {"oi": [], "funding": None, "basis": None, "lsr": []}
    total, breakdown = score_signal(df, metrics)
    regime = detect_regime(df, metrics)

    last = df.iloc[-1]
    txt = (
        f"🤖 <b>Анализ сейчас (LIVE)</b>\n"
        f"EMA9/21/50: {last['ema_9']:.1f} / {last['ema_21']:.1f} / {last['ema_50']:.1f}\n"
        f"RSI: {last['rsi']:.1f} | ADX: {last['adx']:.1f}\n"
        f"VWAP: {last['vwap']:.1f}\n\n"
        f"📊 TA: {breakdown['TA']:+.2f}\n"
        f"📈 BybitData: {breakdown['BybitData']:+.2f}\n"
        f"📊 Volume: {breakdown['Volume']:+.2f}\n"
        f"🌪️ Volatility: {breakdown['Volatility']:+.2f}\n"
        f"➡️ Итоговый скор: <b>{total:+.2f}</b>\n"
        f"⚡ Режим: <b>{regime}</b>\n"
    )
    await m.answer(txt)


@dp.message(Command("testtrade"))
async def testtrade_cmd(m: types.Message):
    """
    LIVE круг: BUY на сумму TEST_TRADE_USDT → сразу SELL reduceOnly тем же qty.
    Важно: это реальная сделка.
    """
    session = _make_session_live()

    # 1) Цена
    kl = fetch_kline(session, SYMBOL, "1", 2)
    if not kl:
        await m.answer("❌ Нет цены для сделки.")
        return
    price = float(kl[-1][4])

    # 2) Фильтры инструмента (лот/минималки)
    info = fetch_instrument_info(session, SYMBOL)
    lot_step = float(info.get("lotSizeFilter", {}).get("qtyStep", 0.001)) if info else 0.001
    min_qty  = float(info.get("lotSizeFilter", {}).get("minOrderQty", 0.001)) if info else 0.001
    min_val  = float(info.get("lotSizeFilter", {}).get("minOrderAmt", 5.0)) if info else 5.0

    def _round_step(v, step):
        import math
        return math.floor(v / step) * step if step > 0 else v

    # 3) Ровно TEST_TRADE_USDT по рынку
    raw_qty = TEST_TRADE_USDT / price if price > 0 else min_qty
    qty = max(_round_step(raw_qty, lot_step), min_qty)
    if qty * price < min_val:
        await m.answer(f"❌ {TEST_TRADE_USDT} USDT меньше минимального ордера ({min_val} USDT).")
        return

    # 4) BUY
    try:
        buy_resp = session.place_order(
            category="linear",
            symbol=SYMBOL,
            side="Buy",
            orderType="Market",
            qty=str(qty),
            timeInForce="GoodTillCancel",
            reduceOnly=False,
        )
    except Exception as e:
        await m.answer(f"❌ Ошибка покупки: {e}")
        return

    if not (isinstance(buy_resp, dict) and buy_resp.get("retCode") == 0):
        await m.answer(f"❌ Ошибка покупки: {buy_resp}")
        return

    # 5) Немедленный SELL reduceOnly на ту же qty (закроет позицию)
    try:
        sell_resp = session.place_order(
            category="linear",
            symbol=SYMBOL,
            side="Sell",
            orderType="Market",
            qty=str(qty),
            reduceOnly=True,
            timeInForce="GoodTillCancel",
        )
    except Exception as e:
        await m.answer(f"⚠️ Купили, но не смогли продать reduceOnly: {e}")
        return

    if isinstance(sell_resp, dict) and sell_resp.get("retCode") == 0:
        await m.answer(
            "💥 LIVE тест круг выполнен:\n"
            f"BUY {SYMBOL} qty={qty} @≈{price:.2f}\n"
            f"SELL reduceOnly qty={qty}"
        )
    else:
        await m.answer(f"⚠️ Купили, но продажа reduceOnly не прошла: {sell_resp}")


async def init_telegram():
    print("🚀 Telegram-бот запущен и ждёт команды (LIVE).")
    await dp.start_polling(bot)
