import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

LISTEN_PORT = int(os.getenv("LISTEN_PORT", "8080"))
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "15"))
# A pod that missed this many reports in a row is treated as gone.
STALE_AFTER_CYCLES = int(os.getenv("STALE_AFTER_CYCLES", "3"))
MAX_REQUEST_BYTES = 64 * 1024
HUSH_ROLE_PREFIX = "_hush_"
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_hush_role(role: str) -> dict:
    """Hush names the roles it mints _hush_<policy id>_<revision>."""
    policy_id, _, revision = role[len(HUSH_ROLE_PREFIX):].rpartition("_")
    return {
        "policyId": policy_id or None,
        "revision": int(revision) if revision.isdigit() else None,
    }


class State:
    """What the apps last reported, and the Hush roles seen in Postgres.

    Held in memory only: a dashboard restart starts empty and refills within
    one report cycle, forgetting roles already removed.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.pods = {}
        self.roles = {}

    def _expire(self) -> None:
        deadline = time.monotonic() - STALE_AFTER_CYCLES * POLL_INTERVAL_SECONDS
        for pod, entry in list(self.pods.items()):
            if entry["lastSeen"] < deadline:
                del self.pods[pod]

    def _track_roles(self, hush_roles: list) -> None:
        current = set(hush_roles)
        for role in current:
            self.roles[role] = {"retiredAt": None}
        # A role gone from pg_roles was removed by Hush; keep it, marked as such.
        for role, info in self.roles.items():
            if role not in current and not info["retiredAt"]:
                info["retiredAt"] = now_iso()

    def report(self, report: dict) -> None:
        pod = report.get("pod")
        if not pod:
            raise ValueError("report without pod")

        with self.lock:
            self.pods[pod] = {"report": report, "lastSeen": time.monotonic()}
            if report.get("hushRoles") is not None:
                self._track_roles(report["hushRoles"])

    def goodbye(self, pod: str) -> None:
        with self.lock:
            self.pods.pop(pod, None)

    def snapshot(self) -> dict:
        with self.lock:
            self._expire()
            monotonic_now = time.monotonic()

            pods = []
            in_use = {}
            for pod, entry in sorted(
                self.pods.items(), key=lambda item: (item[1]["report"].get("app") or "", item[0])
            ):
                report = entry["report"]
                pods.append(
                    {**report, "secondsSinceReport": round(monotonic_now - entry["lastSeen"], 1)}
                )
                username = (report.get("credential") or {}).get("username")
                if username:
                    in_use.setdefault(username, []).append(
                        {"pod": pod, "app": report.get("app")}
                    )

            roles = [
                {"name": role, **parse_hush_role(role), **info, "inUseBy": in_use.get(role, [])}
                for role, info in sorted(self.roles.items())
            ]

            return {
                "now": now_iso(),
                "pollIntervalSeconds": POLL_INTERVAL_SECONDS,
                "pods": pods,
                "roles": roles,
            }


STATE = State()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        # Every app reports every few seconds; access logs would drown the rest.
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("missing or oversized body")
        return json.loads(self.rfile.read(length))

    def do_GET(self):
        if self.path == "/status":
            self._send_json(200, STATE.snapshot())
        elif self.path == "/healthz":
            self._send(200, b"ok", "text/plain")
        elif self.path in ("/", "/index.html"):
            with open(os.path.join(STATIC_DIR, "index.html"), "rb") as page:
                self._send(200, page.read(), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        try:
            payload = self._read_json()
            if self.path == "/report":
                STATE.report(payload)
            elif self.path == "/goodbye":
                STATE.goodbye(payload.get("pod"))
            else:
                self._send(404, b"not found", "text/plain")
                return
        except (ValueError, AttributeError) as exc:
            self._send_json(400, {"error": str(exc)})
            return

        self._send_json(200, {"ok": True})


def main() -> None:
    # As PID 1 the process ignores SIGTERM unless it installs a handler.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    server = ThreadingHTTPServer(("", LISTEN_PORT), Handler)
    logging.info("Dashboard listening on port %s", LISTEN_PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
