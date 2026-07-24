from pathlib import Path


BASE = Path(__file__).resolve().parent.parent
PRINCIPAL = BASE / "Principal"
PROJECTS = BASE / "PROJETOS"
APP = BASE / "app"
AI = BASE / "AI"
STACKS = PRINCIPAL / "docker"
COMPOSE = APP / "runtime" / "docker" / "docker-compose.exe"


def _quoted(path):
    return str(path).replace("'", "''")


def docker_tool(
    tool_id,
    name,
    category,
    description,
    *,
    repo,
    runtime,
    url,
    ready_url=None,
    hardware,
    notes,
    compose_project,
    source_repo="",
    source_path="",
    build=False,
    startup_timeout=300,
):
    stack = STACKS / tool_id
    compose_file = stack / "compose.yaml"
    marker = stack / ".installed"
    prefix = (
        f"& '{_quoted(COMPOSE)}' --project-name {compose_project} "
        f"--file '{_quoted(compose_file)}'"
    )
    availability = "installed" if marker.is_file() else "available"
    return {
        "id": tool_id,
        "name": name,
        "category": category,
        "description": description,
        "repo": repo,
        "project": tool_id,
        "path": str(stack),
        "runtime": runtime,
        "availability": availability,
        "platform": "Docker Engine local / WSL2",
        "banner": (
            "https://opengraph.githubassets.com/aicorte/"
            + repo.removeprefix("https://github.com/").removesuffix(".git")
        ),
        "url": url,
        "ready_url": ready_url or url,
        "start": f"{prefix} up -d --remove-orphans",
        "prepare": (
            f"if (-not (Test-Path '{_quoted(COMPOSE)}')) {{ throw 'Docker Compose portatil ausente' }}; "
            f"if (-not (Test-Path '{_quoted(compose_file)}')) {{ throw 'Compose da ferramenta ausente' }}; "
            f"& '{_quoted(COMPOSE)}' version; if ($LASTEXITCODE -ne 0) {{ throw 'Docker Compose indisponivel' }}"
        ),
        "stop": f"{prefix} stop",
        "hardware": hardware,
        "notes": notes,
        "startup_timeout": startup_timeout,
        "install_managed": True,
        "detached": True,
        "docker_compose": str(compose_file),
        "docker_project": compose_project,
        "docker_marker": str(marker),
        "docker_build": bool(build),
        "docker_source_repo": source_repo,
        "docker_source_path": source_path,
    }


TOOLS = [
    docker_tool(
        "ollama",
        "Ollama",
        "LLM local",
        "Executa modelos locais e inclui painel web para chat, downloads e gerenciamento.",
        repo="https://github.com/ollama/ollama",
        runtime="Docker / NVIDIA GPU",
        url="http://127.0.0.1:11435",
        ready_url="http://127.0.0.1:11435/api/status",
        hardware="16 GB de RAM; GPU NVIDIA recomendada. O consumo depende do modelo carregado.",
        notes=(
            "API Ollama em http://127.0.0.1:11434. No n8n use "
            "http://aicorte-ollama:11434. Modelos ficam em AI\\Ollama."
        ),
        compose_project="aicorte-ollama",
        build=True,
        startup_timeout=600,
    ),
    docker_tool(
        "open-llm-vtuber",
        "Open-LLM-VTuber",
        "Avatar e voz",
        "Companheiro VTuber local com Ollama, voz, memoria, visao e Live2D.",
        repo="https://github.com/Open-LLM-VTuber/Open-LLM-VTuber",
        runtime="Docker / Python / Ollama",
        url="http://127.0.0.1:12393",
        hardware="16 GB de RAM, microfone e GPU NVIDIA recomendada para o modelo local.",
        notes=(
            "Configuracao persistente em Principal\\state\\open-llm-vtuber; "
            "vozes e modelos em AI\\Open-LLM-VTuber. Requer o Ollama ligado."
        ),
        compose_project="aicorte-open-llm-vtuber",
        source_repo="https://github.com/Open-LLM-VTuber/Open-LLM-VTuber.git",
        source_path=str(PROJECTS / "Open-LLM-VTuber"),
        build=True,
        startup_timeout=1200,
    ),
    docker_tool(
        "n8n",
        "n8n",
        "Automacao",
        "Automacao visual de servicos, APIs e agentes com suporte a nos comunitarios.",
        repo="https://github.com/n8n-io/n8n",
        runtime="Docker / Node / SQLite",
        url="http://127.0.0.1:5678",
        hardware="CPU de 4 nucleos, 8 GB de RAM e espaco para workflows e pacotes.",
        notes=(
            "Dados persistentes em Principal\\state\\n8n. Para chamar o Ollama use "
            "http://aicorte-ollama:11434 sem API externa."
        ),
        compose_project="aicorte-n8n",
        startup_timeout=600,
    ),
    docker_tool(
        "open-webui",
        "Open WebUI",
        "Chat e LLM",
        "Interface local semelhante ao ChatGPT, já conectada ao Ollama do AICorte.",
        repo="https://github.com/open-webui/open-webui",
        runtime="Docker / Ollama",
        url="http://127.0.0.1:3000",
        ready_url="http://127.0.0.1:3000/health",
        hardware="4 GB de RAM para a interface; a necessidade do modelo é definida pelo Ollama.",
        notes="Dados persistentes em Principal\\state\\open-webui. Use o Ollama local sem API externa.",
        compose_project="aicorte-open-webui",
        startup_timeout=900,
    ),
    docker_tool(
        "langflow",
        "Langflow",
        "Workflows de IA",
        "Editor visual para criar fluxos com LLMs, agentes, ferramentas e RAG.",
        repo="https://github.com/langflow-ai/langflow",
        runtime="Docker / Python",
        url="http://127.0.0.1:7860",
        hardware="Mínimo de 2 GB de RAM; 4 GB ou mais são recomendados para fluxos maiores.",
        notes="Login automático local e dados persistentes em Principal\\state\\langflow.",
        compose_project="aicorte-langflow",
        startup_timeout=900,
    ),
    docker_tool(
        "memos",
        "Memos",
        "Notas e conhecimento",
        "Aplicativo leve de notas Markdown para registrar contexto, ideias e documentação local.",
        repo="https://github.com/usememos/memos",
        runtime="Docker / SQLite",
        url="http://127.0.0.1:5230",
        hardware="1 GB de RAM e armazenamento proporcional às notas e anexos.",
        notes="Banco e anexos persistentes em Principal\\state\\memos.",
        compose_project="aicorte-memos",
        startup_timeout=300,
    ),
    docker_tool(
        "ntfy",
        "ntfy",
        "Notificações",
        "Servidor local para enviar e receber notificações por HTTP e integrar automações.",
        repo="https://github.com/binwiederhier/ntfy",
        runtime="Docker / Go",
        url="http://127.0.0.1:8085",
        ready_url="http://127.0.0.1:8085/v1/health",
        hardware="Menos de 1 GB de RAM para uso local comum.",
        notes="Cache persistente em Principal\\state\\ntfy. No n8n use http://aicorte-ntfy:80.",
        compose_project="aicorte-ntfy",
        startup_timeout=300,
    ),
]


def get_catalog():
    return {
        "max_running": 0,
        "tools": TOOLS,
        "skipped": [],
        "unverified": [],
    }
