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
    """Add a new rank milestone."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO ranks (rank_name, np_threshold, role_id, position) VALUES (?, ?, ?, ?)",
        (rank_name, np_threshold, role_id, position)
    )
    conn.commit()
    conn.close()

def get_ranks():
    """Get all ranks ordered by NP threshold."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT rank_id, rank_name, np_threshold, role_id FROM ranks ORDER BY np_threshold ASC")
    result = c.fetchall()
    conn.close()
    return result

def get_appropriate_rank(np_amount: int):
    """Get the highest rank the user qualifies for based on their NP."""
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

# ============================================================
# AWARDS SYSTEM
# ============================================================

def init_awards_table():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS awards (
            award_id INTEGER PRIMARY KEY AUTOINCREMENT,
            award_name TEXT NOT NULL UNIQUE,
            award_emoji TEXT,
            description TEXT
        )
    """)
    conn.commit()
    conn.close()

def init_user_awards_table():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_awards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            award_id INTEGER NOT NULL,
            earned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, award_id),
            FOREIGN KEY(award_id) REFERENCES awards(award_id)
        )
    """)
    conn.commit()
    conn.close()

def add_award_type(award_name: str, emoji: str = "🏆", description: str = ""):
    """Create a new award type."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO awards (award_name, award_emoji, description) VALUES (?, ?, ?)",
        (award_name, emoji, description)
    )
    conn.commit()
    conn.close()

def award_to_user(user_id: int, award_name: str) -> bool:
    """Give an award to a user. Returns True if awarded, False if already has it."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT award_id FROM awards WHERE award_name = ?", (award_name,))
    result = c.fetchone()
    
    if not result:
        conn.close()
        return False
    
    award_id = result[0]
    c.execute(
        "INSERT OR IGNORE INTO user_awards (user_id, award_id) VALUES (?, ?)",
        (user_id, award_id)
    )
    success = c.rowcount > 0
    conn.commit()
    conn.close()
    return success

def get_user_awards(user_id: int):
    """Get all awards for a user."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """SELECT a.award_id, a.award_name, a.award_emoji, a.description 
           FROM user_awards ua 
           JOIN awards a ON ua.award_id = a.award_id 
           WHERE ua.user_id = ? 
           ORDER BY ua.earned_at DESC""",
        (user_id,)
    )
    result = c.fetchall()
    conn.close()
    return result

def get_all_awards():
    """Get all award types."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT award_id, award_name, award_emoji, description FROM awards")
    result = c.fetchall()
    conn.close()
    return result

# ============================================================
# INITIAL ROLES
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
    """Add a role to be given on member join."""
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
    """Get all roles to assign on member join."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT role_id FROM startup_roles WHERE is_active = 1")
    result = c.fetchall()
    conn.close()
    return [r[0] for r in result]