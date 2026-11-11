"""Тестовая партия для демо-режима (DEMO=1): игроки и пара бросков."""
from . import dice

PARTY = [
    (101, "aragorn", "Миша", {"name": "Торин Дубощит", "cls": "Воин", "lvl": 5, "hp": 31, "mhp": 44, "ac": 18,
                              "st": {"str": 17, "dex": 12, "con": 16, "int": 9, "wis": 11, "cha": 10},
                              "sv": ["str", "con"], "sk": [2, 6]}),
    (102, "lira", "Аня", {"name": "Лира Звёздная", "cls": "Волшебница", "lvl": 5, "hp": 22, "mhp": 27, "ac": 12,
                          "st": {"str": 8, "dex": 14, "con": 13, "int": 18, "wis": 12, "cha": 10},
                          "sv": ["int", "wis"], "sk": [1, 7, 9]}),
    (103, "shadow", "Дима", {"name": "Тень", "cls": "Плут", "lvl": 4, "hp": 9, "mhp": 27, "ac": 15,
                             "st": {"str": 10, "dex": 18, "con": 12, "int": 13, "wis": 10, "cha": 14},
                             "sv": ["dex", "int"], "sk": [0, 3, 8, 11, 15]}),
]


async def seed(game) -> None:
    if game.db.players():
        return
    for tg_id, username, first, ch in PARTY:
        game.db.add_player(tg_id, username, first)
        game.db.save_char(tg_id, {**game.db.player(tg_id)["char"], **ch})
    for tg_id, title, formula, adv, hidden in [
        (101, "Атлетика", "1d20+5", 0, False),
        (102, "", "8d6", 0, False),
        (103, "Скрытность", "1d20+6", 1, False),
        (103, "Тайный бросок", "1d20+2", 0, True),
        (101, "", "1d12+3", 0, False),
    ]:
        p = game.db.player(tg_id)
        await game.record(tg_id, game.who(p), title, dice.roll(formula, adv), hidden)
    await game.gm_roll("1d20+4", "Восприятие гоблинов", hidden=True)
