/**
 * LCARS Dashboard — Claude Code Session Monitor
 *
 * Connects to a WebSocket backend and renders live session data
 * in a Star Trek LCARS-inspired interface.
 */
(function () {
  'use strict';

  // ---------------------------------------------------------------------------
  // Constants
  // ---------------------------------------------------------------------------

  const WS_URL = 'ws://' + window.location.host + '/ws';
  const RECONNECT_DELAY_MS = 3000;
  const TOAST_DURATION_MS = 4000;
  const MAX_EVENTS = 50;
  const SESSION_COLORS = ['#DDA88A', '#B399B3', '#8E9ED6', '#B07AA0', '#D4906A', '#9CC5E0'];

  const LCARS_THEME = {
    background: '#000000',
    foreground: '#E8E8F0',
    cursor: '#F0A07A',
    cursorAccent: '#000000',
    selectionBackground: 'rgba(192, 160, 192, 0.3)',
    black: '#000000',
    red: '#D08080',
    green: '#80D090',
    yellow: '#D0C878',
    blue: '#90A0D0',
    magenta: '#B080A0',
    cyan: '#A0C8E8',
    white: '#E8E8F0',
    brightBlack: '#707898',
    brightRed: '#D08080',
    brightGreen: '#80D090',
    brightYellow: '#D0C878',
    brightBlue: '#90A0D0',
    brightMagenta: '#C0A0C0',
    brightCyan: '#A0C8E8',
    brightWhite: '#FFFFFF',
  };

  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------

  let ws = null;
  let previousState = null;
  let currentState = null;
  let mergedSessions = []; // sessions + synthetic terminal entries
  let selectedSessionId = null;
  let soundEnabled = false;
  let audioCtx = null;
  let userScrolledUp = false;
  let completedCollapsed = true;
  let renderedEventKeys = new Set();
  let dashboardStartTime = Date.now();
  let lastStateKey = null;

  // Terminal state
  var terminals = new Map();     // id -> { id, ws, xterm, fitAddon, container }
  var activeTerminalId = null;

  // ---------------------------------------------------------------------------
  // DOM references
  // ---------------------------------------------------------------------------

  const dom = {};

  function cacheDom() {
    dom.statActive = document.getElementById('stat-active');
    dom.statIdle = document.getElementById('stat-idle');
    dom.statAgents = document.getElementById('stat-agents');
    dom.statCost = document.getElementById('stat-cost');
    dom.headerStats = document.getElementById('header-stats');
    dom.sessionList = document.getElementById('session-list');
    dom.sessionDetail = document.getElementById('session-detail');
    dom.agentsPanel = document.getElementById('agents-panel');
    dom.activityFeed = document.getElementById('activity-feed');
    dom.btnRefresh = document.getElementById('btn-refresh');
    dom.btnSound = document.getElementById('btn-sound');
    dom.btnScanlines = document.getElementById('btn-scanlines');
    dom.btnNewSession = document.getElementById('btn-new-session');
    dom.clock = document.getElementById('clock');
    dom.dashboardUptime = document.getElementById('dashboard-uptime');
    dom.toastContainer = document.getElementById('toast-container');
    dom.mainTop = document.getElementById('main-top');
    dom.panelDetail = document.getElementById('panel-detail');
    dom.panelAgents = document.getElementById('panel-agents');
    dom.panelTerminal = document.getElementById('panel-terminal');
    dom.terminalContainer = document.getElementById('terminal-container');
    dom.terminalTabs = document.getElementById('terminal-tabs');
    dom.detailView = document.getElementById('detail-view');
    dom.resourceStrip = document.getElementById('resource-strip');
    dom.statTokens = document.getElementById('stat-tokens');
  }

  // ---------------------------------------------------------------------------
  // Formatting helpers
  // ---------------------------------------------------------------------------

  /**
   * Return a human-readable relative duration from an ISO timestamp to now.
   * E.g. "2h 14m", "3m 8s", "42s".
   */
  function formatDuration(isoString) {
    if (!isoString) return '--';
    const diffMs = Date.now() - new Date(isoString).getTime();
    if (diffMs < 0) return '0s';
    const totalSeconds = Math.floor(diffMs / 1000);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    if (hours > 0) return `${hours}h ${minutes}m`;
    if (minutes > 0) return `${minutes}m ${seconds}s`;
    return `${seconds}s`;
  }

  /**
   * Format a token count: 142000 -> "142k", 1500000 -> "1.5M".
   */
  function formatTokens(count) {
    if (count == null) return '0';
    if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
    if (count >= 1_000) return `${Math.round(count / 1_000)}k`;
    return String(count);
  }

  /**
   * Format USD cost: 0.82 -> "$0.82".
   */
  function formatCost(usd) {
    if (usd == null) return '$0.00';
    return `$${usd.toFixed(2)}`;
  }

  /**
   * Strip "-home-allan-" prefix and replace "-" with "/".
   */
  function formatProject(project) {
    if (!project) return '';
    let name = project;
    if (name.startsWith('-home-allan-')) {
      name = name.slice('-home-allan-'.length);
    }
    return name.replace(/-/g, '/');
  }

  /**
   * Extract a short model name: "claude-haiku-4-5-20251001" -> "haiku".
   */
  function shortModelName(model) {
    if (!model) return 'unknown';
    const lower = model.toLowerCase();
    if (lower.includes('opus')) return 'opus';
    if (lower.includes('sonnet')) return 'sonnet';
    if (lower.includes('haiku')) return 'haiku';
    return model;
  }

  /**
   * Truncate a string to maxLen characters, adding ellipsis if needed.
   */
  function truncate(str, maxLen) {
    if (!str) return '';
    return str.length > maxLen ? str.slice(0, maxLen) + '...' : str;
  }

  /**
   * Deterministic color for a session slug.
   */
  function sessionColor(slug) {
    if (!slug) return SESSION_COLORS[0];
    let hash = 0;
    for (let i = 0; i < slug.length; i++) {
      hash = ((hash << 5) - hash + slug.charCodeAt(i)) | 0;
    }
    return SESSION_COLORS[Math.abs(hash) % SESSION_COLORS.length];
  }

  /**
   * Format time as HH:MM:SS from an ISO string.
   */
  function formatTime(isoString) {
    const d = new Date(isoString);
    return d.toLocaleTimeString('en-GB', { hour12: false });
  }

  // ---------------------------------------------------------------------------
  // Audio
  // ---------------------------------------------------------------------------

  function getAudioContext() {
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    return audioCtx;
  }

  function lcarsChirp(freq = 880, duration = 0.1, volume = 0.15) {
    if (!soundEnabled) return;
    const ctx = getAudioContext();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.frequency.value = freq;
    osc.type = 'sine';
    gain.gain.setValueAtTime(volume, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duration);
    osc.start();
    osc.stop(ctx.currentTime + duration);
  }

  const sound = {
    click() { lcarsChirp(880, 0.08); },
    newSession() {
      lcarsChirp(660, 0.1);
      setTimeout(() => lcarsChirp(880, 0.1), 120);
    },
    agentSpawn() { lcarsChirp(1100, 0.12); },
    sessionEnd() { lcarsChirp(440, 0.2); },
    alert() {
      lcarsChirp(330, 0.15);
      setTimeout(() => lcarsChirp(330, 0.15), 200);
    },
  };

  // ---------------------------------------------------------------------------
  // Toast notifications
  // ---------------------------------------------------------------------------

  function showToast(message) {
    const el = document.createElement('div');
    el.className = 'lcars-toast';
    el.textContent = message;
    dom.toastContainer.appendChild(el);

    // Trigger reflow so CSS transition applies
    void el.offsetWidth;
    el.style.opacity = '1';
    el.style.transform = 'translateX(0)';

    setTimeout(() => {
      el.style.opacity = '0';
      el.style.transform = 'translateX(100%)';
      setTimeout(() => el.remove(), 500);
    }, TOAST_DURATION_MS);
  }

  // ---------------------------------------------------------------------------
  // Panel switching logic
  // ---------------------------------------------------------------------------

  /**
   * Check if the selected session is a LCARS terminal session.
   */
  function getSelectedSession() {
    if (!mergedSessions || mergedSessions.length === 0) return null;
    return mergedSessions.find(function (s) { return s.id === selectedSessionId; });
  }

  function isTerminalSession(session) {
    return session && session.terminal_id;
  }

  /**
   * Update panel visibility based on the selected session type.
   * Detail or terminal is shown; agents panel is always visible.
   */
  function updatePanelLayout() {
    var session = getSelectedSession();
    var isTerminal = isTerminalSession(session);

    if (isTerminal) {
      var wasHidden = dom.panelTerminal.style.display === 'none';
      dom.detailView.style.display = 'none';
      dom.panelTerminal.style.display = 'flex';
      connectTerminal(session.terminal_id, wasHidden);
    } else {
      dom.detailView.style.display = 'flex';
      dom.panelTerminal.style.display = 'none';
      // Don't disconnect — keep terminal alive so buffer is preserved
    }
    // Agents panel is always visible — no toggle needed
  }

  // ---------------------------------------------------------------------------
  // Terminal management
  // ---------------------------------------------------------------------------

  function connectTerminal(terminalId, wasHidden) {
    // If already exists, just switch to it
    if (terminals.has(terminalId)) {
      switchToTerminal(terminalId);
      if (wasHidden) {
        requestAnimationFrame(function() {
          requestAnimationFrame(function() {
            var t = terminals.get(activeTerminalId);
            if (!t) return;
            if (t.xterm.clearTextureAtlas) t.xterm.clearTextureAtlas();
            t.fitAddon.fit();
            sendTerminalResize();
            t.xterm.refresh(0, t.xterm.rows - 1);
            t.xterm.focus();
          });
        });
      }
      return;
    }

    // Create a container div for this terminal
    var container = document.createElement('div');
    container.id = 'term-' + terminalId;
    container.style.cssText = 'flex:1;min-height:0;overflow:hidden;display:none;';
    dom.terminalContainer.appendChild(container);

    var xterm = new Terminal({
      theme: LCARS_THEME,
      fontFamily: "'Courier New', monospace",
      fontSize: 14,
      cursorBlink: true,
      allowProposedApi: true,
    });

    var fitAddon = new FitAddon.FitAddon();
    xterm.loadAddon(fitAddon);
    xterm.open(container);

    var wsUrl = 'ws://' + window.location.host + '/ws/terminal/' + terminalId;
    var termWs = new WebSocket(wsUrl);

    termWs.addEventListener('open', function() {
      // Defer initial resize until after switchToTerminal has fitted
      requestAnimationFrame(function() {
        requestAnimationFrame(function() {
          sendTerminalResize();
        });
      });
    });

    termWs.addEventListener('message', function(event) { xterm.write(event.data); });

    xterm.onData(function(data) {
      if (termWs.readyState === WebSocket.OPEN) termWs.send(data);
    });

    terminals.set(terminalId, { id: terminalId, ws: termWs, xterm: xterm, fitAddon: fitAddon, container: container });
    switchToTerminal(terminalId);
    window.addEventListener('resize', handleTerminalResize);
  }

  function switchToTerminal(terminalId) {
    // Hide all terminal containers, show selected
    terminals.forEach(function(t, id) {
      t.container.style.display = id === terminalId ? 'flex' : 'none';
    });
    activeTerminalId = terminalId;
    renderTerminalTabs();

    // Defer fit until layout is complete — container needs dimensions first
    requestAnimationFrame(function() {
      requestAnimationFrame(function() {
        var t = terminals.get(terminalId);
        if (!t) return;
        t.fitAddon.fit();
        sendTerminalResize();
        t.xterm.focus();
      });
    });
  }

  function closeTerminal(terminalId) {
    var t = terminals.get(terminalId);
    if (!t) return;

    if (t.ws) try { t.ws.close(); } catch (_) {}
    if (t.xterm) t.xterm.dispose();
    if (t.container) t.container.remove();
    terminals.delete(terminalId);

    // If we closed the active one, switch to another or hide panel
    if (activeTerminalId === terminalId) {
      var remaining = Array.from(terminals.keys());
      if (remaining.length > 0) {
        switchToTerminal(remaining[0]);
      } else {
        activeTerminalId = null;
        window.removeEventListener('resize', handleTerminalResize);
      }
    }
    renderTerminalTabs();

    // Also delete from backend
    fetch('/api/terminal/' + terminalId, { method: 'DELETE' });
  }

  function renderTerminalTabs() {
    var tabsEl = document.getElementById('terminal-tabs');
    if (!tabsEl) return;

    var idx = 0;
    tabsEl.innerHTML = '';
    terminals.forEach(function(t, id) {
      idx++;
      var isActive = id === activeTerminalId;
      var tab = document.createElement('div');
      tab.className = 'lcars-terminal-tab' + (isActive ? ' active' : '');
      tab.innerHTML = '#' + idx + ' <span class="tab-close">&times;</span>';
      tab.addEventListener('click', function(e) {
        if (e.target.classList.contains('tab-close')) {
          closeTerminal(id);
        } else {
          switchToTerminal(id);
          sound.click();
        }
      });
      tabsEl.appendChild(tab);
    });
  }

  function handleTerminalResize() {
    var t = terminals.get(activeTerminalId);
    if (!t || !t.fitAddon) return;
    t.fitAddon.fit();
    sendTerminalResize();
  }

  function sendTerminalResize() {
    var t = terminals.get(activeTerminalId);
    if (!t || !t.ws || !t.fitAddon) return;
    if (t.ws.readyState !== WebSocket.OPEN) return;
    var dims = t.fitAddon.proposeDimensions();
    if (dims) {
      t.ws.send(JSON.stringify({ type: 'resize', cols: dims.cols, rows: dims.rows }));
    }
  }

  // Re-fit terminal when returning to the tab (browser tab switch)
  document.addEventListener('visibilitychange', function () {
    var t = terminals.get(activeTerminalId);
    if (!document.hidden && t && t.fitAddon) {
      setTimeout(function () {
        t.fitAddon.fit();
        sendTerminalResize();
        t.xterm.refresh(0, t.xterm.rows - 1);
      }, 100);
    }
  });

  // ---------------------------------------------------------------------------
  // New session
  // ---------------------------------------------------------------------------

  function createNewSession() {
    var homeDir = '~';
    var cwd = window.prompt('WORKING DIRECTORY FOR NEW CLAUDE SESSION:', homeDir);
    if (!cwd) return;

    fetch('/api/terminal/new', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cwd: cwd }),
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (data.terminal_id) {
          showToast('TERMINAL SPAWNED');
          sound.newSession();

          // Immediately select the new terminal — don't wait for the next poll
          var syntheticId = 'lcars-' + data.terminal_id;
          selectedSessionId = syntheticId;
          pendingTerminalSelect = data.terminal_id;

          // Show terminal panel right away so fitAddon has real dimensions
          dom.detailView.style.display = 'none';
          dom.panelTerminal.style.display = 'flex';

          connectTerminal(data.terminal_id, true);
        } else {
          showToast('FAILED TO CREATE TERMINAL');
          sound.alert();
        }
      })
      .catch(function () {
        showToast('FAILED TO CREATE TERMINAL');
        sound.alert();
      });
  }

  var pendingTerminalSelect = null;

  // ---------------------------------------------------------------------------
  // Render: header stats
  // ---------------------------------------------------------------------------

  function renderStats(sessions, totalCost) {
    const active = sessions.filter(s => s.status === 'active').length;
    const idle = sessions.filter(s => s.status === 'idle').length;
    const agents = sessions.reduce((n, s) => n + (s.agents ? s.agents.length : 0), 0);

    dom.statActive.textContent = `${active} active`;
    dom.statIdle.textContent = `${idle} idle`;
    dom.statAgents.textContent = `${agents} agents`;
    dom.statCost.textContent = formatCost(totalCost);

    var totalTokens = sessions.reduce(function (n, s) {
      var tc = s.token_counts || {};
      return n + (tc.input || 0) + (tc.output || 0) + (tc.cache_read || 0) + (tc.cache_write || 0);
    }, 0);
    if (dom.statTokens) dom.statTokens.textContent = formatTokens(totalTokens) + ' tokens';
  }

  // ---------------------------------------------------------------------------
  // Render: session list
  // ---------------------------------------------------------------------------

  function renderSessionCard(session) {
    var color = sessionColor(session.slug);
    var selected = session.id === selectedSessionId ? ' selected' : '';
    var agentCount = session.agents ? session.agents.length : 0;
    var agentLine = agentCount > 0
      ? '<div class="session-agents-summary">' + agentCount + ' AGENT' + (agentCount > 1 ? 'S' : '') + '</div>'
      : '';
    var lcarsBadge = session.terminal_id
      ? '<span class="lcars-badge">LCARS</span>'
      : '';

    var actionsHtml = '<div class="lcars-session-actions">'
      + (session.status !== 'done' && session.pid ? '<button class="lcars-action-btn kill" data-action="kill" data-pid="' + (session.pid || '') + '" title="Kill session">KILL</button>' : '')
      + '<button class="lcars-action-btn copy-path" data-action="copy-path" data-value="' + (session.cwd || '') + '" title="Copy working directory">PATH</button>'
      + '<button class="lcars-action-btn copy-id" data-action="copy-id" data-value="' + session.id + '" title="Copy session ID">ID</button>'
      + '</div>';

    return '<div class="lcars-session-item' + selected + '" data-session-id="' + session.id + '" style="border-left-color: ' + color + '">'
      + '<div class="session-name">'
      + '<span class="status-' + session.status + '" title="' + session.status + '"></span> '
      + formatProject(session.project)
      + ' ' + lcarsBadge
      + '</div>'
      + '<div class="session-meta">' + (session.branch || '--') + ' &middot; ' + formatDuration(session.start_time) + ' &middot; ' + formatCost(session.cost_usd) + '</div>'
      + agentLine
      + actionsHtml
      + '</div>';
  }

  function renderSessionList(sessions) {
    if (!sessions || sessions.length === 0) {
      dom.sessionList.innerHTML = '<div class="lcars-empty">NO ACTIVE SESSIONS</div>';
      return;
    }

    // When a pending terminal gets matched to a real session, transfer selection.
    // Don't clear pendingTerminalSelect until the session is active/idle — it may
    // briefly appear as 'done' before the process watcher detects it.
    if (pendingTerminalSelect) {
      var matchingSession = sessions.find(function (s) {
        return s.terminal_id === pendingTerminalSelect;
      });
      if (matchingSession) {
        // Transfer selection from synthetic lcars-<uuid> to the real session ID
        var syntheticId = 'lcars-' + pendingTerminalSelect;
        if (selectedSessionId === syntheticId) {
          selectedSessionId = matchingSession.id;
        }
        // Only clear pending once the session is actually running
        if (matchingSession.status === 'active' || matchingSession.status === 'idle') {
          pendingTerminalSelect = null;
        }
      }
    }

    // Split into active and completed
    var activeSessions = sessions.filter(function (s) { return s.status === 'active' || s.status === 'idle'; });
    var completedSessions = sessions.filter(function (s) { return s.status === 'done'; });

    // Auto-select first active session if none selected.
    // Don't override selection if we have an active terminal connection.
    var selectedExists = sessions.find(function (s) { return s.id === selectedSessionId; });
    var hasActiveTerminal = activeTerminalId && terminals.has(activeTerminalId);
    if ((!selectedSessionId || !selectedExists) && !hasActiveTerminal) {
      selectedSessionId = (activeSessions[0] || sessions[0] || {}).id;
    }

    // Render active sessions
    var html = activeSessions.map(function (session) { return renderSessionCard(session); }).join('');

    // Render completed section if any
    if (completedSessions.length > 0) {
      html += '<div class="lcars-session-divider" id="completed-divider">'
        + 'COMPLETED (' + completedSessions.length + ')'
        + '</div>'
        + '<div class="lcars-completed-zone' + (completedCollapsed ? ' collapsed' : '') + '" id="completed-zone">'
        + completedSessions.map(function (s) {
            return '<div class="lcars-completed-item" data-session-id="' + s.id + '">'
              + '<span>' + formatProject(s.project) + '</span>'
              + '<span>' + formatCost(s.cost_usd) + '</span>'
              + '</div>';
          }).join('')
        + '</div>';
    }

    dom.sessionList.innerHTML = html;

    // Divider click toggles collapse
    var divider = document.getElementById('completed-divider');
    if (divider) {
      divider.addEventListener('click', function () {
        completedCollapsed = !completedCollapsed;
        var zone = document.getElementById('completed-zone');
        if (zone) zone.classList.toggle('collapsed', completedCollapsed);
      });
    }

    // Click handlers for both active cards and completed items
    dom.sessionList.querySelectorAll('.lcars-session-item, .lcars-completed-item').forEach(function (el) {
      el.addEventListener('click', function () {
        sound.click();
        selectedSessionId = el.dataset.sessionId;
        render(currentState);
      });
    });

    // Action button handlers
    dom.sessionList.querySelectorAll('.lcars-action-btn').forEach(function(btn) {
      btn.addEventListener('click', function(e) {
        e.stopPropagation();
        var action = btn.dataset.action;
        if (action === 'kill') {
          var pid = btn.dataset.pid;
          if (pid) {
            fetch('/api/session/' + pid + '/kill', { method: 'POST' })
              .then(function(r) { return r.json(); })
              .then(function(d) {
                showToast(d.status === 'ok' ? 'SESSION TERMINATED' : 'KILL FAILED');
              });
          }
        } else if (action === 'copy-path' || action === 'copy-id') {
          navigator.clipboard.writeText(btn.dataset.value).then(function() {
            showToast('COPIED TO CLIPBOARD');
          });
        }
      });
    });
  }

  // ---------------------------------------------------------------------------
  // Render: session detail
  // ---------------------------------------------------------------------------

  var lastDetailKey = null;

  function renderSessionDetail(sessions) {
    const session = sessions.find(s => s.id === selectedSessionId);
    if (!session) {
      if (lastDetailKey !== 'empty') {
        dom.sessionDetail.innerHTML = '<div class="lcars-empty">SELECT A SESSION</div>';
        lastDetailKey = 'empty';
        lastSessionEventsKey = null;
      }
      return;
    }

    const mc = session.message_counts || {};
    const tc = session.token_counts || {};

    // Skip re-render if nothing changed
    var detailKey = session.id + ':' + session.status + ':' + (mc.user || 0) + ':' + (mc.assistant || 0)
      + ':' + (tc.input || 0) + ':' + (tc.output || 0) + ':' + session.cost_usd;
    if (detailKey === lastDetailKey) return;
    lastDetailKey = detailKey;
    lastSessionEventsKey = null; // reset so events re-render with detail

    const rows = [
      ['STATUS', `<span class="status-${session.status}"></span> ${(session.status || '').toUpperCase()}`],
      ['BRANCH', session.branch || '--'],
      ['WORKING DIR', session.cwd || '--'],
      ['UPTIME', formatDuration(session.start_time)],
      ['MESSAGES', `${mc.user || 0} USER / ${mc.assistant || 0} ASSISTANT`],
      ['TOKENS', `${formatTokens(tc.input)} IN / ${formatTokens(tc.output)} OUT`],
      ['COST', formatCost(session.cost_usd)],
      ['VERSION', session.version || '--'],
    ];

    dom.sessionDetail.innerHTML = rows.map(([label, value]) => `
      <div class="lcars-detail-row">
        <span class="lcars-detail-label">${label}</span>
        <span class="lcars-detail-value">${value}</span>
      </div>
    `).join('');
  }

  // ---------------------------------------------------------------------------
  // Render: resource strip
  // ---------------------------------------------------------------------------

  function renderResourceStrip(resources) {
    if (!dom.resourceStrip) return;
    if (!resources || Object.keys(resources).length === 0) {
      dom.resourceStrip.innerHTML = '';
      return;
    }

    dom.resourceStrip.innerHTML = Object.entries(resources).map(function ([pid, stats]) {
      var cpuColor = stats.cpu_pct > 80 ? 'var(--lcars-red)'
        : stats.cpu_pct > 50 ? 'var(--lcars-yellow)'
        : 'var(--lcars-green)';
      var cpuWidth = Math.min(100, Math.max(2, stats.cpu_pct));
      return '<div class="lcars-resource-gauge">'
        + '<span class="gauge-label">' + truncate(stats.label || pid, 12) + '</span>'
        + '<div class="gauge-bar">'
        + '<div class="gauge-fill" style="width: ' + cpuWidth + '%; background: ' + cpuColor + '"></div>'
        + '</div>'
        + '<span class="gauge-value">CPU ' + stats.cpu_pct.toFixed(0) + '% / ' + stats.rss_mb.toFixed(0) + ' MB</span>'
        + '</div>';
    }).join('');
  }

  // ---------------------------------------------------------------------------
  // Render: agents panel
  // ---------------------------------------------------------------------------

  function renderAgents(sessions) {
    const session = sessions.find(s => s.id === selectedSessionId);
    const agents = session ? (session.agents || []) : [];

    if (agents.length === 0) {
      dom.agentsPanel.innerHTML = '<div class="lcars-empty">NO AGENTS</div>';
      return;
    }

    dom.agentsPanel.innerHTML = agents.map(agent => {
      const shortId = (agent.id || '').slice(0, 8);
      const model = shortModelName(agent.model);
      const tc = agent.token_counts || {};

      return `
        <div class="lcars-agent-card">
          <div>
            <span class="status-${agent.status}"></span>
            <strong>${shortId}</strong> &middot; ${model.toUpperCase()}
          </div>
          <div class="session-meta">${formatDuration(agent.start_time)} &middot; ${formatTokens(tc.input)} IN / ${formatTokens(tc.output)} OUT &middot; ${formatCost(agent.cost_usd)}</div>
          <div class="session-meta">${truncate(agent.task_summary, 80)}</div>
        </div>
      `;
    }).join('');
  }

  // ---------------------------------------------------------------------------
  // Render: agent tree
  // ---------------------------------------------------------------------------

  function renderAgentTree(agentTrees, selectedId) {
    if (!dom.agentsPanel) return;
    var allNodes = agentTrees ? agentTrees[selectedId] : null;
    // Filter to active/idle agents only
    var nodes = allNodes ? allNodes.filter(function(n) { return n.agent.status === 'active' || n.agent.status === 'idle'; }) : [];
    if (!nodes || nodes.length === 0) {
      dom.agentsPanel.innerHTML = '<div class="lcars-empty">NO AGENTS</div>';
      return;
    }

    function renderNode(node, depth) {
      var a = node.agent;
      var shortId = (a.id || '').slice(0, 8);
      var model = shortModelName(a.model);
      var tc = a.token_counts || {};
      var indent = depth * 16;
      var childrenHtml = (node.children || []).map(function (c) { return renderNode(c, depth + 1); }).join('');

      return '<div class="lcars-agent-card" style="margin-left: ' + indent + 'px;' + (depth > 0 ? ' border-left-color: var(--lcars-blue);' : '') + '">'
        + '<div>'
        + '<span class="status-' + a.status + '"></span> '
        + '<strong>' + shortId + '</strong> &middot; ' + (model || '').toUpperCase()
        + '</div>'
        + '<div class="session-meta">' + formatDuration(a.start_time) + ' &middot; ' + formatTokens(tc.input) + ' IN / ' + formatTokens(tc.output) + ' OUT &middot; ' + formatCost(a.cost_usd) + '</div>'
        + '<div class="session-meta">' + truncate(a.task_summary, 60) + '</div>'
        + '</div>'
        + childrenHtml;
    }

    dom.agentsPanel.innerHTML = nodes.map(function (n) { return renderNode(n, 0); }).join('');
  }

  // ---------------------------------------------------------------------------
  // Render: session events
  // ---------------------------------------------------------------------------

  var lastSessionEventsKey = null;

  function renderSessionEvents(sessionEvents, selectedId) {
    if (!dom.sessionDetail) return;
    var events = sessionEvents ? sessionEvents[selectedId] : null;
    if (!events || events.length === 0) return;

    // Skip re-render if events haven't changed
    var eventsKey = selectedId + ':' + events.length + ':' + (events.length > 0 ? events[events.length - 1].timestamp : '');
    if (eventsKey === lastSessionEventsKey) return;
    lastSessionEventsKey = eventsKey;

    // Show latest first (descending)
    var sorted = events.slice().reverse();

    var streamHtml = '<div style="margin: 12px 0 8px; border-top: 1px solid rgba(255,255,255,0.08);"></div>'
      + '<div style="font-size: 11px; color: var(--lcars-lavender); text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 6px;">Recent Activity</div>'
      + '<div class="lcars-session-events">';

    for (var i = 0; i < sorted.length; i++) {
      var evt = sorted[i];
      streamHtml += '<div class="lcars-event-row lcars-no-anim" style="padding: 2px 0; font-size: 11px;">'
        + '<span class="event-time">' + formatTime(evt.timestamp) + '</span> '
        + '<span class="event-type" style="min-width: 60px; display: inline-block;">' + (evt.event_type || '--') + '</span> '
        + '<span class="event-summary">' + truncate(evt.summary, 60) + '</span>'
        + '</div>';
    }

    streamHtml += '</div>';
    dom.sessionDetail.insertAdjacentHTML('beforeend', streamHtml);
  }

  // ---------------------------------------------------------------------------
  // Render: activity feed
  // ---------------------------------------------------------------------------

  function eventKey(evt) {
    return `${evt.timestamp}|${evt.session_slug}|${evt.event_type}|${evt.summary}`;
  }

  function renderActivityFeed(events) {
    if (!events || events.length === 0) {
      if (renderedEventKeys.size === 0) {
        dom.activityFeed.innerHTML = '<div class="lcars-empty">NO ACTIVITY</div>';
      }
      return;
    }

    const feed = dom.activityFeed;
    const wasAtBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 40;

    // On first render, populate everything without animation
    if (renderedEventKeys.size === 0) {
      const display = events.slice(-MAX_EVENTS);
      feed.innerHTML = display.map(evt => {
        const color = sessionColor(evt.session_slug);
        renderedEventKeys.add(eventKey(evt));
        return `
          <div class="lcars-event-row lcars-no-anim">
            <span class="event-time">${formatTime(evt.timestamp)}</span>
            <span class="event-slug" style="color: ${color}">${evt.session_slug || '--'}</span>
            <span class="event-type">${evt.event_type || '--'}</span>
            <span class="event-summary">${truncate(evt.summary, 80)}</span>
          </div>
        `;
      }).join('');
      feed.scrollTop = feed.scrollHeight;
      return;
    }

    // Subsequent renders: only append new events
    const display = events.slice(-MAX_EVENTS);
    let added = false;

    for (const evt of display) {
      const key = eventKey(evt);
      if (renderedEventKeys.has(key)) continue;
      renderedEventKeys.add(key);
      added = true;

      const color = sessionColor(evt.session_slug);
      const row = document.createElement('div');
      row.className = 'lcars-event-row';
      row.innerHTML = `
        <span class="event-time">${formatTime(evt.timestamp)}</span>
        <span class="event-slug" style="color: ${color}">${evt.session_slug || '--'}</span>
        <span class="event-type">${evt.event_type || '--'}</span>
        <span class="event-summary">${truncate(evt.summary, 80)}</span>
      `;
      feed.appendChild(row);
    }

    // Trim old rows if over MAX_EVENTS
    while (feed.children.length > MAX_EVENTS) {
      feed.removeChild(feed.firstChild);
    }

    // Auto-scroll only if user was already at the bottom
    if (added && wasAtBottom && !userScrolledUp) {
      feed.scrollTop = feed.scrollHeight;
    }
  }

  // ---------------------------------------------------------------------------
  // Diff detection & notifications
  // ---------------------------------------------------------------------------

  function detectChanges(prev, curr) {
    if (!prev) return;

    // Collect terminal_ids we're waiting on — suppress notifications for these
    var pendingTerminalIds = new Set();
    if (pendingTerminalSelect) pendingTerminalIds.add(pendingTerminalSelect);

    function isOwnedByPendingTerminal(session) {
      return session.terminal_id && pendingTerminalIds.has(session.terminal_id);
    }

    const prevSessionIds = new Set((prev.sessions || []).map(s => s.id));
    const currSessionIds = new Set((curr.sessions || []).map(s => s.id));

    // New sessions (skip sessions we just spawned via terminal)
    for (const session of (curr.sessions || [])) {
      if (!prevSessionIds.has(session.id) && !isOwnedByPendingTerminal(session)) {
        sound.newSession();
        showToast(`NEW SESSION: ${formatProject(session.project)}`);
      }
    }

    // Build agent lookup from previous state
    const prevAgentIds = new Set();
    for (const session of (prev.sessions || [])) {
      for (const agent of (session.agents || [])) {
        prevAgentIds.add(agent.id);
      }
    }

    // New agents
    for (const session of (curr.sessions || [])) {
      for (const agent of (session.agents || [])) {
        if (!prevAgentIds.has(agent.id)) {
          sound.agentSpawn();
          showToast(`AGENT SPAWNED: ${shortModelName(agent.model).toUpperCase()} IN ${session.slug}`);
        }
      }
    }

    // Session completed (skip sessions owned by pending terminals — they start as 'done'
    // briefly before the process is detected)
    const prevStatusMap = new Map((prev.sessions || []).map(s => [s.id, s.status]));
    for (const session of (curr.sessions || [])) {
      if (session.status === 'done' && prevStatusMap.get(session.id) !== 'done'
          && !isOwnedByPendingTerminal(session)) {
        sound.sessionEnd();
        showToast(`SESSION ENDED: ${formatProject(session.project)}`);
      }
    }

    // New activity events
    var prevEventCount = (prev.events || []).length;
    var currEventCount = (curr.events || []).length;
    if (currEventCount > prevEventCount) {
      lcarsChirp(660, 0.06, 0.08);
    }
  }

  // ---------------------------------------------------------------------------
  // Full render
  // ---------------------------------------------------------------------------

  /**
   * Merge LCARS terminal entries into sessions list.
   * Terminals that already match a session (by terminal_id) are skipped.
   * Unmatched terminals get synthetic session entries so they appear in the list.
   */
  function mergeTerminalSessions(sessions, lcarsTerminals) {
    if (!lcarsTerminals || lcarsTerminals.length === 0) return sessions;

    var matchedTerminalIds = new Set();
    for (var i = 0; i < sessions.length; i++) {
      if (sessions[i].terminal_id) {
        matchedTerminalIds.add(sessions[i].terminal_id);
      }
    }

    var merged = sessions.slice();
    for (var j = 0; j < lcarsTerminals.length; j++) {
      var t = lcarsTerminals[j];
      if (matchedTerminalIds.has(t.terminal_id)) continue;
      // Create a synthetic session entry
      merged.unshift({
        id: 'lcars-' + t.terminal_id,
        slug: 'lcars-terminal',
        project: t.cwd,
        cwd: t.cwd,
        branch: '',
        version: '',
        start_time: new Date().toISOString(),
        last_activity: new Date().toISOString(),
        status: 'active',
        message_counts: {},
        token_counts: {},
        cost_usd: 0,
        agents: [],
        terminal_id: t.terminal_id,
      });
    }
    return merged;
  }

  function render(state) {
    mergedSessions = mergeTerminalSessions(state.sessions || [], state.lcars_terminals || []);
    renderStats(mergedSessions, state.total_cost_usd || 0);
    renderSessionList(mergedSessions);
    renderSessionDetail(mergedSessions);
    renderSessionEvents(state.session_events, selectedSessionId);
    renderAgentTree(state.agent_trees, selectedSessionId);
    renderActivityFeed(state.events || []);
    renderResourceStrip(state.resources);
    updatePanelLayout();
    updateWaveformData(state);

    // Activate data stream animation when data is flowing
    var body = document.querySelector('.lcars-body');
    if (body) {
      var hasActive = mergedSessions.some(function (s) { return s.status === 'active'; });
      body.style.setProperty('--stream-state', hasActive ? 'running' : 'paused');
    }
  }

  // ---------------------------------------------------------------------------
  // WebSocket
  // ---------------------------------------------------------------------------

  function connect() {
    if (ws) {
      try { ws.close(); } catch (_) { /* noop */ }
    }

    ws = new WebSocket(WS_URL);

    ws.addEventListener('open', () => {
      showToast('CONNECTED');
    });

    ws.addEventListener('message', (event) => {
      let msg;
      try {
        msg = JSON.parse(event.data);
      } catch {
        return;
      }

      if (msg.type !== 'state') return;

      previousState = currentState;
      currentState = msg;

      detectChanges(previousState, currentState);

      // Skip re-render if data hasn't changed (prevents DOM flicker)
      var stateKey = JSON.stringify(msg.sessions) + JSON.stringify(msg.events);
      if (stateKey !== lastStateKey) {
        lastStateKey = stateKey;
        render(currentState);
      }
    });

    ws.addEventListener('close', () => {
      showToast('DISCONNECTED');
      sound.alert();
      setTimeout(connect, RECONNECT_DELAY_MS);
    });

    ws.addEventListener('error', () => {
      // The close event will fire after this, triggering reconnection
    });
  }

  // ---------------------------------------------------------------------------
  // Clock
  // ---------------------------------------------------------------------------

  function updateClock() {
    const now = new Date();
    dom.clock.textContent = now.toLocaleTimeString('en-GB', { hour12: false });

    // Update dashboard uptime
    if (dom.dashboardUptime) {
      var elapsed = Date.now() - dashboardStartTime;
      var hrs = Math.floor(elapsed / 3600000);
      var mins = Math.floor((elapsed % 3600000) / 60000);
      var secs = Math.floor((elapsed % 60000) / 1000);
      dom.dashboardUptime.textContent = String(hrs).padStart(2, '0') + ':' + String(mins).padStart(2, '0') + ':' + String(secs).padStart(2, '0');
    }
  }

  // ---------------------------------------------------------------------------
  // Scroll tracking for activity feed
  // ---------------------------------------------------------------------------

  function setupScrollTracking() {
    dom.activityFeed.addEventListener('scroll', () => {
      const feed = dom.activityFeed;
      userScrolledUp = (feed.scrollHeight - feed.scrollTop - feed.clientHeight) > 40;
    });
  }

  // ---------------------------------------------------------------------------
  // Button handlers
  // ---------------------------------------------------------------------------

  function setupButtons() {
    dom.btnRefresh.addEventListener('click', () => {
      sound.click();
      connect();
    });

    dom.btnSound.addEventListener('click', () => {
      soundEnabled = !soundEnabled;
      dom.btnSound.textContent = soundEnabled ? 'SOUND: ON' : 'SOUND: OFF';
      // Play a chirp to confirm sound is on
      if (soundEnabled) {
        sound.click();
      }
    });


    dom.btnNewSession.addEventListener('click', () => {
      sound.click();
      createNewSession();
    });
  }

  // ---------------------------------------------------------------------------
  // Activity Timeline & Tool Phase Visualisation
  // ---------------------------------------------------------------------------

  let timelineCanvas = null;
  let timelineCtx = null;
  let timelineAnimId = null;
  let lastFrameTime = 0;
  const TARGET_FRAME_MS = 1000 / 30;

  // Rolling history: array of { timestamp, sessions: [{slug, status, color, phase}] }
  var timelineHistory = [];
  var TIMELINE_WINDOW_MS = 5 * 60 * 1000; // 5 minute window

  // Tool phase classification — keyed on tool name, not summary text
  var PHASE_COLORS = {
    RESEARCH: '#90A0D0',  // blue — reading, searching codebase
    BUILD:    '#FFCC99',  // gold — editing, writing files
    EXECUTE:  '#80D090',  // green — running commands
    COMMS:    '#C0A0C0',  // lavender — web fetch, search, external
    IDLE:     '#707898',  // dim — no recent tools
  };

  var TOOL_PHASE_MAP = {
    'Read': 'RESEARCH', 'Grep': 'RESEARCH', 'Glob': 'RESEARCH',
    'Agent': 'RESEARCH', 'ListMcpResourcesTool': 'RESEARCH',
    'Edit': 'BUILD', 'Write': 'BUILD', 'NotebookEdit': 'BUILD',
    'Bash': 'EXECUTE',
    'WebSearch': 'COMMS', 'WebFetch': 'COMMS',
  };

  function extractToolName(summary) {
    // Summary format is "ToolName(args)" or just "ToolName"
    var paren = summary.indexOf('(');
    return paren > 0 ? summary.substring(0, paren) : summary;
  }

  function classifyPhase(events) {
    if (!events || events.length === 0) return 'IDLE';
    // Look at last 5 tool_use events to determine current phase
    var toolEvents = events.filter(function(e) {
      return (e.event_type || '').toLowerCase() === 'tool_use';
    }).slice(-5);
    if (toolEvents.length === 0) return 'IDLE';

    // Most recent tool wins for the phase
    for (var i = toolEvents.length - 1; i >= 0; i--) {
      var toolName = extractToolName(toolEvents[i].summary || '');
      // Check direct match first
      if (TOOL_PHASE_MAP[toolName]) return TOOL_PHASE_MAP[toolName];
      // Check MCP tools (mcp__* prefix)
      if (toolName.indexOf('mcp__') === 0) return 'COMMS';
    }
    return 'IDLE';
  }

  function initWaveform() {
    timelineCanvas = document.getElementById('waveform-canvas');
    if (!timelineCanvas) return;
    timelineCtx = timelineCanvas.getContext('2d');
    resizeTimeline();
    window.addEventListener('resize', resizeTimeline);
    timelineAnimId = requestAnimationFrame(drawTimeline);
  }

  function resizeTimeline() {
    if (!timelineCanvas) return;
    var rect = timelineCanvas.parentElement.getBoundingClientRect();
    timelineCanvas.width = rect.width;
    timelineCanvas.height = rect.height - 28;
  }

  function updateWaveformData(state) {
    if (!state || !state.sessions) return;
    var now = Date.now();

    // Build snapshot of current session states with phases
    var snapshot = {
      timestamp: now,
      sessions: []
    };

    var sessions = state.sessions || [];
    for (var i = 0; i < sessions.length; i++) {
      var s = sessions[i];
      if (s.status === 'done') continue; // only track active/idle
      var events = state.session_events ? state.session_events[s.id] : null;
      var phase = classifyPhase(events);
      snapshot.sessions.push({
        slug: s.slug || s.project || s.id,
        status: s.status,
        color: sessionColor(s.slug),
        phase: phase
      });
    }

    timelineHistory.push(snapshot);

    // Trim to window
    var cutoff = now - TIMELINE_WINDOW_MS;
    while (timelineHistory.length > 0 && timelineHistory[0].timestamp < cutoff) {
      timelineHistory.shift();
    }
  }

  function drawTimeline(timestamp) {
    timelineAnimId = requestAnimationFrame(drawTimeline);
    if (timestamp - lastFrameTime < TARGET_FRAME_MS) return;
    lastFrameTime = timestamp;

    var ctx = timelineCtx;
    if (!ctx || !timelineCanvas) return;
    var w = timelineCanvas.width;
    var h = timelineCanvas.height;
    if (w === 0 || h === 0) return;

    ctx.clearRect(0, 0, w, h);

    // Collect unique session slugs from history
    var slugSet = {};
    for (var i = 0; i < timelineHistory.length; i++) {
      var snap = timelineHistory[i];
      for (var j = 0; j < snap.sessions.length; j++) {
        slugSet[snap.sessions[j].slug] = snap.sessions[j].color;
      }
    }
    var slugs = Object.keys(slugSet);
    if (slugs.length === 0) {
      // Empty state — draw "NO ACTIVE SESSIONS" text
      ctx.fillStyle = '#707898';
      ctx.font = '11px Antonio, sans-serif';
      ctx.textAlign = 'center';
      ctx.letterSpacing = '2px';
      ctx.fillText('NO ACTIVE SESSIONS', w / 2, h / 2);
      return;
    }

    var now = Date.now();
    var leftPad = 80;  // space for labels
    var rightPad = 60; // space for phase indicator
    var topPad = 18;   // space for header
    var bottomPad = 16; // space for time axis
    var chartW = w - leftPad - rightPad;
    var chartH = h - topPad - bottomPad;
    var laneH = Math.min(20, Math.max(8, (chartH - 4) / slugs.length));
    var laneGap = 2;

    // Header
    ctx.fillStyle = '#707898';
    ctx.font = '10px Antonio, sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText('SESSION ACTIVITY', leftPad, 12);

    // Time axis labels
    ctx.fillStyle = '#505870';
    ctx.font = '9px Courier New, monospace';
    ctx.textAlign = 'center';
    var intervals = [0, 1, 2, 3, 4, 5];
    for (var t = 0; t < intervals.length; t++) {
      var mins = intervals[t];
      var xPos = leftPad + chartW - (mins / 5) * chartW;
      ctx.fillText('-' + mins + 'm', xPos, h - 3);
    }

    // Draw lanes
    for (var si = 0; si < slugs.length; si++) {
      var slug = slugs[si];
      var laneY = topPad + si * (laneH + laneGap);
      var color = slugSet[slug];

      // Label
      ctx.fillStyle = color;
      ctx.font = '10px Antonio, sans-serif';
      ctx.textAlign = 'right';
      ctx.fillText(truncate(slug, 10).toUpperCase(), leftPad - 6, laneY + laneH / 2 + 4);

      // Lane background
      ctx.fillStyle = 'rgba(255, 255, 255, 0.02)';
      ctx.fillRect(leftPad, laneY, chartW, laneH);

      // Draw activity segments from history
      var lastPhase = null;
      var segStartX = null;

      for (var hi = 0; hi < timelineHistory.length; hi++) {
        var snap = timelineHistory[hi];
        var age = now - snap.timestamp;
        var x = leftPad + chartW - (age / TIMELINE_WINDOW_MS) * chartW;
        if (x < leftPad) continue;

        // Find this session in snapshot
        var found = null;
        for (var fi = 0; fi < snap.sessions.length; fi++) {
          if (snap.sessions[fi].slug === slug) { found = snap.sessions[fi]; break; }
        }

        if (found && found.status === 'active') {
          var phase = found.phase || 'IDLE';
          if (lastPhase !== phase || segStartX === null) {
            // Draw previous segment
            if (segStartX !== null && lastPhase) {
              var segColor = PHASE_COLORS[lastPhase] || color;
              ctx.fillStyle = segColor;
              ctx.globalAlpha = 0.7;
              ctx.fillRect(segStartX, laneY + 1, x - segStartX, laneH - 2);
              ctx.globalAlpha = 1;
            }
            segStartX = x;
            lastPhase = phase;
          }
        } else if (found && found.status === 'idle') {
          // Draw previous active segment
          if (segStartX !== null && lastPhase) {
            var segColor = PHASE_COLORS[lastPhase] || color;
            ctx.fillStyle = segColor;
            ctx.globalAlpha = 0.7;
            ctx.fillRect(segStartX, laneY + 1, x - segStartX, laneH - 2);
            ctx.globalAlpha = 1;
          }
          // Idle: dim segment
          segStartX = x;
          lastPhase = 'IDLE';
        } else {
          // Not in snapshot — close any open segment
          if (segStartX !== null && lastPhase) {
            var segColor = PHASE_COLORS[lastPhase] || color;
            ctx.fillStyle = segColor;
            ctx.globalAlpha = 0.7;
            ctx.fillRect(segStartX, laneY + 1, x - segStartX, laneH - 2);
            ctx.globalAlpha = 1;
          }
          segStartX = null;
          lastPhase = null;
        }
      }

      // Close final segment to right edge
      if (segStartX !== null && lastPhase) {
        var segColor = PHASE_COLORS[lastPhase] || color;
        ctx.fillStyle = segColor;
        ctx.globalAlpha = 0.7;
        ctx.fillRect(segStartX, laneY + 1, leftPad + chartW - segStartX, laneH - 2);
        ctx.globalAlpha = 1;

        // Glow on right edge for currently active
        ctx.shadowColor = segColor;
        ctx.shadowBlur = 6;
        ctx.fillRect(leftPad + chartW - 3, laneY + 1, 3, laneH - 2);
        ctx.shadowBlur = 0;
      }
    }

    // Phase legend (bottom-right corner)
    var legendX = leftPad + chartW + 8;
    var legendY = topPad;
    ctx.font = '9px Antonio, sans-serif';
    ctx.textAlign = 'left';
    var phases = ['RESEARCH', 'BUILD', 'EXECUTE', 'COMMS', 'IDLE'];
    for (var pi = 0; pi < phases.length; pi++) {
      var py = legendY + pi * 14;
      ctx.fillStyle = PHASE_COLORS[phases[pi]];
      ctx.fillRect(legendX, py, 8, 8);
      ctx.fillStyle = '#707898';
      ctx.fillText(phases[pi], legendX + 12, py + 8);
    }
  }

  // ---------------------------------------------------------------------------
  // Keyboard Shortcuts
  // ---------------------------------------------------------------------------

  function setupKeyboardShortcuts() {
    document.addEventListener('keydown', function(e) {
      // Don't handle shortcuts when typing in inputs or terminal is focused
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

      var sessions = mergedSessions || [];

      switch (e.key) {
        case 'j':
        case 'ArrowDown': {
          e.preventDefault();
          var idx = sessions.findIndex(function(s) { return s.id === selectedSessionId; });
          if (idx < sessions.length - 1) {
            selectedSessionId = sessions[idx + 1].id;
            sound.click();
            render(currentState);
          }
          break;
        }
        case 'k':
        case 'ArrowUp': {
          e.preventDefault();
          var idx = sessions.findIndex(function(s) { return s.id === selectedSessionId; });
          if (idx > 0) {
            selectedSessionId = sessions[idx - 1].id;
            sound.click();
            render(currentState);
          }
          break;
        }
        case 'Enter': {
          updatePanelLayout();
          break;
        }
        case 't': {
          e.preventDefault();
          if (typeof createNewSession === 'function') createNewSession();
          break;
        }
        case 'Escape': {
          selectedSessionId = null;
          render(currentState);
          break;
        }
        case '?': {
          e.preventDefault();
          toggleShortcutOverlay();
          break;
        }
      }
    });
  }

  function toggleShortcutOverlay() {
    var overlay = document.getElementById('shortcut-overlay');
    if (overlay) {
      overlay.remove();
      return;
    }
    overlay = document.createElement('div');
    overlay.id = 'shortcut-overlay';
    overlay.className = 'lcars-shortcut-overlay';
    overlay.innerHTML = '<div class="lcars-shortcut-panel">'
      + '<div class="lcars-section-bar lcars-bg-lavender">Keyboard Shortcuts</div>'
      + '<div class="lcars-shortcut-list">'
      + '<div><kbd>j</kbd> / <kbd>↓</kbd> — Next session</div>'
      + '<div><kbd>k</kbd> / <kbd>↑</kbd> — Previous session</div>'
      + '<div><kbd>Enter</kbd> — Select session</div>'
      + '<div><kbd>t</kbd> — New terminal</div>'
      + '<div><kbd>Esc</kbd> — Deselect</div>'
      + '<div><kbd>?</kbd> — Toggle this overlay</div>'
      + '</div>'
      + '</div>';
    overlay.addEventListener('click', function() { overlay.remove(); });
    document.body.appendChild(overlay);
  }

  // ---------------------------------------------------------------------------
  // Initialization
  // ---------------------------------------------------------------------------

  function init() {
    cacheDom();
    setupButtons();
    setupScrollTracking();
    setupKeyboardShortcuts();
    updateClock();
    setInterval(updateClock, 1000);
    connect();
    initWaveform();
  }

  // Boot when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
