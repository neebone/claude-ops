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

  const WS_URL = 'ws://localhost:1701/ws';
  const RECONNECT_DELAY_MS = 3000;
  const TOAST_DURATION_MS = 4000;
  const MAX_EVENTS = 50;
  const SESSION_COLORS = ['#DDA88A', '#B399B3', '#8E9ED6', '#B07AA0', '#D4906A', '#9CC5E0'];

  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------

  let ws = null;
  let previousState = null;
  let currentState = null;
  let selectedSessionId = null;
  let soundEnabled = false;
  let audioCtx = null;
  let userScrolledUp = false;
  let renderedEventKeys = new Set();

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
    dom.clock = document.getElementById('clock');
    dom.toastContainer = document.getElementById('toast-container');
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
  }

  // ---------------------------------------------------------------------------
  // Render: session list
  // ---------------------------------------------------------------------------

  function renderSessionList(sessions) {
    if (!sessions || sessions.length === 0) {
      dom.sessionList.innerHTML = '<div class="lcars-empty">NO ACTIVE SESSIONS</div>';
      return;
    }

    // Auto-select the first session if none selected or selected no longer exists
    if (!selectedSessionId || !sessions.find(s => s.id === selectedSessionId)) {
      selectedSessionId = sessions[0].id;
    }

    dom.sessionList.innerHTML = sessions.map(session => {
      const color = sessionColor(session.slug);
      const selected = session.id === selectedSessionId ? ' selected' : '';
      const agentCount = session.agents ? session.agents.length : 0;
      const agentLine = agentCount > 0
        ? `<div class="session-agents-summary">${agentCount} AGENT${agentCount > 1 ? 'S' : ''}</div>`
        : '';

      return `
        <div class="lcars-session-item${selected}" data-session-id="${session.id}" style="border-left-color: ${color}">
          <div class="session-name">
            <span class="status-${session.status}" title="${session.status}"></span>
            ${formatProject(session.project)}
          </div>
          <div class="session-meta">${session.branch || '--'} &middot; ${formatDuration(session.start_time)} &middot; ${formatCost(session.cost_usd)}</div>
          ${agentLine}
        </div>
      `;
    }).join('');

    // Attach click handlers
    dom.sessionList.querySelectorAll('.lcars-session-item').forEach(el => {
      el.addEventListener('click', () => {
        sound.click();
        selectedSessionId = el.dataset.sessionId;
        renderSessionList(currentState.sessions);
        renderSessionDetail(currentState.sessions);
        renderAgents(currentState.sessions);
      });
    });
  }

  // ---------------------------------------------------------------------------
  // Render: session detail
  // ---------------------------------------------------------------------------

  function renderSessionDetail(sessions) {
    const session = sessions.find(s => s.id === selectedSessionId);
    if (!session) {
      dom.sessionDetail.innerHTML = '<div class="lcars-empty">SELECT A SESSION</div>';
      return;
    }

    const mc = session.message_counts || {};
    const tc = session.token_counts || {};

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

    const prevSessionIds = new Set((prev.sessions || []).map(s => s.id));
    const currSessionIds = new Set((curr.sessions || []).map(s => s.id));

    // New sessions
    for (const session of (curr.sessions || [])) {
      if (!prevSessionIds.has(session.id)) {
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

    // Session completed
    const prevStatusMap = new Map((prev.sessions || []).map(s => [s.id, s.status]));
    for (const session of (curr.sessions || [])) {
      if (session.status === 'done' && prevStatusMap.get(session.id) !== 'done') {
        sound.sessionEnd();
        showToast(`SESSION ENDED: ${formatProject(session.project)}`);
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Full render
  // ---------------------------------------------------------------------------

  function render(state) {
    renderStats(state.sessions || [], state.total_cost_usd || 0);
    renderSessionList(state.sessions || []);
    renderSessionDetail(state.sessions || []);
    renderAgents(state.sessions || []);
    renderActivityFeed(state.events || []);
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
      render(currentState);
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
  }

  // ---------------------------------------------------------------------------
  // Initialization
  // ---------------------------------------------------------------------------

  function init() {
    cacheDom();
    setupButtons();
    setupScrollTracking();
    updateClock();
    setInterval(updateClock, 1000);
    connect();
  }

  // Boot when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
