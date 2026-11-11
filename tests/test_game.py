import asyncio
import json
import sqlite3

import pytest

from app import engine, widgets
from app.db import DB, Conflict
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

    async def _multipart(self, method, fields, photo):
        self.calls.append((method, fields))
        self.mid += 1
        return {"message_id": self.mid}

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


def msg(uid, text, chat_type="private", name="Миша"):
    return {"message": {"text": text, "from": {"id": uid, "first_name": name, "username": "m"},
                        "chat": {"id": uid, "type": chat_type}}}


N = [0]


def cb(uid, data, mid=5000):
    N[0] += 1
    return {"callback_query": {"id": f"cb{N[0]}", "from": {"id": uid, "first_name": "Миша", "username": "m"},
                               "message": {"message_id": mid, "chat": {"id": uid, "type": "private"}}, "data": data}}


def wq(uid, data):
    return {"widget_query": {"id": "q1", "from": {"id": uid, "first_name": "Миша"}, "data": data,
                             "chat_instance": "x", "type": {"type": "data"}}}


def answer(game):
    return json.loads(game.bot.last("answerWidgetQuery")["data"])


def last_text(game):
    for m, p in reversed(game.bot.calls):
        if m in ("sendMessage", "editMessageText"):
            return p["text"], p.get("reply_markup")


def buttons(markup, prefix="w:"):
    return [b["callback_data"][len(prefix):] for row in markup["inline_keyboard"] for b in row]


ROGUE = {
    "name": "Тень", "cls": "Плут", "lvl": 5, "race": "Полурослик", "spd": 25,
    "st": {"str": 10, "dex": 18, "con": 12, "int": 13, "wis": 10, "cha": 14},
    "sk": [15], "atk": [{"n": "Короткий меч", "hit": 7, "dmg": "1d6+4"}],
    "inv": "Воровские инструменты <и плащ>", "spells": "", "feat": "Скрытая атака 3d6",
}
FIGHTER = dict(name="Бор", race="dwarf", sub="mountain", cls="fighter", bg="soldier",
               st=dict(str=15, dex=14, con=13, int=12, wis=10, cha=8), sk=[0, 3], picks={"Боевой стиль": ["Оборона"]}, equip=0)
WIZARD = dict(name="Ли", race="elf", sub="high", cls="wizard", bg="sage", st=dict(str=8, dex=14, con=13, int=15, wis=12, cha=10),
              sk=[9, 7], spells={"known": ["Огненный снаряд", "Волшебная стрела", "Щит", "Усыпление", "Свет"], "prep": ["Волшебная стрела", "Щит"]})


# ---------- данные и движок ----------
def test_widgets_fit_limits():
    for name, (raw, z) in widgets.sizes().items():
        assert raw <= 32768 and z <= 4096, name
        widgets.load(name)


def test_reference_data_is_consistent():
    from app.data.classes import CLASSES
    from app.data.races import RACES
    from app.data.spells import SPELLS
    from app.data.pregens import PREGENS
    assert len(SPELLS) > 300 and len(CLASSES) == 12
    for c in CLASSES.values():
        for s in c["subs"].values():
            for key in ("spells", "extra"):
                for names in s.get(key, {}).values():
                    assert all(n in SPELLS for n in names), names
    for r in RACES.values():
        for n, _ in r.get("spells", []) + [x for s in r.get("subs", {}).values() for x in s.get("spells", [])]:
            assert n in SPELLS
    for p in PREGENS:  # каждый готовый герой собирается без ошибок и предупреждений
        ch = engine.new_character({k: v for k, v in p.items() if k != "title"})
        D = engine.derive(ch)
        assert not D["warn"], (p["title"], D["warn"])
        assert ch["hp"] == D["hp_max"]["val"] > 0
        if D["cast"]:
            from app.data.spells import SPELLS as SP
            names = ch["sp"]["known"] + ch["sp"]["prep"]
            assert len({n for n in names if SP[n]["lvl"] == 0}) <= D["cast"][0]["cantrips"], p["title"]


def test_fighter_numbers_and_breakdown():
    ch = engine.new_character(FIGHTER)
    D = engine.derive(ch)
    assert ch["st"]["str"] == 17 and ch["st"]["con"] == 15  # горный дварф +2 СИЛ +2 ТЕЛ
    assert D["ac"]["val"] == 19 and ("Боевой стиль: Оборона", 1) in D["ac"]["parts"]  # кольчуга + щит + стиль
    assert D["hp_max"]["val"] == 12 and D["spd"]["val"] == 25
    sword = D["attacks"][0]
    assert sword["n"] == "Длинный меч" and sword["hit"] == 5 and sword["dmg"] == "1d8+3"
    assert D["saves"]["str"]["prof"] and D["saves"]["con"]["prof"] and not D["saves"]["dex"]["prof"]


