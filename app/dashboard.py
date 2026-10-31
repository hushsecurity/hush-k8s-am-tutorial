import json
import logging
import os
import signal
import sys
import ssl
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

# The page, its status and its actions; and, apart, the apps' reports, so that
# exposing the page never exposes the reports.
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "8080"))
REPORT_PORT = int(os.getenv("REPORT_PORT", "8081"))
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "15"))
# A pod that missed this many reports in a row is treated as gone.
STALE_AFTER_CYCLES = int(os.getenv("STALE_AFTER_CYCLES", "3"))
MAX_REQUEST_BYTES = 64 * 1024
HUSH_ROLE_PREFIX = "_hush_"
# Names the page needs for the commands and UI values it shows (set by the chart).
TUTORIAL_CONFIG = json.loads(os.getenv("TUTORIAL_CONFIG") or "{}")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
SERVICE_ACCOUNT_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
# The page is reached through kubectl port-forward, or under the host names in
# ALLOWED_HOSTS; refusing other Host names keeps a DNS-rebound site from posting
# actions as if it were the page.
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")
EXTRA_HOSTS = tuple(
    host.strip().lower() for host in os.getenv("ALLOWED_HOSTS", "").split(",") if host.strip()
)
MAX_REPLICAS = 2


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

    def clear(self) -> None:
        """Forget everything, as a restart would: the pods report again within a cycle."""
        with self.lock:
            self.pods.clear()
            self.roles.clear()

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
                "tutorial": TUTORIAL_CONFIG,
                "pollIntervalSeconds": POLL_INTERVAL_SECONDS,
                "pods": pods,
                "roles": roles,
            }


STATE = State()


def patch_deployment(name: str, subresource: str, patch: dict, content_type: str) -> None:
    """PATCH one of the tutorial's deployments, as the dashboard's service account."""
    namespace = TUTORIAL_CONFIG["namespace"]
    url = "https://{}:{}/apis/apps/v1/namespaces/{}/deployments/{}{}".format(
        os.environ["KUBERNETES_SERVICE_HOST"],
        os.environ["KUBERNETES_SERVICE_PORT"],
        namespace,
        name,
        subresource,
    )
    # The token is projected and refreshed by the kubelet; read it every time.
    with open(os.path.join(SERVICE_ACCOUNT_DIR, "token")) as token_file:
        token = token_file.read().strip()
    request = urllib.request.Request(
        url,
        data=json.dumps(patch).encode("utf-8"),
        method="PATCH",
        headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
    )
    context = ssl.create_default_context(cafile=os.path.join(SERVICE_ACCOUNT_DIR, "ca.crt"))
    with urllib.request.urlopen(request, context=context, timeout=10):
        pass


def restart_deployment(name: str) -> None:
    """What kubectl rollout restart does: a new pod template, so new pods."""
    patch_deployment(
        name,
        "",
        {"spec": {"template": {"metadata": {"annotations": {
            "kubectl.kubernetes.io/restartedAt": now_iso()}}}}},
        "application/strategic-merge-patch+json",
    )


def start_over() -> str:
    """Bring every app back as installed, with new pods, and forget what was seen.

    New pods start on their policy's latest credential revision, so a rotation
    done before starting over is not taken for one done after.
    """
    for app in TUTORIAL_CONFIG.get("apps", []):
        replicas = app.get("replicas", 1)
        patch_deployment(
            app["deployment"],
            "/scale",
            {"spec": {"replicas": replicas}},
            "application/merge-patch+json",
        )
        if replicas:
            restart_deployment(app["deployment"])
    STATE.clear()
    return "started over"


def run_action(payload: dict) -> str:
    """Scale or restart a tutorial app, or start over, for the page's buttons."""
    if payload.get("action") == "start-over":
        return start_over()

    apps = {app["name"]: app for app in TUTORIAL_CONFIG.get("apps", [])}
    app = apps.get(payload.get("app"))
    if not app:
        raise ValueError("unknown app")
    deployment = app["deployment"]

    if payload.get("action") == "scale":
        replicas = payload.get("replicas")
        if type(replicas) is not int or not 0 <= replicas <= MAX_REPLICAS:
            raise ValueError(f"replicas must be 0 to {MAX_REPLICAS}")
        patch_deployment(
            deployment, "/scale", {"spec": {"replicas": replicas}}, "application/merge-patch+json"
        )
        return f"scaled {deployment} to {replicas}"

    if payload.get("action") == "restart":
        restart_deployment(deployment)
        return f"restarted {deployment}"

    raise ValueError("unknown action")


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


class PageHandler(Handler):
    """The page, its status and its buttons."""

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

    def _from_page(self) -> bool:
        """Whether a request could only have come from the page itself.

        Other sites the browser visits can post to localhost too. They cannot
        send a JSON body without the browser first asking permission (a CORS
        preflight), which this server never grants; the Host check stops a
        site that rebinds its own name to 127.0.0.1.
        """
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].lower()
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        return host in LOCAL_HOSTS + EXTRA_HOSTS and content_type == "application/json"

    def _action(self) -> None:
        if not self._from_page():
            where = "its usual address" if EXTRA_HOSTS else "localhost"
            self._send_json(403, {"error": f"open the page through {where} to use its buttons"})
            return
        try:
            done = run_action(self._read_json())
        except (ValueError, AttributeError) as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except urllib.error.HTTPError as exc:
            reason = "not deployed" if exc.code == 404 else f"Kubernetes API answered {exc.code}"
            self._send_json(502, {"error": reason})
            return
        except (OSError, KeyError) as exc:
            logging.warning("Action failed: %s", exc)
            self._send_json(502, {"error": "Kubernetes API unreachable"})
            return
        logging.info("Action: %s", done)
        self._send_json(200, {"ok": True})

    def do_POST(self):
        if self.path == "/action":
            self._action()
        else:
            self._send(404, b"not found", "text/plain")


class ReportHandler(Handler):
    """The apps' reports, from inside the cluster."""

    def do_GET(self):
        if self.path == "/healthz":
            self._send(200, b"ok", "text/plain")
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

    reports = ThreadingHTTPServer(("", REPORT_PORT), ReportHandler)
    threading.Thread(target=reports.serve_forever, daemon=True).start()
    page = ThreadingHTTPServer(("", LISTEN_PORT), PageHandler)
    logging.info("Dashboard listening on port %s, reports on port %s", LISTEN_PORT, REPORT_PORT)
    page.serve_forever()


if __name__ == "__main__":
    main()
