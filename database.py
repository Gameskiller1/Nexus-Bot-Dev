import sqlite3
import re

DB_FILE = "nexus.db"

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
    # prevent going negative
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