def test_wizard_slots_cast_and_rest():
    ch = engine.new_character(WIZARD)
    D = engine.derive(ch)
    assert D["cast"][0]["dc"] == 13 and D["slots"] == {1: {"max": 2, "left": 2}}
    assert any(a["n"] == "Огненный снаряд" and a["hit"] == 5 for a in D["attacks"])
    engine.cast(ch, "Волшебная стрела", 1)
    engine.cast(ch, "Щит", 1)
    with pytest.raises(ValueError):
        engine.cast(ch, "Усыпление", 1)
    assert engine.spell_roll(ch, "Волшебная стрела", 2)["dice"] == "3d4+3+1d4+1"
    engine.long_rest(ch)
    assert engine.derive(ch)["slots"][1]["left"] == 2


def test_damage_temp_death_and_resistance():
    ch = engine.new_character({**FIGHTER, "race": "tiefling", "sub": None})
    mx = engine.derive(ch)["hp_max"]["val"]
    engine.set_temp(ch, 5)
    engine.damage(ch, 7)
    assert ch["temp"] == 0 and ch["hp"] == mx - 2
    engine.damage(ch, 10, "огонь")  # тифлинг: сопротивление огню
    assert ch["hp"] == mx - 7
    engine.damage(ch, 100)
    assert ch.get("dead")  # остаток урона ≥ максимума — мгновенная смерть
    ch2 = engine.new_character(FIGHTER)
    engine.damage(ch2, ch2["hp"])
    assert "Без сознания" in ch2["conds"]
    engine.death_save(ch2, 15, 15)
    engine.death_save(ch2, 3, 3)
    assert ch2["death"] == [1, 1]
    engine.heal(ch2, 4)
    assert ch2["hp"] == 4 and "Без сознания" not in ch2["conds"]


def test_conditions_advantage_and_effects():
    ch = engine.new_character(FIGHTER)
    ch["conds"] = ["Отравлен"]
    assert engine.roll_spec(ch, "a", "0")["adv"] == -1
    ch["conds"] = ["Ошеломлён"]
    assert engine.roll_spec(ch, "s", "dex")["note"]
    ch["conds"] = []
    engine.add_effect(ch, "Благословение", "Жрец")
    assert "+1d4" in engine.roll_spec(ch, "a", "0")["formula"]
    engine.add_effect(ch, "Щит веры", "Жрец", rounds=1)
    assert engine.derive(ch)["ac"]["val"] == 21
    engine.tick_effects(ch)
    assert engine.derive(ch)["ac"]["val"] == 19
    ch["adj"] = [{"id": "x", "field": "ac", "val": 1, "reason": "Плащ защиты"}]
    assert ("Мастер: Плащ защиты", 1) in engine.derive(ch)["ac"]["parts"]


def test_level_up_asi_subclass_and_multiclass():
    ch = engine.new_character(FIGHTER)
    engine.level_up(ch, "fighter")
    with pytest.raises(ValueError):
        engine.level_up(ch, "fighter")  # 3 уровень требует архетип
    engine.level_up(ch, "fighter", choices={"sub": "champion"})
    engine.level_up(ch, "fighter", choices={"asi": {"str": 1, "con": 1}})
    D = engine.derive(ch)
    assert D["lvl"] == 4 and ch["st"]["str"] == 18 and engine.crit_range(ch) == 19
    assert D["hp_max"]["val"] == 10 + 6 * 3 + 3 * 4  # 1 ур. максимум, дальше среднее, ТЕЛ 16 → +3
    ok, why = engine.multiclass_ok(ch, "wizard")
    assert not ok and "ИНТ" in why
    ch["st"]["int"] = 13
    engine.level_up(ch, "wizard", choices={"spells": ["Щит"]})
    D = engine.derive(ch)
    assert D["lvl"] == 5 and D["slots"][1]["max"] == 2 and "Щит" in engine.spell_list(ch)


def test_physical_dice():
    from app import dice
    r = dice.manual("1d20+5", 17)
    assert r.total == 22
    with pytest.raises(dice.DiceError):
        dice.manual("1d20+5", 25)
    assert dice.manual("2d6+1", 9).total == 10


