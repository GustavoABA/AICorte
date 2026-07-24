import json
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path

from hub_db import utc_now
from hub_paths import AI, BASE, PROJECTS, ROOT, RUNTIME, portable_environment, safe_tool_id, within


class InstallCancelled(Exception):
    pass


class AutoInstaller:
    """Serialized installer for catalog Docker stacks."""

    def __init__(self, db, append_log, on_definition):
        self.db = db
        self.append_log = append_log
        self.on_definition = on_definition
        self.jobs = queue.Queue()
        self.active_processes = {}
        self._lock = threading.RLock()
        threading.Thread(target=self._worker, daemon=True, name="aicorte-installer").start()

    def enqueue(self, tool_id, kind, payload):
        operation_id = self.db.create_operation(tool_id, kind, payload)
        self.jobs.put((operation_id, safe_tool_id(tool_id), kind, payload))
        return operation_id

    def cancel(self, operation_id):
        changed = self.db.request_cancel(operation_id)
        with self._lock:
            process = self.active_processes.get(operation_id)
        if process and process.poll() is None:
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        return changed

    def _worker(self):
        while True:
            operation_id, tool_id, kind, payload = self.jobs.get()
            try:
                if self.db.cancel_requested(operation_id):
                    raise InstallCancelled()
                self._progress(operation_id, 1, "preflight", "Validando Docker e armazenamento local")
                if kind in {"install", "update"}:
                    result = self._install(operation_id, tool_id, payload)
                elif kind == "remove":
                    result = self._remove(operation_id, tool_id, payload)
                else:
                    raise ValueError(f"Operacao nao suportada: {kind}")
                self.db.finish_operation(operation_id, "completed", result["message"], result)
                self.db.add_event(
                    tool_id,
                    "info",
                    f"{kind}.completed",
                    result["message"],
                    operation_id=operation_id,
                    metadata=result,
                )
            except InstallCancelled:
                self.db.finish_operation(operation_id, "cancelled", "Operacao cancelada")
            except Exception as error:
                message = str(error).strip() or error.__class__.__name__
                self.append_log(tool_id, f"Falha na operacao {operation_id}: {message}")
                self.db.finish_operation(operation_id, "failed", message, {"error": message})
                self.db.add_event(
                    tool_id,
                    "error",
                    f"{kind}.failed",
                    message,
                    operation_id=operation_id,
                )
            finally:
                with self._lock:
                    self.active_processes.pop(operation_id, None)
                self.jobs.task_done()

    def _progress(self, operation_id, progress, phase, message):
        if self.db.cancel_requested(operation_id):
            raise InstallCancelled()
        self.db.update_operation(
            operation_id,
            status="running",
            progress=max(0, min(int(progress), 99)),
            phase=phase,
            message=message,
            started_at=utc_now(),
        )

    def _run(self, operation_id, tool_id, command, cwd, *, label, timeout=7200):
        if self.db.cancel_requested(operation_id):
            raise InstallCancelled()
        command = [str(part) for part in command]
        self.append_log(tool_id, f"[{label}] $ {subprocess.list2cmdline(command)}")
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=portable_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        with self._lock:
            self.active_processes[operation_id] = process
        started = time.monotonic()
        while process.poll() is None:
            line = process.stdout.readline() if process.stdout else ""
            if line:
                self.append_log(tool_id, f"[{label}] {line.rstrip()}")
            if self.db.cancel_requested(operation_id):
                subprocess.run(
                    ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                raise InstallCancelled()
            if time.monotonic() - started > timeout:
                subprocess.run(
                    ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                raise TimeoutError(f"{label} excedeu {timeout // 60} minutos")
            time.sleep(0.05)
        for line in process.stdout or []:
            self.append_log(tool_id, f"[{label}] {line.rstrip()}")
        with self._lock:
            self.active_processes.pop(operation_id, None)
        if process.returncode:
            raise RuntimeError(f"{label} falhou com exit code {process.returncode}")

    @staticmethod
    def _docker_binaries():
        docker = RUNTIME / "docker" / "docker.exe"
        compose = RUNTIME / "docker" / "docker-compose.exe"
        if not docker.is_file() or not compose.is_file():
            raise RuntimeError("Runtime Docker ausente em app\\runtime\\docker")
        return docker, compose

    @staticmethod
    def _wsl_path(path):
        resolved = Path(path).resolve()
        drive = resolved.drive.rstrip(":").lower()
        suffix = resolved.as_posix().split(":", 1)[-1]
        return f"/mnt/{drive}{suffix}"

    @staticmethod
    def _directory_size(root):
        total = 0
        for path in Path(root).rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                continue
        return total

    def _check_storage_limit(self):
        try:
            limit_gb = float(self.db.get_settings().get("max_storage_gb", 0) or 0)
        except (TypeError, ValueError):
            limit_gb = 0
        if limit_gb <= 0:
            return
        used = self._directory_size(BASE)
        limit = int(limit_gb * 1024**3)
        if used >= limit:
            raise RuntimeError(
                f"Limite de armazenamento do AICorte atingido: {used / 1024**3:.1f} de {limit_gb:g} GB"
            )

    def _apply_memory_limit(self, operation_id, tool_id, docker, project):
        try:
            limit_gb = float(self.db.get_settings().get("max_ram_gb", 0) or 0)
        except (TypeError, ValueError):
            limit_gb = 0
        if limit_gb <= 0:
            return
        result = subprocess.run(
            [str(docker), "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"],
            cwd=str(BASE),
            env=portable_environment(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
        container_ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not container_ids:
            return
        limit_bytes = max(6 * 1024**2, int(limit_gb * 1024**3))
        self._run(
            operation_id,
            tool_id,
            [docker, "update", "--memory", str(limit_bytes), "--memory-swap", str(limit_bytes), *container_ids],
            BASE,
            label="memory-limit",
            timeout=120,
        )

    def _check_docker(self, operation_id, tool_id):
        docker, compose = self._docker_binaries()
        try:
            self._run(operation_id, tool_id, [docker, "info"], BASE, label="docker", timeout=60)
        except RuntimeError as error:
            setup = ROOT / "scripts" / "install-docker.ps1"
            raise RuntimeError(
                f"Docker daemon indisponivel. Execute {setup} como administrador e reinicie o AICorte."
            ) from error
        return docker, compose

    def _ensure_network(self, operation_id, tool_id, docker):
        result = subprocess.run(
            [str(docker), "network", "inspect", "aicorte"],
            cwd=str(BASE),
            env=portable_environment(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode:
            self._run(
                operation_id,
                tool_id,
                [docker, "network", "create", "aicorte"],
                BASE,
                label="network",
                timeout=60,
            )

    def _ensure_source(self, operation_id, tool_id, payload):
        raw_path = str(payload.get("docker_source_path") or "")
        repo = str(payload.get("docker_source_repo") or "")
        if not raw_path:
            return None
        target = within(PROJECTS, raw_path)
        if target.is_dir():
            return target
        git = RUNTIME / "git" / "current" / "cmd" / "git.exe"
        if not git.is_file():
            raise RuntimeError("Git portatil ausente em app\\runtime\\git")
        self._progress(operation_id, 12, "source", "Baixando codigo-fonte oficial")
        self._run(
            operation_id,
            tool_id,
            [git, "clone", "--depth", "1", "--single-branch", repo, target],
            PROJECTS,
            label="clone",
            timeout=1800,
        )
        return target

    @staticmethod
    def _seed_storage(tool_id, source):
        (ROOT / "state" / tool_id).mkdir(parents=True, exist_ok=True)
        (AI / "Ollama").mkdir(parents=True, exist_ok=True)
        (ROOT / "state" / "ollama-panel").mkdir(parents=True, exist_ok=True)
        (ROOT / "state" / "n8n" / ".n8n").mkdir(parents=True, exist_ok=True)
        (AI / "Open-LLM-VTuber").mkdir(parents=True, exist_ok=True)
        config_dir = ROOT / "state" / "open-llm-vtuber" / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        if tool_id != "open-llm-vtuber" or not source:
            return
        dockerfile = source / "dockerfile"
        if dockerfile.is_file():
            docker_content = dockerfile.read_text(encoding="utf-8")
            install_line = "RUN uv pip install --no-deps ."
            if "uv pip install piper-tts" not in docker_content:
                if install_line not in docker_content:
                    raise RuntimeError("Dockerfile do Open-LLM-VTuber mudou; ajuste Piper TTS manualmente")
                docker_content = docker_content.replace(
                    install_line,
                    install_line + " \\\n && uv pip install piper-tts",
                    1,
                )
                dockerfile.write_text(docker_content, encoding="utf-8")
        config = config_dir / "conf.yaml"
        if not config.is_file():
            candidate = source / "conf.yaml"
            if not candidate.is_file():
                candidate = source / "config_templates" / "conf.default.yaml"
            shutil.copy2(candidate, config)
            content = config.read_text(encoding="utf-8")
            content = content.replace("http://localhost:11434", "http://aicorte-ollama:11434")
            content = content.replace("http://127.0.0.1:11434", "http://aicorte-ollama:11434")
            models_path = str(AI / "Open-LLM-VTuber")
            content = content.replace(models_path.replace("\\", "/"), "/app/models")
            content = content.replace(models_path, "/app/models")
            content = content.replace("\n  host: localhost\n  port: 12393", "\n  host: 0.0.0.0\n  port: 12393")
            config.write_text(content, encoding="utf-8")
        model_dict = config_dir / "model_dict.json"
        if not model_dict.is_file() and (source / "model_dict.json").is_file():
            shutil.copy2(source / "model_dict.json", model_dict)

    def _install(self, operation_id, tool_id, payload):
        if not payload.get("trusted"):
            raise RuntimeError("A instalacao Docker requer confirmacao explicita")
        self._check_storage_limit()
        compose_file = within(BASE, payload["docker_compose"])
        if not compose_file.is_file():
            raise FileNotFoundError(f"Compose ausente: {compose_file}")
        marker = within(BASE, payload["docker_marker"])
        docker, compose = self._check_docker(operation_id, tool_id)
        self._ensure_network(operation_id, tool_id, docker)
        source = self._ensure_source(operation_id, tool_id, payload)
        self._seed_storage(tool_id, source)
        if tool_id == "n8n":
            n8n_state = self._wsl_path(ROOT / "state" / "n8n" / ".n8n")
            self._run(
                operation_id,
                tool_id,
                [
                    "wsl.exe",
                    "-d",
                    "AICorteDocker",
                    "-u",
                    "root",
                    "--",
                    "bash",
                    "-lc",
                    f"chown -R 1000:1000 '{n8n_state}' && "
                    f"find '{n8n_state}' -type d -exec chmod 700 {{}} + && "
                    f"find '{n8n_state}' -type f -exec chmod 600 {{}} +",
                ],
                BASE,
                label="permissions",
                timeout=600,
            )
        common = [compose, "--project-name", payload["docker_project"], "--file", compose_file]

        self._progress(operation_id, 25, "download", "Baixando imagens Docker")
        self._run(
            operation_id,
            tool_id,
            [*common, "pull", "--ignore-buildable", "--quiet"],
            compose_file.parent,
            label="pull",
        )
        if payload.get("docker_build"):
            self._progress(operation_id, 55, "build", "Construindo imagem local")
            self._run(
                operation_id,
                tool_id,
                [
                    "wsl.exe",
                    "-d",
                    "AICorteDocker",
                    "-u",
                    "root",
                    "--",
                    "env",
                    f"AICORTE_ROOT_WSL={self._wsl_path(BASE)}",
                    "docker",
                    "compose",
                    "--project-name",
                    payload["docker_project"],
                    "--file",
                    self._wsl_path(compose_file),
                    "build",
                    "--pull",
                ],
                compose_file.parent,
                label="build",
                timeout=14400,
            )
        self._progress(operation_id, 88, "create", "Criando containers desligados")
        self._run(operation_id, tool_id, [*common, "create"], compose_file.parent, label="create")
        self._apply_memory_limit(operation_id, tool_id, docker, payload["docker_project"])
        marker.write_text(
            json.dumps({"installed_at": utc_now(), "project": payload["docker_project"]}, indent=2),
            encoding="utf-8",
        )
        definition = dict(payload.get("definition") or {})
        definition["availability"] = "installed"
        self.db.set_override(tool_id, "installed")
        self.on_definition(definition)
        return {"message": f"{definition.get('name', tool_id)} baixado e instalado com Docker"}

    def _remove(self, operation_id, tool_id, payload):
        compose_file = within(BASE, payload["docker_compose"])
        marker = within(BASE, payload["docker_marker"])
        _docker, compose = self._check_docker(operation_id, tool_id)
        common = [compose, "--project-name", payload["docker_project"], "--file", compose_file]
        self._progress(operation_id, 30, "containers", "Removendo containers e imagens")
        self._run(
            operation_id,
            tool_id,
            [*common, "down", "--rmi", "all", "--remove-orphans"],
            compose_file.parent,
            label="remove",
        )
        if marker.is_file():
            marker.unlink()
        self.db.set_override(tool_id, "available")
        definition = dict(payload.get("definition") or {})
        definition["availability"] = "available"
        self.on_definition(definition)
        return {
            "message": "Instalacao Docker removida; modelos, workflows e configuracoes foram preservados",
        }
