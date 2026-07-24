import os
import re
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
BASE = ROOT.parent.resolve()
PROJECTS = (BASE / "PROJETOS").resolve()
APP = (BASE / "app").resolve()
AI = (BASE / "AI").resolve()
STATE = (ROOT / "state").resolve()
LOGS = (ROOT / "logs").resolve()
ASSETS = (ROOT / "assets").resolve()
BACKUPS = (STATE / "backups").resolve()
TRASH = (STATE / "trash").resolve()
STAGING = (APP / "tmp" / "installer").resolve()
DOWNLOADS = (APP / "downloads").resolve()
RUNTIME = (APP / "runtime").resolve()
PYTHON_ENVS = (APP / "envs" / "python").resolve()
NODE_ENVS = (APP / "envs" / "node").resolve()
PORTABLE_DRIVE = BASE.drive.upper()

POWERSHELL = (
    Path(os.environ.get("WINDIR", r"C:\Windows"))
    / "System32"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe"
)

SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
GITHUB_REPO = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)


def initialize_layout():
    directories = (
        PROJECTS,
        APP,
        AI,
        STATE,
        LOGS,
        ASSETS,
        BACKUPS,
        TRASH,
        STAGING,
        DOWNLOADS,
        APP / "tmp",
        AI / "Ollama",
        AI / "Open-LLM-VTuber",
        STATE / "ollama-panel",
        STATE / "n8n",
        STATE / "open-llm-vtuber" / "config",
    )
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)


def safe_tool_id(value):
    candidate = re.sub(r"[^a-z0-9-]+", "-", str(value).strip().lower()).strip("-")
    candidate = re.sub(r"-{2,}", "-", candidate)[:63]
    if not SAFE_ID.fullmatch(candidate):
        raise ValueError("Identificador deve conter apenas letras minúsculas, números e hífens")
    return candidate


def github_repo_url(value):
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname.lower() != "github.com"
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Use uma URL de repositório no formato https://github.com/dono/projeto")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        raise ValueError("Use uma URL de repositório no formato https://github.com/dono/projeto")
    owner, repo = parts[0], parts[1].removesuffix(".git")
    canonical = f"https://github.com/{owner}/{repo}"
    match = GITHUB_REPO.fullmatch(canonical)
    if not match:
        raise ValueError("O proprietário ou nome do repositório contém caracteres inválidos")
    return f"https://github.com/{owner}/{repo}.git"


def github_slug(value):
    match = GITHUB_REPO.fullmatch(github_repo_url(value))
    if not match:
        raise ValueError("Repositório GitHub inválido")
    return f"{match.group('owner')}/{match.group('repo').removesuffix('.git')}"


def within(root, candidate, *, allow_root=False):
    root_path = Path(root).resolve()
    candidate_path = Path(candidate).resolve()
    try:
        relative = candidate_path.relative_to(root_path)
    except ValueError as error:
        raise ValueError(f"Caminho fora de {root_path}: {candidate_path}") from error
    if not allow_root and not relative.parts:
        raise ValueError("A operação não pode usar a raiz protegida")
    return candidate_path


def project_path(tool_id):
    return within(PROJECTS, PROJECTS / safe_tool_id(tool_id))


def portable_environment(extra=None):
    runtime_paths = (
        RUNTIME / "docker",
        RUNTIME / "git" / "current" / "cmd",
        RUNTIME / "git" / "current" / "usr" / "bin",
    )
    env = os.environ.copy()
    env.update(
        {
            "PATH": os.pathsep.join(str(path) for path in runtime_paths if path.exists())
            + os.pathsep
            + env.get("PATH", ""),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "OLLAMA_MODELS": str(AI / "Ollama"),
            "DOCKER_CONFIG": str(RUNTIME / "docker-config"),
            "DOCKER_HOST": "tcp://127.0.0.1:2375",
            "TEMP": str(APP / "tmp"),
            "TMP": str(APP / "tmp"),
            "TMPDIR": str(APP / "tmp"),
            "AICORTE_ROOT": str(BASE),
            "AICORTE_AI": str(AI),
            "AICORTE_APP": str(APP),
            "AICORTE_PROJECTS": str(PROJECTS),
            "AICORTE_ROOT_WSL": "/mnt/"
            + BASE.drive.rstrip(":").lower()
            + BASE.as_posix().split(":", 1)[-1],
        }
    )
    if extra:
        env.update({str(key): str(value) for key, value in extra.items()})
    return env


def local_http_url(value):
    parsed = urlparse(str(value or ""))
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("A URL deve apontar para 127.0.0.1 ou localhost")
    if not parsed.port:
        raise ValueError("A URL local precisa informar a porta")
    return value


initialize_layout()
