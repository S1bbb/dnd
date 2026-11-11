"""Единый движок правил D&D 5e (2014). Всё, что считается, считается здесь — сайт, бот и виджеты
только показывают результат. Каждое итоговое значение несёт разбивку «из чего сложилось».

Персонаж v2 (dict):
  name, race, sub, bg, al, ancestry, xp
  classes  [{c, sub, lvl, hp:[кости хитов за уровни после первого]}]
  st       итоговые значения характеристик (база + раса + УХ), st_log — откуда они
  sk / ex  владение / компетентность навыками (индексы rules.SKILLS); sv_extra — доп. спасброски
  profs    {armor, weapons, tools, langs}; feats [названия]; picks {умение: [выбор]}
  items    инвентарь; gold; sp {known, prep}; fav [избранное]
  hp, temp, death [успехи, провалы], conc, conds [состояния], exh (истощение)
  used     {slot1.., pact, res:<ключ>, hd:<кость>} — потраченные ресурсы
  effects  [{id, n, src, mods, add, adv, dis, rounds, conc_of}]; adj [{id, field, val, reason}]
Старые персонажи (без "v": 2) считаются по прежним полям: lvl, cls, ac, mhp, spd, sv, atk."""
import math
import re
import secrets

from . import dice
from .data.backgrounds import BACKGROUNDS, FEATS
from .data.classes import CLASSES, FULL_SLOTS, PACT_SLOTS, class_info
from .data.equipment import parse_items
from .data.races import DRAGON_ANCESTRY, race_info
from .data.spells import SPELLS
from .rules import ABILITIES, ABILITY_RU, ABILITY_SHORT, SKILLS, mod, prof_bonus

SKILL_IDX = {n: i for i, (n, _) in enumerate(SKILLS)}
AB_BY_SHORT = {v: k for k, v in ABILITY_SHORT.items()}

CONDITIONS = {
    "Ослеплён": "Не видит, проверки зрения проваливаются. Его атаки с помехой, атаки по нему с преимуществом.",
    "Очарован": "Не может атаковать очаровавшего, тот с преимуществом на социальные проверки.",
    "Оглох": "Не слышит, проверки слуха проваливаются.",
    "Испуган": "Помеха на проверки и атаки, пока видит источник страха; не может приблизиться к нему.",
    "Схвачен": "Скорость 0.",
    "Недееспособен": "Не может совершать действия и реакции.",
    "Невидим": "Его атаки с преимуществом, атаки по нему с помехой.",
    "Парализован": "Недееспособен, не двигается и не говорит. Провал спасбросков СИЛ и ЛОВ. Атаки по нему с преимуществом, вблизи — крит.",
    "Окаменел": "Превращён в камень: недееспособен, провал СИЛ и ЛОВ, сопротивление всему урону.",
    "Отравлен": "Помеха на броски атаки и проверки характеристик.",
    "Сбит с ног": "Только ползает. Его атаки с помехой; атаки по нему вблизи с преимуществом, издалека с помехой.",
    "Опутан": "Скорость 0. Его атаки и спасброски ЛОВ с помехой, атаки по нему с преимуществом.",
    "Ошеломлён": "Недееспособен, провал спасбросков СИЛ и ЛОВ, атаки по нему с преимуществом.",
    "Без сознания": "Недееспособен, падает, провал СИЛ и ЛОВ, атаки по нему с преимуществом, вблизи — крит.",
}
EXHAUSTION = ["", "помеха на проверки", "скорость вдвое", "помеха на атаки и спасброски",
              "максимум хитов вдвое", "скорость 0", "смерть"]

# Готовые эффекты: мастер и игрок навешивают их одной кнопкой
EFFECTS = {
    "Благословение": dict(add={"attack": "1d4", "save": "1d4"}, rounds=10, note="+1d4 к атакам и спасброскам"),
    "Порча": dict(add={"attack": "-1d4", "save": "-1d4"}, rounds=10, note="−1d4 к атакам и спасброскам"),
    "Щит веры": dict(mods={"ac": 2}, rounds=100, note="+2 КД"),
    "Щит": dict(mods={"ac": 5}, rounds=1, note="+5 КД до начала хода"),
    "Доспехи мага": dict(mods={"ac_base": 13}, note="КД 13 + ЛОВ без доспехов"),
    "Ускорение": dict(mods={"ac": 2, "spd_x": 2}, adv=["save:dex"], rounds=10, note="+2 КД, скорость ×2, преимущество ЛОВ-спасы"),
    "Ярость": dict(adv=["check:str", "save:str"], mods={"rage": 1}, rounds=10, note="Преимущество СИЛ, бонус урона, сопротивление физ. урону"),
    "Уклонение": dict(adv=["save:dex"], rounds=1, note="Атаки по тебе с помехой"),
    "Указание": dict(add={"check": "1d4"}, rounds=10, once=True, note="+1d4 к одной проверке"),
    "Сопротивление": dict(add={"save": "1d4"}, rounds=10, once=True, note="+1d4 к одному спасброску"),
    "Вдохновение барда": dict(add={"any": "1d6"}, once=True, note="Кость к одной атаке, проверке или спасброску"),
    "Героизм": dict(note="Не испуган, временные хиты каждый ход", rounds=10),
    "Подмога": dict(mods={"hp_max": 5}, note="+5 к максимуму хитов"),
    "Дубовая кора": dict(mods={"ac_min": 16}, rounds=600, note="КД не меньше 16"),
    "Невидимость": dict(adv=["attack"], note="Атаки с преимуществом, по тебе — с помехой"),
    "Божественное благоволение": dict(add={"dmg": "1d4"}, rounds=10, note="+1d4 излучением к урону оружием"),
    "Метка охотника": dict(add={"dmg": "1d6"}, rounds=600, note="+1d6 к урону по помеченной цели"),
    "Сглаз": dict(add={"dmg": "1d6"}, rounds=600, note="+1d6 некротическим по проклятой цели"),
}

ADJ_FIELDS = {
    "ac": "КД", "init": "Инициатива", "spd": "Скорость", "hp_max": "Макс. хиты", "atk": "Атаки (попадание)",
    "dmg": "Урон оружием", "save_all": "Все спасброски", "spell_dc": "СЛ заклинаний", "spell_atk": "Атака заклинанием",
    "passive": "Пассивная Внимательность",
    **{f"ab:{a}": ABILITY_RU[a] for a in ABILITIES}, **{f"save:{a}": f"Спасбросок {ABILITY_SHORT[a]}" for a in ABILITIES},
    **{f"skill:{i}": n for i, (n, _) in enumerate(SKILLS)},
}


def uid() -> str:
    return secrets.token_hex(4)


def sg(n: int) -> str:
    return f"{n:+d}"


# ---------- вспомогательное ----------
def is_v2(ch: dict) -> bool:
    return ch.get("v") == 2 and bool(ch.get("classes"))


def level(ch: dict) -> int:
    return sum(c["lvl"] for c in ch["classes"]) if is_v2(ch) else int(ch.get("lvl", 1))


def class_level(ch: dict, cls: str) -> int:
    return sum(c["lvl"] for c in ch.get("classes", []) if c["c"] == cls)


def has_sub(ch: dict, cls: str, sub: str) -> bool:
    return any(c["c"] == cls and c.get("sub") == sub for c in ch.get("classes", []))


def picked(ch: dict, option: str) -> bool:
    return any(option in v for v in ch.get("picks", {}).values())


def _mods_sum(ch: dict, field: str) -> list[tuple[str, int]]:
    """Ручные корректировки мастера и активные эффекты для поля."""
    out = [(f"Мастер: {a['reason'] or 'корректировка'}", int(a["val"])) for a in ch.get("adj", []) if a["field"] == field]
    for e in ch.get("effects", []):
        v = (e.get("mods") or {}).get(field)
        if isinstance(v, (int, float)) and v:
            out.append((f"Эффект: {e['n']}", int(v)))
    return out


