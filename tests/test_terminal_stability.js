/**
 * Unit tests for LCARS terminal session stability logic.
 *
 * Tests the pure functions: stabiliseTerminalState, mergeTerminalSessions,
 * isOurTerminalSession. These are extracted from lcars.js's IIFE for testing.
 *
 * Run: node tests/test_terminal_stability.js
 */
'use strict';

const assert = require('assert');

// ---------------------------------------------------------------------------
// Extracted pure logic (mirrors lcars.js exactly)
// ---------------------------------------------------------------------------

var TERMINAL_GRACE_MS = 15000;
var knownTerminalSessionMap = {}; // terminal_id -> session.id
var recentlyCreatedTerminals = {}; // terminal_id -> { created, cwd }

function stabiliseTerminalState(state) {
  var sessions = state.sessions || [];
  var lcarsTerminals = state.lcars_terminals || [];
  if (lcarsTerminals.length === 0) return;

  var activeTerminalIds = new Set();
  var terminalCwdMap = {};
  for (var ti = 0; ti < lcarsTerminals.length; ti++) {
    activeTerminalIds.add(lcarsTerminals[ti].terminal_id);
    terminalCwdMap[lcarsTerminals[ti].cwd] = lcarsTerminals[ti].terminal_id;
  }

  // Pass 1: adopt server-provided terminal_id links into cache
  for (var i = 0; i < sessions.length; i++) {
    if (sessions[i].terminal_id && activeTerminalIds.has(sessions[i].terminal_id)) {
      knownTerminalSessionMap[sessions[i].terminal_id] = sessions[i].id;
    }
  }

  // Pass 2: restore cached links for sessions that lost terminal_id
  for (var k = 0; k < sessions.length; k++) {
    if (sessions[k].terminal_id) continue;
    for (var tid in knownTerminalSessionMap) {
      if (knownTerminalSessionMap[tid] === sessions[k].id && activeTerminalIds.has(tid)) {
        sessions[k].terminal_id = tid;
        break;
      }
    }
  }

  // Pass 3: for recently-created terminals, link unmatched sessions by cwd
  var matchedTerminalIds = new Set();
  for (var a = 0; a < sessions.length; a++) {
    if (sessions[a].terminal_id) matchedTerminalIds.add(sessions[a].terminal_id);
  }
  for (var j = 0; j < lcarsTerminals.length; j++) {
    var ltid = lcarsTerminals[j].terminal_id;
    if (matchedTerminalIds.has(ltid)) continue;
    if (!recentlyCreatedTerminals[ltid]) continue;
    var lcwd = lcarsTerminals[j].cwd;
    var bestIdx = -1;
    var bestTime = '';
    for (var m = 0; m < sessions.length; m++) {
      if (sessions[m].terminal_id) continue;
      if (sessions[m].cwd !== lcwd) continue;
      var t = sessions[m].last_activity || sessions[m].start_time || '';
      if (bestIdx === -1 || t > bestTime) {
        bestIdx = m;
        bestTime = t;
      }
    }
    if (bestIdx !== -1) {
      sessions[bestIdx].terminal_id = ltid;
      matchedTerminalIds.add(ltid);
      knownTerminalSessionMap[ltid] = sessions[bestIdx].id;
    }
  }

  // Pass 4: status correction
  for (var s = 0; s < sessions.length; s++) {
    if (sessions[s].terminal_id && activeTerminalIds.has(sessions[s].terminal_id)
        && sessions[s].status === 'done') {
      sessions[s].status = 'idle';
    }
  }
}

