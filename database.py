import sqlite3
import re
import os
import logging
from contextlib import contextmanager

log = logging.getLogger("nexus.db")

# Absolute path. A bare "nexus.db" resolves against the process CWD,
# which is how a bot ends up silently reading a different database.
DB_FILE = os.environ.get(
    "NEXUS_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "nexus.db"),
)


# ============================================================
# CONNECTION HANDLING
# ============================================================

def _connect():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def transaction():
    """All-or-nothing. A crash mid-command rolls back instead of half-committing."""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _query(sql, params=(), one=False):
    conn = _connect()
    try:
        cur = conn.execute(sql, params)
        return cur.fetchone() if one else cur.fetchall()
    finally:
        conn.close()


# ============================================================
# CORE USER SYSTEM
# ============================================================

def init_db():
    with transaction() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                np INTEGER NOT NULL DEFAULT 0
            )
        """)


def ensure_user(user_id: int):
    with transaction() as conn:
        conn.execute("INSERT OR IGNORE INTO users (user_id, np) VALUES (?, 0)", (user_id,))


def get_np(user_id: int) -> int:
    """Read-only. Deliberately does NOT create a row — the auto-rank loop
    calls this for every member every minute."""
    row = _query("SELECT np FROM users WHERE user_id = ?", (user_id,), one=True)
    return row[0] if row else 0


def get_all_np() -> dict:
    """One query for the whole auto-rank loop instead of N."""
    return dict(_query("SELECT user_id, np FROM users"))


def get_leaderboard(limit: int = 10):
    return _query(
        "SELECT user_id, np FROM users WHERE np > 0 ORDER BY np DESC LIMIT ?", (limit,)
    )


# ============================================================
# NP AUDIT LOG — every mutation flows through _apply_np
# ============================================================

def init_np_log():
    with transaction() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS np_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                actor_id INTEGER,
                old_np INTEGER NOT NULL,
                new_np INTEGER NOT NULL,
                source TEXT NOT NULL,
                note TEXT,
                at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_np_log_user ON np_log(user_id, at)")


def _apply_np(user_id: int, new_np: int, actor_id=None, source="unknown", note=None) -> int:
    """Single choke point for NP changes. Balance + audit row commit atomically."""
    new_np = max(int(new_np), 0)
    with transaction() as conn:
        conn.execute("INSERT OR IGNORE INTO users (user_id, np) VALUES (?, 0)", (user_id,))
        old = conn.execute("SELECT np FROM users WHERE user_id = ?", (user_id,)).fetchone()[0]
        if old != new_np:
            conn.execute("UPDATE users SET np = ? WHERE user_id = ?", (new_np, user_id))
            conn.execute(
                "INSERT INTO np_log (user_id, actor_id, old_np, new_np, source, note) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, actor_id, old, new_np, source, note),
            )
            log.info("NP %s: %s -> %s (%+d) source=%s actor=%s note=%s",
                     user_id, old, new_np, new_np - old, source, actor_id, note)
    return new_np


def add_np(user_id: int, amount: int, actor_id=None, source="add_np", note=None) -> int:
    return _apply_np(user_id, get_np(user_id) + amount, actor_id, source, note)


def remove_np(user_id: int, amount: int, actor_id=None, source="remove_np", note=None) -> int:
    return _apply_np(user_id, get_np(user_id) - amount, actor_id, source, note)


def set_np(user_id: int, amount: int, actor_id=None, source="set_np", note=None) -> int:
    """Absolute overwrite. DESTRUCTIVE — always pass actor_id and note."""
    return _apply_np(user_id, amount, actor_id, source, note)


def get_np_history(user_id: int, limit: int = 15):
    return _query(
        "SELECT old_np, new_np, actor_id, source, note, at FROM np_log "
        "WHERE user_id = ? ORDER BY at DESC, id DESC LIMIT ?",
        (user_id, limit),
    )


# ============================================================
# ROLE TAGS
# ============================================================

def init_role_tags_table():
    with transaction() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS role_tags (
                role_id INTEGER PRIMARY KEY,
                tag TEXT NOT NULL
            )
        """)


