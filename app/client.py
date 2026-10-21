import hashlib
import json
import logging
import os
import signal
import threading
import urllib.request
from datetime import datetime, timezone
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
POD_NAMESPACE = os.getenv("POD_NAMESPACE", "<unknown>")
POD_NAME = os.getenv("POD_NAME", "<unknown>")
SERVICE_ACCOUNT = os.getenv("SERVICE_ACCOUNT", "<unknown>")
APP_NAME = os.getenv("APP_NAME", "<unknown>")
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "").rstrip("/")
TABLES = ("invoices",)
HUSH_ROLE_PATTERN = r"\_hush\_%"


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


def credentials_injected() -> bool:
    return bool(POSTGRES_USERNAME and POSTGRES_PASSWORD)


def log_missing_credentials() -> None:
    # Credentials are bound at admission, so a container restart cannot fix this:
    # the pod has to be re-created once a matching policy exists.
    logging.warning(
        "No postgres credential injected: no Hush AccessPolicy matched this "
        "workload when the pod was created (ns=%s sa=%s)",
        POD_NAMESPACE,
        SERVICE_ACCOUNT,
    )


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


def run_operation(conn, operation: str, table: str, work) -> dict:
    try:
        with conn.cursor() as cursor:
            detail = work(cursor)
        conn.commit()
    except psycopg2.Error as exc:
        conn.rollback()
        detail = str(exc).strip()
        logging.error("%s %s: FAILED %s", operation, table, detail)
        return {"operation": operation, "table": table, "ok": False, "detail": detail}

    logging.info("%s %s: OK %s", operation, table, detail)
    return {"operation": operation, "table": table, "ok": True, "detail": detail}


def list_hush_roles(conn) -> list:
    # pg_roles is readable by any role, so this needs no extra grant.
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT rolname FROM pg_roles WHERE rolname LIKE %s ORDER BY rolname",
            (HUSH_ROLE_PATTERN,),
        )
        roles = [row[0] for row in cursor.fetchall()]
    conn.commit()
    return roles


def exercise_tables(result: dict) -> None:
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
        result["connectedAs"] = db_user

        operations = result["operations"]
        for table in TABLES:
            operations.append(
                run_operation(conn, "read", table, partial(read_table, table=table))
            )
            operations.append(
                run_operation(
                    conn,
                    "write",
                    table,
                    partial(write_table, table=table, ref=db_user),
                )
            )

        logging.info(
            "Cycle done: %s/%s operations succeeded",
            sum(op["ok"] for op in operations),
            len(operations),
        )

        result["hushRoles"] = list_hush_roles(conn)
    finally:
        conn.close()


def run_cycle() -> dict:
    result = {
        "credential": None,
        "connectedAs": None,
        "operations": [],
        "hushRoles": None,
        "error": None,
    }

    # Every cycle, so tailing the log shows the credential in use.
    if credentials_injected():
        log_injected_credentials()
        result["credential"] = {
            "username": POSTGRES_USERNAME,
            "passwordPreview": POSTGRES_PASSWORD[:SECRET_REVEAL_CHARS],
            "passwordLength": len(POSTGRES_PASSWORD),
            "fingerprint": fingerprint(POSTGRES_PASSWORD),
        }
    else:
        log_missing_credentials()

    # Without credentials this still tries, so the log shows Postgres refusing.
    try:
        exercise_tables(result)
    except Exception as exc:
        result["error"] = str(exc).strip()
        logging.error("DB connection failed: %s", result["error"])

    return result


class DashboardReporter:
    """Pushes each cycle's outcome to the dashboard; never fails the cycle."""

    def __init__(self, url: str):
        self.url = url
        self.failing = False

    def _post(self, path: str, payload: dict) -> None:
        if not self.url:
            return

        request = urllib.request.Request(
            f"{self.url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=2):
                pass
        except Exception as exc:
            # Once per outage, so a missing dashboard does not flood the log.
            if not self.failing:
                logging.warning("Dashboard unreachable at %s: %s", self.url, exc)
            self.failing = True
            return

        if self.failing:
            logging.info("Dashboard reachable again")
        self.failing = False

    def report(self, result: dict) -> None:
        delivered = [
            name
            for name in ("POSTGRES_USERNAME", "POSTGRES_PASSWORD")
            if os.getenv(name)
        ]
        self._post(
            "/report",
            {
                "pod": POD_NAME,
                "app": APP_NAME,
                "namespace": POD_NAMESPACE,
                "serviceAccount": SERVICE_ACCOUNT,
                "deliveredEnv": delivered,
                "pollIntervalSeconds": POLL_INTERVAL_SECONDS,
                "reportedAt": datetime.now(timezone.utc).isoformat(),
                **result,
            },
        )

    def goodbye(self) -> None:
        self._post("/goodbye", {"pod": POD_NAME})


def main() -> None:
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())

    reporter = DashboardReporter(DASHBOARD_URL)

    while not stop.is_set():
        reporter.report(run_cycle())
        stop.wait(POLL_INTERVAL_SECONDS)

    # Lets the dashboard drop this pod at once instead of waiting for it to go stale.
    reporter.goodbye()
    logging.info("Stopping")


if __name__ == "__main__":
    main()
