"""Пошаговое создание персонажа в боте по «Книге игрока» 2014.

код лобби → способ → имя → раса → подраса → (наследие) → класс → (подкласс 1 ур.) → предыстория →
характеристики (стандартный набор / закупка очков / 4d6 / свои) → бонусы расы на выбор → навыки →
доп. навыки → компетентность → выборы умений → языки → черта → снаряжение → заговоры → заклинания →
мировоззрение → о герое → проверка.

Каждый шаг объясняет термины и отмечает ⭐ рекомендации; «⬅️ Назад» возвращает на прошлый шаг с ответами.
Черновик хранится в kv (`wiz:<tg_id>`), мастер видит его на сайте."""
import copy
import html

from . import dice, engine
from .data.backgrounds import ALIGNMENTS, BACKGROUNDS, FEATS, LANGUAGES
from .data.choices import CHOICES, options
from .data.classes import CLASSES
from .data.pregens import PREGENS
from .data.races import DRAGON_ANCESTRY, RACES, race_info
from .data.spells import SPELLS, for_class
from .rules import ABILITIES, ABILITY_RU, ABILITY_SHORT, SKILLS, mod

e = html.escape
MAX_BAD_CODES = 15
PAGE = 12
STANDARD = [15, 14, 13, 12, 10, 8]
PB_COST = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}
RACE_KEYS, CLASS_KEYS, BG_KEYS = list(RACES), list(CLASSES), list(BACKGROUNDS)
FEAT_KEYS = sorted(FEATS)
SK_NAMES = [s[0] for s in SKILLS]
REC_SPELLS = {
    "wizard": ["Огненный снаряд", "Волшебная рука", "Свет", "Волшебная стрела", "Щит", "Доспехи мага", "Усыпление", "Обнаружение магии", "Огненные ладони"],
    "sorcerer": ["Огненный снаряд", "Луч холода", "Свет", "Фокусы", "Волшебная стрела", "Щит"],
    "warlock": ["Мистический заряд", "Малая иллюзия", "Сглаз", "Доспех Агатиса"],
    "bard": ["Злая насмешка", "Волшебная рука", "Лечащее слово", "Диссонирующий шёпот", "Усыпление", "Героизм"],
    "cleric": ["Священное пламя", "Указание", "Свет", "Лечащее слово", "Направленный снаряд", "Благословение", "Лечение ран"],
    "druid": ["Сотворение пламени", "Указание", "Лечение ран", "Опутывание", "Чудо-ягоды", "Лечащее слово"],
}

LABEL = {
    "code": "вводит код", "mode": "начинает", "pregen": "выбирает готового героя", "name": "выбирает имя",
    "race": "выбирает расу", "sub": "выбирает подрасу", "ancestry": "выбирает предка", "cls": "выбирает класс",
    "csub": "выбирает подкласс", "bg": "выбирает предысторию", "method": "характеристики", "assign": "характеристики",
    "pointbuy": "закупает характеристики", "stats_own": "характеристики", "race_asi": "бонусы расы", "skills": "навыки",
    "extra_skills": "навыки", "ex": "компетентность", "picks": "умения класса", "langs": "языки", "feat": "черта",
    "feat_ab": "черта", "equip": "снаряжение", "race_cantrip": "заговор расы", "cantrips": "заговоры", "spells": "заклинания",
    "prep": "заклинания", "al": "мировоззрение", "about": "пишет о герое", "confirm": "проверяет лист",
}


def kb(rows) -> dict:
    return {"inline_keyboard": [[{"text": t, "callback_data": ("w:" + d)[:64]} for t, d in row] for row in rows if row]}


def chunks(items, n):
    return [items[i:i + n] for i in range(0, len(items), n)]