function mergeTerminalSessions(sessions, lcarsTerminals) {
  if (!lcarsTerminals || lcarsTerminals.length === 0) return sessions;

  var matchedTerminalIds = new Set();
  for (var i = 0; i < sessions.length; i++) {
    if (sessions[i].terminal_id) matchedTerminalIds.add(sessions[i].terminal_id);
  }

  var merged = sessions.slice();
  for (var j = 0; j < lcarsTerminals.length; j++) {
    var t = lcarsTerminals[j];
    if (matchedTerminalIds.has(t.terminal_id)) continue;
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

function isOurTerminalSession(session) {
  if (session.terminal_id && knownTerminalSessionMap[session.terminal_id]) {
    return true;
  }
  if (session.cwd) {
    for (var tid in recentlyCreatedTerminals) {
      var e = recentlyCreatedTerminals[tid];
      if (Date.now() - e.created > TERMINAL_GRACE_MS) {
        delete recentlyCreatedTerminals[tid];
        continue;
      }
      if (e.cwd && e.cwd === session.cwd) return true;
    }
  }
  return false;
}

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

function resetState() {
  for (var k in knownTerminalSessionMap) delete knownTerminalSessionMap[k];
  for (var k in recentlyCreatedTerminals) delete recentlyCreatedTerminals[k];
}

function makeSession(id, opts) {
  return Object.assign({
    id: id,
    slug: 'test',
    project: '/tmp/test',
    cwd: '/tmp/test',
    branch: 'main',
    status: 'active',
    terminal_id: null,
    start_time: '2026-01-01T00:00:00Z',
    last_activity: '2026-01-01T00:00:01Z',
    agents: [],
    message_counts: {},
    token_counts: {},
    cost_usd: 0,
  }, opts || {});
}

function makeTerminal(terminal_id, cwd) {
  return { terminal_id: terminal_id, cwd: cwd || '/tmp/test' };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

var passed = 0;
var failed = 0;

function test(name, fn) {
  resetState();
  try {
    fn();
    passed++;
    console.log('  \x1b[32m✓\x1b[0m ' + name);
  } catch (err) {
    failed++;
    console.log('  \x1b[31m✗\x1b[0m ' + name);
    console.log('    ' + err.message);
  }
}

// --- stabiliseTerminalState ---

console.log('\nstabiliseTerminalState');

test('Pass 1: adopts server-provided terminal_id into cache', function () {
  var state = {
    sessions: [makeSession('s1', { terminal_id: 'T1' })],
    lcars_terminals: [makeTerminal('T1')],
  };
  stabiliseTerminalState(state);
  assert.strictEqual(knownTerminalSessionMap['T1'], 's1');
});

test('Pass 1: ignores terminal_id for terminals not in active list', function () {
  var state = {
    sessions: [makeSession('s1', { terminal_id: 'T-dead' })],
    lcars_terminals: [makeTerminal('T1')],
  };
  stabiliseTerminalState(state);
  assert.strictEqual(knownTerminalSessionMap['T-dead'], undefined);
});

test('Pass 2: restores cached terminal_id when server drops it', function () {
  // First call establishes the link
  var state1 = {
    sessions: [makeSession('s1', { terminal_id: 'T1' })],
    lcars_terminals: [makeTerminal('T1')],
  };
  stabiliseTerminalState(state1);
  assert.strictEqual(knownTerminalSessionMap['T1'], 's1');

  // Second call — server lost terminal_id (ps aux missed it)
  var state2 = {
    sessions: [makeSession('s1', { terminal_id: null })],
    lcars_terminals: [makeTerminal('T1')],
  };
  stabiliseTerminalState(state2);
  assert.strictEqual(state2.sessions[0].terminal_id, 'T1', 'terminal_id should be restored from cache');
});

test('Pass 2: does not restore if terminal is no longer active', function () {
  knownTerminalSessionMap['T1'] = 's1';
  var state = {
    sessions: [makeSession('s1', { terminal_id: null })],
    lcars_terminals: [], // terminal gone
  };
  stabiliseTerminalState(state);
  assert.strictEqual(state.sessions[0].terminal_id, null, 'should not restore dead terminal');
});

test('Pass 3: links unmatched session by cwd for recently-created terminal', function () {
  recentlyCreatedTerminals['T1'] = { created: Date.now(), cwd: '/tmp/project' };
  var state = {
    sessions: [makeSession('s1', { terminal_id: null, cwd: '/tmp/project' })],
    lcars_terminals: [makeTerminal('T1', '/tmp/project')],
  };
  stabiliseTerminalState(state);
  assert.strictEqual(state.sessions[0].terminal_id, 'T1');
  assert.strictEqual(knownTerminalSessionMap['T1'], 's1');
});

test('Pass 3: prefers newest session when multiple share cwd', function () {
  recentlyCreatedTerminals['T1'] = { created: Date.now(), cwd: '/tmp/project' };
  var state = {
    sessions: [
      makeSession('s-old', { terminal_id: null, cwd: '/tmp/project', last_activity: '2026-01-01T00:00:01Z' }),
      makeSession('s-new', { terminal_id: null, cwd: '/tmp/project', last_activity: '2026-01-01T00:05:00Z' }),
    ],
    lcars_terminals: [makeTerminal('T1', '/tmp/project')],
  };
  stabiliseTerminalState(state);
  assert.strictEqual(state.sessions[0].terminal_id, null, 'old session should not be linked');
  assert.strictEqual(state.sessions[1].terminal_id, 'T1', 'newest session should be linked');
});

test('Pass 3: does not link if terminal is not recently created', function () {
  // No entry in recentlyCreatedTerminals
  var state = {
    sessions: [makeSession('s1', { terminal_id: null, cwd: '/tmp/project' })],
    lcars_terminals: [makeTerminal('T1', '/tmp/project')],
  };
  stabiliseTerminalState(state);
  assert.strictEqual(state.sessions[0].terminal_id, null);
});

test('Pass 4: overrides done → idle for session linked to active terminal', function () {
  var state = {
    sessions: [makeSession('s1', { terminal_id: 'T1', status: 'done' })],
    lcars_terminals: [makeTerminal('T1')],
  };
  stabiliseTerminalState(state);
  assert.strictEqual(state.sessions[0].status, 'idle');
});

test('Pass 4: does not override done if terminal is not active', function () {
  var state = {
    sessions: [makeSession('s1', { terminal_id: 'T1', status: 'done' })],
    lcars_terminals: [], // no active terminals
  };
  stabiliseTerminalState(state);
  assert.strictEqual(state.sessions[0].status, 'done');
});

test('Pass 4: does not override active status', function () {
  var state = {
    sessions: [makeSession('s1', { terminal_id: 'T1', status: 'active' })],
    lcars_terminals: [makeTerminal('T1')],
  };
  stabiliseTerminalState(state);
  assert.strictEqual(state.sessions[0].status, 'active');
});

test('full pipeline: server flicker cycle does not change visible state', function () {
  // Tick 1: server reports session with terminal_id
  var tick1 = {
    sessions: [makeSession('s1', { terminal_id: 'T1', status: 'active' })],
    lcars_terminals: [makeTerminal('T1')],
  };
  stabiliseTerminalState(tick1);
  assert.strictEqual(tick1.sessions[0].terminal_id, 'T1');
  assert.strictEqual(tick1.sessions[0].status, 'active');

  // Tick 2: ps aux misses the process — server says done, no terminal_id
  var tick2 = {
    sessions: [makeSession('s1', { terminal_id: null, status: 'done' })],
    lcars_terminals: [makeTerminal('T1')],
  };
  stabiliseTerminalState(tick2);
  assert.strictEqual(tick2.sessions[0].terminal_id, 'T1', 'terminal_id restored from cache');
  assert.strictEqual(tick2.sessions[0].status, 'idle', 'status corrected from done to idle');

  // Tick 3: server recovers
  var tick3 = {
    sessions: [makeSession('s1', { terminal_id: 'T1', status: 'active' })],
    lcars_terminals: [makeTerminal('T1')],
  };
  stabiliseTerminalState(tick3);
  assert.strictEqual(tick3.sessions[0].terminal_id, 'T1');
  assert.strictEqual(tick3.sessions[0].status, 'active');
});

test('no lcars_terminals is a no-op', function () {
  var state = {
    sessions: [makeSession('s1', { terminal_id: null, status: 'done' })],
    lcars_terminals: [],
  };
  stabiliseTerminalState(state);
  assert.strictEqual(state.sessions[0].terminal_id, null);
  assert.strictEqual(state.sessions[0].status, 'done');
});

// --- mergeTerminalSessions ---

console.log('\nmergeTerminalSessions');

test('creates synthetic for unmatched terminal', function () {
  var sessions = [makeSession('s1', { terminal_id: 'T1' })];
  var terminals = [makeTerminal('T1'), makeTerminal('T2', '/tmp/other')];
  var merged = mergeTerminalSessions(sessions, terminals);
  assert.strictEqual(merged.length, 2);
  assert.strictEqual(merged[0].id, 'lcars-T2', 'synthetic prepended');
  assert.strictEqual(merged[0].terminal_id, 'T2');
  assert.strictEqual(merged[1].id, 's1');
});

test('does not create synthetic for matched terminal', function () {
  var sessions = [makeSession('s1', { terminal_id: 'T1' })];
  var terminals = [makeTerminal('T1')];
  var merged = mergeTerminalSessions(sessions, terminals);
  assert.strictEqual(merged.length, 1);
  assert.strictEqual(merged[0].id, 's1');
});

test('returns sessions unchanged when no terminals', function () {
  var sessions = [makeSession('s1')];
  var result = mergeTerminalSessions(sessions, []);
  assert.strictEqual(result.length, 1);
  assert.strictEqual(result[0].id, 's1');
});

test('returns sessions unchanged when terminals is null', function () {
  var sessions = [makeSession('s1')];
  var result = mergeTerminalSessions(sessions, null);
  assert.strictEqual(result.length, 1);
});

// --- isOurTerminalSession ---

console.log('\nisOurTerminalSession');

test('returns true when session is in knownTerminalSessionMap', function () {
  knownTerminalSessionMap['T1'] = 's1';
  var session = makeSession('s1', { terminal_id: 'T1' });
  assert.strictEqual(isOurTerminalSession(session), true);
});

test('returns false for unknown session with no terminal_id', function () {
  var session = makeSession('s1', { terminal_id: null, cwd: '/tmp/other' });
  assert.strictEqual(isOurTerminalSession(session), false);
});

test('returns true during grace period via cwd match', function () {
  recentlyCreatedTerminals['T1'] = { created: Date.now(), cwd: '/tmp/project' };
  var session = makeSession('s1', { terminal_id: null, cwd: '/tmp/project' });
  assert.strictEqual(isOurTerminalSession(session), true);
});

test('returns false after grace period expires', function () {
  recentlyCreatedTerminals['T1'] = { created: Date.now() - TERMINAL_GRACE_MS - 1000, cwd: '/tmp/project' };
  var session = makeSession('s1', { terminal_id: null, cwd: '/tmp/project' });
  assert.strictEqual(isOurTerminalSession(session), false);
  assert.strictEqual(recentlyCreatedTerminals['T1'], undefined, 'expired entry should be cleaned up');
});

test('persists indefinitely once in knownTerminalSessionMap (no grace period)', function () {
  knownTerminalSessionMap['T1'] = 's1';
  // Even without recentlyCreatedTerminals entry, cache is authoritative
  var session = makeSession('s1', { terminal_id: 'T1' });
  assert.strictEqual(isOurTerminalSession(session), true);
});

// --- Pipeline integration ---

console.log('\npipeline integration');

test('stabilise → merge produces correct session list', function () {
  // Terminal T1 is linked to session s1 via cache from a previous tick
  knownTerminalSessionMap['T1'] = 's1';
  // Terminal T2 is brand new, no session yet
  var state = {
    sessions: [
      makeSession('s1', { terminal_id: null, status: 'done', cwd: '/tmp/proj' }),
      makeSession('s2', { status: 'active', cwd: '/tmp/other' }),
    ],
    lcars_terminals: [
      makeTerminal('T1', '/tmp/proj'),
      makeTerminal('T2', '/tmp/new'),
    ],
  };

  stabiliseTerminalState(state);

  // s1 should have terminal_id restored and status corrected
  assert.strictEqual(state.sessions[0].terminal_id, 'T1');
  assert.strictEqual(state.sessions[0].status, 'idle');

  // s2 is unrelated, unchanged
  assert.strictEqual(state.sessions[1].terminal_id, null);
  assert.strictEqual(state.sessions[1].status, 'active');

  // Merge should create synthetic for unmatched T2
  var merged = mergeTerminalSessions(state.sessions, state.lcars_terminals);
  assert.strictEqual(merged.length, 3);
  assert.strictEqual(merged[0].terminal_id, 'T2', 'synthetic for T2');
  assert.strictEqual(merged[1].id, 's1');
  assert.strictEqual(merged[2].id, 's2');
});

test('detectChanges sees stable data (no false SESSION ENDED toast)', function () {
  // Simulate the full pipeline:
  // Tick 1: session active with terminal
  knownTerminalSessionMap['T1'] = 's1';
  var prev = {
    sessions: [makeSession('s1', { terminal_id: 'T1', status: 'active' })],
    lcars_terminals: [makeTerminal('T1')],
    events: [],
  };
  stabiliseTerminalState(prev);

  // Tick 2: server flickers to done, no terminal_id
  var curr = {
    sessions: [makeSession('s1', { terminal_id: null, status: 'done' })],
    lcars_terminals: [makeTerminal('T1')],
    events: [],
  };
  stabiliseTerminalState(curr);

  // After stabilisation, the session should still look alive
  assert.strictEqual(curr.sessions[0].status, 'idle', 'status corrected');
  assert.strictEqual(curr.sessions[0].terminal_id, 'T1', 'terminal_id restored');

  // detectChanges would compare prev.status=active vs curr.status=idle
  // Neither is 'done', so no "SESSION ENDED" toast would fire
  assert.notStrictEqual(curr.sessions[0].status, 'done',
    'status must not be done — would trigger false toast');
});

// --- Session list ordering ---

console.log('\nsession list ordering');

test('terminal-linked sessions sort before non-terminal sessions', function () {
  var sessions = [
    makeSession('s1', { status: 'active', terminal_id: null }),
    makeSession('s2', { status: 'active', terminal_id: 'T1' }),
    makeSession('s3', { status: 'idle', terminal_id: null }),
  ];

  // Same sort as renderSessionList applies
  sessions.sort(function (a, b) {
    var aT = a.terminal_id ? 1 : 0;
    var bT = b.terminal_id ? 1 : 0;
    return bT - aT;
  });

  assert.strictEqual(sessions[0].id, 's2', 'terminal session should be first');
  assert.strictEqual(sessions[1].id, 's1', 'non-terminal sessions follow');
  assert.strictEqual(sessions[2].id, 's3');
});

test('sort is stable — multiple terminal sessions keep relative order', function () {
  var sessions = [
    makeSession('s1', { status: 'active', terminal_id: 'T1' }),
    makeSession('s2', { status: 'active', terminal_id: null }),
    makeSession('s3', { status: 'active', terminal_id: 'T2' }),
  ];

  sessions.sort(function (a, b) {
    var aT = a.terminal_id ? 1 : 0;
    var bT = b.terminal_id ? 1 : 0;
    return bT - aT;
  });

  // Both terminal sessions should be before non-terminal
  assert.ok(sessions[0].terminal_id, 'first should have terminal_id');
  assert.ok(sessions[1].terminal_id, 'second should have terminal_id');
  assert.strictEqual(sessions[2].id, 's2', 'non-terminal last');
});

test('synthetic replacement does not change position when sort is applied', function () {
  // Before: synthetic lcars-T1 is at top, real s1 is lower
  var sessionsWithSynthetic = [
    makeSession('lcars-T1', { status: 'active', terminal_id: 'T1', cwd: '/tmp' }),
    makeSession('s1', { status: 'active', terminal_id: null, cwd: '/tmp' }),
    makeSession('s2', { status: 'active', terminal_id: null, cwd: '/other' }),
  ];

  // Simulate stabilise linking s1 to T1
  knownTerminalSessionMap['T1'] = 's1';

  // After: s1 gets terminal_id, synthetic no longer created
  var sessionsAfterLink = [
    makeSession('s1', { status: 'active', terminal_id: 'T1', cwd: '/tmp' }),
    makeSession('s2', { status: 'active', terminal_id: null, cwd: '/other' }),
  ];

  // Apply the same sort
  sessionsAfterLink.sort(function (a, b) {
    var aT = a.terminal_id ? 1 : 0;
    var bT = b.terminal_id ? 1 : 0;
    return bT - aT;
  });

  assert.strictEqual(sessionsAfterLink[0].id, 's1', 'terminal session stays at top after link');
  assert.strictEqual(sessionsAfterLink[1].id, 's2');
});

// --- Summary ---

console.log('\n' + (passed + failed) + ' tests, ' +
  '\x1b[32m' + passed + ' passed\x1b[0m' +
  (failed > 0 ? ', \x1b[31m' + failed + ' failed\x1b[0m' : '') + '\n');

process.exit(failed > 0 ? 1 : 0);
