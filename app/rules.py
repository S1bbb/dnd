"""Немного правил D&D 5e: характеристики, навыки, модификаторы."""

ABILITIES = ["str", "dex", "con", "int", "wis", "cha"]
ABILITY_RU = {
    "str": "Сила", "dex": "Ловкость", "con": "Телосложение",
    "int": "Интеллект", "wis": "Мудрость", "cha": "Харизма",
}
ABILITY_SHORT = {"str": "СИЛ", "dex": "ЛОВ", "con": "ТЕЛ", "int": "ИНТ", "wis": "МДР", "cha": "ХАР"}

# Порядок важен: виджет листа персонажа ссылается на навыки по индексу.
SKILLS = [
    ("Акробатика", "dex"), ("Анализ", "int"), ("Атлетика", "str"), ("Внимательность", "wis"),
    ("Выживание", "wis"), ("Выступление", "cha"), ("Запугивание", "cha"), ("История", "int"),
    ("Ловкость рук", "dex"), ("Магия", "int"), ("Медицина", "wis"), ("Обман", "cha"),
    ("Природа", "int"), ("Проницательность", "wis"), ("Религия", "int"), ("Скрытность", "dex"),
    ("Убеждение", "cha"), ("Уход за животными", "wis"),
]

DEFAULT_CHAR = {
    "name": "", "cls": "", "lvl": 1, "hp": 10, "mhp": 10, "ac": 10,
    "st": {a: 10 for a in ABILITIES},
    "sv": [],   # владение спасбросками: ["str", "con"]
    "sk": [],   # владение навыками: индексы SKILLS
}


def mod(score: int) -> int:
    return (int(score) - 10) // 2


def prof_bonus(level: int) -> int:
    return 2 + (max(1, min(20, int(level))) - 1) // 4


def check_formula(char: dict, kind: str, key: str) -> tuple[str, str]:
    """kind: c — проверка характеристики, s — спасбросок, k — навык, i — инициатива.
    Возвращает (название, формула)."""
    st = char.get("st", {})
    pb = prof_bonus(char.get("lvl", 1))
    if kind == "i":
        return "Инициатива", f"1d20{mod(st.get('dex', 10)):+d}"
    if kind == "k":
        idx = int(key)
        name, ab = SKILLS[idx]
        bonus = mod(st.get(ab, 10)) + (pb if idx in char.get("sk", []) else 0)
        return name, f"1d20{bonus:+d}"
    if key not in ABILITIES:
        raise ValueError("bad ability")
    bonus = mod(st.get(key, 10))
    if kind == "s":
        if key in char.get("sv", []):
            bonus += pb
        return f"Спасбросок: {ABILITY_RU[key]}", f"1d20{bonus:+d}"
    return f"Проверка: {ABILITY_RU[key]}", f"1d20{bonus:+d}"
