import json
import mimetypes
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DB_PATH = Path(os.environ.get("AICORTE_DB_PATH", str(ROOT / "ollama-panel.sqlite3")))
OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
HOST = os.environ.get("AICORTE_HOST", "127.0.0.1")
PORT = int(os.environ.get("AICORTE_PORT", "11435"))
PULL_TASKS: dict[str, dict] = {}
PULL_LOCK = threading.Lock()


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE IF NOT EXISTS chat_messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT NOT NULL, role TEXT NOT NULL, "
        "content TEXT NOT NULL, created_at INTEGER NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.commit()
    return connection


def ollama(path: str, payload=None, method: str | None = None, timeout: int = 300):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        OLLAMA + path,
        data=body,
        headers={"Content-Type": "application/json"},
        method=method or ("POST" if body is not None else "GET"),
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
        return json.loads(data) if data else {}


def installed_models() -> list[dict]:
    return ollama("/api/tags", timeout=10).get("models", [])


def pull_worker(task_id: str, model: str) -> None:
    request = urllib.request.Request(
        OLLAMA + "/api/pull",
        data=json.dumps({"model": model, "stream": True}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=3600) as response:
            for raw_line in response:
                if not raw_line.strip():
                    continue
                update = json.loads(raw_line)
                with PULL_LOCK:
                    task = PULL_TASKS[task_id]
                    task.update(
                        status=update.get("status", task["status"]),
                        completed=update.get("completed", task["completed"]),
                        total=update.get("total", task["total"]),
                    )
        with PULL_LOCK:
            PULL_TASKS[task_id].update(done=True, status="concluido")
    except Exception as exc:
        with PULL_LOCK:
            PULL_TASKS[task_id].update(done=True, error=str(exc), status="falhou")


def start_pull(model: str) -> dict:
    task_id = uuid.uuid4().hex
    task = {
        "id": task_id,
        "model": model,
        "status": "iniciando",
        "completed": 0,
        "total": 0,
        "done": False,
        "error": "",
    }
    with PULL_LOCK:
        PULL_TASKS[task_id] = task
    threading.Thread(target=pull_worker, args=(task_id, model), daemon=True).start()
    return task


class Handler(BaseHTTPRequestHandler):
    server_version = "AICorteOllama/1.0"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def send_json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def api_error(self, exc):
        if isinstance(exc, urllib.error.HTTPError):
            try:
                detail = json.loads(exc.read()).get("error", str(exc))
            except Exception:
                detail = str(exc)
            self.send_json({"error": detail}, exc.code)
        else:
            self.send_json({"error": str(exc)}, 500)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/api/status":
                self.send_json(
                    {
                        "version": ollama("/api/version", timeout=5).get("version", ""),
                        "models": installed_models(),
                        "running": ollama("/api/ps", timeout=10).get("models", []),
                    }
                )
                return
            if parsed.path == "/api/models":
                self.send_json({"models": installed_models()})
                return
            if parsed.path == "/api/running":
                self.send_json(ollama("/api/ps", timeout=10))
                return
            if parsed.path == "/api/show":
                model = urllib.parse.parse_qs(parsed.query).get("model", [""])[0]
                self.send_json(ollama("/api/show", {"model": model}, timeout=30))
                return
            if parsed.path.startswith("/api/tasks/"):
                task_id = parsed.path.rsplit("/", 1)[-1]
                with PULL_LOCK:
                    task = PULL_TASKS.get(task_id)
                self.send_json(task or {"error": "Tarefa nao encontrada"}, 200 if task else 404)
                return
            if parsed.path == "/api/history":
                model = urllib.parse.parse_qs(parsed.query).get("model", [""])[0]
                with db() as connection:
                    rows = connection.execute(
                        "SELECT id, role, content, created_at FROM chat_messages "
                        "WHERE model=? ORDER BY id DESC LIMIT 100",
                        (model,),
                    ).fetchall()
                self.send_json({"messages": [dict(row) for row in reversed(rows)]})
                return
            self.serve_static(parsed.path)
        except Exception as exc:
            self.api_error(exc)

    def do_POST(self):
        try:
            payload = self.read_json()
            if self.path == "/api/chat":
                self.handle_chat(payload)
                return
            if self.path == "/api/pull":
                model = str(payload.get("model", "")).strip()
                if not model:
                    raise ValueError("Informe o nome do modelo")
                self.send_json(start_pull(model), 202)
                return
            if self.path == "/api/model/action":
                model = str(payload.get("model", "")).strip()
                action = payload.get("action")
                keep_alive = -1 if action == "load" else 0
                if action not in {"load", "unload"}:
                    raise ValueError("Acao de modelo invalida")
                result = ollama(
                    "/api/generate",
                    {"model": model, "prompt": "", "stream": False, "keep_alive": keep_alive},
                )
                self.send_json({"ok": True, "result": result})
                return
            if self.path == "/api/clear":
                model = str(payload.get("model", ""))
                with db() as connection:
                    connection.execute("DELETE FROM chat_messages WHERE model=?", (model,))
                    connection.commit()
                self.send_json({"ok": True})
                return
            if self.path == "/api/command":
                self.handle_command(str(payload.get("command", "")).strip())
                return
            self.send_json({"error": "Rota nao encontrada"}, 404)
        except Exception as exc:
            self.api_error(exc)

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path != "/api/models":
                self.send_json({"error": "Rota nao encontrada"}, 404)
                return
            model = urllib.parse.parse_qs(parsed.query).get("model", [""])[0]
            self.send_json(ollama("/api/delete", {"model": model}, method="DELETE"))
        except Exception as exc:
            self.api_error(exc)

    def handle_chat(self, payload):
        model = str(payload.get("model", "")).strip()
        message = str(payload.get("message", "")).strip()
        temperature = float(payload.get("temperature", 0.7))
        if not model or not message:
            raise ValueError("Modelo e mensagem sao obrigatorios")
        with db() as connection:
            history = connection.execute(
                "SELECT role, content FROM chat_messages WHERE model=? ORDER BY id DESC LIMIT 30",
                (model,),
            ).fetchall()
            messages = [dict(row) for row in reversed(history)] + [{"role": "user", "content": message}]
            result = ollama(
                "/api/chat",
                {"model": model, "messages": messages, "stream": False, "options": {"temperature": temperature}},
                timeout=600,
            )
            answer = result.get("message", {}).get("content", "")
            now = int(time.time())
            connection.executemany(
                "INSERT INTO chat_messages(model, role, content, created_at) VALUES(?,?,?,?)",
                [(model, "user", message, now), (model, "assistant", answer, now)],
            )
            connection.execute(
                "INSERT INTO settings(key,value) VALUES('selected_model',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (model,),
            )
            connection.commit()
        self.send_json({"message": answer, "metrics": result})

    def handle_command(self, command: str):
        parts = command.split(maxsplit=2)
        verb = parts[0].lower() if parts else "help"
        if verb == "help":
            output = "Comandos: help, version, list, ps, show <modelo>, pull <modelo>, rm <modelo>, load <modelo>, stop <modelo>, run <modelo> <prompt>, clear"
        elif verb == "version":
            output = json.dumps(ollama("/api/version"), ensure_ascii=False, indent=2)
        elif verb == "list":
            output = "\n".join(model["name"] for model in installed_models()) or "Nenhum modelo instalado"
        elif verb == "ps":
            output = json.dumps(ollama("/api/ps"), ensure_ascii=False, indent=2)
        elif verb == "show" and len(parts) >= 2:
            output = json.dumps(ollama("/api/show", {"model": parts[1]}), ensure_ascii=False, indent=2)
        elif verb == "pull" and len(parts) >= 2:
            task = start_pull(parts[1])
            output = f"Download iniciado. Tarefa: {task['id']}"
        elif verb == "rm" and len(parts) >= 2:
            ollama("/api/delete", {"model": parts[1]}, method="DELETE")
            output = f"Modelo removido: {parts[1]}"
        elif verb in {"load", "stop"} and len(parts) >= 2:
            ollama(
                "/api/generate",
                {"model": parts[1], "prompt": "", "stream": False, "keep_alive": -1 if verb == "load" else 0},
            )
            output = f"Modelo {'carregado' if verb == 'load' else 'descarregado'}: {parts[1]}"
        elif verb == "run" and len(parts) == 3:
            result = ollama("/api/generate", {"model": parts[1], "prompt": parts[2], "stream": False}, timeout=600)
            output = result.get("response", "")
        elif verb == "clear":
            output = "__CLEAR__"
        else:
            raise ValueError("Comando invalido. Digite help para ver os comandos permitidos.")
        self.send_json({"output": output})

    def serve_static(self, path: str):
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        candidate = (STATIC / relative).resolve()
        try:
            candidate.relative_to(STATIC.resolve())
        except ValueError:
            self.send_error(403)
            return
        if not candidate.is_file():
            self.send_error(404)
            return
        data = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    db().close()
    print(f"Ollama Panel: http://{HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