def test_legacy_characters_still_work():
    ch = clean_char(ROGUE, DEFAULT_CHAR)
    D = engine.derive(ch)
    assert D["ac"]["val"] == 10 and D["attacks"][0]["n"] == "Короткий меч"
    assert engine.roll_spec(ch, "k", "15")["formula"] == "1d20+7"  # Скрытность: ЛОВ +4, мастерство +3


# ---------- бот: создание, лист, запросы ----------
def test_bot_wizard_step_by_step_with_back(game):
    run(game.handle(msg(1, "/start")))
    assert "код лобби" in last_text(game)[0]
    run(game.handle(msg(1, "00000")))
    assert "нет" in last_text(game)[0]
    run(game.handle(msg(1, game.db.game(1)["lobby"])))
    run(game.handle(cb(1, "w:mode:step")))
    run(game.handle(msg(1, "Арагорн")))
    run(game.handle(cb(1, "w:race:3")))  # человек
    run(game.handle(cb(1, "w:sub:base")))
    run(game.handle(cb(1, "w:back:0")))   # назад к подрасе
    assert "Подраса" in last_text(game)[0]
    run(game.handle(cb(1, "w:sub:base")))
    from app.wizard import CLASS_KEYS, BG_KEYS
    run(game.handle(cb(1, f"w:cls:{CLASS_KEYS.index('fighter')}")))
    run(game.handle(cb(1, f"w:bg:{BG_KEYS.index('soldier')}")))
    run(game.handle(cb(1, "w:m:pb")))     # закупка очков
    for _ in range(7):
        run(game.handle(cb(1, "w:pb:str:+")))
    assert "осталось <b>18</b>" in last_text(game)[0]  # 15 стоит 9 очков
    run(game.handle(cb(1, "w:pb:ok:0")))
    sk = [b for b in buttons(last_text(game)[1]) if b.startswith("sk:") and b != "sk:ok"]
    run(game.handle(cb(1, "w:" + sk[0])))
    run(game.handle(cb(1, "w:" + sk[1])))
    run(game.handle(cb(1, "w:sk:ok")))
    assert "Боевой стиль" in last_text(game)[0]
    run(game.handle(cb(1, "w:pk:1")))     # Оборона
    run(game.handle(cb(1, "w:lg:3")))     # язык
    run(game.handle(cb(1, "w:lg:ok")))
    run(game.handle(cb(1, "w:eq:0")))
    run(game.handle(cb(1, "w:al:0")))
    assert game.wiz.drafts(1)[0]["name"] == "Арагорн"  # мастер видит черновик
    run(game.handle(cb(1, "w:skip:0")))
    assert "Всё верно" in last_text(game)[0]
    run(game.handle(cb(1, "w:ok:1")))
    ch = game.db.character_of(1)
    d = ch["data"]
    assert d["name"] == "Арагорн" and d["cls"] == "Воин" and d["st"]["str"] == 16 and d["picks"]["Боевой стиль"] == ["Оборона"]
    assert engine.derive(d)["ac"]["val"] == 19
    assert game.wiz.drafts(1) == [] and len(game.bot.all("sendWidget")) == 1  # лист с бросками


def test_gm_link_claim_and_widget_rolls(game):
    ch = game.create_char(1, ROGUE)
    run(game.handle(msg(1, "/start " + ch["token"])))
    mine = game.db.character_of(1)
    assert mine["id"] == ch["id"]
    run(game.handle(wq(1, f"{ch['id']}|k:15")))
    assert answer(game)["f"] == "1d20+7"
    run(game.handle(wq(1, f"{ch['id']}|a:0")))
    a = answer(game)
    assert a["f"] == "1d20+7" and "d" in a
    run(game.handle(wq(2, f"{ch['id']}|k:15")))  # чужой лист
    assert "не твой" in answer(game)["e"]


def test_requests_visibility_cancel_and_physical(game):
    ch = game.create_char(1, ROGUE)
    run(game.handle(msg(1, "/start " + ch["token"])))
    run(game.ask_roll(1, [ch["id"]], "k:3", "", "gm"))
    data = json.loads(game.bot.last("sendWidget")["chat_data"])
    assert data["h"] == 1
    run(game.handle(wq(1, f"q:{data['i']}:m15")))  # свои кубики: 15 на d20
    shown = json.loads(game.bot.last("editMessageWidgetData")["chat_data"])["x"]
    assert shown.get("h") == 1  # тайный бросок: игрок результата не видит
    roll = game.db.rolls(1)[-1]
    assert roll["hidden"] == 1 and roll["total"] == 15 + 0 and roll["detail"].get("phys")
    run(game.ask_roll(1, [ch["id"]], "c:str", ""))
    rid = json.loads(game.bot.last("sendWidget")["chat_data"])["i"]
    assert run(game.cancel_request(rid))
    run(game.handle(wq(1, f"q:{rid}")))
    assert "отменил" in answer(game)["e"]
    run(game.ask_roll(1, [ch["id"]], "s:dex", "", dc=15))
    run(game.handle(msg(1, "12")))  # просто число в чат = результат d20 для запроса
    assert game.db.requests(1)[0]["status"] == "done"
    last = game.db.rolls(1)[-1]["detail"]
    assert last["dc"] == 15 and last["ok"] is (game.db.rolls(1)[-1]["total"] >= 15)


