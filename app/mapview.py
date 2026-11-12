"""Карта местности: сцена с картинкой, сеткой, фишками и туманом войны.

Мастер ведёт карту на сайте (canvas). Игрокам бот присылает картинку карты (рисует сервер, туман скрывает
клетки и спрятанные фишки) и кнопки ↑↓←→ для своей фишки. В бою двигаться можно только в свой ход и не дальше
скорости (Рывок удваивает). Обновления карты собираются и уходят раз в ~1.5 с."""
import asyncio
import io
import logging

from . import engine
from .tg import TelegramError

log = logging.getLogger("map")
DIRS = {"n": (0, -1), "s": (0, 1), "w": (-1, 0), "e": (1, 0), "nw": (-1, -1), "ne": (1, -1), "sw": (-1, 1), "se": (1, 1)}
COLORS = ["#e3b45c", "#5cbf6a", "#6fa8dc", "#c77dff", "#ff8fab", "#4dd4c6", "#f4a261", "#e0574a"]


def default_scene() -> dict:
    return {"img": None, "w": 1200, "h": 800, "grid": 50, "ox": 0, "oy": 0, "cols": 16, "rows": 10,
            "fog_on": False, "fog": [], "tokens": [], "show": False, "ft": 5}


class MapView:
    def __init__(self, game):
        self.game = game
        self.db = game.db
        self._push: dict[int, asyncio.Task] = {}

    # ---------- сцена ----------
    def scene(self, gid: int) -> dict | None:
        sid = self.game.game_state(gid).get("scene")
        s = self.db.scene(sid) if sid else None
        return {**default_scene(), **s} if s else None

    def save(self, gid: int, sc: dict) -> None:
        self.db.save_scene(gid, sc["id"], sc["name"], sc)
        self.game.emit({"type": "scene", "scene": sc}, gid)
        self.schedule_push(gid)

    def set_current(self, gid: int, sid: int | None) -> None:
        st = self.game.game_state(gid)
        st["scene"] = sid
        self.game.save_state(gid, st)
        self.game.emit({"type": "scene", "scene": self.scene(gid)}, gid)
        self.schedule_push(gid)

    def token_of(self, sc: dict, cid: int) -> dict | None:
        return next((t for t in sc["tokens"] if t.get("cid") == cid), None)

    def add_party(self, gid: int, sc: dict) -> None:
        """Поставить на карту всех персонажей партии, у кого ещё нет фишки."""
        x = 1
        for i, c in enumerate(self.db.characters(gid)):
            if self.token_of(sc, c["id"]):
                continue
            sc["tokens"].append({"id": engine.uid(), "n": c["data"].get("name") or "Игрок", "x": x, "y": 1, "kind": "pc",
                                 "cid": c["id"], "color": COLORS[i % len(COLORS)], "size": 1, "hidden": False})
            x += 1

    # ---------- рисование ----------
    def render(self, sc: dict, viewer_cid: int | None = None, gm: bool = False) -> bytes:
        from PIL import Image, ImageDraw, ImageFont
        g = sc["grid"]
        if sc.get("img"):
            f = self.db.file(sc["img"])
            img = Image.open(io.BytesIO(f["data"])).convert("RGB")
        else:
            img = Image.new("RGB", (sc["cols"] * g + sc["ox"], sc["rows"] * g + sc["oy"]), (38, 32, 27))
        W, H = img.size
        draw = ImageDraw.Draw(img, "RGBA")
        for x in range(sc["ox"] % g, W, g):
            draw.line([(x, 0), (x, H)], fill=(0, 0, 0, 60), width=1)
        for y in range(sc["oy"] % g, H, g):
            draw.line([(0, y), (W, y)], fill=(0, 0, 0, 60), width=1)
        hidden = set(sc.get("fog", [])) if sc.get("fog_on") else set()
        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", max(10, int(g * 0.34)))
        except OSError:
            try:
                font = ImageFont.load_default(size=max(10, int(g * 0.34)))
            except TypeError:
                font = ImageFont.load_default()
        cb = self.game.combat(sc["game_id"]) if sc.get("game_id") else {}
        cur = cb["list"][cb["turn"]] if cb.get("on") and 0 <= cb.get("turn", -1) < len(cb.get("list", [])) else None
        for t in sc["tokens"]:
            if not gm and (t.get("hidden") or f"{t['x']}:{t['y']}" in hidden):
                continue
            s = t.get("size", 1)
            x0, y0 = sc["ox"] + t["x"] * g, sc["oy"] + t["y"] * g
            box = [x0 + 3, y0 + 3, x0 + s * g - 3, y0 + s * g - 3]
            col = t.get("color", "#e3b45c")
            is_turn = cur and ((cur.get("cid") and cur.get("cid") == t.get("cid")) or cur.get("id") == t.get("eid"))
            draw.ellipse(box, fill=col, outline=(255, 255, 255) if t.get("cid") == viewer_cid or is_turn else (20, 15, 10), width=4 if is_turn else 2)
            label = "".join(w[0] for w in t["n"].split()[:2]).upper() or "?"
            draw.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), label, fill=(20, 15, 10), font=font, anchor="mm")
        if hidden:
            for key in hidden:
                cx, cy = map(int, key.split(":"))
                x0, y0 = sc["ox"] + cx * g, sc["oy"] + cy * g
                draw.rectangle([x0, y0, x0 + g, y0 + g], fill=(8, 6, 5, 140 if gm else 255))
        if W > 1600:
            img = img.resize((1600, int(H * 1600 / W)))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=82)
        return out.getvalue()

    # ---------- отправка игрокам ----------
    def schedule_push(self, gid: int) -> None:
        t = self._push.get(gid)
        if t and not t.done():
            t.cancel()
        try:
            self._push[gid] = asyncio.get_running_loop().create_task(self._push_later(gid))
        except RuntimeError:
            pass

    async def _push_later(self, gid: int) -> None:
        await asyncio.sleep(1.5)
        await self.push(gid)

    def keyboard(self, has_token: bool) -> dict:
        rows = []
        if has_token:
            rows = [[{"text": "↖", "callback_data": "m:nw"}, {"text": "↑", "callback_data": "m:n"}, {"text": "↗", "callback_data": "m:ne"}],
                    [{"text": "←", "callback_data": "m:w"}, {"text": "🏃 Рывок", "callback_data": "m:dash"}, {"text": "→", "callback_data": "m:e"}],
                    [{"text": "↙", "callback_data": "m:sw"}, {"text": "↓", "callback_data": "m:s"}, {"text": "↘", "callback_data": "m:se"}]]
        rows.append([{"text": "🔄 Обновить", "callback_data": "m:refresh"}])
        return {"inline_keyboard": rows}

    def caption(self, sc: dict, gid: int, cid: int) -> str:
        cb = self.game.combat(gid)
        cap = f"🗺 <b>{sc['name']}</b>"
        if cb.get("on") and cb["list"]:
            cur = cb["list"][cb["turn"]] if 0 <= cb["turn"] < len(cb["list"]) else None
            cap += f"\n⚔️ Раунд {cb['round']}" + (f", ходит: <b>{cur['name']}</b>" if cur and not cur.get("hidden") else "")
            moved = cb.get("moved", {}).get(str(cid))
            if moved:
                cap += f"\n👣 пройдено {moved['ft']} из {moved['max']} фт"
        return cap

    async def push(self, gid: int, only_cid: int | None = None) -> None:
        sc = self.scene(gid)
        if not sc or not sc.get("show"):
            return
        sc["game_id"] = gid
        for c in self.db.characters(gid):
            if not c["tg_id"] or (only_cid and c["id"] != only_cid):
                continue
            try:
                await self.send(c, sc, gid)
            except Exception:
                log.exception("map push failed")

    async def send(self, c: dict, sc: dict, gid: int, force_new: bool = False) -> None:
        photo = await asyncio.get_running_loop().run_in_executor(None, self.render, sc, c["id"], False)
        markup = self.keyboard(bool(self.token_of(sc, c["id"])))
        cap = self.caption(sc, gid, c["id"])
        bot = self.game.bot
        msgs = c["map_msgs"]
        if msgs and not force_new:
            try:
                await bot.edit_photo(c["tg_id"], msgs[-1], photo, cap, markup)
                return
            except TelegramError as e:
                if "not modified" in e.description:
                    return
        m = await bot.send_photo(c["tg_id"], photo, cap, markup)
        self.db.add_msg(c["id"], m["message_id"], "map_msgs", keep=1)

    # ---------- движение игрока ----------
    async def on_button(self, q: dict) -> None:
        uid = q["from"]["id"]
        act = q.get("data", "")[2:]
        ch = self.db.character_of(uid)
        answer = self.game.wiz.answer
        if not ch:
            return await answer(q, "Нет персонажа")
        gid = ch["game_id"]
        sc = self.scene(gid)
        if not sc or not sc.get("show"):
            return await answer(q, "Мастер скрыл карту")
        sc["game_id"] = gid
        if act == "refresh":
            await answer(q)
            return await self.send(ch, sc, gid)
        tok = self.token_of(sc, ch["id"])
        if not tok:
            return await answer(q, "Твоей фишки нет на карте")
        cb = self.game.combat(gid)
        D = engine.derive(ch["data"])
        if cb.get("on"):
            cur = cb["list"][cb["turn"]] if 0 <= cb["turn"] < len(cb["list"]) else None
            if not cur or cur.get("cid") != ch["id"]:
                return await answer(q, "Сейчас не твой ход")
            mv = cb.setdefault("moved", {}).setdefault(str(ch["id"]), {"ft": 0, "max": D["spd"]["val"]})
            if act == "dash":
                mv["max"] += D["spd"]["val"]
                self.game.save_combat(gid, cb)
                await answer(q, f"🏃 Рывок: можно пройти {mv['max']} фт")
                return await self.send(ch, sc, gid)
            if mv["ft"] + sc["ft"] > mv["max"]:
                return await answer(q, f"Скорость исчерпана ({mv['max']} фт). Есть Рывок 🏃")
        elif act == "dash":
            return await answer(q, "Рывок нужен только в бою")
        dx, dy = DIRS[act]
        nx, ny = tok["x"] + dx, tok["y"] + dy
        cols = sc["cols"] if not sc.get("img") else (sc["w"] - sc["ox"]) // sc["grid"]
        rows = sc["rows"] if not sc.get("img") else (sc["h"] - sc["oy"]) // sc["grid"]
        if not (0 <= nx < cols and 0 <= ny < rows):
            return await answer(q, "Край карты")
        tok["x"], tok["y"] = nx, ny
        if cb.get("on"):
            cb["moved"][str(ch["id"])]["ft"] += sc["ft"]
            self.game.save_combat(gid, cb)
        self.db.save_scene(gid, sc["id"], sc["name"], sc)
        self.game.emit({"type": "scene", "scene": sc}, gid)
        await answer(q)
        await self.send(ch, sc, gid)
        self.schedule_push(gid)
