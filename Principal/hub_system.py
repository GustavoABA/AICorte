import ctypes
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from hub_paths import (
    AI,
    APP,
    BACKUPS,
    BASE,
    LOGS,
    POWERSHELL,
    PROJECTS,
    RUNTIME,
    STATE,
    STAGING,
    within,
)


GB = 1024**3
MB = 1024**2


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class SystemMonitor:
    def __init__(self):
        self._lock = threading.RLock()
        self._cache = {}
        self._storage_value = None
        self._storage_updated = 0
        self._storage_refreshing = False
        self._storage_cache_file = STATE / "storage-cache.json"
        try:
            payload = json.loads(self._storage_cache_file.read_text(encoding="utf-8"))
            self._storage_value = payload.get("items")
            self._storage_updated = float(payload.get("updated", 0))
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    def cached(self, key, ttl, producer):
        now = time.monotonic()
        with self._lock:
            value, expires = self._cache.get(key, (None, 0))
            if now < expires:
                return value
        value = producer()
        with self._lock:
            self._cache[key] = (value, now + ttl)
        return value

    @staticmethod
    def disk():
        usage = shutil.disk_usage(BASE)
        return {
            "free_bytes": usage.free,
            "used_bytes": usage.used,
            "total_bytes": usage.total,
            "free_gb": round(usage.free / GB, 1),
            "used_gb": round(usage.used / GB, 1),
            "total_gb": round(usage.total / GB, 1),
            "percent": round((usage.used / usage.total) * 100, 1),
        }

    @staticmethod
    def memory():
        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return {"ok": False}
        return {
            "ok": True,
            "percent": int(status.dwMemoryLoad),
            "total_bytes": int(status.ullTotalPhys),
            "available_bytes": int(status.ullAvailPhys),
            "used_bytes": int(status.ullTotalPhys - status.ullAvailPhys),
        }

    @staticmethod
    def _runtime_checks():
        return {
            "Python": RUNTIME / "uv-python" / "cpython-3.11-windows-x86_64-none" / "python.exe",
            "Git": RUNTIME / "git" / "current" / "cmd" / "git.exe",
            "Chromium (WebView2)": BASE / "Principal" / "native" / "runtime" / "WebView2Loader.dll",
            "Docker": RUNTIME / "docker" / "docker.exe",
            "Docker Compose": RUNTIME / "docker" / "docker-compose.exe",
        }

    def runtimes(self):
        def collect():
            return [
                {
                    "name": name,
                    "ok": path.is_file(),
                    "path": str(path),
                    "bytes": path.stat().st_size if path.is_file() else 0,
                }
                for name, path in self._runtime_checks().items()
            ]

        return self.cached("runtimes", 30, collect)

    @staticmethod
    def gpu():
        candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "NVIDIA Corporation"
            / "NVSMI"
            / "nvidia-smi.exe",
            Path("nvidia-smi.exe"),
        ]
        command = next((str(path) for path in candidates if path.is_file()), "nvidia-smi.exe")
        try:
            result = subprocess.run(
                [
                    command,
                    "--query-gpu=name,memory.total,memory.used,utilization.gpu,driver_version",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            )
            if result.returncode:
                return {"available": False, "message": "nvidia-smi não respondeu"}
            cards = []
            for line in result.stdout.splitlines():
                parts = [part.strip() for part in line.split(",")]
                if len(parts) >= 5:
                    cards.append(
                        {
                            "name": parts[0],
                            "memory_total_mb": int(parts[1]),
                            "memory_used_mb": int(parts[2]),
                            "utilization": int(parts[3]),
                            "driver": parts[4],
                        }
                    )
            return {"available": bool(cards), "cards": cards}
        except (OSError, subprocess.SubprocessError, ValueError):
            return {"available": False, "message": "GPU NVIDIA não detectada"}

    def overview(self):
        return {
            "root": str(BASE),
            "disk": self.disk(),
            "memory": self.memory(),
            "gpu": self.cached("gpu", 5, self.gpu),
            "runtimes": self.runtimes(),
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    @staticmethod
    def port_open(url):
        try:
            parsed = urlparse(url)
            if parsed.hostname not in {"127.0.0.1", "localhost"} or not parsed.port:
                return False
            with socket.create_connection(("127.0.0.1", parsed.port), timeout=0.4):
                return True
        except (OSError, ValueError):
            return False

    @staticmethod
    def process_metrics(pids):
        ids = sorted({int(pid) for pid in pids if pid})
        if not ids:
            return {}
        joined = ",".join(str(pid) for pid in ids)
        script = (
            f"Get-Process -Id {joined} -ErrorAction SilentlyContinue | "
            "Select-Object Id,ProcessName,CPU,WorkingSet64,StartTime | ConvertTo-Json -Compress"
        )
        try:
            result = subprocess.run(
                [str(POWERSHELL), "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            )
            if result.returncode or not result.stdout.strip():
                return {}
            payload = json.loads(result.stdout)
            rows = payload if isinstance(payload, list) else [payload]
            return {
                int(row["Id"]): {
                    "name": row.get("ProcessName", ""),
                    "cpu_seconds": round(float(row.get("CPU") or 0), 1),
                    "memory_bytes": int(row.get("WorkingSet64") or 0),
                    "started_at": row.get("StartTime"),
                }
                for row in rows
            }
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
            return {}

    @staticmethod
    def directory_size(path):
        root = Path(path)
        total = 0
        if not root.exists():
            return 0
        for current, _dirs, files in os.walk(root):
            for name in files:
                try:
                    total += (Path(current) / name).stat().st_size
                except OSError:
                    continue
        return total

    def storage_breakdown(self):
        roots = {
            "Projetos": PROJECTS,
            "Modelos": AI,
            "Runtimes": RUNTIME,
            "Logs": LOGS,
            "Estado": STATE,
            "Temporários": APP / "tmp",
        }
        with self._lock:
            stale = time.time() - self._storage_updated > 3600
            if (self._storage_value is None or stale) and not self._storage_refreshing:
                self._storage_refreshing = True
                threading.Thread(
                    target=self._refresh_storage,
                    args=(roots,),
                    daemon=True,
                    name="storage-inventory",
                ).start()
            if self._storage_value is not None:
                return self._storage_value
        return [
            {"name": name, "path": str(path), "bytes": 0, "calculating": True}
            for name, path in roots.items()
        ]

    def _refresh_storage(self, roots):
        try:
            items = [
                {"name": name, "path": str(path), "bytes": self.directory_size(path), "calculating": False}
                for name, path in roots.items()
            ]
            updated = time.time()
            temporary = self._storage_cache_file.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps({"updated": updated, "items": items}, ensure_ascii=False),
                encoding="utf-8",
            )
            temporary.replace(self._storage_cache_file)
            with self._lock:
                self._storage_value = items
                self._storage_updated = updated
        finally:
            with self._lock:
                self._storage_refreshing = False

    def maintenance_preview(self):
        candidates = []
        now = time.time()
        rules = [
            ("temporary", APP / "tmp", 2 * 86400),
            ("staging", STAGING, 2 * 86400),
            ("old_logs", LOGS, 14 * 86400),
        ]
        for kind, root, age in rules:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    stat = path.stat()
                    if now - stat.st_mtime >= age:
                        candidates.append(
                            {"kind": kind, "path": str(path), "bytes": stat.st_size}
                        )
                except OSError:
                    continue
        return {
            "items": candidates[:2000],
            "count": len(candidates),
            "bytes": sum(item["bytes"] for item in candidates),
            "actions": [
                {"id": "safe-files", "label": "Arquivos temporários e logs antigos"},
            ],
        }

    def run_maintenance(self, actions):
        result = {"removed": 0, "freed_bytes": 0, "messages": []}
        selected = set(actions or [])
        if "safe-files" in selected:
            for item in self.maintenance_preview()["items"]:
                path = Path(item["path"])
                allowed = any(
                    self._is_inside(path, root)
                    for root in (APP / "tmp", STAGING, LOGS)
                )
                if not allowed or not path.is_file():
                    continue
                try:
                    size = path.stat().st_size
                    path.unlink()
                    result["removed"] += 1
                    result["freed_bytes"] += size
                except OSError as error:
                    result["messages"].append(f"{path.name}: {error}")
        with self._lock:
            self._storage_updated = 0
        return result

    @staticmethod
    def _is_inside(path, root):
        try:
            within(root, path, allow_root=False)
            return True
        except ValueError:
            return False

    def diagnostics(self, tools):
        checks = []
        disk = self.disk()
        checks.append(
            {
                "id": "root-drive",
                "ok": BASE.is_absolute() and BASE.exists(),
                "label": "Raiz de dados selecionada",
                "detail": str(BASE),
            }
        )
        checks.append(
            {
                "id": "disk-space",
                "ok": disk["free_bytes"] >= 5 * GB,
                "label": "Pelo menos 5 GB livres",
                "detail": f"{disk['free_gb']} GB livres",
            }
        )
        checks.extend(
            {
                "id": f"runtime-{item['name'].lower()}",
                "ok": item["ok"],
                "label": f"Runtime {item['name']}",
                "detail": item["path"],
            }
            for item in self.runtimes()
        )
        docker = RUNTIME / "docker" / "docker.exe"
        docker_ready = False
        docker_detail = "Docker CLI ausente"
        if docker.is_file():
            try:
                result = subprocess.run(
                    [str(docker), "info", "--format", "{{.ServerVersion}}"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=15,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    check=False,
                )
                docker_ready = result.returncode == 0
                docker_detail = (result.stdout or result.stderr).strip()[-500:] or "Docker daemon indisponivel"
            except (OSError, subprocess.SubprocessError):
                docker_detail = "Docker daemon indisponivel"
        checks.append(
            {
                "id": "docker-daemon",
                "ok": docker_ready,
                "label": "Docker daemon",
                "detail": docker_detail,
            }
        )
        installed = [tool for tool in tools if tool.get("availability") == "installed"]
        missing_paths = [
            tool["name"]
            for tool in installed
            if tool.get("path") and not Path(tool["path"]).exists()
        ]
        missing_commands = [tool["name"] for tool in installed if not tool.get("start")]
        nonportable_paths = [
            tool["name"]
            for tool in tools
            if tool.get("path") and not self._is_inside(Path(tool["path"]), BASE)
        ]
        checks.extend(
            [
                {
                    "id": "installed-paths",
                    "ok": not missing_paths,
                    "label": "Pastas dos instalados",
                    "detail": "Todas presentes" if not missing_paths else ", ".join(missing_paths),
                },
                {
                    "id": "installed-commands",
                    "ok": not missing_commands,
                    "label": "Comandos de inicialização",
                    "detail": "Todos definidos" if not missing_commands else ", ".join(missing_commands),
                },
                {
                    "id": "portable-paths",
                    "ok": not nonportable_paths,
                    "label": "Caminhos dentro da raiz selecionada",
                    "detail": "Todos portáteis" if not nonportable_paths else ", ".join(nonportable_paths),
                },
            ]
        )
        duplicate_ports = {}
        for tool in tools:
            try:
                port = urlparse(tool.get("ready_url") or tool.get("url") or "").port
            except ValueError:
                port = None
            if port:
                duplicate_ports.setdefault(port, []).append(tool["name"])
        conflicts = {port: names for port, names in duplicate_ports.items() if len(names) > 1}
        checks.append(
            {
                "id": "catalog-ports",
                "ok": not conflicts,
                "label": "Portas únicas no catálogo",
                "detail": "Sem conflitos" if not conflicts else json.dumps(conflicts, ensure_ascii=False),
            }
        )
        return {
            "ok": all(item["ok"] for item in checks),
            "checks": checks,
            "storage": self.storage_breakdown(),
        }

    @staticmethod
    def open_folder(path):
        candidate = Path(path).resolve()
        allowed_roots = (BASE, PROJECTS, AI, APP, STATE, BACKUPS)
        if not any(SystemMonitor._is_inside(candidate, root) or candidate == root for root in allowed_roots):
            raise ValueError("Pasta fora da área permitida")
        if not candidate.exists():
            raise FileNotFoundError(candidate)
        os.startfile(str(candidate))
        return str(candidate)
