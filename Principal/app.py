import json
import os
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from catalog_data import get_catalog
from hub_db import HubDB
from hub_installer import AutoInstaller
from hub_paths import (
    ASSETS,
    BASE,
    LOGS,
    POWERSHELL,
    ROOT,
    portable_environment,
    safe_tool_id,
    within,
)
from hub_process import ProcessJob
from hub_system import SystemMonitor


MAX_LOG_BYTES = 2 * 1024 * 1024
BANNER_DIR = ASSETS / "banners"
BANNER_DIR.mkdir(parents=True, exist_ok=True)
ENV = portable_environment()
DB = HubDB()
MONITOR = SystemMonitor()
CATALOG = get_catalog()

state_lock = threading.RLock()
processes = {}
process_jobs = {}
stopping_tools = set()
states = {}
TOOLS = {}
OVERRIDES = DB.get_overrides()
banner_queue = queue.Queue()
banner_pending = set()
banner_failures = {}
banner_lock = threading.Lock()


def log_path(tool_id):
    safe = "".join(character if character.isalnum() or character in "-_" else "_" for character in tool_id)
    return LOGS / f"{safe}.log"


def rotate_log(path):
    try:
        if not path.is_file() or path.stat().st_size < MAX_LOG_BYTES:
            return
        archive = path.with_suffix(".log.1")
        if archive.exists():
            archive.unlink()
        path.replace(archive)
    except OSError:
        pass


def append_log(tool_id, message):
    path = log_path(tool_id)
    rotate_log(path)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with path.open("a", encoding="utf-8", errors="replace") as stream:
        for line in str(message).splitlines() or [""]:
            stream.write(f"[{stamp}] {line}\n")


def enqueue_banner(tool_id):
    with banner_lock:
        if tool_id in banner_pending or time.monotonic() < banner_failures.get(tool_id, 0):
            return
        banner_pending.add(tool_id)
    banner_queue.put(tool_id)


def banner_worker():
    allowed_types = {"image/png", "image/jpeg", "image/webp"}
    while True:
        tool_id = banner_queue.get()
        try:
            source = str(TOOLS.get(tool_id, {}).get("banner") or "")
            parsed = urlparse(source)
            if parsed.scheme != "https" or parsed.hostname != "opengraph.githubassets.com":
                continue
            request = urllib.request.Request(source, headers={"User-Agent": "AICorte/2.0"})
            with urllib.request.urlopen(request, timeout=20) as response:
                content_type = response.headers.get_content_type()
                if content_type not in allowed_types:
                    continue
                body = response.read(3 * 1024 * 1024 + 1)
                if len(body) > 3 * 1024 * 1024:
                    continue
            target = BANNER_DIR / f"{safe_tool_id(tool_id)}.banner"
            metadata = target.with_suffix(".json")
            temporary = target.with_suffix(".tmp")
            temporary.write_bytes(body)
            temporary.replace(target)
            metadata.write_text(json.dumps({"content_type": content_type}), encoding="utf-8")
            with banner_lock:
                banner_failures.pop(tool_id, None)
        except (OSError, ValueError, urllib.error.URLError):
            with banner_lock:
                banner_failures[tool_id] = time.monotonic() + 3600
        finally:
            with banner_lock:
                banner_pending.discard(tool_id)
            banner_queue.task_done()


def effective_availability(tool):
    return OVERRIDES.get(tool["id"], tool.get("availability", "available"))


def initial_state(tool):
    availability = effective_availability(tool)
    has_start = bool(tool.get("start"))
    status = ("stopped" if has_start else "downloaded") if availability == "installed" else availability
    messages = {
        "installed": "Desligado",
        "available": "Disponível para instalação",
        "source": "Código presente; instalação pendente",
        "blocked": "Requer ambiente externo ou receita específica",
        "catalog": "Item informativo",
        "downloaded": "Código oficial baixado",
    }
    return {
        **tool,
        "availability": availability,
        "status": status,
        "progress": 0,
        "message": messages.get(status, messages.get(availability, availability)),
        "log": str(log_path(tool["id"])),
        "can_start": availability == "installed" and has_start,
        "can_install": availability in {"available", "source"} and bool(tool.get("install_managed")),
        "can_update": availability == "installed" and bool(tool.get("install_managed")),
        "can_remove": availability == "installed" and bool(tool.get("install_managed")),
        "pid": None,
        "resource": {},
    }


