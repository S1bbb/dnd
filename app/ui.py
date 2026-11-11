"""Интерфейс игрока в Telegram: команды, панели на inline-кнопках, виджеты Bot API 10.4, ввод физических кубиков.

Главный экран — виджет листа (броски) + панель «☰» (состояние персонажа и все действия). Все изменения идут через
Game.mutate, поэтому они попадают в историю (↩️ отмена), на сайт мастера и в журнал."""
import asyncio
import html
import json
import logging
import re

from . import dice, engine, widgets
from .data.equipment import CATALOG, item as make_item
from .data.spells import SPELLS, for_class, search as spell_search
from .game import compact_char, info_text
from .rules import ABILITIES, ABILITY_SHORT, SKILLS
from .tg import TelegramError

log = logging.getLogger("ui")
E = html.escape
PAGE = 10

HELP = (
    "<b>Всё просто:</b>\n"
    "🎲 <b>Бросать</b> — кнопки в листе ниже: проверка, спасбросок, навык, атака. Мастер видит броски сразу.\n"
    "☰ <b>Всё остальное</b> — меню под листом (или /menu): заклинания, вещи, умения, отдых.\n"
    "🎯 <b>Мастер просит бросок</b> — придёт карточка с кнопкой «Бросить».\n"
    "🎲 <b>Свои кубики</b> — напиши, что выпало на d20: <code>17 скрытность</code>, или просто число в ответ на запрос."
)
# В бою характеристики персонажа меняет только мастер: эти кнопки игрока заблокированы,
# а заклинания, умения и предметы уходят мастеру заявкой (подтвердить / отклонить на сайте).
COMBAT_LOCK = {"dmg", "heal", "temp", "gold", "cd", "ex", "dropc", "rme", "rest", "hd", "short", "long",
               "eq", "q", "add", "r", "sl", "undo", "healself", "lt", "hpm"}
LOCK_MSG = "⚔️ Идёт бой: хиты, состояния, ресурсы и вещи меняет мастер. Скажи ему, что случилось."
FORMULA_RE = re.compile(r"^\s*[\dкдd%+\-\s]*[dкд]\d*[\dкдdkhl%+\-\s]*$", re.I)
PHYS_RE = re.compile(r"^\s*(\d{1,2})\s+(.{3,})$")
FEATURE_RES = {"Ярость": "rage", "Второе дыхание": "second_wind", "Всплеск действий": "action_surge",
               "Вдохновение барда": "inspiration", "Дикий облик": "wildshape", "Ци": "ki", "Наложение рук": "lay_on_hands",
               "Божественное чувство": "divine_sense", "Источник магии": "sorcery", "Магическое восстановление": "arcane_recovery",
               "Упорный": "indomitable", "Боевое превосходство": "superiority", "Оружие дыхания": "breath"}


def btn(t, d):
    return {"text": t, "callback_data": ("p:" + d)[:64]}


def kb(rows):
    return {"inline_keyboard": [r for r in rows if r]}


