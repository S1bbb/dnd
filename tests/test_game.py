import asyncio
import json
import sqlite3

import pytest

from app import widgets
from app.db import DB
from app.game import Game, compact_char, info_text, clean_char
from app.main import OfflineBot
from app.rules import DEFAULT_CHAR


class RecBot(OfflineBot):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def call(self, method, **params):
        self.calls.append((method, params))
        return await super().call(method, **params)

    def last(self, method):
        return [p for m, p in self.calls if m == method][-1]

    def all(self, method):
        return [p for m, p in self.calls if m == method]


@pytest.fixture
def game(tmp_path):
    g = Game(DB(str(tmp_path / "t.db")), RecBot())
    g.me = {"username": "test_bot"}
    return g


def run(c):
    return asyncio.run(c)


def msg(uid, text, chat_type="private"):
    return {"message": {"text": text, "from": {"id": uid, "first_name": "Миша", "username": "m"},
                        "chat": {"id": uid, "type": chat_type}}}


def wq(uid, data):
    return {"widget_query": {"id": "q1", "from": {"id": uid, "first_name": "Миша"}, "data": data,
                             "chat_instance": "x", "type": {"type": "data"}}}


def answer(game):
    return json.loads(game.bot.last("answerWidgetQuery")["data"])


ROGUE = {
    "name": "Тень", "cls": "Плут", "lvl": 5, "race": "Полурослик", "spd": 25,
    "st": {"str": 10, "dex": 18, "con": 12, "int": 13, "wis": 10, "cha": 14},
    "sk": [15], "atk": [{"n": "Короткий меч", "hit": 7, "dmg": "1d6+4"}],
    "inv": "Воровские инструменты <и плащ>", "spells": "", "feat": "Скрытая атака 3d6",
}


def test_widgets_fit_limits():
    for name, (raw, z) in widgets.sizes().items():
        assert raw <= 32768 and z <= 4096, name
        widgets.load(name)


def test_gm_creates_character_and_player_claims_it(game):
    ch = game.create_char(ROGUE)
    assert ch["tg_id"] is None
    link = game.public_chars()[0]["link"]
    assert link.startswith("https://t.me/test_bot?start=c")

    run(game.handle(msg(1, "/start")))  # без ссылки — не пускаем
    assert game.db.character_of(1) is None

    run(game.handle(msg(1, "/start " + link.split("start=")[1])))
    mine = game.db.character_of(1)
    assert mine["id"] == ch["id"]
    assert len(game.bot.all("sendWidget")) == 2  # лист + кости
    info = "\n".join(p["text"] for p in game.bot.all("sendMessage"))
    assert "Короткий меч" in info and "&lt;и плащ&gt;" in info  # карточка пришла и экранирована


def test_general_link_creates_blank_character(game):
    run(game.handle(msg(5, f"/start {game.join_code}")))
    assert game.db.character_of(5)["data"]["name"] == "Миша"


def test_claiming_other_character_releases_previous(game):
    a = game.create_char({"name": "A"})
    b = game.create_char({"name": "B"})
    run(game.handle(msg(1, f"/start {a['token']}")))
    run(game.handle(msg(1, f"/start {b['token']}")))
    assert game.db.character_of(1)["id"] == b["id"]
    assert game.db.character(a["id"])["tg_id"] is None


def test_widget_dice_roll(game):
    run(game.handle(msg(1, f"/start {game.join_code}")))
    q = asyncio.Queue()
    game.listeners.add(q)
    run(game.handle(wq(1, "rnv:2d6+3")))
    assert 5 <= answer(game)["t"] <= 15
    assert q.get_nowait()["type"] == "roll"


def test_sheet_checks_and_attack(game):
    ch = game.create_char(ROGUE)
    run(game.handle(msg(1, f"/start {ch['token']}")))
    run(game.handle(wq(1, "k:15")))  # Скрытность: +4 ЛОВ +3 мастерство
    assert answer(game)["f"] == "1d20+7"
    run(game.handle(wq(1, "a:0")))
    a = answer(game)
    assert a["f"] == "1d20+7" and "d" in a
    assert 5 <= a["d"]["t"] <= (16 if a["c"] > 0 else 10)
    roll = game.db.rolls()[-1]
    assert roll["title"] == "Короткий меч" and roll["detail"]["d"]["t"] == a["d"]["t"]


def test_info_button_sends_card(game):
    ch = game.create_char(ROGUE)
    run(game.handle(msg(1, f"/start {ch['token']}")))
    n = len(game.bot.all("sendMessage"))

    async def go():
        await game.handle(wq(1, "n:0"))
        await asyncio.sleep(0)

    run(go())
    assert "m" in answer(game)
    assert len(game.bot.all("sendMessage")) == n + 1


def test_gm_edit_updates_sheet_widget(game):
    ch = game.create_char(ROGUE)
    run(game.handle(msg(1, f"/start {ch['token']}")))

    async def go():
        await game.update_char(ch["id"], {"hp": 3})
        await asyncio.sleep(0.01)

    run(go())
    edit = game.bot.last("editMessageWidgetData")
    assert json.loads(edit["chat_data"])["hp"] == 3


def test_gm_request_flow(game):
    ch = game.create_char(ROGUE)
    run(game.handle(msg(1, f"/start {ch['token']}")))
    assert run(game.ask_roll([ch["id"]], "k:3", "")) == [ch["id"]]
    data = json.loads(game.bot.last("sendWidget")["chat_data"])
    assert data["t"] == "Внимательность"
    run(game.handle(wq(1, f"q:{data['i']}")))
    assert "x" in json.loads(game.bot.last("editMessageWidgetData")["chat_data"])
    run(game.handle(wq(1, f"q:{data['i']}")))
    assert "Уже" in answer(game)["e"]


def test_request_skips_unclaimed(game):
    ch = game.create_char(ROGUE)
    assert run(game.ask_roll([ch["id"]], "i:0", "")) == []


def test_bad_attack_formula_rejected():
    with pytest.raises(ValueError):
        clean_char({"atk": [{"n": "Меч", "hit": 5, "dmg": "много"}]}, DEFAULT_CHAR)


def test_compact_char_fits_1024_bytes():
    big = clean_char({"name": "Я" * 40, "cls": "Ж" * 30, "race": "Щ" * 30,
                      "atk": [{"n": "Ъ" * 30, "hit": 5, "dmg": "10d10+10"} for _ in range(8)]}, DEFAULT_CHAR)
    out = compact_char(big)
    assert len(json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode()) <= 1024
    assert out["a"]


def test_info_text_splits_long_cards():
    ch = clean_char({**ROGUE, "feat": "ф" * 2000, "spells": "з" * 2000, "inv": "и" * 2000, "notes": "н" * 2000}, DEFAULT_CHAR)
    msgs = info_text(ch)
    assert len(msgs) > 1 and all(len(m) <= 4096 for m in msgs)


def test_migration_from_v1(tmp_path):
    path = str(tmp_path / "old.db")
    c = sqlite3.connect(path)
    c.executescript("""CREATE TABLE players (tg_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
        char TEXT NOT NULL, sheet_msgs TEXT NOT NULL DEFAULT '[]', joined_at REAL NOT NULL);""")
    c.execute("INSERT INTO players VALUES (7, 'u', 'Вася', ?, '[5]', 1)", (json.dumps({"name": "Гимли"}),))
    c.commit()
    c.close()
    db = DB(path)
    ch = db.character_of(7)
    assert ch["data"]["name"] == "Гимли" and ch["sheet_msgs"] == [5]