def register_definition(tool):
    with state_lock:
        TOOLS[tool["id"]] = tool
        previous = states.get(tool["id"], {})
        current = initial_state(tool)
        if previous.get("status") in {"starting", "running", "stopping"}:
            current.update(
                {
                    "status": previous["status"],
                    "progress": previous.get("progress", 0),
                    "message": previous.get("message", ""),
                    "pid": previous.get("pid"),
                }
            )
        states[tool["id"]] = current


def register_installer_definition(tool):
    OVERRIDES[tool["id"]] = tool.get("availability", "installed")
    register_definition(tool)


for catalog_tool in CATALOG["tools"]:
    register_definition(catalog_tool)


def set_state(tool_id, **updates):
    with state_lock:
        if tool_id in states:
            states[tool_id].update(updates)


def running_count():
    with state_lock:
        return sum(1 for state in states.values() if state["status"] in {"starting", "running", "stopping"})


def max_running_limit():
    try:
        return max(0, int(DB.get_settings().get("max_running_apps", 0)))
    except (TypeError, ValueError):
        return 0


def url_ready(url, timeout=3):
    if not url:
        return False
    try:
        parsed = urlparse(url)
        if parsed.hostname not in {"127.0.0.1", "localhost"}:
            return False
        request = urllib.request.Request(url, headers={"User-Agent": "AICorte/2.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 500
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


def ps_command(command):
    return [str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command]


def run_step(tool_id, cwd, command, label, timeout=1800):
    if not command:
        return 0
    append_log(tool_id, f"[{label}] $ {command}")
    try:
        process = subprocess.Popen(
            ps_command(command),
            cwd=str(cwd),
            env=ENV,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        started = time.monotonic()
        for line in process.stdout or []:
            append_log(tool_id, f"[{label}] {line.rstrip()}")
            if time.monotonic() - started > timeout:
                subprocess.run(
                    ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                append_log(tool_id, f"[{label}] timeout={timeout}")
                return 124
        code = process.wait()
        append_log(tool_id, f"[{label}] exit={code}")
        return code
    except OSError as error:
        append_log(tool_id, f"[{label}] erro: {error}")
        return 1


def apply_memory_limit(tool_id, tool):
    try:
        limit_gb = float(DB.get_settings().get("max_ram_gb", 0) or 0)
    except (TypeError, ValueError):
        limit_gb = 0
    project = tool.get("docker_project")
    docker = BASE / "app" / "runtime" / "docker" / "docker.exe"
    if limit_gb <= 0 or not project or not docker.is_file():
        return
    result = subprocess.run(
        [str(docker), "ps", "-q", "--filter", f"label=com.docker.compose.project={project}"],
        cwd=str(BASE), env=ENV, capture_output=True, text=True, encoding="utf-8",
        errors="replace", creationflags=subprocess.CREATE_NO_WINDOW, check=False,
    )
    ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not ids:
        return
    limit_bytes = max(6 * 1024**2, int(limit_gb * 1024**3))
    update = subprocess.run(
        [str(docker), "update", "--memory", str(limit_bytes), "--memory-swap", str(limit_bytes), *ids],
        cwd=str(BASE), env=ENV, capture_output=True, text=True, encoding="utf-8",
        errors="replace", creationflags=subprocess.CREATE_NO_WINDOW, check=False,
    )
    if update.returncode:
        append_log(tool_id, f"[memory-limit] {update.stderr.strip()}")


def start_tool(tool_id):
    tool = TOOLS[tool_id]
    availability = states[tool_id]["availability"]
    if availability != "installed" or not tool.get("start"):
        set_state(tool_id, status=availability, progress=0, message="Aplicativo ainda não está executável")
        return
    with state_lock:
        if states[tool_id]["status"] in {"starting", "running", "stopping"}:
            return
        active = sum(
            1
            for state in states.values()
            if state["status"] in {"starting", "running", "stopping"}
        )
        limit = max_running_limit()
        if limit and active >= limit:
            states[tool_id].update(
                status="stopped",
                progress=0,
                message=f"Limite de {limit} aplicativos ligados atingido",
            )
            return
        states[tool_id].update(status="starting", progress=2, message="Reservando inicialização")

    try:
        cwd = within(BASE, tool.get("path") or BASE, allow_root=True)
        if not cwd.exists():
            set_state(tool_id, status="error", progress=100, message=f"Pasta ausente: {cwd}")
            return
        with state_lock:
            stopping_tools.discard(tool_id)
        set_state(tool_id, status="starting", progress=7, message="Validando ambiente")
        append_log(tool_id, "Inicialização solicitada")
        DB.add_event(tool_id, "info", "tool.starting", "Inicialização solicitada")
        if tool.get("prepare"):
            set_state(tool_id, progress=20, message="Validando dependências")
            code = run_step(tool_id, cwd, tool["prepare"], "prepare")
            if code:
                set_state(tool_id, status="error", progress=100, message=f"Validação falhou, exit={code}")
                DB.runtime_event(tool_id, "prepare_failed", exit_code=code)
                return

        set_state(tool_id, progress=38, message="Iniciando processo")
        if tool.get("detached"):
            timeout = int(tool.get("startup_timeout", 300))
            code = run_step(tool_id, cwd, tool["start"], "start", timeout=timeout)
            if code:
                set_state(tool_id, status="error", progress=100, message=f"Docker falhou, exit={code}")
                DB.runtime_event(tool_id, "start_failed", exit_code=code)
                return
            apply_memory_limit(tool_id, tool)
            ready_url = tool.get("ready_url", "")
            started = time.monotonic()
            while time.monotonic() - started < timeout:
                with state_lock:
                    if tool_id in stopping_tools or states[tool_id].get("availability") != "installed":
                        return
                elapsed = time.monotonic() - started
                progress = min(96, 42 + int((elapsed / max(timeout, 1)) * 54))
                set_state(tool_id, progress=progress, message=f"Aguardando {ready_url}")
                if not ready_url or url_ready(ready_url):
                    set_state(tool_id, status="running", progress=100, message="Pronto", pid=None)
                    append_log(tool_id, f"Pronto: {ready_url}")
                    DB.runtime_event(tool_id, "start", message="Containers iniciados")
                    return
                time.sleep(2)
            with state_lock:
                if tool_id in stopping_tools or states[tool_id].get("availability") != "installed":
                    return
            set_state(tool_id, status="error", progress=100, message="Containers nao ficaram prontos no tempo limite")
            return

        append_log(tool_id, f"[start] $ {tool['start']}")
        process = subprocess.Popen(
            ps_command(tool["start"]),
            cwd=str(cwd),
            env=ENV,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        job = ProcessJob(process)
        with state_lock:
            processes[tool_id] = process
            if job.active:
                process_jobs[tool_id] = job
        set_state(tool_id, pid=process.pid)
        DB.runtime_event(tool_id, "start", pid=process.pid, message="Processo iniciado")
        def stream_output():
            for line in process.stdout or []:
                append_log(tool_id, line.rstrip())
            code = process.wait()
            append_log(tool_id, f"process exit={code}")
            with state_lock:
                intentional = tool_id in stopping_tools
                stopping_tools.discard(tool_id)
                current = states[tool_id]["status"]
                processes.pop(tool_id, None)
                held_job = process_jobs.pop(tool_id, None)
            if held_job:
                held_job.close()
            DB.runtime_event(tool_id, "exit", pid=process.pid, exit_code=code)
            if intentional:
                set_state(tool_id, status="stopped", progress=0, message="Desligado", pid=None, resource={})
            elif current in {"starting", "running", "stopping"}:
                status = "error" if code else "stopped"
                set_state(
                    tool_id,
                    status=status,
                    progress=100 if code else 0,
                    message=f"Processo terminou, exit={code}",
                    pid=None,
                    resource={},
                )
                if code:
                    DB.add_event(tool_id, "error", "tool.crashed", f"Processo terminou com exit={code}")

        threading.Thread(target=stream_output, daemon=True, name=f"log-{tool_id}").start()
        ready_url = tool.get("ready_url", "")
        if not ready_url:
            time.sleep(1)
            if process.poll() is None:
                set_state(tool_id, status="running", progress=100, message="Processo iniciado")
            return

        timeout = int(tool.get("startup_timeout", 180))
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            if process.poll() is not None:
                return
            elapsed = time.monotonic() - started
            progress = min(96, 42 + int((elapsed / max(timeout, 1)) * 54))
            set_state(tool_id, progress=progress, message=f"Aguardando {ready_url}")
            if url_ready(ready_url):
                set_state(tool_id, status="running", progress=100, message="Pronto")
                append_log(tool_id, f"Pronto: {ready_url}")
                DB.add_event(tool_id, "info", "tool.ready", f"Pronto em {ready_url}")
                return
            time.sleep(2)
        set_state(tool_id, status="starting", progress=97, message="Ainda iniciando; consulte os logs")
    except Exception as error:
        append_log(tool_id, f"Falha ao iniciar: {error}")
        set_state(tool_id, status="error", progress=100, message=str(error), pid=None)
        DB.add_event(tool_id, "error", "tool.start_failed", str(error))


def stop_tool(tool_id):
    tool = TOOLS[tool_id]
    with state_lock:
        if states[tool_id]["status"] not in {"starting", "running", "error"}:
            return
        stopping_tools.add(tool_id)
    append_log(tool_id, "Desligamento solicitado")
    set_state(tool_id, status="stopping", progress=75, message="Desligando")
    DB.add_event(tool_id, "info", "tool.stopping", "Desligamento solicitado")
    if tool.get("stop"):
        code = run_step(tool_id, tool.get("path") or BASE, tool["stop"], "stop", timeout=120)
        if code:
            append_log(tool_id, f"Comando de parada retornou exit={code}; aplicando encerramento da árvore")
        elif tool.get("detached"):
            for _ in range(30):
                if not tool.get("ready_url") or not url_ready(tool["ready_url"], timeout=1):
                    set_state(tool_id, status="stopped", progress=0, message="Desligado", pid=None, resource={})
                    DB.runtime_event(tool_id, "stop", message="Containers desligados")
                    return
                time.sleep(0.2)
            set_state(tool_id, status="error", progress=100, message="A porta continua ativa apos a parada")
            return
    with state_lock:
        process = processes.get(tool_id)
        job = process_jobs.get(tool_id)
    if job and job.active:
        job.terminate()
    elif process and process.poll() is None:
        subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        ready_url = tool.get("ready_url", "")
        try:
            parsed = urlparse(ready_url)
            port = parsed.port if parsed.hostname in {"127.0.0.1", "localhost"} else None
        except ValueError:
            port = None
        if port:
            cleanup = (
                f"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | "
                "Select-Object -ExpandProperty OwningProcess -Unique | "
                f"Where-Object {{ $_ -ne {os.getpid()} }} | "
                "ForEach-Object { taskkill.exe /PID $_ /T /F | Out-Null }"
            )
            run_step(tool_id, tool.get("path") or BASE, cleanup, "port-cleanup", timeout=30)
    for _ in range(30):
        if not tool.get("ready_url") or not url_ready(tool["ready_url"], timeout=1):
            if not process or process.poll() is not None:
                return
        time.sleep(0.2)
    set_state(tool_id, status="error", progress=100, message="A porta continua ativa após a parada")


INSTALLER = AutoInstaller(DB, append_log, register_installer_definition)


def refresh_ready_states():
    while True:
        pids = []
        with state_lock:
            pids = [process.pid for process in processes.values() if process.poll() is None]
        metrics = MONITOR.process_metrics(pids)
        for tool_id, tool in list(TOOLS.items()):
            state = states.get(tool_id, {})
            ready_url = tool.get("ready_url", "")
            process = processes.get(tool_id)
            pid = process.pid if process and process.poll() is None else state.get("pid")
            if pid and pid in metrics:
                set_state(tool_id, resource=metrics[pid], pid=pid)
            if state.get("availability") != "installed" or not ready_url:
                continue
            is_ready = url_ready(ready_url, timeout=1)
            status = state.get("status")
            if is_ready and status not in {"running", "stopping"}:
                set_state(tool_id, status="running", progress=100, message="Pronto detectado")
            elif not is_ready and status == "running" and not process:
                set_state(tool_id, status="stopped", progress=0, message="Desligado", pid=None, resource={})
        time.sleep(8)


def tool_payloads():
    favorites = set(DB.favorites())
    recents = {row["tool_id"]: row for row in DB.recents(50)}
    active_operations = {}
    for operation in DB.operations(100):
        if operation["status"] in {"queued", "running", "cancelling"}:
            active_operations.setdefault(operation["tool_id"], operation)
    with state_lock:
        rows = []
        for tool_id, state in states.items():
            row = dict(state)
            operation = active_operations.get(tool_id)
            if operation:
                action_status = {
                    "install": "installing",
                    "update": "installing",
                    "remove": "stopping",
                    "rollback": "installing",
                }.get(operation["kind"], operation["status"])
                row.update(
                    status=action_status,
                    progress=operation["progress"],
                    message=operation["message"],
                    can_install=False,
                    can_update=False,
                    can_remove=False,
                )
            row["favorite"] = tool_id in favorites
            row["recent"] = recents.get(tool_id)
            rows.append(row)
        return rows


def overview():
    tools = tool_payloads()
    status_counts = {}
    for tool in tools:
        status_counts[tool["status"]] = status_counts.get(tool["status"], 0) + 1
    payload = MONITOR.overview()
    payload.update(
        {
            "running": running_count(),
            "max_running": max_running_limit(),
            "tool_count": len(tools),
            "status_counts": status_counts,
            "favorites": DB.favorites(),
            "recents": DB.recents(),
            "prompt_memory": DB.prompt_stats(),
            "active_operations": sum(
                1 for item in DB.operations(100) if item["status"] in {"queued", "running", "cancelling"}
            ),
        }
    )
    return payload


class Handler(BaseHTTPRequestHandler):
    server_version = "AICorte/2.0"

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self'; script-src 'self'; connect-src 'self'",
        )
        super().end_headers()

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, content_type):
        if not path.is_file():
            self.send_json({"error": "not found"}, 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache" if path.suffix in {".html", ".js", ".css"} else "public, max-age=86400")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self, max_bytes=1024 * 1024):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > max_bytes:
            raise ValueError("Conteúdo excede o limite permitido")
        if not length:
            return {}
        raw = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "")
        charset = ""
        for part in content_type.split(";")[1:]:
            key, separator, value = part.strip().partition("=")
            if separator and key.lower() == "charset":
                charset = value.strip().strip('"').lower()
                break
        try:
            text = raw.decode(charset or "utf-8")
        except (LookupError, UnicodeDecodeError):
            if charset and charset not in {"utf-8", "utf8"}:
                raise ValueError(f"Charset não suportado: {charset}")
            text = raw.decode("cp1252")
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("O corpo deve ser um objeto JSON")
        return payload

    def allowed_origin(self):
        origin = self.headers.get("Origin", "")
        return (
            not origin
            or origin == "http://127.0.0.1:8787"
            or origin == "http://localhost:8787"
            or origin.startswith("chrome-extension://")
        )

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/":
            self.send_file(ROOT / "index.html", "text/html; charset=utf-8")
            return
        if path.startswith("/assets/"):
            relative = Path(unquote(path.removeprefix("/assets/")))
            if ".." in relative.parts:
                self.send_json({"error": "invalid path"}, 400)
                return
            types = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
                ".svg": "image/svg+xml",
                ".css": "text/css; charset=utf-8",
                ".js": "text/javascript; charset=utf-8",
                ".json": "application/json; charset=utf-8",
            }
            self.send_file(ASSETS / relative, types.get(relative.suffix.lower(), "application/octet-stream"))
            return
        if path == "/api/banner":
            tool_id = query.get("id", [""])[0]
            if tool_id not in TOOLS:
                self.send_json({"error": "tool not found"}, 404)
                return
            target = BANNER_DIR / f"{safe_tool_id(tool_id)}.banner"
            metadata = target.with_suffix(".json")
            if target.is_file() and metadata.is_file():
                try:
                    content_type = json.loads(metadata.read_text(encoding="utf-8"))["content_type"]
                except (OSError, KeyError, json.JSONDecodeError):
                    content_type = "application/octet-stream"
                self.send_file(target, content_type)
                return
            enqueue_banner(tool_id)
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if path == "/api/overview":
            self.send_json(overview())
            return
        if path == "/api/tools":
            self.send_json(
                {
                    "tools": tool_payloads(),
                    "skipped": CATALOG["skipped"],
                    "unverified": CATALOG["unverified"],
                    "system": overview(),
                }
            )
            return
        if path == "/api/operations":
            self.send_json({"operations": DB.operations(query.get("limit", [100])[0])})
            return
        if path.startswith("/api/operations/"):
            operation = DB.operation(path.rsplit("/", 1)[-1])
            self.send_json(operation or {"error": "operation not found"}, 200 if operation else 404)
            return
        if path == "/api/events":
            self.send_json(
                {
                    "events": DB.events(
                        query.get("limit", [100])[0],
                        query.get("tool", [""])[0],
                    )
                }
            )
            return
        if path == "/api/logs":
            tool_id = query.get("id", [""])[0]
            if tool_id not in TOOLS:
                self.send_json({"error": "tool not found"}, 404)
                return
            target = log_path(tool_id)
            text = target.read_text(encoding="utf-8", errors="replace")[-100000:] if target.exists() else ""
            self.send_json({"id": tool_id, "log": text, "bytes": target.stat().st_size if target.exists() else 0})
            return
        if path == "/api/diagnostics":
            self.send_json(MONITOR.diagnostics(tool_payloads()))
            return
        if path == "/api/storage":
            self.send_json({"storage": MONITOR.storage_breakdown(), "disk": MONITOR.disk()})
            return
        if path == "/api/maintenance":
            self.send_json(MONITOR.maintenance_preview())
            return
        if path == "/api/backups":
            self.send_json({"backups": DB.backups()})
            return
        if path == "/api/settings":
            self.send_json({"settings": DB.get_settings()})
            return
        if path == "/api/prompt-state":
            app_key = query.get("app", [""])[0]
            if not app_key or len(app_key) > 512:
                self.send_json({"error": "invalid app key"}, 400)
                return
            self.send_json({"app": app_key, "values": DB.prompt_values(app_key)})
            return
        self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        if not self.allowed_origin():
            self.send_json({"error": "origin not allowed"}, 403)
            return
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            payload = self.read_json()
            response, status = self.route_post(path, payload)
            self.send_json(response, status)
        except (ValueError, KeyError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, 400)
        except FileNotFoundError as error:
            self.send_json({"error": str(error)}, 404)
        except Exception as error:
            DB.add_event("", "error", "api.error", str(error), metadata={"path": path})
            self.send_json({"error": str(error)}, 500)

    def route_post(self, path, payload):
        if path == "/api/prompt-state":
            app_key = str(payload.get("app", ""))
            field_key = str(payload.get("field", ""))
            value = str(payload.get("value", ""))
            if not app_key or len(app_key) > 512 or not field_key or len(field_key) > 1024:
                raise ValueError("Chaves de memória inválidas")
            if len(value) > 65536:
                raise ValueError("Prompt excede 64 KiB")
            DB.save_prompt(app_key, field_key, value)
            return {"ok": True}, 200
        if path == "/api/prompt-state/clear":
            return {"ok": True, "removed": DB.clear_prompt_app(str(payload.get("app", "")))}, 200
        if path in {"/api/install/analyze", "/api/install/custom"}:
            return {"error": "O AICorte instala somente receitas Docker verificadas do catalogo"}, 404
        if path == "/api/backup":
            created = DB.backup()
            DB.add_event("", "info", "backup.completed", "Backup SQLite concluído", metadata={"files": created})
            return {"ok": True, "files": created}, 201
        if path == "/api/maintenance/run":
            if payload.get("confirmation") != "LIMPAR":
                raise ValueError("Confirme a manutenção com a palavra LIMPAR")
            result = MONITOR.run_maintenance(payload.get("actions", []))
            DB.add_event("", "info", "maintenance.completed", "Manutenção concluída", metadata=result)
            return {"ok": True, **result}, 200
        if path == "/api/settings":
            allowed = {
                "view",
                "density",
                "auto_refresh",
                "prompt_memory",
                "open_in_new_tab",
                "low_power_browser",
                "max_running_apps",
                "max_ram_gb",
                "max_storage_gb",
            }
            numeric_limits = {"max_running_apps": 100, "max_ram_gb": 1024, "max_storage_gb": 100000}
            for key, maximum in numeric_limits.items():
                if key not in payload:
                    continue
                try:
                    value = float(payload[key])
                except (TypeError, ValueError) as error:
                    raise ValueError(f"Valor inválido para {key}") from error
                if value < 0 or value > maximum:
                    raise ValueError(f"{key} deve ficar entre 0 e {maximum}")
                payload[key] = int(value) if key == "max_running_apps" else value
            for key, value in payload.items():
                if key in allowed:
                    DB.set_setting(key, value)
            if "max_running_apps" in payload:
                with state_lock:
                    for state in states.values():
                        if state.get("status") == "stopped" and str(state.get("message", "")).startswith("Limite de "):
                            state["message"] = "Desligado"
            return {"ok": True, "settings": DB.get_settings()}, 200

        parts = [part for part in path.split("/") if part]
        if len(parts) != 3 or parts[0] != "api":
            return {"error": "not found"}, 404
        action, tool_id = parts[1], parts[2]
        if action == "operations" and payload.get("action") == "cancel":
            return {"ok": INSTALLER.cancel(tool_id)}, 200
        if tool_id not in TOOLS:
            return {"error": "tool not found"}, 404
        tool = TOOLS[tool_id]
        if action == "start":
            threading.Thread(target=start_tool, args=(tool_id,), daemon=True, name=f"start-{tool_id}").start()
            return {"ok": True}, 202
        if action == "stop":
            threading.Thread(target=stop_tool, args=(tool_id,), daemon=True, name=f"stop-{tool_id}").start()
            return {"ok": True}, 202
        if action == "access":
            DB.touch_recent(tool_id)
            return {"ok": True, "url": tool.get("url")}, 200
        if action == "favorite":
            DB.set_favorite(tool_id, bool(payload.get("enabled")))
            return {"ok": True}, 200
        if action == "logs-clear":
            target = log_path(tool_id)
            if target.exists():
                target.write_text("", encoding="utf-8")
            return {"ok": True}, 200
        if action == "open-folder":
            folder = payload.get("path") or tool.get("path")
            return {"ok": True, "path": MONITOR.open_folder(folder)}, 200
        if action == "install":
            install_payload = {
                **payload,
                **tool,
                "definition": tool,
            }
            operation_id = INSTALLER.enqueue(tool_id, "install", install_payload)
            return {"ok": True, "operation_id": operation_id}, 202
        if action == "update":
            if not tool.get("install_managed"):
                raise ValueError("Atualização automática disponível apenas para instalações gerenciadas")
            operation_id = INSTALLER.enqueue(
                tool_id,
                "update",
                {
                    **payload,
                    **tool,
                    "trusted": bool(payload.get("trusted")),
                    "target_path": tool["path"],
                    "definition": tool,
                },
            )
            return {"ok": True, "operation_id": operation_id}, 202
        if action == "repair":
            if not tool.get("install_managed"):
                raise ValueError("Reparo automático disponível apenas para instalações gerenciadas")
            operation_id = INSTALLER.enqueue(
                tool_id,
                "update",
                {**payload, **tool, "trusted": True, "definition": tool},
            )
            return {"ok": True, "operation_id": operation_id}, 202
        if action == "remove":
            if not tool.get("install_managed"):
                raise ValueError("Remoção pelo painel disponível apenas para instalações gerenciadas")
            operation_id = INSTALLER.enqueue(
                tool_id,
                "remove",
                {**tool, "definition": tool},
            )
            return {"ok": True, "operation_id": operation_id}, 202
        return {"error": "not found"}, 404

    def log_message(self, _format, *_args):
        return


def main():
    DB.add_event("", "info", "hub.started", "AICorte iniciado")
    threading.Thread(target=banner_worker, daemon=True, name="banner-cache").start()
    threading.Thread(target=refresh_ready_states, daemon=True, name="health-monitor").start()
    server = ThreadingHTTPServer(("127.0.0.1", 8787), Handler)
    print("AICorte running at http://127.0.0.1:8787", flush=True)
    try:
        server.serve_forever()
    finally:
        DB.add_event("", "info", "hub.stopped", "AICorte encerrado")
        server.server_close()


if __name__ == "__main__":
    main()
