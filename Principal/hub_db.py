import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from hub_paths import BACKUPS, STATE


DB_PATH = STATE / "aicorte.sqlite3"
PROMPT_DB_PATH = STATE / "prompt_state.sqlite3"
SCHEMA_VERSION = 4


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class HubDB:
    def __init__(self, path=DB_PATH, prompt_path=PROMPT_DB_PATH):
        self.path = Path(path)
        self.prompt_path = Path(prompt_path)
        self._write_lock = threading.RLock()
        self.migrate()

    @contextmanager
    def connect(self, prompt=False):
        path = self.prompt_path if prompt else self.path
        connection = sqlite3.connect(path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def migrate(self):
        with self._write_lock, self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tool_overrides (
                    tool_id TEXT PRIMARY KEY,
                    availability TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS custom_tools (
                    tool_id TEXT PRIMARY KEY,
                    definition_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS operations (
                    operation_id TEXT PRIMARY KEY,
                    tool_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    phase TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_operations_created
                    ON operations(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operations_tool
                    ON operations(tool_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tool_id TEXT NOT NULL DEFAULT '',
                    operation_id TEXT NOT NULL DEFAULT '',
                    level TEXT NOT NULL,
                    event TEXT NOT NULL,
                    message TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at DESC);
                CREATE TABLE IF NOT EXISTS favorites (
                    tool_id TEXT PRIMARY KEY,
                    position INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recent_tools (
                    tool_id TEXT PRIMARY KEY,
                    last_accessed TEXT NOT NULL,
                    access_count INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS runtime_history (
                    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tool_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    pid INTEGER,
                    exit_code INTEGER,
                    message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS install_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    tool_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "INSERT INTO metadata(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
            conn.execute(
                "UPDATE operations SET status='interrupted', finished_at=?, "
                "message='Operação interrompida pelo reinício do hub' "
                "WHERE status IN ('queued','running','cancelling')",
                (utc_now(),),
            )
        with self._write_lock, self.connect(prompt=True) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS prompt_state (
                    app_key TEXT NOT NULL,
                    field_key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (app_key, field_key)
                );
                CREATE INDEX IF NOT EXISTS idx_prompt_state_updated
                    ON prompt_state(updated_at DESC);
                """
            )

    def get_overrides(self):
        with self.connect() as conn:
            return dict(conn.execute("SELECT tool_id, availability FROM tool_overrides"))

    def set_override(self, tool_id, availability):
        with self._write_lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO tool_overrides(tool_id, availability, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(tool_id) DO UPDATE SET
                    availability=excluded.availability,
                    updated_at=excluded.updated_at
                """,
                (tool_id, availability, utc_now()),
            )

    def get_settings(self):
        with self.connect() as conn:
            rows = conn.execute("SELECT key, value_json FROM settings")
            return {row["key"]: json.loads(row["value_json"]) for row in rows}

    def set_setting(self, key, value):
        with self._write_lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO settings(key, value_json, updated_at) VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json=excluded.value_json,
                    updated_at=excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False), utc_now()),
            )

    def custom_tools(self):
        with self.connect() as conn:
            return [
                json.loads(row["definition_json"])
                for row in conn.execute("SELECT definition_json FROM custom_tools ORDER BY created_at")
            ]

    def save_custom_tool(self, definition):
        now = utc_now()
        with self._write_lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO custom_tools(tool_id, definition_json, created_at, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(tool_id) DO UPDATE SET
                    definition_json=excluded.definition_json,
                    updated_at=excluded.updated_at
                """,
                (definition["id"], json.dumps(definition, ensure_ascii=False), now, now),
            )

    def remove_custom_tool(self, tool_id):
        with self._write_lock, self.connect() as conn:
            conn.execute("DELETE FROM custom_tools WHERE tool_id=?", (tool_id,))

    def create_operation(self, tool_id, kind, payload=None):
        operation_id = uuid.uuid4().hex
        now = utc_now()
        with self._write_lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO operations(
                    operation_id, tool_id, kind, status, progress, phase,
                    message, payload_json, result_json, created_at
                ) VALUES(?, ?, ?, 'queued', 0, 'queued', 'Aguardando na fila', ?, '{}', ?)
                """,
                (operation_id, tool_id, kind, json.dumps(payload or {}, ensure_ascii=False), now),
            )
        self.add_event(tool_id, "info", "operation.queued", "Operação adicionada à fila", operation_id=operation_id)
        return operation_id

    def operation(self, operation_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            return self._operation_dict(row) if row else None

    def operations(self, limit=100):
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM operations ORDER BY created_at DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            )
            return [self._operation_dict(row) for row in rows]

    @staticmethod
    def _operation_dict(row):
        payload = dict(row)
        payload["cancel_requested"] = bool(payload["cancel_requested"])
        payload["payload"] = json.loads(payload.pop("payload_json") or "{}")
        payload["result"] = json.loads(payload.pop("result_json") or "{}")
        return payload

    def update_operation(self, operation_id, **fields):
        allowed = {
            "status",
            "progress",
            "phase",
            "message",
            "cancel_requested",
            "started_at",
            "finished_at",
        }
        assignments = []
        values = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            assignments.append(f"{key}=?")
            values.append(int(value) if key == "cancel_requested" else value)
        if not assignments:
            return
        values.append(operation_id)
        with self._write_lock, self.connect() as conn:
            conn.execute(f"UPDATE operations SET {', '.join(assignments)} WHERE operation_id=?", values)

    def finish_operation(self, operation_id, status, message, result=None):
        with self._write_lock, self.connect() as conn:
            conn.execute(
                """
                UPDATE operations SET status=?, progress=?, phase=?, message=?,
                    result_json=?, finished_at=?
                WHERE operation_id=?
                """,
                (
                    status,
                    100 if status in {"completed", "failed", "cancelled"} else 0,
                    status,
                    message,
                    json.dumps(result or {}, ensure_ascii=False),
                    utc_now(),
                    operation_id,
                ),
            )

    def request_cancel(self, operation_id):
        with self._write_lock, self.connect() as conn:
            changed = conn.execute(
                "UPDATE operations SET cancel_requested=1, status=CASE "
                "WHEN status='queued' THEN 'cancelled' ELSE 'cancelling' END, "
                "message='Cancelamento solicitado' "
                "WHERE operation_id=? AND status IN ('queued','running')",
                (operation_id,),
            ).rowcount
        return bool(changed)

    def cancel_requested(self, operation_id):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT cancel_requested, status FROM operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            return bool(row and (row["cancel_requested"] or row["status"] == "cancelled"))

    def add_event(
        self,
        tool_id,
        level,
        event,
        message,
        *,
        operation_id="",
        metadata=None,
    ):
        with self._write_lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO events(
                    tool_id, operation_id, level, event, message, metadata_json, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tool_id or "",
                    operation_id or "",
                    level,
                    event,
                    message,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    utc_now(),
                ),
            )

    def events(self, limit=100, tool_id=""):
        with self.connect() as conn:
            if tool_id:
                rows = conn.execute(
                    "SELECT * FROM events WHERE tool_id=? ORDER BY event_id DESC LIMIT ?",
                    (tool_id, max(1, min(int(limit), 500))),
                )
            else:
                rows = conn.execute(
                    "SELECT * FROM events ORDER BY event_id DESC LIMIT ?",
                    (max(1, min(int(limit), 500)),),
                )
            result = []
            for row in rows:
                item = dict(row)
                item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
                result.append(item)
            return result

    def set_favorite(self, tool_id, enabled):
        with self._write_lock, self.connect() as conn:
            if enabled:
                position = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM favorites").fetchone()[0]
                conn.execute(
                    "INSERT OR IGNORE INTO favorites(tool_id, position, created_at) VALUES(?, ?, ?)",
                    (tool_id, position, utc_now()),
                )
            else:
                conn.execute("DELETE FROM favorites WHERE tool_id=?", (tool_id,))

    def favorites(self):
        with self.connect() as conn:
            return [row[0] for row in conn.execute("SELECT tool_id FROM favorites ORDER BY position, created_at")]

    def touch_recent(self, tool_id):
        with self._write_lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO recent_tools(tool_id, last_accessed, access_count) VALUES(?, ?, 1)
                ON CONFLICT(tool_id) DO UPDATE SET
                    last_accessed=excluded.last_accessed,
                    access_count=recent_tools.access_count + 1
                """,
                (tool_id, utc_now()),
            )

    def recents(self, limit=12):
        with self.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT tool_id, last_accessed, access_count FROM recent_tools "
                    "ORDER BY last_accessed DESC LIMIT ?",
                    (max(1, min(int(limit), 50)),),
                )
            ]

    def runtime_event(self, tool_id, action, pid=None, exit_code=None, message=""):
        with self._write_lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO runtime_history(tool_id, action, pid, exit_code, message, created_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (tool_id, action, pid, exit_code, message, utc_now()),
            )

    def prompt_values(self, app_key):
        with self.connect(prompt=True) as conn:
            return dict(
                conn.execute(
                    "SELECT field_key, value FROM prompt_state WHERE app_key=?",
                    (app_key,),
                )
            )

    def save_prompt(self, app_key, field_key, value):
        with self._write_lock, self.connect(prompt=True) as conn:
            conn.execute(
                """
                INSERT INTO prompt_state(app_key, field_key, value, updated_at)
                VALUES(?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(app_key, field_key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (app_key, field_key, value),
            )

    def clear_prompt_app(self, app_key):
        with self._write_lock, self.connect(prompt=True) as conn:
            return conn.execute("DELETE FROM prompt_state WHERE app_key=?", (app_key,)).rowcount

    def prompt_stats(self):
        with self.connect(prompt=True) as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT app_key), COUNT(*), COALESCE(SUM(LENGTH(value)), 0) FROM prompt_state"
            ).fetchone()
            return {"apps": row[0], "fields": row[1], "bytes": row[2]}

    def backup(self):
        destination_root = (
            BACKUPS
            if self.path.resolve() == DB_PATH.resolve()
            else self.path.parent / "backups"
        )
        destination_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        targets = [
            (self.path, destination_root / f"aicorte-{stamp}.sqlite3"),
            (self.prompt_path, destination_root / f"prompts-{stamp}.sqlite3"),
        ]
        created = []
        for source, destination in targets:
            src = sqlite3.connect(source)
            dst = sqlite3.connect(destination)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            created.append(str(destination))
        return created

    def backups(self):
        BACKUPS.mkdir(parents=True, exist_ok=True)
        return [
            {"name": path.name, "path": str(path), "bytes": path.stat().st_size}
            for path in sorted(BACKUPS.glob("*.sqlite3"), key=lambda item: item.stat().st_mtime, reverse=True)
        ]
