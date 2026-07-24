const $ = (selector) => document.querySelector(selector);
const state = { models: [], running: new Set(), selected: '', pullTimer: null };

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function toast(message) {
  const node = $('#toast'); node.textContent = message; node.classList.add('show');
  setTimeout(() => node.classList.remove('show'), 3200);
}

function bytes(value) {
  const gb = Number(value || 0) / 1073741824;
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${(gb * 1024).toFixed(0)} MB`;
}

function escapeHtml(value) {
  const node = document.createElement('div'); node.textContent = value ?? ''; return node.innerHTML;
}

function addMessage(role, content, pending = false) {
  const node = document.createElement('div');
  node.className = `message ${role}${pending ? ' pending' : ''}`;
  node.textContent = content; $('#messages').appendChild(node); node.scrollIntoView({ block: 'end' });
  return node;
}

async function loadHistory() {
  $('#messages').innerHTML = '';
  if (!state.selected) return;
  const data = await api(`/api/history?model=${encodeURIComponent(state.selected)}`);
  data.messages.forEach((message) => addMessage(message.role, message.content));
}

async function refresh() {
  try {
    const data = await api('/api/status');
    state.models = data.models || [];
    state.running = new Set((data.running || []).map((item) => item.name || item.model));
    $('#version').textContent = `v${data.version}`;
    $('#statusDot').classList.add('online'); $('#statusText').textContent = 'Online';
    const select = $('#chatModel'); const previous = state.selected || select.value;
    select.innerHTML = state.models.map((model) => `<option value="${escapeHtml(model.name)}">${escapeHtml(model.name)}</option>`).join('');
    state.selected = state.models.some((model) => model.name === previous) ? previous : (state.models[0]?.name || '');
    select.value = state.selected;
    renderModels();
  } catch (error) {
    $('#statusDot').classList.remove('online'); $('#statusText').textContent = 'Offline'; toast(error.message);
  }
}

function renderModels() {
  $('#modelRows').innerHTML = state.models.map((model) => {
    const running = state.running.has(model.name);
    return `<tr><td><strong>${escapeHtml(model.name)}</strong></td><td>${bytes(model.size)}</td><td>${escapeHtml(model.details?.family || '-')}</td><td>${running ? '<span class="badge">Carregado</span>' : 'Parado'}</td><td class="actions"><button data-action="${running ? 'unload' : 'load'}" data-model="${escapeHtml(model.name)}">${running ? 'Parar' : 'Carregar'}</button><button class="danger" data-action="delete" data-model="${escapeHtml(model.name)}">Remover</button></td></tr>`;
  }).join('') || '<tr><td colspan="5">Nenhum modelo instalado.</td></tr>';
}

async function modelAction(action, model) {
  if (action === 'delete') {
    if (!confirm(`Remover ${model} do disco?`)) return;
    await api(`/api/models?model=${encodeURIComponent(model)}`, { method: 'DELETE' });
  } else {
    await api('/api/model/action', { method: 'POST', body: JSON.stringify({ model, action }) });
  }
  await refresh(); toast('Operacao concluida.');
}

async function pollPull(id) {
  const task = await api(`/api/tasks/${id}`);
  const percent = task.total ? Math.round(task.completed / task.total * 100) : 0;
  $('#pullStatus').textContent = `${task.model}: ${task.status}`; $('#pullPercent').textContent = `${percent}%`; $('#pullBar').value = percent;
  if (task.done) {
    clearInterval(state.pullTimer); state.pullTimer = null;
    if (task.error) toast(task.error); else { toast('Modelo baixado.'); await refresh(); }
  }
}

document.querySelectorAll('.tab').forEach((button) => button.addEventListener('click', () => {
  document.querySelectorAll('.tab,.view').forEach((node) => node.classList.remove('active'));
  button.classList.add('active'); $(`#${button.dataset.tab}`).classList.add('active');
}));

$('#chatModel').addEventListener('change', async (event) => { state.selected = event.target.value; await loadHistory(); });
$('#chatForm').addEventListener('submit', async (event) => {
  event.preventDefault(); const message = $('#prompt').value.trim(); if (!message || !state.selected) return;
  addMessage('user', message); $('#prompt').value = ''; const pending = addMessage('assistant', 'Gerando resposta...', true);
  try {
    const data = await api('/api/chat', { method: 'POST', body: JSON.stringify({ model: state.selected, message, temperature: Number($('#temperature').value) }) });
    pending.classList.remove('pending'); pending.textContent = data.message || '(resposta vazia)';
  } catch (error) { pending.remove(); addMessage('assistant', `Erro: ${error.message}`); }
});
$('#clearChat').addEventListener('click', async () => { if (!state.selected) return; await api('/api/clear', { method: 'POST', body: JSON.stringify({ model: state.selected }) }); await loadHistory(); });
$('#refreshModels').addEventListener('click', refresh);
$('#modelRows').addEventListener('click', (event) => { const button = event.target.closest('button[data-action]'); if (button) modelAction(button.dataset.action, button.dataset.model).catch((error) => toast(error.message)); });
$('#pullForm').addEventListener('submit', async (event) => {
  event.preventDefault(); const model = $('#pullModel').value.trim(); const task = await api('/api/pull', { method: 'POST', body: JSON.stringify({ model }) });
  $('#pullProgress').hidden = false; await pollPull(task.id); state.pullTimer = setInterval(() => pollPull(task.id).catch((error) => toast(error.message)), 900);
});
$('#commandForm').addEventListener('submit', async (event) => {
  event.preventDefault(); const command = $('#command').value.trim(); if (!command) return;
  const terminal = $('#terminal'); const line = document.createElement('div'); line.className = 'cmd'; line.textContent = `ollama> ${command}`; terminal.appendChild(line); $('#command').value = '';
  try {
    const data = await api('/api/command', { method: 'POST', body: JSON.stringify({ command }) });
    if (data.output === '__CLEAR__') terminal.innerHTML = ''; else { const out = document.createElement('div'); out.textContent = data.output; terminal.appendChild(out); }
    if (/^(pull|rm|load|stop)\b/i.test(command)) setTimeout(refresh, 700);
  } catch (error) { const out = document.createElement('div'); out.className = 'err'; out.textContent = error.message; terminal.appendChild(out); }
  terminal.scrollTop = terminal.scrollHeight;
});

refresh().then(loadHistory);
