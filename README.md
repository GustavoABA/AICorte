# AICorte

Hub nativo para instalar, executar e manter aplicativos locais em Docker. O `AICorte.exe` usa WinForms com Microsoft Edge WebView2, sem janela de terminal, barra de endereco ou Chrome for Testing.

## Primeiro uso

1. Execute `AICorte.exe`.
2. Escolha ou crie a pasta raiz onde os dados devem ficar.
3. Clique em **Baixar e configurar**.
4. Quando a verificacao chegar a 100%, abra o painel.

O assistente instala, quando necessario, Git portatil, CPython 3.11, Docker CLI e Docker Compose dentro da raiz escolhida. O Docker Engine para containers Linux usa uma distribuicao WSL2 oculta, com o disco virtual em `app\runtime\docker-wsl`.

> WSL2 e WebView2 sao componentes do Windows. Habilitar WSL2 pode exigir permissao de administrador e reinicializacao. O restante dos runtimes, projetos, modelos e estados fica na raiz selecionada.

## Navegacao

- **Inicio**: disco, memoria, GPU, atividade e runtimes.
- **Explorar**: catalogo GitHub com download e instalacao Docker.
- **Aplicativos**: somente ferramentas instaladas, com acesso, switch e remocao.
- **Manutencao**: diagnostico, limpeza, backup, atualizacao, reparo e fila de operacoes.
- **Configuracoes**: limites opcionais de apps simultaneos, RAM por container e armazenamento. `0` significa ilimitado.

O catalogo inicial contem 59 itens em Explorar. Ollama, Open-LLM-VTuber, n8n, Open WebUI, Langflow, Memos e ntfy possuem receitas Docker verificadas. Os demais aparecem como itens de catalogo e recebem o link do GitHub quando a origem foi confirmada; somente receitas executaveis recebem o botao de download.

## Estrutura

```text
<raiz escolhida>
|-- AICorte.exe
|-- AI                 modelos de IA
|-- app
|   |-- downloads      pacotes baixados
|   |-- runtime        Python, Git, Docker e dados do Engine
|   `-- tmp            arquivos temporarios
|-- Principal
|   |-- docker         receitas Compose do catalogo
|   |-- native         shell WinForms/WebView2
|   |-- state          SQLite e dados persistentes
|   `-- logs           logs do painel e das ferramentas
`-- PROJETOS           repositorios clonados quando necessarios
```

O painel escuta apenas em `http://127.0.0.1:8787`. Ferramentas abrem em janelas WebView2 separadas. Na rede Docker `aicorte`, os containers podem acessar o Ollama em `http://aicorte-ollama:11434`.

## Desenvolvimento

Compile o executavel com:

```powershell
Principal\native\build-native.ps1
```

Execute os testes com o Python portatil da raiz:

```powershell
app\runtime\uv-python\cpython-3.11-windows-x86_64-none\python.exe -m unittest discover -s Principal\tests -v
```
