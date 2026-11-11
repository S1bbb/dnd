"""Тестовая партия для демо-режима (DEMO=1): персонажи и пара бросков."""
from . import dice
from .game import clean_char
from .rules import DEFAULT_CHAR

PARTY = [
    (101, "misha", "Миша", {
        "name": "Торин Дубощит", "cls": "Воин", "lvl": 5, "race": "Горный дварф", "bg": "Солдат", "al": "Законно-добрый",
        "hp": 31, "mhp": 44, "ac": 18, "spd": 25,
        "st": {"str": 17, "dex": 12, "con": 16, "int": 9, "wis": 11, "cha": 10}, "sv": ["str", "con"], "sk": [2, 6],
        "atk": [{"n": "Боевой топор", "hit": 6, "dmg": "1d8+3"}, {"n": "Метательный топорик", "hit": 6, "dmg": "1d6+3"}],
        "feat": "Второе дыхание (1/отдых): 1d10+5 хитов бонусным действием.\nВсплеск действий (1/отдых).\nДополнительная атака.",
        "inv": "Кольчуга, щит, боевой топор, 2 топорика, набор путешественника.\n45 зм",
    }),
    (102, "anya", "Аня", {
        "name": "Лира Звёздная", "cls": "Волшебница", "lvl": 5, "race": "Высшая эльфийка", "bg": "Мудрец", "al": "Нейтрально-добрая",
        "hp": 22, "mhp": 27, "ac": 12, "spd": 30,
        "st": {"str": 8, "dex": 14, "con": 13, "int": 18, "wis": 12, "cha": 10}, "sv": ["int", "wis"], "sk": [1, 7, 9],
        "atk": [{"n": "Огненный снаряд", "hit": 7, "dmg": "2d10"}, {"n": "Кинжал", "hit": 5, "dmg": "1d4+2"}],
        "spells": "Заговоры: огненный снаряд, свет, волшебная рука.\n1 круг (4): щит, волшебная стрела, обнаружение магии.\n2 круг (3): туманный шаг, паутина.\n3 круг (2): огненный шар.",
        "inv": "Книга заклинаний, магическая фокусировка, кинжал, мешочек с компонентами.\n12 зм",
    }),
    (103, "dima", "Дима", {
        "name": "Тень", "cls": "Плут", "lvl": 4, "race": "Полурослик", "bg": "Преступник", "al": "Хаотично-нейтральный",
        "hp": 9, "mhp": 27, "ac": 15, "spd": 25,
        "st": {"str": 10, "dex": 18, "con": 12, "int": 13, "wis": 10, "cha": 14}, "sv": ["dex", "int"], "sk": [0, 3, 8, 11, 15],
        "atk": [{"n": "Короткий меч", "hit": 6, "dmg": "1d6+4"}, {"n": "Короткий лук", "hit": 6, "dmg": "1d6+4"},
                {"n": "Скрытая атака", "hit": 6, "dmg": "2d6"}],
        "feat": "Скрытая атака 2d6 (раз в ход, с преимуществом или если рядом союзник).\nХитрое действие: рывок, отход или засада бонусным действием.\nВезучий: перебрось 1 на d20.",
        "inv": "Кожаный доспех, короткий меч, лук и 20 стрел, воровские инструменты, тёмный плащ.\n27 зм и украденный перстень",
        "notes": "Ищет того, кто подставил его гильдию в Невервинтере.",
    }),
]

WAITING = {"name": "Брат Ансельм", "cls": "Жрец", "lvl": 5, "race": "Человек", "bg": "Прислужник", "al": "Законно-нейтральный",
           "hp": 38, "mhp": 38, "ac": 18, "spd": 30,
           "st": {"str": 14, "dex": 10, "con": 14, "int": 10, "wis": 17, "cha": 12}, "sv": ["wis", "cha"], "sk": [10, 14],
           "atk": [{"n": "Булава", "hit": 5, "dmg": "1d6+2"}, {"n": "Священное пламя", "hit": 0, "dmg": "2d8"}]}


async def seed(game) -> None:
    if game.db.characters():
        return
    from . import engine
    from .data.pregens import PREGENS
    for tg_id, username, first, ch in PARTY:
        game.db.add_player(tg_id, username, first)
        game.db.create_character(clean_char(ch, DEFAULT_CHAR), tg_id, 1)
    game.db.create_character(clean_char(WAITING, DEFAULT_CHAR), None, 1)
    game.db.add_player(104, "lena", "Лена")
    p = {k: v for k, v in PREGENS[2].items() if k != "title"}
    game.db.create_character(engine.new_character(p), 104, 1)
    chars = {c["tg_id"]: c for c in game.db.characters(1) if c["tg_id"]}
    for tg_id, title, formula, adv in [
        (101, "Атлетика", "1d20+5", 0),
        (102, "", "8d6", 0),
        (103, "Скрытность", "1d20+6", 1),
    ]:
        game.record(chars[tg_id], title, dice.roll(formula, adv))
    game.roll_check(chars[101], "a", "0")
    game.gm_roll(1, "1d20+4", "Восприятие гоблинов")
    game.db.add_entry(1, cat="session", title="Сессия 1: Пещера гоблинов", body="Партия нашла вход в пещеру.", vis="all", session=1)
