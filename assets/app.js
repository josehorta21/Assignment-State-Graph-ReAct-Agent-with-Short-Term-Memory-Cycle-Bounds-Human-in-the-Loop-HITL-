(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  let threadId = sessionStorage.getItem('stateguard_thread');
  let socket = null;
  let lastVersion = -1;
  let pending = false;
  let lastState = null;

  function refreshComposer() {
    const disabled = pending || (lastState && lastState.status === 'PAUSED');
    $('send').disabled = Boolean(disabled);
    $('prompt').disabled = Boolean(disabled);
  }

  function note(message, isError = false) {
    const target = $('notification');
    target.textContent = message;
    target.style.color = isError ? '#ff9e9e' : '#79e5cd';
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  }

  function render(snapshot) {
    if (!snapshot || !snapshot.state) return;
    const state = snapshot.state;
    if (state.thread_id !== threadId) return;
    if (snapshot.version < lastVersion) return;
    lastVersion = snapshot.version;
    lastState = state;
    $('threadLabel').textContent = state.thread_id;
    $('statusValue').textContent = state.status;
    $('statusDot').className = 'status-dot ' + state.status.toLowerCase();
    $('cycleValue').textContent = state.iteration_count;
    $('activeNode').textContent = state.current_node;
    $('versionTag').textContent = `v${snapshot.version}`;
    $('jsonState').textContent = JSON.stringify(state, null, 2);

    document.querySelectorAll('.graph-node').forEach((node) => {
      node.classList.toggle('active', node.dataset.node === state.current_node);
    });

    const chat = $('chat');
    chat.replaceChildren();
    if (state.messages.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'empty-chat';
      const logo = document.createElement('div');
      logo.className = 'empty-orbit';
      logo.textContent = '✧';
      const title = document.createElement('strong');
      title.textContent = 'Ready for your first request';
      const subtitle = document.createElement('span');
      subtitle.textContent = 'Choose a scenario or describe an operation below.';
      empty.append(logo, title, subtitle);
      chat.append(empty);
    }
    const visibleMessages = state.messages.filter(m => m.role !== 'system').slice(-20);
    for (const msg of visibleMessages) {
      const row = document.createElement('div');
      row.className = `chat-row ${msg.role}`;
      const avatar = document.createElement('div');
      avatar.className = 'bubble-icon';
      avatar.textContent = msg.role === 'assistant' ? '✦' : msg.role === 'tool' ? '⌘' : 'U';
      const container = document.createElement('div');
      const bubble = document.createElement('div');
      bubble.className = 'bubble';
      bubble.textContent = msg.content;
      const meta = document.createElement('div');
      meta.className = 'chat-meta';
      meta.textContent = msg.role.toUpperCase() + (msg.tool_name ? ' / ' + msg.tool_name : '');
      container.append(bubble, meta);
      row.append(avatar, container);
      chat.append(row);
    }
    chat.scrollTop = chat.scrollHeight;

    const trace = $('trace');
    trace.replaceChildren();
    if (!state.event_log.length) {
      const empty = document.createElement('div');
      empty.className = 'trace-empty';
      empty.textContent = 'Graph transitions appear after the first request.';
      trace.append(empty);
    } else {
      for (const event of state.event_log.slice(-22).reverse()) {
        const row = document.createElement('div');
        row.className = 'trace-item ' + (event.kind || 'info');
        const marker = document.createElement('div');
        marker.className = 'trace-marker';
        const body = document.createElement('div');
        const title = document.createElement('div');
        title.className = 'trace-node';
        title.textContent = event.node.toUpperCase();
        const detail = document.createElement('div');
        detail.className = 'trace-detail';
        detail.textContent = event.detail;
        const time = document.createElement('div');
        time.className = 'trace-clock';
        time.textContent = event.timestamp.replace('T', ' ').slice(0, 19) + ' UTC';
        body.append(title, detail, time);
        row.append(marker, body);
        trace.append(row);
      }
    }

    const approval = $('approval');
    approval.replaceChildren();
    if (state.status === 'PAUSED' && state.pending_approval) {
      approval.classList.remove('hidden');
      const ticket = state.pending_approval;
      const heading = document.createElement('div');
      heading.className = 'approval-heading';
      heading.textContent = '⚠ Human authorization required';
      const badge = document.createElement('span');
      badge.className = 'approval-risk';
      badge.textContent = ticket.risk;
      const parameters = document.createElement('div');
      parameters.className = 'approval-parameter';
      parameters.textContent = `ACTION: ${ticket.tool_name}\nPARAMETERS: ${JSON.stringify(ticket.arguments)}\nTICKET: ${ticket.ticket_id.slice(0, 12)}…`;
      parameters.style.whiteSpace = 'pre-wrap';
      const buttons = document.createElement('div');
      buttons.className = 'approval-buttons';
      for (const [label, decision, css] of [['Approve action', 'APPROVED', 'approve-btn'], ['Reject action', 'REJECTED', 'reject-btn']]) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = css;
        btn.textContent = label;
        btn.onclick = () => decide(ticket.ticket_id, decision);
        buttons.append(btn);
      }
      approval.append(heading, badge, parameters, buttons);
    } else {
      approval.classList.add('hidden');
    }
    refreshComposer();
  }

  function connect() {
    if (socket) socket.close();
    if (!threadId) return;
    const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
    socket = new WebSocket(`${scheme}//${location.host}/ws/${threadId}`);
    socket.onmessage = (event) => {
      try { render(JSON.parse(event.data)); } catch (e) { console.warn(e); }
    };
  }

  async function newSession() {
    pending = true;
    try {
      const snapshot = await api('/api/sessions', { method: 'POST', body: '{}' });
      threadId = snapshot.state.thread_id;
      sessionStorage.setItem('stateguard_thread', threadId);
      lastVersion = -1;
      render(snapshot);
      connect();
      note('New isolated session created.');
    } catch (err) { note(err.message, true); }
    finally { pending = false; refreshComposer(); }
  }

  async function loadSession() {
    if (!threadId) return newSession();
    try {
      const snapshot = await api(`/api/state/${encodeURIComponent(threadId)}`);
      render(snapshot);
      connect();
    } catch { await newSession(); }
  }

  async function send() {
    if (pending || !threadId) return;
    const prompt = $('prompt').value.trim();
    if (!prompt) return note('Enter a request to continue.', true);
    pending = true;
    $('send').disabled = true;
    note('Evaluating request through bounded state graph...');
    try {
      const snapshot = await api('/api/submit', {
        method: 'POST',
        body: JSON.stringify({ thread_id: threadId, prompt, inject_failure: $('errorToggle').checked }),
      });
      $('prompt').value = '';
      render(snapshot);
      note(snapshot.state.status === 'PAUSED' ? 'Graph suspended: operator approval required.' : `Run finished: ${snapshot.state.status}.`);
    } catch (err) { note(err.message, true); }
    finally { pending = false; refreshComposer(); }
  }

  async function decide(ticketId, decision) {
    if (pending) return;
    pending = true;
    note(`Submitting ${decision.toLowerCase()} decision...`);
    try {
      const snapshot = await api('/api/decide', {
        method: 'POST',
        body: JSON.stringify({ thread_id: threadId, ticket_id: ticketId, decision }),
      });
      render(snapshot);
      note(snapshot.state.status === 'PAUSED' ? 'One action handled; next approval checkpoint is pending.' : `Operator decision applied: ${snapshot.state.status}.`);
    } catch (err) { note(err.message, true); }
    finally { pending = false; refreshComposer(); }
  }

  $('send').addEventListener('click', send);
  $('prompt').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) { event.preventDefault(); send(); }
  });
  $('newSession').addEventListener('click', newSession);
  $('copyThread').addEventListener('click', async () => {
    if (!threadId) return;
    try { await navigator.clipboard.writeText(threadId); note('Session ID copied.'); }
    catch { note('Clipboard permission denied; copy the ID from the sidebar.', true); }
  });
  document.querySelectorAll('.scenario').forEach(btn => btn.addEventListener('click', () => {
    $('prompt').value = btn.dataset.prompt || '';
    $('errorToggle').checked = false;
    $('prompt').focus();
  }));
  loadSession();
})();