def test_concurrent_edit_conflict_and_undo(game):
    ch = game.create_char(1, ROGUE)
    rev = game.db.character(ch["id"])["rev"]
    run(game.update_char(ch["id"], {"hp": 3}, rev))
    with pytest.raises(Conflict):
        run(game.update_char(ch["id"], {"hp": 5}, rev))  # старый rev
    assert game.db.character(ch["id"])["data"]["hp"] == 3
    game.undo(ch["id"], "Мастер")
    assert game.db.character(ch["id"])["data"]["hp"] == ROGUE.get("hp", 10)


def test_compact_char_fits_1024_bytes():
    big = clean_char({"name": "Я" * 40, "cls": "Ж" * 30, "race": "Щ" * 30,
                      "atk": [{"n": "Ъ" * 30, "hit": 5, "dmg": "10d10+10"} for _ in range(8)]}, DEFAULT_CHAR)
    out = compact_char(big, 1)
    assert len(json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode()) <= 1024
    assert out["a"]


def test_info_text_splits_long_cards():
    ch = clean_char({**ROGUE, "feat": "ф" * 3000, "spells": "з" * 3000, "inv": "и" * 3000, "notes": "н" * 3000}, DEFAULT_CHAR)
    msgs = info_text(ch)
    assert len(msgs) > 1 and all(len(m) <= 4096 for m in msgs)


def test_migration_from_old_schema(tmp_path):
    path = str(tmp_path / "old.db")
    c = sqlite3.connect(path)
    c.executescript("""CREATE TABLE players (tg_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
        char TEXT NOT NULL DEFAULT '{}', sheet_msgs TEXT NOT NULL DEFAULT '[]', joined_at REAL NOT NULL);
        CREATE TABLE characters (id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER UNIQUE, token TEXT NOT NULL UNIQUE,
        data TEXT NOT NULL, sheet_msgs TEXT NOT NULL DEFAULT '[]', created REAL NOT NULL);
        CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT);""")
    c.execute("INSERT INTO players VALUES (7, 'u', 'Вася', '{}', '[]', 1)")
    c.execute("INSERT INTO characters(tg_id, token, data, sheet_msgs, created) VALUES (7, 'cabc', ?, '[5]', 1)", (json.dumps({"name": "Гимли"}),))
    c.execute("INSERT INTO kv VALUES ('lobby_code', '\"12345\"')")
    c.commit()
    c.close()
    db = DB(path)
    ch = db.character_of(7)
    assert ch["data"]["name"] == "Гимли" and ch["sheet_msgs"] == [5] and ch["game_id"] == 1
    assert db.game(1)["lobby"] == "12345"


