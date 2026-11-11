"""Минимальный клиент Telegram Bot API (включая методы виджетов из Bot API 10.4).

Своя обёртка вместо библиотеки: виджеты появились в API совсем недавно,
а нам нужно всего несколько методов."""
import json
import logging

import aiohttp

log = logging.getLogger("tg")


class TelegramError(Exception):
    def __init__(self, method: str, code: int, description: str):
        super().__init__(f"{method}: {code} {description}")
        self.code = code
        self.description = description


class Bot:
    def __init__(self, token: str, api_base: str = "https://api.telegram.org"):
        self.token = token
        self.base = f"{api_base.rstrip('/')}/bot{token}/"
        self.session: aiohttp.ClientSession | None = None

    async def start(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=70))

    async def close(self):
        if self.session:
            await self.session.close()

    async def call(self, method: str, **params):
        payload = {}
        for k, v in params.items():
            if v is None:
                continue
            payload[k] = v
        async with self.session.post(self.base + method, json=payload) as r:
            data = await r.json(content_type=None)
        if not data.get("ok"):
            raise TelegramError(method, data.get("error_code", 0), data.get("description", ""))
        return data["result"]

    # --- обычные сообщения ---
    async def send_message(self, chat_id, text: str, **kw):
        return await self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML", **kw)

    # --- Bot API 10.4: виджеты ---
    async def send_widget(self, chat_id, html: str, *, chat_data: dict | None = None,
                          public_data: dict | None = None, caption: str | None = None,
                          allow_queries: bool = True, height: int | None = None, **kw):
        return await self.call(
            "sendWidget", chat_id=chat_id, html=html,
            chat_data=json.dumps(chat_data, ensure_ascii=False, separators=(",", ":")) if chat_data is not None else None,
            public_data=json.dumps(public_data, ensure_ascii=False, separators=(",", ":")) if public_data is not None else None,
            caption=caption, parse_mode="HTML" if caption else None,
            allow_queries=allow_queries, height=height, **kw,
        )

    async def answer_widget_query(self, query_id: str, data: dict | str | None = None):
        if isinstance(data, dict):
            data = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        return await self.call("answerWidgetQuery", widget_query_id=query_id, data=data)

    async def edit_widget_data(self, chat_id, message_id: int, chat_data: dict):
        return await self.call(
            "editMessageWidgetData", chat_id=chat_id, message_id=message_id,
            chat_data=json.dumps(chat_data, ensure_ascii=False, separators=(",", ":")),
        )
