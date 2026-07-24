const state = {
  tools: [],
  system: null,
  operations: [],
  diagnostics: null,
  maintenance: null,
  backups: [],
  settings: {
    view: "grid",
    density: "compact",
    auto_refresh: true,
    prompt_memory: true,
    open_in_new_tab: true,
    low_power_browser: true
    ,max_running_apps: 0
    ,max_ram_gb: 0
    ,max_storage_gb: 0
  },
  view: "overview",
  query: "",
  category: "",
  status: "",
  sort: "name",
  selectedTool: null,
  refreshing: false
};

const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let current = value / 1024;
  let index = 0;
  while (current >= 1024 && index < units.length - 1) {
    current /= 1024;
    index += 1;
  }
  return `${current >= 10 ? current.toFixed(1) : current.toFixed(2)} ${units[index]}`;
}

function formatDate(value) {
  if (!value) return "--";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

function initials(name) {
  return String(name || "AI").split(/\s+/).slice(0, 2).map(part => part[0]).join("").toUpperCase();
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Erro HTTP ${response.status}`);
  return payload;
}

function notify(message, type = "info") {
  const alert = document.createElement("div");
  alert.className = `alert ${type}`;
  alert.textContent = message;
  $("#alert-region").append(alert);
  setTimeout(() => alert.remove(), 4200);
}

function statusLabel(tool) {
  const labels = {
    running: "Ligado",
    starting: "Iniciando",
    stopping: "Desligando",
    stopped: "Desligado",
    error: "Erro",
    installed: "Instalado",
    available: "Instalável",
    source: "Código local",
    blocked: "Bloqueado",
    catalog: "Catálogo",
    installing: "Instalando"
  };
  return labels[tool.status] || labels[tool.availability] || tool.status;
}

function availabilityClass(tool) {
  return ["stopped", "catalog"].includes(tool.status) ? tool.availability : tool.status;
}

function toolCard(tool) {
  const active = ["running", "starting", "stopping"].includes(tool.status);
  const canToggle = tool.can_start && !["error"].includes(tool.status);
  const progress = ["starting", "stopping", "installing"].includes(tool.status)
    ? `<div class="progress-track"><span data-progress="${Math.max(2, tool.progress || 0)}"></span></div>`
    : "";
  const mainAction = tool.can_start
    ? `<button class="access-button" data-action="access" data-id="${escapeHtml(tool.id)}" ${tool.status !== "running" ? "disabled" : ""}>Acessar</button>`
    : tool.can_install
      ? `<button class="install-button" data-action="install-catalog" data-id="${escapeHtml(tool.id)}">Download</button>`
      : tool.repo
        ? `<button class="secondary-button" data-action="open-source" data-id="${escapeHtml(tool.id)}">Ver GitHub</button>`
        : `<button class="secondary-button" disabled>Receita pendente</button>`;
  const removeAction = tool.can_remove
    ? `<button class="danger-button" data-action="remove" data-id="${escapeHtml(tool.id)}">Remover</button>`
    : `<span class="remove-placeholder" aria-hidden="true"></span>`;
  const image = tool.banner
    ? `<span class="tool-initials">${escapeHtml(initials(tool.name))}</span><img src="/api/banner?id=${encodeURIComponent(tool.id)}" alt="" loading="lazy">`
    : `<span class="tool-initials">${escapeHtml(initials(tool.name))}</span>`;
  const resource = tool.resource?.memory_bytes
    ? ` · ${formatBytes(tool.resource.memory_bytes)} RAM`
    : "";
  return `
    <article class="tool-card" data-tool-id="${escapeHtml(tool.id)}">
      <div class="tool-banner">
        ${image}
        <button class="icon-button favorite-button ${tool.favorite ? "active" : ""}" data-action="favorite" data-id="${escapeHtml(tool.id)}" title="${tool.favorite ? "Remover dos favoritos" : "Adicionar aos favoritos"}" aria-label="${tool.favorite ? "Remover dos favoritos" : "Adicionar aos favoritos"}">F</button>
      </div>
      <div class="tool-body">
        <div class="tool-title-row">
          <div class="tool-title">
            <strong title="${escapeHtml(tool.name)}">${escapeHtml(tool.name)}</strong>
            <span>${escapeHtml(tool.category)} · ${escapeHtml(tool.runtime || "Local")}</span>
          </div>
          <span class="status-pill ${availabilityClass(tool)}">${escapeHtml(statusLabel(tool))}</span>
        </div>
        <p class="tool-description">${escapeHtml(tool.description)}</p>
        <div class="tool-meta">${escapeHtml(tool.platform || "Windows")}${escapeHtml(resource)}</div>
        <div class="tool-message" title="${escapeHtml(tool.message)}">${escapeHtml(tool.message || "")}</div>
        ${progress}
        <div class="tool-actions">
          ${mainAction}
          ${removeAction}
          <label class="switch" title="${active ? "Desligar" : "Ligar"}">
            <input type="checkbox" data-action="toggle" data-id="${escapeHtml(tool.id)}" ${active ? "checked" : ""} ${canToggle ? "" : "disabled"}>
            <span></span>
          </label>
          <button class="icon-button" data-action="details" data-id="${escapeHtml(tool.id)}" title="Detalhes" aria-label="Detalhes">?</button>
        </div>
      </div>
    </article>`;
}

function filteredTools() {
  const query = state.query.trim().toLocaleLowerCase("pt-BR");
  let rows = state.tools.filter(tool => {
    if (state.category && tool.category !== state.category) return false;
    if (state.status === "installed" && tool.availability !== "installed") return false;
    if (state.status === "available" && !["available", "source"].includes(tool.availability)) return false;
    if (state.status === "catalog" && tool.availability !== "catalog") return false;
    if (state.status === "blocked" && tool.availability !== "blocked") return false;
    if (!query) return true;
    return [tool.name, tool.category, tool.description, tool.runtime]
      .some(value => String(value || "").toLocaleLowerCase("pt-BR").includes(query));
  });
  rows.sort((a, b) => {
    if (state.sort === "recent") {
      return String(b.recent?.last_accessed || "").localeCompare(String(a.recent?.last_accessed || ""));
    }
    if (state.sort === "status") return statusLabel(a).localeCompare(statusLabel(b), "pt-BR");
    return a.name.localeCompare(b.name, "pt-BR");
  });
  return rows;
}

function renderSummary() {
  const system = state.system || {};
  const disk = system.disk || {};
  const memory = system.memory || {};
  const gpu = system.gpu?.cards?.[0];
  const items = [
    {
      label: "Aplicativos",
      value: system.tool_count ?? state.tools.length,
      note: `${state.tools.filter(tool => tool.availability === "installed").length} instalados`
    },
    {
      label: "Em execução",
      value: system.max_running ? `${system.running || 0}/${system.max_running}` : `${system.running || 0}/∞`,
      note: `${system.active_operations || 0} operações ativas`,
      meter: system.max_running ? ((system.running || 0) / system.max_running) * 100 : 0
    },
    {
      label: `Disco ${(system.root || "").slice(0, 2) || "local"}`,
      value: `${disk.free_gb ?? "--"} GB`,
      note: `${disk.percent ?? 0}% utilizado`,
      meter: disk.percent || 0,
      warning: (disk.percent || 0) > 85
    },
    {
      label: gpu ? "GPU" : "Memória",
      value: gpu ? `${gpu.utilization}%` : `${memory.percent ?? "--"}%`,
      note: gpu ? `${gpu.name} · ${gpu.memory_used_mb}/${gpu.memory_total_mb} MB` : `${formatBytes(memory.available_bytes)} livres`,
      meter: gpu ? gpu.utilization : memory.percent
    }
  ];
  $("#summary-grid").innerHTML = items.map(item => `
    <div class="summary-item">
      <span>${escapeHtml(item.label)}</span>
      <strong>${escapeHtml(item.value)}</strong>
      <small title="${escapeHtml(item.note)}">${escapeHtml(item.note)}</small>
      ${item.meter !== undefined ? `<div class="meter ${item.warning ? "warning" : ""}"><span data-progress="${Math.min(100, item.meter || 0)}"></span></div>` : ""}
    </div>`).join("");
  $("#nav-installed").textContent = state.tools.filter(tool => tool.availability === "installed").length;
  $("#nav-operations").textContent = system.active_operations || 0;
  $("#install-free-space").textContent = disk.free_gb !== undefined ? `${disk.free_gb} GB` : "--";
  $("#root-path").textContent = system.root || "Raiz não identificada";
}

function renderQuickTools() {
  const favorites = state.tools.filter(tool => tool.favorite);
  const recent = state.tools.filter(tool => tool.recent).sort((a, b) =>
    String(b.recent.last_accessed).localeCompare(String(a.recent.last_accessed)));
  const quick = [...favorites, ...recent.filter(tool => !tool.favorite)].slice(0, 6);
  $("#quick-tools").innerHTML = quick.length
    ? quick.map(toolCard).join("")
    : `<div class="empty-state"><strong>Nenhum favorito ou acesso recente</strong><span>Marque aplicativos com F.</span></div>`;
}

function renderRuntimes() {
  const runtimes = state.system?.runtimes || [];
  $("#runtime-list").innerHTML = runtimes.map(runtime => `
    <div class="runtime-row">
      <div><strong>${escapeHtml(runtime.name)}</strong><span title="${escapeHtml(runtime.path)}">${escapeHtml(runtime.path)}</span></div>
      <span class="runtime-state ${runtime.ok ? "ok" : ""}" title="${runtime.ok ? "Disponível" : "Ausente"}"></span>
    </div>`).join("");
}

function renderCategories() {
  const categories = [...new Set(state.tools.map(tool => tool.category).filter(Boolean))].sort((a, b) => a.localeCompare(b, "pt-BR"));
  $("#category-filters").innerHTML = [
    `<button class="${state.category ? "" : "active"}" data-category="">Todos</button>`,
    ...categories.map(category => `<button class="${state.category === category ? "active" : ""}" data-category="${escapeHtml(category)}">${escapeHtml(category)}</button>`)
  ].join("");
}

function renderApps() {
  const tools = filteredTools();
  const container = $("#all-tools");
  container.className = `tool-grid ${state.settings.view === "list" ? "list" : ""}`;
  container.innerHTML = tools.length ? tools.map(toolCard).join("") : `<div class="empty-state"><strong>Nenhum resultado</strong><span>Revise os filtros ativos.</span></div>`;
  $("#tool-result-count").textContent = tools.length;
  $("#status-filter").value = state.status;
  $("#sort-filter").value = state.sort;
  $$("[data-layout]").forEach(button => button.classList.toggle("active", button.dataset.layout === state.settings.view));
}

function renderInstalled() {
  const tools = state.tools.filter(tool => tool.availability === "installed");
  $("#installed-tools").innerHTML = tools.map(toolCard).join("");
  $("#installed-empty").hidden = Boolean(tools.length);
  $("#installed-running-label").textContent = `${tools.filter(tool => ["running", "starting", "stopping"].includes(tool.status)).length} em execução`;
}

function renderInstallable() {
  const tools = state.tools.filter(tool => tool.can_install);
  $("#installable-tools").innerHTML = tools.length
    ? tools.map(toolCard).join("")
    : `<div class="empty-state"><strong>Catálogo preparado</strong><span>Use o formulário para adicionar outro repositório.</span></div>`;
}

function operationRow(operation, compact = false) {
  if (compact) {
    return `<div class="activity-row">
      <div><strong>${escapeHtml(operation.tool_id)} · ${escapeHtml(operation.kind)}</strong><span>${escapeHtml(operation.message)} · ${formatDate(operation.created_at)}</span></div>
      <span class="status-pill ${escapeHtml(operation.status)}">${escapeHtml(operation.status)}</span>
    </div>`;
  }
  const cancel = ["queued", "running", "cancelling"].includes(operation.status)
    ? `<button class="icon-button" data-action="cancel-operation" data-id="${escapeHtml(operation.operation_id)}" title="Cancelar" aria-label="Cancelar">X</button>`
    : "";
  return `<div class="table-row">
    <span class="truncate" title="${escapeHtml(operation.operation_id)}">${escapeHtml(operation.operation_id.slice(0, 12))}</span>
    <strong class="truncate">${escapeHtml(operation.tool_id)}</strong>
    <span>${escapeHtml(operation.kind)}</span>
    <span class="truncate" title="${escapeHtml(operation.message)}">${escapeHtml(operation.message)}</span>
    <div class="table-progress"><div class="progress-track"><span data-progress="${operation.progress || 0}"></span></div><span>${operation.progress || 0}%</span></div>
    <span class="status-pill ${escapeHtml(operation.status)}">${escapeHtml(operation.status)}</span>
    ${cancel}
  </div>`;
}

function renderOperations() {
  $("#recent-operations").innerHTML = state.operations.slice(0, 6).map(operation => operationRow(operation, true)).join("")
    || `<div class="activity-row"><div><strong>Sem operações</strong><span>A fila está vazia.</span></div></div>`;
  $("#operations-table").innerHTML = `
    <div class="table-row header"><span>ID</span><span>Aplicativo</span><span>Tipo</span><span>Mensagem</span><span>Progresso</span><span>Estado</span><span></span></div>
    ${state.operations.map(operation => operationRow(operation)).join("")}`;
}

function renderMaintenance() {
  const storage = state.diagnostics?.storage || [];
  const total = storage.reduce((sum, item) => sum + Number(item.bytes || 0), 0);
  const preview = state.maintenance || { count: 0, bytes: 0 };
  $("#maintenance-summary").innerHTML = [
    ["Armazenamento AICorte", formatBytes(total), `${storage.length} áreas medidas`],
    ["Limpeza disponível", formatBytes(preview.bytes), `${preview.count || 0} arquivos`],
    ["Backups", state.backups.length, state.backups[0] ? formatDate(state.backups[0].name.match(/\d{8}-\d{6}/)?.[0] || "") : "Nenhum backup"]
  ].map(([label, value, note]) => `<div class="summary-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(note)}</small></div>`).join("");
  $("#cleanup-preview").innerHTML = `<strong>${formatBytes(preview.bytes)}</strong><span>${preview.count || 0} arquivos qualificados para limpeza.</span>`;
  const checks = state.diagnostics?.checks || [];
  $("#diagnostic-list").innerHTML = checks.map(check => `
    <div class="check-row">
      <div><strong>${escapeHtml(check.label)}</strong><span title="${escapeHtml(check.detail)}">${escapeHtml(check.detail)}</span></div>
      <span class="check-state ${check.ok ? "ok" : ""}"></span>
    </div>`).join("");
  $("#backup-list").innerHTML = state.backups.length
    ? state.backups.slice(0, 12).map(item => `<div class="backup-row"><div><strong>${escapeHtml(item.name)}</strong><span>${formatBytes(item.bytes)}</span></div><span class="status-pill neutral">SQLite</span></div>`).join("")
    : `<div class="backup-row"><div><strong>Nenhum backup</strong><span>O primeiro backup será armazenado na raiz selecionada.</span></div></div>`;
  const installed = state.tools.filter(tool => tool.availability === "installed");
  $("#maintenance-apps").innerHTML = installed.length ? installed.map(tool => `
    <div class="maintenance-app-row">
      <div><strong>${escapeHtml(tool.name)}</strong><span>${escapeHtml(tool.message || statusLabel(tool))}</span></div>
      <span class="status-pill ${availabilityClass(tool)}">${escapeHtml(statusLabel(tool))}</span>
      <button class="secondary-button" data-action="update" data-id="${escapeHtml(tool.id)}" ${tool.can_update ? "" : "disabled"}>Atualizar</button>
      <button class="secondary-button" data-action="repair" data-id="${escapeHtml(tool.id)}" ${tool.can_update ? "" : "disabled"}>Reparar</button>
    </div>`).join("") : `<div class="empty-state"><strong>Nenhum aplicativo instalado</strong><span>Não há itens para manter.</span></div>`;
}

function renderSettings() {
  const form = $("#settings-form");
  Object.entries(state.settings).forEach(([key, value]) => {
    const field = form.elements.namedItem(key);
    if (!field) return;
    if (field.type === "checkbox") field.checked = Boolean(value);
    else field.value = value;
  });
  document.body.classList.toggle("density-comfortable", state.settings.density === "comfortable");
}

function renderAll() {
  renderSummary();
  renderQuickTools();
  renderRuntimes();
  renderCategories();
  renderApps();
  renderInstalled();
  renderInstallable();
  renderOperations();
  renderMaintenance();
  renderSettings();
  applyProgress();
}

function applyProgress(root = document) {
  root.querySelectorAll("[data-progress]").forEach(element => {
    const value = Math.max(0, Math.min(100, Number(element.dataset.progress || 0)));
    element.style.width = `${value}%`;
  });
}

async function refresh(silent = false) {
  if (state.refreshing) return;
  state.refreshing = true;
  try {
    const [tools, operations] = await Promise.all([
      api("/api/tools"),
      api("/api/operations?limit=100")
    ]);
    state.tools = tools.tools || [];
    state.system = tools.system || {};
    state.operations = operations.operations || [];
    $("#connection-dot").className = "connection-dot ok";
    $("#connection-label").textContent = "Conectado";
    renderAll();
  } catch (error) {
    $("#connection-dot").className = "connection-dot error";
    $("#connection-label").textContent = "Sem conexão";
    if (!silent) notify(error.message, "error");
  } finally {
    state.refreshing = false;
  }
}

async function refreshMaintenance() {
  try {
    const [diagnostics, maintenance, backups] = await Promise.all([
      api("/api/diagnostics"),
      api("/api/maintenance"),
      api("/api/backups")
    ]);
    state.diagnostics = diagnostics;
    state.maintenance = maintenance;
    state.backups = backups.backups || [];
    renderMaintenance();
  } catch (error) {
    notify(error.message, "error");
  }
}

function switchView(view) {
  state.view = view;
  const names = {
    overview: ["CENTRAL LOCAL", "Início"],
    apps: ["CATÁLOGO", "Explorar"],
    installed: ["AMBIENTE LOCAL", "Aplicativos"],
    install: ["PROVISIONAMENTO", "Instalar"],
    maintenance: ["ARMAZENAMENTO", "Manutenção"],
    settings: ["PREFERÊNCIAS", "Configurações"]
  };
  $$(".view").forEach(section => section.classList.toggle("active", section.id === `view-${view}`));
  $$(".nav-item").forEach(button => button.classList.toggle("active", button.dataset.view === view));
  $("#view-eyebrow").textContent = names[view][0];
  $("#view-title").textContent = names[view][1];
  document.body.classList.remove("menu-open");
  if (location.hash !== `#${view}`) history.replaceState(null, "", `#${view}`);
  if (view === "maintenance") refreshMaintenance();
}

async function postTool(action, toolId, payload = {}) {
  const result = await api(`/api/${action}/${encodeURIComponent(toolId)}`, {
    method: "POST",
    body: JSON.stringify(payload)
  });
  await refresh(true);
  return result;
}

async function toggleTool(toolId, enabled) {
  const tool = state.tools.find(item => item.id === toolId);
  if (!tool) return;
  try {
    await postTool(enabled ? "start" : "stop", toolId);
    notify(enabled ? `${tool.name} está iniciando.` : `${tool.name} está desligando.`, "success");
  } catch (error) {
    notify(error.message, "error");
    await refresh(true);
  }
}

async function accessTool(toolId) {
  const tool = state.tools.find(item => item.id === toolId);
  if (!tool?.url) return;
  try {
    await postTool("access", toolId);
    const target = state.settings.open_in_new_tab ? `tools-hub:${tool.id}` : "_self";
    window.open(tool.url, target);
  } catch (error) {
    notify(error.message, "error");
  }
}

async function installCatalog(toolId) {
  const tool = state.tools.find(item => item.id === toolId);
  if (!tool) return;
  const confirmed = window.confirm(`Baixar e instalar ${tool.name} com Docker na raiz selecionada?`);
  if (!confirmed) return;
  try {
    const result = await postTool("install", toolId, { trusted: true });
    notify(`Operação ${result.operation_id.slice(0, 8)} adicionada à fila.`, "success");
    switchView("maintenance");
  } catch (error) {
    notify(error.message, "error");
  }
}

async function showDetails(toolId) {
  const tool = state.tools.find(item => item.id === toolId);
  if (!tool) return;
  state.selectedTool = tool;
  $("#dialog-category").textContent = `${tool.category} · ${statusLabel(tool)}`;
  $("#dialog-title").textContent = tool.name;
  const resource = tool.resource || {};
  $("#dialog-content").innerHTML = `
    <p class="detail-description">${escapeHtml(tool.description)}</p>
    <div class="detail-grid">
      <div class="detail-item"><span>Runtime</span><strong>${escapeHtml(tool.runtime || "--")}</strong></div>
      <div class="detail-item"><span>Endereço</span><strong>${escapeHtml(tool.url || "--")}</strong></div>
      <div class="detail-item"><span>Projeto</span><strong>${escapeHtml(tool.path || "--")}</strong></div>
      <div class="detail-item"><span>Hardware</span><strong>${escapeHtml(tool.hardware || "--")}</strong></div>
      <div class="detail-item"><span>PID</span><strong>${escapeHtml(tool.pid || "--")}</strong></div>
      <div class="detail-item"><span>Memória</span><strong>${resource.memory_bytes ? formatBytes(resource.memory_bytes) : "--"}</strong></div>
    </div>
    <div class="detail-actions">
      ${tool.status === "running" ? `<button class="primary-button" data-action="access" data-id="${escapeHtml(tool.id)}">Acessar</button>` : ""}
      ${tool.repo ? `<button class="secondary-button" data-action="open-source" data-id="${escapeHtml(tool.id)}">Ver GitHub</button>` : ""}
      ${tool.path ? `<button class="secondary-button" data-action="open-folder" data-id="${escapeHtml(tool.id)}">Abrir pasta</button>` : ""}
      ${tool.can_update ? `<button class="secondary-button" data-action="update" data-id="${escapeHtml(tool.id)}">Atualizar</button>` : ""}
      ${tool.can_remove ? `<button class="danger-button" data-action="remove" data-id="${escapeHtml(tool.id)}">Remover</button>` : ""}
      <button class="secondary-button" data-action="clear-log" data-id="${escapeHtml(tool.id)}">Limpar log</button>
    </div>
    <p class="detail-description">${escapeHtml(tool.notes || "")}</p>
    <pre id="detail-log" class="log-view">Carregando log...</pre>`;
  $("#tool-dialog").showModal();
  try {
    const payload = await api(`/api/logs?id=${encodeURIComponent(tool.id)}`);
    $("#detail-log").textContent = payload.log || "Log vazio.";
  } catch (error) {
    $("#detail-log").textContent = error.message;
  }
}

document.addEventListener("click", async event => {
  const nav = event.target.closest("[data-view]");
  if (nav) switchView(nav.dataset.view);
  const go = event.target.closest("[data-go]");
  if (go) switchView(go.dataset.go);
  const category = event.target.closest("[data-category]");
  if (category) {
    state.category = category.dataset.category;
    renderCategories();
    renderApps();
    applyProgress($("#view-apps"));
  }
  const layout = event.target.closest("[data-layout]");
  if (layout) {
    state.settings.view = layout.dataset.layout;
    renderApps();
    applyProgress($("#view-apps"));
  }
  const action = event.target.closest("[data-action]");
  if (!action) return;
  const id = action.dataset.id;
  try {
    if (action.dataset.action === "access") await accessTool(id);
    if (action.dataset.action === "open-source") {
      const tool = state.tools.find(item => item.id === id);
      if (tool?.repo) window.open(tool.repo, "_blank", "noopener");
    }
    if (action.dataset.action === "details") await showDetails(id);
    if (action.dataset.action === "install-catalog") await installCatalog(id);
    if (action.dataset.action === "favorite") {
      const tool = state.tools.find(item => item.id === id);
      await postTool("favorite", id, { enabled: !tool.favorite });
    }
    if (action.dataset.action === "cancel-operation") {
      await api(`/api/operations/${encodeURIComponent(id)}`, { method: "POST", body: JSON.stringify({ action: "cancel" }) });
      notify("Cancelamento solicitado.");
      await refresh(true);
    }
    if (action.dataset.action === "open-folder") await postTool("open-folder", id);
    if (action.dataset.action === "clear-log") {
      await postTool("logs-clear", id);
      $("#detail-log").textContent = "Log vazio.";
    }
    if (action.dataset.action === "update") {
      if (window.confirm("Atualizar com rollback automático em caso de falha?")) {
        await postTool("update", id, { trusted: true });
        $("#tool-dialog").close();
        switchView("maintenance");
      }
    }
    if (action.dataset.action === "repair") {
      if (window.confirm("Recriar a instalação Docker preservando dados persistentes?")) {
        await postTool("repair", id, { trusted: true });
        $("#tool-dialog").close();
        switchView("maintenance");
      }
    }
    if (action.dataset.action === "remove") {
      if (window.confirm("Remover containers e imagens? Modelos, workflows e configurações serão preservados.")) {
        await postTool("remove", id);
        $("#tool-dialog").close();
        switchView("maintenance");
      }
    }
  } catch (error) {
    notify(error.message, "error");
  }
});

document.addEventListener("error", event => {
  if (event.target instanceof HTMLImageElement && event.target.closest(".tool-banner")) {
    event.target.remove();
  }
}, true);

document.addEventListener("change", event => {
  const toggle = event.target.closest('[data-action="toggle"]');
  if (toggle) toggleTool(toggle.dataset.id, toggle.checked);
});

$("#main-nav").addEventListener("click", event => {
  const button = event.target.closest("[data-view]");
  if (button) switchView(button.dataset.view);
});
$("#menu-toggle").addEventListener("click", () => document.body.classList.toggle("menu-open"));
$("#refresh-button").addEventListener("click", () => refresh());
$("#refresh-operations").addEventListener("click", () => refresh());
$("#global-search").addEventListener("input", event => {
  state.query = event.target.value;
  if (state.view !== "apps") switchView("apps");
  renderApps();
});
$("#status-filter").addEventListener("change", event => { state.status = event.target.value; renderApps(); });
$("#sort-filter").addEventListener("change", event => { state.sort = event.target.value; renderApps(); });
$("#close-dialog").addEventListener("click", () => $("#tool-dialog").close());
$("#tool-dialog").addEventListener("click", event => {
  if (event.target === $("#tool-dialog")) $("#tool-dialog").close();
});

$("#analyze-install").addEventListener("click", async () => {
  const form = new FormData($("#install-form"));
  const repo = form.get("repo");
  try {
    const result = await api("/api/install/analyze", { method: "POST", body: JSON.stringify({ repo }) });
    $("#install-analysis").className = "analysis-result success";
    $("#install-analysis").textContent = `Repositório válido. ${result.plan.length} fases serão executadas na raiz selecionada.`;
    $("#install-plan-list").innerHTML = result.plan.map(item => `<li>${escapeHtml(item)}</li>`).join("");
    $("#install-free-space").textContent = formatBytes(result.free_bytes);
  } catch (error) {
    $("#install-analysis").className = "analysis-result error";
    $("#install-analysis").textContent = error.message;
  }
});

$("#install-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const payload = Object.fromEntries(form.entries());
  payload.trusted = form.get("trusted") === "on";
  if (payload.port) payload.port = Number(payload.port);
  try {
    const result = await api("/api/install/custom", { method: "POST", body: JSON.stringify(payload) });
    notify(`Instalação ${result.operation_id.slice(0, 8)} adicionada à fila.`, "success");
    event.currentTarget.reset();
    switchView("maintenance");
    await refresh(true);
  } catch (error) {
    notify(error.message, "error");
  }
});

