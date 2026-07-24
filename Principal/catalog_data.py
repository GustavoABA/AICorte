from pathlib import Path


BASE = Path(__file__).resolve().parent.parent
PRINCIPAL = BASE / "Principal"
PROJECTS = BASE / "PROJETOS"
APP = BASE / "app"
AI = BASE / "AI"
STACKS = PRINCIPAL / "docker"
COMPOSE = APP / "runtime" / "docker" / "docker-compose.exe"
INSTALLS = APP / "installations"
PACKAGES = APP / "packages"


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
    secret_keys=(),
):
    stack = STACKS / tool_id
    compose_file = stack / "compose.yaml"
    marker = stack / ".installed"
    env_file = PRINCIPAL / "state" / tool_id / ".env"
    prefix = (
        f"& '{_quoted(COMPOSE)}' --project-name {compose_project} "
        + (f"--env-file '{_quoted(env_file)}' " if secret_keys else "")
        + f"--file '{_quoted(compose_file)}'"
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
        "install_kind": "docker",
        "install_label": "Download",
        "detached": True,
        "docker_compose": str(compose_file),
        "docker_project": compose_project,
        "docker_marker": str(marker),
        "docker_build": bool(build),
        "docker_source_repo": source_repo,
        "docker_source_path": source_path,
        "docker_env_file": str(env_file) if secret_keys else "",
        "docker_secret_keys": list(secret_keys),
    }


def catalog_tool(
    tool_id,
    name,
    category,
    description,
    *,
    runtime,
    repo="",
    platform="Windows / self-hosted",
    hardware="Consulte os requisitos oficiais do projeto antes de instalar.",
    notes="",
    supported=True,
):
    banner = ""
    if repo:
        banner = (
            "https://opengraph.githubassets.com/aicorte/"
            + repo.removeprefix("https://github.com/").removesuffix(".git")
        )
    marker = INSTALLS / tool_id / ".installed"
    target = PROJECTS / tool_id
    installable = bool(repo) and supported
    if marker.is_file():
        availability = "installed"
    elif installable:
        availability = "available"
    else:
        availability = "blocked"
    return {
        "id": tool_id,
        "name": name,
        "category": category,
        "description": description,
        "repo": repo,
        "project": tool_id,
        "path": str(target) if installable else "",
        "runtime": runtime,
        "availability": availability,
        "platform": platform,
        "banner": banner,
        "url": "",
        "ready_url": "",
        "start": "",
        "prepare": "",
        "stop": "",
        "hardware": hardware,
        "notes": notes or (
            "O AICorte baixa e atualiza o codigo-fonte oficial em PROJETOS. "
            "Dependencias, credenciais e servicos externos continuam sujeitos a documentacao oficial."
        ),
        "startup_timeout": 0,
        "install_managed": installable,
        "install_kind": "source" if installable else "unsupported",
        "install_label": "Baixar código" if installable else "Incompatível",
        "install_marker": str(marker),
        "source_repo": repo + ("" if repo.endswith(".git") else ".git") if repo else "",
        "source_path": str(target) if installable else "",
        "detached": False,
        "docker_compose": "",
        "docker_project": "",
        "docker_marker": "",
        "docker_build": False,
        "docker_source_repo": "",
        "docker_source_path": "",
    }


def release_tool(
    tool_id,
    name,
    category,
    description,
    *,
    runtime,
    repo,
    asset_pattern,
    executable_glob,
    archive="raw",
    hardware="Consulte os requisitos oficiais do projeto antes de instalar.",
    notes="",
):
    tool = catalog_tool(
        tool_id,
        name,
        category,
        description,
        runtime=runtime,
        repo=repo,
        hardware=hardware,
        notes=notes,
    )
    package = PACKAGES / tool_id
    launcher = INSTALLS / tool_id / "start.ps1"
    tool.update(
        {
            "path": str(package),
            "install_kind": "release",
            "install_label": "Download",
            "source_repo": "",
            "source_path": "",
            "release_repo": repo.removeprefix("https://github.com/").removesuffix(".git"),
            "release_asset_pattern": asset_pattern,
            "release_archive": archive,
            "release_executable_glob": executable_glob,
            "start": f"& '{_quoted(launcher)}'",
            "prepare": (
                f"if (-not (Test-Path '{_quoted(launcher)}')) "
                "{ throw 'Launcher do pacote ausente; use Reparar' }"
            ),
            "stop": "",
            "startup_timeout": 60,
        }
    )
    return tool


