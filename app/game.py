"""Ядро игры: игры и права, изменения персонажей (с историей и отменой), броски, запросы мастера,
бой, журнал. Сайт и бот вызывают только эти методы — логика в одном месте."""
import asyncio
import copy
import html
import json
import logging
import secrets
import time

from . import dice, engine, widgets
from .db import DB, Conflict
from .data.monsters import MONSTERS
from .rules import ABILITIES, ABILITY_SHORT, SKILLS, TEXT_FIELDS, DEFAULT_CHAR
from .tg import Bot, TelegramError

log = logging.getLogger("game")
TEXT_LIMIT = 4000
E = html.escape

# изменения, которые сами попадают в журнал как механические события
JOURNAL_LABELS = ("урон", "лечение", "отдых", "уровень", "смерть", "создан", "состояние", "концентрац")


def compact_char(ch: dict, cid: int | None = None, D: dict | None = None) -> dict:
    """Персонаж для chat_data виджета листа (≤ 1024 байт). Все бонусы уже посчитаны сервером."""
    D = D or engine.derive(ch)
    conds = list(ch.get("conds", []))
    if ch.get("exh"):
        conds.append(f"Истощение {ch['exh']}")
    out = {
        "id": cid, "n": ch.get("name", "")[:40], "c": ch.get("cls", "")[:30], "r": ch.get("race", "")[:30],
        "l": D["lvl"], "hp": ch.get("hp", 10), "mhp": D["hp_max"]["val"], "tp": ch.get("temp", 0), "ac": D["ac"]["val"],
        "sp": D["spd"]["val"], "pb": D["pb"],
        "st": [D["ab"][a]["score"] for a in ABILITIES],
        "cb": [D["m"][a] for a in ABILITIES],
        "sb": [D["saves"][a]["bonus"] for a in ABILITIES],
        "sv": [i for i, a in enumerate(ABILITIES) if D["saves"][a]["prof"]],
        "kb": [s["bonus"] for s in D["skills"]],
        "sk": [i for i, s in enumerate(D["skills"]) if s["prof"]],
        "ib": D["init"]["bonus"],
        "a": [[a["n"], a["hit"], a["dmg"]] for a in D["attacks"]],
        "co": ", ".join(conds)[:80], "cn": (ch.get("conc") or "")[:30],
    }
    if ch.get("hp", 1) == 0:
        out["ds"] = ch.get("death", [0, 0])
    size = lambda: len(json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode())
    cut = 24
    while size() > 1024 and cut > 6:
        out["a"] = [[n[:cut], h, d] for n, h, d in out["a"]]
        cut -= 6
    while size() > 1024 and out["a"]:
        out["a"].pop()
    return out


