"""Хранилище на SQLite. Маленькая игра — маленькая база.

players    — люди из Telegram, которые открыли бота;
characters — персонажи. Их создаёт мастер на сайте (или игрок по общей ссылке),
             и у каждого есть своя ссылка, по которой игрок забирает его себе.
"""
import json
import secrets
import sqlite3
import time

from .rules import DEFAULT_CHAR

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    tg_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    char TEXT NOT NULL DEFAULT '{}',
    sheet_msgs TEXT NOT NULL DEFAULT '[]',
    joined_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id INTEGER UNIQUE,
    token TEXT NOT NULL UNIQUE,
    data TEXT NOT NULL,
    sheet_msgs TEXT NOT NULL DEFAULT '[]',
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS rolls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    tg_id INTEGER,
    who TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    formula TEXT NOT NULL,
    total INTEGER NOT NULL,
    detail TEXT NOT NULL,
    hidden INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    tg_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    msg_id INTEGER,
    hidden INTEGER NOT NULL DEFAULT 0,
    roll_id INTEGER
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


def new_token() -> str:
    # для ссылки t.me/bot?start=... разрешены только буквы, цифры, _ и -
    return "c" + secrets.token_hex(5)


class DB:
    def __init__(self, path: str):
        self.c = sqlite3.connect(path, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(SCHEMA)
        self._migrate()
        self.c.commit()

    def _migrate(self) -> None:
        """Первая версия хранила персонажа прямо в players — переносим."""
        for r in self.c.execute("SELECT * FROM players").fetchall():
            ch = json.loads(r["char"] or "{}")
            has = self.c.execute("SELECT 1 FROM characters WHERE tg_id=?", (r["tg_id"],)).fetchone()
            if ch and not has:
                self.c.execute(
                    "INSERT INTO characters(tg_id, token, data, sheet_msgs, created) VALUES(?,?,?,?,?)",
                    (r["tg_id"], new_token(), json.dumps(ch, ensure_ascii=False), r["sheet_msgs"], r["joined_at"]))
                self.c.execute("UPDATE players SET char='{}' WHERE tg_id=?", (r["tg_id"],))

    # --- kv ---
    def get(self, k: str, default=None):
        r = self.c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r["v"]) if r else default

    def set(self, k: str, v) -> None:
        self.c.execute("INSERT OR REPLACE INTO kv(k, v) VALUES(?, ?)", (k, json.dumps(v)))
        self.c.commit()

    # --- игроки (люди в Telegram) ---
    def player(self, tg_id: int) -> dict | None:
        r = self.c.execute("SELECT tg_id, username, first_name FROM players WHERE tg_id=?", (tg_id,)).fetchone()
        return {"id": r["tg_id"], "username": r["username"], "first_name": r["first_name"]} if r else None

    def add_player(self, tg_id: int, username: str | None, first_name: str) -> dict:
        self.c.execute(
            "INSERT INTO players(tg_id, username, first_name, joined_at) VALUES(?,?,?,?) "
            "ON CONFLICT(tg_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name",
            (tg_id, username, first_name, time.time()))
        self.c.commit()
        return self.player(tg_id)

    # --- персонажи ---
    def _char(self, r) -> dict:
        return {
            "id": r["id"], "tg_id": r["tg_id"], "token": r["token"],
            "data": {**DEFAULT_CHAR, **json.loads(r["data"])},
            "sheet_msgs": json.loads(r["sheet_msgs"]),
            "player": self.player(r["tg_id"]) if r["tg_id"] else None,
        }

    def character(self, cid: int) -> dict | None:
        r = self.c.execute("SELECT * FROM characters WHERE id=?", (cid,)).fetchone()
        return self._char(r) if r else None

    def character_of(self, tg_id: int) -> dict | None:
        r = self.c.execute("SELECT * FROM characters WHERE tg_id=?", (tg_id,)).fetchone()
        return self._char(r) if r else None

    def character_by_token(self, token: str) -> dict | None:
        r = self.c.execute("SELECT * FROM characters WHERE token=?", (token,)).fetchone()
        return self._char(r) if r else None

    def characters(self) -> list[dict]:
        return [self._char(r) for r in self.c.execute("SELECT * FROM characters ORDER BY created, id")]

    def create_character(self, data: dict, tg_id: int | None = None) -> dict:
        cur = self.c.execute(
            "INSERT INTO characters(tg_id, token, data, created) VALUES(?,?,?,?)",
            (tg_id, new_token(), json.dumps(data, ensure_ascii=False), time.time()))
        self.c.commit()
        return self.character(cur.lastrowid)

    def save_character(self, cid: int, data: dict) -> None:
        self.c.execute("UPDATE characters SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), cid))
        self.c.commit()

    def bind(self, cid: int, tg_id: int) -> None:
        """Отдать персонажа игроку. Прежний персонаж игрока остаётся у мастера без владельца."""
        self.c.execute("UPDATE characters SET tg_id=NULL, sheet_msgs='[]' WHERE tg_id=? AND id<>?", (tg_id, cid))
        self.c.execute("UPDATE characters SET tg_id=?, sheet_msgs='[]' WHERE id=?", (tg_id, cid))
        self.c.commit()

    def unbind(self, cid: int) -> None:
        self.c.execute("UPDATE characters SET tg_id=NULL, sheet_msgs='[]', token=? WHERE id=?", (new_token(), cid))
        self.c.commit()

    def delete_character(self, cid: int) -> None:
        self.c.execute("DELETE FROM characters WHERE id=?", (cid,))
        self.c.commit()

    def add_sheet_msg(self, cid: int, msg_id: int) -> None:
        ch = self.character(cid)
        if not ch:
            return
        msgs = (ch["sheet_msgs"] + [msg_id])[-3:]  # обновляем только последние листы
        self.c.execute("UPDATE characters SET sheet_msgs=? WHERE id=?", (json.dumps(msgs), cid))
        self.c.commit()

    # --- броски ---
    def add_roll(self, tg_id, who, title, formula, total, detail: dict) -> dict:
        ts = time.time()
        cur = self.c.execute(
            "INSERT INTO rolls(ts, tg_id, who, title, formula, total, detail) VALUES(?,?,?,?,?,?,?)",
            (ts, tg_id, who, title, formula, total, json.dumps(detail, ensure_ascii=False)))
        self.c.commit()
        return {"id": cur.lastrowid, "ts": ts, "tg_id": tg_id, "who": who, "title": title,
                "formula": formula, "total": total, "detail": detail}

    def rolls(self, limit: int = 100) -> list[dict]:
        rows = self.c.execute("SELECT * FROM rolls ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r["id"], "ts": r["ts"], "tg_id": r["tg_id"], "who": r["who"], "title": r["title"],
                 "formula": r["formula"], "total": r["total"], "detail": json.loads(r["detail"])}
                for r in reversed(rows)]

    def clear_rolls(self) -> None:
        self.c.execute("DELETE FROM rolls")
        self.c.commit()

    # --- запросы бросков от мастера ---
    def add_request(self, tg_id: int, title: str, kind: str) -> int:
        cur = self.c.execute("INSERT INTO requests(ts, tg_id, title, kind) VALUES(?,?,?,?)",
                             (time.time(), tg_id, title, kind))
        self.c.commit()
        return cur.lastrowid

    def request(self, rid: int) -> dict | None:
        r = self.c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
        return dict(r) if r else None

    def update_request(self, rid: int, **kw) -> None:
        sets = ", ".join(f"{k}=?" for k in kw)
        self.c.execute(f"UPDATE requests SET {sets} WHERE id=?", (*kw.values(), rid))
        self.c.commit()
