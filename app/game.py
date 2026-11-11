"""Игровая логика: обработка апдейтов бота, броски, события для сайта мастера."""
import asyncio
import html
import json
import logging
import re
import secrets

from . import dice, widgets
from .db import DB
from .rules import ABILITIES, ABILITY_SHORT, SKILLS, TEXT_FIELDS, check_formula, mod, prof_bonus, DEFAULT_CHAR
from .tg import Bot, TelegramError

log = logging.getLogger("game")

HELP = (
    "<b>Как пользоваться</b>\n"
    "/sheet — лист персонажа: проверки, спасброски, навыки, атаки\n"
    "/dice — кости\n"
    "/info — всё о персонаже: умения, заклинания, снаряжение\n"
    "/roll 2d6+3 — бросок текстом (или просто напиши <code>d20+5</code>)\n"
    "\n<b>Поправить персонажа самому</b>\n"
    "/name Арагорн · /class Следопыт 5 · /hp 38 · /ac 16\n"
    "/stats 16 14 13 10 12 8 — СИЛ ЛОВ ТЕЛ ИНТ МДР ХАР\n"
    "\nВсё остальное мастер заполняет на своём сайте, лист обновится сам."
)

FORMULA_RE = re.compile(r"^\s*[\dкдd%+\-\s]*[dкд]\d*[\dкдdkhl%+\-\s]*$", re.I)
TEXT_LIMIT = 2000


def compact_char(ch: dict) -> dict:
    """Персонаж в формате chat_data виджета листа: Telegram даёт не больше 1024 байт."""
    out = {
        "n": ch.get("name", "")[:40], "c": ch.get("cls", "")[:30], "r": ch.get("race", "")[:30],
        "l": ch.get("lvl", 1), "hp": ch.get("hp", 10), "mhp": ch.get("mhp", 10), "ac": ch.get("ac", 10),
        "sp": ch.get("spd", 30),
        "st": [ch.get("st", {}).get(a, 10) for a in ABILITIES],
        "sv": [ABILITIES.index(a) for a in ch.get("sv", []) if a in ABILITIES],
        "sk": ch.get("sk", []),
        "a": [[a["n"], a["hit"], a["dmg"]] for a in ch.get("atk", [])],
    }
    size = lambda: len(json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode())
    cut = 24
    while size() > 1024 and cut > 6:
        out["a"] = [[n[:cut], h, d] for n, h, d in out["a"]]
        cut -= 6
    while size() > 1024 and out["a"]:
        out["a"].pop()
    return out


def clean_char(raw: dict, old: dict) -> dict:
    """Проверка данных персонажа, пришедших с сайта или из команд."""
    ch = {**DEFAULT_CHAR, **old}

    def num(v, lo, hi, d):
        try:
            return max(lo, min(hi, int(v)))
        except (TypeError, ValueError):
            return d

    for k, limit in (("name", 40), ("cls", 30), ("race", 30), ("bg", 40), ("al", 30)):
        if k in raw:
            ch[k] = str(raw[k] or "").strip()[:limit]
    for k in TEXT_FIELDS:
        if k in raw:
            ch[k] = str(raw[k] or "").strip()[:TEXT_LIMIT]
    for k, lo, hi in (("lvl", 1, 20), ("hp", -99, 999), ("mhp", 1, 999), ("ac", 0, 40), ("spd", 0, 200)):
        if k in raw:
            ch[k] = num(raw[k], lo, hi, ch[k])
    if isinstance(raw.get("st"), dict):
        ch["st"] = {a: num(raw["st"].get(a, ch["st"].get(a, 10)), 1, 30, 10) for a in ABILITIES}
    if isinstance(raw.get("sv"), list):
        ch["sv"] = [a for a in ABILITIES if a in raw["sv"]]
    if isinstance(raw.get("sk"), list):
        ch["sk"] = sorted({int(i) for i in raw["sk"] if str(i).isdigit() and int(i) < len(SKILLS)})
    if isinstance(raw.get("atk"), list):
        atk = []
        for a in raw["atk"][:8]:
            if not isinstance(a, dict) or not str(a.get("n", "")).strip():
                continue
            dmg = str(a.get("dmg", "")).strip()[:30]
            try:
                dice.parse(dmg)
            except dice.DiceError:
                raise ValueError(f"Урон «{dmg}» у атаки «{a['n']}» — не формула кубиков (пример: 1d8+3)")
            atk.append({"n": str(a["n"]).strip()[:30], "hit": num(a.get("hit"), -10, 30, 0), "dmg": dice.normalize(dmg)})
        ch["atk"] = atk
    return ch


