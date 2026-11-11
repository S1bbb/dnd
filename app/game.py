"""Игровая логика: обработка апдейтов бота, броски, события для сайта мастера."""
import asyncio
import html
import json
import logging
import re
import secrets

from . import dice, widgets
from .db import DB
from .rules import ABILITIES, SKILLS, check_formula, DEFAULT_CHAR
from .tg import Bot, TelegramError

log = logging.getLogger("game")

HELP = (
    "<b>Как пользоваться</b>\n"
    "/dice — кости (виджет)\n"
    "/sheet — лист персонажа (виджет)\n"
    "/roll 2d6+3 — бросок текстом (или просто напиши <code>d20+5</code>)\n"
    "\n<b>Персонаж</b>\n"
    "/name Арагорн — имя\n"
    "/class Следопыт 5 — класс и уровень\n"
    "/stats 16 14 13 10 12 8 — СИЛ ЛОВ ТЕЛ ИНТ МДР ХАР\n"
    "/hp 38 44 — хиты (текущие и максимум)\n"
    "/ac 16 — класс доспеха\n"
    "\nОстальное (владения навыками и спасбросками) мастер настроит на сайте."
)

FORMULA_RE = re.compile(r"^\s*[\dкдd%+\-\s]*[dкд]\d*[\dкдdkhl%+\-\s]*$", re.I)


def compact_char(ch: dict) -> dict:
    """Персонаж в формате chat_data виджета листа (≤1024 байт)."""
    return {
        "n": ch.get("name", "")[:40], "c": ch.get("cls", "")[:30], "l": ch.get("lvl", 1),
        "hp": ch.get("hp", 10), "mhp": ch.get("mhp", 10), "ac": ch.get("ac", 10),
        "st": [ch.get("st", {}).get(a, 10) for a in ABILITIES],
        "sv": [ABILITIES.index(a) for a in ch.get("sv", []) if a in ABILITIES],
        "sk": ch.get("sk", []),
    }


def clean_char(raw: dict, old: dict) -> dict:
    """Проверка данных персонажа, пришедших с сайта или из команд."""
    ch = {**DEFAULT_CHAR, **old}

    def num(v, lo, hi, d):
        try:
            return max(lo, min(hi, int(v)))
        except (TypeError, ValueError):
            return d

    if "name" in raw:
        ch["name"] = str(raw["name"]).strip()[:40]
    if "cls" in raw:
        ch["cls"] = str(raw["cls"]).strip()[:30]
    for k, lo, hi in (("lvl", 1, 20), ("hp", -99, 999), ("mhp", 1, 999), ("ac", 0, 40)):
        if k in raw:
            ch[k] = num(raw[k], lo, hi, ch[k])
    if isinstance(raw.get("st"), dict):
        ch["st"] = {a: num(raw["st"].get(a, ch["st"].get(a, 10)), 1, 30, 10) for a in ABILITIES}
    if isinstance(raw.get("sv"), list):
        ch["sv"] = [a for a in ABILITIES if a in raw["sv"]]
    if isinstance(raw.get("sk"), list):
        ch["sk"] = sorted({int(i) for i in raw["sk"] if str(i).isdigit() and int(i) < len(SKILLS)})
    return ch