DOCKER_TOOLS = [
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
    docker_tool(
        "qwenpaw",
        "QwenPaw",
        "Agentes",
        "Assistente pessoal de IA self-hosted com memoria, skills, automacoes e multiplos canais.",
        repo="https://github.com/agentscope-ai/QwenPaw",
        runtime="Docker / LLM",
        url="http://127.0.0.1:8088",
        hardware="8 GB de RAM; o provedor ou modelo de IA configurado pode exigir mais recursos.",
        notes="Dados, segredos e backups persistem em Principal\\state\\qwenpaw.",
        compose_project="aicorte-qwenpaw",
        startup_timeout=600,
    ),
    docker_tool(
        "open-notebook",
        "Open Notebook",
        "Conhecimento",
        "Alternativa self-hosted ao NotebookLM para estudar PDFs, sites, videos e audios.",
        repo="https://github.com/lfnovo/open-notebook",
        runtime="Docker / RAG",
        url="http://127.0.0.1:8502",
        hardware="8 GB de RAM; extracao local pesada e modelos adicionais podem exigir GPU.",
        notes="Banco e arquivos persistem em Principal\\state\\open-notebook; chaves locais sao geradas na instalacao.",
        compose_project="aicorte-open-notebook",
        secret_keys=("OPEN_NOTEBOOK_ENCRYPTION_KEY", "SURREAL_PASSWORD"),
        startup_timeout=900,
    ),
    docker_tool(
        "trek",
        "Trek",
        "Produtividade",
        "Planejador colaborativo de viagens com mapas, orcamento, listas e recursos de IA.",
        repo="https://github.com/liketrek/TREK",
        runtime="Docker / mapas",
        url="http://127.0.0.1:3001",
        ready_url="http://127.0.0.1:3001/api/health",
        hardware="2 GB de RAM para uso local comum.",
        notes="Dados e uploads persistem em Principal\\state\\trek.",
        compose_project="aicorte-trek",
        startup_timeout=600,
    ),
    docker_tool(
        "reclip",
        "ReClip",
        "Vídeo e mídia",
        "Aplicação web local para baixar vídeo ou áudio de sites suportados.",
        repo="https://github.com/averygan/reclip",
        runtime="Docker / web",
        url="http://127.0.0.1:8899",
        hardware="2 GB de RAM; espaco em disco proporcional aos downloads.",
        notes="Use somente com conteudo autorizado. Downloads persistem em Principal\\state\\reclip.",
        compose_project="aicorte-reclip",
        source_repo="https://github.com/averygan/reclip.git",
        source_path=str(PROJECTS / "reclip"),
        build=True,
        startup_timeout=1200,
    ),
    docker_tool(
        "whaticket-community",
        "WhaTicket Community",
        "Atendimento",
        "Central local multiusuario para atender conversas do WhatsApp em filas e tickets.",
        repo="https://github.com/canove/whaticket-community",
        runtime="Docker / Node / MariaDB / Chromium",
        url="http://127.0.0.1:3002",
        hardware=(
            "CPU x64 com virtualizacao e memoria suficiente para frontend, backend, Chromium e MariaDB. "
            "O consumo cresce conforme o numero de sessoes e atendentes."
        ),
        notes=(
            "Dados, anexos e autenticacao do WhatsApp persistem em Principal\\state\\whaticket-community. "
            "Primeiro acesso: admin@whaticket.com / admin; altere a senha imediatamente. "
            "A integracao usa um cliente nao oficial do WhatsApp e pode sofrer bloqueio pela plataforma."
        ),
        compose_project="aicorte-whaticket",
        source_repo="https://github.com/canove/whaticket-community.git",
        source_path=str(PROJECTS / "whaticket-community"),
        build=True,
        secret_keys=("MYSQL_ROOT_PASSWORD", "JWT_SECRET", "JWT_REFRESH_SECRET"),
        startup_timeout=1800,
    ),
]