# ---------- сквозной сценарий ----------
def test_end_to_end_campaign(game):
    """выбор игры → создание персонажа → проверка → бой → расход ресурсов → отдых → журнал → переключение игры и возврат"""
    g2 = game.create_game("Проклятие Страда")
    # создание персонажа в боте по коду второй игры (готовый воин)
    run(game.handle(msg(1, "/start " + g2["lobby"])))
    run(game.handle(cb(1, "w:mode:pregen")))
    run(game.handle(cb(1, "w:pg:0")))
    run(game.handle(cb(1, "w:ok:1")))
    ch = game.db.character_of(1)
    assert ch["game_id"] == g2["id"] and ch["data"]["name"] == "Торгрим"
    cid = ch["id"]
    # проверка: мастер просит Атлетику, игрок бросает из виджета
    run(game.ask_roll(g2["id"], [cid], "k:2", ""))
    rid = json.loads(game.bot.last("sendWidget")["chat_data"])["i"]
    run(game.handle(wq(1, f"q:{rid}")))
    assert game.db.request(rid)["status"] == "done"
    # бой: инициатива, гоблины, ход, урон
    run(game.combat_start(g2["id"]))
    game.combat_add_npc(g2["id"], "", "Гоблин", 2)
    ini = json.loads(game.bot.last("sendWidget")["chat_data"])["i"]
    run(game.handle(wq(1, f"q:{ini}")))
    cbt = game.combat(g2["id"])
    assert len(cbt["list"]) == 3 and all(x["init"] is not None for x in cbt["list"])
    run(game.combat_next(g2["id"]))
    full = game.db.character(cid)["data"]["hp"]
    # игрок сам себе хиты не меняет — это делает мастер (и в бою, и вне его)
    run(game.handle(cb(1, "p:dmg")))
    assert game.bot.last("answerCallbackQuery")["show_alert"] and game.db.get("inp:1") is None
    assert game.db.character(cid)["data"]["hp"] == full
    game.mutate(cid, lambda d: engine.damage(d, 6), "Мастер", "урон 6")  # кнопка 🗡 на сайте
    hp_after = game.db.character(cid)["data"]["hp"]
    assert hp_after == full - 6
    # игрок тратит свой ресурс сам: Второе дыхание списывается сразу, бросок лечения уходит мастеру в ленту
    feats = game.ui.my_feats(game.db.character(cid))
    i = next(k for k, f in enumerate(feats) if f["n"] == "Второе дыхание")
    run(game.handle(cb(1, f"p:uf:{i}")))
    d = game.db.character(cid)["data"]
    assert d["used"]["res:second_wind"] == 1 and d["hp"] == hp_after
    roll = game.db.rolls(g2["id"])[-1]
    assert roll["title"] == "Второе дыхание" and roll["detail"]["ap"]["k"] == "heal"
    game.mutate(cid, lambda d: engine.heal(d, roll["detail"]["ap"]["n"]), "Мастер", "лечение")  # «✚ применить» в ленте
    assert game.db.character(cid)["data"]["hp"] > hp_after
    run(game.combat_end(g2["id"]))
    # отдых объявляет мастер; до этого игрок кости хитов тратить не может
    run(game.handle(cb(1, "p:hd:d10")))
    assert "hd:d10" not in game.db.character(cid)["data"]["used"]
    run(game.party_rest(g2["id"], "short"))
    run(game.handle(cb(1, "p:hd:d10")))
    run(game.handle(cb(1, "p:short")))
    d = game.db.character(cid)["data"]
    assert "res:second_wind" not in d["used"] and d["used"]["hd:d10"] == 1 and game.db.get(f"srest:{cid}") is None
    # журнал: механика записалась сама; мастер правит запись и открывает игрокам
    auto = game.db.journal(g2["id"], cat="mech")
    assert any("Начался бой" in e["title"] for e in auto) and any("отдых" in e["title"].lower() for e in auto)
    e = game.db.add_entry(g2["id"], title="Деревня Баровия", body="Туман", vis="gm")
    game.db.update_entry(e["id"], "Мастер", body="Туман и волки", vis="all")
    assert len(game.db.entry_revs(e["id"])) == 2 and game.journal_for_players(g2["id"])[0]["body"] == "Туман и волки"
    assert game.db.character(cid)["data"]["hp"] == d["hp"]  # правка текста не трогает расчёты
    # переключение игры и возврат: персонаж в первой игре, затем снова во второй
    other = game.create_char(1, ROGUE)
    run(game.handle(msg(1, "/start " + other["token"])))
    assert game.db.character_of(1)["game_id"] == 1
    run(game.handle(cb(1, f"p:g:{g2['id']}")))
    back = game.db.character_of(1)
    assert back["game_id"] == g2["id"] and back["data"]["hp"] == d["hp"] and back["data"]["used"]["hd:d10"] == 1
    # архив: игра только для чтения
    game.set_game_status(g2["id"], "archived")
    run(game.handle(wq(1, f"{cid}|k:2")))
    assert "архиве" in answer(game)["e"]


def test_map_render_and_move(game):
    ch = game.create_char(1, ROGUE)
    run(game.handle(msg(1, "/start " + ch["token"])))
    sid = game.db.save_scene(1, None, "Пещера", {"show": True})
    game.map.set_current(1, sid)
    sc = game.map.scene(1)
    game.map.add_party(1, sc)
    sc["fog_on"], sc["fog"] = True, ["5:5"]
    game.db.save_scene(1, sid, sc["name"], sc)
    sc["game_id"] = 1
    img = game.map.render(sc, ch["id"])
    assert img[:2] == b"\xff\xd8"  # JPEG
    run(game.map.push(1))
    assert game.bot.all("sendPhoto")
    tok = game.map.token_of(game.map.scene(1), ch["id"])
    run(game.handle(cb(1, "m:e")))
    assert game.map.token_of(game.map.scene(1), ch["id"])["x"] == tok["x"] + 1
