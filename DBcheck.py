#!/usr/bin/env python3
import argparse
import os
import sqlite3
import subprocess
import sys

# --- try to reuse the same DB path logic as your bot ---
def get_db_path():
    try:
        import database  # your existing module
        return database.DB_FILE
    except Exception:
        # fallback: assume DB next to database.py
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(here, "nexus.db")

def get_log_path():
    # matches bot.py: logs/nexus.log relative to bot folder
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "logs", "nexus.log")

def connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def print_rows(rows, columns=None, max_width=36):
    rows = list(rows)
    if not rows:
        print("(no rows)")
        return

    if columns is None:
        columns = rows[0].keys()

    cols = list(columns)
    # header
    print(" | ".join([c.ljust(max_width)[:max_width] for c in cols]))
    print("-" * (sum(min(max_width, len(c)) for c in cols) + 3 * (len(cols) - 1)))

    for r in rows:
        line = []
        for c in cols:
            v = r[c]
            s = str(v)
            if len(s) > max_width:
                s = s[: max_width - 3] + "..."
            line.append(s.ljust(max_width)[:max_width])
        print(" | ".join(line))

def cmd_db_file(_args):
    print(get_db_path())

def cmd_tables(_args):
    db = get_db_path()
    conn = connect(db)
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    print_rows(cur.fetchall(), columns=["name"])

def cmd_list_ranks(_args):
    db = get_db_path()
    conn = connect(db)
    cur = conn.execute("""
        SELECT rank_id, rank_name, np_threshold, role_id, obtainable, position
        FROM ranks
        ORDER BY np_threshold ASC;
    """)
    print_rows(cur.fetchall())

def cmd_user(_args):
    db = get_db_path()
    conn = connect(db)
    uid = int(_args.user_id)

    # users
    cur1 = conn.execute("SELECT user_id, np FROM users WHERE user_id=?", (uid,))
    print("\n[users]")
    print_rows(cur1.fetchall())

    # rank
    cur2 = conn.execute("SELECT user_id, current_rank_id FROM user_ranks WHERE user_id=?", (uid,))
    print("\n[user_ranks]")
    print_rows(cur2.fetchall())

    # lock
    cur3 = conn.execute("SELECT user_id, locked, reason FROM promo_locks WHERE user_id=?", (uid,))
    print("\n[promo_locks]")
    print_rows(cur3.fetchall())

    # np log
    cur4 = conn.execute("""
        SELECT id, old_np, new_np, actor_id, source, note, at
        FROM np_log
        WHERE user_id=?
        ORDER BY at DESC, id DESC
        LIMIT ?
    """, (uid, int(_args.limit)))
    print(f"\n[np_log last {int(_args.limit)}]")
    print_rows(cur4.fetchall())

def cmd_locked_users(_args):
    db = get_db_path()
    conn = connect(db)
    cur = conn.execute("""
        SELECT user_id, locked, reason
        FROM promo_locks
        WHERE locked=1
        ORDER BY user_id;
    """)
    print_rows(cur.fetchall())

def cmd_leaderboard(_args):
    db = get_db_path()
    conn = connect(db)
    lim = int(_args.limit)
    cur = conn.execute("""
        SELECT user_id, np
        FROM users
        ORDER BY np DESC
        LIMIT ?;
    """, (lim,))
    print_rows(cur.fetchall())

def cmd_tail_logs(_args):
    path = get_log_path()
    n = int(_args.lines)

    if not os.path.exists(path):
        print(f"Log file not found: {path}")
        sys.exit(1)

    # tail -n is simplest
    subprocess.run(["tail", "-n", str(n), path], check=False)

def cmd_journal(_args):
    # requires service name
    svc = _args.service
    n = int(_args.lines)

    subprocess.run(
        ["sudo", "journalctl", "-u", svc, "-n", str(n), "--no-pager"],
        check=False
    )

def main():
    p = argparse.ArgumentParser(description="DB + log helper for Nexus bot")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("db-file", help="Print DB file path the bot uses")
    sp.set_defaults(func=cmd_db_file)

    sp = sub.add_parser("tables", help="List sqlite tables")
    sp.set_defaults(func=cmd_tables)

    sp = sub.add_parser("list-ranks", help="Show ranks configured for auto/manual")
    sp.set_defaults(func=cmd_list_ranks)

    sp = sub.add_parser("user", help="Show DB info for a specific Discord user_id")
    sp.add_argument("user_id", type=int)
    sp.add_argument("--limit", type=int, default=20, help="NP log lines limit")
    sp.set_defaults(func=cmd_user)

    sp = sub.add_parser("locked-users", help="List promo-locked users")
    sp.set_defaults(func=cmd_locked_users)

    sp = sub.add_parser("leaderboard", help="Top users by NP")
    sp.add_argument("--limit", type=int, default=10)
    sp.set_defaults(func=cmd_leaderboard)

    sp = sub.add_parser("tail-logs", help="Tail logs/nexus.log")
    sp.add_argument("--lines", type=int, default=100)
    sp.set_defaults(func=cmd_tail_logs)

    sp = sub.add_parser("journal", help="Show journalctl logs for a systemd service")
    sp.add_argument("service", help="systemd unit name")
    sp.add_argument("--lines", type=int, default=200)
    sp.set_defaults(func=cmd_journal)

    args = p.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()