def info_text(ch: dict) -> list[str]:
    """Карточка персонажа текстом для Telegram (HTML), порезанная на сообщения ≤ 4096 символов."""
    e = html.escape
    st = ch["st"]
    pb = prof_bonus(ch["lvl"])
    sub = " · ".join(x for x in (f"{ch['cls'] or 'Без класса'}, {ch['lvl']} ур.", ch["race"], ch["bg"], ch["al"]) if x)
    parts = [
        f"🧙 <b>{e(ch['name'] or 'Безымянный герой')}</b>\n<i>{e(sub)}</i>\n\n"
        f"❤️ Хиты {ch['hp']}/{ch['mhp']} · 🛡 КД {ch['ac']} · 👣 {ch['spd']} фт\n"
        f"⚡ Инициатива {mod(st['dex']):+d} · бонус мастерства +{pb}",
        "<b>Характеристики</b>\n" + "\n".join(
            f"{ABILITY_SHORT[a]} {st[a]} ({mod(st[a]):+d})"
            + (f" · спасбросок {mod(st[a]) + pb:+d} ●" if a in ch["sv"] else "") for a in ABILITIES),
    ]
    if ch["sk"]:
        parts.append("<b>Навыки</b>\n" + ", ".join(
            f"{SKILLS[i][0]} {mod(st[SKILLS[i][1]]) + pb:+d}" for i in ch["sk"]))
    if ch["atk"]:
        parts.append("<b>Атаки</b>\n" + "\n".join(
            f"⚔️ {e(a['n'])}: {a['hit']:+d} к попаданию, урон {e(a['dmg'])}" for a in ch["atk"]))
    for k, title in TEXT_FIELDS.items():
        if ch.get(k):
            parts.append(f"<b>{title}</b>\n{e(ch[k])}")
    msgs, cur = [], ""
    for p in parts:
        if cur and len(cur) + len(p) + 2 > 4000:
            msgs.append(cur)
            cur = ""
        cur = (cur + "\n\n" + p) if cur else p
    msgs.append(cur)
    return [m[:4096] for m in msgs]


