import json
import os
import threading
from typing import Any, Dict

STATE_FILE = "runtime_state.json"
_state_lock = threading.Lock()
_state_cache: Dict[str, Dict[str, Any]] = {}


def load_state() -> Dict[str, Dict[str, Any]]:
    """Загрузить состояние из файла в память (в начале работы бота)."""
    global _state_cache
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                _state_cache = json.load(f)
        except Exception:
            _state_cache = {}
    else:
        _state_cache = {}
    return _state_cache


def save_state() -> None:
    """Сохранить текущее состояние из памяти в файл."""
    with _state_lock:
        with open(STATE_FILE, "w") as f:
            json.dump(_state_cache, f, indent=2)


def get_state(symbol: str) -> Dict[str, Any]:
    """Получить state по символу (если нет — вернуть пустой словарь)."""
    return _state_cache.get(symbol, {})


def set_state(symbol: str, key: str, value: Any) -> None:
    """Обновить ключ state по символу и сразу сохранить на диск."""
    if symbol not in _state_cache:
        _state_cache[symbol] = {}
    _state_cache[symbol][key] = value
    save_state()


def set_limit(key: str, value: Any) -> None:
    """Обновить глобальные лимиты (cooldown, дневные лимиты и т.д.)."""
    if "limits" not in _state_cache:
        _state_cache["limits"] = {}
    _state_cache["limits"][key] = value
    save_state()


def get_limit(key: str, default=None):
    """Получить значение глобального лимита."""
    return _state_cache.get("limits", {}).get(key, default)
