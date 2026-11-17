"""Database migrations + first owner login.

    python migrate.py                 # apply new files in migrations/ (in name order)
    python migrate.py create-owner    # create the owner login (asks for details)

Each .sql file runs once inside a transaction and is recorded in schema_migrations.
Never edit a migration that has already run on the server – add a new numbered file.
"""
import getpass
import sys
from pathlib import Path

from common.auth import hash_password
from common.db import fetch_all, insert, pool, transaction


def apply_migrations():
    with pool.connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
    done = {r["name"] for r in fetch_all("SELECT name FROM schema_migrations")}
    pending = [f for f in sorted(Path("migrations").glob("*.sql")) if f.name not in done]
    if not pending:
        print("Database is up to date.")
        return
    for f in pending:
        with transaction() as conn:
            conn.execute(f.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (f.name,))
        print(f"applied {f.name}")


def create_owner():
    name = input("Owner full name: ").strip()
    username = input("Username: ").strip()
    mobile = input("Mobile: ").strip()
    password = getpass.getpass("Password (min 8): ")
    if len(password) < 8:
        sys.exit("Password too short.")
    insert("users", {
        "name": name, "username": username, "mobile": mobile,
        "password_hash": hash_password(password), "role": "owner",
    })
    print(f"Owner '{username}' created.")


if __name__ == "__main__":
    pool.open(wait=True)
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "create-owner":
            create_owner()
        else:
            apply_migrations()
    finally:
        pool.close()