class Game:
    def __init__(self, db: DB, bot: Bot):
        self.db = db
        self.bot = bot
        self.listeners: set[asyncio.Queue] = set()
        self.me: dict = {}
        if not db.get("join_code"):
            db.set("join_code", secrets.token_urlsafe(6).replace("-", "x").replace("_", "y"))

    # ---------- события для сайта мастера ----------
    def emit(self, event: dict) -> None:
        for q in list(self.listeners):
            q.put_nowait(event)

    def emit_players(self) -> None:
        self.emit({"type": "players", "players": self.public_players()})

    def public_players(self) -> list[dict]:
        return [{k: p[k] for k in ("id", "username", "first_name", "char")} for p in self.db.players()]

    @property
    def join_code(self) -> str:
        return self.db.get("join_code")

    @property
    def invite_link(self) -> str | None:
        u = self.me.get("username")
        return f"https://t.me/{u}?start={self.join_code}" if u else None

    def state(self) -> dict:
        return {
            "players": self.public_players(),
            "rolls": self.db.rolls(200),
            "invite": self.invite_link,
            "bot": self.me.get("username"),
            "group": self.db.get("group"),
            "skills": [s[0] for s in SKILLS],
        }

    # ---------- броски ----------
    def who(self, p: dict) -> str:
        return p["char"].get("name") or p["first_name"] or "Игрок"

    async def record(self, tg_id, who: str, title: str, r: dice.Roll, hidden: bool) -> dict:
        entry = self.db.add_roll(tg_id, who, title, r.formula, r.total,
                                 {**r.to_dict(), "txt": r.dice_text()}, hidden)
        self.emit({"type": "roll", "roll": entry})
        group = self.db.get("group")
        if group and not hidden:
            mark = " 💥" if r.crit else (" 💀" if r.fumble else "")
            t = f"{html.escape(title)}: " if title else ""
            try:
                await self.bot.send_message(
                    group["id"],
                    f"🎲 <b>{html.escape(who)}</b> — {t}<b>{r.total}</b>{mark}\n"
                    f"<i>{html.escape(r.formula)}: {html.escape(r.dice_text())}</i>",
                    disable_notification=True,
                )
            except TelegramError as e:
                log.warning("group post failed: %s", e)
        return entry

    async def gm_roll(self, formula: str, title: str = "", adv: int = 0, hidden: bool = True) -> dict:
        r = dice.roll(formula, adv)
        return await self.record(None, "Мастер", title, r, hidden)

    # ---------- рассылка виджетов ----------
    async def send_dice(self, chat_id: int, p: dict):
        await self.bot.send_widget(chat_id, widgets.load("dice"), chat_data={"n": self.who(p)})

    async def send_sheet(self, chat_id: int, p: dict):
        msg = await self.bot.send_widget(chat_id, widgets.load("sheet"), chat_data=compact_char(p["char"]))
        self.db.add_sheet_msg(p["id"], msg["message_id"])

    async def refresh_sheets(self, tg_id: int) -> None:
        p = self.db.player(tg_id)
        if not p:
            return
        data = compact_char(p["char"])
        for mid in p["sheet_msgs"]:
            try:
                await self.bot.edit_widget_data(tg_id, mid, data)
            except TelegramError as e:
                log.info("sheet %s not updated: %s", mid, e.description)

    async def update_char(self, tg_id: int, raw: dict) -> dict:
        p = self.db.player(tg_id)
        if not p:
            raise KeyError(tg_id)
        ch = clean_char(raw, p["char"])
        self.db.save_char(tg_id, ch)
        self.emit_players()
        asyncio.create_task(self.refresh_sheets(tg_id))
        return ch

    async def ask_roll(self, tg_ids: list[int], kind: str, title: str, hidden: bool) -> list[int]:
        """Мастер просит игроков бросить. kind: c:str | s:dex | k:3 | i:0 | f:2d6+1"""
        sent = []
        for tg_id in tg_ids:
            p = self.db.player(tg_id)
            if not p:
                continue
            if not title:
                title = self.kind_title(p["char"], kind)
            rid = self.db.add_request(tg_id, title, kind, hidden)
            try:
                msg = await self.bot.send_widget(
                    tg_id, widgets.load("request"), chat_data={"i": rid, "t": title, "h": int(hidden)})
                self.db.update_request(rid, msg_id=msg["message_id"])
                sent.append(tg_id)
            except TelegramError as e:
                log.warning("request to %s failed: %s", tg_id, e)
        self.emit({"type": "asked", "players": sent, "title": title})
        return sent

    @staticmethod
    def kind_title(ch: dict, kind: str) -> str:
        k, _, v = kind.partition(":")
        if k == "f":
            return f"Бросок {v}"
        return check_formula(ch, k, v)[0]

    async def message_players(self, tg_ids: list[int], text: str) -> int:
        n = 0
        for tg_id in tg_ids:
            try:
                await self.bot.send_message(tg_id, "🧙 <b>Мастер:</b> " + html.escape(text))
                n += 1
            except TelegramError as e:
                log.warning("message to %s failed: %s", tg_id, e)
        return n

    # ---------- апдейты Telegram ----------
    async def handle(self, upd: dict) -> None:
        try:
            if "message" in upd:
                await self.on_message(upd["message"])
            elif "widget_query" in upd:
                await self.on_widget_query(upd["widget_query"])
            elif "shared_widget_query" in upd:
                await self.bot.answer_widget_query(upd["shared_widget_query"]["id"], {"e": "Открой чат с ботом"})
        except Exception:
            log.exception("update failed: %s", json.dumps(upd)[:500])

    async def on_message(self, m: dict) -> None:
        text = (m.get("text") or "").strip()
        chat = m["chat"]
        user = m.get("from") or {}
        if not text:
            return
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        arg = arg.strip()

        if chat["type"] != "private":
            if cmd == "/bindgroup":
                if arg == self.join_code:
                    self.db.set("group", {"id": chat["id"], "title": chat.get("title", "")})
                    self.emit({"type": "group", "group": self.db.get("group")})
                    await self.bot.send_message(chat["id"], "✅ Буду писать сюда все открытые броски.")
                else:
                    await self.bot.send_message(chat["id"], "Нужен код кампании: /bindgroup КОД (есть на сайте мастера)")
            return

        p = self.db.player(user["id"])
        if cmd == "/start":
            if not p and arg != self.join_code:
                await self.bot.send_message(chat["id"], "🛡 Привет! Чтобы присоединиться к игре, попроси у мастера ссылку-приглашение.")
                return
            p = self.db.add_player(user["id"], user.get("username"), user.get("first_name", ""))
            self.emit_players()
            await self.bot.send_message(
                chat["id"], f"⚔️ Добро пожаловать в игру, <b>{html.escape(self.who(p))}</b>!\n\n" + HELP)
            await self.send_sheet(chat["id"], p)
            await self.send_dice(chat["id"], p)
            return
        if not p:
            await self.bot.send_message(chat["id"], "Сначала присоединись по ссылке от мастера.")
            return

        if cmd in ("/help", "/помощь"):
            await self.bot.send_message(chat["id"], HELP)
        elif cmd in ("/dice", "/кости"):
            await self.send_dice(chat["id"], p)
        elif cmd in ("/sheet", "/лист"):
            await self.send_sheet(chat["id"], p)
        elif cmd == "/name" and arg:
            await self.update_char(p["id"], {"name": arg})
            await self.bot.send_message(chat["id"], f"Теперь ты — <b>{html.escape(arg[:40])}</b>")
        elif cmd == "/class" and arg:
            parts = arg.rsplit(" ", 1)
            raw = {"cls": parts[0]}
            if len(parts) == 2 and parts[1].isdigit():
                raw["lvl"] = parts[1]
            elif arg.isdigit():
                raw = {"lvl": arg}
            await self.update_char(p["id"], raw)
            await self.bot.send_message(chat["id"], "Записал ✍️")
        elif cmd == "/stats":
            nums = arg.split()
            if len(nums) != 6 or not all(n.isdigit() for n in nums):
                await self.bot.send_message(chat["id"], "Нужно 6 чисел: /stats 16 14 13 10 12 8")
                return
            await self.update_char(p["id"], {"st": dict(zip(ABILITIES, nums))})
            await self.bot.send_message(chat["id"], "Характеристики обновлены 💪")
        elif cmd == "/hp" and arg:
            nums = arg.split()
            raw = {"hp": nums[0]}
            if len(nums) > 1:
                raw["mhp"] = nums[1]
            ch = await self.update_char(p["id"], raw)
            await self.bot.send_message(chat["id"], f"❤️ Хиты: {ch['hp']} / {ch['mhp']}")
        elif cmd == "/ac" and arg:
            ch = await self.update_char(p["id"], {"ac": arg})
            await self.bot.send_message(chat["id"], f"🛡 КД: {ch['ac']}")
        elif cmd in ("/roll", "/r") or FORMULA_RE.match(text):
            formula = arg if cmd.startswith("/") else text
            await self.text_roll(chat["id"], p, formula or "1d20")
        else:
            await self.bot.send_message(chat["id"], "Не понял. /help — список команд.")

    async def text_roll(self, chat_id: int, p: dict, formula: str) -> None:
        try:
            r = dice.roll(formula)
        except dice.DiceError as e:
            await self.bot.send_message(chat_id, f"🤔 {html.escape(str(e))}")
            return
        await self.record(p["id"], self.who(p), "", r, False)
        mark = " 💥 КРИТ!" if r.crit else (" 💀 провал" if r.fumble else "")
        await self.bot.send_message(
            chat_id, f"🎲 <b>{r.total}</b>{mark}\n<i>{html.escape(r.formula)}: {html.escape(r.dice_text())}</i>")

    async def on_widget_query(self, q: dict) -> None:
        qid, data, user = q["id"], q.get("data", ""), q["from"]
        p = self.db.player(user["id"])
        if not p:
            await self.bot.answer_widget_query(qid, {"e": "Сначала присоединись к игре"})
            return
        try:
            answer = await self.widget_roll(p, data)
        except (dice.DiceError, ValueError, KeyError, IndexError) as e:
            answer = {"e": str(e) or "Ошибка"}
        await self.bot.answer_widget_query(qid, answer)

    async def widget_roll(self, p: dict, data: str) -> dict:
        kind, _, arg = data.partition(":")
        who = self.who(p)
        if kind.startswith("r") and len(kind) == 3:  # кости: r<n|a|d><v|h>:формула
            adv = {"n": 0, "a": 1, "d": -1}[kind[1]]
            hidden = kind[2] == "h"
            r = dice.roll(arg, adv)
            await self.record(p["id"], who, "Тайный бросок" if hidden else "", r, hidden)
            return r.to_dict()
        if kind in ("c", "s", "k", "i"):  # лист персонажа
            title, formula = check_formula(p["char"], kind, arg)
            r = dice.roll(formula)
            await self.record(p["id"], who, title, r, False)
            return r.to_dict()
        if kind == "q":  # запрос мастера
            req = self.db.request(int(arg))
            if not req or req["tg_id"] != p["id"]:
                raise ValueError("Этот запрос не тебе")
            if req["roll_id"]:
                raise ValueError("Уже брошено")
            k, _, v = req["kind"].partition(":")
            formula = v if k == "f" else check_formula(p["char"], k, v)[1]
            r = dice.roll(formula)
            entry = await self.record(p["id"], who, req["title"], r, bool(req["hidden"]))
            self.db.update_request(req["id"], roll_id=entry["id"])
            if req["msg_id"]:
                try:
                    await self.bot.edit_widget_data(p["id"], req["msg_id"], {
                        "i": req["id"], "t": req["title"], "h": req["hidden"], "x": r.to_dict()})
                except TelegramError as e:
                    log.info("request widget not updated: %s", e)
            return r.to_dict()
        raise ValueError("Неизвестный запрос")

    # ---------- long polling ----------
    async def poll(self) -> None:
        self.me = await self.bot.call("getMe")
        log.info("bot @%s started; invite: %s", self.me.get("username"), self.invite_link)
        await self.bot.call("setMyCommands", commands=[
            {"command": "dice", "description": "Кости"},
            {"command": "sheet", "description": "Лист персонажа"},
            {"command": "roll", "description": "Бросок текстом: /roll 2d6+3"},
            {"command": "help", "description": "Помощь"},
        ])
        offset = 0
        while True:
            try:
                updates = await self.bot.call(
                    "getUpdates", offset=offset, timeout=50,
                    allowed_updates=["message", "widget_query", "shared_widget_query"])
            except Exception as e:
                log.warning("getUpdates failed: %s", e)
                await asyncio.sleep(3)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                asyncio.create_task(self.handle(u))
