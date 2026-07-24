import json
import os
import queue
import re
import secrets
import shutil
import stat
import subprocess
import tarfile
import threading
import time
import urllib.request
import zipfile
from fnmatch import fnmatch
from pathlib import Path

from hub_db import utc_now
from hub_paths import AI, APP, BASE, DOWNLOADS, PROJECTS, ROOT, RUNTIME, portable_environment, safe_tool_id, within


class InstallCancelled(Exception):
    pass


class AutoInstaller:
    """Serialized installer for verified catalog recipes."""

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
                self._progress(operation_id, 1, "preflight", "Validando receita e armazenamento local")
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

    @staticmethod
    def _remove_tree(path):
        target = Path(path)
        if not target.exists():
            return

        def clear_readonly(function, filename, _error):
            os.chmod(filename, stat.S_IWRITE)
            function(filename)

        shutil.rmtree(target, onerror=clear_readonly)

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
        raw_path = str(payload.get("source_path") or payload.get("docker_source_path") or "")
        repo = str(payload.get("source_repo") or payload.get("docker_source_repo") or "")
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

    @staticmethod
    def _ensure_docker_env(payload):
        keys = [str(key) for key in payload.get("docker_secret_keys") or []]
        raw_path = str(payload.get("docker_env_file") or "")
        if not keys or not raw_path:
            return None
        env_file = within(BASE, raw_path)
        env_file.parent.mkdir(parents=True, exist_ok=True)
        values = {}
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip()
        changed = False
        for key in keys:
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", key):
                raise RuntimeError("Nome de segredo invalido na receita Docker")
            if not values.get(key):
                values[key] = secrets.token_urlsafe(36)
                changed = True
        if changed or not env_file.is_file():
            env_file.write_text(
                "\n".join(f"{key}={values[key]}" for key in sorted(values)) + "\n",
                encoding="utf-8",
            )
        return env_file

    def _install(self, operation_id, tool_id, payload):
        kind = str(payload.get("install_kind") or "docker")
        if kind == "docker":
            return self._install_docker(operation_id, tool_id, payload)
        if kind == "source":
            return self._install_source(operation_id, tool_id, payload)
        if kind == "release":
            return self._install_release(operation_id, tool_id, payload)
        raise RuntimeError(f"Receita de instalacao nao suportada: {kind}")

    def _download_release_asset(self, operation_id, tool_id, payload):
        slug = str(payload.get("release_repo") or "")
        pattern = str(payload.get("release_asset_pattern") or "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", slug):
            raise RuntimeError("Repositorio de release invalido na receita")
        request = urllib.request.Request(
            f"https://api.github.com/repos/{slug}/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "AICorte/3.0"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            release = json.load(response)
        try:
            matcher = re.compile(pattern, re.IGNORECASE)
        except re.error as error:
            raise RuntimeError("Padrao de release invalido na receita") from error
        asset = next(
            (item for item in release.get("assets", []) if matcher.fullmatch(str(item.get("name", "")))),
            None,
        )
        if not asset:
            raise RuntimeError(f"O release atual de {slug} nao contem o pacote Windows esperado")
        destination_dir = within(DOWNLOADS, DOWNLOADS / tool_id)
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = within(destination_dir, destination_dir / Path(asset["name"]).name)
        partial = destination.with_suffix(destination.suffix + ".part")
        if partial.exists():
            partial.unlink()
        self._progress(operation_id, 20, "download", f"Baixando {asset['name']}")
        download = urllib.request.Request(
            asset["browser_download_url"],
            headers={"Accept": "application/octet-stream", "User-Agent": "AICorte/3.0"},
        )
        with urllib.request.urlopen(download, timeout=120) as response, partial.open("wb") as output:
            total = int(response.headers.get("Content-Length") or asset.get("size") or 0)
            copied = 0
            while True:
                if self.db.cancel_requested(operation_id):
                    raise InstallCancelled()
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                copied += len(chunk)
                if total:
                    self._progress(
                        operation_id,
                        20 + min(40, int(copied / total * 40)),
                        "download",
                        f"Baixando {asset['name']}",
                    )
        partial.replace(destination)
        return destination

    @staticmethod
    def _safe_archive_members(names):
        for name in names:
            path = Path(name.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError(f"Pacote contem caminho inseguro: {name}")

    @staticmethod
    def _find_release_executable(package, pattern):
        matches = [
            path
            for path in package.rglob("*")
            if path.is_file() and fnmatch(path.name.lower(), pattern.lower())
        ]
        if not matches:
            raise RuntimeError(f"Executavel {pattern} nao foi encontrado no pacote")
        return sorted(matches, key=lambda item: (len(item.parts), len(str(item))))[0]

    def _install_release(self, operation_id, tool_id, payload):
        if not payload.get("trusted"):
            raise RuntimeError("O download requer confirmacao explicita")
        self._check_storage_limit()
        marker = within(APP, payload["install_marker"])
        package = within(APP, payload["path"])
        archive = self._download_release_asset(operation_id, tool_id, payload)
        self._progress(operation_id, 65, "extract", "Preparando pacote portatil")
        if package.exists():
            self._remove_tree(package)
        package.mkdir(parents=True, exist_ok=True)
        archive_kind = str(payload.get("release_archive") or "raw")
        executable_pattern = str(payload.get("release_executable_glob") or "*.exe")
        if archive_kind == "zip":
            with zipfile.ZipFile(archive) as bundle:
                members = bundle.infolist()
                self._safe_archive_members(item.filename for item in members)
                if any(
                    stat.S_IFMT(item.external_attr >> 16) == stat.S_IFLNK
                    for item in members
                ):
                    raise RuntimeError("Pacote ZIP contem links nao permitidos")
                bundle.extractall(package, members=members)
            executable = self._find_release_executable(package, executable_pattern)
        elif archive_kind == "tar.gz":
            with tarfile.open(archive, "r:gz") as bundle:
                members = bundle.getmembers()
                self._safe_archive_members(item.name for item in members)
                if any(item.issym() or item.islnk() for item in members):
                    raise RuntimeError("Pacote TAR contem links nao permitidos")
                bundle.extractall(package, members=members)
            executable = self._find_release_executable(package, executable_pattern)
        elif archive_kind == "raw":
            executable = package / executable_pattern
            shutil.copy2(archive, executable)
        else:
            raise RuntimeError(f"Formato de pacote nao suportado: {archive_kind}")
        self._progress(operation_id, 88, "launcher", "Criando launcher local")
        marker.parent.mkdir(parents=True, exist_ok=True)
        launcher = marker.parent / "start.ps1"
        escaped_package = str(executable.parent).replace("'", "''")
        escaped_executable = str(executable).replace("'", "''")
        launcher.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f"Set-Location -LiteralPath '{escaped_package}'\n"
            f"& '{escaped_executable}' @args\n"
            "exit $LASTEXITCODE\n",
            encoding="utf-8",
        )
        marker.write_text(
            json.dumps(
                {"installed_at": utc_now(), "kind": "release", "asset": archive.name, "executable": str(executable)},
                indent=2,
            ),
            encoding="utf-8",
        )
        definition = dict(payload.get("definition") or {})
        definition["availability"] = "installed"
        self.db.set_override(tool_id, "installed")
        self.on_definition(definition)
        return {
            "message": f"{definition.get('name', tool_id)} instalado como pacote portatil",
            "path": str(package),
        }

    def _install_source(self, operation_id, tool_id, payload):
        if not payload.get("trusted"):
            raise RuntimeError("O download requer confirmacao explicita")
        self._check_storage_limit()
        marker = within(APP, payload["install_marker"])
        target = within(PROJECTS, payload["source_path"])
        repo = str(payload.get("source_repo") or "")
        if not repo.startswith("https://github.com/") or not repo.endswith(".git"):
            raise RuntimeError("Repositorio oficial invalido na receita")
        git = RUNTIME / "git" / "current" / "cmd" / "git.exe"
        if not git.is_file():
            raise RuntimeError("Git portatil ausente em app\\runtime\\git")
        if target.exists() and not (target / ".git").is_dir():
            raise RuntimeError(f"A pasta de destino ja existe e nao e um repositorio Git: {target}")
        if (target / ".git").is_dir():
            self._progress(operation_id, 25, "update", "Atualizando codigo-fonte oficial")
            self._run(
                operation_id,
                tool_id,
                [git, "pull", "--ff-only"],
                target,
                label="git-pull",
                timeout=1800,
            )
        else:
            self._progress(operation_id, 20, "download", "Baixando codigo-fonte oficial")
            target.parent.mkdir(parents=True, exist_ok=True)
            self._run(
                operation_id,
                tool_id,
                [git, "clone", "--depth", "1", "--single-branch", repo, target],
                target.parent,
                label="git-clone",
                timeout=3600,
            )
        self._progress(operation_id, 92, "verify", "Validando repositorio baixado")
        self._run(
            operation_id,
            tool_id,
            [git, "rev-parse", "--verify", "HEAD"],
            target,
            label="git-verify",
            timeout=60,
        )
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps({"installed_at": utc_now(), "kind": "source", "repo": repo}, indent=2),
            encoding="utf-8",
        )
        definition = dict(payload.get("definition") or {})
        definition["availability"] = "installed"
        self.db.set_override(tool_id, "installed")
        self.on_definition(definition)
        return {
            "message": f"Codigo oficial de {definition.get('name', tool_id)} baixado e validado",
            "path": str(target),
        }

    def _install_docker(self, operation_id, tool_id, payload):
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
        env_file = self._ensure_docker_env(payload)
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
        common = [compose, "--project-name", payload["docker_project"]]
        if env_file:
            common.extend(["--env-file", env_file])
        common.extend(["--file", compose_file])

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
        kind = str(payload.get("install_kind") or "docker")
        if kind == "docker":
            return self._remove_docker(operation_id, tool_id, payload)
        if kind == "source":
            return self._remove_source(operation_id, tool_id, payload)
        if kind == "release":
            return self._remove_release(operation_id, tool_id, payload)
        raise RuntimeError(f"Receita de remocao nao suportada: {kind}")

    def _remove_release(self, operation_id, tool_id, payload):
        package = within(APP, payload["path"])
        marker = within(APP, payload["install_marker"])
        self._progress(operation_id, 35, "package", "Removendo pacote portatil")
        if package.exists():
            self._remove_tree(package)
        if marker.parent.exists():
            self._remove_tree(marker.parent)
        self.db.set_override(tool_id, "available")
        definition = dict(payload.get("definition") or {})
        definition["availability"] = "available"
        self.on_definition(definition)
        return {"message": "Pacote portatil removido"}

    def _remove_source(self, operation_id, tool_id, payload):
        target = within(PROJECTS, payload["source_path"])
        marker = within(APP, payload["install_marker"])
        self._progress(operation_id, 35, "source", "Removendo codigo-fonte gerenciado")
        if target.exists():
            self._remove_tree(target)
        if marker.is_file():
            marker.unlink()
        self.db.set_override(tool_id, "available")
        definition = dict(payload.get("definition") or {})
        definition["availability"] = "available"
        self.on_definition(definition)
        return {"message": "Codigo-fonte gerenciado removido"}

    def _remove_docker(self, operation_id, tool_id, payload):
        compose_file = within(BASE, payload["docker_compose"])
        marker = within(BASE, payload["docker_marker"])
        _docker, compose = self._check_docker(operation_id, tool_id)
        common = [compose, "--project-name", payload["docker_project"]]
        raw_env = str(payload.get("docker_env_file") or "")
        if raw_env:
            common.extend(["--env-file", within(BASE, raw_env)])
        common.extend(["--file", compose_file])
        self._progress(operation_id, 30, "containers", "Removendo containers e imagens")
        self._run(
            operation_id,
            tool_id,
            [*common, "down", "--rmi", "all", "--remove-orphans"],
            compose_file.parent,
            label="remove",
        )
        raw_source = str(payload.get("docker_source_path") or "")
        if raw_source:
            source = within(PROJECTS, raw_source)
            if source.exists():
                self._progress(operation_id, 80, "source", "Removendo codigo usado no build")
                self._remove_tree(source)
        if marker.is_file():
            marker.unlink()
        self.db.set_override(tool_id, "available")
        definition = dict(payload.get("definition") or {})
        definition["availability"] = "available"
        self.on_definition(definition)
        return {
            "message": "Instalacao Docker e codigo de build removidos; dados persistentes foram preservados",
        }
