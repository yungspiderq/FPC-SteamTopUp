"""
SteamTopUp — автопополнение кошелька Steam через DesslyHub (плагин для FunPay Cardinal v0.1.17+)

💰 Покупатель оплатил лот «Пополнение Steam на N ₽» — плагин сам находит его логин
Steam (в полях заказа или вежливо спрашивает в чате), проверяет возможность
пополнения, покупает топ-ап через API DesslyHub и следит за транзакцией до
статуса success / failed, сообщая покупателю результат.

Возможности:
 • Полный цикл по API DesslyHub: проверка логина (check_login) -> пополнение
   (steam_refill) -> опрос статуса транзакции/заказа.
 • Поддержка двух поколений API: legacy (заголовок apikey) и v2 (HMAC-подпись
   key+secret, единый эндпоинт /orders) - режим определяется автоматически.
 • Привязка лотов через меню: фиксированная сумма в USD («Лот | 5») или в рублях
   («Лот | 500 RUB») - рубли конвертируются по курсу DesslyHub в момент покупки.
 • Логин Steam ищется в полях заказа и в описании; если его нет - плагин просит
   покупателя в чате и ждёт N минут (настраивается).
 • 🧪 DRY-RUN по умолчанию: весь цикл проходит, но покупка НЕ совершается -
   можно спокойно настроить привязки и проверить сценарий без трат.
 • Защита денег: проверка баланса, лимит стоимости одной покупки, минимальная
   маржа (доход FunPay в USD против стоимости топ-апа), ровно одна покупка на
   заказ, состояние переживает рестарт Кардинала.
 • Ошибки DesslyHub переводятся на русский (-100 неверный логин, -2 мало средств
   и т.д.), при failed/cancelled деньги возвращаются на баланс автоматически.
 • Меню в Telegram: /topup - баланс, привязки лотов, история операций, лимиты.

Команды Telegram:
 • /topup - меню плагина (то же, что «Настройки» на странице плагина).

Установка: положить этот файл в папку plugins/ и перезапустить Кардинал.
ВАЖНО: лоты с пополнением НЕ должны быть в auto_delivery.cfg - иначе Кардинал
выдаст "товар" сам. API-ключ (и секрет для v2) выдаёт менеджер DesslyHub
(https://t.me/DesslyHub_Manager), документация: https://desslyhub.readme.io
Настройки: storage/plugins/steam_topup/settings.json (или меню /topup).
Автор: yungspider
"""

from __future__ import annotations

# ══════════════════════════════ МЕТАДАННЫЕ ПЛАГИНА ══════════════════════════════
NAME = "SteamTopUp (DesslyHub Auto Steam Wallet Top-Up)"
VERSION = "1.0.0"
DESCRIPTION = ("Автопополнение кошелька Steam через API DesslyHub: покупатель оплатил лот - плагин "
               "находит его логин Steam, проверяет возможность пополнения, покупает топ-ап и следит "
               "за транзакцией до success/failed. Поддержка legacy и v2 (HMAC) API, DRY-RUN, лимиты "
               "и маржа, суммы в USD или RUB. Настройки: storage/plugins/steam_topup/settings.json. "
               "Команда: /topup")
CREDITS = "yungspider"
UUID = "c4ff7101-a014-4d19-b3fd-102bc8f52676"
SETTINGS_PAGE = True

PLUG = "SteamTopUp"
MODULE = "steam_topup"
CMD = "topup"
CB = "DSTopup"
INPUT_STATE = "DSTopup_input"
MENU_EMOJI = "💰"
MENU_SUBTITLE = "автопополнение кошелька Steam через DesslyHub"
USE_MAPPINGS = True
USE_ORDERS = True

CMD_TEXTS = (("ru", "Автопополнение Steam через DesslyHub"),
             ("en", "Steam wallet auto top-up via DesslyHub"),
             ("uk", "Автопоповнення Steam через DesslyHub"))

MAPPING_HELP = ("Формат привязки:\n"
                "<code>Название лота | 5</code> - пополнение на 5 $\n"
                "<code>Название лота | 500 RUB</code> - на 500 ₽ (конвертация по курсу "
                "DesslyHub в момент покупки)\n\n"
                "Название лота должно совпадать с названием из auto_delivery.cfg "
                "(или содержать его).")

EDITABLE_TEXTS = {
    "ask_data_text": ("✉️ Запрос логина", "текст запроса логина Steam",
                      "Отправляется, если логин не найден в заказе. Доступен $username."),
    "success_text": ("✅ Об успехе", "текст после успешного пополнения",
                     "Переменные: $username, $order, $login, $amount."),
    "failed_text": ("❌ Об ошибке", "текст при ошибке (пусто = не писать)",
                    "Покупателю об ошибке лучше не писать - оставьте пустым."),
}

EXTRA_INPUT_FIELDS = {
    "mapping": ("➕ Привязка лота", "Пополнение Steam 500₽ | 500 RUB", str, MAPPING_HELP.replace("\n", " ")),
}

PLUGIN_DEFAULTS: dict = {
    "check_login": True,
    "login_field_names": ["логин steam", "steam логин", "логин", "login", "username",
                          "ник steam", "steam", "аккаунт"],
    "ask_data_text": ("Здравствуйте, $username! Спасибо за покупку 💰\n"
                      "Чтобы пополнить кошелёк Steam, пришлите, пожалуйста, ваш ЛОГИН Steam "
                      "(тот, под которым вы входите в Steam - не ссылка и не отображаемое имя).\n"
                      "Пример: coolgamer2010"),
    "success_text": ("💰 Ваш аккаунт Steam ($login) пополнен на $amount $!\n"
                     "Средства обычно зачисляются в течение 5-30 минут "
                     "(иногда дольше на стороне Steam).\n"
                     "Спасибо за покупку, $username! Будем рады вашему отзыву ⭐"),
    "failed_text": "",
}

import datetime
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cardinal import Cardinal

import requests
from telebot.types import InlineKeyboardMarkup as K, InlineKeyboardButton as B, CallbackQuery, Message

from FunPayAPI import types
from FunPayAPI.updater.events import NewOrderEvent, NewMessageEvent, LastChatMessageChangedEvent

from tg_bot import utils as tg_utils, CBT
from locales.localizer import Localizer

logger = logging.getLogger(f"FPC.{PLUG}")
localizer = Localizer()
_ = localizer.translate

# ══════════════════════════════ ПУТИ И КОНСТАНТЫ ══════════════════════════════
PLUGIN_DIR = os.path.join("storage", "plugins", MODULE)
SETTINGS_PATH = os.path.join(PLUGIN_DIR, "settings.json")
ORDERS_PATH = os.path.join(PLUGIN_DIR, "orders.json")
CACHE_PATH = os.path.join(PLUGIN_DIR, "catalog.json")

BASE_URL = "https://desslyhub.com/api/v1"
DOC_URL = "https://desslyhub.readme.io/reference/introduction"

CURRENCY_CODES = {"USD": 1, "GBP": 2, "EUR": 3, "RUB": 5, "UAH": 18, "KZT": 37}
CURRENCY_BY_NAME = {str(name).upper(): int(code) for name, code in CURRENCY_CODES.items()}

# Ошибки DesslyHub (старый apikey-контракт + коды нового API v2)
ERROR_CODES = {
    -1: "внутренняя ошибка сервера DesslyHub",
    -2: "недостаточно средств на балансе DesslyHub",
    -3: "некорректная сумма",
    -4: "некорректное тело запроса",
    -5: "доступ запрещён (проверьте ключ/секрет)",
    -51: "некорректная ссылка «добавить в друзья»",
    -52: "некорректный app_id игры",
    -53: "информация об игре не найдена",
    -54: "у получателя нет основной игры (это DLC)",
    -55: "у получателя уже есть эта игра",
    -56: "не удалось добавить получателя в друзья",
    -57: "указан некорректный регион получателя",
    -58: "регион получателя недоступен для подарка",
    -59: "получатель не добавил бота в друзья (или удалил его)",
    -100: "некорректное имя пользователя Steam",
    -120: "некорректное значение валюты",
    -121: "валюта не поддерживается",
    -151: "некорректный ID транзакции",
    -152: "транзакция не найдена",
    -153: "не указан номер страницы",
    -200: "мобильная игра не найдена",
    -201: "позиция (номинал) мобильной игры не найдена",
    -202: "источник (variant) не найден",
    -300: "ваучер не найден",
    -301: "ваучер недоступен (нет в наличии)",
}
BUSINESS_ERRORS = {-2, -3, -4, -5, -51, -52, -53, -54, -55, -56, -57, -58, -59,
                   -100, -120, -121, -151, -152, -153, -200, -201, -202, -300, -301}

FINAL_STATUSES = ("done", "failed", "error", "timeout", "timeout_data", "dry_run", "skipped", "cancelled")

_settings: dict = {}
_orders: dict = {}
_lock = threading.RLock()
_cardinal: Cardinal | None = None
_thread_started = False
_last_low_balance_alert = 0.0

# ══════════════════════════════ НАСТРОЙКИ ПО УМОЛЧАНИЮ ══════════════════════════════
BASE_DEFAULTS: dict[str, Any] = {
    # Безопасность: по умолчанию плагин ВЫКЛЮЧЕН и работает в DRY-RUN (деньги не тратятся).
    "enabled": False,
    "dry_run": True,

    # API DesslyHub: legacy (apikey) и v2 (HMAC key+secret); auto = определить самому
    "apikey": "",
    "secret": "",
    "api_mode": "auto",
    "base_url": BASE_URL,
    "timeout": 20,
    "retries": 2,

    # Опрос транзакций и ожидание данных покупателя
    "poll_interval": 10,
    "poll_max_minutes": 90,
    "wait_data_minutes": 60,

    # Защита денег
    "min_balance_usd": 5.0,
    "max_order_cost_usd": 100.0,
    "min_margin_percent": 0.0,          # 0 = проверка маржи выключена
    "funpay_currency_code": 5,          # код валюты цен FunPay (5 = RUB, 1 = USD, 37 = KZT)
    "notify_low_balance": True,
    "low_balance_repeat_minutes": 60,

    # Данные покупателя и сообщения
    "ask_for_data": True,
    "ask_data_text": "",
    "success_text": "",
    "failed_text": "",                  # пусто = покупателю об ошибке не пишем

    # Каталог и привязки
    "catalog_cache_minutes": 720,
    "mappings": {},
}

DEFAULT_SETTINGS = {**BASE_DEFAULTS, **PLUGIN_DEFAULTS}


class DesslyError(Exception):
    """Ошибка API DesslyHub с кодом из документации."""

    def __init__(self, code: int, message: str | None = None):
        self.code = int(code)
        self.message = message or ERROR_CODES.get(self.code, f"неизвестная ошибка (код {self.code})")
        super().__init__(f"[{self.code}] {self.message}")


class PluginConfigError(Exception):
    """Ошибка настроек плагина (нет привязки, не выбран номинал и т.п.)."""


