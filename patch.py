with open("src/extension.ts", "r") as f:
    lines = f.readlines()

new_getChatHtml = r'''  private getChatHtml(webview: vscode.Webview): string {
    const nonce = createNonce();
    const cspSource = webview.cspSource;

    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; font-src https://fonts.gstatic.com; img-src \${cspSource} data:; style-src 'unsafe-inline' \${cspSource} https://fonts.googleapis.com; script-src 'unsafe-inline' 'nonce-\${nonce}';">
<title>Bench Chat</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #0a0a0a;
    --card-bg: #111111;
    --border: #222222;
    --text: #ededed;
    --mute: #888888;
    --pass: #3fb950;
    --fail: #f85149;
    --run: #58a6ff;
    --btn-fill: #a5d6ff;
    --btn-text: #0a0a0a;
    --mono: 'JetBrains Mono', monospace;
    --sans: 'Inter', sans-serif;
  }
  * { box-sizing: border-box; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    margin: 0; padding: 0;
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .header { display: flex; justify-content: space-between; padding: 16px; border-bottom: 1px solid var(--border); align-items: center; flex-shrink: 0; }
  .header .title { font-weight: 600; font-size: 14px; }
  .header .icons { display: flex; gap: 12px; }
  .header i { width: 16px; height: 16px; display: inline-block; cursor: pointer; }
  .header i:hover { filter: brightness(1.5); }
  
  .chat { padding: 16px; display: flex; flex-direction: column; gap: 24px; overflow-y: auto; flex: 1; padding-bottom: 120px; }
  
  .msg.user {
    background: #151515;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 12px;
    font-size: 13px;
    line-height: 1.5;
  }
  
  .msg.assistant {
    display: flex; gap: 12px; font-size: 13px; line-height: 1.5; flex-direction: column;
  }
  .msg-content { display: flex; gap: 12px; align-items: flex-start; }
  .msg-content > i { flex-shrink: 0; margin-top: 2px; }
  
  .cards { display: flex; flex-direction: column; gap: 16px; margin-top: 8px; width: 100%; }
  .card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    position: relative;
    overflow: hidden;
  }
  .card.passed { border-left: 4px solid var(--pass); }
  .card.failed, .card.error, .card.timeout { border-left: 4px solid var(--fail); }
  .card.running, .card.queued { border-left: 4px solid var(--run); }
  .card.draft { border-left: 4px solid var(--mute); }
  
  .card-top { display: flex; justify-content: space-between; align-items: center; padding: 12px 16px; }
  .card-title-wrap { display: flex; align-items: center; gap: 12px; }
  .badge { background: #222; color: #aaa; font-family: var(--mono); font-size: 10px; padding: 2px 6px; border-radius: 4px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;}
  .card-title { font-weight: 600; font-size: 14px; }
  .status-icon i { width: 16px; height: 16px; }
  .running .status-icon i, .queued .status-icon i { animation: spin 2s linear infinite; }
  @keyframes spin { 100% { transform: rotate(360deg); } }
  
  .metrics { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; padding: 0 16px 16px; }
  .metric-col { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
  .metric-title { font-family: var(--mono); font-size: 11px; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .metric-bar-bg { height: 4px; background: #333; border-radius: 2px; width: 100%; overflow: hidden; }
  .metric-bar-fg { height: 100%; border-radius: 2px; width: 100%; }
  .passed .metric-bar-fg { background: var(--pass); }
  .failed .metric-bar-fg, .error .metric-bar-fg, .timeout .metric-bar-fg { background: var(--fail); }
  .running .metric-bar-fg, .queued .metric-bar-fg { background: var(--run); }
  .draft .metric-bar-fg { background: var(--mute); }
  .metric-val { font-family: var(--mono); font-size: 11px; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .failed .metric-val, .error .metric-val, .timeout .metric-val { color: var(--fail); }
  .running .metric-val, .queued .metric-val { color: var(--mute); }
  
  .card-actions { display: flex; justify-content: space-between; align-items: center; padding: 12px 16px; border-top: 1px solid var(--border); }
  .left-actions { display: flex; gap: 16px; }
  .action-btn { display: flex; align-items: center; gap: 6px; background: transparent; border: none; color: var(--mute); font-family: var(--mono); font-size: 11px; cursor: pointer; padding: 0; }
  .action-btn:hover { color: var(--text); }
  .failed .action-btn.view-logs, .error .action-btn.view-logs, .timeout .action-btn.view-logs { color: var(--fail); }
  
  .select-btn { background: var(--btn-fill); color: var(--btn-text); border: none; padding: 6px 16px; border-radius: 4px; font-weight: 600; font-size: 12px; cursor: pointer; }
  .select-btn:hover { opacity: 0.9; }
  .select-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  
  .composer-wrap { position: absolute; bottom: 0; left: 0; right: 0; padding: 16px; background: var(--bg); border-top: 1px solid var(--border); }
  .run-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; display: none; }
  .run-bar.visible { display: flex; }
  .run-status { font-family: var(--mono); font-size: 11px; color: var(--mute); display: flex; align-items: center; gap: 8px; }
  .run-actions { display: flex; gap: 8px; }
  .outline-btn { background: transparent; border: 1px solid var(--border); color: var(--mute); padding: 4px 12px; font-size: 11px; border-radius: 4px; font-family: var(--mono); cursor: pointer; }
  .outline-btn:hover:not(:disabled) { border-color: var(--text); color: var(--text); }
  .outline-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  
  .input-box { display: flex; align-items: center; gap: 12px; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 8px 12px; }
  .input-box:focus-within { border-color: var(--mute); }
  .input-box i { flex-shrink: 0; cursor: pointer; }
  .input-box input { flex: 1; background: transparent; border: none; color: var(--text); font-family: var(--sans); font-size: 13px; outline: none; }
  .input-box input::placeholder { color: var(--mute); }
  
  /* SVGs */
  .svg-sparkle { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23ededed' d='M11.5 2L13 8l6 1.5-6 1.5-1.5 6-1.5-6-6-1.5 6-1.5z'/%3E%3C/svg%3E") no-repeat center; width: 18px; height: 18px; display: inline-block; }
  .svg-check { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%233fb950' d='M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z'/%3E%3C/svg%3E") no-repeat center; display: inline-block; width: 16px; height: 16px; }
  .svg-spin { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%2358a6ff' d='M12 4V2C6.48 2 2 6.48 2 12h2c0-4.41 3.59-8 8-8z'/%3E%3C/svg%3E") no-repeat center; display: inline-block; width: 16px; height: 16px; }
  .svg-alert { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23f85149' d='M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z'/%3E%3C/svg%3E") no-repeat center; display: inline-block; width: 16px; height: 16px; }
  .svg-draft { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23888888' d='M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8z'/%3E%3C/svg%3E") no-repeat center; display: inline-block; width: 16px; height: 16px; }
  .svg-code { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='currentColor' d='M9.4 16.6L4.8 12l4.6-4.6L8 6l-6 6 6 6 1.4-1.4zm5.2 0l4.6-4.6-4.6-4.6L16 6l6 6-6 6-1.4-1.4z'/%3E%3C/svg%3E") no-repeat center; display: inline-block; width: 14px; height: 14px; }
  .svg-metrics { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='currentColor' d='M5 9.2h3V19H5zM10.6 5h2.8v14h-2.8zm5.6 8H19v6h-2.8z'/%3E%3C/svg%3E") no-repeat center; display: inline-block; width: 14px; height: 14px; }
  .svg-logs { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='currentColor' d='M20 19H4v-1h16v1zm0-14H4v1h16V5zm0 7H4v1h16v-1z'/%3E%3C/svg%3E") no-repeat center; display: inline-block; width: 14px; height: 14px; }
  .svg-send { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23888888' d='M2.01 21L23 12 2.01 3 2 10l15 2-15 2z'/%3E%3C/svg%3E") no-repeat center; width: 16px; height: 16px; display: inline-block; }
  .svg-plus { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23888888' d='M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z'/%3E%3C/svg%3E") no-repeat center; width: 16px; height: 16px; display: inline-block; }
  .svg-history { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23888888' d='M13 3a9 9 0 0 0-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42A8.954 8.954 0 0 0 13 21a9 9 0 0 0 0-18zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z'/%3E%3C/svg%3E") no-repeat center; width: 16px; height: 16px; display: inline-block; }
</style>
</head>
<body>
  <div class="header">
    <div class="title">Bench</div>
    <div class="icons">
      <i class="svg-plus" title="New Chat" data-action="newChat"></i>
      <i class="svg-history" title="History"></i>
    </div>
  </div>
  
  <div class="chat" id="messages"></div>
  
  <div class="composer-wrap">
    <div class="run-bar" id="runBar"></div>
    <form id="form" class="input-box">
      <i class="svg-plus"></i>
      <input type="text" id="input" placeholder="Refine approaches..." autocomplete="off">
      <button type="submit" style="background:none;border:none;padding:0;cursor:pointer;"><i class="svg-send"></i></button>
    </form>
  </div>

  <script nonce="\${nonce}">
    const vscode = acquireVsCodeApi();
    const messagesEl = document.getElementById('messages');
    const formEl = document.getElementById('form');
    const inputEl = document.getElementById('input');
    const runBarEl = document.getElementById('runBar');
    
    let state = { messages: [], options: [], loading: false };

    window.addEventListener('message', (event) => {
      const data = event.data;
      if (data.type === 'state') {
        state = data.state;
        render();
      }
    });

    formEl.addEventListener('submit', (event) => {
      event.preventDefault();
      const text = inputEl.value.trim();
      if (!text || state.loading) return;
      vscode.postMessage({ type: 'askFeature', text });
      inputEl.value = '';
    });

    document.addEventListener('click', (event) => {
      const target = event.target;
      if (!target || typeof target.getAttribute !== 'function') return;
      
      let actionNode = target.closest('[data-action]');
      if (!actionNode) actionNode = target;
      
      const action = actionNode.getAttribute('data-action');
      const optionId = target.closest('[data-option-id]')?.getAttribute('data-option-id');
      
      if (action === 'newChat') vscode.postMessage({ type: 'newFeatureChat' });
      else if (action && optionId) vscode.postMessage({ type: action, optionId });
      else if (action) vscode.postMessage({ type: action });
    });

    function render() {
      inputEl.disabled = Boolean(state.loading);
      renderRunBar();
      messagesEl.innerHTML = '';

      for (const message of state.messages) {
        const node = document.createElement('div');
        node.className = 'msg ' + message.role;
        if (message.role === 'user') {
          node.innerHTML = escapeHtml(message.content);
        } else {
          let html = '<div class="msg-content"><i class="svg-sparkle"></i><div>' + escapeHtml(message.content) + '</div></div>';
          if (message.options && message.options.length) {
            html += renderCards(message.options);
          }
          node.innerHTML = html;
        }
        messagesEl.appendChild(node);
      }
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function renderRunBar() {
      const hasOptions = (state.options || []).length >= 2;
      const isRunning = (state.runState && state.runState.status === 'running') || (state.options || []).some(o => o.runStatus === 'queued' || o.runStatus === 'running');
      const canApply = Boolean(state.runState && state.runState.winnerCandidateId);
      
      runBarEl.className = hasOptions ? 'run-bar visible' : 'run-bar';
      if (!hasOptions) return;

      const label = (state.runState && state.runState.summary) || (isRunning ? 'Running tests...' : 'Ready to test approaches.');
      const icon = isRunning ? '<i class="svg-spin"></i>' : '';
      
      runBarEl.innerHTML = \`
        <div class="run-status">\${icon}\${escapeHtml(label)}</div>
        <div class="run-actions">
          <button class="outline-btn" data-action="testAll" \${isRunning ? 'disabled' : ''}>Test all</button>
          <button class="outline-btn" data-action="applyWinner" \${!canApply ? 'disabled' : ''}>Apply winner</button>
        </div>
      \`;
    }

    function renderCards(options) {
      let html = '<div class="cards">';
      options.forEach((option, index) => {
        const status = option.runStatus || 'draft';
        const letter = String.fromCharCode(65 + index);
        const title = escapeHtml(option.title);
        
        let iconClass = 'svg-draft';
        if (status === 'passed') iconClass = 'svg-check';
        else if (status === 'failed' || status === 'error' || status === 'timeout') iconClass = 'svg-alert';
        else if (status === 'running' || status === 'queued') iconClass = 'svg-spin';

        html += \`
          <div class="card \${status}" data-option-id="\${escapeHtml(option.id)}">
            <div class="card-top">
              <div class="card-title-wrap">
                <span class="badge">APPROACH \${letter}</span>
                <span class="card-title">\${title}</span>
              </div>
              <div class="status-icon"><i class="\${iconClass}"></i></div>
            </div>
            \${renderMetrics(option, status)}
            <div class="card-actions">
              <div class="left-actions">
                <button class="action-btn" data-action="viewCode"><i class="svg-code"></i> Code</button>
                <button class="action-btn view-logs" data-action="viewMetrics"><i class="svg-\${status === 'failed' || status === 'error' ? 'logs' : 'metrics'}"></i> \${status === 'failed' || status === 'error' ? 'View Logs' : 'Metrics'}</button>
              </div>
              <button class="select-btn" data-action="selectOption">\${option.selected ? 'Selected' : 'Select'}</button>
            </div>
          </div>
        \`;
      });
      html += '</div>';
      return html;
    }

    function renderMetrics(option, status) {
      const m = option.measured || {};
      const mock = option.metrics || {};
      
      let runVal = typeof m.durationMs === 'number' ? Math.round(m.durationMs) + 'ms' : (status === 'running' ? 'Running...' : (status === 'failed' || status === 'error' ? 'OOM Error' : (mock.speed ? mock.speed + ' (est)' : '--')));
      let testVal = m.tests ? \`\${m.tests.passed}/\${m.tests.total} passed\` : (status === 'running' ? '48%' : (status === 'failed' || status === 'error' ? 'Fail' : (mock.testConfidence ? mock.testConfidence + '% (est)' : '--')));
      let memVal = m.memory ? m.memory : (status === 'running' ? 'Calcing...' : (status === 'failed' || status === 'error' ? 'Limit Hit' : (mock.readability ? mock.readability + ' (est)' : '--')));
      
      let runTitle = 'Runtime', testTitle = m.tests ? \`Tests (\${m.tests.passed}/\${m.tests.total})\` : 'Tests', memTitle = 'Mem';

      let runPct = typeof m.durationMs === 'number' ? '100%' : (status === 'running' ? '40%' : (status === 'failed' || status === 'error' ? '50%' : (mock.speed ? mock.speed+'%' : '0%')));
      let testPct = m.tests ? (m.tests.passed / m.tests.total * 100) + '%' : (status === 'running' ? '48%' : (status === 'failed' || status === 'error' ? '10%' : (mock.testConfidence ? mock.testConfidence+'%' : '0%')));
      let memPct = m.memory ? '80%' : (status === 'running' ? '20%' : (status === 'failed' || status === 'error' ? '100%' : (mock.readability ? mock.readability+'%' : '0%')));

      return \`
        <div class="metrics">
          <div class="metric-col">
            <span class="metric-title">\${runTitle}</span>
            <div class="metric-bar-bg"><div class="metric-bar-fg" style="width: \${runPct}"></div></div>
            <span class="metric-val">\${runVal}</span>
          </div>
          <div class="metric-col">
            <span class="metric-title">\${testTitle}</span>
            <div class="metric-bar-bg"><div class="metric-bar-fg" style="width: \${testPct}"></div></div>
            <span class="metric-val">\${testVal}</span>
          </div>
          <div class="metric-col">
            <span class="metric-title">\${memTitle}</span>
            <div class="metric-bar-bg"><div class="metric-bar-fg" style="width: \${memPct}"></div></div>
            <span class="metric-val">\${memVal}</span>
          </div>
        </div>
      \`;
    }

    function escapeHtml(value) {
      if (typeof value !== 'string') return '';
      return value.replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[char]));
    }

    vscode.postMessage({ type: 'ready' });
  </script>
</body>
</html>`;
  }
'''

new_lines = lines[:173] + [new_getChatHtml] + lines[721:]

with open("src/extension.ts", "w") as f:
    f.writelines(new_lines)

print("Sliced and patched successfully!")