CATALOG_ONLY_TOOLS = [
    catalog_tool(
        "open-cut", "OpenCut", "Vídeo e mídia",
        "Editor de vídeo open source semelhante ao CapCut, executável localmente ou self-hosted.",
        runtime="Docker / Node", repo="https://github.com/opencut-app/opencut",
    ),
    catalog_tool(
        "open-montage", "OpenMontage", "Vídeo e mídia",
        "Geração e montagem automatizada de vídeos usando mídia, voz e composição programática.",
        runtime="Python / IA", repo="https://github.com/calesthio/OpenMontage",
    ),
    catalog_tool(
        "hyperframes", "HyperFrames", "Vídeo e mídia",
        "Criação de vídeos determinísticos em MP4 usando HTML, CSS, mídia e animações.",
        runtime="Node / FFmpeg", repo="https://github.com/heygen-com/hyperframes",
    ),
    catalog_tool(
        "reclip", "ReClip", "Vídeo e mídia",
        "Aplicação web para baixar vídeo ou áudio de YouTube, TikTok, Instagram e outros sites.",
        runtime="Web / Docker", repo="https://github.com/averygan/reclip",
        notes="Use somente com conteúdo que você tem autorização para baixar.",
    ),
    catalog_tool(
        "snapotter", "SnapOtter", "Imagem e design",
        "Editor e conversor local de imagens com dezenas de ferramentas.",
        runtime="Local / Web", repo="https://github.com/snapotter-hq/snapotter",
    ),
    catalog_tool(
        "compresso", "CompressO", "Vídeo e mídia",
        "Aplicativo desktop para compactação e processamento de imagens e vídeos.",
        runtime="Desktop / FFmpeg", repo="https://github.com/codeforreal1/compressO",
    ),
    catalog_tool(
        "modly", "Modly", "3D e design",
        "Aplicativo local para converter imagens em modelos ou malhas 3D usando IA.",
        runtime="Python / GPU", repo="https://github.com/lightningpixel/modly",
        hardware="GPU compatível recomendada; memória depende dos modelos utilizados.",
    ),
    catalog_tool(
        "librechat", "LibreChat", "Chat e LLM",
        "Interface self-hosted unificada para ChatGPT, Claude, Gemini e modelos locais.",
        runtime="Docker / Node", repo="https://github.com/danny-avila/LibreChat",
    ),
    catalog_tool(
        "open-higgsfield-ai", "Open Higgsfield AI", "Imagem e design",
        "Plataforma self-hosted de geração de imagens e conteúdo audiovisual com IA.",
        runtime="Python / GPU", repo="https://github.com/sunnychase/open-higgsfield-ai",
        hardware="GPU NVIDIA recomendada; VRAM depende do modelo de geração escolhido.",
    ),
    catalog_tool(
        "penecho", "PenEcho", "Produtividade",
        "Quadro colaborativo em que desenhos, equações e diagramas são usados como contexto para IA.",
        runtime="Web / IA", repo="https://github.com/penecho/penecho",
    ),
    catalog_tool(
        "qwenpaw", "QwenPaw", "Agentes",
        "Assistente pessoal de IA self-hosted com memória, skills, automações e múltiplos canais.",
        runtime="LLM / self-hosted", repo="https://github.com/agentscope-ai/QwenPaw",
    ),
    catalog_tool(
        "observer-ai", "Observer AI", "Agentes",
        "Plataforma de microagentes que observam tela, câmera ou áudio e executam ações.",
        runtime="Desktop / visão / áudio", repo="https://github.com/Roy3838/Observer",
    ),
    catalog_tool(
        "deerflow", "DeerFlow", "Agentes",
        "Plataforma de agentes e subagentes para pesquisa, programação e execução de tarefas complexas.",
        runtime="Docker / LLM", repo="https://github.com/bytedance/deer-flow",
    ),
    catalog_tool(
        "raven", "Raven", "Agentes",
        "Ambiente de agentes com memória persistente e melhoria baseada em execuções anteriores.",
        runtime="Python / Node / LLM", repo="https://github.com/EverMind-AI/Raven",
    ),
    catalog_tool(
        "agentic-inbox", "Agentic Inbox", "Comunicação",
        "Cliente de e-mail self-hosted com agente de IA para leitura, pesquisa e criação de respostas.",
        runtime="Docker / Workers", repo="https://github.com/cloudflare/agentic-inbox",
    ),
    catalog_tool(
        "open-notebook", "Open Notebook", "Conhecimento",
        "Alternativa self-hosted ao NotebookLM para estudar PDFs, sites, vídeos e áudios.",
        runtime="Docker / RAG", repo="https://github.com/lfnovo/open-notebook",
    ),
    catalog_tool(
        "yuvomi", "Yuvomi", "Produtividade",
        "Planejador familiar self-hosted com tarefas, refeições, calendário, orçamento e organização doméstica.",
        runtime="Docker / Web", repo="https://github.com/ulsklyc/yuvomi",
    ),
    catalog_tool(
        "trek", "Trek", "Produtividade",
        "Planejador colaborativo de viagens com mapas, orçamento, listas e recursos de IA.",
        runtime="Docker / mapas", repo="https://github.com/liketrek/TREK",
    ),
    catalog_tool(
        "seafile", "Seafile", "Armazenamento",
        "Plataforma self-hosted de armazenamento, sincronização e compartilhamento de arquivos.",
        runtime="Docker / banco de dados", repo="https://github.com/haiwen/seafile",
    ),
    catalog_tool(
        "instatic", "Instatic", "Web e conteúdo",
        "CMS self-hosted com editor visual, gerenciamento de conteúdo e publicação de sites.",
        runtime="Docker / CMS", repo="https://github.com/CoreBunch/Instatic",
    ),
    release_tool(
        "simplex-chat", "SimpleX Chat", "Comunicação",
        "Aplicativo de mensagens privado sem identificadores permanentes de usuário.",
        runtime="Terminal / mensagens", repo="https://github.com/simplex-chat/simplex-chat",
        asset_pattern=r"simplex-chat-windows-x86-64$", executable_glob="simplex-chat.exe",
        notes="Cliente oficial de terminal para Windows. O aplicativo desktop usa instalador MSI separado.",
    ),
    catalog_tool(
        "adguard-home", "AdGuard Home", "Infraestrutura",
        "Servidor DNS que bloqueia anúncios, rastreadores e domínios indesejados em toda a rede.",
        runtime="Docker / DNS", repo="https://github.com/AdguardTeam/AdGuardHome",
    ),
    catalog_tool(
        "logto", "Logto", "Infraestrutura",
        "Plataforma completa de autenticação e autorização para sites, SaaS e APIs.",
        runtime="Docker / Node / banco de dados", repo="https://github.com/logto-io/logto",
    ),
    catalog_tool(
        "floci", "Floci", "Desenvolvimento",
        "Ambiente local que simula serviços da AWS para desenvolvimento e testes.",
        runtime="Docker / cloud emulator", repo="https://github.com/floci-io/floci",
    ),
    catalog_tool(
        "databasement", "Databasement", "Backup e dados",
        "Aplicação self-hosted para backup e restauração de bancos de dados.",
        runtime="Docker / bancos de dados", repo="https://github.com/David-Crty/databasement",
    ),
    catalog_tool(
        "duplicati", "Duplicati", "Backup e dados",
        "Aplicativo de backup criptografado para nuvem, servidores remotos e armazenamento local.",
        runtime="Docker / desktop", repo="https://github.com/duplicati/duplicati",
    ),
    release_tool(
        "velero", "Velero", "Backup e dados",
        "Plataforma para backup, restauração e migração de clusters Kubernetes.",
        runtime="Kubernetes / CLI", repo="https://github.com/velero-io/velero",
        asset_pattern=r"velero-.*-windows-amd64\.tar\.gz$", executable_glob="velero.exe",
        archive="tar.gz",
        notes="Instala o CLI oficial. Para operar, ainda e necessario informar um cluster Kubernetes valido.",
    ),
    catalog_tool(
        "dory", "Dory", "Infraestrutura",
        "Aplicativo para executar e gerenciar containers Linux no macOS.",
        runtime="macOS / containers", platform="Somente macOS",
        repo="https://github.com/Augani/dory", supported=False,
        notes="Item exibido conforme a lista solicitada, mas não é compatível com este host Windows.",
    ),
    catalog_tool(
        "docker-android", "docker-android", "Desenvolvimento",
        "Ambiente Android completo executado em container para testes e automação.",
        runtime="Docker / Android emulator", repo="https://github.com/budtmo/docker-android",
        hardware="Virtualização e memória adicionais são necessárias para o emulador Android.",
    ),
    catalog_tool(
        "mac-sai", "Mac Sai", "Sistema",
        "Aplicativo macOS para limpeza, otimização e verificação de segurança.",
        runtime="macOS", platform="Somente macOS",
        repo="https://github.com/iliyami/MacSai", supported=False,
        notes="Item exibido conforme a lista solicitada, mas não é compatível com este host Windows.",
    ),
    release_tool(
        "mouzi", "Mouzi", "Sistema",
        "Organizador automático da pasta de downloads para Windows e Linux.",
        runtime="Desktop portatil", repo="https://github.com/hsr88/mouzi",
        asset_pattern=r"Mouzi_.*_x64-portable\.exe$", executable_glob="mouzi.exe",
    ),
    catalog_tool(
        "fileexplorer", "FileExplorer", "Sistema",
        "Gerenciador de arquivos desktop construído com Rust e Tauri.",
        runtime="Rust / Tauri", repo="https://github.com/conaticus/FileExplorer",
    ),
    release_tool(
        "superfile", "Superfile", "Sistema",
        "Gerenciador de arquivos completo executado no terminal.",
        runtime="Terminal / Go", repo="https://github.com/yorukot/superfile",
        asset_pattern=r"superfile-windows-.*-amd64\.zip$", executable_glob="spf.exe", archive="zip",
    ),
    catalog_tool(
        "veloxdb", "VeloxDB", "Banco de dados",
        "Cliente desktop para consultar, editar e administrar bancos PostgreSQL.",
        runtime="Desktop / PostgreSQL", repo="https://github.com/veloxbase/veloxdb",
    ),
    catalog_tool(
        "bruno", "Bruno", "Desenvolvimento",
        "Cliente desktop para testar APIs REST e GraphQL, alternativa ao Postman.",
        runtime="Desktop / Electron", repo="https://github.com/usebruno/bruno",
    ),
    catalog_tool(
        "voidaccess", "VoidAccess", "Segurança e OSINT",
        "Plataforma self-hosted de investigação OSINT e inteligência de ameaças.",
        runtime="Docker / segurança", repo="https://github.com/KatrielMoses/voidaccess",
    ),
    catalog_tool(
        "maigret", "Maigret", "Segurança e OSINT",
        "Aplicação de investigação de nomes de usuário em diversas plataformas.",
        runtime="Python / CLI", repo="https://github.com/soxoj/maigret",
        notes="Use apenas para pesquisas legítimas e em conformidade com a legislação aplicável.",
    ),
    catalog_tool(
        "scout", "Scout", "Pesquisa e dados",
        "Aplicação de prospecção e enriquecimento de leads a partir de perfis públicos.",
        runtime="Python / dados", repo="https://github.com/kiryano/Scout",
        notes="Use apenas dados públicos com base legal e respeite os termos das plataformas consultadas.",
    ),
    catalog_tool(
        "unblink", "Unblink", "Segurança e vídeo",
        "Sistema self-hosted de videomonitoramento com busca e análise por IA.",
        runtime="Docker / visão / GPU", repo="https://github.com/zapdos-labs/unblink",
        hardware="Armazenamento contínuo e GPU podem ser necessários conforme câmeras e retenção.",
    ),
    catalog_tool(
        "openscholarxiv", "OpenScholarXIV", "Pesquisa acadêmica",
        "Aplicativo para pesquisar, ler, resumir e salvar artigos científicos do arXiv.",
        runtime="Web / LLM", repo="https://github.com/ScholarXIV/OpenScholarXIV",
    ),
    catalog_tool(
        "paperbanana", "PaperBanana", "Pesquisa acadêmica",
        "Aplicação para gerar diagramas acadêmicos e gráficos a partir de artigos e descrições.",
        runtime="Python / LLM", repo="https://github.com/llmsresearch/paperbanana",
    ),
    catalog_tool(
        "olmocr-2", "olmOCR 2", "OCR e documentos",
        "Aplicação para converter PDFs e documentos escaneados em texto ou Markdown estruturado.",
        runtime="Python / OCR / GPU", repo="https://github.com/allenai/olmocr",
    ),
    catalog_tool(
        "textsnap", "TextSnap", "OCR e documentos",
        "Aplicação local que transforma imagens, telas e páginas em texto pesquisável.",
        runtime="Rust / OCR", repo="https://github.com/TH07008/textsnap",
    ),
    catalog_tool(
        "pixelrag", "PixelRAG", "Conhecimento",
        "Sistema de busca visual e RAG baseado em screenshots renderizados de documentos e páginas.",
        runtime="Python / visão / RAG", repo="https://github.com/StarTrail-org/PixelRAG",
    ),
    catalog_tool(
        "hyperextract", "HyperExtract", "Conhecimento",
        "Aplicação para converter documentos em conhecimento estruturado com auxílio de LLMs.",
        runtime="Python / LLM", repo="https://github.com/yifanfeng97/hyper-extract",
    ),
    catalog_tool(
        "graphify", "Graphify", "Conhecimento",
        "Aplicação que transforma projetos e documentos em grafos de conhecimento navegáveis.",
        runtime="Grafo / LLM", repo="https://github.com/Graphify-Labs/graphify",
    ),
    release_tool(
        "helixdb", "HelixDB", "Banco de dados",
        "Banco de dados completo para grafos, vetores, documentos e memória de agentes.",
        runtime="Banco vetorial / grafo", repo="https://github.com/helixdb/helix-db",
        asset_pattern=r"helix-x86_64-pc-windows-msvc\.exe$", executable_glob="helix.exe",
    ),
    catalog_tool(
        "openclaw", "OpenClaw", "Agentes",
        "Assistente pessoal de IA que pode ser executado em dispositivos próprios.",
        runtime="Node / LLM", repo="https://github.com/openclaw/openclaw",
    ),
    catalog_tool(
        "autogpt", "AutoGPT", "Agentes",
        "Plataforma para criação e execução de agentes autônomos.",
        runtime="Python / Docker / LLM", repo="https://github.com/Significant-Gravitas/AutoGPT",
    ),
    catalog_tool(
        "comfyui", "ComfyUI", "Imagem e design",
        "Aplicação visual baseada em nós para geração e processamento de imagens com IA.",
        runtime="Python / GPU", repo="https://github.com/Comfy-Org/ComfyUI",
        hardware="GPU NVIDIA recomendada; VRAM e armazenamento dependem dos modelos escolhidos.",
    ),
    release_tool(
        "kilo-code", "Kilo Code", "Desenvolvimento",
        "Agente de programação open source para VS Code, JetBrains e terminal.",
        runtime="Terminal", repo="https://github.com/Kilo-Org/kilocode",
        asset_pattern=r"kilo-windows-x64\.zip$", executable_glob="kilo.exe", archive="zip",
        notes="Instala o CLI oficial portatil. A extensao de editor continua opcional.",
    ),
    catalog_tool(
        "peacock", "Peacock", "Desenvolvimento",
        "Extensão completa do VS Code para identificar projetos por cores diferentes.",
        runtime="Extensão VS Code", repo="https://github.com/johnpapa/vscode-peacock",
        notes="Extensão de editor exibida conforme a lista solicitada; não é uma aplicação Docker independente.",
    ),
]

CATALOG_ONLY_TOOLS = [
    tool
    for tool in CATALOG_ONLY_TOOLS
    if tool["id"] not in {"qwenpaw", "open-notebook", "trek", "reclip"}
]


TOOLS = DOCKER_TOOLS + CATALOG_ONLY_TOOLS


def get_catalog():
    return {
        "max_running": 0,
        "tools": TOOLS,
        "skipped": [],
        "unverified": [],
    }
