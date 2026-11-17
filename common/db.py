"""All PostgreSQL access for the app lives here.

Routes never open connections themselves. They call one of:
    fetch_one, fetch_all, execute, insert, update, paginate, transaction

Rows come back as plain dicts. Always pass values through `params`
(%s / %(name)s placeholders) – never format SQL strings with user input.
"""
import math
import os
from contextlib import contextmanager

from dotenv import load_dotenv
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

load_dotenv()

MAX_PER_PAGE = 100

pool = ConnectionPool(
    conninfo=os.environ["DATABASE_URL"],
    min_size=int(os.getenv("DB_POOL_MIN", "1")),
    max_size=int(os.getenv("DB_POOL_MAX", "10")),
    kwargs={"row_factory": dict_row},
    timeout=10,  # wait at most 10s for a free connection instead of hanging
    open=False,  # opened in main.py lifespan
)


def fetch_one(query, params=None):
    """First row as a dict, or None. Also use for INSERT/UPDATE ... RETURNING."""
    with pool.connection() as conn:
        return conn.execute(query, params).fetchone()


def fetch_all(query, params=None):
    """All rows as a list of dicts. Only for small, bounded results –
    anything a user can grow (lists, history) must go through paginate()."""
    with pool.connection() as conn:
        return conn.execute(query, params).fetchall()


def execute(query, params=None):
    """Run a statement, return the number of affected rows."""
    with pool.connection() as conn:
        return conn.execute(query, params).rowcount


def insert(table, data, conn=None):
    """INSERT one row from a dict and return the new row."""
    cols = list(data)
    query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, cols)),
        sql.SQL(", ").join(sql.Placeholder(c) for c in cols),
    )
    if conn is not None:
        return conn.execute(query, data).fetchone()
    return fetch_one(query, data)


def update(table, row_id, data, conn=None):
    """UPDATE one row by id from a dict (also bumps updated_at) and return it."""
    sets = [sql.SQL("{} = {}").format(sql.Identifier(c), sql.Placeholder(c)) for c in data]
    sets.append(sql.SQL("updated_at = now()"))
    query = sql.SQL("UPDATE {} SET {} WHERE id = %(_id)s RETURNING *").format(
        sql.Identifier(table), sql.SQL(", ").join(sets)
    )
    params = {**data, "_id": row_id}
    if conn is not None:
        return conn.execute(query, params).fetchone()
    return fetch_one(query, params)


@contextmanager
def transaction():
    """Several statements that must succeed or fail together.

        with transaction() as conn:
            f = insert("files", {...}, conn=conn)
            insert("file_services", {...}, conn=conn)

    Commits on success, rolls back on any exception.
    """
    with pool.connection() as conn:
        with conn.transaction():
            yield conn


def paginate(query, params=None, page=1, per_page=25):
    """Run a SELECT one page at a time. Every list endpoint uses this.

    `query` is a full SELECT including ORDER BY but WITHOUT LIMIT/OFFSET.
    Returns a dict the templates/pagination.html partial understands.
    """
    page = max(int(page or 1), 1)
    per_page = min(max(int(per_page or 25), 1), MAX_PER_PAGE)
    params = params or {}
    with pool.connection() as conn:
        total = conn.execute(
            f"SELECT count(*) AS n FROM ({query}) AS sub", params
        ).fetchone()["n"]
        pages = max(math.ceil(total / per_page), 1)
        page = min(page, pages)  # an old ?page=9 after filtering shows the last page, not "nothing found"
        rows = conn.execute(
            f"{query} LIMIT {per_page} OFFSET {(page - 1) * per_page}", params
        ).fetchall()
    return {
        "rows": rows,
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
        "has_prev": page > 1,
        "has_next": page < pages,
    }


def audit(user_id, action, entity, entity_id=None, details=None, conn=None):
    """Write one line to the audit log (SRS 7 – every create/edit/payment)."""
    from psycopg.types.json import Jsonb

    data = {
        "user_id": user_id,
        "action": action,
        "entity": entity,
        "entity_id": entity_id,
        "details": Jsonb(details or {}),
    }
    return insert("audit_log", data, conn=conn)


def stream(query, params=None, chunk=1000):
    """Yield rows one by one through a server-side cursor – for CSV exports of any size
    without loading everything in memory (prompt 09). The connection is held until the generator ends."""
    with pool.connection() as conn:
        with conn.transaction():
            with conn.cursor(name="export_cursor") as cur:
                cur.itersize = chunk
                cur.execute(query, params)
                for row in cur:
                    yield row
