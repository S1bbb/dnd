import asyncio
import json

import pytest

from app import widgets
from app.db import DB
from app.game import Game
from app.main import OfflineBot


class RecBot(OfflineBot):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def call(self, method, **params):
        self.calls.append((method, params))
        return await super().call(method, **params)

    def last(self, method):
        return [p for m, p in self.calls if m == method][-1]


@pytest.fixture
def game(tmp_path):
    g = Game(DB(str(tmp_path / "t.db")), RecBot())
    g.me = {"username": "test_bot"}
    return g


def run(c):
    return asyncio.run(c)


def msg(uid, text, chat_type="private", chat_id=None):
    return {"message": {"text": text, "from": {"id": uid, "first_name": "Миша", "username": "m"},
                        "chat": {"id": chat_id or uid, "type": chat_type, "title": "Партия"}}}


def wq(uid, data):
    return {"widget_query": {"id": "q1", "from": {"id": uid, "first_name": "Миша"}, "data": data,
                             "chat_instance": "x", "type": {"type": "data"}}}


def test_widgets_fit_limits():
    for name, (raw, z) in widgets.sizes().items():
        assert raw <= 32768 and z <= 4096, name
        widgets.load(name)


def test_join_requires_code(game):
    run(game.handle(msg(1, "/start")))
    assert game.db.player(1) is None
    run(game.handle(msg(1, f"/start {game.join_code}")))
    assert game.db.player(1) is not None
    sent = [m for m, _ in game.bot.calls if m == "sendWidget"]
    assert len(sent) == 2  # лист + кости
    assert game.invite_link.endswith(game.join_code)


def test_widget_dice_roll(game):
    run(game.handle(msg(1, f"/start {game.join_code}")))
    q = asyncio.Queue()
    game.listeners.add(q)
    run(game.handle(wq(1, "rnh:2d6+3")))
    ans = json.loads(game.bot.last("answerWidgetQuery")["data"])
    assert 5 <= ans["t"] <= 15
    ev = q.get_nowait()
    assert ev["type"] == "roll" and ev["roll"]["hidden"] is True


def test_sheet_checks_use_character(game):
    run(game.handle(msg(1, f"/start {game.join_code}")))
    run(game.handle(msg(1, "/stats 10 18 10 10 10 10")))
    run(game.handle(msg(1, "/class Плут 5")))
    p = game.db.player(1)
    assert p["char"]["st"]["dex"] == 18 and p["char"]["lvl"] == 5
    run(game.update_char(1, {"sk": [15]}))  # Скрытность
    run(game.handle(wq(1, "k:15")))
    ans = json.loads(game.bot.last("answerWidgetQuery")["data"])
    assert ans["f"] == "1d20+7"  # +4 ЛОВ +3 мастерство


def test_gm_request_flow(game):
    run(game.handle(msg(1, f"/start {game.join_code}")))
    run(game.ask_roll([1], "k:3", "", False))
    req = game.bot.last("sendWidget")
    data = json.loads(req["chat_data"])
    assert data["t"] == "Внимательность"
    run(game.handle(wq(1, f"q:{data['i']}")))
    edit = game.bot.last("editMessageWidgetData")
    assert "x" in json.loads(edit["chat_data"])
    run(game.handle(wq(1, f"q:{data['i']}")))
    assert "Уже" in json.loads(game.bot.last("answerWidgetQuery")["data"])["e"]


def test_text_roll_and_group(game):
    run(game.handle(msg(1, f"/start {game.join_code}")))
    run(game.handle(msg(1, f"/bindgroup {game.join_code}", "group", -100)))
    assert game.db.get("group")["id"] == -100
    run(game.handle(msg(1, "d20+1")))
    assert any(p.get("chat_id") == -100 and "🎲" in p.get("text", "") for m, p in game.bot.calls if m == "sendMessage")
