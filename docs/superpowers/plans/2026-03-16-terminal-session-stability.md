# Terminal Session Stability Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate all visual flicker (sidebar highlight jumps, panel flips, false toasts) when a terminal session is running.

**Architecture:** The server's `terminal_id` and `status` on sessions are unreliable — `ps aux` can miss processes between polls, causing them to flicker. The fix is to make the client authoritative for terminal-session links (via a persistent cache) and to process all state through this cache before any rendering or change detection runs.

**Tech Stack:** Vanilla JS (lcars.js), Python/FastAPI (server.py)

---

## Problem Analysis

There are **three symptoms** of one root cause:

| Symptom | Trigger | Why it happens |
|---|---|---|
| Sidebar highlight jumps | Session flickers from `active` → `done` | Session moves from active to completed sidebar section |
| Terminal panel flips to detail | Session loses `terminal_id` | `updatePanelLayout` sees non-terminal session |
| "SESSION ENDED" toast fires | `detectChanges` sees status → `done` | `isRecentTerminalSession` grace period (15s) expires |

**Root cause:** `ps aux` intermittently fails to detect the Claude process. When this happens, the server reports the session as `{status: "done", terminal_id: null}`. The client currently processes raw server state for change detection *before* applying its cache, so the flicker propagates to the UI.

## Current Data Flow (broken)

```
Server state (raw) ──→ detectChanges (sees flicker) ──→ mergeTerminalSessions (fixes flicker) ──→ render
```

`detectChanges` fires toasts based on uncorrected data. The cache fix in `mergeTerminalSessions` runs too late.

## Target Data Flow (correct)

```
Server state (raw) ──→ stabiliseTerminalState (fixes flicker) ──→ detectChanges (sees stable data) ──→ render
```

A single stabilisation function runs first, correcting both `terminal_id` and `status` before anything else touches the data. `mergeTerminalSessions` still creates synthetics for unmatched terminals but no longer needs to fix flicker.

## File Structure

- Modify: `src/claude_ops/static/lcars.js` — all changes are in this file
- Modify: `src/claude_ops/static/index.html` — cache-bust version bump
- No server changes needed — the client handles the instability

## Key Design Decisions

1. **`knownTerminalSessionMap`** (terminal_id → session_id) is the single source of truth once a link is established. It persists for the page lifetime.

2. **Status override:** If a session is linked to an active terminal but the server says `done`, override to `idle`. The terminal is still alive — `ps aux` just missed the process.

3. **Toast suppression:** Replace the 15-second `recentlyCreatedTerminals` grace period approach with a check against `knownTerminalSessionMap`. If a session is linked to a live terminal, suppress new/ended toasts indefinitely.

4. **`updatePanelLayout`:** Pure function — just checks `isTerminalSession(selectedSession)`. No guards, no special cases.

5. **`detectChanges`:** Runs AFTER stabilisation, so it sees corrected `terminal_id` and `status`. No false toasts.

---

### Task 1: Extract `stabiliseTerminalState` and reorder the data pipeline

This task pulls the terminal_id restoration and status correction out of `mergeTerminalSessions` into a dedicated function that runs first, before `detectChanges`.

**Files:**
- Modify: `src/claude_ops/static/lcars.js:1036-1114` (mergeTerminalSessions)
- Modify: `src/claude_ops/static/lcars.js:1158-1187` (WS message handler)
- Modify: `src/claude_ops/static/lcars.js:1116-1133` (render function)

- [ ] **Step 1: Write `stabiliseTerminalState` function**

This function takes the raw server state and mutates sessions in-place to restore cached `terminal_id` and correct `status` flicker. It runs before `detectChanges` and before `render`.

Replace the existing `mergeTerminalSessions` and `knownTerminalSessionMap` block (lines ~1036-1114) with:

```javascript
// Client-side terminal→session link cache. Once a link is established,
// it persists for the page lifetime. This is the single source of truth.
var knownTerminalSessionMap = {}; // terminal_id -> session.id

function stabiliseTerminalState(state) {
    var sessions = state.sessions || [];
    var lcarsTerminals = state.lcars_terminals || [];
    if (lcarsTerminals.length === 0) return;

    // Build lookup of active terminal IDs and cwds
    var activeTerminalIds = new Set();
    var terminalCwdMap = {}; // cwd -> terminal_id
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
    for (var m = 0; m < sessions.length; m++) {
        if (sessions[m].terminal_id) continue;
        if (!sessions[m].cwd) continue;
        var cwdTid = terminalCwdMap[sessions[m].cwd];
        if (!cwdTid || matchedTerminalIds.has(cwdTid)) continue;
        if (!recentlyCreatedTerminals[cwdTid]) continue;
        sessions[m].terminal_id = cwdTid;
        matchedTerminalIds.add(cwdTid);
        knownTerminalSessionMap[cwdTid] = sessions[m].id;
    }

    // Pass 4: status correction — if a session is linked to an active terminal
    // but server says "done", override to "idle" (ps aux just missed it)
    for (var s = 0; s < sessions.length; s++) {
        if (sessions[s].terminal_id && activeTerminalIds.has(sessions[s].terminal_id)
            && sessions[s].status === 'done') {
            sessions[s].status = 'idle';
        }
    }
}
```

- [ ] **Step 2: Simplify `mergeTerminalSessions`**

After `stabiliseTerminalState` runs, `mergeTerminalSessions` only needs to create synthetic entries for unmatched terminals. All the cache/restoration logic is gone.

