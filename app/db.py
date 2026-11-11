"""Хранилище на SQLite. Маленькая игра — маленькая база."""
import json
import sqlite3
import time

from .rules import DEFAULT_CHAR

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    tg_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    char TEXT NOT NULL,
    sheet_msgs TEXT NOT NULL DEFAULT '[]',
    joined_at REAL NOT NULL
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


class DB:
    def __init__(self, path: str):
        self.c = sqlite3.connect(path, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(SCHEMA)
        self.c.commit()

    # --- kv ---
    def get(self, k: str, default=None):
        r = self.c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r["v"]) if r else default

    def set(self, k: str, v) -> None:
        self.c.execute("INSERT OR REPLACE INTO kv(k, v) VALUES(?, ?)", (k, json.dumps(v)))
        self.c.commit()

    # --- players ---
    @staticmethod
    def _player(r) -> dict:
        ch = {**DEFAULT_CHAR, **json.loads(r["char"])}
        return {
            "id": r["tg_id"], "username": r["username"], "first_name": r["first_name"],
            "char": ch, "sheet_msgs": json.loads(r["sheet_msgs"]),
        }

    def player(self, tg_id: int) -> dict | None:
        r = self.c.execute("SELECT * FROM players WHERE tg_id=?", (tg_id,)).fetchone()
        return self._player(r) if r else None

    def players(self) -> list[dict]:
        return [self._player(r) for r in self.c.execute("SELECT * FROM players ORDER BY joined_at")]

    def add_player(self, tg_id: int, username: str | None, first_name: str) -> dict:
        ch = dict(DEFAULT_CHAR, name=first_name)
        self.c.execute(
            "INSERT OR IGNORE INTO players(tg_id, username, first_name, char, joined_at) VALUES(?,?,?,?,?)",
            (tg_id, username, first_name, json.dumps(ch, ensure_ascii=False), time.time()),
        )
        self.c.execute("UPDATE players SET username=?, first_name=? WHERE tg_id=?", (username, first_name, tg_id))
        self.c.commit()
        return self.player(tg_id)

    def remove_player(self, tg_id: int) -> None:
        self.c.execute("DELETE FROM players WHERE tg_id=?", (tg_id,))
        self.c.commit()

    def save_char(self, tg_id: int, char: dict) -> None:
        self.c.execute("UPDATE players SET char=? WHERE tg_id=?", (json.dumps(char, ensure_ascii=False), tg_id))
        self.c.commit()

    def add_sheet_msg(self, tg_id: int, msg_id: int) -> None:
        p = self.player(tg_id)
        if not p:
            return
        msgs = (p["sheet_msgs"] + [msg_id])[-3:]  # обновляем только последние листы
        self.c.execute("UPDATE players SET sheet_msgs=? WHERE tg_id=?", (json.dumps(msgs), tg_id))
        self.c.commit()

    # --- rolls ---
    def add_roll(self, tg_id, who, title, formula, total, detail: dict, hidden: bool) -> dict:
        ts = time.time()
        cur = self.c.execute(
            "INSERT INTO rolls(ts, tg_id, who, title, formula, total, detail, hidden) VALUES(?,?,?,?,?,?,?,?)",
            (ts, tg_id, who, title, formula, total, json.dumps(detail, ensure_ascii=False), int(hidden)),
        )
        self.c.commit()
        return {"id": cur.lastrowid, "ts": ts, "tg_id": tg_id, "who": who, "title": title,
                "formula": formula, "total": total, "detail": detail, "hidden": bool(hidden)}

    def rolls(self, limit: int = 100) -> list[dict]:
        rows = self.c.execute("SELECT * FROM rolls ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r["id"], "ts": r["ts"], "tg_id": r["tg_id"], "who": r["who"], "title": r["title"],
                 "formula": r["formula"], "total": r["total"], "detail": json.loads(r["detail"]),
                 "hidden": bool(r["hidden"])} for r in reversed(rows)]

    def clear_rolls(self) -> None:
        self.c.execute("DELETE FROM rolls")
        self.c.commit()

    # --- roll requests from the GM ---
    def add_request(self, tg_id: int, title: str, kind: str, hidden: bool) -> int:
        cur = self.c.execute(
            "INSERT INTO requests(ts, tg_id, title, kind, hidden) VALUES(?,?,?,?,?)",
            (time.time(), tg_id, title, kind, int(hidden)),
        )
        self.c.commit()
        return cur.lastrowid

    def request(self, rid: int) -> dict | None:
        r = self.c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
        return dict(r) if r else None

    def update_request(self, rid: int, **kw) -> None:
        sets = ", ".join(f"{k}=?" for k in kw)
        self.c.execute(f"UPDATE requests SET {sets} WHERE id=?", (*kw.values(), rid))
        self.c.commit()