class Game:
    def __init__(self, db: DB, bot: Bot):
        self.db = db
        self.bot = bot
        self.listeners: set[asyncio.Queue] = set()
        self.me: dict = {}
        if not db.get("join_code"):
            db.set("join_code", "j" + secrets.token_hex(5))

    # ---------- события для сайта мастера ----------
    def emit(self, event: dict) -> None:
        for q in list(self.listeners):
            q.put_nowait(event)

    def emit_chars(self) -> None:
        self.emit({"type": "chars", "chars": self.public_chars()})

    def link(self, start: str) -> str | None:
        u = self.me.get("username")
        return f"https://t.me/{u}?start={start}" if u else None

    def public_chars(self) -> list[dict]:
        return [{"id": c["id"], "tg_id": c["tg_id"], "player": c["player"], "char": c["data"],
                 "link": self.link(c["token"])} for c in self.db.characters()]

    @property
    def join_code(self) -> str:
        return self.db.get("join_code")

    def state(self) -> dict:
        return {
            "chars": self.public_chars(),
            "rolls": self.db.rolls(200),
            "invite": self.link(self.join_code),
            "bot": self.me.get("username"),
            "skills": [s[0] for s in SKILLS],
        }

    # ---------- броски ----------
    @staticmethod
    def who(ch: dict) -> str:
        return ch["data"].get("name") or (ch["player"] or {}).get("first_name") or "Игрок"

    def record(self, tg_id, who: str, title: str, r: dice.Roll, extra: dict | None = None) -> dict:
        detail = {**r.to_dict(), "txt": r.dice_text(), **(extra or {})}
        entry = self.db.add_roll(tg_id, who, title, r.formula, r.total, detail)
        self.emit({"type": "roll", "roll": entry})
        return entry

    def gm_roll(self, formula: str, title: str = "", adv: int = 0) -> dict:
        return self.record(None, "Мастер", title, dice.roll(formula, adv))

    def attack(self, ch: dict, idx: int) -> dict:
        """Бросок атаки: попадание и сразу урон (на крите кубики урона удваиваются)."""
        a = ch["data"]["atk"][idx]
        hit = dice.roll(f"1d20{a['hit']:+d}")
        dmg = dice.roll(a["dmg"], crit=hit.crit)
        dd = {**dmg.to_dict(), "txt": dmg.dice_text()}
        self.record(ch["tg_id"], self.who(ch), a["n"], hit, {"d": dd})
        return {**hit.to_dict(), "d": dmg.to_dict()}

    # ---------- рассылка виджетов ----------
    async def send_dice(self, ch: dict):
        await self.bot.send_widget(ch["tg_id"], widgets.load("dice"), chat_data={"n": self.who(ch)})

    async def send_sheet(self, ch: dict):
        msg = await self.bot.send_widget(ch["tg_id"], widgets.load("sheet"), chat_data=compact_char(ch["data"]))
        self.db.add_sheet_msg(ch["id"], msg["message_id"])

    async def send_info(self, ch: dict):
        for text in info_text(ch["data"]):
            await self.bot.send_message(ch["tg_id"], text)

    async def refresh_sheets(self, cid: int) -> None:
        ch = self.db.character(cid)
        if not ch or not ch["tg_id"]:
            return
        data = compact_char(ch["data"])
        for mid in ch["sheet_msgs"]:
            try:
                await self.bot.edit_widget_data(ch["tg_id"], mid, data)
            except TelegramError as e:
                log.info("sheet %s not updated: %s", mid, e.description)

    def create_char(self, raw: dict) -> dict:
        data = clean_char(raw, DEFAULT_CHAR)
        ch = self.db.create_character(data)
        self.emit_chars()
        return ch

    async def update_char(self, cid: int, raw: dict) -> dict:
        ch = self.db.character(cid)
        if not ch:
            raise KeyError(cid)
        data = clean_char(raw, ch["data"])
        self.db.save_character(cid, data)
        self.emit_chars()
        asyncio.create_task(self.refresh_sheets(cid))
        return data

    async def ask_roll(self, cids: list[int], kind: str, title: str) -> list[int]:
        """Мастер просит игроков бросить. kind: c:str | s:dex | k:3 | i:0 | f:2d6+1"""
        sent = []
        for cid in cids:
            ch = self.db.character(cid)
            if not ch or not ch["tg_id"]:
                continue
            t = title or self.kind_title(ch["data"], kind)
            rid = self.db.add_request(ch["tg_id"], t, kind)
            try:
                msg = await self.bot.send_widget(ch["tg_id"], widgets.load("request"), chat_data={"i": rid, "t": t})
                self.db.update_request(rid, msg_id=msg["message_id"])
                sent.append(cid)
            except TelegramError as e:
                log.warning("request to %s failed: %s", ch["tg_id"], e)
        return sent

    @staticmethod
    def kind_title(ch: dict, kind: str) -> str:
        k, _, v = kind.partition(":")
        if k == "f":
            return f"Бросок {v}"
        return check_formula(ch, k, v)[0]

    async def message_players(self, cids: list[int], text: str) -> int:
        n = 0
        for cid in cids:
            ch = self.db.character(cid)
            if not ch or not ch["tg_id"]:
                continue
            try:
                await self.bot.send_message(ch["tg_id"], "🧙 <b>Мастер:</b> " + html.escape(text))
                n += 1
            except TelegramError as e:
                log.warning("message to %s failed: %s", ch["tg_id"], e)
        return n

    async def send_char_to_player(self, cid: int) -> bool:
        ch = self.db.character(cid)
        if not ch or not ch["tg_id"]:
            return False
        await self.send_info(ch)
        await self.send_sheet(ch)
        return True

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

    async def on_start(self, chat_id: int, user: dict, arg: str) -> None:
        own = self.db.character_of(user["id"])
        target = self.db.character_by_token(arg) if arg.startswith("c") else None
        if target:
            self.db.add_player(user["id"], user.get("username"), user.get("first_name", ""))
            if target["tg_id"] != user["id"]:
                self.db.bind(target["id"], user["id"])
            ch = self.db.character(target["id"])
        elif own:
            ch = own
        elif arg and arg == self.join_code:
            self.db.add_player(user["id"], user.get("username"), user.get("first_name", ""))
            ch = self.db.create_character(dict(DEFAULT_CHAR, name=user.get("first_name", "")), user["id"])
        else:
            await self.bot.send_message(chat_id, "🛡 Привет! Чтобы присоединиться к игре, попроси у мастера ссылку на своего персонажа.")
            return
        self.emit_chars()
        await self.bot.send_message(
            chat_id, f"⚔️ Ты играешь за <b>{html.escape(self.who(ch))}</b>!\n\n" + HELP)
        await self.send_info(ch)
        await self.send_sheet(ch)
        await self.send_dice(ch)

    async def on_message(self, m: dict) -> None:
        text = (m.get("text") or "").strip()
        chat = m["chat"]
        user = m.get("from") or {}
        if not text or chat["type"] != "private":
            return
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        arg = arg.strip()

        if cmd == "/start":
            await self.on_start(chat["id"], user, arg)
            return
        ch = self.db.character_of(user["id"])
        if not ch:
            await self.bot.send_message(chat["id"], "Сначала открой ссылку на персонажа от мастера.")
            return
        cid = ch["id"]

        if cmd in ("/help", "/помощь"):
            await self.bot.send_message(chat["id"], HELP)
        elif cmd in ("/dice", "/кости"):
            await self.send_dice(ch)
        elif cmd in ("/sheet", "/лист"):
            await self.send_sheet(ch)
        elif cmd in ("/info", "/char", "/перс"):
            await self.send_info(ch)
        elif cmd == "/name" and arg:
            await self.update_char(cid, {"name": arg})
            await self.bot.send_message(chat["id"], f"Теперь ты — <b>{html.escape(arg[:40])}</b>")
        elif cmd == "/class" and arg:
            parts = arg.rsplit(" ", 1)
            raw = {"cls": parts[0]}
            if arg.isdigit():
                raw = {"lvl": arg}
            elif len(parts) == 2 and parts[1].isdigit():
                raw["lvl"] = parts[1]
            await self.update_char(cid, raw)
            await self.bot.send_message(chat["id"], "Записал ✍️")
        elif cmd == "/stats":
            nums = arg.split()
            if len(nums) != 6 or not all(n.isdigit() for n in nums):
                await self.bot.send_message(chat["id"], "Нужно 6 чисел: /stats 16 14 13 10 12 8")
                return
            await self.update_char(cid, {"st": dict(zip(ABILITIES, nums))})
            await self.bot.send_message(chat["id"], "Характеристики обновлены 💪")
        elif cmd == "/hp" and arg:
            nums = arg.split()
            raw = {"hp": nums[0]}
            if len(nums) > 1:
                raw["mhp"] = nums[1]
            data = await self.update_char(cid, raw)
            await self.bot.send_message(chat["id"], f"❤️ Хиты: {data['hp']} / {data['mhp']}")
        elif cmd == "/ac" and arg:
            data = await self.update_char(cid, {"ac": arg})
            await self.bot.send_message(chat["id"], f"🛡 КД: {data['ac']}")
        elif cmd in ("/roll", "/r") or FORMULA_RE.match(text):
            formula = arg if cmd.startswith("/") else text
            await self.text_roll(ch, formula or "1d20")
        else:
            await self.bot.send_message(chat["id"], "Не понял. /help — список команд.")

    async def text_roll(self, ch: dict, formula: str) -> None:
        try:
            r = dice.roll(formula)
        except dice.DiceError as e:
            await self.bot.send_message(ch["tg_id"], f"🤔 {html.escape(str(e))}")
            return
        self.record(ch["tg_id"], self.who(ch), "", r)
        mark = " 💥 КРИТ!" if r.crit else (" 💀 провал" if r.fumble else "")
        await self.bot.send_message(
            ch["tg_id"], f"🎲 <b>{r.total}</b>{mark}\n<i>{html.escape(r.formula)}: {html.escape(r.dice_text())}</i>")

    async def on_widget_query(self, q: dict) -> None:
        qid, data, user = q["id"], q.get("data", ""), q["from"]
        ch = self.db.character_of(user["id"])
        if not ch:
            await self.bot.answer_widget_query(qid, {"e": "У тебя нет персонажа — попроси ссылку у мастера"})
            return
        try:
            answer = await self.widget_roll(ch, data)
        except (dice.DiceError, ValueError, KeyError, IndexError) as e:
            answer = {"e": str(e) or "Ошибка"}
        await self.bot.answer_widget_query(qid, answer)

    async def widget_roll(self, ch: dict, data: str) -> dict:
        kind, _, arg = data.partition(":")
        who = self.who(ch)
        if kind.startswith("r") and len(kind) == 3:  # кости: r<n|a|d><флаг>:формула
            adv = {"n": 0, "a": 1, "d": -1}[kind[1]]
            return self.record(ch["tg_id"], who, "", dice.roll(arg, adv))["detail"]
        if kind in ("c", "s", "k", "i"):  # лист персонажа
            title, formula = check_formula(ch["data"], kind, arg)
            return self.record(ch["tg_id"], who, title, dice.roll(formula))["detail"]
        if kind == "a":  # атака из листа
            return self.attack(ch, int(arg))
        if kind == "n":  # «📜» — карточка персонажа сообщением
            asyncio.create_task(self.send_info(ch))
            return {"m": "📜 Отправил карточку в чат"}
        if kind == "q":  # запрос мастера
            req = self.db.request(int(arg))
            if not req or req["tg_id"] != ch["tg_id"]:
                raise ValueError("Этот запрос не тебе")
            if req["roll_id"]:
                raise ValueError("Уже брошено")
            k, _, v = req["kind"].partition(":")
            formula = v if k == "f" else check_formula(ch["data"], k, v)[1]
            r = dice.roll(formula)
            entry = self.record(ch["tg_id"], who, req["title"], r)
            self.db.update_request(req["id"], roll_id=entry["id"])
            if req["msg_id"]:
                try:
                    await self.bot.edit_widget_data(ch["tg_id"], req["msg_id"], {
                        "i": req["id"], "t": req["title"], "x": r.to_dict()})
                except TelegramError as e:
                    log.info("request widget not updated: %s", e)
            return r.to_dict()
        raise ValueError("Неизвестный запрос")

    # ---------- long polling ----------
    async def poll(self) -> None:
        self.me = await self.bot.call("getMe")
        log.info("bot @%s started", self.me.get("username"))
        await self.bot.call("setMyCommands", commands=[
            {"command": "sheet", "description": "Лист персонажа"},
            {"command": "dice", "description": "Кости"},
            {"command": "info", "description": "Всё о персонаже"},
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
