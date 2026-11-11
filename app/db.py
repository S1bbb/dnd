"""Хранилище на SQLite.

games      — игры (кампании): своя партия, лобби-код, бой, сцены, журнал;
players    — люди из Telegram;
characters — персонажи, каждый в своей игре; rev растёт при каждом изменении (защита от одновременных правок);
history    — снимки персонажа до и после изменения: история и отмена;
rolls / requests — броски и запросы мастера; journal (+ journal_rev) — журнал с историей правок;
scenes / files — сцены с картой и загруженные картинки; ops — уже выполненные операции (повторы игнорируются)."""
import json
import secrets
import sqlite3
import time

from .rules import DEFAULT_CHAR

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    tg_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
    char TEXT NOT NULL DEFAULT '{}', sheet_msgs TEXT NOT NULL DEFAULT '[]', joined_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
    lobby_code TEXT UNIQUE, created REAL NOT NULL, updated REAL NOT NULL, state TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS rolls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, tg_id INTEGER, who TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '', formula TEXT NOT NULL, total INTEGER NOT NULL, detail TEXT NOT NULL,
    hidden INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, tg_id INTEGER NOT NULL, title TEXT NOT NULL,
    kind TEXT NOT NULL, msg_id INTEGER, hidden INTEGER NOT NULL DEFAULT 0, roll_id INTEGER
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, game_id INTEGER, char_id INTEGER NOT NULL, ts REAL NOT NULL,
    actor TEXT NOT NULL, label TEXT NOT NULL, before TEXT NOT NULL, after TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT, game_id INTEGER NOT NULL, cat TEXT NOT NULL DEFAULT 'note',
    title TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '', vis TEXT NOT NULL DEFAULT 'gm',
    author TEXT NOT NULL DEFAULT 'Мастер', scene_id INTEGER, char_id INTEGER, quest TEXT NOT NULL DEFAULT '',
    session INTEGER, created REAL NOT NULL, updated REAL NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
    auto INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS journal_rev (
    id INTEGER PRIMARY KEY AUTOINCREMENT, entry_id INTEGER NOT NULL, ts REAL NOT NULL, author TEXT NOT NULL,
    title TEXT NOT NULL, body TEXT NOT NULL, cat TEXT NOT NULL, vis TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scenes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, game_id INTEGER NOT NULL, name TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT, game_id INTEGER, mime TEXT NOT NULL, data BLOB NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ops (op TEXT PRIMARY KEY, ts REAL NOT NULL, result TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""

CHAR_TABLE = """
CREATE TABLE characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT, game_id INTEGER NOT NULL DEFAULT 1, tg_id INTEGER,
    token TEXT NOT NULL UNIQUE, data TEXT NOT NULL, sheet_msgs TEXT NOT NULL DEFAULT '[]',
    map_msgs TEXT NOT NULL DEFAULT '[]', rev INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL
)"""


def new_token() -> str:
    # для ссылки t.me/bot?start=... разрешены только буквы, цифры, _ и -
    return "c" + secrets.token_hex(5)


def lobby_code() -> str:
    return str(10000 + secrets.randbelow(90000))


def J(x) -> str:
    return json.dumps(x, ensure_ascii=False)


class Conflict(Exception):
    """Персонажа уже изменили — клиент должен обновить данные."""


class DB:
    def __init__(self, path: str):
        self.path = path
        self.c = sqlite3.connect(path, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.c.execute("PRAGMA journal_mode=WAL")
        self.c.executescript(SCHEMA)
        self._migrate()
        self.c.commit()

    # ---------- миграции ----------
    def _cols(self, table: str) -> set[str]:
        return {r["name"] for r in self.c.execute(f"PRAGMA table_info({table})")}

    def _migrate(self) -> None:
        has_chars = self.c.execute("SELECT 1 FROM sqlite_master WHERE name='characters'").fetchone()
        if not has_chars:
            self.c.execute(CHAR_TABLE)
        elif "game_id" not in self._cols("characters"):
            # v2 → v3: персонажи получают игру, rev и возможность нескольких персонажей у игрока (в разных играх)
            self.c.execute("ALTER TABLE characters RENAME TO characters_old")
            self.c.execute(CHAR_TABLE)
            self.c.execute("INSERT INTO characters(id, game_id, tg_id, token, data, sheet_msgs, created) "
                           "SELECT id, 1, tg_id, token, data, sheet_msgs, created FROM characters_old")
            self.c.execute("DROP TABLE characters_old")
        self.c.execute("CREATE UNIQUE INDEX IF NOT EXISTS char_game_player ON characters(game_id, tg_id) WHERE tg_id IS NOT NULL")
        for table, cols in (("rolls", {"game_id": "INTEGER", "char_id": "INTEGER"}),
                            ("requests", {"game_id": "INTEGER", "char_id": "INTEGER", "grp": "TEXT", "status": "TEXT DEFAULT 'pending'",
                                          "vis": "TEXT DEFAULT 'player'", "ans_ts": "REAL", "dc": "INTEGER"})):
            have = self._cols(table)
            for col, typ in cols.items():
                if col not in have:
                    self.c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        # самая первая версия хранила персонажа прямо в players
        for r in self.c.execute("SELECT * FROM players").fetchall():
            ch = json.loads(r["char"] or "{}")
            if ch and not self.c.execute("SELECT 1 FROM characters WHERE tg_id=?", (r["tg_id"],)).fetchone():
                self.c.execute("INSERT INTO characters(game_id, tg_id, token, data, sheet_msgs, created) VALUES(1,?,?,?,?,?)",
                               (r["tg_id"], new_token(), J(ch), r["sheet_msgs"], r["joined_at"]))
                self.c.execute("UPDATE players SET char='{}' WHERE tg_id=?", (r["tg_id"],))
        if not self.c.execute("SELECT 1 FROM games").fetchone():
            old = self.c.execute("SELECT v FROM kv WHERE k='lobby_code'").fetchone()
            code = json.loads(old["v"]) if old else lobby_code()
            now = time.time()
            self.c.execute("INSERT INTO games(id, name, lobby_code, created, updated) VALUES(1, 'Первая игра', ?, ?, ?)", (code, now, now))
            self.c.execute("UPDATE characters SET game_id=1 WHERE game_id IS NULL")
            self.c.execute("UPDATE rolls SET game_id=1 WHERE game_id IS NULL")
            self.c.execute("UPDATE requests SET game_id=1 WHERE game_id IS NULL")
            self.c.execute("DELETE FROM kv WHERE k LIKE 'wiz:%'")  # черновики старого мастера создания несовместимы

    def backup(self, dest: str) -> None:
        out = sqlite3.connect(dest)
        with out:
            self.c.backup(out)
        out.close()

    # ---------- kv и идемпотентность ----------
    def get(self, k: str, default=None):
        r = self.c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r["v"]) if r else default

    def set(self, k: str, v) -> None:
        self.c.execute("INSERT OR REPLACE INTO kv(k, v) VALUES(?, ?)", (k, J(v)))
        self.c.commit()

    def delete(self, k: str) -> None:
        self.c.execute("DELETE FROM kv WHERE k=?", (k,))
        self.c.commit()

    def prefixed(self, prefix: str) -> list[tuple[str, object]]:
        rows = self.c.execute("SELECT k, v FROM kv WHERE k LIKE ? ORDER BY rowid", (prefix + "%",)).fetchall()
        return [(r["k"], json.loads(r["v"])) for r in rows]

    def op_done(self, op: str):
        r = self.c.execute("SELECT result FROM ops WHERE op=?", (op,)).fetchone()
        return json.loads(r["result"]) if r else None

    def op_save(self, op: str, result) -> None:
        self.c.execute("INSERT OR REPLACE INTO ops(op, ts, result) VALUES(?,?,?)", (op, time.time(), J(result)))
        self.c.execute("DELETE FROM ops WHERE ts < ?", (time.time() - 86400 * 3,))
        self.c.commit()

    # ---------- игры ----------
    def _game(self, r) -> dict:
        return {"id": r["id"], "name": r["name"], "status": r["status"], "lobby": r["lobby_code"],
                "created": r["created"], "updated": r["updated"], "state": json.loads(r["state"])}

    def games(self) -> list[dict]:
        return [self._game(r) for r in self.c.execute("SELECT * FROM games ORDER BY status='archived', updated DESC")]

    def game(self, gid: int) -> dict | None:
        r = self.c.execute("SELECT * FROM games WHERE id=?", (gid,)).fetchone()
        return self._game(r) if r else None

    def game_by_code(self, code: str) -> dict | None:
        r = self.c.execute("SELECT * FROM games WHERE lobby_code=? AND status='active'", (code,)).fetchone()
        return self._game(r) if r else None

    def create_game(self, name: str) -> dict:
        now = time.time()
        for _ in range(20):
            try:
                cur = self.c.execute("INSERT INTO games(name, lobby_code, created, updated) VALUES(?,?,?,?)",
                                     (name, lobby_code(), now, now))
                break
            except sqlite3.IntegrityError:
                continue
        self.c.commit()
        return self.game(cur.lastrowid)

    def update_game(self, gid: int, **kw) -> None:
        if "state" in kw:
            kw["state"] = J(kw["state"])
        kw["updated"] = time.time()
        sets = ", ".join(f"{k}=?" for k in kw)
        self.c.execute(f"UPDATE games SET {sets} WHERE id=?", (*kw.values(), gid))
        self.c.commit()

    def new_lobby(self, gid: int) -> str:
        while True:
            code = lobby_code()
            try:
                self.update_game(gid, lobby_code=code)
                return code
            except sqlite3.IntegrityError:
                continue

    # ---------- игроки ----------
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

    def active_game_of(self, tg_id: int) -> int | None:
        g = self.get(f"pg:{tg_id}")
        if g and self.c.execute("SELECT 1 FROM characters WHERE game_id=? AND tg_id=?", (g, tg_id)).fetchone():
            return g
        r = self.c.execute("SELECT c.game_id FROM characters c JOIN games g ON g.id=c.game_id WHERE c.tg_id=? "
                           "ORDER BY g.status='archived', g.updated DESC LIMIT 1", (tg_id,)).fetchone()
        return r["game_id"] if r else None

    def set_active_game(self, tg_id: int, gid: int) -> None:
        self.set(f"pg:{tg_id}", gid)

    # ---------- персонажи ----------
    def _char(self, r) -> dict:
        data = {**DEFAULT_CHAR, **json.loads(r["data"])}
        data["_id"] = r["id"]
        return {
            "id": r["id"], "game_id": r["game_id"], "tg_id": r["tg_id"], "token": r["token"], "rev": r["rev"],
            "data": data, "sheet_msgs": json.loads(r["sheet_msgs"]), "map_msgs": json.loads(r["map_msgs"]),
            "player": self.player(r["tg_id"]) if r["tg_id"] else None,
        }

    def character(self, cid: int) -> dict | None:
        r = self.c.execute("SELECT * FROM characters WHERE id=?", (cid,)).fetchone()
        return self._char(r) if r else None

    def character_of(self, tg_id: int, gid: int | None = None) -> dict | None:
        gid = gid or self.active_game_of(tg_id)
        if gid is None:
            return None
        r = self.c.execute("SELECT * FROM characters WHERE tg_id=? AND game_id=?", (tg_id, gid)).fetchone()
        return self._char(r) if r else None

    def characters_of_player(self, tg_id: int) -> list[dict]:
        return [self._char(r) for r in self.c.execute("SELECT * FROM characters WHERE tg_id=? ORDER BY id", (tg_id,))]

    def character_by_token(self, token: str) -> dict | None:
        r = self.c.execute("SELECT * FROM characters WHERE token=?", (token,)).fetchone()
        return self._char(r) if r else None

    def characters(self, gid: int | None = None) -> list[dict]:
        if gid is None:
            rows = self.c.execute("SELECT * FROM characters ORDER BY created, id")
        else:
            rows = self.c.execute("SELECT * FROM characters WHERE game_id=? ORDER BY created, id", (gid,))
        return [self._char(r) for r in rows]

    def create_character(self, data: dict, tg_id: int | None = None, gid: int = 1) -> dict:
        data = {k: v for k, v in data.items() if k != "_id"}
        cur = self.c.execute("INSERT INTO characters(game_id, tg_id, token, data, created) VALUES(?,?,?,?,?)",
                             (gid, tg_id, new_token(), J(data), time.time()))
        self.c.commit()
        return self.character(cur.lastrowid)

    def save_character(self, cid: int, data: dict, rev: int | None = None) -> int:
        """Сохранить; если передан rev и он устарел — Conflict. Возвращает новый rev."""
        data = {k: v for k, v in data.items() if k != "_id"}
        if rev is not None:
            cur = self.c.execute("UPDATE characters SET data=?, rev=rev+1 WHERE id=? AND rev=?", (J(data), cid, rev))
            if cur.rowcount == 0:
                raise Conflict()
        else:
            self.c.execute("UPDATE characters SET data=?, rev=rev+1 WHERE id=?", (J(data), cid))
        self.c.commit()
        return self.c.execute("SELECT rev FROM characters WHERE id=?", (cid,)).fetchone()["rev"]

    def bind(self, cid: int, tg_id: int) -> None:
        """Отдать персонажа игроку. Прежний персонаж игрока в этой игре остаётся у мастера без владельца."""
        gid = self.c.execute("SELECT game_id FROM characters WHERE id=?", (cid,)).fetchone()["game_id"]
        self.c.execute("UPDATE characters SET tg_id=NULL, sheet_msgs='[]', map_msgs='[]' WHERE tg_id=? AND game_id=? AND id<>?",
                       (tg_id, gid, cid))
        self.c.execute("UPDATE characters SET tg_id=?, sheet_msgs='[]', map_msgs='[]' WHERE id=?", (tg_id, cid))
        self.c.commit()
        self.set_active_game(tg_id, gid)

    def unbind(self, cid: int) -> None:
        self.c.execute("UPDATE characters SET tg_id=NULL, sheet_msgs='[]', map_msgs='[]', token=? WHERE id=?", (new_token(), cid))
        self.c.commit()

    def delete_character(self, cid: int) -> None:
        self.c.execute("DELETE FROM characters WHERE id=?", (cid,))
        self.c.commit()

    def add_msg(self, cid: int, msg_id: int, col: str = "sheet_msgs", keep: int = 3) -> None:
        r = self.c.execute(f"SELECT {col} FROM characters WHERE id=?", (cid,)).fetchone()
        if not r:
            return
        msgs = (json.loads(r[col]) + [msg_id])[-keep:]
        self.c.execute(f"UPDATE characters SET {col}=? WHERE id=?", (J(msgs), cid))
        self.c.commit()

    def add_sheet_msg(self, cid: int, msg_id: int) -> None:
        self.add_msg(cid, msg_id)

    # ---------- история и отмена ----------
    def add_history(self, gid, cid, actor, label, before, after) -> int:
        cur = self.c.execute("INSERT INTO history(game_id, char_id, ts, actor, label, before, after) VALUES(?,?,?,?,?,?,?)",
                             (gid, cid, time.time(), actor, label, J(before), J(after)))
        self.c.commit()
        return cur.lastrowid

    def history(self, cid: int, limit: int = 30) -> list[dict]:
        rows = self.c.execute("SELECT id, ts, actor, label, undone FROM history WHERE char_id=? ORDER BY id DESC LIMIT ?",
                              (cid, limit)).fetchall()
        return [dict(r) for r in rows]

    def last_history(self, cid: int) -> dict | None:
        r = self.c.execute("SELECT * FROM history WHERE char_id=? AND undone=0 ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
        return dict(r) if r else None

    def mark_undone(self, hid: int) -> None:
        self.c.execute("UPDATE history SET undone=1 WHERE id=?", (hid,))
        self.c.commit()

    # ---------- броски ----------
    def add_roll(self, tg_id, who, title, formula, total, detail: dict, gid=None, cid=None, hidden=0) -> dict:
        ts = time.time()
        cur = self.c.execute(
            "INSERT INTO rolls(ts, tg_id, who, title, formula, total, detail, hidden, game_id, char_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (ts, tg_id, who, title, formula, total, J(detail), hidden, gid, cid))
        self.c.commit()
        return {"id": cur.lastrowid, "ts": ts, "tg_id": tg_id, "who": who, "title": title, "formula": formula,
                "total": total, "detail": detail, "hidden": hidden, "char_id": cid}

    def rolls(self, gid: int | None = None, limit: int = 100) -> list[dict]:
        if gid is None:
            rows = self.c.execute("SELECT * FROM rolls ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        else:
            rows = self.c.execute("SELECT * FROM rolls WHERE game_id=? ORDER BY id DESC LIMIT ?", (gid, limit)).fetchall()
        return [{"id": r["id"], "ts": r["ts"], "tg_id": r["tg_id"], "who": r["who"], "title": r["title"], "formula": r["formula"],
                 "total": r["total"], "detail": json.loads(r["detail"]), "hidden": r["hidden"], "char_id": r["char_id"]}
                for r in reversed(rows)]

    def clear_rolls(self, gid: int) -> None:
        self.c.execute("DELETE FROM rolls WHERE game_id=?", (gid,))
        self.c.commit()

    # ---------- запросы бросков ----------
    def add_request(self, tg_id: int, title: str, kind: str, gid=None, cid=None, grp=None, vis="player", dc=None) -> int:
        cur = self.c.execute("INSERT INTO requests(ts, tg_id, title, kind, game_id, char_id, grp, vis, status, dc) VALUES(?,?,?,?,?,?,?,?, 'pending', ?)",
                             (time.time(), tg_id, title, kind, gid, cid, grp, vis, dc))
        self.c.commit()
        return cur.lastrowid

    def request(self, rid: int) -> dict | None:
        r = self.c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
        return dict(r) if r else None

    def requests(self, gid: int, limit: int = 60) -> list[dict]:
        rows = self.c.execute("SELECT q.*, r.total AS total FROM requests q LEFT JOIN rolls r ON r.id=q.roll_id "
                              "WHERE q.game_id=? ORDER BY q.id DESC LIMIT ?", (gid, limit)).fetchall()
        return [dict(r) for r in rows]

    def pending_request(self, tg_id: int, gid: int) -> dict | None:
        r = self.c.execute("SELECT * FROM requests WHERE tg_id=? AND game_id=? AND status='pending' ORDER BY id LIMIT 1",
                           (tg_id, gid)).fetchone()
        return dict(r) if r else None

    def update_request(self, rid: int, **kw) -> None:
        sets = ", ".join(f"{k}=?" for k in kw)
        self.c.execute(f"UPDATE requests SET {sets} WHERE id=?", (*kw.values(), rid))
        self.c.commit()

    # ---------- журнал ----------
    def _entry(self, r) -> dict:
        return dict(r)

    def journal(self, gid: int, q: str = "", cat: str = "", deleted: bool = False, vis: str | None = None,
                char_id=None, scene_id=None, limit: int = 300) -> list[dict]:
        sql = "SELECT * FROM journal WHERE game_id=? AND deleted=?"
        args: list = [gid, 1 if deleted else 0]
        if q:
            sql += " AND (title LIKE ? OR body LIKE ? OR quest LIKE ?)"
            args += [f"%{q}%"] * 3
        if cat:
            sql += " AND cat=?"
            args.append(cat)
        if vis:
            sql += " AND vis=?"
            args.append(vis)
        if char_id:
            sql += " AND char_id=?"
            args.append(char_id)
        if scene_id:
            sql += " AND scene_id=?"
            args.append(scene_id)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [self._entry(r) for r in self.c.execute(sql, args)]

    def entry(self, eid: int) -> dict | None:
        r = self.c.execute("SELECT * FROM journal WHERE id=?", (eid,)).fetchone()
        return self._entry(r) if r else None

    def add_entry(self, gid: int, **kw) -> dict:
        now = time.time()
        kw = {k: v for k, v in kw.items() if k in ("cat", "title", "body", "vis", "author", "scene_id", "char_id", "quest", "session", "auto")}
        cols = ", ".join(["game_id", "created", "updated", *kw])
        qs = ", ".join("?" * (3 + len(kw)))
        cur = self.c.execute(f"INSERT INTO journal({cols}) VALUES({qs})", (gid, now, now, *kw.values()))
        self.c.commit()
        e = self.entry(cur.lastrowid)
        self._rev(e, e["author"])
        return e

    def _rev(self, e: dict, author: str) -> None:
        self.c.execute("INSERT INTO journal_rev(entry_id, ts, author, title, body, cat, vis) VALUES(?,?,?,?,?,?,?)",
                       (e["id"], time.time(), author, e["title"], e["body"], e["cat"], e["vis"]))
        self.c.commit()

    def update_entry(self, eid: int, author: str, **kw) -> dict:
        kw = {k: v for k, v in kw.items() if k in ("cat", "title", "body", "vis", "scene_id", "char_id", "quest", "session", "deleted")}
        kw["updated"] = time.time()
        sets = ", ".join(f"{k}=?" for k in kw)
        self.c.execute(f"UPDATE journal SET {sets} WHERE id=?", (*kw.values(), eid))
        self.c.commit()
        e = self.entry(eid)
        if any(k in kw for k in ("title", "body", "cat", "vis")):
            self._rev(e, author)
        return e

    def entry_revs(self, eid: int) -> list[dict]:
        return [dict(r) for r in self.c.execute("SELECT * FROM journal_rev WHERE entry_id=? ORDER BY id DESC", (eid,))]

    # ---------- сцены и файлы ----------
    def scenes(self, gid: int) -> list[dict]:
        return [{"id": r["id"], "name": r["name"], **json.loads(r["data"])}
                for r in self.c.execute("SELECT * FROM scenes WHERE game_id=? ORDER BY id", (gid,))]

    def scene(self, sid: int) -> dict | None:
        r = self.c.execute("SELECT * FROM scenes WHERE id=?", (sid,)).fetchone()
        return {"id": r["id"], "game_id": r["game_id"], "name": r["name"], **json.loads(r["data"])} if r else None

    def save_scene(self, gid: int, sid: int | None, name: str, data: dict) -> int:
        data = {k: v for k, v in data.items() if k not in ("id", "name", "game_id")}
        if sid:
            self.c.execute("UPDATE scenes SET name=?, data=? WHERE id=? AND game_id=?", (name, J(data), sid, gid))
        else:
            sid = self.c.execute("INSERT INTO scenes(game_id, name, data, created) VALUES(?,?,?,?)",
                                 (gid, name, J(data), time.time())).lastrowid
        self.c.commit()
        return sid

    def delete_scene(self, sid: int) -> None:
        self.c.execute("DELETE FROM scenes WHERE id=?", (sid,))
        self.c.commit()

    def add_file(self, gid: int, mime: str, data: bytes) -> int:
        cur = self.c.execute("INSERT INTO files(game_id, mime, data, created) VALUES(?,?,?,?)", (gid, mime, data, time.time()))
        self.c.commit()
        return cur.lastrowid

    def file(self, fid: int) -> dict | None:
        r = self.c.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
        return dict(r) if r else None
