import sqlite3
import os
from datetime import datetime

DB_FILE = "nexus.db"
BACKUP_FILE = f"nexus_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"

def migrate():
    """Migrate from old awards system to new role-based awards system."""
    if not os.path.exists(DB_FILE):
        print(f"Database {DB_FILE} not found!")
        return
    
    # Backup the database
    print(f"Creating backup: {BACKUP_FILE}")
    os.system(f"cp {DB_FILE} {BACKUP_FILE}")
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # Check if old user_awards table exists and has award_id column
    c.execute("PRAGMA table_info(user_awards)")
    columns = [col[1] for col in c.fetchall()]
    
    if 'award_id' in columns and 'role_id' not in columns:
        print("Migrating user_awards table...")
        
        # Rename old table
        c.execute("ALTER TABLE user_awards RENAME TO user_awards_old")
        
        # Create new user_awards table with correct schema
        c.execute("""
            CREATE TABLE user_awards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                np_bonus INTEGER NOT NULL DEFAULT 0,
                awarded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, role_id)
            )
        """)
        
        conn.commit()
        print("✅ user_awards table migrated successfully!")
        print("   Old award assignments were cleared (switching to role-based system)")
        print(f"   Backup saved to: {BACKUP_FILE}")
    else:
        print("Database schema is already up to date!")
    
    # Check if old awards table exists and drop it
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='awards'")
    if c.fetchone():
        print("Dropping old awards catalog table...")
        c.execute("DROP TABLE IF EXISTS awards")
        conn.commit()
        print("✅ Old awards catalog removed")
    
    # Drop old user_awards_old if it exists
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_awards_old'")
    if c.fetchone():
        c.execute("DROP TABLE IF EXISTS user_awards_old")
        conn.commit()
        print("✅ Old user_awards backup dropped")
    
    conn.close()
    print("\n✅ Migration complete!")

if __name__ == "__main__":
    migrate()