class BotUI:
    def __init__(self, game):
        self.g = game
        self.db = game.db
        self.bot = game.bot
        self.seen: set[str] = set()

    # ---------- общее ----------
    def char(self, uid: int) -> dict | None:
        return self.db.character_of(uid)

    async def answer(self, q, text=None, alert=False):
        try:
            await self.bot.call("answerCallbackQuery", callback_query_id=q["id"], text=text, show_alert=alert or None)
        except Exception:
            pass

    async def edit_or_send(self, chat_id, msg_id, text, markup):
        text = text[:4000]
        if msg_id:
            try:
                await self.bot.call("editMessageText", chat_id=chat_id, message_id=msg_id, text=text, parse_mode="HTML", reply_markup=markup)
                return
            except TelegramError as e:
                if "not modified" in e.description:
                    return
        await self.bot.send_message(chat_id, text, reply_markup=markup)

    async def welcome(self, ch: dict) -> None:
        g = self.db.game(ch["game_id"])
        await self.bot.send_message(ch["tg_id"], f"⚔️ Ты играешь за <b>{E(self.g.who(ch))}</b> в игре «{E(g['name'])}»!\n\n" + HELP)
        await self.send_sheet(ch)
        await self.panel_main(ch["tg_id"], None, ch)

    async def send_sheet(self, ch: dict):
        msg = await self.bot.send_widget(ch["tg_id"], widgets.load("sheet"), chat_data=compact_char(ch["data"], ch["id"]))
        self.db.add_sheet_msg(ch["id"], msg["message_id"])

    async def send_dice(self, ch: dict):
        await self.bot.send_widget(ch["tg_id"], widgets.load("dice"), chat_data={"n": self.g.who(ch), "id": ch["id"]})

    async def send_info(self, ch: dict):
        for text in info_text(ch["data"]):
            await self.bot.send_message(ch["tg_id"], text)

    def mut(self, ch, fn, label):
        return self.g.mutate(ch["id"], fn, self.g.who(ch), label)

    # ---------- апдейты ----------
    async def handle(self, upd: dict) -> None:
        try:
            if "message" in upd:
                await self.on_message(upd["message"])
            elif "callback_query" in upd:
                q = upd["callback_query"]
                if q["id"] in self.seen:  # повтор того же нажатия
                    return
                self.seen.add(q["id"])
                if len(self.seen) > 5000:
                    self.seen.clear()
                data = q.get("data", "")
                if data.startswith("w:"):
                    await self.g.wiz.on_button(q)
                elif data.startswith("l:"):
                    await self.g.lvl.on_button(q)
                elif data.startswith("m:"):
                    await self.g.map.on_button(q)
                elif data.startswith("p:"):
                    await self.on_panel(q)
                else:
                    await self.answer(q)
            elif "widget_query" in upd:
                await self.on_widget_query(upd["widget_query"])
            elif "shared_widget_query" in upd:
                await self.bot.answer_widget_query(upd["shared_widget_query"]["id"], {"e": "Открой чат с ботом"})
        except Exception:
            log.exception("update failed: %s", json.dumps(upd)[:500])

    async def on_start(self, chat_id: int, user: dict, arg: str) -> None:
        uid = user["id"]
        target = self.db.character_by_token(arg) if arg.startswith("c") else None
        if target:  # личная ссылка от мастера
            self.db.add_player(uid, user.get("username"), user.get("first_name", ""))
            if target["tg_id"] != uid:
                self.db.bind(target["id"], uid)
            self.db.set_active_game(uid, target["game_id"])
            self.g.wiz.drop(uid)
            self.g.emit_chars(target["game_id"])
            return await self.welcome(self.db.character(target["id"]))
        g = self.db.game_by_code(arg) if arg else None
        if g:
            own = self.db.character_of(uid, g["id"])
            if own:
                self.db.set_active_game(uid, g["id"])
                return await self.welcome(own)
            return await self.g.wiz.begin(chat_id, user, g["id"])
        own = self.char(uid)
        if own:
            return await self.welcome(own)
        await self.g.wiz.begin(chat_id, user)

    async def on_message(self, m: dict) -> None:
        text = (m.get("text") or "").strip()
        chat = m["chat"]
        user = m.get("from") or {}
        if not text or chat["type"] != "private":
            return
        uid = user["id"]
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        arg = arg.strip()
        if cmd == "/start":
            return await self.on_start(chat["id"], user, arg)
        ch = self.char(uid)
        wiz = self.g.wiz.get(uid)
        if cmd in ("/cancel", "/отмена"):
            if wiz:
                return await self.g.wiz.cancel(chat["id"], uid)
            self.db.delete(f"inp:{uid}")
            self.g.lvl.drop(uid)
            return await self.bot.send_message(chat["id"], "Отменено.")
        if cmd in ("/new", "/новый"):
            return await self.g.wiz.begin(chat["id"], user, ch["game_id"] if ch else None)
        if wiz and (not cmd.startswith("/") or not ch):
            return await self.g.wiz.on_text(chat["id"], user, text)
        if not ch:
            typed = not cmd.startswith("/") and any(c.isdigit() for c in text)
            await self.g.wiz.begin(chat["id"], user, show=not typed)
            if typed:
                await self.g.wiz.on_text(chat["id"], user, text)
            return
        inp = self.db.get(f"inp:{uid}")
        if inp and not cmd.startswith("/"):
            self.db.delete(f"inp:{uid}")
            return await self.on_input(ch, inp, text)
        game = self.db.game(ch["game_id"])
        if game["status"] == "archived" and cmd not in ("/games", "/help", "/info", "/journal"):
            return await self.bot.send_message(chat["id"], f"Игра «{E(game['name'])}» в архиве. /games — переключиться.")
        routes = {
            ("/help", "/помощь"): lambda: self.bot.send_message(chat["id"], HELP),
            ("/dice", "/кости"): lambda: self.send_dice(ch),
            ("/sheet", "/лист"): lambda: self.send_sheet(ch),
            ("/info", "/char", "/перс"): lambda: self.send_info(ch),
            ("/menu", "/hp", "/state"): lambda: self.panel_main(chat["id"], None, ch),
            ("/spells", "/закл"): lambda: self.panel_spells(chat["id"], None, ch, 0, arg),
            ("/inv", "/инв"): lambda: self.panel_inv(chat["id"], None, ch),
            ("/feats", "/умения"): lambda: self.panel_feats(chat["id"], None, ch, 0, arg),
            ("/res",): lambda: self.panel_res(chat["id"], None, ch),
            ("/rest", "/отдых"): lambda: self.panel_rest(chat["id"], None, ch),
            ("/levelup", "/уровень"): lambda: self.g.lvl.begin(chat["id"], ch),
            ("/journal", "/журнал"): lambda: self.panel_journal(chat["id"], None, ch, arg),
            ("/games", "/игры"): lambda: self.panel_games(chat["id"], None, ch),
            ("/map", "/карта"): lambda: self.send_map(ch),
            ("/undo",): lambda: self.do_undo(chat["id"], ch),
        }
        for keys, fn in routes.items():
            if cmd in keys:
                return await fn()
        if cmd in ("/roll", "/r") or FORMULA_RE.match(text):
            return await self.text_roll(ch, arg if cmd.startswith("/") else text)
        if text.isdigit():
            req = self.db.pending_request(uid, ch["game_id"])
            if req:
                try:
                    res = await self.g.answer_request(ch, req["id"], int(text))
                    return await self.bot.send_message(chat["id"], f"🎲 {E(req['title'])}: <b>{res['t']}</b> (свои кубики)"
                                                       if not res.get("h") else "🎲 Отправил мастеру")
                except (ValueError, dice.DiceError) as e:
                    return await self.bot.send_message(chat["id"], f"🤔 {E(str(e))}")
        pm = PHYS_RE.match(text)
        if pm:
            spec = engine.check_by_name(ch["data"], pm.group(2))
            if spec:
                try:
                    r = self.g.roll_check(ch, spec[0], spec[1], int(pm.group(1)))
                    return await self.bot.send_message(chat["id"], f"🎲 <b>{r['t']}</b> — {E(r['f'])}")
                except dice.DiceError as e:
                    return await self.bot.send_message(chat["id"], f"🤔 {E(str(e))}")
        await self.bot.send_message(chat["id"], "Не понял. /help — список команд, /menu — действия.")

    async def text_roll(self, ch: dict, formula: str) -> None:
        try:
            r = dice.roll(formula or "1d20")
        except dice.DiceError as e:
            await self.bot.send_message(ch["tg_id"], f"🤔 {E(str(e))}")
            return
        self.g.record(ch, "", r)
        mark = " 💥 КРИТ!" if r.crit else (" 💀 провал" if r.fumble else "")
        await self.bot.send_message(ch["tg_id"], f"🎲 <b>{r.total}</b>{mark}\n<i>{E(r.formula)}: {E(r.dice_text())}</i>")

    async def do_undo(self, chat_id, ch):
        if self.in_combat(ch):
            return await self.bot.send_message(chat_id, LOCK_MSG)
        h = self.db.last_history(ch["id"])
        if not h:
            return await self.bot.send_message(chat_id, "Нечего отменять.")
        if h["actor"] != self.g.who(ch):
            return await self.bot.send_message(chat_id, "Последнее изменение сделал мастер — отменить может только он.")
        label = self.g.undo(ch["id"], self.g.who(ch))
        await self.bot.send_message(chat_id, f"↩️ Отменено: {E(label)}")

    # ---------- ввод чисел и текста ----------
    async def on_input(self, ch, inp, text):
        op = inp["op"]
        if op in ("dmg", "heal", "temp", "loh", "gold", "add_item") and self.in_combat(ch):
            return await self.bot.send_message(ch["tg_id"], LOCK_MSG)
        cid = ch["id"]
        if op in ("dmg", "heal", "temp", "loh", "gold"):
            mm = re.match(r"^\s*(\d+)\s*(.*)$", text)
            if not mm:
                return await self.bot.send_message(ch["tg_id"], "Нужно число.")
            n, rest = int(mm.group(1)), mm.group(2).strip().lower()
            if op == "dmg":
                _, log_ = self.mut(ch, lambda d: engine.damage(d, n, rest), f"урон {n}" + (f" ({rest})" if rest else ""))
            elif op == "heal":
                _, log_ = self.mut(ch, lambda d: engine.heal(d, n), f"лечение {n}")
            elif op == "temp":
                _, log_ = self.mut(ch, lambda d: engine.set_temp(d, n), f"временные хиты {n}")
            elif op == "gold":
                _, log_ = self.mut(ch, lambda d: d.update(gold=n) or [], f"золото = {n}")
            else:
                def loh(d):
                    engine.use_res(d, "lay_on_hands", n)
                    return engine.heal(d, n)
                try:
                    _, log_ = self.mut(ch, loh, f"Наложение рук на себя: лечение {n}")
                except ValueError as e:
                    return await self.bot.send_message(ch["tg_id"], f"🤔 {E(str(e))}")
            if log_:
                await self.bot.send_message(ch["tg_id"], "\n".join(E(x) for x in log_))
            return await self.panel_main(ch["tg_id"], None, self.db.character(cid))
        if op == "spell_search":
            return await self.panel_spells(ch["tg_id"], None, ch, 0, text)
        if op == "feat_search":
            return await self.panel_feats(ch["tg_id"], None, ch, 0, text)
        if op == "add_item":
            name = text.strip()[:60]
            match = next((n for n in CATALOG if n.lower() == name.lower()), None) or \
                next((n for n in CATALOG if n.lower().startswith(name.lower())), None)
            it = make_item(match or name)
            self.mut(ch, lambda d: d.setdefault("items", []).append(it), f"предмет: {it['n']}")
            await self.bot.send_message(ch["tg_id"], f"🎒 Добавлено: {E(it['n'])}" + ("" if match else " (свой предмет)"))
            return await self.panel_inv(ch["tg_id"], None, self.db.character(cid))

    # ---------- главная панель ----------
    def main_text(self, ch) -> str:
        d = ch["data"]
        D = engine.derive(d)
        hp, mx = d.get("hp", 0), D["hp_max"]["val"]
        bar = "🟩" * round(8 * max(0, hp) / mx) + "⬜" * (8 - round(8 * max(0, hp) / mx)) if mx else ""
        lines = [f"🧙 <b>{E(d.get('name') or '?')}</b> · {E(d.get('cls', ''))} {D['lvl']} ур. · «{E(self.db.game(ch['game_id'])['name'])}»",
                 f"❤️ <b>{hp}/{mx}</b>" + (f" +{d['temp']} врем." if d.get("temp") else "") + f" {bar}",
                 f"🛡 КД {D['ac']['val']} · 👣 {D['spd']['val']} фт · ⚡ {D['init']['bonus']:+d}"]
        if self.in_combat(ch):
            cb = self.g.combat(ch["game_id"])
            cur = cb["list"][cb["turn"]] if 0 <= cb.get("turn", -1) < len(cb["list"]) else None
            mine = cur and cur.get("cid") == ch["id"]
            lines.insert(0, f"⚔️ <b>Идёт бой, раунд {cb['round']}</b>" + (" — <b>твой ход!</b>" if mine else (f", ходит {E(cur['name'])}" if cur and not cur.get('hidden') else ""))
                         + "\n<i>Ты бросаешь и объявляешь действия. Хиты, состояния и ресурсы меняет мастер; заклинания и умения уходят ему на подтверждение.</i>\n")
        if d.get("dead"):
            lines.append("☠️ <b>Персонаж мёртв</b>")
        elif hp == 0:
            s, f = d.get("death", [0, 0])
            lines.append(f"💀 Спасброски от смерти: {'✅' * s}{'▫️' * (3 - s)} | {'❌' * f}{'▫️' * (3 - f)}")
        conds = list(d.get("conds", [])) + ([f"Истощение {d['exh']}"] if d.get("exh") else [])
        if conds:
            lines.append("🩹 " + ", ".join(conds))
        if d.get("conc"):
            lines.append(f"🧠 Концентрация: {E(d['conc'])}")
        for e_ in d.get("effects", []):
            lines.append(f"✨ {E(e_['n'])}" + (f" ({e_['rounds']} р.)" if e_.get("rounds") else "") + (f" — {E(e_['note'])}" if e_.get("note") else ""))
        if D["slots"] or D["pact"]:
            sl = " ".join(f"{k}:{'●' * v['left']}{'○' * (v['max'] - v['left'])}" for k, v in D["slots"].items())
            if D["pact"]:
                sl += f" договор {D['pact']['lvl']}:{'●' * D['pact']['left']}{'○' * (D['pact']['max'] - D['pact']['left'])}"
            lines.append("🔮 " + sl)
        if D["res"]:
            lines.append("🔋 " + " · ".join(f"{r['n']} {r['left']}/{r['max']}" for r in D["res"][:5]))
        req = self.db.pending_request(ch["tg_id"], ch["game_id"]) if ch["tg_id"] else None
        if req:
            lines.append(f"🎯 Мастер ждёт бросок: <b>{E(req['title'])}</b> (кнопка в виджете или пришли число с d20)")
        if d.get("lvl_pending"):
            lines.append("📈 <b>Доступен новый уровень!</b> /levelup")
        for w in D["warn"]:
            lines.append("⚠️ " + E(w))
        return "\n".join(lines)

    def main_kb(self, ch):
        d = ch["data"]
        fight = self.in_combat(ch)
        rows = []
        if d.get("hp", 1) == 0 and not d.get("dead"):
            rows.append([btn("💀 Бросить спасбросок от смерти", "death")])
        if d.get("lvl_pending") and not fight:
            rows.append([btn("📈 Получить новый уровень", "lvl")])
        rows.append([btn("🎲 Лист с бросками", "sheet"), btn("🎲 Кости", "dice")])
        if engine.is_v2(d):
            rows.append([btn("✨ Заклинания", "sp:0"), btn("⭐ Умения", "ft:0"), btn("🎒 Вещи", "inv")])
        if not fight:
            rows.append([btn("❤️ Хиты и урон", "hpm"), btn("🩹 Состояния", "cond")])
            if engine.is_v2(d):
                rows.append([btn("🏕 Отдых", "rest"), btn("🔋 Ресурсы и ячейки", "res")])
        rows.append([btn("📜 Карточка героя", "info"), btn("📖 Журнал", "jr"), btn("🗺 Карта", "map")])
        if not fight:
            rows.append([btn("↩️ Отменить моё последнее действие", "undo")])
        return kb(rows)

    async def panel_hp(self, chat_id, msg_id, ch):
        d = ch["data"]
        D = engine.derive(d)
        await self.edit_or_send(chat_id, msg_id,
                                f"❤️ <b>Хиты {d.get('hp', 0)}/{D['hp_max']['val']}</b>" + (f" +{d['temp']} временных" if d.get("temp") else "")
                                + "\n\nВне боя меняй хиты сам. В бою это делает мастер.",
                                kb([[btn("🗡 Получил урон", "dmg"), btn("✚ Вылечился", "heal")], [btn("🛡 Временные хиты", "temp")], [btn("⬅️ Меню", "main")]]))

    async def panel_main(self, chat_id, msg_id, ch):
        await self.edit_or_send(chat_id, msg_id, self.main_text(ch), self.main_kb(ch))

    # ---------- заклинания ----------
    def my_spells(self, ch, q="") -> list[dict]:
        names = engine.spell_list(ch["data"])
        fav = set(ch["data"].get("fav", []))
        sp = [SPELLS[n] for n in names]
        if q:
            sp = spell_search(q, sp)
        return sorted(sp, key=lambda s: (s["n"] not in fav, s["lvl"], s["n"]))

    async def panel_spells(self, chat_id, msg_id, ch, page=0, q=""):
        if not engine.is_v2(ch["data"]):
            return await self.bot.send_message(chat_id, "У этого персонажа нет списка заклинаний (его ведёт мастер).")
        D = engine.derive(ch["data"])
        lst = self.my_spells(ch, q)
        fav = set(ch["data"].get("fav", []))
        part = lst[page * PAGE:(page + 1) * PAGE]
        all_names = [s["n"] for s in self.my_spells(ch)]
        rows = [[btn(("⭐ " if s["n"] in fav else "") + f"{s['n']} ({s['lvl'] or 'з'})" + (" 🧠" if s["conc"] else ""),
                     f"s:{all_names.index(s['n'])}")] for s in part]
        nav = []
        if page:
            nav.append(btn("◀", f"sp:{page - 1}"))
        if (page + 1) * PAGE < len(lst):
            nav.append(btn("▶", f"sp:{page + 1}"))
        rows.append(nav)
        rows.append([btn("🔍 Поиск", "sps"), btn("📚 Выучить / подготовить", "lr:0")])
        rows.append([btn("⬅️ Меню", "main")])
        cast = D["cast"][0] if D["cast"] else None
        head = "✨ <b>Заклинания</b>" + (f" — «{E(q)}»" if q else "")
        if cast:
            head += f"\nСЛ спасброска {cast['dc']} · атака {cast['atk']:+d}"
            for c in D["cast"]:
                lim = []
                if c.get("known"):
                    lim.append(f"известных до {c['known']}")
                if c.get("prepared"):
                    lim.append(f"готовить до {c['prepared']}")
                lim.append(f"заговоров {c['cantrips']}")
                head += f"\n{c['n']}: " + ", ".join(lim)
        if not lst:
            head += "\n\nПусто. Нажми «📚 Выучить / подготовить»."
        await self.edit_or_send(chat_id, msg_id, head, kb(rows))

    async def spell_card(self, chat_id, msg_id, ch, idx):
        lst = self.my_spells(ch)
        if idx >= len(lst):
            return
        s = lst[idx]
        D = engine.derive(ch["data"])
        r = engine.spell_roll(ch["data"], s["n"], None, D)
        mech = ""
        if r.get("kind") == "a":
            mech = f"🎯 Атака {r['hit']:+d}, урон {r['dice']} {r['type']}"
        elif r.get("kind") == "s":
            mech = f"🛡 Спасбросок {r['save']} СЛ {r['dc']}" + (f", урон {r['dice']} {r['type']}" if r.get("dice") else "") + (" (½ при успехе)" if r.get("half") else "")
        elif r.get("kind") == "h":
            mech = f"✚ Лечение {r['dice']}"
        elif r.get("kind") == "d":
            mech = f"💥 Урон {r['dice']} {r['type']}"
        text = (f"✨ <b>{E(s['n'])}</b> — {'заговор' if not s['lvl'] else str(s['lvl']) + ' круг'}, {s['school']}\n"
                f"⏱ {s['time']} · 📏 {E(s['rng'])} · ⌛ {E(s['dur'])}" + (" · 🧠 концентрация" if s["conc"] else "") + (" · ритуал" if s["ritual"] else "")
                + f"\n\n{E(s['d'])}" + (f"\n\n{mech}" if mech else ""))
        rows = []
        if s["lvl"] == 0:
            rows.append([btn("✨ Сотворить", f"c:{idx}:0")])
        else:
            opts = []
            if D["pact"] and D["pact"]["lvl"] >= s["lvl"] and D["pact"]["left"]:
                opts.append(btn(f"Договор {D['pact']['lvl']} ({D['pact']['left']})", f"c:{idx}:{D['pact']['lvl']}"))
            for lv, v in D["slots"].items():
                if lv >= s["lvl"] and v["left"]:
                    opts.append(btn(f"{lv} круг ({v['left']})", f"c:{idx}:{lv}"))
            rows += [opts[i:i + 3] for i in range(0, len(opts), 3)] or [[btn("Нет свободных ячеек", "noop")]]
            if s["ritual"]:
                rows.append([btn("📿 Ритуалом (без ячейки, +10 мин)", f"c:{idx}:r")])
        fav = s["n"] in ch["data"].get("fav", [])
        rows.append([btn("☆ Убрать из избранного" if fav else "⭐ В избранное", f"fav:s:{idx}"), btn("⬅️ Список", "sp:0")])
        await self.edit_or_send(chat_id, msg_id, text, kb(rows))

    def in_combat(self, ch) -> bool:
        return bool(self.g.combat(ch["game_id"]).get("on"))

    def cast_core(self, ch, name: str, slot: str, actor: str) -> tuple[str, int]:
        """Сотворить: списать ячейку, бросить. Возвращает (текст результата, сколько лечит или 0)."""
        s = SPELLS[name]
        ritual = slot == "r"
        lvl = 0 if s["lvl"] == 0 else (None if ritual else int(slot))
        if not ritual:
            fn = lambda d: engine.cast(d, name, lvl)  # noqa: E731
        else:
            def fn(d):  # ритуал: без ячейки, но концентрация та же
                out = []
                if s["conc"]:
                    out += engine.drop_conc(d)
                    d["conc"] = name
                    out.append("🧠 концентрация")
                return out
        _, log_ = self.g.mutate(ch["id"], fn, actor, f"{'ритуал' if ritual else 'заклинание'} «{name}»" + (f" ({lvl} круг)" if lvl else ""))
        ch = self.db.character(ch["id"])
        r = engine.spell_roll(ch["data"], name, lvl or s["lvl"])
        out = [f"✨ <b>{E(name)}</b>" + (f" — {'; '.join(E(x) for x in log_)}" if log_ else "")]
        heal = 0
        if r.get("kind") == "a":
            hit = dice.roll(f"1d20{r['hit']:+d}")
            dmg = dice.roll(r["dice"], crit=hit.crit)
            self.g.record(ch, name, hit, {"d": {**dmg.to_dict(), "txt": dmg.dice_text()}})
            out.append(f"🎯 Атака: <b>{hit.total}</b>{' 💥' if hit.crit else ' 💀' if hit.fumble else ''} · урон <b>{dmg.total}</b> {E(r['type'])}")
        elif r.get("kind") in ("s", "d") and r.get("dice"):
            dmg = dice.roll(r["dice"])
            self.g.record(ch, name, dmg)
            out.append((f"🛡 Цели: спасбросок {r['save']} СЛ {r['dc']}. " if r.get("kind") == "s" else "")
                       + f"Урон <b>{dmg.total}</b> {E(r['type'])}" + (" (половина при успехе)" if r.get("half") else ""))
        elif r.get("kind") == "s":
            out.append(f"🛡 Цели: спасбросок {r['save']} СЛ {r['dc']}")
        elif r.get("kind") == "h":
            h = dice.roll(r["dice"])
            self.g.record(ch, name, h)
            out.append(f"✚ Лечение <b>{h.total}</b>")
            heal = h.total
        return "\n".join(out), heal

    async def do_cast(self, chat_id, msg_id, ch, idx, slot):
        s = self.my_spells(ch)[idx]
        if self.in_combat(ch):
            lv = "ритуалом" if slot == "r" else ("" if s["lvl"] == 0 else f"{slot} круг")
            return await self.propose(ch, "cast", {"spell": s["n"], "slot": slot}, f"заклинание «{s['n']}» {lv}".strip())
        try:
            text, heal = self.cast_core(ch, s["n"], slot, self.g.who(ch))
        except ValueError as e:
            return await self.bot.send_message(chat_id, f"🤔 {E(str(e))}")
        if heal:
            self.db.set(f"heal:{ch['tg_id']}", heal)
            return await self.bot.send_message(chat_id, text, reply_markup=kb([[btn(f"✚ На себя (+{heal})", "healself")], [btn("⬅️ Меню", "main")]]))
        await self.bot.send_message(chat_id, text, reply_markup=kb([[btn("⬅️ Меню", "main"), btn("✨ Заклинания", "sp:0")]]))

    async def propose(self, ch, kind: str, args: dict, label: str) -> None:
        """В бою игрок не меняет себя сам: действие уходит мастеру на подтверждение."""
        self.g.propose(ch, kind, args, label)
        await self.bot.send_message(ch["tg_id"], f"⏳ Отправил мастеру: <b>{E(label)}</b>.\nВ бою ресурсы и хиты меняет он — когда подтвердит, пришлю результат.")

    async def apply_proposal(self, p: dict) -> str:
        """Мастер подтвердил заявку игрока — выполнить её. Возвращает текст результата."""
        ch = self.db.character(p["cid"])
        actor = f"Мастер (заявка {self.g.who(ch)})"
        if p["kind"] == "cast":
            text, heal = self.cast_core(ch, p["args"]["spell"], p["args"]["slot"], actor)
            if heal:
                text += "\nКого лечить — мастер решит сам (на сайте ✚)."
            return text
        if p["kind"] == "feature":
            return self.feature_core(ch, p["args"]["name"], actor)
        if p["kind"] == "item":
            return self.item_core(ch, p["args"]["i"], p["args"]["name"], actor)
        raise ValueError("неизвестная заявка")

    async def panel_learn(self, chat_id, msg_id, ch, page=0):
        D = engine.derive(ch["data"])
        if not D["cast"]:
            return await self.bot.send_message(chat_id, "Твой класс пока не колдует.")
        c = D["cast"][0]
        sp = ch["data"]["sp"]
        src = c["c"] if c["kind"] != "third" else "wizard"
        pool = for_class(src, c["max_lvl"])
        if c["c"] == "wizard":  # волшебник готовит из книги, учит новые в книгу
            pool = pool
        part = pool[page * PAGE:(page + 1) * PAGE]
        rows = []
        for i, s in enumerate(part):
            k = s["n"] in sp.get("known", [])
            p = s["n"] in sp.get("prep", [])
            mark = "📗" if p else ("📘" if k else "▫️")
            rows.append([btn(f"{mark} {s['n']} ({s['lvl'] or 'з'})", f"lt:{page * PAGE + i}")])
        nav = []
        if page:
            nav.append(btn("◀", f"lr:{page - 1}"))
        if (page + 1) * PAGE < len(pool):
            nav.append(btn("▶", f"lr:{page + 1}"))
        rows += [nav, [btn("⬅️ Заклинания", "sp:0")]]
        known_n = sum(1 for n in sp.get("known", []) if SPELLS.get(n, {}).get("lvl", 0) > 0)
        cant_n = sum(1 for n in sp.get("known", []) + sp.get("prep", []) if SPELLS.get(n, {}).get("lvl", 1) == 0)
        prep_n = sum(1 for n in sp.get("prep", []) if SPELLS.get(n, {}).get("lvl", 0) > 0)
        lim = [f"заговоров {cant_n}/{c['cantrips']}"]
        if c.get("known"):
            lim.append(f"известных {known_n}/{c['known']}")
        if c.get("book"):
            lim.append(f"в книге {known_n} (+2 за уровень)")
        if c.get("prepared"):
            lim.append(f"подготовлено {prep_n}/{c['prepared']}")
        hint = ("📗 — подготовлено, 📘 — известно (в книге). Нажми, чтобы переключить." if c.get("prepared")
                else "📘 — известно. Нажми, чтобы выучить или забыть (менять известные можно при повышении уровня — договорись с мастером).")
        await self.edit_or_send(chat_id, msg_id, f"📚 <b>{c['n']}</b>: до {c['max_lvl']} круга\n" + ", ".join(lim) + "\n" + hint, kb(rows))

    def toggle_learn(self, ch, idx):
        D = engine.derive(ch["data"])
        c = D["cast"][0]
        src = c["c"] if c["kind"] != "third" else "wizard"
        s = for_class(src, c["max_lvl"])[idx]

        def fn(d):
            sp = d.setdefault("sp", {"known": [], "prep": []})
            n = s["n"]
            cant = s["lvl"] == 0
            cnt = lambda lst, z: sum(1 for x in lst if (SPELLS.get(x, {}).get("lvl", 1) == 0) == z)  # noqa: E731
            if c.get("prepared") and not c.get("book") and not cant:
                lst = sp["prep"]
                if n in lst:
                    lst.remove(n)
                    return [f"«{n}» больше не подготовлено"]
                if cnt(lst, False) >= c["prepared"]:
                    raise ValueError(f"Подготовлено максимум ({c['prepared']})")
                lst.append(n)
                return [f"«{n}» подготовлено"]
            if c.get("book") and not cant:
                if n in sp["prep"]:
                    sp["prep"].remove(n)
                    return [f"«{n}» не подготовлено"]
                if n in sp["known"]:
                    if cnt(sp["prep"], False) >= c["prepared"]:
                        sp["known"].remove(n)
                        return [f"«{n}» убрано из книги"]
                    sp["prep"].append(n)
                    return [f"«{n}» подготовлено"]
                sp["known"].append(n)
                return [f"«{n}» записано в книгу"]
            lst = sp["known"]
            if n in lst:
                lst.remove(n)
                return [f"«{n}» забыто"]
            limit = c["cantrips"] if cant else c.get("known", 99)
            if cnt(lst + sp.get("prep", []), cant) >= limit:
                raise ValueError(f"Лимит {'заговоров' if cant else 'известных'}: {limit}")
            lst.append(n)
            return [f"«{n}» выучено"]
        return self.mut(ch, fn, "список заклинаний")

    # ---------- инвентарь ----------
    async def panel_inv(self, chat_id, msg_id, ch):
        d = ch["data"]
        items = d.get("items", [])
        rows = [[btn(("✅ " if it.get("eq") else "") + it["n"] + (f" ×{it['qty']}" if it.get("qty", 1) != 1 else "")
                     + (f" [{it['charges']}/{it.get('max_charges', it['charges'])}]" if it.get("max_charges") else ""), f"it:{i}")]
                for i, it in enumerate(items[:40])]
        rows.append([btn("➕ Добавить", "add"), btn(f"💰 {d.get('gold', 0)} зм", "gold")])
        rows.append([btn("⬅️ Меню", "main")])
        await self.edit_or_send(chat_id, msg_id, "🎒 <b>Инвентарь</b> — ✅ надето/в руках. Нажми на предмет.", kb(rows))

    async def item_card(self, chat_id, msg_id, ch, i):
        it = ch["data"]["items"][i]
        info = []
        if it.get("kind") == "weapon":
            info.append(f"⚔️ {it.get('cat', '')} оружие, урон {it.get('dmg')} {it.get('dtype', '')} {it.get('props', '')}")
        if it.get("kind") in ("armor", "shield"):
            info.append(f"🛡 КД {it.get('ac')}" + (f", ЛОВ максимум +{it['dexmax']}" if it.get("dexmax") else "") +
                        (f", нужна СИЛ {it['strreq']}" if it.get("strreq") else "") + (", помеха Скрытности" if it.get("stealth") else ""))
        if it.get("desc"):
            info.append(it["desc"])
        rows = []
        if it.get("kind") in ("weapon", "armor", "shield"):
            rows.append([btn("Снять" if it.get("eq") else "Надеть / взять в руки", f"eq:{i}")])
        if it.get("use") or it.get("kind") == "consumable" or it.get("max_charges"):
            rows.append([btn("🧪 Использовать", f"use:{i}")])
        rows.append([btn("−1 шт", f"q:{i}:-1"), btn("+1 шт", f"q:{i}:1"), btn("🗑 Выбросить", f"q:{i}:x")])
        rows.append([btn("⬅️ Инвентарь", "inv")])
        await self.edit_or_send(chat_id, msg_id, f"🎒 <b>{E(it['n'])}</b> ×{it.get('qty', 1)}\n" + "\n".join(E(x) for x in info), kb(rows))

    # ---------- умения ----------
    def my_feats(self, ch, q=""):
        D = engine.derive(ch["data"])
        fav = set(ch["data"].get("fav", []))
        lst = D["features"]
        if q:
            lst = [f for f in lst if q.lower() in f["n"].lower() or q.lower() in f["d"].lower()]
        return sorted(lst, key=lambda f: f["n"] not in fav)

    async def panel_feats(self, chat_id, msg_id, ch, page=0, q=""):
        lst = self.my_feats(ch, q)
        all_ = self.my_feats(ch)
        fav = set(ch["data"].get("fav", []))
        part = lst[page * PAGE:(page + 1) * PAGE]
        rows = [[btn(("⭐ " if f["n"] in fav else "") + f["n"], f"f:{all_.index(f)}")] for f in part]
        nav = []
        if page:
            nav.append(btn("◀", f"ft:{page - 1}"))
        if (page + 1) * PAGE < len(lst):
            nav.append(btn("▶", f"ft:{page + 1}"))
        rows += [nav, [btn("🔍 Поиск", "fts"), btn("⬅️ Меню", "main")]]
        await self.edit_or_send(chat_id, msg_id, "⭐ <b>Умения и особенности</b>" + (f" — «{E(q)}»" if q else ""), kb(rows))

    async def feat_card(self, chat_id, msg_id, ch, i):
        f = self.my_feats(ch)[i]
        D = engine.derive(ch["data"])
        rows = []
        key = next((v for k, v in FEATURE_RES.items() if f["n"].startswith(k)), None)
        if f["n"].startswith("Божественный канал"):
            key = "channel"
        r = next((r for r in D["res"] if r["key"] == key), None)
        if r:
            rows.append([btn(f"⚡ Использовать ({r['left']}/{r['max']})", f"uf:{i}")])
        fav = f["n"] in ch["data"].get("fav", [])
        rows.append([btn("☆ Убрать" if fav else "⭐ В избранное", f"fav:f:{i}"), btn("⬅️ Умения", "ft:0")])
        await self.edit_or_send(chat_id, msg_id, f"⭐ <b>{E(f['n'])}</b>\n<i>{E(f['src'])}</i>\n\n{E(f['d'])}", kb(rows))

    @staticmethod
    def feature_key(name: str) -> str | None:
        if name.startswith("Божественный канал"):
            return "channel"
        return next((v for k, v in FEATURE_RES.items() if name.startswith(k)), None)

    def feature_core(self, ch, name: str, actor: str) -> str:
        key = self.feature_key(name)
        extra = ""

        def fn(d):
            nonlocal extra
            out = engine.use_res(d, key)
            if key == "second_wind":
                r = dice.roll(f"1d10+{engine.class_level(d, 'fighter')}")
                extra = f"✚ +{r.total}"
                out += engine.heal(d, r.total)
            if key == "rage":
                engine.add_effect(d, "Ярость", "Варвар")
            return out
        _, log_ = self.g.mutate(ch["id"], fn, actor, f"умение «{name}»")
        return f"⚡ <b>{E(name)}</b> {extra}\n" + "\n".join(E(x) for x in log_)

    async def use_feature(self, chat_id, ch, i):
        f = self.my_feats(ch)[i]
        key = self.feature_key(f["n"])
        if self.in_combat(ch):
            return await self.propose(ch, "feature", {"name": f["n"]}, f"умение «{f['n']}»")
        if key == "lay_on_hands":
            self.db.set(f"inp:{ch['tg_id']}", {"op": "loh"})
            return await self.bot.send_message(chat_id, "✋ Сколько хитов вылечить на себе? (Союзнику — скажи мастеру.)")
        try:
            text = self.feature_core(ch, f["n"], self.g.who(ch))
        except ValueError as e:
            return await self.bot.send_message(chat_id, f"🤔 {E(str(e))}")
        await self.bot.send_message(chat_id, text, reply_markup=kb([[btn("⬅️ Меню", "main")]]))

    def item_core(self, ch, i: int, name: str, actor: str) -> str:
        items = ch["data"].get("items", [])
        if i >= len(items) or items[i]["n"] != name:
            i = next((k for k, x in enumerate(items) if x["n"] == name), None)
            if i is None:
                raise ValueError(f"«{name}» больше нет в инвентаре")
        res_txt = ""

        def use(d):
            nonlocal res_txt
            x = d["items"][i]
            out = []
            u = x.get("use", "")
            if x.get("max_charges"):
                if x.get("charges", 0) <= 0:
                    raise ValueError("Заряды кончились")
                x["charges"] -= 1
                out.append(f"заряды {x['charges']}/{x['max_charges']}")
            else:
                if x.get("qty", 1) <= 0:
                    raise ValueError("Не осталось")
                x["qty"] = x.get("qty", 1) - 1
            if u.startswith("heal:"):
                r = dice.roll(u[5:])
                res_txt = f"✚ {r.total}"
                out += engine.heal(d, r.total)
            elif u.startswith("dmg:"):
                r = dice.roll(u[4:])
                res_txt = f"💥 урон {r.total}"
            if x.get("qty", 1) <= 0 and not x.get("max_charges"):
                d["items"].pop(i)
            return out
        _, log_ = self.g.mutate(ch["id"], use, actor, f"использован «{name}»")
        return f"🧪 {E(name)} {res_txt}\n" + "\n".join(E(x) for x in log_)

    # ---------- ресурсы, отдых, состояния ----------
    async def panel_res(self, chat_id, msg_id, ch):
        D = engine.derive(ch["data"])
        rows = []
        for r in D["res"]:
            rows.append([btn(f"{r['n']} {r['left']}/{r['max']}", "noop"), btn("−", f"r:{r['key']}:1"), btn("+", f"r:{r['key']}:-1")])
        for lv, v in D["slots"].items():
            rows.append([btn(f"Ячейки {lv}: {v['left']}/{v['max']}", "noop"), btn("−", f"sl:{lv}:1"), btn("+", f"sl:{lv}:-1")])
        if D["pact"]:
            p = D["pact"]
            rows.append([btn(f"Договор {p['lvl']}: {p['left']}/{p['max']}", "noop"), btn("−", "sl:pact:1"), btn("+", "sl:pact:-1")])
        for h in D["hd"]:
            rows.append([btn(f"Кости хитов {h['die']}: {h['left']}/{h['max']}", "noop")])
        rows.append([btn("⬅️ Меню", "main")])
        await self.edit_or_send(chat_id, msg_id, "🔋 <b>Ресурсы</b>: − потратить, + вернуть.", kb(rows))

    async def panel_rest(self, chat_id, msg_id, ch, spend=None):
        D = engine.derive(ch["data"])
        spend = spend if spend is not None else (self.db.get(f"rest:{ch['tg_id']}") or [])
        rows = []
        for h in D["hd"]:
            left = h["left"] - spend.count(h["die"])
            rows.append([btn(f"🎲 Потратить {h['die']} (осталось {left})", f"hd:{h['die']}")])
        rows.append([btn(f"☕ Завершить короткий отдых ({len(spend)} костей)", "short")])
        rows.append([btn("🛌 Долгий отдых (8 часов)", "long")])
        rows.append([btn("⬅️ Меню", "main")])
        await self.edit_or_send(chat_id, msg_id,
                                "🏕 <b>Отдых</b>\n☕ <b>Короткий</b> (1 час): трать кости хитов — каждая лечит кость + ТЕЛ; "
                                "возвращаются ресурсы «за короткий отдых» и ячейки колдуна.\n"
                                "🛌 <b>Долгий</b> (8 часов, раз в сутки): все хиты, ячейки и ресурсы, половина костей хитов, −1 истощение.", kb(rows))

    async def panel_cond(self, chat_id, msg_id, ch):
        d = ch["data"]
        cur = set(d.get("conds", []))
        names = list(engine.CONDITIONS)
        rows = [[btn(("✅ " if n in cur else "") + n, f"cd:{i}") for i, n in enumerate(names)][j:j + 2] for j in range(0, len(names), 2)]
        rows.append([btn(f"Истощение {d.get('exh', 0)}", "noop"), btn("−", "ex:-1"), btn("+", "ex:1")])
        if d.get("conc"):
            rows.append([btn(f"🧠 Прервать концентрацию ({d['conc']})", "dropc")])
        for e_ in d.get("effects", []):
            rows.append([btn(f"✖ Снять «{e_['n']}»", f"rme:{e_['id']}")])
        rows.append([btn("⬅️ Меню", "main")])
        text = "🩹 <b>Состояния</b> — нажми, чтобы включить/выключить.\n\n" + "\n".join(
            f"<b>{n}</b>: {E(engine.CONDITIONS[n])}" for n in cur) + (f"\n\n<b>Истощение {d['exh']}</b>: {engine.EXHAUSTION[d['exh']]}" if d.get("exh") else "")
        await self.edit_or_send(chat_id, msg_id, text, kb(rows))

    # ---------- журнал, игры, карта ----------
    async def panel_journal(self, chat_id, msg_id, ch, q=""):
        entries = self.g.journal_for_players(ch["game_id"], q)
        rows = [[btn(f"{'📜' if e_['cat'] in ('session', 'plot') else '📝'} {e_['title'][:50] or 'Без названия'}", f"je:{e_['id']}")] for e_ in entries[:15]]
        rows.append([btn("⬅️ Меню", "main")])
        await self.edit_or_send(chat_id, msg_id, "📖 <b>Журнал партии</b> — записи, которые открыл мастер." + ("" if entries else "\n\nПока пусто."), kb(rows))

    async def panel_games(self, chat_id, msg_id, ch):
        rows = []
        cur = ch["game_id"] if ch else None
        for c in self.db.characters_of_player(chat_id):
            g = self.db.game(c["game_id"])
            rows.append([btn(("▶️ " if g["id"] == cur else "") + f"{g['name']} — {c['data'].get('name')}" + (" (архив)" if g["status"] == "archived" else ""), f"g:{g['id']}")])
        rows.append([btn("➕ Войти в новую игру по коду", "join")])
        await self.edit_or_send(chat_id, msg_id, "🎲 <b>Мои игры</b> — выбери, в какой играешь сейчас.", kb(rows))

    async def send_map(self, ch):
        sc = self.g.map.scene(ch["game_id"])
        if not sc or not sc.get("show"):
            return await self.bot.send_message(ch["tg_id"], "🗺 Мастер пока не показал карту.")
        sc["game_id"] = ch["game_id"]
        await self.g.map.send(ch, sc, ch["game_id"], force_new=True)

    # ---------- кнопки панелей ----------
    async def on_panel(self, q):
        uid = q["from"]["id"]
        msg = q.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id", uid)
        mid = msg.get("message_id")
        act, _, val = q.get("data", "")[2:].partition(":")
        ch = self.char(uid)
        if not ch:
            return await self.answer(q, "Нет персонажа")
        note = None
        if act in COMBAT_LOCK and self.in_combat(ch):
            return await self.answer(q, LOCK_MSG, alert=True)
        try:
            if act == "noop":
                pass
            elif act == "main":
                await self.panel_main(chat_id, mid, ch)
            elif act == "hpm":
                await self.panel_hp(chat_id, mid, ch)
            elif act == "sheet":
                await self.send_sheet(ch)
            elif act == "dice":
                await self.send_dice(ch)
            elif act == "info":
                await self.send_info(ch)
            elif act in ("dmg", "heal", "temp", "gold"):
                self.db.set(f"inp:{uid}", {"op": act})
                await self.bot.send_message(chat_id, {"dmg": "🗡 Сколько урона? Напиши число, можно с типом: <code>7 огонь</code>",
                                                      "heal": "✚ Сколько лечения?", "temp": "🛡 Сколько временных хитов?",
                                                      "gold": "💰 Сколько теперь золота?"}[act])
            elif act == "death":
                r = dice.roll("1d20")
                self.g.record(ch, "Спасбросок от смерти", r)
                _, log_ = self.mut(ch, lambda d: engine.death_save(d, r.total, r.total), "спасбросок от смерти")
                note = f"🎲 {r.total}: " + "; ".join(log_)
                await self.panel_main(chat_id, mid, self.db.character(ch["id"]))
            elif act == "sp":
                await self.panel_spells(chat_id, mid, ch, int(val or 0))
            elif act == "sps":
                self.db.set(f"inp:{uid}", {"op": "spell_search"})
                await self.bot.send_message(chat_id, "🔍 Напиши часть названия или описания заклинания.")
            elif act == "s":
                await self.spell_card(chat_id, mid, ch, int(val))
            elif act == "c":
                i, _, slot = val.partition(":")
                await self.do_cast(chat_id, mid, ch, int(i), slot)
            elif act == "healself":
                n = self.db.get(f"heal:{uid}") or 0
                self.db.delete(f"heal:{uid}")
                if n:
                    self.mut(ch, lambda d: engine.heal(d, n), f"лечение {n} (заклинание)")
                    note = f"✚ +{n}"
            elif act == "lr":
                await self.panel_learn(chat_id, mid, ch, int(val or 0))
            elif act == "lt":
                _, log_ = self.toggle_learn(ch, int(val))
                note = "; ".join(log_)
                await self.panel_learn(chat_id, mid, self.db.character(ch["id"]), int(val) // PAGE)
            elif act == "fav":
                kind, _, i = val.partition(":")
                name = (self.my_spells(ch) if kind == "s" else self.my_feats(ch))[int(i)]["n"]

                def fav(d):
                    f = d.setdefault("fav", [])
                    f.remove(name) if name in f else f.append(name)
                self.g.mutate(ch["id"], fav, self.g.who(ch), "избранное", journal=False)
                ch = self.db.character(ch["id"])
                await (self.spell_card(chat_id, mid, ch, [s["n"] for s in self.my_spells(ch)].index(name)) if kind == "s"
                       else self.feat_card(chat_id, mid, ch, [f["n"] for f in self.my_feats(ch)].index(name)))
            elif act == "inv":
                await self.panel_inv(chat_id, mid, ch)
            elif act == "it":
                await self.item_card(chat_id, mid, ch, int(val))
            elif act == "eq":
                i = int(val)

                def eq(d):
                    it = d["items"][i]
                    it["eq"] = not it.get("eq")
                    if it["eq"] and it.get("kind") in ("armor", "shield"):
                        for o in d["items"]:
                            if o is not it and o.get("kind") == it["kind"]:
                                o["eq"] = False
                    return [f"{it['n']}: {'надето' if it['eq'] else 'снято'}"]
                _, log_ = self.mut(ch, eq, "экипировка")
                note = log_[0]
                await self.item_card(chat_id, mid, self.db.character(ch["id"]), i)
            elif act == "use":
                i = int(val)
                it = ch["data"]["items"][i]
                if self.in_combat(ch):
                    await self.propose(ch, "item", {"i": i, "name": it["n"]}, f"использовать «{it['n']}»")
                else:
                    text = self.item_core(ch, i, it["n"], self.g.who(ch))
                    await self.bot.send_message(chat_id, text, reply_markup=kb([[btn("🎒 Вещи", "inv"), btn("⬅️ Меню", "main")]]))
            elif act == "q":
                i, _, dq = val.partition(":")
                i = int(i)

                def qty(d):
                    it = d["items"][i]
                    if dq == "x":
                        d["items"].pop(i)
                        return [f"выброшено: {it['n']}"]
                    it["qty"] = max(0, it.get("qty", 1) + int(dq))
                    return [f"{it['n']} ×{it['qty']}"]
                self.mut(ch, qty, "инвентарь")
                ch = self.db.character(ch["id"])
                await (self.panel_inv(chat_id, mid, ch) if dq == "x" else self.item_card(chat_id, mid, ch, i))
            elif act == "add":
                self.db.set(f"inp:{uid}", {"op": "add_item"})
                await self.bot.send_message(chat_id, "➕ Название предмета: из справочника (Зелье лечения, Длинный меч, Кольчуга…) или любое своё.")
            elif act == "ft":
                await self.panel_feats(chat_id, mid, ch, int(val or 0))
            elif act == "fts":
                self.db.set(f"inp:{uid}", {"op": "feat_search"})
                await self.bot.send_message(chat_id, "🔍 Что ищем в умениях?")
            elif act == "f":
                await self.feat_card(chat_id, mid, ch, int(val))
            elif act == "uf":
                await self.use_feature(chat_id, ch, int(val))
            elif act == "res":
                await self.panel_res(chat_id, mid, ch)
            elif act == "r":
                key, _, dlt = val.partition(":")
                _, log_ = self.mut(ch, lambda d: engine.use_res(d, key, int(dlt)), "ресурс")
                note = log_[0]
                await self.panel_res(chat_id, mid, self.db.character(ch["id"]))
            elif act == "sl":
                lv, _, dlt = val.partition(":")
                self.mut(ch, lambda d: engine.set_slot(d, lv, int(dlt)), "ячейки заклинаний")
                await self.panel_res(chat_id, mid, self.db.character(ch["id"]))
            elif act == "rest":
                self.db.set(f"rest:{uid}", [])
                await self.panel_rest(chat_id, mid, ch, [])
            elif act == "hd":
                spend = self.db.get(f"rest:{uid}") or []
                D = engine.derive(ch["data"])
                h = next(h for h in D["hd"] if h["die"] == val)
                if spend.count(val) >= h["left"]:
                    note = "Кости этого типа кончились"
                else:
                    spend.append(val)
                    self.db.set(f"rest:{uid}", spend)
                await self.panel_rest(chat_id, mid, ch, spend)
            elif act == "short":
                spend = self.db.get(f"rest:{uid}") or []
                self.db.delete(f"rest:{uid}")
                _, log_ = self.mut(ch, lambda d: engine.short_rest(d, spend), "короткий отдых")
                await self.edit_or_send(chat_id, mid, "☕ <b>Короткий отдых</b>\n" + "\n".join(E(x) for x in log_), kb([[btn("⬅️ Меню", "main")]]))
            elif act == "long":
                _, log_ = self.mut(ch, engine.long_rest, "долгий отдых")
                await self.edit_or_send(chat_id, mid, "🛌 <b>Долгий отдых</b>\n" + "\n".join(E(x) for x in log_), kb([[btn("⬅️ Меню", "main")]]))
            elif act == "cond":
                await self.panel_cond(chat_id, mid, ch)
            elif act == "cd":
                name = list(engine.CONDITIONS)[int(val)]

                def cd(d):
                    c = d.setdefault("conds", [])
                    if name in c:
                        c.remove(name)
                        return [f"снято: {name}"]
                    c.append(name)
                    return [f"наложено: {name}"]
                self.mut(ch, cd, f"состояние {name}")
                await self.panel_cond(chat_id, mid, self.db.character(ch["id"]))
            elif act == "ex":
                self.mut(ch, lambda d: d.update(exh=max(0, min(6, int(d.get("exh", 0)) + int(val)))) or [], "состояние: истощение")
                await self.panel_cond(chat_id, mid, self.db.character(ch["id"]))
            elif act == "dropc":
                self.mut(ch, engine.drop_conc, "концентрация прервана")
                await self.panel_cond(chat_id, mid, self.db.character(ch["id"]))
            elif act == "rme":
                self.mut(ch, lambda d: d.update(effects=[e_ for e_ in d.get("effects", []) if e_["id"] != val]) or [], "эффект снят")
                await self.panel_cond(chat_id, mid, self.db.character(ch["id"]))
            elif act == "lvl":
                await self.g.lvl.begin(chat_id, ch)
            elif act == "jr":
                await self.panel_journal(chat_id, mid, ch)
            elif act == "je":
                e_ = self.db.entry(int(val))
                if e_ and e_["game_id"] == ch["game_id"] and e_["vis"] == "all" and not e_["deleted"]:
                    await self.edit_or_send(chat_id, mid, f"📖 <b>{E(e_['title'])}</b>\n\n{E(e_['body'])}", kb([[btn("⬅️ Журнал", "jr")]]))
            elif act == "g":
                gid = int(val)
                if self.db.character_of(uid, gid):
                    self.db.set_active_game(uid, gid)
                    note = "Игра переключена"
                    await self.welcome(self.db.character_of(uid, gid))
            elif act == "join":
                await self.g.wiz.begin(chat_id, q["from"])
            elif act == "map":
                await self.send_map(ch)
            elif act == "undo":
                await self.do_undo(chat_id, ch)
            else:
                note = "?"
        except ValueError as ex:
            note = str(ex)
        await self.answer(q, note[:190] if note else None)

    # ---------- виджеты ----------
    async def on_widget_query(self, q: dict) -> None:
        qid, data, user = q["id"], q.get("data", ""), q["from"]
        cid, sep, rest = data.partition("|")
        if sep and cid.isdigit():
            ch = self.db.character(int(cid))
            if not ch or ch["tg_id"] != user["id"]:
                return await self.bot.answer_widget_query(qid, {"e": "Этот лист не твой"})
            data = rest
        else:
            ch = self.db.character_of(user["id"])
        if not ch:
            return await self.bot.answer_widget_query(qid, {"e": "У тебя нет персонажа — напиши боту /start"})
        if self.db.game(ch["game_id"])["status"] == "archived":
            return await self.bot.answer_widget_query(qid, {"e": "Игра в архиве"})
        try:
            answer = await self.widget_roll(ch, data)
        except (dice.DiceError, ValueError, KeyError, IndexError) as e:
            answer = {"e": str(e) or "Ошибка"}
        await self.bot.answer_widget_query(qid, answer)

    async def widget_roll(self, ch: dict, data: str) -> dict:
        kind, _, arg = data.partition(":")
        if kind.startswith("r") and len(kind) == 3:  # кости: r<n|a|d><флаг>:формула
            adv = {"n": 0, "a": 1, "d": -1}[kind[1]]
            return self.g.record(ch, "", dice.roll(arg, adv))["detail"]
        if kind in ("c", "s", "k", "i", "a"):
            return self.g.roll_check(ch, kind, arg)
        if kind == "n":
            asyncio.create_task(self.send_info(ch))
            return {"m": "📜 Отправил карточку в чат"}
        if kind == "p":
            asyncio.create_task(self.panel_main(ch["tg_id"], None, self.db.character(ch["id"])))
            return {"m": "☰ Панель — в чате ниже"}
        if kind == "q":
            rid, _, phys = arg.partition(":")
            return await self.g.answer_request(ch, int(rid), int(phys[1:]) if phys.startswith("m") else None)
        raise ValueError("Неизвестный запрос")

    # ---------- long polling ----------
    async def poll(self) -> None:
        self.g.me = await self.bot.call("getMe")
        log.info("bot @%s started", self.g.me.get("username"))
        await self.bot.call("setMyCommands", commands=[
            {"command": "menu", "description": "☰ Меню: всё о герое и действия"},
            {"command": "sheet", "description": "🎲 Лист с бросками"},
            {"command": "dice", "description": "🎲 Кости"},
            {"command": "games", "description": "Мои игры"},
            {"command": "new", "description": "Создать нового героя"},
            {"command": "help", "description": "Как пользоваться"},
        ])
        offset = self.db.get("tg_offset", 0)
        while True:
            try:
                updates = await self.bot.call("getUpdates", offset=offset, timeout=50,
                                              allowed_updates=["message", "callback_query", "widget_query", "shared_widget_query"])
            except Exception as e:
                log.warning("getUpdates failed: %s", e)
                await asyncio.sleep(3)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                asyncio.create_task(self.handle(u))
            if updates:
                self.db.set("tg_offset", offset)
