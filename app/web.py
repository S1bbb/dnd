"""Сайт мастера: страница, REST API (всё в рамках выбранной игры) и WebSocket с живыми событиями.

Права: сайт — только мастер (пароль → подписанная кука). Игроки действуют через бота, бот проверяет,
что персонаж принадлежит именно этому Telegram-аккаунту.
Надёжность: заголовок X-Op делает POST идемпотентным (повтор вернёт прошлый ответ), поле rev у персонажа
защищает от одновременных правок (409 — кто-то уже изменил, обнови)."""
import asyncio
import hashlib
import hmac
import io
import json
import logging
from pathlib import Path

from aiohttp import web, WSMsgType

from . import dice, engine
from .data.backgrounds import BACKGROUNDS, FEATS, LANGUAGES
from .data.classes import CLASSES
from .data.equipment import CATALOG, item as make_item
from .data.pregens import PREGENS
from .data.races import RACES
from .data.spells import SPELLS
from .db import Conflict
from .game import Game

log = logging.getLogger("web")
STATIC = Path(__file__).parent / "static"
COOKIE = "gm"


def make_app(game: Game, password: str, secret: str, demo_mode: bool = False) -> web.Application:
    token = hmac.new(secret.encode(), b"gm:" + password.encode(), hashlib.sha256).hexdigest()
    db = game.db

    def authed(req: web.Request) -> bool:
        return hmac.compare_digest(req.cookies.get(COOKIE, ""), token)

    @web.middleware
    async def auth(req: web.Request, handler):
        if req.path in ("/login",) or req.path.startswith(("/demo", "/static/")) or authed(req):
            return await handler(req)
        if req.path.startswith("/api") or req.path == "/ws":
            return web.json_response({"error": "unauthorized"}, status=401)
        raise web.HTTPFound("/login")

    @web.middleware
    async def idempotent(req: web.Request, handler):
        op = req.headers.get("X-Op")
        if req.method in ("POST", "DELETE") and op and req.path.startswith("/api"):
            done = db.op_done(op)
            if done is not None:
                return web.json_response(done["body"], status=done["status"])
            resp = await handler(req)
            if isinstance(resp, web.Response) and resp.content_type == "application/json" and resp.status < 500:
                db.op_save(op, {"status": resp.status, "body": json.loads(resp.text)})
            return resp
        return await handler(req)

    @web.middleware
    async def errors(req: web.Request, handler):
        try:
            return await handler(req)
        except Conflict:
            return web.json_response({"error": "Персонажа уже изменили (в боте или в другой вкладке) — данные обновлены, повтори правку."}, status=409)
        except (ValueError, dice.DiceError) as e:
            return web.json_response({"error": str(e)}, status=400)
        except KeyError:
            raise web.HTTPNotFound()

    app = web.Application(middlewares=[errors, auth, idempotent], client_max_size=12 * 1024 * 1024)
    r = app.router

    def J(x, status=200):
        return web.json_response(x, status=status)

    async def body(req) -> dict:
        try:
            return await req.json()
        except json.JSONDecodeError:
            raise web.HTTPBadRequest(text="bad json")

    def gid_of(req) -> int:
        try:
            gid = int(req.match_info["gid"])
        except ValueError:
            raise web.HTTPNotFound()
        if not db.game(gid):
            raise web.HTTPNotFound()
        return gid

    def cid_of(req) -> int:
        try:
            return int(req.match_info["id"])
        except ValueError:
            raise web.HTTPNotFound()

    # ---------- страницы ----------
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
        from .game import compact_char, info_text
        chars = [c for c in db.characters() if c["tg_id"]]
        if not demo_mode or not chars:
            raise web.HTTPNotFound()
        c = next((c for c in chars if engine.is_v2(c["data"])), chars[0])
        return J({"sheet": compact_char(c["data"], c["id"]), "info": info_text(c["data"])})

    # ---------- игры ----------
    async def games(req):
        return J({"games": db.games()})

    async def create_game(req):
        d = await body(req)
        return J(game.create_game(str(d.get("name", ""))))

    async def update_game(req):
        gid = gid_of(req)
        d = await body(req)
        if d.get("status") in ("active", "archived"):
            game.set_game_status(gid, d["status"])
        if d.get("name"):
            db.update_game(gid, name=str(d["name"])[:60])
            game.emit({"type": "games"})
        return J(db.game(gid))

    async def state(req):
        return J(game.state(gid_of(req)))

    async def new_lobby(req):
        gid = gid_of(req)
        code = db.new_lobby(gid)
        return J({"lobby": code, "lobby_link": game.link(code)})

    async def catalog(req):
        return J({
            "spells": [{"n": s["n"], "lvl": s["lvl"], "cls": s["cls"], "d": s["d"], "conc": s["conc"]} for s in SPELLS.values()],
            "items": CATALOG, "feats": {k: v["d"] for k, v in FEATS.items()}, "langs": LANGUAGES,
            "classes": {k: {"n": c["n"], "hd": c["hd"], "subs": {sk: s["n"] for sk, s in c["subs"].items()}, "sub_lvl": c["sub_lvl"]} for k, c in CLASSES.items()},
            "races": {k: {"n": r["n"], "subs": {sk: s["n"] for sk, s in r.get("subs", {}).items()}} for k, r in RACES.items()},
            "backgrounds": {k: b["n"] for k, b in BACKGROUNDS.items()},
            "pregens": [p["title"] for p in PREGENS],
        })

    # ---------- персонажи ----------
    async def create_char(req):
        gid = gid_of(req)
        d = await body(req)
        if "pregen" in d:
            p = {k: v for k, v in PREGENS[int(d["pregen"])].items() if k != "title"}
            if d.get("name"):
                p["name"] = str(d["name"])[:40]
            data = engine.new_character(p)
        elif "build" in d:
            data = engine.new_character(d["build"])
        else:
            data = d
        ch = game.create_char(gid, data)
        return J({"id": ch["id"], "link": game.link(ch["token"])})

    async def save_char(req):
        d = await body(req)
        rev = d.pop("_rev", None)
        ch = await game.update_char(cid_of(req), d, rev)
        return J({"char": ch})

    async def char_op(req):
        """Все механические действия мастера над персонажем — через движок."""
        cid = cid_of(req)
        d = await body(req)
        op = d.get("op")
        reason = str(d.get("reason", "")).strip()[:120]
        n = int(d.get("n", 0) or 0)
        ops = {
            "dmg": (lambda c: engine.damage(c, n, str(d.get("type", ""))), f"урон {n}"),
            "heal": (lambda c: engine.heal(c, n), f"лечение {n}"),
            "temp": (lambda c: engine.set_temp(c, n), f"временные хиты {n}"),
            "rest_short": (lambda c: engine.short_rest(c, d.get("spend", [])), "короткий отдых"),
            "rest_long": (engine.long_rest, "долгий отдых"),
            "conc_drop": (engine.drop_conc, "концентрация прервана"),
            "res": (lambda c: engine.use_res(c, str(d["key"]), n), "ресурс"),
            "slot": (lambda c: engine.set_slot(c, str(d["slot"]), n), "ячейки"),
            "level_grant": (lambda c: c.update(lvl_pending=int(c.get("lvl_pending", 0)) + 1) or ["мастер дал новый уровень"], "уровень выдан"),
        }
        if op == "cond":
            name = d["name"]
            if name not in engine.CONDITIONS:
                raise ValueError("нет такого состояния")

            def fn(c):
                lst = c.setdefault("conds", [])
                (lst.remove(name) if name in lst else lst.append(name))
                return [f"{'наложено' if name in lst else 'снято'}: {name}"]
            ops["cond"] = (fn, f"состояние {name}")
        elif op == "exh":
            ops["exh"] = (lambda c: c.update(exh=max(0, min(6, n))) or [f"истощение {n}"], "состояние: истощение")
        elif op == "effect":
            ops["effect"] = (lambda c: [f"эффект «{engine.add_effect(c, str(d['name'])[:40], d.get('src', 'Мастер'), int(d['rounds']) if d.get('rounds') else None)['n']}»"],
                             "эффект")
        elif op == "rm_effect":
            ops["rm_effect"] = (lambda c: c.update(effects=[e for e in c.get("effects", []) if e["id"] != d["id"]]) or ["эффект снят"], "эффект снят")
        elif op == "adj":
            field = str(d["field"])
            if field not in engine.ADJ_FIELDS:
                raise ValueError("нельзя корректировать это поле")
            if not reason:
                raise ValueError("укажи причину корректировки")
            ops["adj"] = (lambda c: c.setdefault("adj", []).append({"id": engine.uid(), "field": field, "val": n, "reason": reason})
                          or [f"{engine.ADJ_FIELDS[field]} {n:+d}: {reason}"], "корректировка мастера")
        elif op == "rm_adj":
            ops["rm_adj"] = (lambda c: c.update(adj=[a for a in c.get("adj", []) if a["id"] != d["id"]]) or ["корректировка снята"], "корректировка снята")
        elif op == "level_up":
            ops["level_up"] = (lambda c: engine.level_up(c, str(d["cls"]), d.get("hp", "avg"), d.get("choices", {})), "повышение уровня")
        elif op == "item_add":
            it = make_item(str(d["name"])[:60], max(1, n or 1))
            ops["item_add"] = (lambda c: c.setdefault("items", []).append(it) or [f"предмет: {it['n']}"], "предмет")
        elif op == "item_eq":
            i = int(d["i"])

            def eq(c):
                it = c["items"][i]
                it["eq"] = not it.get("eq")
                if it["eq"] and it.get("kind") in ("armor", "shield"):
                    for o in c["items"]:
                        if o is not it and o.get("kind") == it["kind"]:
                            o["eq"] = False
                return [f"{it['n']}: {'надето' if it['eq'] else 'снято'}"]
            ops["item_eq"] = (eq, "экипировка")
        elif op == "item_rm":
            ops["item_rm"] = (lambda c: [f"убрано: {c['items'].pop(int(d['i']))['n']}"], "инвентарь")
        if op not in ops:
            raise ValueError("неизвестная операция")
        fn, label = ops[op]
        data, out = game.mutate(cid, fn, "Мастер", label + (f" ({reason})" if reason and op != "adj" else ""), d.get("_rev"))
        if op == "level_grant":
            c = db.character(cid)
            await game.notify(c, "📈 <b>Мастер дал новый уровень!</b> Нажми /levelup, чтобы выбрать, что получит герой.")
        return J({"char": data, "log": out})

    async def undo(req):
        return J({"undone": game.undo(cid_of(req), "Мастер")})

    async def history(req):
        return J({"history": db.history(cid_of(req))})

    async def hp_delta(req):
        cid = cid_of(req)
        d = await body(req)
        delta = int(d.get("delta", 0))
        fn = (lambda c: engine.damage(c, -delta)) if delta < 0 else (lambda c: engine.heal(c, delta))
        data, out = game.mutate(cid, fn, "Мастер", f"{'урон' if delta < 0 else 'лечение'} {abs(delta)}")
        return J({"char": data, "log": out})

    async def delete_char(req):
        c = db.character(cid_of(req))
        db.delete_character(c["id"])
        game.emit_chars(c["game_id"])
        return J({"ok": True})

    async def unbind_char(req):
        c = db.character(cid_of(req))
        db.unbind(c["id"])
        game.emit_chars(c["game_id"])
        return J({"ok": True})

    async def send_char(req):
        c = db.character(cid_of(req))
        if not c or not c["tg_id"]:
            return J({"ok": False})
        await game.ui.send_info(c)
        await game.ui.send_sheet(c)
        return J({"ok": True})

    def all_bound(gid) -> list[int]:
        return [c["id"] for c in db.characters(gid) if c["tg_id"]]

    # ---------- броски и запросы ----------
    async def gm_roll(req):
        gid = gid_of(req)
        d = await body(req)
        return J(game.gm_roll(gid, str(d.get("formula", "")), str(d.get("title", ""))[:60], int(d.get("adv", 0))))

    async def ask(req):
        gid = gid_of(req)
        d = await body(req)
        ids = d.get("chars") or all_bound(gid)
        kind = str(d.get("kind", "c:dex"))
        k, _, v = kind.partition(":")
        if k == "f":
            dice.parse(v)
        elif k not in ("c", "s", "k", "i"):
            raise ValueError("bad kind")
        vis = d.get("vis") if d.get("vis") in ("player", "gm", "all") else "player"
        sent = await game.ask_roll(gid, [int(i) for i in ids], kind, str(d.get("title", ""))[:60], vis)
        return J({"sent": sent})

    async def cancel_request(req):
        return J({"ok": await game.cancel_request(int(req.match_info["rid"]))})

    async def message(req):
        gid = gid_of(req)
        d = await body(req)
        ids = d.get("chars") or all_bound(gid)
        text = str(d.get("text", "")).strip()[:3000]
        if not text:
            raise ValueError("пустое сообщение")
        return J({"sent": await game.message_players(gid, [int(i) for i in ids], text)})

    async def clear(req):
        gid = gid_of(req)
        db.clear_rolls(gid)
        game.emit({"type": "cleared"}, gid)
        return J({"ok": True})

    async def cancel_draft(req):
        game.wiz.drop(cid_of(req))
        return J({"ok": True})

    # ---------- бой ----------
    async def combat(req):
        gid = gid_of(req)
        d = await body(req)
        op = d.get("op")
        if op == "start":
            cb = await game.combat_start(gid, bool(d.get("ask", True)))
        elif op == "next":
            cb = await game.combat_next(gid)
        elif op == "end":
            await game.combat_end(gid)
            cb = game.combat(gid)
        elif op == "add_pc":
            cb = game.combat_add_pc(gid, int(d["cid"]))
        elif op == "add_npc":
            cb = game.combat_add_npc(gid, str(d.get("name", ""))[:40], d.get("template"), int(d.get("count", 1)),
                                     bool(d.get("hidden")), int(d["hp"]) if d.get("hp") else None, int(d["ac"]) if d.get("ac") else None,
                                     int(d.get("ib", 0) or 0))
        elif op == "init":
            game.combat_set_init(gid, d["eid"], int(d["value"]))
            cb = game.combat(gid)
        elif op == "npc":
            fields = {k: d[k] for k in ("delta", "name", "ac", "hp", "mhp", "temp", "init", "hidden", "note", "conds", "effect", "rounds", "rm_effect") if k in d}
            cb = game.npc_update(gid, d["eid"], **fields)
        elif op == "remove":
            game.combat_remove(gid, d["eid"])
            cb = game.combat(gid)
        elif op == "npc_roll":
            return J(game.npc_roll(gid, d["eid"], int(d["i"])))
        elif op == "ask_init":
            cb = game.combat(gid)
            await game.ask_roll(gid, [x["cid"] for x in cb["list"] if x["kind"] == "pc"], "i:0", "Инициатива")
        else:
            raise ValueError("неизвестная операция боя")
        return J({"combat": cb})

    async def resolve(req):
        gid = gid_of(req)
        d = await body(req)
        return J({"result": await game.resolve(gid, req.match_info["pid"], bool(d.get("ok")))})

    # ---------- журнал ----------
    async def journal(req):
        gid = gid_of(req)
        q = req.query
        return J({"entries": db.journal(gid, q.get("q", ""), q.get("cat", ""), q.get("deleted") == "1",
                                        q.get("vis") or None, int(q["char"]) if q.get("char") else None,
                                        int(q["scene"]) if q.get("scene") else None)})

    def entry_fields(d):
        out = {k: str(d[k])[:20000] if k == "body" else str(d[k])[:200] for k in ("title", "body", "quest") if k in d}
        if d.get("cat") in ("note", "plot", "session", "quest", "npc", "place", "loot", "mech"):
            out["cat"] = d["cat"]
        if d.get("vis") in ("gm", "all"):
            out["vis"] = d["vis"]
        for k in ("scene_id", "char_id", "session"):
            if k in d:
                out[k] = int(d[k]) if d[k] not in (None, "") else None
        return out

    async def add_entry(req):
        gid = gid_of(req)
        d = await body(req)
        f = entry_fields(d)
        f.setdefault("session", game.game_state(gid).get("session") or None)
        e = db.add_entry(gid, author="Мастер", **f)
        game.emit({"type": "journal"}, gid)
        return J(e)

    async def update_entry(req):
        eid = int(req.match_info["eid"])
        d = await body(req)
        old = db.entry(eid)
        if not old:
            raise web.HTTPNotFound()
        f = entry_fields(d)
        if "deleted" in d:
            f["deleted"] = 1 if d["deleted"] else 0
        e = db.update_entry(eid, "Мастер", **f)
        game.emit({"type": "journal"}, e["game_id"])
        if f.get("vis") == "all" and old["vis"] != "all":  # открыли игрокам — уведомим
            for c in db.characters(e["game_id"]):
                await game.notify(c, f"📖 Мастер открыл запись в журнале: <b>{e['title'][:100]}</b>\n/journal")
        return J(e)

    async def entry_revs(req):
        return J({"revs": db.entry_revs(int(req.match_info["eid"]))})

    async def new_session(req):
        gid = gid_of(req)
        st = game.game_state(gid)
        st["session"] = int(st.get("session", 0)) + 1
        game.save_state(gid, st)
        e = db.add_entry(gid, cat="session", title=f"Сессия {st['session']}", body="", vis="gm", author="Мастер", session=st["session"])
        game.emit({"type": "journal"}, gid)
        return J({"session": st["session"], "entry": e})

    # ---------- сцены и карта ----------
    async def scenes(req):
        gid = gid_of(req)
        return J({"scenes": db.scenes(gid), "current": game.game_state(gid).get("scene")})

    async def save_scene(req):
        gid = gid_of(req)
        d = await body(req)
        from .mapview import default_scene
        base = default_scene()
        sid = d.get("id")
        if sid:
            old = db.scene(int(sid))
            if not old or old["game_id"] != gid:
                raise web.HTTPNotFound()
            base.update(old)
        for k in ("grid", "ox", "oy", "cols", "rows", "ft", "w", "h", "img"):
            if k in d:
                base[k] = int(d[k]) if d[k] is not None else None
        for k in ("fog_on", "show"):
            if k in d:
                base[k] = bool(d[k])
        if isinstance(d.get("fog"), list):
            base["fog"] = [str(x) for x in d["fog"]][:20000]
        if isinstance(d.get("tokens"), list):
            base["tokens"] = [{k: t.get(k) for k in ("id", "n", "x", "y", "kind", "cid", "eid", "color", "size", "hidden")}
                              for t in d["tokens"] if isinstance(t, dict)][:300]
        if d.get("party"):
            game.map.add_party(gid, base)
        name = str(d.get("name") or base.get("name") or "Сцена")[:60]
        sid = db.save_scene(gid, int(sid) if sid else None, name, base)
        sc = {**db.scene(sid)}
        if game.game_state(gid).get("scene") == sid or d.get("current"):
            if d.get("current"):
                game.map.set_current(gid, sid)
            game.emit({"type": "scene", "scene": sc}, gid)
            game.map.schedule_push(gid)
        return J(sc)

    async def scene_current(req):
        gid = gid_of(req)
        sid = int(req.match_info["sid"])
        game.map.set_current(gid, sid or None)
        return J({"ok": True})

    async def delete_scene(req):
        gid = gid_of(req)
        sid = int(req.match_info["sid"])
        db.delete_scene(sid)
        if game.game_state(gid).get("scene") == sid:
            game.map.set_current(gid, None)
        return J({"ok": True})

    async def upload(req):
        gid = gid_of(req)
        reader = await req.multipart()
        part = await reader.next()
        data = await part.read(decode=False)
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(data))
            img.load()
            w, h = img.size
            if w > 4000 or h > 4000:
                img.thumbnail((4000, 4000))
                w, h = img.size
            out = io.BytesIO()
            img.convert("RGB").save(out, "JPEG", quality=85)
            data = out.getvalue()
        except Exception:
            raise ValueError("Это не картинка")
        fid = db.add_file(gid, "image/jpeg", data)
        return J({"id": fid, "w": w, "h": h})

    async def file(req):
        f = db.file(int(req.match_info["fid"]))
        if not f:
            raise web.HTTPNotFound()
        return web.Response(body=f["data"], content_type=f["mime"], headers={"Cache-Control": "max-age=86400"})

    async def map_preview(req):
        gid = gid_of(req)
        sc = game.map.scene(gid)
        if not sc:
            raise web.HTTPNotFound()
        sc["game_id"] = gid
        img = await asyncio.get_running_loop().run_in_executor(None, game.map.render, sc, None, False)
        return web.Response(body=img, content_type="image/jpeg")

    # ---------- живое соединение ----------
    async def ws(req):
        sock = web.WebSocketResponse(heartbeat=25)
        await sock.prepare(req)
        q: asyncio.Queue = asyncio.Queue()
        try:
            gid = int(req.query.get("g", "0")) or None
        except ValueError:
            gid = None
        game.listeners[q] = gid

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
            game.listeners.pop(q, None)
        return sock

    r.add_get("/", index)
    r.add_get("/login", login_page)
    r.add_post("/login", login)
    r.add_get("/logout", logout)
    r.add_get("/demo", demo)
    r.add_get("/demo/widget/{name}", widget_src)
    r.add_get("/demo/data", demo_data)
    r.add_static("/static", STATIC)
    r.add_get("/api/catalog", catalog)
    r.add_get("/api/games", games)
    r.add_post("/api/games", create_game)
    r.add_post("/api/g/{gid}", update_game)
    r.add_get("/api/g/{gid}/state", state)
    r.add_post("/api/g/{gid}/lobby/new", new_lobby)
    r.add_post("/api/g/{gid}/char", create_char)
    r.add_post("/api/g/{gid}/roll", gm_roll)
    r.add_post("/api/g/{gid}/ask", ask)
    r.add_post("/api/g/{gid}/message", message)
    r.add_post("/api/g/{gid}/clear", clear)
    r.add_post("/api/g/{gid}/combat", combat)
    r.add_post("/api/g/{gid}/pending/{pid}", resolve)
    r.add_get("/api/g/{gid}/journal", journal)
    r.add_post("/api/g/{gid}/journal", add_entry)
    r.add_post("/api/g/{gid}/session", new_session)
    r.add_get("/api/g/{gid}/scenes", scenes)
    r.add_post("/api/g/{gid}/scene", save_scene)
    r.add_post("/api/g/{gid}/scene/{sid}/current", scene_current)
    r.add_delete("/api/g/{gid}/scene/{sid}", delete_scene)
    r.add_post("/api/g/{gid}/upload", upload)
    r.add_get("/api/g/{gid}/map.jpg", map_preview)
    r.add_get("/api/file/{fid}", file)
    r.add_post("/api/journal/{eid}", update_entry)
    r.add_get("/api/journal/{eid}/revs", entry_revs)
    r.add_post("/api/request/{rid}/cancel", cancel_request)
    r.add_post("/api/char/{id}", save_char)
    r.add_post("/api/char/{id}/op", char_op)
    r.add_post("/api/char/{id}/hp", hp_delta)
    r.add_post("/api/char/{id}/undo", undo)
    r.add_get("/api/char/{id}/history", history)
    r.add_post("/api/char/{id}/unbind", unbind_char)
    r.add_post("/api/char/{id}/send", send_char)
    r.add_delete("/api/char/{id}", delete_char)
    r.add_delete("/api/draft/{id}", cancel_draft)
    r.add_get("/ws", ws)
    return app
