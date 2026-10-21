import hashlib
import logging
import os
import time
from functools import partial

import psycopg2
from psycopg2 import sql

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "127.0.0.1")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB", "tutorial")
POSTGRES_USERNAME = os.getenv("POSTGRES_USERNAME")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "15"))
SECRET_REVEAL_CHARS = int(os.getenv("SECRET_REVEAL_CHARS", "4"))
CONNECT_TIMEOUT_SECONDS = int(os.getenv("CONNECT_TIMEOUT_SECONDS", "5"))
TABLES = ("invoices",)


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def redact(value: str) -> str:
    """Leading characters plus a fingerprint: enough to see a rotation."""
    if not value:
        return "<empty>"

    shown = value[:SECRET_REVEAL_CHARS]
    hidden = "*" * max(len(value) - SECRET_REVEAL_CHARS, 0)
    return f"{shown}{hidden} (len={len(value)} sha256={fingerprint(value)})"


def log_injected_credentials() -> None:
    logging.info(
        "Injected postgres credential: username=%s password=%s",
        POSTGRES_USERNAME,
        redact(POSTGRES_PASSWORD),
    )


def validate_required_env() -> None:
    if POSTGRES_USERNAME and POSTGRES_PASSWORD:
        return

    logging.error(
        "Missing injected postgres credentials (POSTGRES_USERNAME / POSTGRES_PASSWORD). "
        "Hush AccessPolicy may not have been reconciled before this pod was admitted. "
        "Delete this pod so it is re-created after the policy is active."
    )
    raise SystemExit(1)


def read_table(cursor, table: str) -> str:
    ident = sql.Identifier(table)

    cursor.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(ident))
    rows = cursor.fetchone()[0]

    cursor.execute(
        sql.SQL(
            "SELECT ref, to_char(written_at, 'HH24:MI:SS') FROM {} "
            "ORDER BY written_at DESC LIMIT 1"
        ).format(ident)
    )
    latest = cursor.fetchone()

    if latest is None:
        return f"rows={rows}"

    return f"rows={rows} latest={latest[0]}@{latest[1]}"


def write_table(cursor, table: str, ref: str) -> str:
    cursor.execute(
        sql.SQL("INSERT INTO {} (ref) VALUES (%s)").format(sql.Identifier(table)),
        (ref,),
    )
    return f"ref={ref}"


def run_operation(conn, operation: str, table: str, work) -> bool:
    try:
        with conn.cursor() as cursor:
            detail = work(cursor)
        conn.commit()
    except psycopg2.Error as exc:
        conn.rollback()
        logging.error("%s %s: FAILED %s", operation, table, str(exc).strip())
        return False

    logging.info("%s %s: OK %s", operation, table, detail)
    return True


def exercise_tables() -> None:
    conn = psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        dbname=POSTGRES_DB,
        user=POSTGRES_USERNAME,
        password=POSTGRES_PASSWORD,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
    )

    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT current_user")
            db_user = cursor.fetchone()[0]

        logging.info("Connected to %s as %s", POSTGRES_DB, db_user)

        outcomes = []
        for table in TABLES:
            outcomes.append(
                run_operation(conn, "read", table, partial(read_table, table=table))
            )
            outcomes.append(
                run_operation(
                    conn,
                    "write",
                    table,
                    partial(write_table, table=table, ref=db_user),
                )
            )

        logging.info(
            "Cycle done: %s/%s operations succeeded",
            sum(outcomes),
            len(outcomes),
        )
    finally:
        conn.close()


def main() -> None:
    validate_required_env()

    while True:
        # Every cycle, so tailing the log shows the credential in use.
        log_injected_credentials()

        try:
            exercise_tables()
        except Exception as exc:
            logging.error("DB connection failed: %s", exc)

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