def _parts_total(parts: list[tuple[str, int]]) -> int:
    return sum(v for _, v in parts)


def equipped(ch: dict, kind: str) -> list[dict]:
    return [i for i in ch.get("items", []) if i.get("kind") == kind and i.get("eq")]


def race_data(ch: dict) -> dict:
    r = ch.get("race_key")
    if not r:
        return {}
    return race_info(r, ch.get("sub_key"))


# ---------- расчёт ----------
def derive(ch: dict) -> dict:
    """Все производные параметры персонажа с разбивкой."""
    v2 = is_v2(ch)
    lvl = level(ch)
    pb = prof_bonus(lvl)
    rd = race_data(ch) if v2 else {}
    cls_levels = {c["c"]: c["lvl"] for c in ch.get("classes", [])} if v2 else {}
    out = {"lvl": lvl, "pb": pb, "v2": v2, "warn": []}

    # характеристики
    abil = {}
    for a in ABILITIES:
        base = int(ch.get("st", {}).get(a, 10))
        parts = [(x["src"], x["val"]) for x in ch.get("st_log", []) if x["ab"] == a] or [("Значение", base)]
        extra = _mods_sum(ch, f"ab:{a}")
        score = base + _parts_total(extra)
        abil[a] = {"score": score, "mod": mod(score), "parts": parts + extra}
    out["ab"] = abil
    m = {a: abil[a]["mod"] for a in ABILITIES}
    out["m"] = m

    # преимущества и помехи от состояний и эффектов
    adv: dict[str, int] = {}

    def a_(key, v):
        adv[key] = adv.get(key, 0) | (1 if v > 0 else 2)  # битовая маска: 1 — преимущество, 2 — помеха
    conds = set(ch.get("conds", []))
    exh = int(ch.get("exh", 0))
    for c in ("Отравлен", "Испуган"):
        if c in conds:
            a_("attack", -1), a_("check", -1)
    for c in ("Ослеплён", "Сбит с ног", "Опутан"):
        if c in conds:
            a_("attack", -1)
    if "Опутан" in conds:
        a_("save:dex", -1)
    if "Невидим" in conds:
        a_("attack", 1)
    if exh >= 1:
        a_("check", -1)
    if exh >= 3:
        a_("attack", -1), a_("save", -1)
    autofail = conds & {"Парализован", "Ошеломлён", "Без сознания", "Окаменел"}
    out["autofail"] = ["str", "dex"] if autofail else []
    adds: dict[str, list[tuple[str, str]]] = {}
    for e in ch.get("effects", []):
        for k in e.get("adv", []):
            a_(k, 1)
        for k in e.get("dis", []):
            a_(k, -1)
        for k, d in (e.get("add") or {}).items():
            adds.setdefault(k, []).append((e["n"], d))
    # доспех без владения — помеха на всё по СИЛ/ЛОВ
    armor = equipped(ch, "armor")
    shield = equipped(ch, "shield")
    profs = ch.get("profs", {})
    armor_prof = set(profs.get("armor", []))
    if v2:
        for it in armor:
            if it.get("akind") not in armor_prof:
                out["warn"].append(f"Нет владения доспехом «{it['n']}»: помеха на СИЛ/ЛОВ и нельзя колдовать")
                for k in ("attack", "check:str", "check:dex", "save:str", "save:dex"):
                    a_(k, -1)
        if shield and "щиты" not in armor_prof and not any("щит" in p for p in armor_prof):
            out["warn"].append("Нет владения щитом")
    out["adv"] = adv
    out["adds"] = adds

    # спасброски
    if v2:
        first = ch["classes"][0]["c"]
        sv = set(CLASSES[first]["saves"]) | set(ch.get("sv_extra", []))
        if class_level(ch, "monk") >= 14:
            sv |= set(ABILITIES)
        if class_level(ch, "rogue") >= 15:
            sv.add("wis")
    else:
        sv = set(ch.get("sv", []))
    saves = {}
    for a in ABILITIES:
        parts = [(f"{ABILITY_SHORT[a]}", m[a])]
        if a in sv:
            parts.append(("Мастерство", pb))
        parts += _mods_sum(ch, f"save:{a}") + _mods_sum(ch, "save_all")
        saves[a] = {"bonus": _parts_total(parts), "prof": a in sv, "parts": parts}
    out["saves"] = saves

    # навыки
    sk, ex = set(ch.get("sk", [])), set(ch.get("ex", []))
    jack = v2 and class_level(ch, "bard") >= 2
    athlete = v2 and has_sub(ch, "fighter", "champion") and class_level(ch, "fighter") >= 7
    skills = []
    for i, (name, ab) in enumerate(SKILLS):
        parts = [(ABILITY_SHORT[ab], m[ab])]
        if i in ex:
            parts.append(("Компетентность", pb * 2))
        elif i in sk:
            parts.append(("Мастерство", pb))
        elif jack:
            parts.append(("Мастер на все руки", pb // 2))
        elif athlete and ab in ("str", "dex", "con"):
            parts.append(("Выдающийся атлет", math.ceil(pb / 2)))
        parts += _mods_sum(ch, f"skill:{i}")
        skills.append({"n": name, "ab": ab, "bonus": _parts_total(parts), "prof": i in sk, "exp": i in ex, "parts": parts})
    out["skills"] = skills
    per = skills[SKILL_IDX["Внимательность"]]["bonus"]
    pparts = [("10 + Внимательность", 10 + per)]
    if "Наблюдательный" in ch.get("feats", []):
        pparts.append(("Наблюдательный", 5))
    pparts += _mods_sum(ch, "passive")
    out["passive"] = {"val": _parts_total(pparts), "parts": pparts}

    # инициатива
    iparts = [("ЛОВ", m["dex"])]
    if jack:
        iparts.append(("Мастер на все руки", pb // 2))
    elif athlete:
        iparts.append(("Выдающийся атлет", math.ceil(pb / 2)))
    if "Бдительный" in ch.get("feats", []):
        iparts.append(("Бдительный", 5))
    iparts += _mods_sum(ch, "init")
    out["init"] = {"bonus": _parts_total(iparts), "parts": iparts,
                   "adv": v2 and class_level(ch, "barbarian") >= 7}

    # КД
    out["ac"] = _armor_class(ch, v2, m, armor, shield)

    # скорость
    if v2:
        sparts = [("Раса", rd.get("spd", 30))]
        heavy = any(i.get("akind") == "тяжёлые" for i in armor)
        if class_level(ch, "monk") >= 2 and not armor and not shield:
            ml = class_level(ch, "monk")
            sparts.append(("Движение без доспехов", 10 + 5 * sum(ml >= x for x in (6, 10, 14, 18))))
        if class_level(ch, "barbarian") >= 5 and not heavy:
            sparts.append(("Быстрое передвижение", 10))
        if "Подвижный" in ch.get("feats", []):
            sparts.append(("Подвижный", 10))
        for it in armor:
            if it.get("strreq") and abil["str"]["score"] < it["strreq"] and ch.get("race_key") != "dwarf":
                sparts.append((f"{it['n']}: нужна СИЛ {it['strreq']}", -10))
    else:
        sparts = [("Скорость", int(ch.get("spd", 30)))]
    sparts += _mods_sum(ch, "spd")
    spd = _parts_total(sparts)
    if any((e.get("mods") or {}).get("spd_x") for e in ch.get("effects", [])):
        spd *= 2
        sparts.append(("Ускорение", "×2"))
    if exh >= 2:
        spd //= 2
        sparts.append(("Истощение", "÷2"))
    if exh >= 5 or conds & {"Схвачен", "Опутан"}:
        spd = 0
        sparts.append(("Состояние", "0"))
    out["spd"] = {"val": max(0, spd), "parts": sparts}

    # хиты
    out["hp_max"] = _hp_max(ch, v2, m, lvl, rd)
    out["hd"] = _hit_dice(ch) if v2 else []

    # заклинания и ресурсы
    out["cast"] = _casting(ch, m, pb) if v2 else []
    out["slots"], out["pact"] = _slots(ch) if v2 else ({}, None)
    out["res"] = _resources(ch, m) if v2 else []
    out["attacks"] = _attacks(ch, v2, m, pb, shield, out)
    out["features"] = features(ch) if v2 else []
    out["resist"] = sorted(set(rd.get("resist", [])) | _ancestry_resist(ch))
    return out


def _ancestry_resist(ch):
    a = ch.get("ancestry")
    return {DRAGON_ANCESTRY[a][0]} if a in DRAGON_ANCESTRY else set()


def _armor_class(ch, v2, m, armor, shield):
    if not v2:
        parts = [("КД", int(ch.get("ac", 10)))]
    else:
        cands = []
        if armor:
            a = armor[0]
            dx = m["dex"]
            cap = a.get("dexmax")
            if a.get("akind") == "средние" and "Мастер средних доспехов" in ch.get("feats", []):
                cap = 3
            if cap is not None:
                dx = min(dx, cap)
            cands.append([(a["n"], a["ac"] + a.get("bonus", 0))] + ([("ЛОВ", dx)] if cap != 0 else []))
        else:
            cands.append([("Без доспехов", 10), ("ЛОВ", m["dex"])])
            if class_level(ch, "barbarian"):
                cands.append([("Защита без доспехов", 10), ("ЛОВ", m["dex"]), ("ТЕЛ", m["con"])])
            if class_level(ch, "monk") and not shield:
                cands.append([("Защита без доспехов", 10), ("ЛОВ", m["dex"]), ("МДР", m["wis"])])
            if has_sub(ch, "sorcerer", "draconic"):
                cands.append([("Драконья устойчивость", 13), ("ЛОВ", m["dex"])])
            for e in ch.get("effects", []):
                b = (e.get("mods") or {}).get("ac_base")
                if b:
                    cands.append([(e["n"], b), ("ЛОВ", m["dex"])])
        parts = max(cands, key=_parts_total)
        if shield:
            parts.append((shield[0]["n"], 2 + shield[0].get("bonus", 0)))
        if armor and picked(ch, "Оборона"):
            parts.append(("Боевой стиль: Оборона", 1))
    parts = parts + _mods_sum(ch, "ac")
    val = _parts_total(parts)
    for e in ch.get("effects", []):
        mn = (e.get("mods") or {}).get("ac_min")
        if mn and val < mn:
            parts.append((f"{e['n']}: минимум {mn}", mn - val))
            val = mn
    return {"val": val, "parts": parts}


def _hp_max(ch, v2, m, lvl, rd):
    if not v2:
        parts = [("Максимум", int(ch.get("mhp", 10)))]
    else:
        parts = []
        first = True
        for c in ch["classes"]:
            hd = CLASSES[c["c"]]["hd"]
            rolls = list(c.get("hp", []))
            if first:
                parts.append((f"{CLASSES[c['c']]['n']}: 1 уровень (максимум d{hd})", hd))
                first = False
                n_other = c["lvl"] - 1
            else:
                n_other = c["lvl"]
            if n_other:
                vals = rolls[:n_other] + [hd // 2 + 1] * max(0, n_other - len(rolls))
                parts.append((f"{CLASSES[c['c']]['n']}: ещё {n_other} ур. (d{hd})", sum(vals)))
        parts.append((f"ТЕЛ {sg(m['con'])} × {lvl}", m["con"] * lvl))
        if rd.get("hp_lvl"):
            parts.append(("Дварфская выдержка", rd["hp_lvl"] * lvl))
        sl = class_level(ch, "sorcerer") if has_sub(ch, "sorcerer", "draconic") else 0
        if sl:
            parts.append(("Драконья устойчивость", sl))
        if "Крепыш" in ch.get("feats", []):
            parts.append(("Крепыш", 2 * lvl))
    parts += _mods_sum(ch, "hp_max")
    val = max(1, _parts_total(parts))
    if int(ch.get("exh", 0)) >= 4:
        val //= 2
        parts.append(("Истощение 4", "÷2"))
    return {"val": val, "parts": parts}


def _hit_dice(ch):
    out = {}
    for c in ch["classes"]:
        k = f"d{CLASSES[c['c']]['hd']}"
        out[k] = out.get(k, 0) + c["lvl"]
    used = ch.get("used", {})
    return [{"die": k, "max": n, "left": n - int(used.get(f"hd:{k}", 0))} for k, n in sorted(out.items())]


def _casting(ch, m, pb):
    """Заклинательные классы персонажа: характеристика, СЛ, атака, лимиты."""
    out = []
    for c in ch["classes"]:
        ci = class_info(c["c"], c.get("sub"))
        if not ci.get("caster"):
            continue
        ab = ci["cast_ab"]
        l = c["lvl"]
        dc_parts = [("8", 8), ("Мастерство", pb), (ABILITY_SHORT[ab], m[ab])] + _mods_sum(ch, "spell_dc")
        at_parts = [("Мастерство", pb), (ABILITY_SHORT[ab], m[ab])] + _mods_sum(ch, "spell_atk")
        info = {"c": c["c"], "n": ci["n"], "ab": ab, "dc": _parts_total(dc_parts), "atk": _parts_total(at_parts),
                "dc_parts": dc_parts, "atk_parts": at_parts, "kind": ci["caster"],
                "cantrips": ci["cantrips"](l) if ci.get("cantrips") else 0,
                "max_lvl": _max_spell_level(ci["caster"], l)}
        if ci.get("prepared"):
            if c["c"] == "paladin":
                info["prepared"] = max(1, m[ab] + l // 2) if l >= 2 else 0
            else:
                info["prepared"] = max(1, m[ab] + l)
        if ci.get("known"):
            info["known"] = ci["known"](l)
        if ci.get("spellbook"):
            info["book"] = 6 + 2 * (l - 1)
        info["always"] = always_prepared(ch, c)
        out.append(info)
    return out


def _max_spell_level(kind, l):
    if kind == "full":
        return len(FULL_SLOTS[l - 1])
    if kind == "half":
        return len(FULL_SLOTS[math.ceil(l / 2) - 1]) if l >= 2 else 0
    if kind == "third":
        return len(FULL_SLOTS[math.ceil(l / 3) - 1]) if l >= 3 else 0
    if kind == "pact":
        return PACT_SLOTS[l - 1][1]
    return 0


def _slots(ch):
    casters = []
    for c in ch["classes"]:
        ci = class_info(c["c"], c.get("sub"))
        if ci.get("caster") in ("full", "half", "third"):
            casters.append((ci["caster"], c["lvl"]))
    if len(casters) == 1:
        kind, l = casters[0]
        cl = {"full": l, "half": math.ceil(l / 2) if l >= 2 else 0, "third": math.ceil(l / 3) if l >= 3 else 0}[kind]
    else:
        cl = sum(l if k == "full" else l // 2 if k == "half" else l // 3 for k, l in casters)
    used = ch.get("used", {})
    slots = {}
    if cl:
        for i, n in enumerate(FULL_SLOTS[min(cl, 20) - 1], 1):
            slots[i] = {"max": n, "left": max(0, n - int(used.get(f"slot{i}", 0)))}
    pact = None
    wl = class_level(ch, "warlock")
    if wl:
        n, lv = PACT_SLOTS[wl - 1]
        pact = {"lvl": lv, "max": n, "left": max(0, n - int(used.get("pact", 0)))}
    return slots, pact


def _resources(ch, m):
    out = []
    used = ch.get("used", {})
    for c in ch["classes"]:
        ci = CLASSES[c["c"]]
        srcs = list(ci.get("resources", []))
        sub = ci["subs"].get(c.get("sub") or "", {})
        srcs += sub.get("resources", [])
        for key, n, fn, reset, frm in srcs:
            if c["lvl"] < frm:
                continue
            mx = fn(c["lvl"], m)
            if mx <= 0:
                continue
            out.append({"key": key, "n": n, "max": mx, "left": max(0, mx - int(used.get(f"res:{key}", 0))), "reset": reset})
    for f in ch.get("feats", []):
        r = FEATS.get(f, {}).get("res")
        if r:
            key, n, mx, reset = r
            out.append({"key": key, "n": n, "max": mx, "left": max(0, mx - int(used.get(f"res:{key}", 0))), "reset": reset})
    if ch.get("ancestry"):
        out.append({"key": "breath", "n": "Оружие дыхания", "max": 1, "left": 1 - int(used.get("res:breath", 0)), "reset": "short"})
    for r in ch.get("custom_res", []):
        out.append({"key": r["key"], "n": r["n"], "max": r["max"], "left": max(0, r["max"] - int(used.get(f"res:{r['key']}", 0))), "reset": r.get("reset", "long")})
    return out


def _weapon_attack(ch, it, m, pb, shield, D):
    props = it.get("props", "")
    ranged = it.get("rng") == "дальн"
    finesse = "ф" in props.split()
    monk = class_level(ch, "monk") and (it["n"] == "Короткий меч" or (it.get("cat") == "простое" and not ranged
                                                                         and "т" not in props.split() and "д" not in props.split()))
    if ranged:
        ab = "dex"
    elif finesse or monk:
        ab = "dex" if m["dex"] >= m["str"] else "str"
    else:
        ab = "str"
    profs = set(ch.get("profs", {}).get("weapons", [])) if is_v2(ch) else {"простое", "воинское"}
    prof = it.get("cat") in profs or it["n"] in profs
    hit = [(ABILITY_SHORT[ab], m[ab])]
    if prof:
        hit.append(("Мастерство", pb))
    if ranged and picked(ch, "Стрельба"):
        hit.append(("Боевой стиль: Стрельба", 2))
    if it.get("bonus"):
        hit.append(("Магия", it["bonus"]))
    hit += _mods_sum(ch, "atk")
    die = it.get("dmg", "1d4")
    vers = re.search(r"у:(\S+)", props)
    two = False
    if vers and not shield:
        die, two = vers.group(1), True
    if monk and class_level(ch, "monk"):
        md = _martial_die(class_level(ch, "monk"))
        if dice_avg(md) > dice_avg(die):
            die = md
    dmg_b = m[ab] + it.get("bonus", 0)
    if not ranged and picked(ch, "Дуэлянт") and "д" not in props.split() and not two:
        dmg_b += 2
    dmg_b += _parts_total(_mods_sum(ch, "dmg"))
    if not ranged and any((e.get("mods") or {}).get("rage") for e in ch.get("effects", [])):
        bl = class_level(ch, "barbarian")
        dmg_b += 2 if bl < 9 else 3 if bl < 16 else 4
    extra = "".join(f"+{d}" for _, d in D["adds"].get("dmg", []))
    return {"n": it["n"] + (" (двумя руками)" if two else ""), "hit": _parts_total(hit), "hit_parts": hit,
            "dmg": f"{die}{sg(dmg_b) if dmg_b else ''}{extra}", "type": it.get("dtype", ""), "kind": "weapon",
            "note": "" if prof else "без владения"}


def _martial_die(l):
    return "1d4" if l < 5 else "1d6" if l < 11 else "1d8" if l < 17 else "1d10"


def dice_avg(f: str) -> float:
    try:
        groups, bonus = dice.parse(f)
    except dice.DiceError:
        return 0
    return sum(g.sign * g.count * (g.sides + 1) / 2 for g in groups) + bonus


def cantrip_dice(d: str, lvl: int) -> str:
    k = 1 + sum(lvl >= x for x in (5, 11, 17))
    mm = re.match(r"^(\d+)d(\d+)(.*)$", d)
    if not mm:
        return d
    return f"{int(mm.group(1)) * k}d{mm.group(2)}{mm.group(3)}"


def _attacks(ch, v2, m, pb, shield, D):
    out = []
    if v2:
        for it in ch.get("items", []):
            if it.get("kind") == "weapon" and it.get("eq") and it.get("dmg") not in (None, "0"):
                out.append(_weapon_attack(ch, it, m, pb, shield, D))
        ml = class_level(ch, "monk")
        best = max(m["str"], m["dex"]) if ml else m["str"]
        out.append({"n": "Безоружный удар", "hit": best + pb + _parts_total(_mods_sum(ch, "atk")),
                    "hit_parts": [("Характеристика", best), ("Мастерство", pb)],
                    "dmg": f"{_martial_die(ml)}{sg(best)}" if ml else (f"1{sg(best)}" if best else "1"), "type": "дробящий", "kind": "weapon"})
        # заговоры-атаки и заговоры со спасброском
        for name in known_cantrips(ch):
            s = SPELLS.get(name)
            if not s or not s["m"].get("dice"):
                continue
            cs = next((c for c in D["cast"] if c["c"] in s["cls"]), None)
            if not cs:  # расовый заговор: ХАР у тифлинга/дроу, ИНТ у эльфа и гнома
                ab = "int" if ch.get("race_key") in ("elf", "gnome") and ch.get("sub_key") != "drow" else "cha"
                cs = {"atk": pb + m[ab], "dc": 8 + pb + m[ab], "atk_parts": [("Мастерство", pb), (ABILITY_SHORT[ab], m[ab])]}
            mm = s["m"]
            dmg = cantrip_dice(mm["dice"], D["lvl"])
            if name == "Мистический заряд" and picked(ch, "Мучительный взрыв"):
                dmg += sg(m["cha"])
            if mm["kind"] == "a":
                out.append({"n": name, "hit": cs["atk"], "hit_parts": cs["atk_parts"], "dmg": dmg, "type": mm.get("type", ""), "kind": "spell"})
            elif mm["kind"] == "s":
                out.append({"n": f"{name} (спас {mm['save']} СЛ {cs['dc']})", "hit": None, "dmg": dmg, "type": mm.get("type", ""), "kind": "spell"})
        rl = class_level(ch, "rogue")
        if rl:
            out.append({"n": "Скрытая атака", "hit": None, "dmg": f"{math.ceil(rl / 2)}d6", "type": "", "kind": "extra"})
        if class_level(ch, "paladin") >= 2:
            out.append({"n": "Божественная кара (ячейка 1)", "hit": None, "dmg": "2d8", "type": "излучение", "kind": "extra"})
        if ch.get("ancestry") in DRAGON_ANCESTRY:
            dt, shape, sv = DRAGON_ANCESTRY[ch["ancestry"]]
            dc = 8 + m["con"] + pb
            n = 2 + sum(D["lvl"] >= x for x in (6, 11, 16))
            out.append({"n": f"Дыхание ({shape}, спас {sv} СЛ {dc})", "hit": None, "dmg": f"{n}d6", "type": dt, "kind": "extra"})
    for a in ch.get("atk", []):  # ручные атаки (в т. ч. у старых персонажей)
        out.append({"n": a["n"], "hit": a.get("hit", 0), "dmg": a["dmg"], "type": "", "kind": "manual",
                    "hit_parts": [("Задано вручную", a.get("hit", 0))]})
    return out


def known_cantrips(ch):
    sp = ch.get("sp", {})
    names = list(sp.get("known", [])) + list(sp.get("prep", []))
    lvl = level(ch)
    rd = race_data(ch)
    for n, at in rd.get("spells", []):
        if lvl >= at:
            names.append(n)
    return [n for n in dict.fromkeys(names) if SPELLS.get(n, {}).get("lvl") == 0]


def always_prepared(ch, c) -> list[str]:
    ci = CLASSES[c["c"]]
    sub = ci["subs"].get(c.get("sub") or "")
    out = []
    if sub and sub.get("spells"):
        for lv, names in sub["spells"].items():
            if c["lvl"] >= lv:
                out += names
    return out


def spell_list(ch: dict) -> list[str]:
    """Все заклинания, которые персонаж может творить сейчас (известные, подготовленные, всегда готовые, расовые)."""
    sp = ch.get("sp", {})
    names = list(sp.get("known", [])) + list(sp.get("prep", []))
    for c in ch.get("classes", []):
        names += always_prepared(ch, c)
    lvl = level(ch)
    for n, at in race_data(ch).get("spells", []):
        if lvl >= at:
            names.append(n)
    return [n for n in dict.fromkeys(names) if n in SPELLS]


def features(ch: dict) -> list[dict]:
    """Список умений с источником: раса, классы, подклассы, предыстория, черты, выборы."""
    out = []
    rd = race_data(ch)
    for n, d in rd.get("traits", []):
        out.append({"n": n, "d": d, "src": rd.get("sub_n") or rd.get("n", "Раса")})
    if ch.get("ancestry"):
        dt, shape, sv = DRAGON_ANCESTRY.get(ch["ancestry"], ("", "", ""))
        out.append({"n": f"Предок: {ch['ancestry']} дракон", "d": f"Дыхание {shape}, урон — {dt}, спас {sv}. Сопротивление: {dt}.", "src": "Раса"})
    for c in ch.get("classes", []):
        ci = CLASSES[c["c"]]
        for lv in range(1, c["lvl"] + 1):
            for f in ci["features"].get(lv, []):
                if f[0] == "Увеличение характеристик":
                    continue
                out.append({"n": f[0], "d": f[1], "src": f"{ci['n']} {lv}"})
            sub = ci["subs"].get(c.get("sub") or "")
            if sub:
                for f in sub["features"].get(lv, []):
                    out.append({"n": f[0], "d": f[1], "src": f"{sub['n']} {lv}"})
    bg = BACKGROUNDS.get(ch.get("bg_key") or "")
    if bg:
        out.append({"n": bg["feat"][0], "d": bg["feat"][1], "src": f"Предыстория: {bg['n']}"})
    for f in ch.get("feats", []):
        out.append({"n": f, "d": FEATS.get(f, {}).get("d", ""), "src": "Черта"})
    from .data.choices import CHOICE_DESC
    for k, vals in ch.get("picks", {}).items():
        for v in vals:
            out.append({"n": v, "d": CHOICE_DESC.get(v, ""), "src": k})
    return out


# ---------- броски ----------
def _adv_for(D, kind: str, ab: str | None) -> int:
    keys = {"attack": ["attack"], "check": ["check", f"check:{ab}"], "save": ["save", f"save:{ab}"]}[kind]
    mask = 0
    for k in keys:
        mask |= D["adv"].get(k, 0)
    return {0: 0, 1: 1, 2: -1, 3: 0}[mask]


def _adds(D, kind):
    lst = D["adds"].get(kind, []) + D["adds"].get("any", [])
    return "".join((d if d.startswith("-") else "+" + d) for _, d in lst)


def roll_spec(ch: dict, kind: str, key: str, D: dict | None = None) -> dict:
    """Что и как бросать: {title, formula, adv, note, dmg?}. kind: c|s|k|i|a|x|d"""
    D = D or derive(ch)
    if kind == "i":
        return {"title": "Инициатива", "formula": f"1d20{sg(D['init']['bonus'])}", "adv": 1 if D["init"]["adv"] else _adv_for(D, "check", "dex")}
    if kind == "k":
        s = D["skills"][int(key)]
        return {"title": s["n"], "formula": f"1d20{sg(s['bonus'])}{_adds(D, 'check')}", "adv": _adv_for(D, "check", s["ab"])}
    if kind in ("c", "s"):
        if key not in ABILITIES:
            raise ValueError("bad ability")
        if kind == "s":
            if key in D["autofail"]:
                return {"title": f"Спасбросок: {ABILITY_RU[key]}", "formula": "0", "adv": 0, "note": "автоматический провал (состояние)"}
            return {"title": f"Спасбросок: {ABILITY_RU[key]}", "formula": f"1d20{sg(D['saves'][key]['bonus'])}{_adds(D, 'save')}",
                    "adv": _adv_for(D, "save", key)}
        return {"title": f"Проверка: {ABILITY_RU[key]}", "formula": f"1d20{sg(D['m'][key])}{_adds(D, 'check')}", "adv": _adv_for(D, "check", key)}
    if kind in ("a", "x"):
        a = D["attacks"][int(key)]
        if a["hit"] is None or kind == "x":
            return {"title": a["n"], "formula": None, "dmg": a["dmg"], "adv": 0}
        return {"title": a["n"], "formula": f"1d20{sg(a['hit'])}{_adds(D, 'attack')}", "dmg": a["dmg"], "adv": _adv_for(D, "attack", None),
                "crit": crit_range(ch)}
    if kind == "d":
        return {"title": "Спасбросок от смерти", "formula": "1d20", "adv": 0}
    raise ValueError("bad kind")


def crit_range(ch) -> int:
    fl = class_level(ch, "fighter")
    if has_sub(ch, "fighter", "champion"):
        return 18 if fl >= 15 else 19 if fl >= 3 else 20
    return 20


def check_by_name(ch: dict, name: str) -> tuple[str, str] | None:
    """«скрытность» → ('k', '15'), «ловкость» → ('c','dex'), «спас лов» → ('s','dex'), «инициатива» → ('i','0')."""
    t = name.lower().strip()
    if not t:
        return None
    if t.startswith("иниц"):
        return "i", "0"
    save = t.startswith("спас")
    t2 = t.split(maxsplit=1)[1] if save and " " in t else t
    for a in ABILITIES:
        if t2.startswith(ABILITY_RU[a].lower()[:3]) or t2.startswith(ABILITY_SHORT[a].lower()):
            return ("s" if save else "c"), a
    for i, (n, _) in enumerate(SKILLS):
        if n.lower().startswith(t[:4]) and len(t) >= 3:
            return "k", str(i)
    return None


# ---------- изменения состояния ----------
def clamp_hp(ch, D=None):
    D = D or derive(ch)
    ch["hp"] = max(0, min(int(ch.get("hp", 0)), D["hp_max"]["val"]))


def damage(ch: dict, n: int, dtype: str = "") -> list[str]:
    D = derive(ch)
    log = []
    n = max(0, int(n))
    if dtype and dtype in D["resist"]:
        n //= 2
        log.append(f"сопротивление ({dtype}): урон вдвое")
    if dtype in ("дробящий", "колющий", "рубящий") and any((e.get("mods") or {}).get("rage") for e in ch.get("effects", [])):
        n //= 2
        log.append("ярость: урон вдвое")
    t = int(ch.get("temp", 0))
    if t:
        absorbed = min(t, n)
        ch["temp"] = t - absorbed
        n -= absorbed
        if absorbed:
            log.append(f"временные хиты поглотили {absorbed}")
    before = int(ch.get("hp", 0))
    if before == 0 and n > 0:
        ch.setdefault("death", [0, 0])
        ch["death"][1] = min(3, ch["death"][1] + 1)
        log.append("урон при 0 хитов: провал спасброска от смерти")
        if n >= D["hp_max"]["val"]:
            ch["death"][1] = 3
    ch["hp"] = max(0, before - n)
    if ch["hp"] == 0 and before > 0:
        overflow = n - before
        if overflow >= D["hp_max"]["val"]:
            ch["dead"] = True
            log.append("☠️ мгновенная смерть: остаток урона ≥ максимума хитов")
        else:
            ch.setdefault("conds", [])
            if "Без сознания" not in ch["conds"]:
                ch["conds"].append("Без сознания")
            ch["death"] = [0, 0]
            log.append("💀 0 хитов: без сознания, спасброски от смерти")
        if ch.get("conc"):
            log += drop_conc(ch)
    elif ch.get("conc") and n > 0:
        log.append(f"🧠 концентрация ({ch['conc']}): спасбросок ТЕЛ СЛ {max(10, n // 2)}")
    return log


def heal(ch: dict, n: int) -> list[str]:
    D = derive(ch)
    if ch.get("dead"):
        return ["мёртвых лечение не поднимает"]
    was0 = int(ch.get("hp", 0)) == 0
    ch["hp"] = min(D["hp_max"]["val"], int(ch.get("hp", 0)) + max(0, int(n)))
    log = []
    if was0 and ch["hp"] > 0:
        ch["death"] = [0, 0]
        ch["conds"] = [c for c in ch.get("conds", []) if c != "Без сознания"]
        log.append("приходит в сознание")
    return log


def set_temp(ch: dict, n: int) -> list[str]:
    old = int(ch.get("temp", 0))
    ch["temp"] = max(old, int(n))
    return [] if ch["temp"] > old or n == 0 else ["временные хиты не складываются — оставлено большее"]


def death_save(ch: dict, total: int, nat: int) -> list[str]:
    ch.setdefault("death", [0, 0])
    s, f = ch["death"]
    if nat == 20:
        ch["hp"] = 1
        ch["death"] = [0, 0]
        ch["conds"] = [c for c in ch.get("conds", []) if c != "Без сознания"]
        return ["💥 20: приходишь в себя с 1 хитом"]
    if nat == 1:
        f += 2
    elif total >= 10:
        s += 1
    else:
        f += 1
    ch["death"] = [min(3, s), min(3, f)]
    if f >= 3:
        ch["dead"] = True
        return ["☠️ три провала — персонаж умер"]
    if s >= 3:
        ch["death"] = [0, 0]
        ch["stable"] = True
        return ["стабилизирован: без сознания, но не умирает"]
    return [f"успехи {ch['death'][0]}/3, провалы {ch['death'][1]}/3"]


def drop_conc(ch: dict, others: list[dict] | None = None) -> list[str]:
    """Сбросить концентрацию: убрать эффекты, которые держались на ней."""
    sp = ch.get("conc")
    if not sp:
        return []
    ch["conc"] = None
    cid = ch.get("_id")
    for o in [ch] + (others or []):
        o["effects"] = [e for e in o.get("effects", []) if not (e.get("conc_of") == cid and e.get("spell") == sp)]
    return [f"концентрация на «{sp}» потеряна"]


def cast(ch: dict, spell: str, slot: int | None) -> list[str]:
    """Потратить ячейку (или ячейку договора) на заклинание; заговоры бесплатны."""
    s = SPELLS.get(spell)
    if not s:
        raise ValueError("Нет такого заклинания")
    log = []
    if s["lvl"] > 0:
        slot = slot or s["lvl"]
        if slot < s["lvl"]:
            raise ValueError("Ячейка ниже круга заклинания")
        D = derive(ch)
        used = ch.setdefault("used", {})
        pact = D["pact"]
        if pact and pact["lvl"] == slot and pact["left"] > 0:
            used["pact"] = int(used.get("pact", 0)) + 1
            log.append(f"ячейка договора {slot} круга")
        elif D["slots"].get(slot, {}).get("left", 0) > 0:
            used[f"slot{slot}"] = int(used.get(f"slot{slot}", 0)) + 1
            log.append(f"ячейка {slot} круга")
        else:
            raise ValueError(f"Нет свободных ячеек {slot} круга")
    if s["conc"]:
        if ch.get("conc"):
            log += drop_conc(ch)
        ch["conc"] = spell
        log.append("🧠 концентрация")
    return log


def spell_roll(ch: dict, spell: str, slot: int | None, D: dict | None = None) -> dict:
    """Формулы для заклинания: атака/спасбросок/урон/лечение с учётом круга."""
    D = D or derive(ch)
    s = SPELLS[spell]
    mm = s["m"]
    if not mm:
        return {}
    cs = next((c for c in D["cast"] if c["c"] in s["cls"]), D["cast"][0] if D["cast"] else None)
    mod_ = D["m"][cs["ab"]] if cs else 0
    d = mm.get("dice", "")
    if s["lvl"] == 0:
        d = cantrip_dice(d, D["lvl"])
    slot = slot or s["lvl"]
    extra = slot - s["lvl"] if s["lvl"] else 0
    if mm.get("up") and extra > 0:
        up = mm["up"]
        mu = re.match(r"^(\d+)d(\d+)(.*)$", up)
        if mu:
            d += f"+{int(mu.group(1)) * extra}d{mu.group(2)}"
            rest = mu.group(3)
            if rest:
                d += "".join([rest] * extra)
        elif up.isdigit():
            d = str(int(d) + int(up) * extra) if d.isdigit() else d + f"+{int(up) * extra}"
    d = d.replace("+m", sg(mod_) if mod_ else "")
    if s["n"] in ("Лечение ран", "Лечащее слово", "Молитва лечения", "Множественное лечащее слово", "Массовое лечение ран") \
            and has_sub(ch, "cleric", "life") and s["lvl"] > 0:
        d += f"+{2 + slot}"  # Последователь жизни
    out = {"kind": mm["kind"], "dice": d, "type": mm.get("type", "")}
    if mm["kind"] == "a" and cs:
        out["hit"] = cs["atk"]
    if mm["kind"] == "s" and cs:
        out["save"], out["dc"], out["half"] = mm["save"], cs["dc"], mm.get("half", False)
    return out


def use_res(ch: dict, key: str, delta: int = 1) -> list[str]:
    D = derive(ch)
    r = next((r for r in D["res"] if r["key"] == key), None)
    if not r:
        raise ValueError("Нет такого ресурса")
    used = ch.setdefault("used", {})
    cur = int(used.get(f"res:{key}", 0))
    new = max(0, min(r["max"], cur + delta))
    if delta > 0 and cur + delta > r["max"]:
        raise ValueError(f"{r['n']}: не осталось")
    used[f"res:{key}"] = new
    return [f"{r['n']}: осталось {r['max'] - new}/{r['max']}"]


def set_slot(ch: dict, slot: str, delta: int) -> list[str]:
    """Вручную потратить/вернуть ячейку: slot='3' или 'pact'."""
    D = derive(ch)
    used = ch.setdefault("used", {})
    key = "pact" if slot == "pact" else f"slot{slot}"
    mx = D["pact"]["max"] if slot == "pact" and D["pact"] else D["slots"].get(int(slot) if slot.isdigit() else 0, {}).get("max", 0)
    used[key] = max(0, min(mx, int(used.get(key, 0)) + delta))
    return [f"ячейки: {mx - used[key]}/{mx}"]


def short_rest(ch: dict, spend: list[str]) -> list[str]:
    """Короткий отдых: потраченные кости хитов (['d10','d10']), ресурсы короткого отдыха, ячейки договора."""
    D = derive(ch)
    log = []
    used = ch.setdefault("used", {})
    healed = 0
    for die in spend:
        hd = next((h for h in D["hd"] if h["die"] == die), None)
        if not hd or hd["left"] <= 0:
            continue
        hd["left"] -= 1
        used[f"hd:{die}"] = int(used.get(f"hd:{die}", 0)) + 1
        r = dice.roll(f"1{die}")
        healed += max(0, r.total + D["m"]["con"])
    if healed:
        log += heal(ch, healed)
        log.insert(0, f"кости хитов: +{healed} хитов")
    for r in D["res"]:
        if r["reset"] == "short":
            used.pop(f"res:{r['key']}", None)
    used.pop("pact", None)
    log.append("ресурсы короткого отдыха восстановлены")
    return log


def spend_hit_die(ch: dict, die: str) -> list[str]:
    """Короткий отдых: потратить одну кость хитов — лечит бросок + ТЕЛ (минимум 0)."""
    D = derive(ch)
    hd = next((h for h in D["hd"] if h["die"] == die), None)
    if not hd or hd["left"] <= 0:
        raise ValueError("Кости этого типа кончились")
    used = ch.setdefault("used", {})
    used[f"hd:{die}"] = int(used.get(f"hd:{die}", 0)) + 1
    r = dice.roll(f"1{die}")
    n = max(0, r.total + D["m"]["con"])
    return [f"кость {die}: {r.total} {sg(D['m']['con'])} = +{n} хитов"] + heal(ch, n)


def long_rest(ch: dict) -> list[str]:
    D = derive(ch)
    used = ch.setdefault("used", {})
    ch["hp"] = D["hp_max"]["val"]
    ch["temp"] = 0
    ch["death"] = [0, 0]
    ch["conds"] = [c for c in ch.get("conds", []) if c not in ("Без сознания",)]
    for h in D["hd"]:
        k = f"hd:{h['die']}"
        back = max(1, h["max"] // 2)
        used[k] = max(0, int(used.get(k, 0)) - back)
    for k in list(used):
        if k.startswith(("slot", "res:")) or k == "pact":
            used.pop(k)
    log = ["хиты, ячейки и ресурсы восстановлены, вернулась половина костей хитов"]
    if int(ch.get("exh", 0)) > 0:
        ch["exh"] = int(ch["exh"]) - 1
        log.append(f"истощение снижено до {ch['exh']}")
    ch["effects"] = [e for e in ch.get("effects", []) if not e.get("rounds")]
    ch["conc"] = None
    return log


def add_effect(ch: dict, name: str, src: str = "", rounds: int | None = None, conc_of=None, spell=None, **custom) -> dict:
    tpl = dict(EFFECTS.get(name, {}))
    tpl.update({k: v for k, v in custom.items() if v is not None})
    e = {"id": uid(), "n": name, "src": src, "mods": tpl.get("mods", {}), "add": tpl.get("add", {}),
         "adv": tpl.get("adv", []), "dis": tpl.get("dis", []), "rounds": rounds if rounds is not None else tpl.get("rounds"),
         "note": tpl.get("note", ""), "once": tpl.get("once", False)}
    if conc_of is not None:
        e["conc_of"], e["spell"] = conc_of, spell or name
    ch.setdefault("effects", []).append(e)
    return e


def tick_effects(ch: dict) -> list[str]:
    """Начало хода персонажа: эффекты с длительностью в раундах тикают."""
    log = []
    keep = []
    for e in ch.get("effects", []):
        if e.get("rounds"):
            e["rounds"] -= 1
            if e["rounds"] <= 0:
                log.append(f"эффект «{e['n']}» закончился")
                continue
        keep.append(e)
    ch["effects"] = keep
    return log


def consume_once(ch: dict, kind: str) -> list[str]:
    """Одноразовые бонусы (Указание, Вдохновение) тратятся после броска."""
    gone = [e for e in ch.get("effects", []) if e.get("once") and (kind in e.get("add", {}) or "any" in e.get("add", {}))]
    ch["effects"] = [e for e in ch.get("effects", []) if e not in gone]
    return [f"«{e['n']}» использовано" for e in gone]


# ---------- создание и повышение уровня ----------
def new_character(a: dict) -> dict:
    """Персонаж 1 уровня из ответов мастера создания (бот или сайт).
    a: name, race, sub, cls, csub, bg, st (база до бонусов), asi (выбранные расовые +1), sk, ex, langs, picks,
       equip (индекс набора), spells {known, prep}, feat, al, about, ancestry, extra_skills, hp_mode"""
    rd = race_info(a["race"], a.get("sub"))
    ci = CLASSES[a["cls"]]
    bg = BACKGROUNDS.get(a.get("bg") or "", {})
    st, log = {}, []
    for ab in ABILITIES:
        base = int(a["st"][ab])
        st[ab] = base
        log.append({"ab": ab, "val": base, "src": "База"})
    bon = dict(rd.get("asi", {}))
    for ab in a.get("asi", []):
        bon[ab] = bon.get(ab, 0) + 1
    for ab, v in bon.items():
        st[ab] += v
        log.append({"ab": ab, "val": v, "src": rd.get("sub_n") or rd["n"]})
    feats = [a["feat"]] if a.get("feat") else []
    for f in feats:
        fd = FEATS.get(f, {})
        if fd.get("asi_any") and a.get("feat_ab"):
            st[a["feat_ab"]] += 1
            log.append({"ab": a["feat_ab"], "val": 1, "src": f"Черта: {f}"})
    sk = set(a.get("sk", []))
    for n in rd.get("skills", []) + bg.get("skills", []):
        sk.add(SKILL_IDX[n])
    sk |= set(a.get("extra_skills", []))
    profs = {
        "armor": sorted(set(ci["armor"]) | set(rd.get("armor", [])) | set((ci["subs"].get(a.get("csub") or "", {}) or {}).get("armor", []))),
        "weapons": sorted(set(ci["weapons"]) | set(rd.get("weapons", [])) | set((ci["subs"].get(a.get("csub") or "", {}) or {}).get("weapons", []))),
        "tools": sorted(set(ci.get("tools", [])) | set(bg.get("tools", [])) | set(rd.get("tools", []))),
        "langs": list(dict.fromkeys(rd.get("langs", []) + a.get("langs", []))),
    }
    for f in feats:
        profs["armor"] = sorted(set(profs["armor"]) | set(FEATS.get(f, {}).get("armor", [])))
    eq = ci["equip"][a.get("equip", 0) or 0][1] if ci.get("equip") else []
    items = parse_items(eq + bg.get("items", []))
    _auto_equip(items, profs)
    ch = {
        "v": 2, "name": a["name"][:40], "race_key": a["race"], "sub_key": a.get("sub"),
        "race": rd.get("sub_n") or rd["n"], "bg_key": a.get("bg"), "bg": bg.get("n", ""), "al": a.get("al", ""),
        "ancestry": a.get("ancestry"), "xp": 0,
        "classes": [{"c": a["cls"], "sub": a.get("csub"), "lvl": 1, "hp": []}],
        "cls": ci["n"], "lvl": 1,
        "st": st, "st_log": log, "sk": sorted(sk), "ex": sorted(set(a.get("ex", [])) & sk), "sv_extra": [],
        "profs": profs, "feats": feats, "picks": a.get("picks", {}), "items": items, "gold": bg.get("gold", 10),
        "sp": {"known": list(a.get("spells", {}).get("known", [])), "prep": list(a.get("spells", {}).get("prep", []))},
        "fav": [], "temp": 0, "death": [0, 0], "conc": None, "conds": [], "exh": 0, "used": {}, "effects": [], "adj": [],
        "notes": a.get("about", ""), "feat": "", "spells": "", "inv": "", "atk": [],
    }
    D = derive(ch)
    ch["hp"] = D["hp_max"]["val"]
    sync_legacy(ch, D)
    return ch


def _auto_equip(items, profs):
    """Надеть лучший доспех с владением, щит и всё оружие."""
    best = None
    for it in items:
        if it.get("kind") == "armor" and it.get("akind") in profs["armor"]:
            if not best or it["ac"] > best["ac"]:
                best = it
        if it.get("kind") == "weapon":
            it["eq"] = True
    if best:
        best["eq"] = True
    for it in items:
        if it.get("kind") == "shield" and "щиты" in " ".join(profs["armor"]):
            it["eq"] = True
            break


def sync_legacy(ch: dict, D: dict | None = None) -> None:
    """Плоские поля для старого кода и виджетов: lvl, cls, ac, mhp, spd, sv."""
    if not is_v2(ch):
        return
    D = D or derive(ch)
    ch["lvl"] = D["lvl"]
    ch["cls"] = " / ".join(f"{CLASSES[c['c']]['n']} {c['lvl']}" if len(ch["classes"]) > 1 else CLASSES[c["c"]]["n"]
                           for c in ch["classes"])
    ch["ac"] = D["ac"]["val"]
    ch["mhp"] = D["hp_max"]["val"]
    ch["spd"] = D["spd"]["val"]
    ch["sv"] = [a for a in ABILITIES if D["saves"][a]["prof"]]
    clamp_hp(ch, D)


def multiclass_ok(ch: dict, cls: str) -> tuple[bool, str]:
    """Требования мультиклассирования: 13 в основных характеристиках текущих и нового класса."""
    for c in {x["c"] for x in ch["classes"]} | {cls}:
        for k, need in CLASSES[c]["mc_req"].items():
            abs_ = k.split("|")
            if not any(ch["st"].get(a, 0) >= need for a in abs_):
                return False, f"{CLASSES[c]['n']}: нужно {' или '.join(ABILITY_SHORT[a] for a in abs_)} {need}"
    return True, ""


def level_up_plan(ch: dict, cls: str) -> dict:
    """Что даст следующий уровень в классе: новые умения, нужные выборы."""
    cur = class_level(ch, cls)
    new = cur + 1
    ci = CLASSES[cls]
    entry = next((c for c in ch["classes"] if c["c"] == cls), None)
    feats = list(ci["features"].get(new, []))
    sub = ci["subs"].get(entry.get("sub") or "") if entry else None
    if sub:
        feats += sub["features"].get(new, [])
    need = {"sub": new == ci["sub_lvl"] and not (entry and entry.get("sub")), "asi": False, "picks": [], "ex": 0}
    for f in feats:
        ch_ = f[2] if len(f) > 2 else {}
        if ch_.get("asi"):
            need["asi"] = True
        if ch_.get("pick"):
            need["picks"].append((f[0], ch_["pick"], ch_["n"]))
        if ch_.get("expertise"):
            need["ex"] += ch_["expertise"]
    return {"cls": cls, "lvl": new, "features": feats, "need": need, "hd": ci["hd"], "new_class": cur == 0}


def level_up(ch: dict, cls: str, hp_mode: str = "avg", choices: dict | None = None) -> list[str]:
    """Применить повышение уровня. choices: sub, asi {ab: +n} | feat (+feat_ab), picks {умение: [..]}, ex [..],
    spells [новые известные], sub_feats... Возвращает журнал изменений."""
    choices = choices or {}
    plan = level_up_plan(ch, cls)
    ci = CLASSES[cls]
    log = []
    # сначала проверки — персонаж не должен меняться наполовину
    if plan["need"]["sub"] and choices.get("sub") not in ci["subs"]:
        raise ValueError(f"Нужно выбрать: {ci['sub_title']}")
    if plan["need"]["asi"] and not choices.get("feat") and sum((choices.get("asi") or {}).values()) != 2:
        raise ValueError("Распредели +2 (одной характеристике или +1 двум) или выбери черту")
    if plan["new_class"]:
        ok, why = multiclass_ok(ch, cls)
        if not ok:
            raise ValueError(why)
        ch["classes"].append({"c": cls, "sub": None, "lvl": 0, "hp": []})
        mp = ci.get("mc_prof", {})
        ch["profs"]["armor"] = sorted(set(ch["profs"]["armor"]) | set(mp.get("armor", [])))
        ch["profs"]["weapons"] = sorted(set(ch["profs"]["weapons"]) | set(mp.get("weapons", [])))
        ch["profs"]["tools"] = sorted(set(ch["profs"].get("tools", [])) | set(mp.get("tools", [])))
        if mp.get("skills") and choices.get("mc_skill") is not None:
            ch["sk"] = sorted(set(ch["sk"]) | {int(choices["mc_skill"])})
        log.append(f"мультикласс: {ci['n']}")
    entry = next(c for c in ch["classes"] if c["c"] == cls)
    entry["lvl"] += 1
    if not (entry is ch["classes"][0] and entry["lvl"] == 1):
        if hp_mode == "roll":
            hp = dice.roll(f"1d{ci['hd']}").total
            log.append(f"хиты: бросок d{ci['hd']} = {hp}")
        else:
            hp = ci["hd"] // 2 + 1
            log.append(f"хиты: среднее {hp}")
        entry["hp"].append(hp)
    if plan["need"]["sub"]:
        sub = choices.get("sub")
        if sub not in ci["subs"]:
            raise ValueError(f"Нужно выбрать: {ci['sub_title']}")
        entry["sub"] = sub
        s = ci["subs"][sub]
        ch["profs"]["armor"] = sorted(set(ch["profs"]["armor"]) | set(s.get("armor", [])))
        ch["profs"]["weapons"] = sorted(set(ch["profs"]["weapons"]) | set(s.get("weapons", [])))
        log.append(f"{ci['sub_title']}: {s['n']}")
    if plan["need"]["asi"]:
        if choices.get("feat"):
            f = choices["feat"]
            ch.setdefault("feats", []).append(f)
            fd = FEATS.get(f, {})
            if (fd.get("asi_any") or fd.get("asi_choose")) and choices.get("feat_ab"):
                _bump(ch, choices["feat_ab"], 1, f"Черта: {f}")
                if fd.get("asi_choose"):
                    ch.setdefault("sv_extra", []).append(choices["feat_ab"])
            ch["profs"]["armor"] = sorted(set(ch["profs"]["armor"]) | set(fd.get("armor", [])))
            log.append(f"черта: {f}")
        else:
            asi = choices.get("asi") or {}
            if sum(asi.values()) != 2:
                raise ValueError("Распредели +2 (одной характеристике или +1 двум)")
            for ab, v in asi.items():
                _bump(ch, ab, int(v), f"Увеличение характеристик ({ci['n']} {entry['lvl']})")
            log.append("характеристики: " + ", ".join(f"{ABILITY_SHORT[a]} +{v}" for a, v in asi.items()))
    for fname, key, n in plan["need"]["picks"]:
        vals = (choices.get("picks") or {}).get(fname, [])[:n]
        if vals:
            ch.setdefault("picks", {}).setdefault(fname, [])
            ch["picks"][fname] += vals
            log.append(f"{fname}: {', '.join(vals)}")
    if plan["need"]["ex"]:
        ex = [int(i) for i in choices.get("ex", []) if int(i) in ch["sk"]][:plan["need"]["ex"]]
        ch["ex"] = sorted(set(ch.get("ex", [])) | set(ex))
        if ex:
            log.append("компетентность: " + ", ".join(SKILLS[i][0] for i in ex))
    for sp in choices.get("spells", []):
        if sp in SPELLS and sp not in ch["sp"]["known"]:
            ch["sp"]["known"].append(sp)
    if choices.get("spells"):
        log.append("новые заклинания: " + ", ".join(choices["spells"]))
    if cls == "barbarian" and entry["lvl"] == 20:
        _bump(ch, "str", 4, "Первобытный чемпион", cap=24)
        _bump(ch, "con", 4, "Первобытный чемпион", cap=24)
    D = derive(ch)
    old_max = ch.get("mhp", D["hp_max"]["val"])
    ch["hp"] = int(ch.get("hp", 0)) + max(0, D["hp_max"]["val"] - old_max)
    ch["lvl_pending"] = max(0, int(ch.get("lvl_pending", 0)) - 1)
    sync_legacy(ch, D)
    log.insert(0, f"⬆️ {ci['n']} {entry['lvl']} (общий уровень {D['lvl']})")
    return log


def _bump(ch, ab, v, src, cap=20):
    new = min(cap, ch["st"][ab] + v)
    real = new - ch["st"][ab]
    if real:
        ch["st"][ab] = new
        ch.setdefault("st_log", []).append({"ab": ab, "val": real, "src": src})


def upgrade_legacy(ch: dict) -> dict:
    """Старый персонаж с сайта → мягко v2 нельзя без расы/класса; оставляем как есть."""
    return ch