$("#run-diagnostics").addEventListener("click", refreshMaintenance);
$("#run-cleanup").addEventListener("click", async () => {
  if (!window.confirm("Remover apenas os arquivos listados na prévia?")) return;
  const actions = [];
  if ($("#cleanup-files").checked) actions.push("safe-files");
  try {
    const result = await api("/api/maintenance/run", {
      method: "POST",
      body: JSON.stringify({ actions, confirmation: "LIMPAR" })
    });
    notify(`${result.removed} arquivos removidos; ${formatBytes(result.freed_bytes)} liberados.`, "success");
    await refreshMaintenance();
  } catch (error) {
    notify(error.message, "error");
  }
});
$("#create-backup").addEventListener("click", async () => {
  try {
    await api("/api/backup", { method: "POST", body: "{}" });
    notify("Backup SQLite concluído.", "success");
    await refreshMaintenance();
  } catch (error) {
    notify(error.message, "error");
  }
});

$("#settings-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = {
    auto_refresh: form.auto_refresh.checked,
    prompt_memory: form.prompt_memory.checked,
    open_in_new_tab: form.open_in_new_tab.checked,
    low_power_browser: form.low_power_browser.checked,
    density: form.density.value,
    view: form.view.value
    ,max_running_apps: Number(form.max_running_apps.value || 0)
    ,max_ram_gb: Number(form.max_ram_gb.value || 0)
    ,max_storage_gb: Number(form.max_storage_gb.value || 0)
  };
  try {
    const result = await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    state.settings = { ...state.settings, ...result.settings };
    renderAll();
    notify("Configurações salvas.", "success");
  } catch (error) {
    notify(error.message, "error");
  }
});

async function loadSettings() {
  try {
    const result = await api("/api/settings");
    state.settings = { ...state.settings, ...(result.settings || {}) };
  } catch {
    // Defaults remain active.
  }
}

async function boot() {
  await loadSettings();
  await Promise.all([refresh(), refreshMaintenance()]);
  const requestedView = location.hash.slice(1);
  if (["overview", "apps", "installed", "maintenance", "settings"].includes(requestedView)) switchView(requestedView);
  setInterval(() => {
    if (state.settings.auto_refresh && document.visibilityState === "visible") refresh(true);
  }, 3000);
}

window.addEventListener("hashchange", () => {
  const view = location.hash.slice(1);
  if (["overview", "apps", "installed", "maintenance", "settings"].includes(view)) switchView(view);
});

boot();