def clean_char(raw: dict, old: dict) -> dict:
    """Правка персонажа с сайта: проверяем и ограничиваем поля. Механику v2 (классы, предметы) принимаем как есть,
    но с проверками типов."""
    ch = copy.deepcopy({**DEFAULT_CHAR, **old})

    def num(v, lo, hi, d):
        try:
            return max(lo, min(hi, int(v)))
        except (TypeError, ValueError):
            return d

    for k, limit in (("name", 40), ("cls", 30), ("race", 40), ("bg", 40), ("al", 30)):
        if k in raw:
            ch[k] = str(raw[k] or "").strip()[:limit]
    for k in TEXT_FIELDS:
        if k in raw:
            ch[k] = str(raw[k] or "").strip()[:TEXT_LIMIT]
    for k, lo, hi in (("lvl", 1, 20), ("hp", -99, 999), ("mhp", 1, 999), ("ac", 0, 40), ("spd", 0, 200),
                      ("temp", 0, 999), ("exh", 0, 6), ("gold", 0, 10 ** 7), ("xp", 0, 10 ** 7)):
        if k in raw:
            ch[k] = num(raw[k], lo, hi, ch.get(k, 0))
    if isinstance(raw.get("st"), dict):
        new = {a: num(raw["st"].get(a, ch["st"].get(a, 10)), 1, 30, 10) for a in ABILITIES}
        if engine.is_v2(ch):  # ручная правка характеристик — в разбивку как «правка мастера»
            for a in ABILITIES:
                d = new[a] - ch["st"].get(a, 10)
                if d:
                    ch.setdefault("st_log", []).append({"ab": a, "val": d, "src": "Правка мастера"})
        ch["st"] = new
    if isinstance(raw.get("sv"), list):
        ch["sv"] = [a for a in ABILITIES if a in raw["sv"]]
    if isinstance(raw.get("sv_extra"), list):
        ch["sv_extra"] = [a for a in ABILITIES if a in raw["sv_extra"]]
    for k in ("sk", "ex"):
        if isinstance(raw.get(k), list):
            ch[k] = sorted({int(i) for i in raw[k] if str(i).isdigit() and int(i) < len(SKILLS)})
    if isinstance(raw.get("conds"), list):
        ch["conds"] = [c for c in raw["conds"] if c in engine.CONDITIONS]
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
    if isinstance(raw.get("items"), list):
        items = []
        for it in raw["items"][:80]:
            if isinstance(it, dict) and str(it.get("n", "")).strip():
                it = {k: v for k, v in it.items() if k in ("n", "qty", "eq", "kind", "cat", "rng", "dmg", "dtype", "props", "akind", "ac",
                                                          "dexmax", "strreq", "stealth", "desc", "use", "bonus", "charges", "max_charges")}
                it["n"] = str(it["n"])[:60]
                it["qty"] = num(it.get("qty", 1), 0, 9999, 1)
                if it.get("dmg"):
                    try:
                        dice.parse(str(it["dmg"]))
                    except dice.DiceError:
                        raise ValueError(f"Урон предмета «{it['n']}» — не формула")
                items.append(it)
        ch["items"] = items
    if isinstance(raw.get("sp"), dict):
        from .data.spells import SPELLS
        ch["sp"] = {k: [s for s in raw["sp"].get(k, []) if s in SPELLS][:80] for k in ("known", "prep")}
    for k in ("profs",):
        if isinstance(raw.get(k), dict):
            ch[k] = {kk: [str(x)[:60] for x in v][:40] for kk, v in raw[k].items() if isinstance(v, list)}
    if isinstance(raw.get("feats"), list):
        ch["feats"] = [str(f)[:60] for f in raw["feats"]][:20]
    if isinstance(raw.get("custom_res"), list):
        ch["custom_res"] = [{"key": str(r.get("key") or engine.uid()), "n": str(r.get("n", ""))[:40],
                             "max": num(r.get("max"), 1, 999, 1), "reset": "short" if r.get("reset") == "short" else "long"}
                            for r in raw["custom_res"] if isinstance(r, dict) and r.get("n")][:20]
    return ch