class Wizard:
    def __init__(self, game):
        self.game = game
        self.db = game.db
        self.bot = game.bot

    # ---------- состояние ----------
    def get(self, uid: int) -> dict | None:
        return self.db.get(f"wiz:{uid}")

    def put(self, uid: int, w: dict) -> None:
        self.db.set(f"wiz:{uid}", w)
        if w.get("gid"):
            self.game.emit_drafts(w["gid"])

    def drop(self, uid: int) -> None:
        w = self.get(uid)
        self.db.delete(f"wiz:{uid}")
        if w and w.get("gid"):
            self.game.emit_drafts(w["gid"])

    def drafts(self, gid: int) -> list[dict]:
        out = []
        for k, w in self.db.prefixed("wiz:"):
            if w.get("gid") != gid or w.get("step") == "code":
                continue
            d = w.get("d", {})
            race = race_info(d["race"], d.get("sub")).get("sub_n") or RACES[d["race"]]["n"] if d.get("race") else ""
            out.append({"tg_id": int(k[4:]), "player": w.get("player", ""), "step": LABEL.get(w["step"], ""),
                        "name": d.get("name", ""), "race": race, "cls": CLASSES[d["cls"]]["n"] if d.get("cls") else ""})
        return out

    # ---------- вход ----------
    async def begin(self, chat_id: int, user: dict, gid: int | None = None, show: bool = True) -> None:
        w = {"step": "mode" if gid else "code", "d": {}, "gid": gid, "player": user.get("first_name", ""), "bad": 0, "hist": []}
        if gid:
            self.db.add_player(user["id"], user.get("username"), user.get("first_name", ""))
            g = self.db.game(gid)
            await self.bot.send_message(chat_id, f"🎉 Ты в игре «<b>{e(g['name'])}</b>»! Соберём героя.")
        self.put(user["id"], w)
        if show:
            await self.show(chat_id, w)

    async def cancel(self, chat_id: int, uid: int) -> None:
        self.drop(uid)
        await self.bot.send_message(chat_id, "Создание персонажа отменено. /new — начать заново.")

    # ---------- порядок шагов ----------
    def next_step(self, w: dict, cur: str) -> str:
        d = w["d"]
        order = ["name", "race", "sub", "ancestry", "cls", "csub", "bg", "method", "assign", "race_asi", "skills",
                 "extra_skills", "ex", "picks", "langs", "feat", "feat_ab", "equip", "race_cantrip", "cantrips", "spells",
                 "prep", "al", "about", "confirm"]
        if cur in ("pointbuy", "stats_own"):
            cur = "assign"
        i = order.index(cur) + 1 if cur in order else 0
        while i < len(order):
            s = order[i]
            if self.applies(w, s):
                return s
            i += 1
        return "confirm"

    def applies(self, w: dict, s: str) -> bool:
        d = w["d"]
        rd = race_info(d["race"], d.get("sub")) if d.get("race") else {}
        ci = CLASSES.get(d.get("cls") or "")
        if s == "sub":
            return bool(RACES[d["race"]].get("subs"))
        if s == "ancestry":
            return bool(rd.get("ancestry"))
        if s == "csub":
            return ci["sub_lvl"] == 1
        if s == "assign":
            return False  # переход в assign/pointbuy/stats_own делает шаг method
        if s == "race_asi":
            return bool(rd.get("asi_choice"))
        if s == "extra_skills":
            return bool(rd.get("skill_choice"))
        if s == "ex":
            return any((f[2] if len(f) > 2 else {}).get("expertise") for f in ci["features"].get(1, []))
        if s == "picks":
            return bool(self.level1_picks(d))
        if s == "langs":
            return self.lang_count(d) > 0
        if s in ("feat", "feat_ab"):
            if s == "feat_ab":
                f = FEATS.get(d.get("feat") or "", {})
                return bool(rd.get("feat")) and (len(f.get("asi_any", [])) > 1 or f.get("asi_choose"))
            return bool(rd.get("feat"))
        if s == "race_cantrip":
            return bool(rd.get("cantrip"))
        if s == "cantrips":
            return bool(ci.get("cantrips")) and ci.get("caster") and ci["cantrips"](1) > 0
        if s == "spells":
            return ci.get("caster") in ("full", "pact") and (ci.get("known") or ci.get("spellbook"))
        if s == "prep":
            return ci.get("caster") == "full" and ci.get("prepared")
        return True

    def level1_picks(self, d: dict) -> list[tuple[str, str, int]]:
        ci = CLASSES[d["cls"]]
        feats = list(ci["features"].get(1, []))
        if d.get("csub"):
            feats += ci["subs"][d["csub"]]["features"].get(1, [])
        return [(f[0], f[2]["pick"], f[2]["n"]) for f in feats if len(f) > 2 and f[2].get("pick")]

    def lang_count(self, d: dict) -> int:
        rd = race_info(d["race"], d.get("sub"))
        return rd.get("lang_choice", 0) + BACKGROUNDS.get(d.get("bg") or "", {}).get("langs", 0)

    def fixed_skills(self, d: dict) -> set[int]:
        rd = race_info(d["race"], d.get("sub"))
        bg = BACKGROUNDS.get(d.get("bg") or "", {})
        return {SK_NAMES.index(n) for n in rd.get("skills", []) + bg.get("skills", [])}

    def caster_mod(self, d: dict) -> int:
        ci = CLASSES[d["cls"]]
        st = self.final_stats(d)
        return mod(st[ci["cast_ab"]]) if ci.get("cast_ab") else 0

    def final_stats(self, d: dict) -> dict:
        rd = race_info(d["race"], d.get("sub"))
        st = dict(d.get("st") or {a: 10 for a in ABILITIES})
        for a, v in rd.get("asi", {}).items():
            st[a] = st.get(a, 10) + v
        for a in d.get("asi", []):
            st[a] += 1
        if d.get("feat_ab"):
            st[d["feat_ab"]] += 1
        return st

    # ---------- отрисовка ----------
    def render(self, w: dict) -> tuple[str, dict | None]:
        d, step = w["d"], w["step"]
        back = [("⬅️ Назад", "back:0")] if w.get("hist") else []
        rows: list = []
        if step == "code":
            return ("🛡 <b>Привет, путник!</b>\n\nВведи <b>код лобби</b> — его назовёт мастер (5 цифр).", None)
        if step == "mode":
            return ("🧙 <b>Как соберём героя?</b>\n\n🧭 <b>Пошагово</b> — выберешь всё сам, я объясню каждый термин (5–10 минут).\n"
                    "⚡ <b>Готовый герой</b> — один из 12 собранных персонажей, можно играть сразу.",
                    kb([[("🧭 Пошагово", "mode:step")], [("⚡ Готовый герой", "mode:pregen")]]))
        if step == "pregen":
            rows = [[(p["title"], f"pg:{i}")] for i, p in enumerate(PREGENS)] + [back]
            return ("⚡ <b>Готовые герои</b>\nВсе 1 уровня, со снаряжением и заклинаниями. Имя потом можно поменять.", kb(rows))
        if step == "name":
            return ("✍️ <b>Имя героя</b>\n\nКак зовут твоего персонажа? Просто напиши имя.", kb([back]) if back else None)
        if step == "race":
            lines = [f"<b>{r['n']}</b> — {r['about']}" for r in RACES.values()]
            rows = chunks([(RACES[k]["n"], f"race:{i}") for i, k in enumerate(RACE_KEYS)], 3) + [back]
            return (f"🧝 <b>Раса</b> героя <b>{e(d['name'])}</b>\n\nРаса даёт бонусы к характеристикам, скорость и врождённые умения.\n\n"
                    + "\n".join(lines), kb(rows))
        if step == "sub":
            subs = RACES[d["race"]]["subs"]
            lines = [f"<b>{s['n']}</b> — {s.get('about', '')}" for s in subs.values()]
            rows = [[(s["n"], f"sub:{k}")] for k, s in subs.items()] + [back]
            return (f"🧝 <b>Подраса</b> ({RACES[d['race']]['n']})\n\n" + "\n".join(lines), kb(rows))
        if step == "ancestry":
            lines = [f"<b>{c}</b>: {t}, {s}, спас {sv}" for c, (t, s, sv) in DRAGON_ANCESTRY.items()]
            rows = chunks([(c, f"anc:{c}") for c in DRAGON_ANCESTRY], 3) + [back]
            return ("🐉 <b>Драконий предок</b>\nОпределяет урон и форму дыхания и твоё сопротивление.\n\n" + "\n".join(lines), kb(rows))
        if step == "cls":
            rd = race_info(d["race"], d.get("sub"))
            good = set(rd.get("asi", {}))
            lines = []
            for k in CLASS_KEYS:
                c = CLASSES[k]
                star = "⭐ " if set(c["main"][:1]) & good else ""
                lines.append(f"{star}<b>{c['n']}</b> (d{c['hd']}, {'/'.join(ABILITY_SHORT[a] for a in c['main'])}) — {c['about']}")
            rows = chunks([(("⭐" if set(CLASSES[k]["main"][:1]) & good else "") + CLASSES[k]["n"], f"cls:{i}")
                           for i, k in enumerate(CLASS_KEYS)], 3) + [back]
            return ("⚔️ <b>Класс</b> — чем герой занимается в приключениях.\n"
                    "d8/d10 — кость хитов: чем больше, тем живучее. ⭐ — раса хорошо подходит.\n\n" + "\n".join(lines), kb(rows))
        if step == "csub":
            ci = CLASSES[d["cls"]]
            lines = [f"<b>{s['n']}</b> — {s.get('about', '')}" for s in ci["subs"].values()]
            rows = chunks([(s["n"], f"csub:{k}") for k, s in ci["subs"].items()], 2) + [back]
            return (f"✨ <b>{ci['sub_title']}</b>\nУ класса «{ci['n']}» подкласс выбирается сразу.\n\n" + "\n".join(lines), kb(rows))
        if step == "bg":
            lines = [f"<b>{b['n']}</b>: {', '.join(b['skills'])}" for b in BACKGROUNDS.values()]
            rows = chunks([(BACKGROUNDS[k]["n"], f"bg:{i}") for i, k in enumerate(BG_KEYS)], 3) + [back]
            return ("📜 <b>Предыстория</b> — кем герой был до приключений. Даёт 2 навыка, инструменты или языки, "
                    "снаряжение и особенность.\n\n" + "\n".join(lines), kb(rows))
        if step == "method":
            main = " и ".join(ABILITY_RU[a] for a in CLASSES[d["cls"]]["main"])
            return ("🎲 <b>Характеристики</b>\nШесть чисел: Сила, Ловкость, Телосложение, Интеллект, Мудрость, Харизма. "
                    "10–11 — средне, 15+ — отлично. Модификатор = (значение − 10) / 2.\n"
                    f"Для класса «{CLASSES[d['cls']]['n']}» важнее всего: <b>{main}</b>.\n\nКак получим числа?",
                    kb([[("📋 Стандартный набор 15·14·13·12·10·8 ⭐", "m:std")], [("🧮 Закупка очков (27)", "m:pb")],
                        [("🎲 Бросить 4d6 ×6", "m:roll")], [("✍️ Свои числа", "m:own")], back]))
        if step == "stats_own":
            return ("✍️ Напиши <b>6 чисел</b> от 3 до 18 через пробел, например <code>16 14 13 12 10 8</code>.", kb([back]))
        if step == "assign":
            val = w["pool"][0]
            main = CLASSES[d["cls"]]["main"]
            free = [a for a in ABILITIES if a not in d["st"]]
            rows = chunks([(("⭐ " if a in main else "") + ABILITY_RU[a], f"as:{a}") for a in free], 2)
            rows.append([("↩️ Заново", "as:reset")] if d["st"] else [])
            rows.append(back)
            done = " · ".join(f"{ABILITY_SHORT[a]} {d['st'][a]}" for a in ABILITIES if a in d["st"])
            left = ", ".join(map(str, w["pool"][1:]))
            return (f"🎲 <b>Расставь характеристики</b>{w.get('roll_txt', '')}\n\nКуда поставить <b>{val}</b>?"
                    + (f"\nДальше: {left}" if left else "") + (f"\n\n{done}" if done else "")
                    + "\n⭐ — важно для класса. Бонусы расы добавятся сами.", kb(rows))
        if step == "pointbuy":
            st = d["st"]
            spent = sum(PB_COST[v] for v in st.values())
            rows = [[(f"{'⭐' if a in CLASSES[d['cls']]['main'] else ''}{ABILITY_SHORT[a]} {st[a]}", "noop:0"), ("−", f"pb:{a}:-"), ("+", f"pb:{a}:+")]
                    for a in ABILITIES]
            rows.append([("✅ Готово", "pb:ok:0")])
            rows.append(back)
            return (f"🧮 <b>Закупка очков</b>: осталось <b>{27 - spent}</b> из 27.\nВсе начинают с 8; 9–13 стоят по 1 очку, "
                    "14 и 15 — по 2. Максимум 15 до бонусов расы.", kb(rows))
        if step == "race_asi":
            rd = race_info(d["race"], d.get("sub"))
            n, amt, excl = rd["asi_choice"]
            chosen = d.get("asi", [])
            st = self.final_stats(d)
            rows = chunks([(("✅ " if a in chosen else "") + f"{ABILITY_RU[a]} {st[a]}", f"ra:{a}") for a in ABILITIES if a not in excl], 2)
            if len(chosen) == n:
                rows.append([("➡️ Дальше", "ra:ok")])
            rows.append(back)
            return (f"✨ <b>Бонусы расы</b>: выбери {n} характеристики, каждая +{amt}. Выбрано {len(chosen)}/{n}.", kb(rows))
        if step in ("skills", "extra_skills", "ex"):
            fixed = self.fixed_skills(d)
            if step == "skills":
                n, names = CLASSES[d["cls"]]["skills"]
                allowed = list(range(len(SKILLS))) if names is None else [SK_NAMES.index(x) for x in names]
                chosen = d.get("sk", [])
                head = (f"🎯 <b>Навыки класса</b>: выбери {n}. Владение навыком добавляет бонус мастерства (+2) к проверке.\n"
                        f"Уже есть от расы и предыстории: {', '.join(SK_NAMES[i] for i in sorted(fixed)) or 'нет'}.")
                allowed = [i for i in allowed if i not in fixed]
            elif step == "extra_skills":
                n = race_info(d["race"], d.get("sub"))["skill_choice"]
                have = fixed | set(d.get("sk", []))
                allowed = [i for i in range(len(SKILLS)) if i not in have]
                chosen = d.get("extra_skills", [])
                head = f"🎯 <b>Навыки расы</b>: выбери ещё {n} любых."
            else:
                n = 2
                allowed = sorted(fixed | set(d.get("sk", [])) | set(d.get("extra_skills", [])))
                chosen = d.get("ex", [])
                head = "⭐ <b>Компетентность</b>: выбери 2 своих навыка — для них бонус мастерства удваивается."
            rows = chunks([(("✅ " if i in chosen else "") + SK_NAMES[i] + f" ({ABILITY_SHORT[SKILLS[i][1]]})", f"sk:{i}") for i in allowed], 2)
            if len(chosen) == n:
                rows.append([("➡️ Дальше", "sk:ok")])
            rows.append(back)
            return (f"{head}\n\nВыбрано {len(chosen)} из {n}.", kb(rows))
        if step == "picks":
            todo = self.level1_picks(d)
            done = d.get("picks", {})
            fname, key, n = next(((f, k, n) for f, k, n in todo if len(done.get(f, [])) < n), todo[-1])
            chosen = done.get(fname, [])
            opts = CHOICES[key]
            lines = [f"<b>{o[0]}</b> — {o[1]}" for o in opts if isinstance(o, tuple) and o[1]]
            rows = chunks([(("✅ " if o in chosen else "") + o, f"pk:{options(key).index(o)}") for o in options(key)], 2)
            rows.append(back)
            return (f"🧩 <b>{e(fname)}</b>: выбери {n}.\n\n" + "\n".join(lines), kb(rows))
        if step == "langs":
            n = self.lang_count(d)
            rd = race_info(d["race"], d.get("sub"))
            chosen = d.get("langs", [])
            rows = chunks([(("✅ " if l in chosen else "") + l, f"lg:{LANGUAGES.index(l)}") for l in LANGUAGES if l not in rd["langs"]], 3)
            if len(chosen) == n:
                rows.append([("➡️ Дальше", "lg:ok")])
            rows.append(back)
            return (f"🗣 <b>Языки</b>: уже знаешь {', '.join(rd['langs'])}. Выбери ещё {n}. Выбрано {len(chosen)}/{n}.", kb(rows))
        if step == "feat":
            page = w.get("page", 0)
            keys = FEAT_KEYS[page * PAGE:(page + 1) * PAGE]
            lines = [f"<b>{k}</b>{' (' + FEATS[k]['req'] + ')' if FEATS[k].get('req') else ''} — {FEATS[k]['d']}" for k in keys]
            rows = chunks([(k, f"ft:{FEAT_KEYS.index(k)}") for k in keys], 2)
            nav = []
            if page:
                nav.append(("◀", "pg:-"))
            if (page + 1) * PAGE < len(FEAT_KEYS):
                nav.append(("▶", "pg:+"))
            rows += [nav, back]
            return ("🏅 <b>Черта</b> — особый талант (человек-вариант получает её на 1 уровне).\n\n" + "\n".join(lines), kb(rows))
        if step == "feat_ab":
            f = FEATS[d["feat"]]
            abs_ = f.get("asi_any") or ABILITIES
            return (f"🏅 «{d['feat']}»: какой характеристике +1?", kb([[(ABILITY_RU[a], f"fa:{a}")] for a in abs_] + [back]))
        if step == "equip":
            opts = CLASSES[d["cls"]]["equip"]
            bg = BACKGROUNDS[d["bg"]]
            lines = [f"{'⭐ ' if i == 0 else ''}<b>{n}</b>: {', '.join(items)}" for i, (n, items) in enumerate(opts)]
            rows = [[(("⭐ " if i == 0 else "") + n, f"eq:{i}")] for i, (n, _) in enumerate(opts)] + [back]
            return ("🎒 <b>Снаряжение</b>. Плюс от предыстории: " + ", ".join(bg["items"]) + f", {bg['gold']} зм.\n\n" + "\n".join(lines), kb(rows))
        if step in ("race_cantrip", "cantrips", "spells", "prep"):
            return self.render_spells(w, back)
        if step == "al":
            rows = chunks([(a, f"al:{i}") for i, a in enumerate(ALIGNMENTS)], 3) + [[("Пропустить", "al:-")], back]
            return ("⚖️ <b>Мировоззрение</b> — моральный компас героя: закон/хаос и добро/зло. Влияет только на отыгрыш.", kb(rows))
        if step == "about":
            return ("📜 <b>О герое</b>\nПара слов: внешность, характер, откуда родом, чего хочет. Мастер это прочитает.",
                    kb([[("Пропустить ➡️", "skip:0")], back]))
        if step == "confirm":
            return (self.summary(d), kb([[("✅ Создать персонажа", "ok:1")], [("⬅️ Назад", "back:0"), ("🔄 Заново", "ok:0")]]))
        raise ValueError(step)

    def spell_ctx(self, w: dict) -> tuple[list[dict], int, list[str], str]:
        """(варианты, сколько выбрать, выбранные, заголовок) для шагов с заклинаниями."""
        d, step = w["d"], w["step"]
        ci = CLASSES[d["cls"]]
        sp = d.setdefault("spells", {"known": [], "prep": []})
        if step == "race_cantrip":
            pool = [s for s in for_class("wizard", 0)]
            return pool, 1, [n for n in sp.get("race", [])], "Заговор высшего эльфа (из списка волшебника, ИНТ)"
        if step == "cantrips":
            pool = for_class(d["cls"], 0)
            n = ci["cantrips"](1)
            return pool, n, [x for x in sp["known"] if SPELLS[x]["lvl"] == 0], "Заговоры — бесплатная магия без ячеек, используется постоянно"
        if step == "spells":
            pool = for_class(d["cls"], 1)
            pool = [s for s in pool if s["lvl"] == 1]
            if d["cls"] == "warlock" and d.get("csub"):
                extra = CLASSES["warlock"]["subs"][d["csub"]].get("extra", {}).get(1, [])
                pool += [SPELLS[x] for x in extra if x in SPELLS and SPELLS[x] not in pool]
            n = 6 if ci.get("spellbook") else ci["known"](1)
            title = ("Книга заклинаний: 6 заклинаний 1 круга" if ci.get("spellbook") else
                     f"Известные заклинания 1 круга: {n}. Тратят ячейки (у тебя их 2{' и вернутся на коротком отдыхе' if d['cls'] == 'warlock' else ''})")
            return pool, n, [x for x in sp["known"] if SPELLS[x]["lvl"] == 1], title
        # prep
        n = max(1, self.caster_mod(d) + 1)
        if ci.get("spellbook"):
            pool = [SPELLS[x] for x in sp["known"] if SPELLS[x]["lvl"] == 1]
        else:
            pool = [s for s in for_class(d["cls"], 1) if s["lvl"] == 1]
        return pool, n, sp["prep"], f"Подготовленные заклинания: {n} (МОД + уровень). Менять можно после долгого отдыха"

    def render_spells(self, w, back):
        pool, n, chosen, title = self.spell_ctx(w)
        rec = set(REC_SPELLS.get(w["d"]["cls"], []))
        page = w.get("page", 0)
        part = pool[page * PAGE:(page + 1) * PAGE]
        lines = [f"{'⭐' if s['n'] in rec else '•'} <b>{s['n']}</b>{' 🧠' if s['conc'] else ''} — {s['d']}" for s in part]
        rows = chunks([(("✅ " if s["n"] in chosen else ("⭐" if s["n"] in rec else "")) + s["n"], f"sp:{page * PAGE + i}")
                       for i, s in enumerate(part)], 2)
        nav = []
        if page:
            nav.append(("◀", "pg:-"))
        if (page + 1) * PAGE < len(pool):
            nav.append(("▶", "pg:+"))
        rows.append(nav)
        if len(chosen) == n:
            rows.append([("➡️ Дальше", "sp:ok")])
        rows.append(back)
        return (f"🔮 <b>{title}</b>\nВыбрано {len(chosen)} из {n}. ⭐ — советую новичку, 🧠 — концентрация.\n\n" + "\n".join(lines), kb(rows))

    def build(self, d: dict) -> dict:
        a = copy.deepcopy(d)
        sp = a.get("spells", {})
        a["spells"] = {"known": list(sp.get("known", [])) + list(sp.get("race", [])), "prep": list(sp.get("prep", []))}
        return engine.new_character(a)

    def summary(self, d: dict) -> str:
        ch = self.build(d)
        D = engine.derive(ch)
        atk = "\n".join(f"⚔️ {e(a['n'])}: " + (f"{a['hit']:+d}, " if a["hit"] is not None else "") + f"урон {a['dmg']}" for a in D["attacks"])
        sk = ", ".join(s["n"] + ("★" if s["exp"] else "") for s in D["skills"] if s["prof"])
        spells = engine.spell_list(ch)
        cast = D["cast"][0] if D["cast"] else None
        return (f"🧙 <b>{e(ch['name'])}</b>\n<i>{e(ch['race'])}, {e(ch['cls'])} 1 ур., {e(ch['bg'])}</i>\n\n"
                f"❤️ Хиты {D['hp_max']['val']} · 🛡 КД {D['ac']['val']} · 👣 {D['spd']['val']} фт · ⚡ {D['init']['bonus']:+d}\n\n"
                + " · ".join(f"{ABILITY_SHORT[a]} {D['ab'][a]['score']} ({D['m'][a]:+d})" for a in ABILITIES)
                + f"\n\n🎯 {sk}\n\n{atk}"
                + (f"\n\n🔮 СЛ {cast['dc']}, атака {cast['atk']:+d}: {', '.join(spells)}" if cast and spells else "")
                + (f"\n\n⚠️ " + "; ".join(D["warn"]) if D["warn"] else "") + "\n\nВсё верно?")

    async def show(self, chat_id: int, w: dict, edit_msg: int | None = None) -> None:
        text, markup = self.render(w)
        if len(text) > 4000:
            text = text[:3990] + "…"
        if edit_msg:
            try:
                await self.bot.call("editMessageText", chat_id=chat_id, message_id=edit_msg, text=text,
                                    parse_mode="HTML", reply_markup=markup)
                return
            except Exception:
                pass
        await self.bot.send_message(chat_id, text, reply_markup=markup)

    def go(self, w: dict, step: str) -> None:
        snap = {k: copy.deepcopy(v) for k, v in w.items() if k not in ("hist",)}
        w.setdefault("hist", []).append(snap)
        w["hist"] = w["hist"][-40:]
        w["step"] = step
        w["page"] = 0

    def advance(self, w: dict) -> None:
        self.go(w, self.next_step(w, w["step"]))

    # ---------- текст ----------
    async def on_text(self, chat_id: int, user: dict, text: str) -> None:
        uid = user["id"]
        w = self.get(uid)
        step, d = w["step"], w["d"]
        if step == "code":
            if w.get("bad", 0) >= MAX_BAD_CODES:
                await self.bot.send_message(chat_id, "Слишком много попыток. Спроси у мастера ссылку на лобби.")
                return
            g = self.db.game_by_code("".join(c for c in text if c.isdigit()))
            if g:
                self.drop(uid)
                await self.begin(chat_id, user, g["id"])
            else:
                w["bad"] = w.get("bad", 0) + 1
                self.db.set(f"wiz:{uid}", w)
                await self.bot.send_message(chat_id, "🤔 Такого лобби нет. Проверь код у мастера и попробуй ещё раз.")
            return
        if step == "name":
            name = " ".join(text.split())[:40]
            if not name:
                return
            d["name"] = name
            self.advance(w)
        elif step == "stats_own":
            nums = [int(x) for x in text.replace(",", " ").split() if x.isdigit()]
            if len(nums) != 6 or not all(3 <= n <= 18 for n in nums):
                await self.bot.send_message(chat_id, "Нужно ровно 6 чисел от 3 до 18, например <code>16 14 13 12 10 8</code>")
                return
            self.start_assign(w, nums, "")
        elif step == "about":
            d["about"] = text.strip()[:2000]
            self.advance(w)
        self.put(uid, w)
        await self.show(chat_id, w)

    def start_assign(self, w: dict, nums: list[int], roll_txt: str) -> None:
        self.go(w, "assign")
        w["pool"] = sorted(nums, reverse=True)
        w["all"] = list(w["pool"])
        w["roll_txt"] = roll_txt
        w["d"]["st"] = {}

    # ---------- кнопки ----------
    async def on_button(self, q: dict) -> None:
        uid = q["from"]["id"]
        msg = q.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id", uid)
        act, _, val = q.get("data", "")[2:].partition(":")
        w = self.get(uid)
        if not w:
            return await self.answer(q, "Создание уже завершено")
        if act == "noop":
            return await self.answer(q)
        d, step = w["d"], w["step"]
        allowed = {"mode": "mode", "pregen": "pg", "race": "race", "sub": "sub", "ancestry": "anc", "cls": "cls", "csub": "csub",
                   "bg": "bg", "method": "m", "assign": "as", "pointbuy": "pb", "race_asi": "ra", "skills": "sk", "extra_skills": "sk",
                   "ex": "sk", "picks": "pk", "langs": "lg", "feat": "ft", "feat_ab": "fa", "equip": "eq", "race_cantrip": "sp",
                   "cantrips": "sp", "spells": "sp", "prep": "sp", "al": "al", "about": "skip", "confirm": "ok"}
        if act == "back":
            if not w.get("hist"):
                return await self.answer(q, "Это первый шаг")
            prev = w["hist"].pop()
            hist = w["hist"]
            w.clear()
            w.update(prev)
            w["hist"] = hist
            self.put(uid, w)
            await self.answer(q)
            return await self.show(chat_id, w, msg.get("message_id"))
        if act == "pg" and step in ("feat", "race_cantrip", "cantrips", "spells", "prep") and val in "+-":
            w["page"] = max(0, w.get("page", 0) + (1 if val == "+" else -1))
            self.put(uid, w)
            await self.answer(q)
            return await self.show(chat_id, w, msg.get("message_id"))
        if allowed.get(step) != act:
            return await self.answer(q, "Этот шаг уже пройден")
        note = None
        try:
            note = self.apply(w, act, val)
        except ValueError as ex:
            return await self.answer(q, str(ex))
        if note == "FINISH":
            await self.answer(q, "✨ Персонаж создан!")
            return await self.finish(chat_id, q["from"], w, msg.get("message_id"))
        self.put(uid, w)
        await self.answer(q, note)
        await self.show(chat_id, w, msg.get("message_id"))

    def apply(self, w: dict, act: str, val: str) -> str | None:
        d, step = w["d"], w["step"]

        def toggle(lst: list, item, n: int):
            if item in lst:
                lst.remove(item)
            elif len(lst) >= n:
                raise ValueError(f"Уже выбрано {n} — сними другой")
            else:
                lst.append(item)

        if act == "mode":
            if val == "pregen":
                self.go(w, "pregen")
            else:
                self.go(w, "name")
        elif act == "pg":
            p = dict(PREGENS[int(val)])
            p.pop("title")
            w["d"] = {**p, "spells": {"known": p.get("spells", {}).get("known", []), "prep": p.get("spells", {}).get("prep", [])}}
            self.go(w, "confirm")
        elif act == "race":
            d["race"] = RACE_KEYS[int(val)]
            for k in ("sub", "ancestry", "asi", "extra_skills", "langs", "feat", "feat_ab"):
                d.pop(k, None)
            self.advance(w)
        elif act == "sub":
            d["sub"] = val
            self.advance(w)
        elif act == "anc":
            d["ancestry"] = val
            self.advance(w)
        elif act == "cls":
            d["cls"] = CLASS_KEYS[int(val)]
            for k in ("csub", "sk", "ex", "picks", "spells", "equip"):
                d.pop(k, None)
            self.advance(w)
        elif act == "csub":
            d["csub"] = val
            self.advance(w)
        elif act == "bg":
            d["bg"] = BG_KEYS[int(val)]
            d.pop("sk", None)
            self.advance(w)
        elif act == "m":
            if val == "std":
                self.start_assign(w, STANDARD, "")
            elif val == "roll":
                rolls = [dice.roll("4d6kh3") for _ in range(6)]
                txt = "\n" + "\n".join(f"🎲 {r.dice_text().replace('~', '')} → <b>{r.total}</b>" for r in rolls)
                self.start_assign(w, [r.total for r in rolls], txt)
            elif val == "pb":
                self.go(w, "pointbuy")
                d["st"] = {a: 8 for a in ABILITIES}
            else:
                self.go(w, "stats_own")
        elif act == "as":
            if val == "reset":
                w["pool"], d["st"] = list(w["all"]), {}
            elif val in ABILITIES and val not in d["st"]:
                d["st"][val] = w["pool"].pop(0)
                if not w["pool"]:
                    w["step"] = "assign"
                    self.advance(w)
        elif act == "pb":
            if val == "ok:0" or val.startswith("ok"):
                if sum(PB_COST[v] for v in d["st"].values()) > 27:
                    raise ValueError("Перерасход очков")
                w["step"] = "assign"
                self.advance(w)
            else:
                ab, _, sign = val.partition(":")
                cur = d["st"][ab]
                new = cur + (1 if sign == "+" else -1)
                if new < 8 or new > 15:
                    raise ValueError("От 8 до 15")
                spent = sum(PB_COST[v] for v in d["st"].values()) - PB_COST[cur] + PB_COST[new]
                if spent > 27:
                    raise ValueError("Не хватает очков")
                d["st"][ab] = new
        elif act == "ra":
            n, _, excl = race_info(d["race"], d.get("sub"))["asi_choice"]
            if val == "ok":
                self.advance(w)
            elif val in ABILITIES and val not in excl:
                toggle(d.setdefault("asi", []), val, n)
        elif act == "sk":
            key = {"skills": "sk", "extra_skills": "extra_skills", "ex": "ex"}[step]
            n = (CLASSES[d["cls"]]["skills"][0] if step == "skills" else
                 race_info(d["race"], d.get("sub"))["skill_choice"] if step == "extra_skills" else 2)
            if val == "ok":
                if len(d.get(key, [])) != n:
                    raise ValueError(f"Нужно {n}")
                self.advance(w)
            else:
                toggle(d.setdefault(key, []), int(val), n)
        elif act == "pk":
            todo = self.level1_picks(d)
            picks = d.setdefault("picks", {})
            fname, key, n = next(((f, k, n) for f, k, n in todo if len(picks.get(f, [])) < n), todo[-1])
            opt = options(key)[int(val)]
            lst = picks.setdefault(fname, [])
            if opt in lst:
                lst.remove(opt)
            else:
                lst.append(opt)
            if all(len(picks.get(f, [])) >= n for f, _, n in todo):
                self.advance(w)
        elif act == "lg":
            n = self.lang_count(d)
            if val == "ok":
                self.advance(w)
            else:
                toggle(d.setdefault("langs", []), LANGUAGES[int(val)], n)
        elif act == "ft":
            d["feat"] = FEAT_KEYS[int(val)]
            f = FEATS[d["feat"]]
            if len(f.get("asi_any", [])) == 1:
                d["feat_ab"] = f["asi_any"][0]
            self.advance(w)
        elif act == "fa":
            d["feat_ab"] = val
            self.advance(w)
        elif act == "eq":
            d["equip"] = int(val)
            self.advance(w)
        elif act == "sp":
            pool, n, chosen, _ = self.spell_ctx(w)
            if val == "ok":
                if len(chosen) != n:
                    raise ValueError(f"Нужно {n}")
                self.advance(w)
            else:
                if not val.isdigit() or int(val) >= len(pool):
                    raise ValueError("Нет такого")
                name = pool[int(val)]["n"]
                sp = d.setdefault("spells", {"known": [], "prep": []})
                lst = sp.setdefault("race", []) if step == "race_cantrip" else (sp["prep"] if step == "prep" else sp["known"])
                if name in lst:
                    lst.remove(name)
                elif len(chosen) >= n:
                    raise ValueError(f"Уже выбрано {n} — сними другое")
                else:
                    lst.append(name)
        elif act == "al":
            d["al"] = "" if val == "-" else ALIGNMENTS[int(val)]
            self.advance(w)
        elif act == "skip":
            d["about"] = ""
            self.advance(w)
        elif act == "ok":
            if val == "1":
                return "FINISH"
            w.update({"step": "mode", "d": {}, "hist": []})
            return "Начинаем заново"
        return None

    async def answer(self, q: dict, text: str | None = None) -> None:
        try:
            await self.bot.call("answerCallbackQuery", callback_query_id=q["id"], text=text)
        except Exception:
            pass

    async def finish(self, chat_id: int, user: dict, w: dict, msg_id: int | None) -> None:
        uid = user["id"]
        gid = w["gid"]
        data = self.build(w["d"])
        self.db.add_player(uid, user.get("username"), user.get("first_name", ""))
        self.game.emit({"type": "created", "player": user.get("first_name", ""), "name": data["name"]}, gid)
        ch = self.game.create_char(gid, data, uid)
        self.drop(uid)
        if msg_id:
            try:
                await self.bot.call("editMessageReplyMarkup", chat_id=chat_id, message_id=msg_id)
            except Exception:
                pass
        await self.game.ui.welcome(ch)