```javascript
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
```

- [ ] **Step 3: Reorder the WS message handler pipeline**

Change the WS message handler so `stabiliseTerminalState` runs BEFORE `detectChanges`:

```javascript
ws.addEventListener('message', (event) => {
    if (myId !== wsId) return;
    let msg;
    try {
        msg = JSON.parse(event.data);
    } catch {
        return;
    }

    if (msg.type !== 'state') return;

    try {
        previousState = currentState;
        currentState = msg;

        // Stabilise BEFORE change detection — corrects terminal_id and status
        stabiliseTerminalState(currentState);

        detectChanges(previousState, currentState);

        // Always update timeline data and phase detection (not gated by stateKey)
        updateWaveformData(currentState);

        // Skip re-render if data hasn't changed (prevents DOM flicker)
        var stateKey = JSON.stringify(msg.sessions) + JSON.stringify(msg.events);
        if (stateKey !== lastStateKey) {
            lastStateKey = stateKey;
            render(currentState);
        }
    } catch (err) {
        console.error('LCARS render error:', err);
    }
});
```

- [ ] **Step 4: Verify `updatePanelLayout` is clean**

Confirm `updatePanelLayout` has NO special guards — just `isTerminalSession(session)`:

```javascript
function updatePanelLayout() {
    var session = getSelectedSession();
    var isTerminal = isTerminalSession(session);

    if (isTerminal) {
        var wasHidden = dom.panelTerminal.style.display === 'none';
        dom.detailView.style.display = 'none';
        dom.panelTerminal.style.display = 'flex';
        var termId = session.terminal_id || activeTerminalId;
        if (termId) connectTerminal(termId, wasHidden);
    } else {
        dom.detailView.style.display = 'flex';
        dom.panelTerminal.style.display = 'none';
    }
}
```

- [ ] **Step 5: Run server, hard-refresh browser, verify basic rendering**

Run: Open browser, check sessions appear, check no console errors.

- [ ] **Step 6: Commit**

```bash
git add src/claude_ops/static/lcars.js
git commit -m "refactor(lcars): extract stabiliseTerminalState, reorder data pipeline"
```

---

### Task 2: Replace grace-period toast suppression with terminal-link check

Currently `isRecentTerminalSession` uses a 15-second grace period from `recentlyCreatedTerminals`. After 15s, status flicker causes false toasts. Replace with a check against `knownTerminalSessionMap`.

**Files:**
- Modify: `src/claude_ops/static/lcars.js:948-975` (isRecentTerminalSession)

- [ ] **Step 1: Rewrite `isRecentTerminalSession` to `isOurTerminalSession`**

The function should return true if the session is linked to one of our LCARS terminals, regardless of how long ago it was created. Keep the cwd grace fallback only for the initial linking period (before `knownTerminalSessionMap` has the entry).

```javascript
function isOurTerminalSession(session) {
    // Check if this session is linked to one of our terminals via the cache
    if (session.terminal_id && knownTerminalSessionMap[session.terminal_id]) {
        return true;
    }

    // Fallback for the first few seconds: match by cwd against recently created
    // terminals (before stabiliseTerminalState has linked them)
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
```

- [ ] **Step 2: Update all call sites from `isRecentTerminalSession` to `isOurTerminalSession`**

There are two call sites in `detectChanges`:
- Line ~984: `!isRecentTerminalSession(session)` → `!isOurTerminalSession(session)`
- Line ~1013: `!isRecentTerminalSession(session)` → `!isOurTerminalSession(session)`

- [ ] **Step 3: Verify no stale references**

Run: `grep -n 'isRecentTerminalSession' src/claude_ops/static/lcars.js`
Expected: no results

- [ ] **Step 4: Commit**

```bash
git add src/claude_ops/static/lcars.js
git commit -m "fix(lcars): replace grace-period toast suppression with terminal-link check"
```

---

### Task 3: Version bump, restart, and end-to-end verification

**Files:**
- Modify: `src/claude_ops/static/index.html` — version bump

- [ ] **Step 1: Bump cache-bust version**

In `index.html`, change `lcars.js?v=30` to `lcars.js?v=31`.

- [ ] **Step 2: Restart server**

```bash
kill $(lsof -ti :1701) 2>/dev/null
sleep 1
python -c "from claude_ops.server import start_web_server; start_web_server()" &
sleep 4
curl -s http://localhost:1701/ | grep lcars.js
```

Expected: `lcars.js?v=31`

- [ ] **Step 3: Hard-refresh browser and verify all scenarios**

Test matrix (all should pass):

| Scenario | Expected |
|---|---|
| Page load, no terminal | Sessions appear, first active is selected |
| Click different sessions | Detail panel shows for each, highlight follows |
| Create new terminal | Terminal panel shows, sidebar highlights synthetic |
| Send first message in terminal | No toast, no sidebar jump, terminal stays |
| Wait 30+ seconds, terminal still running | No "SESSION ENDED" toast, no highlight jump |
| Click a different session while terminal runs | Detail panel shows for that session |
| Click back to terminal session | Terminal panel shows again |
| Keyboard j/k between sessions | Panel switches between terminal and detail |

- [ ] **Step 4: Run existing tests**

```bash
pytest tests/test_server.py -v
```

Expected: 27 passed

- [ ] **Step 5: Commit version bump**

```bash
git add src/claude_ops/static/index.html
git commit -m "chore(lcars): bump JS version to v31 for terminal stability fixes"
```
