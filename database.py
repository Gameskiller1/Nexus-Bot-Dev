import sqlite3
import re

DB_FILE = "nexus.db"

# ============================================================
# CORE USER SYSTEM
# ============================================================

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            np INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def ensure_user(user_id: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, np) VALUES (?, 0)", (user_id,))
    conn.commit()
    conn.close()

def add_np(user_id: int, amount: int):
    ensure_user(user_id)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET np = np + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()

def remove_np(user_id: int, amount: int):
    ensure_user(user_id)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET np = MAX(np - ?, 0) WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()

def get_np(user_id: int) -> int:
    ensure_user(user_id)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT np FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else 0

def get_leaderboard(limit: int = 10):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id, np FROM users ORDER BY np DESC LIMIT ?", (limit,))
    result = c.fetchall()
    conn.close()
    return result

# ============================================================
# ROLE TAGS
# ============================================================

def init_role_tags_table():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS role_tags (
            role_id INTEGER PRIMARY KEY,
            tag TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def set_role_tag(role_id: int, tag: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO role_tags (role_id, tag) VALUES (?, ?)", (role_id, tag))
    conn.commit()
    conn.close()

def remove_role_tag(role_id: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM role_tags WHERE role_id = ?", (role_id,))
    conn.commit()
    conn.close()

def get_role_tag(role_id: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT tag FROM role_tags WHERE role_id = ?", (role_id,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else None

def get_all_role_tags():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT role_id, tag FROM role_tags")
    result = c.fetchall()
    conn.close()
    return result

# ============================================================
# RANKING SYSTEM
# ============================================================

def init_ranking_table():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS ranks (
            rank_id INTEGER PRIMARY KEY AUTOINCREMENT,
            rank_name TEXT NOT NULL UNIQUE,
            np_threshold INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            position INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def add_rank(rank_name: str, np_threshold: int, role_id: int, position: int = 0):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO ranks (rank_name, np_threshold, role_id, position) VALUES (?, ?, ?, ?)",
        (rank_name, np_threshold, role_id, position)
    )
    conn.commit()
    conn.close()

def get_ranks():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT rank_id, rank_name, np_threshold, role_id FROM ranks ORDER BY np_threshold ASC")
    result = c.fetchall()
    conn.close()
    return result

def get_appropriate_rank(np_amount: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "SELECT rank_id, rank_name, role_id FROM ranks WHERE np_threshold <= ? ORDER BY np_threshold DESC LIMIT 1",
        (np_amount,)
    )
    result = c.fetchone()
    conn.close()
    return result

def init_user_ranks_table():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_ranks (
            user_id INTEGER PRIMARY KEY,
            current_rank_id INTEGER,
            FOREIGN KEY(current_rank_id) REFERENCES ranks(rank_id)
        )
    """)
    conn.commit()
    conn.close()

def set_user_rank(user_id: int, rank_id: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO user_ranks (user_id, current_rank_id) VALUES (?, ?)", (user_id, rank_id))
    conn.commit()
    conn.close()

def get_user_rank(user_id: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT current_rank_id FROM user_ranks WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else None

def delete_rank(rank_id: int):
    """Delete a rank by ID."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM ranks WHERE rank_id = ?", (rank_id,))
    conn.commit()
    conn.close()
# ============================================================
# STARTUP ROLES
# ============================================================

def init_startup_roles_table():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS startup_roles (
            role_id INTEGER PRIMARY KEY,
            is_active INTEGER DEFAULT 1
        )
    """)
    conn.commit()
    conn.close()

def add_startup_role(role_id: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO startup_roles (role_id, is_active) VALUES (?, 1)", (role_id,))
    conn.commit()
    conn.close()

def remove_startup_role(role_id: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM startup_roles WHERE role_id = ?", (role_id,))
    conn.commit()
    conn.close()

def get_startup_roles():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT role_id FROM startup_roles WHERE is_active = 1")
    result = c.fetchall()
    conn.close()
    return [r[0] for r in result]

# ============================================================
# AWARDS = DISCORD ROLES + OPTIONAL NP, WITH HISTORY
# ============================================================

def init_award_history_table():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_awards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            np_bonus INTEGER NOT NULL DEFAULT 0,
            awarded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, role_id)
        )
    """)
    conn.commit()
    conn.close()

def award_role_to_user(user_id: int, role_id: int, np_bonus: int = 0) -> bool:
    """
    Store award history.
    Returns True if newly stored, False if user already has this award record.
    """
    ensure_user(user_id)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO user_awards (user_id, role_id, np_bonus) VALUES (?, ?, ?)",
        (user_id, role_id, np_bonus)
    )
    success = c.rowcount > 0
    conn.commit()
    conn.close()
    if success and np_bonus:
        add_np(user_id, np_bonus)
    return success

def get_user_awards(user_id: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        SELECT role_id, np_bonus, awarded_at
        FROM user_awards
        WHERE user_id = ?
        ORDER BY awarded_at DESC
    """, (user_id,))
    result = c.fetchall()
    conn.close()
    return result