def set_role_tag(role_id: int, tag: str):
    with transaction() as conn:
        conn.execute("INSERT OR REPLACE INTO role_tags (role_id, tag) VALUES (?, ?)", (role_id, tag))


def remove_role_tag(role_id: int):
    with transaction() as conn:
        conn.execute("DELETE FROM role_tags WHERE role_id = ?", (role_id,))


def get_role_tag(role_id: int):
    row = _query("SELECT tag FROM role_tags WHERE role_id = ?", (role_id,), one=True)
    return row[0] if row else None


def get_all_role_tags():
    return _query("SELECT role_id, tag FROM role_tags")


# ============================================================
# RANKING SYSTEM
# ============================================================

def init_ranking_table():
    with transaction() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ranks (
                rank_id INTEGER PRIMARY KEY AUTOINCREMENT,
                rank_name TEXT NOT NULL UNIQUE,
                np_threshold INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                obtainable INTEGER NOT NULL DEFAULT 1
            )
        """)


def _ensure_column(conn, table, column, decl):
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not exists:
        return
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        log.warning("MIGRATION: added %s.%s", table, column)


def run_migrations():
    """Idempotent, runs every boot so new code never meets an old schema."""
    with transaction() as conn:
        _ensure_column(conn, "ranks", "obtainable", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn, "ranks", "position", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "promo_locks", "reason", "TEXT")
        _ensure_column(conn, "user_awards", "np_bonus", "INTEGER NOT NULL DEFAULT 0")


def add_rank(rank_name: str, np_threshold: int, role_id: int, position: int = 0, obtainable: int = 1):
    with transaction() as conn:
        conn.execute(
            "INSERT INTO ranks (rank_name, np_threshold, role_id, position, obtainable) "
            "VALUES (?, ?, ?, ?, ?)",
            (rank_name, np_threshold, role_id, position, 1 if obtainable else 0),
        )


def get_ranks():
    return _query(
        "SELECT rank_id, rank_name, np_threshold, role_id, obtainable "
        "FROM ranks ORDER BY np_threshold ASC"
    )


def get_appropriate_rank(np_amount: int, auto_only: bool = False):
    if auto_only:
        sql = (
            "SELECT rank_id, rank_name, role_id, obtainable FROM ranks "
            "WHERE np_threshold <= ? AND obtainable = 1 "
            "ORDER BY np_threshold DESC LIMIT 1"
        )
    else:
        sql = (
            "SELECT rank_id, rank_name, role_id, obtainable FROM ranks "
            "WHERE np_threshold <= ? ORDER BY np_threshold DESC LIMIT 1"
        )
    return _query(sql, (np_amount,), one=True)


def get_rank_by_name(rank_name: str):
    return _query(
        "SELECT rank_id, rank_name, np_threshold, role_id, obtainable "
        "FROM ranks WHERE rank_name = ?",
        (rank_name,),
        one=True,
    )


def get_rank_by_id(rank_id: int):
    return _query(
        "SELECT rank_id, rank_name, np_threshold, role_id, obtainable "
        "FROM ranks WHERE rank_id = ?",
        (rank_id,),
        one=True,
    )


def delete_rank(rank_id: int):
    with transaction() as conn:
        conn.execute("DELETE FROM ranks WHERE rank_id = ?", (rank_id,))
        conn.execute("DELETE FROM user_ranks WHERE current_rank_id = ?", (rank_id,))
    log.warning("rank %s deleted", rank_id)


# ============================================================
# USER RANKS + PROMO LOCKS
# ============================================================

def init_user_ranks_table():
    with transaction() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_ranks (
                user_id INTEGER PRIMARY KEY,
                current_rank_id INTEGER,
                FOREIGN KEY(current_rank_id) REFERENCES ranks(rank_id)
            )
        """)