def info_text(ch: dict, D: dict | None = None) -> list[str]:
    """Карточка персонажа текстом для Telegram (HTML), порезанная на сообщения ≤ 4096 символов."""
    D = D or engine.derive(ch)
    sub = " · ".join(x for x in (f"{ch.get('cls') or 'Без класса'}, {D['lvl']} ур.", ch.get("race"), ch.get("bg"), ch.get("al")) if x)
    hp = f"❤️ Хиты {ch.get('hp', 0)}/{D['hp_max']['val']}" + (f" (+{ch['temp']} врем.)" if ch.get("temp") else "")
    parts = [
        f"🧙 <b>{E(ch.get('name') or 'Безымянный герой')}</b>\n<i>{E(sub)}</i>\n\n"
        f"{hp} · 🛡 КД {D['ac']['val']} · 👣 {D['spd']['val']} фт\n"
        f"⚡ Инициатива {D['init']['bonus']:+d} · бонус мастерства +{D['pb']} · 👁 пассивная Внимательность {D['passive']['val']}",
        "<b>Характеристики</b>\n" + "\n".join(
            f"{ABILITY_SHORT[a]} {D['ab'][a]['score']} ({D['m'][a]:+d})"
            + (f" · спасбросок {D['saves'][a]['bonus']:+d} ●" if D["saves"][a]["prof"] else "") for a in ABILITIES),
    ]
    sk = [s for s in D["skills"] if s["prof"] or s["exp"]]
    if sk:
        parts.append("<b>Навыки</b>\n" + ", ".join(f"{s['n']} {s['bonus']:+d}" + ("★" if s["exp"] else "") for s in sk))
    if D["attacks"]:
        parts.append("<b>Атаки</b>\n" + "\n".join(
            f"⚔️ {E(a['n'])}: " + (f"{a['hit']:+d} к попаданию, " if a["hit"] is not None else "") + f"урон {E(a['dmg'])} {E(a.get('type', ''))}"
            for a in D["attacks"]))
    for c in D.get("cast", []):
        slots = " ".join(f"{k}:{v['left']}/{v['max']}" for k, v in D["slots"].items())
        parts.append(f"<b>Магия ({c['n']})</b>\nСЛ спасброска {c['dc']}, атака {c['atk']:+d}" + (f"\nЯчейки {slots}" if slots else "")
                     + (f"\nДоговор: {D['pact']['left']}/{D['pact']['max']} ({D['pact']['lvl']} круг)" if D["pact"] else ""))
        break
    if engine.is_v2(ch):
        names = engine.spell_list(ch)
        if names:
            parts.append("<b>Заклинания</b>\n" + ", ".join(E(n) for n in names))
        if D["res"]:
            parts.append("<b>Ресурсы</b>\n" + "\n".join(f"🔋 {r['n']}: {r['left']}/{r['max']}" for r in D["res"]))
        if D["features"]:
            parts.append("<b>Умения</b>\n" + "\n".join(f"• <b>{E(f['n'])}</b> <i>({E(f['src'])})</i>" for f in D["features"]))
        if ch.get("items"):
            parts.append("<b>Снаряжение</b>\n" + ", ".join(
                E(i["n"]) + (f" ×{i['qty']}" if i.get("qty", 1) > 1 else "") + (" ✓" if i.get("eq") else "") for i in ch["items"])
                + f"\n💰 {ch.get('gold', 0)} зм")
        pr = ch.get("profs", {})
        if pr:
            parts.append("<b>Владения</b>\n" + "\n".join(f"{t}: {', '.join(pr.get(k, [])) or '—'}" for k, t in
                                                        (("armor", "Доспехи"), ("weapons", "Оружие"), ("tools", "Инструменты"), ("langs", "Языки"))))
    for k, title in TEXT_FIELDS.items():
        if ch.get(k) and isinstance(ch[k], str):
            parts.append(f"<b>{title}</b>\n{E(ch[k])}")
    if D["warn"]:
        parts.append("⚠️ " + "\n⚠️ ".join(E(w) for w in D["warn"]))
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
        self.listeners: dict[asyncio.Queue, int | None] = {}
        self.me: dict = {}
        self._refresh: dict[int, asyncio.Task] = {}
        from .wizard import Wizard
        from .levelup import LevelUp
        from .mapview import MapView
        from .ui import BotUI
        self.wiz = Wizard(self)
        self.lvl = LevelUp(self)
        self.map = MapView(self)
        self.ui = BotUI(self)

    async def handle(self, upd: dict) -> None:
        await self.ui.handle(upd)

    async def poll(self) -> None:
        await self.ui.poll()

    # ---------- события для сайта ----------
    def emit(self, event: dict, gid: int | None = None) -> None:
        for q, g in list(self.listeners.items()):
            if g is None or gid is None or g == gid:
                q.put_nowait(event)

    def emit_chars(self, gid: int) -> None:
        self.emit({"type": "chars", "chars": self.public_chars(gid)}, gid)

    def emit_drafts(self, gid: int | None = None) -> None:
        for g in ([gid] if gid else [x["id"] for x in self.db.games()]):
            self.emit({"type": "drafts", "drafts": self.wiz.drafts(g)}, g)

    def link(self, start: str) -> str | None:
        u = self.me.get("username")
        return f"https://t.me/{u}?start={start}" if u else None

    def public_char(self, c: dict) -> dict:
        D = engine.derive(c["data"])
        trim = {k: D[k] for k in ("lvl", "pb", "ab", "saves", "skills", "init", "ac", "spd", "hp_max", "attacks", "cast",
                                  "slots", "pact", "res", "hd", "features", "warn", "passive", "resist", "v2")}
        trim["spells"] = engine.spell_list(c["data"]) if D["v2"] else []
        return {"id": c["id"], "tg_id": c["tg_id"], "player": c["player"], "char": c["data"], "rev": c["rev"],
                "link": self.link(c["token"]), "d": trim}

    def public_chars(self, gid: int) -> list[dict]:
        return [self.public_char(c) for c in self.db.characters(gid)]

    def state(self, gid: int) -> dict:
        g = self.db.game(gid)
        st = g["state"]
        return {
            "game": {k: g[k] for k in ("id", "name", "status", "lobby")},
            "games": [{k: x[k] for k in ("id", "name", "status", "lobby", "updated")} for x in self.db.games()],
            "chars": self.public_chars(gid), "rolls": self.db.rolls(gid, 200), "requests": self.db.requests(gid),
            "lobby_link": self.link(g["lobby"]) if g["lobby"] else None, "bot": self.me.get("username"),
            "drafts": self.wiz.drafts(gid), "combat": st.get("combat"), "session": st.get("session", 0),
            "scene": st.get("scene"), "scenes": [{"id": s["id"], "name": s["name"]} for s in self.db.scenes(gid)],
            "skills": [s[0] for s in SKILLS], "monsters": sorted(MONSTERS), "effects": sorted(engine.EFFECTS),
            "conditions": engine.CONDITIONS, "adj_fields": engine.ADJ_FIELDS,
        }

    # ---------- игры ----------
    def create_game(self, name: str) -> dict:
        g = self.db.create_game(name.strip()[:60] or "Новая игра")
        self.emit({"type": "games"})
        return g

    def set_game_status(self, gid: int, status: str) -> None:
        self.db.update_game(gid, status=status)
        self.emit({"type": "games"})

    def game_state(self, gid: int) -> dict:
        return self.db.game(gid)["state"]

    def save_state(self, gid: int, st: dict) -> None:
        self.db.update_game(gid, state=st)

    # ---------- изменения персонажа ----------
    def mutate(self, cid: int, fn, actor: str, label: str, rev: int | None = None, journal: bool | None = None) -> tuple[dict, list[str]]:
        """Единственная точка изменения персонажа: снимок «до», применение, пересчёт, сохранение,
        история (для отмены), событие на сайт, обновление листов в Telegram, механика в журнал."""
        c = self.db.character(cid)
        if not c:
            raise KeyError(cid)
        before = copy.deepcopy(c["data"])
        data = copy.deepcopy(c["data"])
        out = fn(data) or []
        engine.sync_legacy(data)
        self.db.save_character(cid, data, rev)
        self.db.add_history(c["game_id"], cid, actor, label + (": " + "; ".join(out) if out else ""), before, data)
        gid = c["game_id"]
        self.emit_chars(gid)
        self.schedule_refresh(cid)
        if journal or (journal is None and any(w in label.lower() for w in JOURNAL_LABELS)):
            self.auto_journal(gid, f"{data.get('name') or 'Персонаж'}: {label}", "; ".join(out), cid)
        return data, out

    def undo(self, cid: int, actor: str) -> str:
        h = self.db.last_history(cid)
        if not h:
            raise ValueError("Нечего отменять")
        c = self.db.character(cid)
        self.db.save_character(cid, json.loads(h["before"]))
        self.db.mark_undone(h["id"])
        self.db.add_history(c["game_id"], cid, actor, f"↩️ отмена: {h['label']}", c["data"], json.loads(h["before"]))
        self.db.mark_undone(self.db.last_history(cid)["id"])  # саму отмену не отменяем повторно цепочкой
        self.emit_chars(c["game_id"])
        self.schedule_refresh(cid)
        return h["label"]

    def schedule_refresh(self, cid: int) -> None:
        """Обновить листы игрока в Telegram (с задержкой, чтобы серия правок дала одно обновление)."""
        t = self._refresh.get(cid)
        if t and not t.done():
            t.cancel()
        try:
            self._refresh[cid] = asyncio.get_running_loop().create_task(self._refresh_later(cid))
        except RuntimeError:
            pass

    async def _refresh_later(self, cid: int) -> None:
        await asyncio.sleep(0.8)
        await self.refresh_sheets(cid)

    async def refresh_sheets(self, cid: int) -> None:
        ch = self.db.character(cid)
        if not ch or not ch["tg_id"]:
            return
        data = compact_char(ch["data"], cid)
        for mid in ch["sheet_msgs"]:
            try:
                await self.bot.edit_widget_data(ch["tg_id"], mid, data)
            except TelegramError as e:
                log.info("sheet %s not updated: %s", mid, e.description)

    def create_char(self, gid: int, raw: dict, tg_id: int | None = None) -> dict:
        data = raw if raw.get("v") == 2 else clean_char(raw, DEFAULT_CHAR)
        engine.sync_legacy(data)
        ch = self.db.create_character(data, gid=gid)
        if tg_id:
            self.db.bind(ch["id"], tg_id)
        self.emit_chars(gid)
        self.auto_journal(gid, f"Новый персонаж: {data.get('name')}", f"{data.get('race', '')} {data.get('cls', '')}".strip(), ch["id"])
        return self.db.character(ch["id"])

    async def update_char(self, cid: int, raw: dict, rev: int | None = None, actor: str = "Мастер") -> dict:
        data, _ = self.mutate(cid, lambda d: d.update(clean_char(raw, d)), actor, "правка листа", rev, journal=False)
        return data

    # ---------- броски ----------
    @staticmethod
    def who(ch: dict) -> str:
        return ch["data"].get("name") or (ch["player"] or {}).get("first_name") or "Игрок"

    def record(self, ch: dict | None, title: str, r: dice.Roll, extra: dict | None = None, hidden: int = 0,
               who: str | None = None, gid: int | None = None) -> dict:
        detail = {**r.to_dict(), "txt": r.dice_text(), **(extra or {})}
        gid = gid or (ch["game_id"] if ch else None)
        entry = self.db.add_roll(ch["tg_id"] if ch else None, who or (self.who(ch) if ch else "Мастер"), title, r.formula,
                                 r.total, detail, gid, ch["id"] if ch else None, hidden)
        self.emit({"type": "roll", "roll": entry}, gid)
        return entry

    def gm_roll(self, gid: int, formula: str, title: str = "", adv: int = 0) -> dict:
        return self.record(None, title, dice.roll(formula, adv), who="Мастер", gid=gid, hidden=1)

    def roll_check(self, ch: dict, kind: str, key: str, physical: int | None = None, hidden: int = 0,
                   title: str | None = None, dc: int | None = None) -> dict:
        """Бросок по листу через движок. physical — выпавшее на настоящем d20 (сервер добавит модификатор)."""
        spec = engine.roll_spec(ch["data"], kind, key)
        t = title or spec["title"]
        if spec.get("formula") is None:  # только урон
            r = dice.roll(spec["dmg"])
            return {**self.record(ch, t, r, hidden=hidden)["detail"], "note": spec.get("note", "")}
        if spec["formula"] == "0":
            r = dice.roll("0")
            entry = self.record(ch, t, r, {"note": spec.get("note")}, hidden)
            return {**entry["detail"], "m": spec.get("note")}
        if physical is not None:
            r = dice.manual(spec["formula"], physical)
        else:
            r = dice.roll(spec["formula"], spec["adv"])
        extra = {}
        if dc:
            extra["dc"], extra["ok"] = dc, r.total >= dc
        if spec.get("adv"):
            extra["adv"] = spec["adv"]
        if physical is not None:
            extra["phys"] = 1
        if kind == "a" and spec.get("dmg"):
            nat = r.d20 or 0
            crit = nat >= spec.get("crit", 20)
            dmg = dice.roll(spec["dmg"], crit=crit)
            extra["d"] = {**dmg.to_dict(), "txt": dmg.dice_text()}
            extra["ap"] = {"k": "dmg", "n": dmg.total}
            if crit:
                extra["c"] = 1
        entry = self.record(ch, t, r, extra, hidden)
        if kind == "i":
            self.init_from_roll(ch, r.total)
        once = engine.consume_once(ch["data"], {"a": "attack", "s": "save"}.get(kind, "check"))
        if once:
            self.mutate(ch["id"], lambda d: engine.consume_once(d, {"a": "attack", "s": "save"}.get(kind, "check")), "система", "бонус использован")
        out = {**r.to_dict(), **({"c": 1} if extra.get("c") else {})}
        if "d" in extra:
            out["d"] = {k: extra["d"][k] for k in ("f", "t", "r", "s", "b", "c")}
        if spec.get("adv"):
            out["adv"] = spec["adv"]
        return out

    # ---------- запросы мастера ----------
    async def ask_roll(self, gid: int, cids: list[int], kind: str, title: str, vis: str = "player", dc: int | None = None) -> list[int]:
        """kind: c:str | s:dex | k:3 | i:0 | f:2d6+1. vis: player (видит игрок и мастер) | gm (тайный) | all (всем игрокам)."""
        sent = []
        grp = secrets.token_hex(3)
        for cid in cids:
            ch = self.db.character(cid)
            if not ch or not ch["tg_id"] or ch["game_id"] != gid:
                continue
            t = title or self.kind_title(ch["data"], kind)
            rid = self.db.add_request(ch["tg_id"], t, kind, gid, cid, grp, vis, dc)
            try:
                msg = await self.bot.send_widget(ch["tg_id"], widgets.load("request"),
                                                 chat_data={"i": rid, "t": t, "h": 1 if vis == "gm" else 0})
                self.db.update_request(rid, msg_id=msg["message_id"])
                sent.append(cid)
            except TelegramError as e:
                log.warning("request to %s failed: %s", ch["tg_id"], e)
                self.db.update_request(rid, status="failed")
        self.emit({"type": "requests", "requests": self.db.requests(gid)}, gid)
        return sent

    async def cancel_request(self, rid: int) -> bool:
        req = self.db.request(rid)
        if not req or req["status"] != "pending":
            return False
        self.db.update_request(rid, status="cancelled")
        if req["msg_id"]:
            try:
                await self.bot.edit_widget_data(req["tg_id"], req["msg_id"], {"i": rid, "t": req["title"], "z": 1})
            except TelegramError:
                pass
        self.emit({"type": "requests", "requests": self.db.requests(req["game_id"])}, req["game_id"])
        return True

    @staticmethod
    def kind_title(ch: dict, kind: str) -> str:
        k, _, v = kind.partition(":")
        if k == "f":
            return f"Бросок {v}"
        return engine.roll_spec(ch, k, v)["title"]

    async def answer_request(self, ch: dict, rid: int, physical: int | None = None) -> dict:
        req = self.db.request(rid)
        if not req or req["tg_id"] != ch["tg_id"] or req["game_id"] != ch["game_id"]:
            raise ValueError("Этот запрос не тебе")
        if req["status"] == "cancelled":
            raise ValueError("Мастер отменил запрос")
        if req["roll_id"]:
            raise ValueError("Уже брошено")
        self.db.update_request(rid, status="answering")  # защита от двойного нажатия
        k, _, v = req["kind"].partition(":")
        hidden = 1 if req["vis"] == "gm" else 0
        if k == "f":
            r = dice.manual(v, physical) if physical is not None else dice.roll(v)
            ex = {"phys": 1} if physical is not None else {}
            if req.get("dc"):
                ex.update(dc=req["dc"], ok=r.total >= req["dc"])
            entry = self.record(ch, req["title"], r, ex, hidden)
            res = r.to_dict()
        else:
            res = self.roll_check(ch, k, v, physical, hidden, req["title"], req.get("dc"))
            entry = self.db.rolls(ch["game_id"], 1)[-1]
        self.db.update_request(rid, roll_id=entry["id"], status="done", ans_ts=time.time())
        shown = {"t": "?", "f": "", "r": [], "s": [], "b": 0, "c": 0, "h": 1} if hidden else res
        if req["msg_id"]:
            try:
                await self.bot.edit_widget_data(ch["tg_id"], req["msg_id"], {"i": rid, "t": req["title"], "x": shown})
            except TelegramError as e:
                log.info("request widget not updated: %s", e)
        if req["vis"] == "all":
            for o in self.db.characters(ch["game_id"]):
                if o["tg_id"] and o["tg_id"] != ch["tg_id"]:
                    try:
                        await self.bot.send_message(o["tg_id"], f"🎲 {E(self.who(ch))} — {E(req['title'])}: <b>{res['t']}</b>")
                    except TelegramError:
                        pass
        self.emit({"type": "requests", "requests": self.db.requests(ch["game_id"])}, ch["game_id"])
        return shown

    async def message_players(self, gid: int, cids: list[int], text: str) -> int:
        n = 0
        for cid in cids:
            ch = self.db.character(cid)
            if not ch or not ch["tg_id"] or ch["game_id"] != gid:
                continue
            try:
                await self.bot.send_message(ch["tg_id"], f"🧙 <b>Мастер ({E(self.db.game(gid)['name'])}):</b> " + E(text))
                n += 1
            except TelegramError as e:
                log.warning("message to %s failed: %s", ch["tg_id"], e)
        return n

    async def notify(self, ch: dict, text: str, **kw) -> None:
        if ch and ch["tg_id"]:
            try:
                await self.bot.send_message(ch["tg_id"], text, **kw)
            except TelegramError as e:
                log.info("notify failed: %s", e)

    # ---------- журнал ----------
    def auto_journal(self, gid: int, title: str, body: str = "", cid: int | None = None) -> None:
        st = self.game_state(gid)
        self.db.add_entry(gid, cat="mech", title=title[:200], body=body[:2000], vis="gm", author="Система",
                          char_id=cid, session=st.get("session") or None, scene_id=st.get("scene"), auto=1)
        self.emit({"type": "journal"}, gid)

    def journal_for_players(self, gid: int, q: str = "") -> list[dict]:
        return self.db.journal(gid, q=q, vis="all", limit=30)

    # ---------- бой ----------
    def combat(self, gid: int) -> dict:
        return self.game_state(gid).get("combat") or {"on": False, "round": 0, "turn": -1, "list": []}

    def save_combat(self, gid: int, cb: dict) -> None:
        st = self.game_state(gid)
        st["combat"] = cb
        self.save_state(gid, st)
        self.emit({"type": "combat", "combat": cb}, gid)

    @staticmethod
    def _sort(cb):
        cur = cb["list"][cb["turn"]]["id"] if 0 <= cb["turn"] < len(cb["list"]) else None
        cb["list"].sort(key=lambda x: (-(x.get("init") if x.get("init") is not None else -99), -x.get("dex", 0), x["name"]))
        if cur:
            cb["turn"] = next(i for i, x in enumerate(cb["list"]) if x["id"] == cur)

    def combat_add_pc(self, gid: int, cid: int) -> dict:
        cb = self.combat(gid)
        if any(x.get("cid") == cid for x in cb["list"]):
            return cb
        c = self.db.character(cid)
        cb["list"].append({"id": engine.uid(), "kind": "pc", "cid": cid, "name": c["data"].get("name") or "Игрок",
                           "init": None, "dex": engine.derive(c["data"])["m"]["dex"]})
        self._sort(cb)
        self.save_combat(gid, cb)
        return cb

    def combat_add_npc(self, gid: int, name: str, template: str | None = None, count: int = 1, hidden: bool = False,
                       hp: int | None = None, ac: int | None = None, init_bonus: int = 0) -> dict:
        cb = self.combat(gid)
        t = MONSTERS.get(template or "")
        for i in range(max(1, min(count, 20))):
            if t:
                ac_, hpf, ib, spd, cr, attacks, note = t
                mhp = max(1, dice.roll(hpf).total)
                n = f"{name or template}" + (f" {i + 1}" if count > 1 else "")
                ent = {"id": engine.uid(), "kind": "npc", "name": n, "ac": ac_, "hp": mhp, "mhp": mhp, "temp": 0, "ib": ib,
                       "atk": [list(a) for a in attacks], "note": note, "cr": cr, "spd": spd}
            else:
                mhp = hp or 10
                ent = {"id": engine.uid(), "kind": "npc", "name": (name or "Противник") + (f" {i + 1}" if count > 1 else ""),
                       "ac": ac or 12, "hp": mhp, "mhp": mhp, "temp": 0, "ib": init_bonus, "atk": [], "note": ""}
            ent.update(init=dice.roll(f"1d20{ent['ib']:+d}").total, dex=ent["ib"], conds=[], effects=[], hidden=hidden)
            cb["list"].append(ent)
        self._sort(cb)
        self.save_combat(gid, cb)
        return cb

    async def combat_start(self, gid: int, ask: bool = True) -> dict:
        cb = self.combat(gid)
        cb.update(on=True, round=1, turn=-1)
        for c in self.db.characters(gid):
            self.db.delete(f"srest:{c['id']}")
        for c in self.db.characters(gid):
            if c["tg_id"] and not any(x.get("cid") == c["id"] for x in cb["list"]):
                cb["list"].append({"id": engine.uid(), "kind": "pc", "cid": c["id"], "name": c["data"].get("name") or "Игрок",
                                   "init": None, "dex": engine.derive(c["data"])["m"]["dex"]})
        self._sort(cb)
        self.save_combat(gid, cb)
        self.auto_journal(gid, "⚔️ Начался бой", ", ".join(x["name"] for x in cb["list"]))
        for x in cb["list"]:
            if x["kind"] == "pc":
                await self.notify(self.db.character(x["cid"]), "⚔️ <b>Бой!</b> Сейчас придёт запрос инициативы.\n"
                                  "В свой ход объявляй действие и бросай. Урон, лечение и состояния применяет мастер.")
        if ask:
            await self.ask_roll(gid, [x["cid"] for x in cb["list"] if x["kind"] == "pc"], "i:0", "Инициатива")
        return cb

    def combat_set_init(self, gid: int, eid: str, value: int) -> None:
        cb = self.combat(gid)
        for x in cb["list"]:
            if x["id"] == eid:
                x["init"] = value
        self._sort(cb)
        self.save_combat(gid, cb)

    def init_from_roll(self, ch: dict, total: int) -> None:
        cb = self.combat(ch["game_id"])
        if not cb.get("on"):
            return
        for x in cb["list"]:
            if x.get("cid") == ch["id"] and x.get("init") is None:
                x["init"] = total
                self._sort(cb)
                self.save_combat(ch["game_id"], cb)
                return

    async def combat_next(self, gid: int) -> dict:
        cb = self.combat(gid)
        if not cb["list"]:
            return cb
        cb["turn"] += 1
        if cb["turn"] >= len(cb["list"]):
            cb["turn"] = 0
            cb["round"] += 1
        cur = cb["list"][cb["turn"]]
        msgs = []
        if cur["kind"] == "pc":
            c = self.db.character(cur["cid"])
            if c:
                _, msgs = self.mutate(c["id"], engine.tick_effects, "бой", "начало хода", journal=False) if c["data"].get("effects") else (None, [])
                await self.notify(c, f"⚔️ <b>Твой ход!</b> Раунд {cb['round']}." + ("\n" + "\n".join(E(m) for m in msgs) if msgs else ""))
        else:
            keep = []
            for e in cur.get("effects", []):
                if e.get("rounds"):
                    e["rounds"] -= 1
                    if e["rounds"] <= 0:
                        msgs.append(f"{cur['name']}: «{e['n']}» закончился")
                        continue
                keep.append(e)
            cur["effects"] = keep
        cb["moved"] = {}
        self.save_combat(gid, cb)
        self.map.schedule_push(gid)
        return cb

    async def combat_end(self, gid: int) -> None:
        cb = self.combat(gid)
        self.auto_journal(gid, f"🏁 Бой окончен (раундов: {cb.get('round', 0)})",
                          ", ".join(f"{x['name']} {x.get('hp', '')}/{x.get('mhp', '')}" for x in cb["list"] if x["kind"] == "npc"))
        for x in cb["list"]:
            if x["kind"] == "pc":
                await (self.notify(self.db.character(x["cid"]), "🏁 Бой окончен."))
        self.save_combat(gid, {"on": False, "round": 0, "turn": -1, "list": []})

    def npc_update(self, gid: int, eid: str, **kw) -> dict:
        cb = self.combat(gid)
        for x in cb["list"]:
            if x["id"] == eid:
                if "delta" in kw:
                    d = int(kw.pop("delta"))
                    if d < 0:
                        t = min(x.get("temp", 0), -d)
                        x["temp"] = x.get("temp", 0) - t
                        x["hp"] = max(0, x["hp"] + d + t)
                    else:
                        x["hp"] = min(x["mhp"], x["hp"] + d)
                for k in ("name", "ac", "hp", "mhp", "temp", "init", "hidden", "note", "conds"):
                    if k in kw:
                        x[k] = kw[k]
                if "effect" in kw:
                    x.setdefault("effects", []).append({"id": engine.uid(), "n": kw["effect"], "rounds": kw.get("rounds")})
                if "rm_effect" in kw:
                    x["effects"] = [e for e in x.get("effects", []) if e["id"] != kw["rm_effect"]]
        if "init" in kw:
            self._sort(cb)
        self.save_combat(gid, cb)
        return cb

    def combat_remove(self, gid: int, eid: str) -> None:
        cb = self.combat(gid)
        idx = next((i for i, x in enumerate(cb["list"]) if x["id"] == eid), None)
        if idx is None:
            return
        cb["list"].pop(idx)
        if idx < cb["turn"] or cb["turn"] >= len(cb["list"]):
            cb["turn"] = max(-1, cb["turn"] - 1)
        self.save_combat(gid, cb)

    def npc_roll(self, gid: int, eid: str, idx: int) -> dict:
        x = next(x for x in self.combat(gid)["list"] if x["id"] == eid)
        n, hit, dmg = x["atk"][idx]
        if hit:
            r = dice.roll(f"1d20{hit:+d}")
            d = dice.roll(dmg, crit=r.crit)
            return self.record(None, f"{x['name']}: {n}", r, {"d": {**d.to_dict(), "txt": d.dice_text()}, "ap": {"k": "dmg", "n": d.total}},
                               hidden=1, who=x["name"], gid=gid)
        d = dice.roll(dmg)
        return self.record(None, f"{x['name']}: {n}", d, {"ap": {"k": "dmg", "n": d.total}}, hidden=1, who=x["name"], gid=gid)

    # ---------- отдых партии (объявляет мастер) ----------
    async def party_rest(self, gid: int, kind: str, cids: list[int] | None = None) -> list[str]:
        out = []
        for c in self.db.characters(gid):
            if cids and c["id"] not in cids:
                continue
            if not engine.is_v2(c["data"]) and kind == "short":
                continue
            if kind == "long":
                self.db.delete(f"srest:{c['id']}")
                _, log_ = self.mutate(c["id"], engine.long_rest, "Мастер", "долгий отдых", journal=False)
                await self.notify(c, "🛌 <b>Долгий отдых.</b> Хиты, ячейки и умения восстановлены.")
            else:
                _, log_ = self.mutate(c["id"], lambda d: engine.short_rest(d, []), "Мастер", "короткий отдых", journal=False)
                self.db.set(f"srest:{c['id']}", 1)
                if c["tg_id"]:
                    try:
                        await self.bot.send_message(c["tg_id"], "☕ <b>Короткий отдых.</b> Умения «до короткого отдыха» восстановлены.\n"
                                                    "Хочешь подлечиться — потрать кости хитов:",
                                                    reply_markup={"inline_keyboard": [[{"text": "☕ Потратить кости хитов", "callback_data": "p:rest"}]]})
                    except TelegramError:
                        pass
            out.append(c["data"].get("name") or "?")
        self.auto_journal(gid, f"{'🛌 Долгий' if kind == 'long' else '☕ Короткий'} отдых", ", ".join(out))
        return out

    # ---------- фоновые задачи ----------
    async def backups(self, folder: str, keep: int = 14) -> None:
        import os
        os.makedirs(folder, exist_ok=True)
        while True:
            try:
                name = os.path.join(folder, time.strftime("dnd-%Y%m%d.sqlite3"))
                if not os.path.exists(name):
                    self.db.backup(name)
                    log.info("backup %s", name)
                files = sorted(f for f in os.listdir(folder) if f.startswith("dnd-"))
                for f in files[:-keep]:
                    os.remove(os.path.join(folder, f))
            except Exception:
                log.exception("backup failed")
            await asyncio.sleep(3600)
