"""翻译历史：SQLite 持久化 + FTS5 全文检索。"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime

from ..config import paths
from ..utils.logging import get_logger

log = get_logger("history")


@dataclass
class HistoryItem:
    id: int
    src_text: str
    dst_text: str
    source_lang: str
    target_lang: str
    engine: str
    favorite: int
    created_at: str

    @property
    def time_text(self) -> str:
        try:
            return datetime.fromisoformat(self.created_at).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return self.created_at


_SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    src_text    TEXT NOT NULL,
    dst_text    TEXT NOT NULL,
    source_lang TEXT DEFAULT '',
    target_lang TEXT DEFAULT '',
    engine      TEXT DEFAULT '',
    favorite    INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_created ON history(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_history_fav ON history(favorite);

CREATE VIRTUAL TABLE IF NOT EXISTS history_fts USING fts5(
    src_text, dst_text, content='history', content_rowid='id',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS history_ai AFTER INSERT ON history BEGIN
    INSERT INTO history_fts(rowid, src_text, dst_text)
    VALUES (new.id, new.src_text, new.dst_text);
END;
CREATE TRIGGER IF NOT EXISTS history_ad AFTER DELETE ON history BEGIN
    INSERT INTO history_fts(history_fts, rowid, src_text, dst_text)
    VALUES ('delete', old.id, old.src_text, old.dst_text);
END;
CREATE TRIGGER IF NOT EXISTS history_au AFTER UPDATE ON history BEGIN
    INSERT INTO history_fts(history_fts, rowid, src_text, dst_text)
    VALUES ('delete', old.id, old.src_text, old.dst_text);
    INSERT INTO history_fts(rowid, src_text, dst_text)
    VALUES (new.id, new.src_text, new.dst_text);
END;
"""


class HistoryStore:
    def __init__(self, db_path: str | None = None) -> None:
        self._path = db_path or paths.history_db_path()
        self._lock = threading.Lock()
        self._fts_ok = True
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        try:
            with self._lock:
                self._conn.executescript(_SCHEMA)
                self._conn.commit()
        except sqlite3.OperationalError as exc:
            # 某些 SQLite 未编译 FTS5，降级为 LIKE 检索
            log.warning("FTS5 不可用（%s），改用 LIKE 检索", exc)
            self._fts_ok = False
            with self._lock:
                self._conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS history (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        src_text    TEXT NOT NULL,
                        dst_text    TEXT NOT NULL,
                        source_lang TEXT DEFAULT '',
                        target_lang TEXT DEFAULT '',
                        engine      TEXT DEFAULT '',
                        favorite    INTEGER DEFAULT 0,
                        created_at  TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_history_created ON history(created_at DESC);
                    """
                )
                self._conn.commit()

    # ------------------------------------------------------------------ #
    def _row(self, row: sqlite3.Row) -> HistoryItem:
        return HistoryItem(
            id=row["id"],
            src_text=row["src_text"],
            dst_text=row["dst_text"],
            source_lang=row["source_lang"],
            target_lang=row["target_lang"],
            engine=row["engine"],
            favorite=row["favorite"],
            created_at=row["created_at"],
        )

    def add(
        self,
        src_text: str,
        dst_text: str,
        source_lang: str = "",
        target_lang: str = "",
        engine: str = "",
    ) -> int:
        if not src_text.strip() and not dst_text.strip():
            return -1
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO history (src_text, dst_text, source_lang, target_lang, engine, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (src_text, dst_text, source_lang, target_lang, engine, now),
            )
            self._conn.commit()
            return int(cur.lastrowid or -1)

    def query(self, keyword: str = "", only_favorite: bool = False, limit: int = 500) -> list[HistoryItem]:
        keyword = keyword.strip()
        sql = "SELECT * FROM history"
        params: list[object] = []
        where: list[str] = []

        if keyword:
            if self._fts_ok:
                # 用 FTS 子查询匹配，再回表取完整记录
                escaped = keyword.replace('"', '""')
                where.append(
                    "id IN (SELECT rowid FROM history_fts WHERE history_fts MATCH ?)"
                )
                params.append(f'"{escaped}"*')
            else:
                where.append("(src_text LIKE ? OR dst_text LIKE ?)")
                params.extend([f"%{keyword}%", f"%{keyword}%"])

        if only_favorite:
            where.append("favorite = 1")

        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY favorite DESC, id DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            try:
                rows = self._conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError as exc:
                log.warning("查询失败（%s），回退 LIKE", exc)
                like = f"%{keyword}%" if keyword else "%"
                rows = self._conn.execute(
                    "SELECT * FROM history WHERE src_text LIKE ? OR dst_text LIKE ?"
                    " ORDER BY id DESC LIMIT ?",
                    (like, like, limit),
                ).fetchall()
        return [self._row(r) for r in rows]

    def toggle_favorite(self, item_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE history SET favorite = 1 - favorite WHERE id = ?", (item_id,)
            )
            self._conn.commit()

    def delete(self, item_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM history WHERE id = ?", (item_id,))
            self._conn.commit()

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM history")
            self._conn.commit()

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM history").fetchone()
        return int(row["n"]) if row else 0

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