def init_demotion_table():
    with transaction() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS promo_locks (
                user_id INTEGER PRIMARY KEY,
                locked INTEGER NOT NULL DEFAULT 0,
                reason TEXT
            )
        """)


def set_promo_lock(user_id: int, locked: bool, reason: str = None):
    with transaction() as conn:
        conn.execute("INSERT OR IGNORE INTO users (user_id, np) VALUES (?, 0)", (user_id,))
        conn.execute(
            "INSERT OR REPLACE INTO promo_locks (user_id, locked, reason) VALUES (?, ?, ?)",
            (user_id, 1 if locked else 0, reason),
        )
    log.info("promo_lock %s -> %s (%s)", user_id, locked, reason)


def is_promo_locked(user_id: int) -> bool:
    row = _query("SELECT locked FROM promo_locks WHERE user_id = ?", (user_id,), one=True)
    return bool(row[0]) if row else False


def get_locked_users():
    return _query("SELECT user_id, reason FROM promo_locks WHERE locked = 1")


def set_user_rank(user_id: int, rank_id: int):
    with transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO user_ranks (user_id, current_rank_id) VALUES (?, ?)",
            (user_id, rank_id),
        )


def get_user_rank(user_id: int):
    row = _query("SELECT current_rank_id FROM user_ranks WHERE user_id = ?", (user_id,), one=True)
    return row[0] if row else None


def get_all_user_ranks() -> dict:
    return dict(_query("SELECT user_id, current_rank_id FROM user_ranks"))


def clear_user_rank(user_id: int):
    with transaction() as conn:
        conn.execute("DELETE FROM user_ranks WHERE user_id = ?", (user_id,))


# ============================================================
# BOT CONFIGURATION
# ============================================================

def init_config_table():
    with transaction() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)


def set_config(key: str, value: str):
    with transaction() as conn:
        conn.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)", (key, value))


def get_config(key: str, default: str = None) -> str:
    row = _query("SELECT value FROM bot_config WHERE key = ?", (key,), one=True)
    return row[0] if row else default


# ============================================================
# STARTUP ROLES
# ============================================================

def init_startup_roles_table():
    with transaction() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS startup_roles (
                role_id INTEGER PRIMARY KEY,
                is_active INTEGER DEFAULT 1
            )
        """)


def add_startup_role(role_id: int):
    with transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO startup_roles (role_id, is_active) VALUES (?, 1)",
            (role_id,),
        )


def remove_startup_role(role_id: int):
    with transaction() as conn:
        conn.execute("DELETE FROM startup_roles WHERE role_id = ?", (role_id,))


def get_startup_roles():
    return [r[0] for r in _query("SELECT role_id FROM startup_roles WHERE is_active = 1")]


# ============================================================
# AWARDS
# ============================================================

def init_award_history_table():
    with transaction() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_awards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                np_bonus INTEGER NOT NULL DEFAULT 0,
                awarded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, role_id)
            )
        """)


def award_role_to_user(user_id: int, role_id: int, np_bonus: int = 0, actor_id=None) -> bool:
    """Returns True if newly stored, False if the user already had this award."""
    with transaction() as conn:
        conn.execute("INSERT OR IGNORE INTO users (user_id, np) VALUES (?, 0)", (user_id,))
        cur = conn.execute(
            "INSERT OR IGNORE INTO user_awards (user_id, role_id, np_bonus) VALUES (?, ?, ?)",
            (user_id, role_id, np_bonus),
        )
        success = cur.rowcount > 0

    if success and np_bonus:
        add_np(user_id, np_bonus, actor_id=actor_id, source="award", note=f"role_id={role_id}")
    return success


def get_user_awards(user_id: int):
    return _query(
        "SELECT role_id, np_bonus, awarded_at FROM user_awards "
        "WHERE user_id = ? ORDER BY awarded_at DESC",
        (user_id,),
    )


def remove_award(user_id: int, role_id: int) -> bool:
    with transaction() as conn:
        cur = conn.execute("DELETE FROM user_awards WHERE user_id = ? AND role_id = ?", (user_id, role_id))
        return cur.rowcount > 0
