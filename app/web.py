"""Сайт мастера: страница, REST API и WebSocket с живой лентой бросков."""
import asyncio
import hashlib
import hmac
import json
import logging
from pathlib import Path

from aiohttp import web, WSMsgType

from . import dice
from .game import Game

log = logging.getLogger("web")
STATIC = Path(__file__).parent / "static"
COOKIE = "gm"


def make_app(game: Game, password: str, secret: str, demo_mode: bool = False) -> web.Application:
    token = hmac.new(secret.encode(), b"gm:" + password.encode(), hashlib.sha256).hexdigest()

    def authed(req: web.Request) -> bool:
        return hmac.compare_digest(req.cookies.get(COOKIE, ""), token)

    @web.middleware
    async def auth(req: web.Request, handler):
        open_paths = ("/login", "/demo", "/static/")
        if req.path == "/login" or req.path.startswith(open_paths) or authed(req):
            return await handler(req)
        if req.path.startswith("/api") or req.path == "/ws":
            return web.json_response({"error": "unauthorized"}, status=401)
        raise web.HTTPFound("/login")

    app = web.Application(middlewares=[auth])
    r = app.router

    async def index(req):
        return web.FileResponse(STATIC / "index.html")

    async def login_page(req):
        return web.FileResponse(STATIC / "login.html")

    async def login(req):
        form = await req.post()
        if hmac.compare_digest(str(form.get("password", "")), password):
            resp = web.HTTPFound("/")
            resp.set_cookie(COOKIE, token, max_age=60 * 60 * 24 * 90, httponly=True, samesite="Lax")
            raise resp
        raise web.HTTPFound("/login?bad=1")

    async def logout(req):
        resp = web.HTTPFound("/login")
        resp.del_cookie(COOKIE)
        raise resp

    async def demo(req):
        return web.FileResponse(STATIC / "demo.html")

    async def widget_src(req):
        from . import widgets
        name = req.match_info["name"]
        if name not in ("dice", "sheet", "request"):
            raise web.HTTPNotFound()
        return web.Response(text=widgets.load(name), content_type="text/html")

    async def demo_data(req):
        """Только в демо-режиме: данные персонажа для превью виджетов на /demo."""
        from .game import compact_char, info_text
        chars = [c for c in game.db.characters() if c["tg_id"]]
        if not demo_mode or not chars:
            raise web.HTTPNotFound()
        c = next((c for c in chars if c["data"]["atk"]), chars[0])
        return web.json_response({"sheet": compact_char(c["data"]), "info": info_text(c["data"])})

    async def state(req):
        return web.json_response(game.state())

    async def body(req) -> dict:
        try:
            return await req.json()
        except json.JSONDecodeError:
            raise web.HTTPBadRequest(text="bad json")

    def cid_of(req) -> int:
        try:
            return int(req.match_info["id"])
        except ValueError:
            raise web.HTTPNotFound()

    async def create_char(req):
        data = await body(req)
        try:
            ch = game.create_char(data)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"id": ch["id"]})

    async def save_char(req):
        data = await body(req)
        try:
            ch = await game.update_char(cid_of(req), data)
        except KeyError:
            raise web.HTTPNotFound()
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"char": ch})

    async def hp_delta(req):
        cid = cid_of(req)
        data = await body(req)
        ch = game.db.character(cid)
        if not ch:
            raise web.HTTPNotFound()
        c = ch["data"]
        new = max(-c["mhp"], min(c["mhp"], c["hp"] + int(data.get("delta", 0))))
        c = await game.update_char(cid, {"hp": new})
        return web.json_response({"char": c})

    async def delete_char(req):
        game.db.delete_character(cid_of(req))
        game.emit_chars()
        return web.json_response({"ok": True})

    async def unbind_char(req):
        game.db.unbind(cid_of(req))
        game.emit_chars()
        return web.json_response({"ok": True})

    async def send_char(req):
        ok = await game.send_char_to_player(cid_of(req))
        return web.json_response({"ok": ok})

    def all_bound() -> list[int]:
        return [c["id"] for c in game.db.characters() if c["tg_id"]]

    async def gm_roll(req):
        data = await body(req)
        try:
            entry = game.gm_roll(str(data.get("formula", "")), str(data.get("title", ""))[:60], int(data.get("adv", 0)))
        except dice.DiceError as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response(entry)

    async def ask(req):
        data = await body(req)
        ids = data.get("chars") or all_bound()
        kind = str(data.get("kind", "c:dex"))
        k, _, v = kind.partition(":")
        if k == "f":
            try:
                dice.parse(v)
            except dice.DiceError as e:
                return web.json_response({"error": str(e)}, status=400)
        elif k not in ("c", "s", "k", "i"):
            return web.json_response({"error": "bad kind"}, status=400)
        sent = await game.ask_roll([int(i) for i in ids], kind, str(data.get("title", ""))[:60])
        return web.json_response({"sent": sent})

    async def message(req):
        data = await body(req)
        ids = data.get("chars") or all_bound()
        text = str(data.get("text", "")).strip()[:3000]
        if not text:
            return web.json_response({"error": "пустое сообщение"}, status=400)
        n = await game.message_players([int(i) for i in ids], text)
        return web.json_response({"sent": n})

    async def clear(req):
        game.db.clear_rolls()
        game.emit({"type": "cleared"})
        return web.json_response({"ok": True})

    async def ws(req):
        sock = web.WebSocketResponse(heartbeat=25)
        await sock.prepare(req)
        q: asyncio.Queue = asyncio.Queue()
        game.listeners.add(q)

        async def pump():
            while True:
                await sock.send_json(await q.get())

        task = asyncio.create_task(pump())
        try:
            async for msg in sock:
                if msg.type == WSMsgType.ERROR:
                    break
        finally:
            task.cancel()
            game.listeners.discard(q)
        return sock

    r.add_get("/", index)
    r.add_get("/login", login_page)
    r.add_post("/login", login)
    r.add_get("/logout", logout)
    r.add_get("/demo", demo)
    r.add_get("/demo/widget/{name}", widget_src)
    r.add_get("/demo/data", demo_data)
    r.add_static("/static", STATIC)
    r.add_get("/api/state", state)
    r.add_post("/api/char", create_char)
    r.add_post("/api/char/{id}", save_char)
    r.add_post("/api/char/{id}/hp", hp_delta)
    r.add_post("/api/char/{id}/unbind", unbind_char)
    r.add_post("/api/char/{id}/send", send_char)
    r.add_delete("/api/char/{id}", delete_char)
    r.add_post("/api/roll", gm_roll)
    r.add_post("/api/ask", ask)
    r.add_post("/api/message", message)
    r.add_post("/api/clear", clear)
    r.add_get("/ws", ws)
    return app