# ══════════════════════════════ ХРАНИЛИЩЕ ══════════════════════════════
def _atomic_write(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _read_json(path: str) -> Any:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.error(f"[{PLUG}] Не удалось прочитать $YELLOW{path}$RESET - файл поврежден?")
        logger.debug("TRACEBACK", exc_info=True)
        return None


def load_settings(force: bool = False) -> dict:
    """
    Загружает настройки (создает файл с настройками по умолчанию при первом запуске).
    Значения, которых нет в файле, дополняются из DEFAULT_SETTINGS - поэтому добавление
    новых опций в следующих версиях плагина не ломает существующие настройки.
    """
    global _settings
    with _lock:
        if _settings and not force:
            return _settings
        raw = _read_json(SETTINGS_PATH)
        settings = dict(DEFAULT_SETTINGS)
        if isinstance(raw, dict):
            for key, value in raw.items():
                if key in DEFAULT_SETTINGS and isinstance(DEFAULT_SETTINGS[key], type(value)):
                    settings[key] = value
                elif key in DEFAULT_SETTINGS:
                    try:
                        settings[key] = type(DEFAULT_SETTINGS[key])(value)
                    except Exception:
                        logger.warning(f"[{PLUG}] Настройка {key} имеет неверный тип - беру значение "
                                       f"по умолчанию.")
                else:
                    settings[key] = value
            if not isinstance(settings.get("mappings"), dict):
                settings["mappings"] = {}
        else:
            save_settings(settings)
            logger.info(f"[{PLUG}] Создан файл настроек $YELLOW{SETTINGS_PATH}$RESET")
        _settings = settings
        return _settings


def save_settings(settings: dict | None = None) -> None:
    """Сохраняет настройки на диск."""
    global _settings
    with _lock:
        if settings is not None:
            _settings = settings
        if not _settings:
            return
        try:
            _atomic_write(SETTINGS_PATH, _settings)
        except Exception:
            logger.error(f"[{PLUG}] Не удалось сохранить настройки.")
            logger.debug("TRACEBACK", exc_info=True)


def update_setting(key: str, value: Any) -> None:
    """Меняет одну настройку и сохраняет файл."""
    settings = load_settings()
    with _lock:
        settings[key] = value
    save_settings(settings)


def load_orders() -> dict:
    """Загружает журнал заказов/транзакций."""
    global _orders
    with _lock:
        if _orders:
            return _orders
        raw = _read_json(ORDERS_PATH)
        _orders = raw if isinstance(raw, dict) else {}
        return _orders


def save_orders() -> None:
    """Сохраняет журнал заказов/транзакций."""
    with _lock:
        if not _orders:
            return
        try:
            _atomic_write(ORDERS_PATH, _orders)
        except Exception:
            logger.error(f"[{PLUG}] Не удалось сохранить журнал транзакций.")
            logger.debug("TRACEBACK", exc_info=True)


def get_record(order_id: str) -> dict | None:
    return load_orders().get(str(order_id))


def set_record(order_id: str, **fields) -> dict:
    """Создает/обновляет запись заказа в журнале."""
    orders = load_orders()
    with _lock:
        record = orders.setdefault(str(order_id), {})
        record["order_id"] = str(order_id)
        record.update(fields)
        record["updated"] = int(time.time())
        if "created" not in record:
            record["created"] = int(time.time())
        # журнал не должен расти бесконечно: старые завершенные заказы удаляются
        if len(orders) > 500:
            finished = [i for i in orders if orders[i].get("status") in FINAL_STATUSES]
            for key in sorted(finished, key=lambda k: int(orders[k].get("updated", 0)))[:100]:
                orders.pop(key, None)
    save_orders()
    return orders[str(order_id)]


def load_cache(section: str) -> tuple[Any, float]:
    """Возвращает (данные кэша каталога, время сохранения)."""
    raw = _read_json(CACHE_PATH)
    if isinstance(raw, dict):
        entry = raw.get(section)
        if isinstance(entry, dict) and "data" in entry:
            return entry.get("data"), float(entry.get("time", 0))
    return None, 0.0


def save_cache(section: str, data: Any) -> None:
    """Сохраняет секцию кэша каталога."""
    try:
        raw = _read_json(CACHE_PATH)
        raw = raw if isinstance(raw, dict) else {}
        raw[section] = {"time": time.time(), "data": data}
        _atomic_write(CACHE_PATH, raw)
    except Exception:
        logger.debug("TRACEBACK", exc_info=True)


def cache_ttl_seconds() -> int:
    settings = load_settings()
    return max(60, int(settings.get("catalog_cache_minutes", 720))) * 60


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


# ══════════════════════════════ КЛИЕНТ API DESSLYHUB (ДВА ПОКОЛЕНИЯ) ══════════════════════════════
class DesslyClient:
    """
    Клиент API DesslyHub с поддержкой двух поколений API:

    • legacy - заголовок ``apikey`` (официальная документация desslyhub.readme.io, v2.0.3):
      /service/..., /merchants/balance, /merchants/transaction/{id}/status, /exchange_rate/...
    • v2 - HMAC-SHA256 подпись (заголовки X-Api-Key / X-Timestamp / X-Signature,
      подпись = HMAC(secret, api_key + timestamp + body)): единый эндпоинт POST /orders,
      каталоги /catalog/*, баланс /balance, статусы /orders/{id}, курсы /currency/exchange_rates/*.

    Режим ``auto`` определяет поколение зондированием: сначала v2 (если задан секрет),
    затем legacy (если задан apikey). Все ответы нормализуются к единому формату,
    поэтому логика плагинов не зависит от поколения API.
    """

    def __init__(self, apikey: str, secret: str = "", base_url: str = BASE_URL, mode: str = "auto",
                 timeout: int = 20, retries: int = 2, session=None):
        self.apikey = str(apikey or "")
        self.secret = str(secret or "")
        self.base_url = str(base_url or BASE_URL).rstrip("/")
        self.mode_setting = str(mode or "auto").lower()
        if self.mode_setting not in ("auto", "legacy", "v2"):
            self.mode_setting = "auto"
        self.timeout = max(5, int(timeout or 20))
        self.retries = max(0, int(retries or 0))
        self.session = session if session is not None else requests.Session()
        self._resolved: str | None = None

    # ────────────────────────── подписывание и запросы ──────────────────────────
    def sign_headers(self, body_str: str = "") -> dict:
        """Заголовки авторизации API v2: HMAC-SHA256(secret, api_key + timestamp + body)."""
        timestamp = str(int(time.time()))
        message = self.apikey + timestamp + body_str
        signature = hmac.new(self.secret.encode("utf-8"), message.encode("utf-8"),
                             hashlib.sha256).hexdigest()
        return {"X-Api-Key": self.apikey, "X-Timestamp": timestamp, "X-Signature": signature}

    def _request(self, method: str, path: str, payload: dict | None = None, params: dict | None = None,
                 raise_on_error: bool = True, v2: bool = False, retries: int | None = None) -> dict:
        if v2 and not self.secret:
            raise DesslyError(-5, "для API v2 нужен секретный ключ (меню -> 🔐 Секретный ключ)")
        if not v2 and not self.apikey:
            raise DesslyError(-5, f"API-ключ DesslyHub не задан (меню -> 🔑 API-ключ)")
        url = f"{self.base_url}{path}"
        attempts = self.retries if retries is None else max(0, int(retries))
        body_str = ""
        if payload is not None:
            body_str = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        last_error: DesslyError | None = None
        for attempt in range(attempts + 1):
            try:
                headers = {"accept": "application/json"}
                if v2:
                    headers.update(self.sign_headers(body_str))
                    data = None
                    if payload is not None:
                        headers["Content-Type"] = "application/json"
                        data = body_str.encode("utf-8")
                    response = self.session.request(method, url, headers=headers, data=data,
                                                    params=params, timeout=self.timeout)
                else:
                    headers["apikey"] = self.apikey
                    response = self.session.request(method, url, headers=headers, json=payload,
                                                    params=params, timeout=self.timeout)
                try:
                    parsed = response.json()
                except Exception:
                    raise DesslyError(-1, f"ответ не похож на JSON (HTTP {response.status_code})")
                if response.status_code >= 400:
                    code, message = None, None
                    if isinstance(parsed, dict):
                        code, message = parsed.get("error_code"), parsed.get("message")
                    code = _int_or_none(code)
                    raise DesslyError(code if code is not None else -1,
                                      str(message) if message else f"HTTP {response.status_code}")
                code = parsed.get("error_code") if isinstance(parsed, dict) else None
                if code not in (None, 0, "0", ""):
                    code_int = _int_or_none(code)
                    error = DesslyError(code_int if code_int is not None else -1)
                    if raise_on_error:
                        raise error
                    return parsed if isinstance(parsed, dict) else {"error_code": error.code}
                return parsed if isinstance(parsed, dict) else {"result": parsed}
            except DesslyError as e:
                if e.code in BUSINESS_ERRORS:
                    raise
                last_error = e
            except Exception as e:
                last_error = DesslyError(-1, f"{type(e).__name__}: {e}")
            if attempt < attempts:
                time.sleep(1.5 * (attempt + 1))
        raise last_error or DesslyError(-1)

    # ────────────────────────── определение поколения API ──────────────────────────
    def resolve_mode(self, force: bool = False) -> str:
        """Возвращает работающее поколение API: 'v2', 'legacy' или 'none'."""
        if self._resolved and not force:
            return self._resolved
        mode = self.mode_setting
        if mode == "legacy":
            self._resolved = "legacy" if self.apikey else "none"
        elif mode == "v2":
            self._resolved = "v2" if self.secret else "none"
        else:
            resolved = "none"
            if self.secret:
                try:
                    self._request("GET", "/balance", v2=True, retries=0)
                    resolved = "v2"
                except Exception:
                    logger.debug("TRACEBACK", exc_info=True)
            if resolved == "none" and self.apikey:
                try:
                    self._request("GET", "/merchants/balance", retries=0)
                    resolved = "legacy"
                except Exception:
                    logger.debug("TRACEBACK", exc_info=True)
            self._resolved = resolved
        return self._resolved

    def is_v2(self) -> bool:
        return self.resolve_mode() == "v2"

    def require_mode(self) -> str:
        mode = self.resolve_mode()
        if mode == "none":
            raise DesslyError(-5, "ни одно поколение API не отвечает - проверьте 🔑 API-ключ "
                                  "и 🔐 секретный ключ (меню плагина -> 🧪 Тест подключения)")
        return mode

    # ────────────────────────── мерчант: баланс, курсы, история ──────────────────────────
    def balance(self) -> dict:
        """Баланс мерчанта. v2: balance/available_balance/overdraft/reserve; legacy: balance."""
        if self.is_v2():
            data = self._request("GET", "/balance", v2=True)
            return {"balance": _float(data.get("balance")),
                    "available_balance": _float(data.get("available_balance")),
                    "overdraft": _float(data.get("overdraft")),
                    "reserve": _float(data.get("reserve"))}
        data = self._request("GET", "/merchants/balance")
        return {"balance": _float(data.get("balance")), "available_balance": None,
                "overdraft": None, "reserve": None}

    def exchange_rate(self, currency_code: int) -> float:
        """Курс валюты к USD (например 87.87 для RUB)."""
        if self.is_v2():
            data = self._request("GET", f"/currency/exchange_rates/steam/{int(currency_code)}", v2=True)
        else:
            data = self._request("GET", f"/exchange_rate/steam/{int(currency_code)}")
        return float(data.get("exchange_rate", 0) or 0)

    def all_rates(self) -> dict:
        """Курсы всех поддерживаемых валют к USD: {код валюты: курс}."""
        if self.is_v2():
            data = self._request("GET", "/currency/exchange_rates/steam", v2=True)
            rates = data.get("exchange_rates") or data.get("rates") or {}
            if isinstance(rates, dict) and rates:
                return {int(k): float(v) for k, v in rates.items()}
            return {}
        try:
            data = self._request("GET", "/exchange_rate/steam")
            rates = data.get("exchange_rates") or data.get("rates") or {}
            if isinstance(rates, dict) and rates:
                return {int(k): float(v) for k, v in rates.items()}
        except DesslyError:
            pass
        result = {}
        for code in (1, 3, 5, 18, 37):
            try:
                result[code] = self.exchange_rate(code)
            except DesslyError:
                continue
        return result

    def history(self, page: int = 1) -> tuple[list, int | None]:
        """
        История операций: v2 - заказы (/orders), legacy - транзакции (/merchants/transactions).

        :return: (список нормализованных записей, всего записей или None).
        """
        page = max(1, int(page or 1))
        if self.is_v2():
            data = self._request("GET", "/orders", params={"limit": 20, "offset": (page - 1) * 20},
                                 v2=True)
            items = data.get("items") or []
            result = [{"id": str(item.get("order_id", "")),
                       "status": str(item.get("order_status", "")),
                       "type": str(item.get("service_type", "")),
                       "created": str(item.get("created_at", "")),
                       "amount": item.get("final_amount")}
                      for item in items if isinstance(item, dict)]
            return result, _int_or_none(data.get("total"))
        data = self._request("GET", "/merchants/transactions", params={"page": page})
        items = data.get("transactions") or []
        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            result.append({
                "id": str(item.get("transaction_id") or item.get("id") or item.get("uuid") or ""),
                "status": str(item.get("status", "")),
                "type": str(item.get("service_type") or item.get("service") or item.get("type") or ""),
                "created": str(item.get("created_at") or item.get("date") or item.get("created") or ""),
                "amount": item.get("amount") if item.get("amount") is not None else item.get("sum")})
        return result, None

    def txn_status(self, txn: str) -> dict:
        """
        Статус транзакции (legacy) или заказа (v2), нормализованный к единому формату:
        {status, error_code, result, final_amount, commission}. 'processing' -> 'pending'.
        """
        if self.is_v2():
            order_id = _int_or_none(txn)
            if order_id is None:
                raise DesslyError(-151, f"некорректный ID заказа для API v2: {txn}")
            data = self._request("GET", f"/orders/{order_id}", v2=True)
            status = str(data.get("order_status", "") or "")
            return {"status": "pending" if status == "processing" else status,
                    "error_code": _int_or_none(data.get("error_code")),
                    "result": data.get("service_result"),
                    "final_amount": _float(data.get("final_amount")),
                    "commission": _float(data.get("commission"))}
        data = self._request("GET", f"/merchants/transaction/{txn}/status")
        status = str(data.get("status", "") or "")
        code = _int_or_none(data.get("error_code"))
        return {"status": "pending" if status == "processing" else status,
                "error_code": code if code else None, "result": None,
                "final_amount": None, "commission": None}

    # ────────────────────────── единая точка покупок (v2) ──────────────────────────
    def create_order(self, service_type: str, service_params: dict,
                     reference: str | None = None) -> dict:
        """POST /orders (v2). Возвращает {txn, status, error_code}."""
        body: dict[str, Any] = {"payment_method": "balance", "service_type": str(service_type),
                                "service_params": dict(service_params)}
        if reference:
            body["reference"] = str(reference)
        try:
            data = self._request("POST", "/orders", payload=body, v2=True)
        except DesslyError as e:
            return {"txn": None, "status": "failed", "error_code": e.code}
        txn = data.get("order_id") if data.get("order_id") is not None else data.get("transaction_id")
        return {"txn": str(txn) if txn is not None else None,
                "status": str(data.get("status", "") or ""),
                "error_code": _int_or_none(data.get("error_code"))}

    def _legacy_purchase(self, path: str, payload: dict) -> dict:
        """Покупка в legacy-API: нормализованный {txn, status, error_code} + исходный ответ."""
        try:
            data = self._request("POST", path, payload=payload, raise_on_error=False)
        except DesslyError as e:
            return {"txn": None, "status": "failed", "error_code": e.code, "raw": {}}
        txn = data.get("transaction_id") or data.get("transaction_uuid")
        return {"txn": str(txn) if txn else None, "status": str(data.get("status", "") or ""),
                "error_code": _int_or_none(data.get("error_code")), "raw": data}

    # ────────────────────────── Steam: пополнение ──────────────────────────
    def check_steam_login(self, username: str, amount: float) -> bool:
        """Можно ли пополнить аккаунт Steam с указанным логином."""
        if self.is_v2():
            data = self._request("GET", f"/catalog/steam-refill/check_login/{username}", v2=True)
        else:
            data = self._request("POST", "/service/steamtopup/check_login",
                                 payload={"username": str(username), "amount": float(amount)})
        value = data.get("can_refill")
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes")
        return bool(value)

    def steam_topup(self, username: str, amount: float, reference: str | None = None) -> dict:
        """Пополнение кошелька Steam: v2 - заказ steam_refill, legacy - /service/steamtopup/topup."""
        if self.is_v2():
            return self.create_order("steam_refill", {"username": str(username), "amount": float(amount)},
                                     reference)
        payload: dict[str, Any] = {"username": str(username), "amount": float(amount)}
        if reference:
            payload["reference"] = str(reference)
        return self._legacy_purchase("/service/steamtopup/topup", payload)

    # ────────────────────────── Steam: подарки ──────────────────────────
    def steam_games(self, use_cache: bool = True) -> list:
        """Каталог игр для гифтов, нормализованный к [{name, appid}]."""
        if use_cache:
            cached, cached_at = load_cache("steam_games")
            if isinstance(cached, list) and cached and time.time() - cached_at < cache_ttl_seconds():
                return cached
        if self.is_v2():
            games = self._request("GET", "/catalog/steam-gift/games", v2=True).get("games") or []
        else:
            games = self._request("GET", "/service/steamgift/games").get("games") or []
        result = []
        for game in games if isinstance(games, list) else []:
            if not isinstance(game, dict):
                continue
            appid = game.get("appid") if game.get("appid") is not None else game.get("app_id")
            result.append({"name": str(game.get("name", "")), "appid": _int_or_none(appid)})
        if result:
            save_cache("steam_games", result)
        return result

    def steam_game(self, app_id: int | str) -> list:
        """Издания игры с ценами по регионам; package_id нормализован к строке."""
        if self.is_v2():
            editions = self._request("GET", f"/catalog/steam-gift/games/{int(app_id)}",
                                     v2=True).get("game") or []
        else:
            editions = self._request("GET", f"/service/steamgift/games/{int(app_id)}").get("game") or []
        result = []
        for edition in editions if isinstance(editions, list) else []:
            if not isinstance(edition, dict):
                continue
            item = dict(edition)
            item["package_id"] = str(item.get("package_id", ""))
            result.append(item)
        return result

    def send_gift(self, invite_url: str, package_id: int | str, region: str,
                  reference: str | None = None) -> dict:
        """Отправка гифта: v2 - заказ steam_gift, legacy - /service/steamgift/sendgames."""
        if self.is_v2():
            return self.create_order("steam_gift", {"invite_url": str(invite_url),
                                                    "package_id": str(package_id),
                                                    "region": str(region)}, reference)
        payload: dict[str, Any] = {"invite_url": str(invite_url), "package_id": str(package_id),
                                   "region": str(region)}
        if reference:
            payload["reference"] = str(reference)
        return self._legacy_purchase("/service/steamgift/sendgames", payload)

    # ────────────────────────── мобильные игры ──────────────────────────
    def mobile_games(self, variant: int = 1, use_cache: bool = True) -> list:
        """Каталог мобильных игр [{id, name}]. В v2 источник (variant) не используется."""
        variant = int(variant or 1)
        if use_cache:
            cached, cached_at = load_cache(f"mobile_games_{variant}")
            if isinstance(cached, list) and cached and time.time() - cached_at < cache_ttl_seconds():
                return cached
        if self.is_v2():
            games = self._request("GET", "/catalog/mobile-refill/games", v2=True).get("games") or []
        else:
            games = self._request("GET", f"/service/mobile/variant/{variant}/games").get("games") or []
        result = [{"id": _int_or_none(g.get("id")), "name": str(g.get("name", ""))}
                  for g in games if isinstance(g, dict)]
        if result:
            save_cache(f"mobile_games_{variant}", result)
        return result

    def mobile_game(self, game_id: int | str, variant: int = 1, use_cache: bool = True) -> dict:
        """
        Информация о мобильной игре: {id, name, positions: [{id, name, price}], fields, servers}.
        Ответы legacy/v2 нормализуются; fields - обязательные поля покупателя (напр. Character ID).
        """
        variant = int(variant or 1)
        cache_key = f"mobile_game_{variant}_{game_id}"
        if use_cache:
            cached, cached_at = load_cache(cache_key)
            if isinstance(cached, dict) and cached and time.time() - cached_at < cache_ttl_seconds():
                return cached
        if self.is_v2():
            payload = self._request("GET", f"/catalog/mobile-refill/games/{game_id}", v2=True)
        else:
            payload = self._request("GET", f"/service/mobile/variant/{variant}/games/{game_id}")
        details = payload.get("game") if isinstance(payload.get("game"), dict) else None
        if details is None and isinstance(payload.get("data"), dict):
            details = payload["data"]
        if details is None:
            details = payload
        positions = details.get("positions") or details.get("variants") or []
        norm_positions = []
        for position in positions if isinstance(positions, list) else []:
            if isinstance(position, dict):
                norm_positions.append({"id": _int_or_none(position.get("id")),
                                       "name": str(position.get("name", "")),
                                       "price": _float(position.get("price"))})
        result = {"id": _int_or_none(details.get("id")) or _int_or_none(game_id),
                  "name": str(details.get("name", "") or ""),
                  "positions": norm_positions,
                  "fields": details.get("fields") if isinstance(details.get("fields"), dict) else {},
                  "servers": details.get("servers") if isinstance(details.get("servers"), dict) else {}}
        if result["positions"]:
            save_cache(cache_key, result)
        return result

    def mobile_refill(self, position: int, fields: dict, variant: int = 1,
                      reference: str | None = None) -> dict:
        """Пополнение мобильной игры: v2 - заказ mobile_refill, legacy - /service/mobile/.../refill."""
        clean_fields = {str(k): str(v) for k, v in (fields or {}).items()}
        if self.is_v2():
            return self.create_order("mobile_refill", {"position": int(position),
                                                       "fields": clean_fields}, reference)
        payload: dict[str, Any] = {"position": int(position), "fields": clean_fields}
        if reference:
            payload["reference"] = str(reference)
        return self._legacy_purchase(f"/service/mobile/variant/{int(variant or 1)}/games/refill", payload)

    # ────────────────────────── ваучеры ──────────────────────────
    @staticmethod
    def _normalize_voucher_product(product: dict) -> dict:
        variations = []
        for variation in product.get("variations") or product.get("variants") or []:
            if isinstance(variation, dict):
                variations.append({"id": _int_or_none(variation.get("id")),
                                   "name": str(variation.get("name", "")),
                                   "voucher_currency": str(variation.get("voucher_currency", "") or ""),
                                   "price": _float(variation.get("price")),
                                   "stock": _int_or_none(variation.get("stock")) or 0,
                                   "benefits": str(variation.get("benefits", "") or "")})
        return {"id": _int_or_none(product.get("id")), "name": str(product.get("name", "")),
                "country": str(product.get("country", "") or ""), "variations": variations}

    def voucher_products(self, use_cache: bool = True) -> list:
        """Каталог ваучеров: [{id, name, country, variations: [{id, name, price, stock, ...}]}]."""
        if use_cache:
            cached, cached_at = load_cache("voucher_products")
            if isinstance(cached, list) and cached and time.time() - cached_at < cache_ttl_seconds():
                return cached
        if self.is_v2():
            products = self._request("GET", "/catalog/vouchers", v2=True).get("products") or []
        else:
            products = self._request("GET", "/service/voucher/products").get("products") or []
        result = [self._normalize_voucher_product(p) for p in products if isinstance(p, dict)]
        if result:
            save_cache("voucher_products", result)
        return result

    def voucher_product(self, root_id: int) -> dict | None:
        """Один ваучер по root_id (свежие данные, без кэша)."""
        if self.is_v2():
            try:
                data = self._request("GET", f"/catalog/vouchers/{int(root_id)}", v2=True)
                product = data.get("data") if isinstance(data.get("data"), dict) else data
                if product.get("id") is not None or product.get("variations"):
                    return self._normalize_voucher_product(product)
            except DesslyError:
                pass
        else:
            try:
                data = self._request("GET", f"/service/voucher/products/{int(root_id)}")
                product = data.get("data") if isinstance(data.get("data"), dict) else data
                if isinstance(product, dict) and (product.get("id") is not None
                                                  or product.get("variations")):
                    return self._normalize_voucher_product(product)
            except DesslyError:
                pass
        for product in self.voucher_products(use_cache=False):
            if product.get("id") == int(root_id):
                return product
        return None

    def voucher_buy(self, root_id: int, variant_id: int, quantity: int = 1,
                    reference: str | None = None) -> dict:
        """
        Покупка ваучера: v2 - заказ voucher (количество поддерживается), legacy - /service/voucher/buy
        (только по одному; коды приходят сразу в ответе).
        """
        quantity = max(1, int(quantity or 1))
        if self.is_v2():
            return self.create_order("voucher", {"root_id": int(root_id), "variant_id": int(variant_id),
                                                 "quantity": quantity}, reference)
        if quantity > 1:
            raise PluginConfigError("legacy API покупает только 1 ваучер за запрос - "
                                    "разбейте заказ или используйте API v2 (секретный ключ)")
        payload: dict[str, Any] = {"root_id": int(root_id), "variant_id": int(variant_id)}
        if reference:
            payload["reference"] = str(reference)
        result = self._legacy_purchase("/service/voucher/buy", payload)
        vouchers = DesslyClient.normalize_vouchers(result.get("raw", {}).get("vouchers"))
        result["vouchers"] = vouchers or None
        return result

    @staticmethod
    def normalize_vouchers(raw: Any) -> list:
        """Приводит выданные ваучеры к [{serial, pin, expiration}]."""
        result = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            serial = item.get("serialNumber") if item.get("serialNumber") is not None \
                else item.get("serial_number")
            result.append({"serial": str(serial or ""), "pin": str(item.get("pin", "") or ""),
                           "expiration": str(item.get("expiration", "") or "")})
        return result

    # ────────────────────────── eSIM (только v2) ──────────────────────────
    ESIM_LEGACY_ERROR = "eSIM доступен только в новом API v2 - укажите 🔐 секретный ключ в меню плагина"

    def esim_products(self, cursor: str | None = None) -> dict:
        """Страница каталога eSIM: {variants, next_cursor, prev_cursor}."""
        if not self.is_v2():
            raise DesslyError(-5, self.ESIM_LEGACY_ERROR)
        params = {"cursor": str(cursor)} if cursor else None
        data = self._request("GET", "/catalog/esim/products", params=params, v2=True)
        return {"variants": [v for v in (data.get("variants") or []) if isinstance(v, dict)],
                "next_cursor": str(data.get("next_cursor") or ""),
                "prev_cursor": str(data.get("prev_cursor") or "")}

    def esim_product(self, variant_id: str) -> dict:
        """Вариант eSIM с продуктами: {id, name, description, products: [{id, name, price, stock, ...}]}."""
        if not self.is_v2():
            raise DesslyError(-5, self.ESIM_LEGACY_ERROR)
        data = self._request("GET", f"/catalog/esim/products/{variant_id}", v2=True)
        details = data.get("data") if isinstance(data.get("data"), dict) else data
        products = []
        for product in details.get("products") or []:
            if isinstance(product, dict):
                products.append({"id": str(product.get("id", "")), "name": str(product.get("name", "")),
                                 "description": str(product.get("description", "") or ""),
                                 "price": _float(product.get("price")),
                                 "stock": _int_or_none(product.get("stock")) or 0,
                                 "max_per_order": _int_or_none(product.get("max_per_order")) or 0})
        return {"id": str(details.get("id", "") or variant_id), "name": str(details.get("name", "") or ""),
                "description": str(details.get("description", "") or ""), "products": products}

    def esim_buy(self, product_id: str, variant_id: str, reference: str | None = None) -> dict:
        """Покупка eSIM (заказ v2): результат придет в service_result (QR/LPA-данные)."""
        if not self.is_v2():
            raise DesslyError(-5, self.ESIM_LEGACY_ERROR)
        return self.create_order("esim", {"product_id": str(product_id), "variant_id": str(variant_id)},
                                 reference)


def make_client(settings: dict | None = None) -> DesslyClient:
    """Создает клиент DesslyHub по настройкам плагина."""
    settings = settings or load_settings()
    return DesslyClient(settings.get("apikey", ""), settings.get("secret", ""),
                        settings.get("base_url", BASE_URL), settings.get("api_mode", "auto"),
                        int(settings.get("timeout", 20)), int(settings.get("retries", 2)))


# ══════════════════════════════ ХЕЛПЕРЫ ══════════════════════════════
def error_text(code: int | None, extra: str = "") -> str:
    """Понятное описание ошибки DesslyHub."""
    if code is None:
        return extra or "неизвестная ошибка"
    text = ERROR_CODES.get(int(code), f"неизвестная ошибка (код {code})")
    return f"{text} (код {code}){f': {extra}' if extra else ''}"


def format_price(value: Any) -> str:
    try:
        return f"{float(value):,.2f}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


def notify(c: Cardinal, text: str, critical: bool = False) -> None:
    """Отправляет уведомление продавцу в Telegram (в отдельном потоке)."""
    if not c.telegram:
        return
    notification_type = tg_utils.NotificationTypes.critical if critical else tg_utils.NotificationTypes.other
    threading.Thread(target=c.telegram.send_notification, args=(text,),
                     kwargs={"notification_type": notification_type}, daemon=True).start()


def mask_key(apikey: str) -> str:
    """Маскирует ключ/секрет для вывода в меню и логи."""
    apikey = str(apikey or "")
    if not apikey:
        return "не задан"
    if len(apikey) <= 4:
        return "*" * len(apikey)
    return f"{apikey[:2]}{'*' * 6}{apikey[-2:]}"


def order_currency_code(order, settings: dict) -> int:
    """Код валюты заказа в справочнике DesslyHub (по названию валюты или настройке)."""
    currency = getattr(order, "currency", None)
    name = str(getattr(currency, "name", currency) or "").upper().replace("CURRENCY.", "")
    if name in CURRENCY_BY_NAME:
        return CURRENCY_BY_NAME[name]
    return int(settings.get("funpay_currency_code", 5) or 5)


def to_usd(amount: float, currency_code: int, client: DesslyClient) -> float | None:
    """Переводит сумму из валюты заказа в доллары (валюта баланса DesslyHub)."""
    if int(currency_code) == CURRENCY_CODES["USD"]:
        return float(amount)
    try:
        rate = client.exchange_rate(int(currency_code))
    except DesslyError as e:
        logger.warning(f"[{PLUG}] Не удалось получить курс валюты {currency_code}: {e}")
        return None
    if not rate:
        return None
    return float(amount) / float(rate)


def order_field_texts(order) -> list[tuple[str, str]]:
    """Список (название поля, значение) из заполненных полей заказа."""
    result = []
    fields = getattr(order, "fields", None) or {}
    for key, field in fields.items():
        if key in ("payment_msg", "desc", "summary"):
            continue
        try:
            value = order.get_field_value_any(key)
        except Exception:
            value = getattr(field, "value", None)
            if isinstance(value, dict):
                value = " \n".join(str(i) for i in value.values() if i)
        name = getattr(field, "name", None) or str(key)
        if value:
            result.append((str(name), str(value)))
    return result


def extract_by_aliases(order, aliases) -> str | None:
    """Ищет значение в полях заказа по списку псевдонимов названия поля."""
    markers = [str(a).strip().lower() for a in (aliases or []) if str(a).strip()]
    if not markers:
        return None
    for name, value in order_field_texts(order):
        if any(marker in name.lower() for marker in markers):
            value = value.strip()
            if value:
                return value
    return None


def get_order_safe(c: Cardinal, order_id: str):
    """Возвращает объект заказа или None (не роняет поток)."""
    try:
        return c.account.get_order(str(order_id))
    except Exception:
        logger.debug("TRACEBACK", exc_info=True)
        return None


def resolve_chat_id(c: Cardinal, order) -> int | str | None:
    """Определяет ID чата покупателя (как это делает ядро Кардинала)."""
    try:
        chat = c.account.get_chat_by_name(getattr(order, "buyer_username", None))
        if chat:
            return chat.id
    except Exception:
        logger.debug("TRACEBACK", exc_info=True)
    return getattr(order, "chat_id", None)


def cardinal_tools_format(template: str, order) -> str:
    """Форматирует текст штатной функцией Кардинала (с запасным вариантом)."""
    try:
        from Utils import cardinal_tools
        return cardinal_tools.format_order_text(template, order)
    except Exception:
        logger.debug("TRACEBACK", exc_info=True)
        return template.replace("$username", str(getattr(order, "buyer_username", "") or ""))


def send_buyer_message(c: Cardinal, record: dict, template: str) -> bool:
    """
    Отправляет сообщение покупателю по шаблону: переменные Кардинала ($username и т.д.),
    $order и переменные плагина из message_vars(record).

    Переменные плагина подставляются первыми и одним проходом (regex), чтобы их значения
    не портились последующими заменами (например, $smdp внутри значения $qr).
    """
    if not template or not template.strip():
        return False
    chat_id = record.get("chat_id")
    if not chat_id:
        return False
    mapping = dict(message_vars(record) or {})
    mapping["order"] = str(record.get("order_id", ""))

    def _replace(match) -> str:
        return str(mapping.get(match.group(1), match.group(0)))

    text = re.sub(r"\$([A-Za-z_][A-Za-z0-9_]*)", _replace, template)
    try:
        order = get_order_safe(c, record.get("order_id"))
        text = cardinal_tools_format(text, order) if order is not None else text
    except Exception:
        logger.debug("TRACEBACK", exc_info=True)
    try:
        result = c.send_message(chat_id, text, record.get("buyer"))
        return bool(result)
    except Exception:
        logger.error(f"[{PLUG}] Не удалось отправить сообщение покупателю заказа "
                     f"#{record.get('order_id')}.")
        logger.debug("TRACEBACK", exc_info=True)
        return False


def is_authorized(c: Cardinal, user_id: int | None) -> bool:
    """Проверяет, авторизован ли пользователь в ПУ Telegram."""
    if not c.telegram or user_id is None:
        return False
    return user_id in c.telegram.authorized_users


# ══════════════════════════════ ДЕНЕЖНЫЕ ЗАЩИТЫ ══════════════════════════════
def money_guards(c: Cardinal, order_id: str, client: DesslyClient, settings: dict,
                 cost_usd: float) -> tuple[bool, float | None]:
    """
    Проверяет баланс, лимит стоимости и минимальную маржу до покупки.

    :return: (можно покупать, доход в USD или None). При отказе заказ помечается failed.
    """
    record = get_record(order_id) or {}
    try:
        bal = client.balance()
    except DesslyError as e:
        fail_order(c, order_id, e.code, "не удалось проверить баланс DesslyHub")
        return False, None
    balance = bal.get("balance")
    if balance is not None:
        last = settings.get("_last_balance")
        if last is None or abs(float(last) - float(balance)) > 0.0001:
            update_setting("_last_balance", balance)
    effective = bal.get("available_balance") if bal.get("available_balance") is not None else balance
    min_balance = _float(settings.get("min_balance_usd")) or 0.0
    if effective is not None and effective < min_balance:
        fail_order(c, order_id, -2, f"баланс DesslyHub {format_price(effective)} $ ниже порога "
                                    f"{format_price(min_balance)} $")
        return False, None
    if effective is not None and effective < cost_usd:
        fail_order(c, order_id, -2, f"баланс DesslyHub {format_price(effective)} $ меньше стоимости "
                                    f"покупки {format_price(cost_usd)} $")
        return False, None
    max_cost = _float(settings.get("max_order_cost_usd")) or 0.0
    if max_cost and cost_usd > max_cost:
        fail_order(c, order_id, -3, f"стоимость покупки {format_price(cost_usd)} $ больше лимита "
                                    f"{format_price(max_cost)} $")
        return False, None
    income_usd = None
    price = _float(record.get("funpay_price"))
    if price:
        code = _int_or_none(record.get("currency_code")) or int(settings.get("funpay_currency_code", 5))
        income_usd = to_usd(price, code, client)
    min_margin = _float(settings.get("min_margin_percent")) or 0.0
    if min_margin > 0 and income_usd:
        margin = (income_usd - cost_usd) / income_usd * 100.0
        if margin < min_margin:
            fail_order(c, order_id, -3, f"маржа {margin:.1f}% ниже минимальной {min_margin:.1f}% "
                                        f"(доход {format_price(income_usd)} $, расход "
                                        f"{format_price(cost_usd)} $)")
            return False, income_usd
    set_record(order_id, income_usd=income_usd, cost_usd=cost_usd)
    return True, income_usd


def fail_order(c: Cardinal, order_id: str, code: int | None = None, message: str = "") -> None:
    """Помечает заказ невыполненным, уведомляет продавца и (опционально) покупателя."""
    record = set_record(order_id, status="failed", error_code=code,
                        error_text=error_text(code, message))
    logger.error(f"[{PLUG}] Заказ $YELLOW#{order_id}$RESET не выполнен: {record.get('error_text')}")
    lot_text = ""
    if record.get("lot"):
        lot_text = f" (лот «{tg_utils.escape(str(record.get('lot')))}»)"
    notify(c, f"❌ <b>{PLUG}</b>: заказ <code>{tg_utils.escape(str(order_id))}</code>{lot_text} "
              f"не выполнен - {tg_utils.escape(record.get('error_text') or '')}.\n"
              f"ℹ️ При статусах failed/cancelled DesslyHub возвращает деньги на баланс. "
              f"Если товар не выдан - оформите возврат на FunPay.", True)
    settings = load_settings()
    if settings.get("failed_text"):
        send_buyer_message(c, record, str(settings.get("failed_text")))


def finish_success(c: Cardinal, order_id: str, note: str = "") -> None:
    """Помечает заказ выполненным, считает прибыль и уведомляет продавца."""
    record = get_record(order_id) or {}
    income = _float(record.get("income_usd"))
    spent = _float(record.get("spent_usd"))
    if spent is None:
        spent = _float(record.get("cost_usd"))
    profit = round(income - spent, 4) if income is not None and spent is not None else None
    set_record(order_id, status="done", spent_usd=spent, profit_usd=profit,
               finished=int(time.time()))
    money = ""
    if spent is not None:
        money = f", потрачено <b>{format_price(spent)} $</b>"
        if profit is not None:
            money += f", маржа <b>{format_price(profit)} $</b>"
    lot_text = ""
    if record.get("lot"):
        lot_text = f" (лот «{tg_utils.escape(str(record.get('lot')))}»)"
    notify(c, f"✅ <b>{PLUG}</b>: заказ <code>{tg_utils.escape(str(order_id))}</code>{lot_text} "
              f"выполнен{money}.{f' {note}' if note else ''}")


def advance_txn(c: Cardinal, order_id: str) -> dict | None:
    """
    Опрашивает статус транзакции/заказа. Возвращает инфо при терминальном статусе
    (success/failed/cancelled) или None, если ещё в обработке. Зависшие заказы
    помечает timeout сам poller_loop.
    """
    record = get_record(order_id)
    if not record:
        return None
    txn = record.get("txn")
    if not txn:
        set_record(order_id, status="error",
                   error_text="нет ID транзакции - проверьте операции DesslyHub вручную")
        notify(c, f"⚠️ <b>{PLUG}</b>: заказ <code>{tg_utils.escape(str(order_id))}</code> "
                  f"остался без ID транзакции. Проверьте историю операций DesslyHub вручную.", True)
        return None
    try:
        info = make_client().txn_status(str(txn))
    except DesslyError as e:
        logger.warning(f"[{PLUG}] Заказ #{order_id}: не удалось проверить статус {txn}: {e}")
        return None
    status = str(info.get("status") or "")
    if status == "success":
        set_record(order_id, spent_usd=info.get("final_amount"), commission_usd=info.get("commission"))
        return info
    if status in ("failed", "cancelled"):
        fail_order(c, order_id, info.get("error_code"), f"транзакция {txn} -> {status}")
        return info
    return None


def check_low_balance(c: Cardinal) -> None:
    """Периодическая проверка баланса DesslyHub (уведомление при низком балансе)."""
    global _last_low_balance_alert
    settings = load_settings()
    if not settings.get("notify_low_balance") or not (settings.get("apikey") or settings.get("secret")):
        return
    repeat = max(5, int(settings.get("low_balance_repeat_minutes", 60) or 60)) * 60
    if time.time() - _last_low_balance_alert < repeat:
        return
    try:
        bal = make_client(settings).balance()
    except DesslyError:
        return
    balance = bal.get("balance")
    if balance is not None and balance != settings.get("_last_balance"):
        update_setting("_last_balance", balance)
    effective = bal.get("available_balance") if bal.get("available_balance") is not None else balance
    threshold = _float(settings.get("min_balance_usd")) or 0.0
    if effective is not None and effective < threshold:
        _last_low_balance_alert = time.time()
        notify(c, f"💸 <b>{PLUG}</b>: баланс DesslyHub опустился до <b>{format_price(effective)} $</b> "
                  f"(порог {format_price(threshold)} $). Пополните баланс, иначе выдачи "
                  f"остановятся.", True)


def poller_loop(c: Cardinal, run_id: int) -> None:
    """Фоновый поток: опрос транзакций, контроль ожидания данных и баланса."""
    while True:
        if c.run_id != run_id:
            break
        settings = load_settings()
        interval = max(3, int(settings.get("poll_interval", 10) or 10))
        time.sleep(interval)
        if c.run_id != run_id:
            break
        try:
            now = time.time()
            poll_max_value = settings.get("poll_max_minutes")
            poll_max = max(1, int(poll_max_value if poll_max_value is not None else 90)) * 60
            wait_value = settings.get("wait_data_minutes")
            wait_max = max(1, int(wait_value if wait_value is not None else 60)) * 60
            for order_id, record in list(load_orders().items()):
                status = record.get("status")
                if status == "waiting_result":
                    try:
                        poll_record(c, order_id)
                    except Exception:
                        logger.error(f"[{PLUG}] Ошибка опроса заказа #{order_id}.")
                        logger.debug("TRACEBACK", exc_info=True)
                    record = get_record(order_id) or {}
                    if record.get("status") == "waiting_result":
                        elapsed = now - float(record.get("sent_at") or record.get("created") or now)
                        if elapsed > poll_max:
                            set_record(order_id, status="timeout")
                            notify(c, f"⏰ <b>{PLUG}</b>: транзакция заказа "
                                      f"<code>{tg_utils.escape(str(order_id))}</code> не завершилась за "
                                      f"{int(poll_max // 60)} мин. Проверьте историю операций "
                                      f"DesslyHub вручную.", True)
                elif status == "waiting_data":
                    elapsed = now - float(record.get("asked_at") or record.get("created") or now)
                    if elapsed > wait_max:
                        timeout_waiting_data(c, order_id)
            check_low_balance(c)
        except Exception:
            logger.error(f"[{PLUG}] Сбой в потоке опроса.")
            logger.debug("TRACEBACK", exc_info=True)


def recover_interrupted(c: Cardinal) -> None:
    """
    Помечает заказы, прерванные перезапуском Кардинала в момент покупки.
    Повторять покупку НЕЛЬЗЯ (возможен двойной расход) - заказ передается продавцу.
    """
    orders = load_orders()
    for order_id, record in list(orders.items()):
        if record.get("status") == "sending" and not record.get("txn"):
            set_record(order_id, status="error",
                       error_text="покупка прервана перезапуском бота - проверьте операции DesslyHub")
            notify(c, f"⚠️ <b>{PLUG}</b>: заказ <code>{tg_utils.escape(order_id)}</code> оборвался "
                      f"во время покупки (перезапуск бота). Повторно покупать не стал - проверьте "
                      f"историю операций DesslyHub и дообработайте заказ вручную.", True)


# ══════════════════════════════ ПОТОК НОВОГО ЗАКАЗА ══════════════════════════════
def normalize_lot_name(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def find_mapping(settings: dict, order, event=None) -> tuple[str, dict] | None:
    """
    Ищет привязку лота: точное совпадение с названием лота из auto_delivery.cfg,
    затем ID лота, затем самое длинное вхождение ключа привязки в описание заказа.
    """
    mappings = settings.get("mappings") or {}
    if not mappings:
        return None
    lot_name = normalize_lot_name(getattr(event, "config_section_name", ""))
    if lot_name and lot_name in mappings:
        return lot_name, mappings[lot_name]
    lot_id = getattr(event, "lot_id", None)
    if lot_id is not None and str(lot_id) in mappings:
        return str(lot_id), mappings[str(lot_id)]
    description = str(getattr(order, "description", "") or "")
    candidates = [key for key in mappings if key and key in description]
    if candidates:
        key = max(candidates, key=len)
        return key, mappings[key]
    if lot_name:
        candidates = [key for key in mappings if key and key in lot_name]
        if candidates:
            key = max(candidates, key=len)
            return key, mappings[key]
    return None


def clean_username(name: str) -> str:
    """Удаляет невидимые разделители safe_text из никнейма."""
    return str(name or "").replace("\u2063", "")


def handle_new_order(c: Cardinal, e: NewOrderEvent) -> None:
    """Хэндлер нового заказа: если лот привязан - запускает сценарий выдачи."""
    settings = load_settings()
    if not settings.get("enabled"):
        return
    order = getattr(e, "order", None)
    if order is None or order.id == "ADTEST":
        return
    if get_record(order.id):
        return  # заказ уже обработан (или обрабатывается)
    buyer = clean_username(getattr(order, "buyer_username", ""))
    blacklist = [clean_username(str(b)) for b in (getattr(c, "blacklist", None) or [])]
    if buyer and buyer.lower() in [b.lower() for b in blacklist]:
        set_record(order.id, status="skipped", buyer=buyer, error_text="покупатель в черном списке")
        logger.info(f"[{PLUG}] Заказ #{order.id}: покупатель {buyer} в черном списке - пропуск.")
        return
    found = find_mapping(settings, order, e)
    if not found:
        return
    lot_key, mapping = found
    set_record(order.id, status="new", buyer=buyer,
               buyer_id=getattr(order, "buyer_id", None), lot=lot_key, mapping=dict(mapping),
               funpay_price=float(getattr(order, "price", 0) or 0),
               funpay_currency=str(getattr(order, "currency", "") or ""),
               currency_code=order_currency_code(order, settings),
               amount=int(getattr(order, "amount", 1) or 1),
               dry_run=bool(settings.get("dry_run")),
               chat_id=getattr(order, "chat_id", None) or resolve_chat_id(c, order))
    logger.info(f"[{PLUG}] Заказ $YELLOW#{order.id}$RESET подходит под привязку «{lot_key}» - "
                f"запускаю выдачу.")
    threading.Thread(target=start_order, args=(c, str(order.id)), daemon=True,
                     name=f"{PLUG}-{order.id}").start()


def on_new_order(c: Cardinal, e: NewOrderEvent) -> None:
    """Обертка над handle_new_order (чтобы исключения не ломали обработку событий)."""
    try:
        handle_new_order(c, e)
    except Exception:
        logger.error(f"[{PLUG}] Ошибка при обработке нового заказа.")
        logger.debug("TRACEBACK", exc_info=True)


def on_new_message(c: Cardinal, e: NewMessageEvent | LastChatMessageChangedEvent) -> None:
    """Следит за чатами, где ждём данные покупателя: продолжает выдачу, как только их прислали."""
    try:
        settings = load_settings()
        if not settings.get("enabled"):
            return
        if isinstance(e, NewMessageEvent):
            message = e.message
            chat_id, text = message.chat_id, str(message)
            author_id = getattr(message, "author_id", None)
            by_me = bool(getattr(message, "by_bot", False)) or author_id == getattr(c.account, "id", None)
        else:
            if not c.old_mode_enabled:
                return
            chat = e.chat
            chat_id, text, by_me = chat.id, str(chat), bool(getattr(chat, "last_by_bot", False))
        if by_me:
            return
        waiting = [oid for oid, rec in load_orders().items()
                   if rec.get("status") == "waiting_data" and str(rec.get("chat_id")) == str(chat_id)]
        for order_id in waiting:
            record = get_record(order_id) or {}
            if extract_waiting_data(c, order_id, text, record):
                return
    except Exception:
        logger.error(f"[{PLUG}] Ошибка при обработке сообщения покупателя.")
        logger.debug("TRACEBACK", exc_info=True)


def ask_buyer_data(c: Cardinal, order_id: str, text: str | None = None) -> None:
    """Переводит заказ в ожидание данных и просит покупателя прислать их в чат."""
    settings = load_settings()
    record = get_record(order_id) or {}
    template = text if text is not None else str(settings.get("ask_data_text", ""))
    set_record(order_id, status="waiting_data", asked_at=int(time.time()))
    if template and record.get("chat_id"):
        send_buyer_message(c, get_record(order_id) or {}, template)


def timeout_waiting_data(c: Cardinal, order_id: str) -> None:
    """Покупатель так и не прислал данные - заказ закрывается ошибкой."""
    settings = load_settings()
    minutes = int(settings.get("wait_data_minutes", 60) or 60)
    record = get_record(order_id) or {}
    if record.get("status") != "waiting_data":
        return
    set_record(order_id, status="timeout_data")
    notify(c, f"⏰ <b>{PLUG}</b>: покупатель заказа <code>{tg_utils.escape(str(order_id))}</code> "
              f"не прислал данные за {minutes} мин. Закрыл заказ как невыполненный - "
              f"напишите ему вручную или оформите возврат.", True)
    if settings.get("failed_text"):
        send_buyer_message(c, record, str(settings.get("failed_text")))


# ══════════════════════════════ TELEGRAM-МЕНЮ ══════════════════════════════
def stats_line() -> str:
    """Короткая статистика по журналу заказов."""
    orders = load_orders()
    counts: dict[str, int] = {}
    for record in orders.values():
        key = str(record.get("status", "?"))
        counts[key] = counts.get(key, 0) + 1
    return (f"🧾 В обработке: <b>{counts.get('sending', 0) + counts.get('waiting_result', 0)}</b>  |  "
            f"ждём данные: <b>{counts.get('waiting_data', 0)}</b>\n"
            f"✅ Успешно: <b>{counts.get('done', 0)}</b>  |  "
            f"🚨 Ошибок: <b>{counts.get('failed', 0) + counts.get('error', 0) + counts.get('timeout', 0) + counts.get('timeout_data', 0)}</b>  |  "
            f"🧪 DRY-RUN: <b>{counts.get('dry_run', 0)}</b>")


def menu_text(c: Cardinal) -> str:
    settings = load_settings()
    balance = settings.get("_last_balance")
    balance_text = f"{format_price(balance)} $" if balance is not None else "не проверялся"
    state = "🟢 включен" if settings.get("enabled") else "🔴 ВЫКЛЮЧЕН"
    mode = "🧪 DRY-RUN (деньги не тратятся)" if settings.get("dry_run") else "💸 БОЕВОЙ РЕЖИМ"
    lines = [f"{MENU_EMOJI} <b>{PLUG}</b> · {MENU_SUBTITLE} <i>v{VERSION}</i>", ""]
    if USE_ORDERS:
        lines.append(f"Состояние: {state}")
        lines.append(f"Режим: {mode}")
    lines.append(f"🔑 API-ключ: <code>{tg_utils.escape(mask_key(settings.get('apikey', '')))}</code>  |  "
                 f"🔐 Секрет: <code>{tg_utils.escape(mask_key(settings.get('secret', '')))}</code>")
    lines.append(f"🌐 Поколение API: <code>{tg_utils.escape(str(settings.get('api_mode', 'auto')))}"
                 f"{'' if settings.get('_detected_mode') is None else ' (определено: ' + tg_utils.escape(str(settings.get('_detected_mode'))) + ')'}</code>")
    lines.append(f"💳 Баланс DesslyHub: <b>{balance_text}</b>")
    if USE_MAPPINGS:
        lines.append(f"📦 Привязок лотов: <b>{len(settings.get('mappings') or {})}</b>")
    if USE_ORDERS:
        lines.append(f"🛡 Лимит покупки: <b>{format_price(settings.get('max_order_cost_usd', 0))} $</b>  |  "
                     f"порог баланса: <b>{format_price(settings.get('min_balance_usd', 0))} $</b>  |  "
                     f"мин. маржа: <b>{settings.get('min_margin_percent', 0)}%</b>")
        lines.append("")
        lines.append(stats_line())
    extra = menu_text_extra(c)
    if extra:
        lines.append("")
        lines.append(extra)
    lines.append("")
    lines.append(f"<i>API-ключ и секрет выдаёт менеджер DesslyHub (@DesslyHub_Manager). "
                 f"Документация: desslyhub.readme.io</i>")
    return "\n".join(lines)


def menu_keyboard(c: Cardinal) -> K:
    settings = load_settings()
    kb = K()
    if USE_ORDERS:
        kb.row(B("🟢 Выключить" if settings.get("enabled") else "🔴 Включить", None,
                 f"{CB}:toggle_enabled"),
               B("🧪 DRY-RUN: ВКЛ" if settings.get("dry_run") else "💸 DRY-RUN: ВЫКЛ", None,
                 f"{CB}:toggle_dry"))
    kb.row(B("🔑 API-ключ", None, f"{CB}:input:apikey"),
           B("🔐 Секрет (v2)", None, f"{CB}:input:secret"))
    kb.row(B("💳 Баланс", None, f"{CB}:balance"),
           B("🧪 Тест подключения", None, f"{CB}:test"))
    for text, data in extra_menu(c):
        kb.add(B(text, None, data))
    if USE_MAPPINGS:
        kb.add(B(f"📦 Привязки лотов ({len(settings.get('mappings') or {})})", None, f"{CB}:mappings"))
    kb.add(B("🧾 История операций", None, f"{CB}:history"))
    if USE_ORDERS:
        kb.add(B("🔄 Проверить транзакции", None, f"{CB}:check_now"))
        kb.add(B("🛡 Лимиты и защита", None, f"{CB}:limits"))
        if EDITABLE_TEXTS:
            kb.add(B("💬 Тексты сообщений", None, f"{CB}:texts"))
    kb.add(B(f"🌐 API: {settings.get('api_mode', 'auto')}", None, f"{CB}:apimode"))
    kb.add(B("🔙 Назад", None, f"{CBT.PLUGINS_LIST}:0"))
    return kb


def answer(c: Cardinal, obj: Message | CallbackQuery, text: str, kb: K | None = None) -> None:
    """Отправляет или обновляет сообщение меню."""
    tg = c.telegram
    if isinstance(obj, CallbackQuery):
        try:
            tg.bot.edit_message_text(text, obj.message.chat.id, obj.message.id, reply_markup=kb,
                                     parse_mode="HTML")
        except Exception:
            logger.debug("TRACEBACK", exc_info=True)
        try:
            tg.bot.answer_callback_query(obj.id)
        except Exception:
            logger.debug("TRACEBACK", exc_info=True)
    else:
        tg.bot.send_message(obj.chat.id, text, reply_markup=kb, parse_mode="HTML")


def open_menu(c: Cardinal, obj: Message | CallbackQuery) -> None:
    if not c.telegram or not is_authorized(c, getattr(obj.from_user, "id", None)):
        return
    answer(c, obj, menu_text(c), menu_keyboard(c))


def toggle_setting(c: Cardinal, obj: CallbackQuery, key: str) -> None:
    if not is_authorized(c, obj.from_user.id):
        return
    settings = load_settings()
    update_setting(key, not bool(settings.get(key)))
    logger.info(f"[{PLUG}] Настройка $YELLOW{key}$RESET -> $MAGENTA{not bool(settings.get(key))}$RESET")
    open_menu(c, obj)


def cycle_api_mode(c: Cardinal, obj: CallbackQuery) -> None:
    if not is_authorized(c, obj.from_user.id):
        return
    settings = load_settings()
    order = ["auto", "legacy", "v2"]
    current = str(settings.get("api_mode", "auto"))
    next_mode = order[(order.index(current) + 1) % len(order)] if current in order else "auto"
    update_setting("api_mode", next_mode)
    update_setting("_detected_mode", None)
    open_menu(c, obj)


def do_balance(c: Cardinal, obj: CallbackQuery) -> None:
    if not is_authorized(c, obj.from_user.id):
        return

    def worker():
        try:
            client = make_client()
            mode = client.resolve_mode(force=True)
            update_setting("_detected_mode", mode)
            bal = client.balance()
            update_setting("_last_balance", bal.get("balance"))
            lines = [f"💳 <b>Баланс DesslyHub</b> (API: {mode})",
                     f"Всего: <b>{format_price(bal.get('balance'))} $</b>"]
            if bal.get("available_balance") is not None:
                lines.append(f"Доступно: <b>{format_price(bal.get('available_balance'))} $</b>")
            if bal.get("reserve") is not None:
                lines.append(f"В резерве: {format_price(bal.get('reserve'))} $")
            if bal.get("overdraft") is not None:
                lines.append(f"Овердрафт: {format_price(bal.get('overdraft'))} $")
            answer(c, obj, "\n".join(lines), menu_keyboard(c))
        except DesslyError as e:
            answer(c, obj, f"❌ Не удалось получить баланс: {tg_utils.escape(str(e))}",
                   menu_keyboard(c))
        except Exception:
            logger.debug("TRACEBACK", exc_info=True)
            answer(c, obj, "❌ Не удалось получить баланс (см. логи).", menu_keyboard(c))

    threading.Thread(target=worker, daemon=True, name=f"{PLUG}-Balance").start()


def do_test(c: Cardinal, obj: CallbackQuery) -> None:
    """Тест подключения: определяет поколение API, баланс и курс RUB."""
    if not is_authorized(c, obj.from_user.id):
        return

    def worker():
        lines = ["🧪 <b>Тест подключения к DesslyHub</b>"]
        try:
            client = make_client()
            mode = client.resolve_mode(force=True)
            update_setting("_detected_mode", mode)
            if mode == "none":
                lines.append("❌ Ни legacy (apikey), ни v2 (HMAC) не ответили. Проверьте ключ/секрет.")
                answer(c, obj, "\n".join(lines), menu_keyboard(c))
                return
            lines.append(f"Поколение API: <b>{mode}</b> "
                         f"({'HMAC key+secret' if mode == 'v2' else 'заголовок apikey'})")
            bal = client.balance()
            update_setting("_last_balance", bal.get("balance"))
            lines.append(f"Баланс: <b>{format_price(bal.get('balance'))} $</b>")
            try:
                rate = client.exchange_rate(CURRENCY_CODES["RUB"])
                lines.append(f"Курс RUB: <b>{format_price(rate)}</b> ₽ за 1 $")
            except DesslyError:
                lines.append("Курс RUB: получить не удалось")
            lines.append("✅ Подключение работает.")
        except DesslyError as e:
            lines.append(f"❌ {tg_utils.escape(str(e))}")
        except Exception as e:
            logger.debug("TRACEBACK", exc_info=True)
            lines.append(f"❌ {tg_utils.escape(f'{type(e).__name__}: {e}')}")
        answer(c, obj, "\n".join(lines), menu_keyboard(c))

    threading.Thread(target=worker, daemon=True, name=f"{PLUG}-Test").start()


def open_mappings(c: Cardinal, obj: Message | CallbackQuery) -> None:
    if not c.telegram or not is_authorized(c, getattr(obj.from_user, "id", None)):
        return
    settings = load_settings()
    mappings = settings.get("mappings") or {}
    kb = K()
    if not mappings:
        text = ("📦 <b>Привязки лотов</b>\n\nПока пусто. Добавьте привязку кнопкой ниже.\n\n"
                + MAPPING_HELP)
    else:
        items = list(mappings.items())
        text = f"📦 <b>Привязки лотов ({len(items)})</b>\n\n"
        for index, (key, mapping) in enumerate(items[:30]):
            text += f"{index + 1}. {render_mapping(key, mapping)}\n"
        if len(items) > 30:
            text += f"... и ещё {len(items) - 30}\n"
        text += "\n" + MAPPING_HELP
        for index, (key, _mapping) in enumerate(items[:30]):
            kb.add(B(f"🗑 {key[:30]}", None, f"{CB}:delmap:{index}"))
    kb.add(B("➕ Добавить привязку", None, f"{CB}:input:mapping"))
    kb.add(B("🔙 Назад", None, f"{CB}:menu"))
    answer(c, obj, text, kb)


def delete_mapping(c: Cardinal, obj: CallbackQuery) -> None:
    if not is_authorized(c, obj.from_user.id):
        return
    try:
        index = int(obj.data.split(":")[-1])
    except ValueError:
        return
    settings = load_settings()
    mappings = settings.get("mappings") or {}
    keys = list(mappings.keys())
    if 0 <= index < len(keys):
        removed = keys[index]
        mappings.pop(removed, None)
        update_setting("mappings", mappings)
        logger.info(f"[{PLUG}] Привязка «{removed}» удалена.")
    open_mappings(c, obj)


def open_history(c: Cardinal, obj: Message | CallbackQuery, page: int = 1) -> None:
    if not c.telegram or not is_authorized(c, getattr(obj.from_user, "id", None)):
        return

    def worker():
        try:
            items, total = make_client().history(page)
            text = f"🧾 <b>История операций DesslyHub</b> (стр. {page}" + \
                   (f" из {max(1, (total + 19) // 20)}" if total is not None else "") + ")\n\n"
            if not items:
                text += "Пусто."
            for item in items[:20]:
                icon = {"success": "✅", "done": "✅", "failed": "❌", "cancelled": "🚫",
                        "pending": "⏳", "processing": "⏳"}.get(str(item.get("status")), "•")
                amount = f" · {format_price(item.get('amount'))} $" if item.get("amount") is not None else ""
                text += (f"{icon} <code>{tg_utils.escape(str(item.get('id'))[:20])}</code> "
                         f"{tg_utils.escape(str(item.get('type') or '-'))} → "
                         f"{tg_utils.escape(str(item.get('status') or '?'))}{amount}\n")
            kb = K()
            if page > 1:
                kb.add(B("⬅️ Ранее", None, f"{CB}:history:{page - 1}"))
            if items and (total is None or page * 20 < int(total or 0)):
                kb.add(B("➡️ Позднее", None, f"{CB}:history:{page + 1}"))
            kb.add(B("🔙 Назад", None, f"{CB}:menu"))
            answer(c, obj, text, kb)
        except DesslyError as e:
            answer(c, obj, f"❌ {tg_utils.escape(str(e))}", menu_keyboard(c))
        except Exception:
            logger.debug("TRACEBACK", exc_info=True)
            answer(c, obj, "❌ Не удалось получить историю (см. логи).", menu_keyboard(c))

    threading.Thread(target=worker, daemon=True, name=f"{PLUG}-History").start()


def do_check_now(c: Cardinal, obj: CallbackQuery) -> None:
    """Внеочередная проверка всех транзакций в работе."""
    if not is_authorized(c, obj.from_user.id):
        return

    def worker():
        waiting = [oid for oid, rec in load_orders().items() if rec.get("status") == "waiting_result"]
        if not waiting:
            answer(c, obj, "⏳ Транзакций в ожидании нет.", menu_keyboard(c))
            return
        for order_id in waiting:
            try:
                poll_record(c, order_id)
            except Exception:
                logger.debug("TRACEBACK", exc_info=True)
        still = [oid for oid, rec in load_orders().items() if rec.get("status") == "waiting_result"]
        answer(c, obj, f"🔄 Проверил {len(waiting)} транзакций, в ожидании осталось {len(still)}.",
               menu_keyboard(c))

    threading.Thread(target=worker, daemon=True, name=f"{PLUG}-CheckNow").start()


def open_limits(c: Cardinal, obj: Message | CallbackQuery) -> None:
    if not c.telegram or not is_authorized(c, getattr(obj.from_user, "id", None)):
        return
    settings = load_settings()
    text = f"""🛡 <b>Лимиты и защита</b>

💳 Порог баланса: <b>{format_price(settings.get('min_balance_usd', 0))} $</b>
🛡 Лимит одной покупки: <b>{format_price(settings.get('max_order_cost_usd', 0))} $</b>
📉 Минимальная маржа: <b>{settings.get('min_margin_percent', 0)}%</b> (0 = выключено)
💱 Валюта цен FunPay: код <b>{settings.get('funpay_currency_code', 5)}</b> (5=RUB, 1=USD, 37=KZT)
⏳ Ожидание данных покупателя: <b>{settings.get('wait_data_minutes', 60)} мин</b>
🔁 Интервал опроса: <b>{settings.get('poll_interval', 10)} сек</b>
⌛ Максимум опроса: <b>{settings.get('poll_max_minutes', 90)} мин</b>
🔔 Уведомлять о низком балансе: <b>{'да' if settings.get('notify_low_balance') else 'нет'}</b>"""
    kb = K()
    kb.add(B("💳 Порог баланса", None, f"{CB}:input:min_balance_usd"),
           B("🛡 Лимит покупки", None, f"{CB}:input:max_order_cost_usd"))
    kb.add(B("📉 Мин. маржа", None, f"{CB}:input:min_margin_percent"),
           B("⏳ Ожидание данных", None, f"{CB}:input:wait_data_minutes"))
    kb.add(B("🔁 Интервал опроса", None, f"{CB}:input:poll_interval"),
           B("⌛ Максимум опроса", None, f"{CB}:input:poll_max_minutes"))
    kb.add(B(f"🔔 Низкий баланс: {'ВКЛ' if settings.get('notify_low_balance') else 'ВЫКЛ'}", None,
             f"{CB}:toggle_low_balance"))
    kb.add(B("🔙 Назад", None, f"{CB}:menu"))
    answer(c, obj, text, kb)


def open_texts(c: Cardinal, obj: Message | CallbackQuery) -> None:
    if not c.telegram or not is_authorized(c, getattr(obj.from_user, "id", None)):
        return
    settings = load_settings()
    text = "💬 <b>Тексты сообщений покупателю</b>\n\n"
    kb = K()
    for field, (title, _placeholder, _hint) in EDITABLE_TEXTS.items():
        value = str(settings.get(field, "") or "")
        preview = value.replace("\n", " ")[:60] + ("..." if len(value) > 60 else "")
        text += f"{title}: <i>{tg_utils.escape(preview) if preview else '(пусто)'}</i>\n"
        kb.add(B(f"✏️ {title}", None, f"{CB}:text:{field}"))
    text += ("\nДоступны переменные: $username, $order и переменные плагина "
             "(см. README). Перенос строки: \\n")
    kb.add(B("🔙 Назад", None, f"{CB}:menu"))
    answer(c, obj, text, kb)


def ask_input(c: Cardinal, obj: CallbackQuery, field: str) -> None:
    """Просит ввести значение настройки."""
    if not is_authorized(c, obj.from_user.id):
        return
    if field not in INPUT_FIELDS:
        return
    title, placeholder, _converter, hint = INPUT_FIELDS[field]
    tg = c.telegram
    text = (f"{title}\n\nВведите: <i>{tg_utils.escape(placeholder)}</i>\n\n{tg_utils.escape(hint)}\n\n"
            f"<i>Отмена - /cancel или кнопка ниже.</i>")
    kb = K().add(B("🔙 Отмена", None, f"{CB}:menu"))
    message = tg.bot.send_message(obj.message.chat.id, text, reply_markup=kb, parse_mode="HTML")
    tg.set_state(obj.message.chat.id, message.id, obj.from_user.id, INPUT_STATE, {"field": field})
    try:
        tg.bot.answer_callback_query(obj.id)
    except Exception:
        logger.debug("TRACEBACK", exc_info=True)


def handle_input(m: Message) -> None:
    """Обрабатывает введенное значение настройки."""
    c = _cardinal
    if c is None or not c.telegram or not is_authorized(c, m.from_user.id):
        return
    tg = c.telegram
    state = tg.get_state(m.chat.id, m.from_user.id)
    if not state or state.get("state") != INPUT_STATE:
        return
    field = (state.get("data") or {}).get("field")
    tg.clear_state(m.chat.id, m.from_user.id, True)
    if field not in INPUT_FIELDS:
        return
    title, _placeholder, converter, _hint = INPUT_FIELDS[field]
    value = (m.text or "").strip()
    if not value:
        tg.bot.send_message(m.chat.id, "❌ Пустое значение - ничего не изменил.", parse_mode="HTML")
        return
    try:
        if field == "mapping":
            apply_mapping(value)
            tg.bot.send_message(m.chat.id, f"✅ Привязка добавлена:\n<code>{tg_utils.escape(value)}</code>",
                                parse_mode="HTML")
            open_mappings(c, m)
            return
        if extra_input(field, value, m):
            return
        if field in ("apikey", "secret") and value in ("-", "удалить", "delete"):
            update_setting(field, "")
            update_setting("_detected_mode", None)
            tg.bot.send_message(m.chat.id, f"✅ {title}: удален.", parse_mode="HTML",
                                reply_markup=menu_keyboard(c))
            return
        converted = converter(value)
        if field in ("max_order_cost_usd", "min_balance_usd", "min_margin_percent") and \
                float(converted) < 0:
            raise ValueError("значение не может быть отрицательным")
        if field in ("wait_data_minutes", "poll_interval", "poll_max_minutes") and int(converted) < 1:
            raise ValueError("значение должно быть больше нуля")
        if field in EDITABLE_TEXTS:
            converted = value.replace("\\n", "\n")
    except (ValueError, TypeError, PluginConfigError) as e:
        tg.bot.send_message(m.chat.id, f"❌ Не удалось сохранить {title}: {tg_utils.escape(str(e))}",
                            parse_mode="HTML")
        return
    update_setting(field, converted)
    if field in ("apikey", "secret"):
        update_setting("_detected_mode", None)
    shown = mask_key(converted) if field in ("apikey", "secret") else (
        "сохранен" if field in EDITABLE_TEXTS else f"{converted}")
    logger.info(f"[{PLUG}] Настройка $YELLOW{field}$RESET изменена пользователем "
                f"$MAGENTA{m.from_user.id}$RESET")
    tg.bot.send_message(m.chat.id, f"✅ {title}: <code>{tg_utils.escape(str(shown))}</code>",
                        parse_mode="HTML", reply_markup=menu_keyboard(c))


def on_settings_callback(c: Cardinal, obj: CallbackQuery) -> None:
    """Диспетчер callback'ов меню плагина."""
    try:
        data = str(obj.data or "")
        if data.startswith(f"{CBT.PLUGIN_SETTINGS}:{UUID}"):
            open_menu(c, obj)
            return
        if not data.startswith(f"{CB}:"):
            return
        if not c.telegram or not is_authorized(c, obj.from_user.id):
            try:
                c.telegram.bot.answer_callback_query(obj.id, "Недостаточно прав.")
            except Exception:
                logger.debug("TRACEBACK", exc_info=True)
            return
        action = data[len(CB) + 1:]
        if action == "menu":
            open_menu(c, obj)
        elif action == "toggle_enabled":
            toggle_setting(c, obj, "enabled")
        elif action == "toggle_dry":
            toggle_setting(c, obj, "dry_run")
        elif action == "toggle_low_balance":
            toggle_setting(c, obj, "notify_low_balance")
        elif action.startswith("input:"):
            ask_input(c, obj, action.split(":", 1)[1])
        elif action == "balance":
            do_balance(c, obj)
        elif action == "test":
            do_test(c, obj)
        elif action == "mappings":
            open_mappings(c, obj)
        elif action.startswith("delmap:"):
            delete_mapping(c, obj)
        elif action == "history":
            open_history(c, obj, 1)
        elif action.startswith("history:"):
            open_history(c, obj, _int_or_none(action.split(":")[1]) or 1)
        elif action == "check_now":
            do_check_now(c, obj)
        elif action == "limits":
            open_limits(c, obj)
        elif action == "texts":
            open_texts(c, obj)
        elif action.startswith("text:"):
            ask_input(c, obj, action.split(":", 1)[1])
        elif action == "apimode":
            cycle_api_mode(c, obj)
        elif action == "close":
            answer(c, obj, "Меню закрыто.")
        elif not on_extra_callback(c, obj, action):
            try:
                c.telegram.bot.answer_callback_query(obj.id, "Неизвестная команда.")
            except Exception:
                logger.debug("TRACEBACK", exc_info=True)
    except Exception:
        logger.error(f"[{PLUG}] Ошибка обработки callback меню.")
        logger.debug("TRACEBACK", exc_info=True)


def on_cmd_command(m: Message) -> None:
    """Команда /CMD: открывает меню плагина."""
    c = _cardinal
    if c is None or not c.telegram:
        return
    if not is_authorized(c, m.from_user.id):
        c.telegram.bot.reply_to(m, "⛔ Эта команда доступна только авторизованным пользователям.")
        return
    open_menu(c, m)


# ══════════════════════════════ ЖИЗНЕННЫЙ ЦИКЛ ══════════════════════════════
def pre_init(c: Cardinal) -> None:
    """Регистрирует команду, страницу настроек и переводы."""
    global _cardinal
    _cardinal = c
    load_settings()
    load_orders()

    for language, text in CMD_TEXTS:
        try:
            localizer.add_translation(UUID, f"cmd_{CMD}", text, language)
        except Exception:
            logger.debug("TRACEBACK", exc_info=True)

    if not c.telegram:
        logger.info(f"[{PLUG}] Telegram отключен - меню плагина недоступно.")
        return
    c.telegram.msg_handler(on_cmd_command, commands=[CMD])
    c.telegram.cbq_handler(lambda obj: on_settings_callback(c, obj),
                           lambda call: call.data.startswith(f"{CBT.PLUGIN_SETTINGS}:{UUID}"))
    c.telegram.cbq_handler(lambda obj: on_settings_callback(c, obj),
                           lambda call: call.data.startswith(f"{CB}:"))
    c.telegram.msg_handler(handle_input,
                           func=lambda msg: c.telegram.check_state(msg.chat.id, msg.from_user.id,
                                                                   INPUT_STATE))
    c.add_telegram_commands(UUID, [(CMD, f"{UUID}_cmd_{CMD}", True)])
    settings = load_settings()
    logger.info(f"[{PLUG}] Команда $YELLOW/{CMD}$RESET зарегистрирована. "
                f"enabled={settings.get('enabled')}, dry_run={settings.get('dry_run')}, "
                f"api_mode={settings.get('api_mode')}, привязок лотов: "
                f"{len(settings.get('mappings') or {})}")
    if settings.get("enabled") and not (settings.get("apikey") or settings.get("secret")):
        logger.warning(f"[{PLUG}] Плагин включен, но ключи DesslyHub не заданы - выдача не сработает.")


def post_start(c: Cardinal) -> None:
    """Восстанавливает прерванные заказы и запускает поток опроса."""
    global _thread_started
    recover_interrupted(c)
    if _thread_started:
        return
    _thread_started = True
    threading.Thread(target=poller_loop, args=(c, c.run_id), daemon=True,
                     name=f"{PLUG}Poller").start()


def on_delete(c: Cardinal, call) -> None:
    """Удаляет файлы плагина при его удалении через ПУ Telegram."""
    import shutil
    try:
        if os.path.exists(PLUGIN_DIR):
            shutil.rmtree(PLUGIN_DIR, ignore_errors=True)
    except Exception:
        logger.debug("TRACEBACK", exc_info=True)
    try:
        c.telegram.bot.answer_callback_query(call.id, f"Файлы плагина {PLUG} удалены.")
    except Exception:
        logger.debug("TRACEBACK", exc_info=True)


# ══════════════════════════════ ХУКИ (переопределяются кодом плагина ниже) ══════════════════════════════
def extra_menu(c: Cardinal) -> list:
    """Дополнительные кнопки меню: [(текст, callback_data)]."""
    return []


def on_extra_callback(c: Cardinal, obj: CallbackQuery, action: str) -> bool:
    """Обработка специфичных callback'ов плагина. True = обработано."""
    return False


def menu_text_extra(c: Cardinal) -> str:
    """Дополнительные строки в тексте меню."""
    return ""


def message_vars(record: dict) -> dict:
    """Переменные для шаблонов сообщений покупателю: {имя: значение} ($имя)."""
    return {}


def start_order(c: Cardinal, order_id: str) -> None:
    """Запуск сценария выдачи для заказа (реализует плагин)."""
    logger.warning(f"[{PLUG}] start_order не реализован для заказа #{order_id}.")


def poll_record(c: Cardinal, order_id: str) -> None:
    """Опрос транзакции заказа (реализует плагин)."""


def extract_waiting_data(c: Cardinal, order_id: str, text: str, record: dict) -> bool:
    """Пытается извлечь ожидаемые данные из сообщения покупателя. True = данные приняты."""
    return False


def apply_mapping(value: str) -> None:
    """Добавляет привязку лота из строки меню (реализует плагин)."""
    raise PluginConfigError("привязки лотов этим плагином не поддерживаются")


def render_mapping(key: str, mapping: dict) -> str:
    """HTML-строка привязки для списка в меню."""
    return (f"<code>{tg_utils.escape(str(key))}</code> → "
            f"{tg_utils.escape(json.dumps(mapping, ensure_ascii=False))}")


def extra_input(field: str, value: str, m: Message | None = None) -> bool:
    """Обработка специфичных полей ввода. True = обработано."""
    return False


# ══════════════════════════════ ПОЛЯ ВВОДА МЕНЮ ══════════════════════════════
INPUT_FIELDS: dict[str, tuple] = {
    "apikey": ("🔑 API-ключ DesslyHub", "ключ или - чтобы удалить", str,
               "Выдаёт менеджер DesslyHub (@DesslyHub_Manager). Вводится здесь, а не в чате."),
    "secret": ("🔐 Секретный ключ (API v2)", "секрет или - чтобы удалить", str,
               "Нужен для нового поколения API (HMAC-подпись). Без него используется legacy-режим."),
    "min_balance_usd": ("💳 Порог баланса, $", "5.0", float,
                        "Уведомление при балансе ниже порога; покупки приостанавливаются."),
    "max_order_cost_usd": ("🛡 Лимит одной покупки, $", "100.0", float,
                           "Покупки дороже лимита не выполняются (защита от ошибок настроек)."),
    "min_margin_percent": ("📉 Минимальная маржа, %", "0", float,
                           "0 = выключено. Сделка с маржой ниже порога не выполняется."),
    "wait_data_minutes": ("⏳ Ожидание данных, минут", "60", int,
                          "Сколько ждать, пока покупатель пришлёт данные в чат."),
    "poll_interval": ("🔁 Интервал опроса, сек", "10", int,
                      "Как часто проверять статусы транзакций DesslyHub."),
    "poll_max_minutes": ("⌛ Максимум опроса, минут", "90", int,
                         "После этого транзакция считается зависшей - сообщу продавцу."),
}
for _field, (_title, _placeholder, _hint) in EDITABLE_TEXTS.items():
    INPUT_FIELDS[_field] = (_title, _placeholder, str, _hint)
INPUT_FIELDS.update(EXTRA_INPUT_FIELDS)


# ══════════════════════════════ ЛОГИКА ПЛАГИНА: ПОПОЛНЕНИЕ STEAM ══════════════════════════════
LOGIN_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")
PROFILE_RE = re.compile(r"steamcommunity\.com/(?:id/([A-Za-z0-9_\-]{3,32})|profiles/\d+)", re.I)


def parse_amount(text: str) -> tuple[float | None, str]:
    """Разбирает сумму привязки: ('5' -> 5.0 USD), ('500 rub' -> 500.0 RUB)."""
    text = str(text or "").strip().replace(",", ".")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(usd|rub|₽|\$|р|руб)?", text, re.I)
    if not match:
        return None, ""
    amount = float(match.group(1))
    currency = (match.group(2) or "usd").lower()
    if currency in ("rub", "₽", "р", "руб"):
        return amount, "RUB"
    return amount, "USD"


def apply_mapping(value: str) -> None:
    """Добавляет привязку лота: «Название лота | 5» или «Название лота | 500 RUB»."""
    parts = [p.strip() for p in str(value).split("|")]
    if len(parts) < 2 or not parts[0]:
        raise PluginConfigError("формат: Название лота | 5 (USD) или Название лота | 500 RUB")
    amount, currency = parse_amount(parts[1])
    if amount is None or amount <= 0:
        raise PluginConfigError("не удалось разобрать сумму (примеры: 5, 500 RUB)")
    key = normalize_lot_name(parts[0])
    settings = load_settings()
    mappings = settings.get("mappings") or {}
    mapping: dict[str, Any] = {}
    if currency == "RUB":
        mapping["amount_rub"] = amount
    else:
        mapping["amount_usd"] = amount
    mappings[key] = mapping
    update_setting("mappings", mappings)
    logger.info(f"[{PLUG}] Добавлена привязка «{key}» -> {mapping}")


def render_mapping(key: str, mapping: dict) -> str:
    if mapping.get("amount_rub"):
        amount = f"{format_price(mapping.get('amount_rub'))} ₽ (по курсу)"
    else:
        amount = f"{format_price(mapping.get('amount_usd'))} $"
    return f"<code>{tg_utils.escape(str(key))}</code> → пополнение на <b>{amount}</b>"


def match_login(text: str | None) -> str | None:
    """Ищет логин Steam в тексте: голое слово-логин или ссылку на профиль."""
    if not text:
        return None
    text = str(text)
    match = PROFILE_RE.search(text)
    if match and match.group(1):
        return match.group(1)
    for word in re.split(r"[\s,;:()\[\]<>\"']+", text):
        word = word.strip(".!?")
        if LOGIN_RE.fullmatch(word) and not word.isdigit():
            return word
    return None


def extract_login(order, settings: dict) -> str | None:
    """Ищет логин Steam в полях заказа и в описании."""
    if order is None:
        return None
    aliases = settings.get("login_field_names") or []
    value = extract_by_aliases(order, aliases)
    if value:
        found = match_login(value)
        if found:
            return found
    for _name, value in order_field_texts(order):
        found = match_login(value)
        if found:
            return found
    for text in (getattr(order, "description", None), getattr(order, "short_description", None)):
        found = match_login(text)
        if found:
            return found
    return None


def resolve_cost_usd(c: Cardinal, order_id: str, mapping: dict, client: DesslyClient) -> float | None:
    """Стоимость топ-апа в USD: из USD-привязки напрямую или конвертацией RUB по курсу."""
    if mapping.get("amount_usd"):
        return float(mapping["amount_usd"])
    rub = float(mapping.get("amount_rub") or 0)
    if not rub:
        fail_order(c, order_id, None, "в привязке лота не указана сумма пополнения")
        return None
    try:
        rate = client.exchange_rate(CURRENCY_CODES["RUB"])
    except DesslyError as e:
        fail_order(c, order_id, e.code, "не удалось получить курс RUB для конвертации суммы")
        return None
    if not rate:
        fail_order(c, order_id, None, "курс RUB равен нулю - конвертация невозможна")
        return None
    return round(rub / float(rate), 2)


def start_order(c: Cardinal, order_id: str) -> None:
    """Сценарий выдачи: логин -> проверки -> покупка топ-апа -> ожидание результата."""
    settings = load_settings()
    record = get_record(order_id) or {}
    mapping = dict(record.get("mapping") or {})
    order = get_order_safe(c, order_id)

    username = record.get("username") or extract_login(order, settings)
    if not username:
        if settings.get("ask_for_data") and record.get("chat_id"):
            ask_buyer_data(c, order_id)
            logger.info(f"[{PLUG}] Заказ #{order_id}: логин не найден - жду ответ покупателя.")
            return
        fail_order(c, order_id, -100, "логин Steam не найден ни в заказе, ни в чате")
        return
    if not LOGIN_RE.fullmatch(str(username)):
        fail_order(c, order_id, -100, f"«{username}» не похож на логин Steam")
        return

    client = make_client(settings)
    cost_usd = resolve_cost_usd(c, order_id, mapping, client)
    if cost_usd is None:
        return

    ok, income_usd = money_guards(c, order_id, client, settings, cost_usd)
    if not ok:
        return

    if record.get("dry_run") or settings.get("dry_run"):
        set_record(order_id, status="dry_run", username=username, amount_usd=cost_usd)
        notify(c, f"🧪 <b>{PLUG}</b> DRY-RUN: заказ <code>{tg_utils.escape(str(order_id))}</code> - "
                  f"пополнил бы Steam <code>{tg_utils.escape(str(username))}</code> на "
                  f"<b>{format_price(cost_usd)} $</b>"
                  + (f" (доход {format_price(income_usd)} $)" if income_usd else "")
                  + ". Покупка НЕ совершалась.")
        logger.info(f"[{PLUG}] DRY-RUN заказ #{order_id}: {username} +{cost_usd}$")
        return

    if settings.get("check_login"):
        try:
            can = client.check_steam_login(username, cost_usd)
        except DesslyError as e:
            fail_order(c, order_id, e.code, f"проверка логина {username} не удалась")
            return
        if not can:
            fail_order(c, order_id, -100, f"аккаунт {username} нельзя пополнить "
                                          f"(проверьте логин/регион/ограничения Steam)")
            return

    set_record(order_id, status="sending", username=username, amount_usd=cost_usd)
    logger.info(f"[{PLUG}] Заказ #{order_id}: пополняю Steam {username} на {cost_usd} $")
    response = client.steam_topup(username, cost_usd, reference=f"funpay-{order_id}")
    if response.get("error_code"):
        fail_order(c, order_id, int(response["error_code"]), "пополнение отклонено при создании")
        return
    if not response.get("txn"):
        set_record(order_id, status="error", error_text="DesslyHub не вернул ID транзакции")
        notify(c, f"⚠️ <b>{PLUG}</b>: заказ <code>{tg_utils.escape(str(order_id))}</code> - "
                  f"DesslyHub не вернул ID транзакции. Проверьте историю операций вручную "
                  f"(деньги могли списаться).", True)
        return
    set_record(order_id, status="waiting_result", txn=str(response["txn"]),
               sent_at=int(time.time()))
    notify(c, f"⏳ <b>{PLUG}</b>: заказ <code>{tg_utils.escape(str(order_id))}</code> - пополнение "
              f"Steam <code>{tg_utils.escape(str(username))}</code> на "
              f"<b>{format_price(cost_usd)} $</b> отправлено (транзакция "
              f"<code>{tg_utils.escape(str(response['txn'])[:20])}</code>), слежу за статусом.")


def poll_record(c: Cardinal, order_id: str) -> None:
    """Опрос транзакции пополнения; при успехе пишет покупателю и закрывает заказ."""
    info = advance_txn(c, order_id)
    if not info or info.get("status") != "success":
        return
    record = get_record(order_id) or {}
    settings = load_settings()
    send_buyer_message(c, record, str(settings.get("success_text", "")))
    finish_success(c, order_id, f"Steam {record.get('username', '?')} "
                                f"+{format_price(record.get('amount_usd'))} $")


def message_vars(record: dict) -> dict:
    return {"login": record.get("username", ""),
            "amount": format_price(record.get("amount_usd"))}


def extract_waiting_data(c: Cardinal, order_id: str, text: str, record: dict) -> bool:
    """Покупатель прислал логин в чат - продолжаем выдачу."""
    username = match_login(text)
    if not username:
        return False
    set_record(order_id, username=username, status="sending")
    logger.info(f"[{PLUG}] Заказ #{order_id}: покупатель прислал логин {username} - продолжаю.")
    threading.Thread(target=start_order, args=(c, str(order_id)), daemon=True,
                     name=f"{PLUG}-{order_id}").start()
    return True


def extra_menu(c: Cardinal) -> list:
    return [(f"📊 Статистика выдач", f"{CB}:stats")]


def on_extra_callback(c: Cardinal, obj: CallbackQuery, action: str) -> bool:
    if action != "stats":
        return False

    def worker():
        orders = load_orders()
        today = datetime.date.today().isoformat()
        total_done = total_failed = today_done = 0
        today_spent = today_profit = 0.0
        for record in orders.values():
            status = record.get("status")
            day = datetime.date.fromtimestamp(int(record.get("updated") or 0)).isoformat() \
                if record.get("updated") else ""
            if status == "done":
                total_done += 1
                if day == today:
                    today_done += 1
                    today_spent += float(record.get("spent_usd") or record.get("cost_usd") or 0)
                    today_profit += float(record.get("profit_usd") or 0)
            elif status in ("failed", "error", "timeout"):
                total_failed += 1
        text = (f"📊 <b>Статистика {PLUG}</b>\n\n"
                f"Сегодня: ✅ <b>{today_done}</b> пополнений"
                f"{f', потрачено {format_price(today_spent)} $, маржа {format_price(today_profit)} $' if today_done else ''}\n"
                f"Всего в журнале: ✅ {total_done}  |  ❌ {total_failed}\n\n"
                f"{stats_line()}")
        answer(c, obj, text, menu_keyboard(c))

    threading.Thread(target=worker, daemon=True, name=f"{PLUG}-Stats").start()
    return True


# ══════════════════════════════ ПРИВЯЗКА К CARDINAL ══════════════════════════════
BIND_TO_PRE_INIT = [pre_init]
BIND_TO_POST_START = [post_start]
BIND_TO_NEW_ORDER = [on_new_order]
BIND_TO_NEW_MESSAGE = [on_new_message]
BIND_TO_LAST_CHAT_MESSAGE_CHANGED = [on_new_message]
BIND_TO_DELETE = on_delete
