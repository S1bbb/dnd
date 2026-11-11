"""Точка входа: поднимает сайт мастера и бота в одном процессе."""
import asyncio
import logging
import os
import secrets
import sys

from aiohttp import web

from .db import DB
from .game import Game
from .tg import Bot
from .web import make_app

log = logging.getLogger("main")


class OfflineBot(Bot):
    """Бот-заглушка для демо-режима без токена: ничего не отправляет, только пишет в лог."""

    def __init__(self):
        super().__init__("offline")
        self.mid = 1000

    async def start(self):
        pass

    async def close(self):
        pass

    async def call(self, method, **params):
        log.info("[offline] %s %s", method, {k: v for k, v in params.items() if k != "html"})
        if method == "getMe":
            return {"id": 1, "username": "your_dnd_bot", "first_name": "DnD"}
        self.mid += 1
        return {"message_id": self.mid}


async def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    token = os.getenv("BOT_TOKEN", "").strip()
    password = os.getenv("GM_PASSWORD", "").strip()
    demo = os.getenv("DEMO") == "1"
    if not password:
        sys.exit("Задай GM_PASSWORD в .env — это пароль от сайта мастера")
    if not token and not demo:
        sys.exit("Задай BOT_TOKEN в .env (получить у @BotFather)")

    db_path = os.getenv("DB_PATH", "data/dnd.sqlite3")
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    db = DB(db_path)
    secret = db.get("secret") or secrets.token_hex(32)
    db.set("secret", secret)

    bot = OfflineBot() if demo else Bot(token, os.getenv("TELEGRAM_API", "https://api.telegram.org"))
    await bot.start()
    game = Game(db, bot)
    if demo:
        from .demo_data import seed
        await seed(game)

    app = make_app(game, password, secret)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "8080"))
    await web.TCPSite(runner, os.getenv("HOST", "0.0.0.0"), port).start()
    log.info("сайт мастера: http://localhost:%s", port)

    try:
        if demo:
            game.me = await bot.call("getMe")
            await asyncio.Event().wait()
        else:
            await game.poll()
    finally:
        await bot.close()
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
