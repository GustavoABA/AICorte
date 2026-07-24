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
):
    banner = ""
    if repo:
        banner = (
            "https://opengraph.githubassets.com/aicorte/"
            + repo.removeprefix("https://github.com/").removesuffix(".git")
        )
    return {
        "id": tool_id,
        "name": name,
        "category": category,
        "description": description,
        "repo": repo,
        "project": "",
        "path": "",
        "runtime": runtime,
        "availability": "catalog",
        "platform": platform,
        "banner": banner,
        "url": "",
        "ready_url": "",
        "start": "",
        "prepare": "",
        "stop": "",
        "hardware": hardware,
        "notes": notes or "Item solicitado para o catálogo. A receita de instalação ainda não foi validada.",
        "startup_timeout": 0,
        "install_managed": False,
        "detached": False,
        "docker_compose": "",
        "docker_project": "",
        "docker_marker": "",
        "docker_build": False,
        "docker_source_repo": "",
        "docker_source_path": "",
    }


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
        runtime="Web / Docker",
        notes="Use somente com conteúdo que você tem autorização para baixar. Receita ainda não validada.",
    ),
    catalog_tool(
        "snapotter", "SnapOtter", "Imagem e design",
        "Editor e conversor local de imagens com dezenas de ferramentas.",
        runtime="Local / Web", repo="https://github.com/snapotter-hq/snapotter",
    ),
    catalog_tool(
        "compresso", "CompressO", "Vídeo e mídia",
        "Aplicativo desktop para compactação e processamento de imagens e vídeos.",
        runtime="Desktop / FFmpeg",
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
        runtime="Web / IA",
    ),
    catalog_tool(
        "qwenpaw", "QwenPaw", "Agentes",
        "Assistente pessoal de IA self-hosted com memória, skills, automações e múltiplos canais.",
        runtime="LLM / self-hosted",
    ),
    catalog_tool(
        "observer-ai", "Observer AI", "Agentes",
        "Plataforma de microagentes que observam tela, câmera ou áudio e executam ações.",
        runtime="Python / visão / áudio",
    ),
    catalog_tool(
        "deerflow", "DeerFlow", "Agentes",
        "Plataforma de agentes e subagentes para pesquisa, programação e execução de tarefas complexas.",
        runtime="Docker / LLM",
    ),
    catalog_tool(
        "raven", "Raven", "Agentes",
        "Ambiente de agentes com memória persistente e melhoria baseada em execuções anteriores.",
        runtime="Python / LLM",
    ),
    catalog_tool(
        "agentic-inbox", "Agentic Inbox", "Comunicação",
        "Cliente de e-mail self-hosted com agente de IA para leitura, pesquisa e criação de respostas.",
        runtime="Docker / Workers", repo="https://github.com/cloudflare/agentic-inbox",
    ),
    catalog_tool(
        "open-notebook", "Open Notebook", "Conhecimento",
        "Alternativa self-hosted ao NotebookLM para estudar PDFs, sites, vídeos e áudios.",
        runtime="Docker / RAG",
    ),
    catalog_tool(
        "yuvomi", "Yuvomi", "Produtividade",
        "Planejador familiar self-hosted com tarefas, refeições, calendário, orçamento e organização doméstica.",
        runtime="Docker / Web", repo="https://github.com/ulsklyc/yuvomi",
    ),
    catalog_tool(
        "trek", "Trek", "Produtividade",
        "Planejador colaborativo de viagens com mapas, orçamento, listas e recursos de IA.",
        runtime="Web / mapas",
    ),
    catalog_tool(
        "seafile", "Seafile", "Armazenamento",
        "Plataforma self-hosted de armazenamento, sincronização e compartilhamento de arquivos.",
        runtime="Docker / banco de dados",
    ),
    catalog_tool(
        "instatic", "Instatic", "Web e conteúdo",
        "CMS self-hosted com editor visual, gerenciamento de conteúdo e publicação de sites.",
        runtime="Web / CMS",
    ),
    catalog_tool(
        "simplex-chat", "SimpleX Chat", "Comunicação",
        "Aplicativo de mensagens privado sem identificadores permanentes de usuário.",
        runtime="Desktop / mobile",
    ),
    catalog_tool(
        "adguard-home", "AdGuard Home", "Infraestrutura",
        "Servidor DNS que bloqueia anúncios, rastreadores e domínios indesejados em toda a rede.",
        runtime="Docker / DNS",
    ),
    catalog_tool(
        "logto", "Logto", "Infraestrutura",
        "Plataforma completa de autenticação e autorização para sites, SaaS e APIs.",
        runtime="Docker / Node / banco de dados",
    ),
    catalog_tool(
        "floci", "Floci", "Desenvolvimento",
        "Ambiente local que simula serviços da AWS para desenvolvimento e testes.",
        runtime="Local / cloud emulator",
    ),
    catalog_tool(
        "databasement", "Databasement", "Backup e dados",
        "Aplicação self-hosted para backup e restauração de bancos de dados.",
        runtime="Docker / bancos de dados",
    ),
    catalog_tool(
        "duplicati", "Duplicati", "Backup e dados",
        "Aplicativo de backup criptografado para nuvem, servidores remotos e armazenamento local.",
        runtime="Docker / desktop",
    ),
    catalog_tool(
        "velero", "Velero", "Backup e dados",
        "Plataforma para backup, restauração e migração de clusters Kubernetes.",
        runtime="Kubernetes / CLI",
        platform="Kubernetes",
    ),
    catalog_tool(
        "dory", "Dory", "Infraestrutura",
        "Aplicativo para executar e gerenciar containers Linux no macOS.",
        runtime="macOS / containers", platform="Somente macOS",
        notes="Item exibido conforme a lista solicitada, mas não é compatível com este host Windows.",
    ),
    catalog_tool(
        "docker-android", "docker-android", "Desenvolvimento",
        "Ambiente Android completo executado em container para testes e automação.",
        runtime="Docker / Android emulator",
        hardware="Virtualização e memória adicionais são necessárias para o emulador Android.",
    ),
    catalog_tool(
        "mac-sai", "Mac Sai", "Sistema",
        "Aplicativo macOS para limpeza, otimização e verificação de segurança.",
        runtime="macOS", platform="Somente macOS",
        notes="Item exibido conforme a lista solicitada, mas não é compatível com este host Windows.",
    ),
    catalog_tool(
        "mouzi", "Mouzi", "Sistema",
        "Organizador automático da pasta de downloads para Windows e Linux.",
        runtime="Python / desktop", repo="https://github.com/hsr88/mouzi",
    ),
    catalog_tool(
        "fileexplorer", "FileExplorer", "Sistema",
        "Gerenciador de arquivos desktop construído com Rust e Tauri.",
        runtime="Rust / Tauri",
    ),
    catalog_tool(
        "superfile", "Superfile", "Sistema",
        "Gerenciador de arquivos completo executado no terminal.",
        runtime="Terminal / Go",
    ),
    catalog_tool(
        "veloxdb", "VeloxDB", "Banco de dados",
        "Cliente desktop para consultar, editar e administrar bancos PostgreSQL.",
        runtime="Desktop / PostgreSQL",
    ),
    catalog_tool(
        "bruno", "Bruno", "Desenvolvimento",
        "Cliente desktop para testar APIs REST e GraphQL, alternativa ao Postman.",
        runtime="Desktop / Electron",
    ),
    catalog_tool(
        "voidaccess", "VoidAccess", "Segurança e OSINT",
        "Plataforma self-hosted de investigação OSINT e inteligência de ameaças.",
        runtime="Docker / segurança", repo="https://github.com/KatrielMoses/voidaccess",
    ),
    catalog_tool(
        "maigret", "Maigret", "Segurança e OSINT",
        "Aplicação de investigação de nomes de usuário em diversas plataformas.",
        runtime="Python / CLI",
        notes="Use apenas para pesquisas legítimas e em conformidade com a legislação aplicável.",
    ),
    catalog_tool(
        "scout", "Scout", "Pesquisa e dados",
        "Aplicação de prospecção e enriquecimento de leads a partir de perfis públicos.",
        runtime="Web / dados",
        notes="Use apenas dados públicos com base legal e respeite os termos das plataformas consultadas.",
    ),
    catalog_tool(
        "unblink", "Unblink", "Segurança e vídeo",
        "Sistema self-hosted de videomonitoramento com busca e análise por IA.",
        runtime="Docker / visão / GPU",
        hardware="Armazenamento contínuo e GPU podem ser necessários conforme câmeras e retenção.",
    ),
    catalog_tool(
        "openscholarxiv", "OpenScholarXIV", "Pesquisa acadêmica",
        "Aplicativo para pesquisar, ler, resumir e salvar artigos científicos do arXiv.",
        runtime="Web / LLM",
    ),
    catalog_tool(
        "paperbanana", "PaperBanana", "Pesquisa acadêmica",
        "Aplicação para gerar diagramas acadêmicos e gráficos a partir de artigos e descrições.",
        runtime="Python / LLM", repo="https://github.com/llmsresearch/paperbanana",
    ),
    catalog_tool(
        "olmocr-2", "olmOCR 2", "OCR e documentos",
        "Aplicação para converter PDFs e documentos escaneados em texto ou Markdown estruturado.",
        runtime="Python / OCR / GPU",
    ),
    catalog_tool(
        "textsnap", "TextSnap", "OCR e documentos",
        "Aplicação local que transforma imagens, telas e páginas em texto pesquisável.",
        runtime="Desktop / OCR",
    ),
    catalog_tool(
        "pixelrag", "PixelRAG", "Conhecimento",
        "Sistema de busca visual e RAG baseado em screenshots renderizados de documentos e páginas.",
        runtime="Python / visão / RAG",
    ),
    catalog_tool(
        "hyperextract", "HyperExtract", "Conhecimento",
        "Aplicação para converter documentos em conhecimento estruturado com auxílio de LLMs.",
        runtime="Python / LLM",
    ),
    catalog_tool(
        "graphify", "Graphify", "Conhecimento",
        "Aplicação que transforma projetos e documentos em grafos de conhecimento navegáveis.",
        runtime="Grafo / LLM",
    ),
    catalog_tool(
        "helixdb", "HelixDB", "Banco de dados",
        "Banco de dados completo para grafos, vetores, documentos e memória de agentes.",
        runtime="Banco vetorial / grafo", repo="https://github.com/helixdb/helix-db",
    ),
    catalog_tool(
        "openclaw", "OpenClaw", "Agentes",
        "Assistente pessoal de IA que pode ser executado em dispositivos próprios.",
        runtime="Node / LLM",
    ),
    catalog_tool(
        "autogpt", "AutoGPT", "Agentes",
        "Plataforma para criação e execução de agentes autônomos.",
        runtime="Python / Docker / LLM",
    ),
    catalog_tool(
        "comfyui", "ComfyUI", "Imagem e design",
        "Aplicação visual baseada em nós para geração e processamento de imagens com IA.",
        runtime="Python / GPU",
        hardware="GPU NVIDIA recomendada; VRAM e armazenamento dependem dos modelos escolhidos.",
    ),
    catalog_tool(
        "kilo-code", "Kilo Code", "Desenvolvimento",
        "Agente de programação open source para VS Code, JetBrains e terminal.",
        runtime="Extensão / terminal",
        notes="Integração de editor ou terminal; não é uma aplicação Docker independente no catálogo atual.",
    ),
    catalog_tool(
        "peacock", "Peacock", "Desenvolvimento",
        "Extensão completa do VS Code para identificar projetos por cores diferentes.",
        runtime="Extensão VS Code",
        notes="Extensão de editor exibida conforme a lista solicitada; não é uma aplicação Docker independente.",
    ),
]


TOOLS = DOCKER_TOOLS + CATALOG_ONLY_TOOLS


def get_catalog():
    return {
        "max_running": 0,
        "tools": TOOLS,
        "skipped": [],
        "unverified": [],
    }
