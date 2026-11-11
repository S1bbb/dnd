"""Пошаговое повышение уровня в боте. Уровень выдаёт мастер (на сайте «⬆ Дать уровень»),
игрок проходит шаги: класс (или мультикласс) → хиты → подкласс → увеличение характеристик/черта →
выборы умений → компетентность → новые заклинания → подтверждение. Применяет engine.level_up."""
import copy
import html

from . import engine
from .data.backgrounds import FEATS
from .data.choices import CHOICES, options
from .data.classes import CLASSES, class_info
from .data.spells import SPELLS, for_class
from .rules import ABILITIES, ABILITY_RU, ABILITY_SHORT, SKILLS

e = html.escape
FEAT_KEYS = sorted(FEATS)
PAGE = 12


def kb(rows) -> dict:
    return {"inline_keyboard": [[{"text": t, "callback_data": ("l:" + d)[:64]} for t, d in row] for row in rows if row]}


def chunks(items, n):
    return [items[i:i + n] for i in range(0, len(items), n)]


class LevelUp:
    def __init__(self, game):
        self.game = game
        self.db = game.db
        self.bot = game.bot

    def get(self, uid):
        return self.db.get(f"lvl:{uid}")

    def put(self, uid, w):
        self.db.set(f"lvl:{uid}", w)

    def drop(self, uid):
        self.db.delete(f"lvl:{uid}")

    async def begin(self, chat_id: int, ch: dict) -> None:
        if int(ch["data"].get("lvl_pending", 0)) <= 0:
            await self.bot.send_message(chat_id, "Повышение уровня выдаёт мастер. Когда он даст уровень, я напишу.")
            return
        if not engine.is_v2(ch["data"]):
            await self.bot.send_message(chat_id, "Этого персонажа ведёт мастер на сайте — попроси его поднять уровень там.")
            return
        w = {"cid": ch["id"], "step": "cls", "cls": None, "c": {}, "hist": [], "page": 0}
        self.put(ch["tg_id"], w)
        await self.show(chat_id, w)

    # ---------- что нужно выбрать ----------
    def plan(self, w):
        ch = self.db.character(w["cid"])["data"]
        return ch, engine.level_up_plan(ch, w["cls"])

    def new_spells(self, w) -> tuple[int, int, list[dict]]:
        """(новых заговоров, новых заклинаний, список доступных) после повышения."""
        ch, plan = self.plan(w)
        ci = class_info(w["cls"], (next((c for c in ch["classes"] if c["c"] == w["cls"]), {}) or {}).get("sub") or w["c"].get("sub"))
        if not ci.get("caster"):
            return 0, 0, []
        old, new = plan["lvl"] - 1, plan["lvl"]
        nc = (ci["cantrips"](new) - (ci["cantrips"](old) if old else 0)) if ci.get("cantrips") else 0
        if ci.get("spellbook"):
            ns = 2 if old else 6
        elif ci.get("known"):
            ns = ci["known"](new) - (ci["known"](old) if old else 0)
        else:
            ns = 0
        mx = engine._max_spell_level(ci["caster"], new)
        src = w["cls"] if ci.get("caster") != "third" else "wizard"
        have = set(ch["sp"].get("known", [])) | set(ch["sp"].get("prep", []))
        nc, ns = max(0, nc), max(0, ns)
        pool = [s for s in for_class(src, mx) if s["n"] not in have and ((s["lvl"] == 0 and nc) or (s["lvl"] > 0 and ns))]
        return nc, ns, pool

    def steps(self, w) -> list[str]:
        ch, plan = self.plan(w)
        out = ["cls", "hp"]
        if plan["need"]["sub"]:
            out.append("sub")
        if plan["need"]["asi"]:
            out.append("asi")
        if plan["need"]["picks"]:
            out.append("picks")
        if plan["need"]["ex"]:
            out.append("ex")
        nc, ns, _ = self.new_spells(w)
        if nc or ns:
            out.append("spells")
        return out + ["confirm"]

    def advance(self, w):
        w["hist"].append({k: copy.deepcopy(v) for k, v in w.items() if k != "hist"})
        st = self.steps(w)
        w["step"] = st[st.index(w["step"]) + 1] if w["step"] in st else "confirm"
        w["page"] = 0

    # ---------- отрисовка ----------
    def render(self, w):
        ch = self.db.character(w["cid"])["data"]
        back = [("⬅️ Назад", "back:0")] if w["hist"] else []
        step = w["step"]
        if step == "cls":
            rows, lines = [], []
            for c in ch["classes"]:
                rows.append([(f"⬆ {CLASSES[c['c']]['n']} {c['lvl']} → {c['lvl'] + 1}", f"cls:{c['c']}")])
            for k, c in CLASSES.items():
                if any(x["c"] == k for x in ch["classes"]):
                    continue
                ok, why = engine.multiclass_ok(ch, k)
                lines.append(f"{'✅' if ok else '🚫'} {c['n']}" + ("" if ok else f" — {why}"))
                if ok:
                    rows.append([(f"✨ Мультикласс: {c['n']}", f"cls:{k}")])
            rows.append([("Отмена", "cancel:0")])
            return ("📈 <b>Новый уровень!</b>\nОбычно развивают текущий класс. Мультикласс — взять уровень другого класса "
                    "(нужно 13 в основных характеристиках обоих).\n\n" + "\n".join(lines), kb(rows))
        _, plan = self.plan(w)
        ci = CLASSES[w["cls"]]
        if step == "hp":
            avg = ci["hd"] // 2 + 1
            return (f"❤️ <b>Хиты</b> за {plan['lvl']} уровень {ci['n']}: d{ci['hd']} + ТЕЛ.\nСреднее ({avg}) — надёжно, бросок — азарт.",
                    kb([[(f"📊 Среднее {avg} ⭐", "hp:avg"), (f"🎲 Бросить d{ci['hd']}", "hp:roll")], back]))
        if step == "sub":
            lines = [f"<b>{s['n']}</b> — {s.get('about', '')}" for s in ci["subs"].values()]
            return (f"✨ <b>{ci['sub_title']}</b>\n\n" + "\n".join(lines),
                    kb(chunks([(s["n"], f"sub:{k}") for k, s in ci["subs"].items()], 2) + [back]))
        if step == "asi":
            c = w["c"]
            if c.get("asi_mode") == "feat":
                page = w.get("page", 0)
                keys = FEAT_KEYS[page * PAGE:(page + 1) * PAGE]
                lines = [f"<b>{k}</b>{' (' + FEATS[k]['req'] + ')' if FEATS[k].get('req') else ''} — {FEATS[k]['d']}" for k in keys]
                rows = chunks([(k, f"ft:{FEAT_KEYS.index(k)}") for k in keys], 2)
                rows.append([x for x in (("◀", "pg:-") if page else None, ("▶", "pg:+") if (page + 1) * PAGE < len(FEAT_KEYS) else None) if x])
                rows.append([("↩️ Лучше характеристики", "mode:asi")])
                rows.append(back)
                return ("🏅 <b>Черта</b>\n\n" + "\n".join(lines), kb(rows))
            if c.get("feat") and (FEATS[c["feat"]].get("asi_any") or FEATS[c["feat"]].get("asi_choose")) and not c.get("feat_ab"):
                abs_ = FEATS[c["feat"]].get("asi_any") or ABILITIES
                return (f"🏅 «{c['feat']}»: какой характеристике +1?", kb([[(ABILITY_RU[a], f"fa:{a}")] for a in abs_] + [back]))
            asi = c.setdefault("asi", {})
            pts = 2 - sum(asi.values())
            rows = chunks([(f"{ABILITY_RU[a]} {ch['st'][a] + asi.get(a, 0)}" + (f" (+{asi[a]})" if asi.get(a) else ""), f"ab:{a}")
                           for a in ABILITIES], 2)
            rows.append([("↩️ Сбросить", "ab:reset"), ("🏅 Взять черту", "mode:feat")])
            if pts == 0:
                rows.append([("➡️ Дальше", "ab:ok")])
            rows.append(back)
            return (f"💪 <b>Увеличение характеристик</b>: распредели +2 (одной +2 или двум по +1, максимум 20). Осталось {pts}.", kb(rows))
        if step == "picks":
            done = w["c"].setdefault("picks", {})
            fname, key, n = next(((f, k, n) for f, k, n in plan["need"]["picks"] if len(done.get(f, [])) < n), plan["need"]["picks"][-1])
            have = {v for vals in ch.get("picks", {}).values() for v in vals}
            opts = [o for o in options(key) if o not in have]
            lines = [f"<b>{o[0]}</b> — {o[1]}" for o in CHOICES[key] if isinstance(o, tuple) and o[0] in opts and o[1]]
            rows = chunks([(("✅ " if o in done.get(fname, []) else "") + o, f"pk:{options(key).index(o)}") for o in opts], 2) + [back]
            return (f"🧩 <b>{e(fname)}</b>: выбери {n}.\n\n" + "\n".join(lines), kb(rows))
        if step == "ex":
            n = plan["need"]["ex"]
            chosen = w["c"].setdefault("ex", [])
            allowed = [i for i in ch["sk"] if i not in ch.get("ex", [])]
            rows = chunks([(("✅ " if i in chosen else "") + SKILLS[i][0], f"ex:{i}") for i in allowed], 2)
            if len(chosen) == n:
                rows.append([("➡️ Дальше", "ex:ok")])
            return (f"⭐ <b>Компетентность</b>: выбери {n} навыка — двойной бонус мастерства.", kb(rows + [back]))
        if step == "spells":
            nc, ns, pool = self.new_spells(w)
            chosen = w["c"].setdefault("spells", [])
            cc = sum(1 for x in chosen if SPELLS[x]["lvl"] == 0)
            sc = len(chosen) - cc
            page = w.get("page", 0)
            part = pool[page * PAGE:(page + 1) * PAGE]
            rows = chunks([(("✅ " if s["n"] in chosen else "") + f"{s['n']} ({s['lvl'] or 'з'})", f"sp:{page * PAGE + i}") for i, s in enumerate(part)], 2)
            rows.append([x for x in (("◀", "pg:-") if page else None, ("▶", "pg:+") if (page + 1) * PAGE < len(pool) else None) if x])
            if cc == nc and sc == ns:
                rows.append([("➡️ Дальше", "sp:ok")])
            lines = [f"• <b>{s['n']}</b> ({s['lvl'] or 'заговор'}) — {s['d']}" for s in part]
            return (f"🔮 <b>Новые заклинания</b>: заговоров {cc}/{nc}, заклинаний {sc}/{ns}.\n\n" + "\n".join(lines), kb(rows + [back]))
        if step == "confirm":
            test = copy.deepcopy(ch)
            try:
                logs = engine.level_up(test, w["cls"], w["c"].get("hp", "avg") if w["c"].get("hp") != "roll" else "avg", self.choices(w))
            except ValueError as ex:
                return (f"⚠️ {e(str(ex))}", kb([back]))
            D = engine.derive(test)
            feats = "\n".join(f"• <b>{e(f[0])}</b> — {e(f[1])}" for f in plan["features"] if f[0] != "Увеличение характеристик")
            return ("📈 <b>Проверь</b>\n" + "\n".join(e(x) for x in logs if not x.startswith("хиты"))
                    + f"\n\n❤️ Максимум хитов ≈ {D['hp_max']['val']} ({'бросок' if w['c'].get('hp') == 'roll' else 'среднее'})"
                    + (f"\n\nНовые умения:\n{feats}" if feats else ""),
                    kb([[("✅ Повысить уровень", "ok:1")], back]))
        return ("?", None)

    def choices(self, w) -> dict:
        c = w["c"]
        out = {"sub": c.get("sub"), "picks": c.get("picks", {}), "ex": c.get("ex", []), "spells": c.get("spells", [])}
        if c.get("feat"):
            out["feat"], out["feat_ab"] = c["feat"], c.get("feat_ab")
        elif c.get("asi"):
            out["asi"] = c["asi"]
        return out

    async def show(self, chat_id, w, edit=None):
        text, markup = self.render(w)
        if edit:
            try:
                await self.bot.call("editMessageText", chat_id=chat_id, message_id=edit, text=text[:4000], parse_mode="HTML", reply_markup=markup)
                return
            except Exception:
                pass
        await self.bot.send_message(chat_id, text[:4000], reply_markup=markup)

    async def on_button(self, q: dict) -> None:
        uid = q["from"]["id"]
        msg = q.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id", uid)
        act, _, val = q.get("data", "")[2:].partition(":")
        w = self.get(uid)
        note = None
        if not w:
            return await self.game.wiz.answer(q, "Уже готово")
        if act == "cancel":
            self.drop(uid)
            await self.game.wiz.answer(q, "Отменено")
            return await self.bot.call("editMessageText", chat_id=chat_id, message_id=msg.get("message_id"), text="Повышение уровня отложено. /levelup — продолжить.")
        if act == "back" and w["hist"]:
            prev = w["hist"].pop()
            hist = w["hist"]
            w.clear(), w.update(prev)
            w["hist"] = hist
        elif act == "pg":
            w["page"] = max(0, w.get("page", 0) + (1 if val == "+" else -1))
        elif act == "cls" and w["step"] == "cls":
            w["cls"] = val
            w["c"] = {}
            self.advance(w)
        elif act == "hp" and w["step"] == "hp":
            w["c"]["hp"] = val
            self.advance(w)
        elif act == "sub" and w["step"] == "sub":
            w["c"]["sub"] = val
            self.advance(w)
        elif act == "mode" and w["step"] == "asi":
            w["c"]["asi_mode"] = val
            w["c"].pop("feat", None), w["c"].pop("feat_ab", None), w["c"].pop("asi", None)
        elif act == "ft" and w["step"] == "asi":
            w["c"]["feat"] = FEAT_KEYS[int(val)]
            w["c"]["asi_mode"] = "featab"
            f = FEATS[w["c"]["feat"]]
            if len(f.get("asi_any", [])) == 1:
                w["c"]["feat_ab"] = f["asi_any"][0]
            if not (f.get("asi_any") or f.get("asi_choose")) or w["c"].get("feat_ab"):
                self.advance(w)
        elif act == "fa" and w["step"] == "asi":
            w["c"]["feat_ab"] = val
            self.advance(w)
        elif act == "ab" and w["step"] == "asi":
            asi = w["c"].setdefault("asi", {})
            ch = self.db.character(w["cid"])["data"]
            if val == "reset":
                asi.clear()
            elif val == "ok":
                self.advance(w)
            elif sum(asi.values()) < 2 and ch["st"][val] + asi.get(val, 0) < 20:
                asi[val] = asi.get(val, 0) + 1
            else:
                note = "Больше нельзя"
        elif act == "pk" and w["step"] == "picks":
            _, plan = self.plan(w)
            done = w["c"].setdefault("picks", {})
            fname, key, n = next(((f, k, n) for f, k, n in plan["need"]["picks"] if len(done.get(f, [])) < n), plan["need"]["picks"][-1])
            opt = options(key)[int(val)]
            lst = done.setdefault(fname, [])
            lst.remove(opt) if opt in lst else lst.append(opt)
            if all(len(done.get(f, [])) >= n for f, _, n in plan["need"]["picks"]):
                self.advance(w)
        elif act == "ex" and w["step"] == "ex":
            _, plan = self.plan(w)
            lst = w["c"].setdefault("ex", [])
            if val == "ok":
                self.advance(w)
            else:
                i = int(val)
                if i in lst:
                    lst.remove(i)
                elif len(lst) < plan["need"]["ex"]:
                    lst.append(i)
        elif act == "sp" and w["step"] == "spells":
            nc, ns, pool = self.new_spells(w)
            lst = w["c"].setdefault("spells", [])
            if val == "ok":
                cc = sum(1 for x in lst if SPELLS[x]["lvl"] == 0)
                if cc != nc or len(lst) - cc != ns:
                    return await self.game.wiz.answer(q, f"Выбери заговоров {nc} и заклинаний {ns}")
                self.advance(w)
            else:
                s = pool[int(val)]
                cc = sum(1 for x in lst if SPELLS[x]["lvl"] == 0)
                if s["n"] in lst:
                    lst.remove(s["n"])
                elif (s["lvl"] == 0 and cc < nc) or (s["lvl"] > 0 and len(lst) - cc < ns):
                    lst.append(s["n"])
                else:
                    note = "Лимит этого типа заклинаний набран"
        elif act == "ok" and w["step"] == "confirm":
            ch = self.db.character(w["cid"])
            try:
                data, logs = self.game.mutate(ch["id"], lambda d: engine.level_up(d, w["cls"], w["c"].get("hp", "avg"), self.choices(w)),
                                              self.game.who(ch), "повышение уровня")
            except ValueError as ex:
                return await self.game.wiz.answer(q, str(ex))
            self.drop(uid)
            await self.game.wiz.answer(q, "📈 Уровень повышен!")
            await self.bot.call("editMessageText", chat_id=chat_id, message_id=msg.get("message_id"),
                                text="📈 <b>Уровень повышен!</b>\n" + "\n".join(e(x) for x in logs), parse_mode="HTML")
            await self.game.ui.send_sheet(self.db.character(ch["id"]))
            return
        else:
            note = "Этот шаг уже пройден"
        self.put(uid, w)
        await self.game.wiz.answer(q, note)
        await self.show(chat_id, w, msg.get("message_id"))
