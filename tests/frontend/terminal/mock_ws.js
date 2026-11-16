/*
 * tests/frontend/terminal/mock_ws.js — package 8b, TEST ONLY (never served by the app).
 *
 * A scripted stand-in for the Port-Key WebSocket server (docs/ws-protocol.md), installed by
 * replacing window.WebSocket for /ws/terminal and /ws/install. Injected with add_init_script
 * after tests/frontend/mock/data.js and server.js. It also answers GET and DELETE
 * /api/sessions from its own sessions, which survive a page reload (sessionStorage).
 *
 * /ws/terminal behaviour by connection name (fictional hosts from mock/data.js):
 *   web-prod, build-box  direct; publickey; a shell that echoes and runs echo, seq, burst, color, exit
 *   parser-vm            through build-box; the target's key is first seen (hostkey_new)
 *   imaging-01           encrypted key: passphrase "open sesame" (3 wrong -> key_locked)
 *   backup-nas           key refused (auth_failed, methods publickey,password); with
 *                        force_password a password prompt
 *   print-srv            password "pa55word" then a verification code "492039"
 *   vpn-gw               timeout
 * __pkWs.behave(name, kind) overrides: hostkey_changed, hostkey_new, too_many, slow, ok.
 *
 * Hooks on window.__pkWs: sockets (client side, each with .sent and .path), sessions,
 * push(id, text), drop(id), exit(id, reason), stealAttach(id), mute(socket), expireToken(),
 * inputOf(id), behave(name, kind), RealWebSocket. /ws/install lives in mock_install.js.
 */
(function () {
  'use strict';
  const RealWebSocket = window.WebSocket;
  const KEY = '__pkWsMock';
  const RING = 256 * 1024;
  const enc = new TextEncoder();
  const data = window.__pkMockData(window.__PK_MOCK_SCENARIO || 'normal');
  const conns = data.connections;
  const byId = (id) => conns.find((c) => c.id === id);
  const byName = (name) => conns.find((c) => c.name === name);
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const hop = (c, index, count, role) => ({ index, count, connection_id: c.id, name: c.name, host: c.host, port: c.port, role });
  const toBin = (u8) => { let s = ''; for (const b of u8) s += String.fromCharCode(b); return s; };
  const fromBin = (s) => Uint8Array.from(s, (ch) => ch.charCodeAt(0));

  let saved = {};
  try { saved = JSON.parse(sessionStorage.getItem(KEY) || '{}'); } catch (e) { saved = {}; }
  const sessions = new Map(Object.entries(saved.sessions || {}).map(([id, s]) => [id, Object.assign(s, { socket: null })]));
  const trusted = new Set(saved.trusted || []);
  let seq = saved.seq || 0;
  function persist() {
    const out = {};
    for (const [id, s] of sessions) out[id] = { id: s.id, connection_id: s.connection_id, started_at: s.started_at, buf: s.buf, base: s.base, closed: s.closed, pendingExit: s.pendingExit || null, input: s.input, line: s.line, size: s.size };
    try { sessionStorage.setItem(KEY, JSON.stringify({ sessions: out, trusted: [...trusted], seq })); } catch (e) { /* ignore */ }
  }

  const hooks = {
    RealWebSocket, sockets: [], sessions, behaviour: {}, badToken: false, installs: [],
    behave(name, kind) { hooks.behaviour[name] = kind; },
    expireToken() { hooks.badToken = true; },
    inputOf(id) { return sessions.get(id)?.input || ''; },
    push(id, text) { output(sessions.get(id), text); },
    drop(id) { const s = sessions.get(id); if (s && s.socket) { const sock = s.socket; s.socket = null; sock._finish(1006, ''); } },
    exit(id, reason) { endSession(sessions.get(id), reason || 'closed'); },
    stealAttach(id) { const s = sessions.get(id); if (s && s.socket) { const sock = s.socket; s.socket = null; sock._json({ type: 'detached', reason: 'attached_elsewhere' }); sock._serverClose(1000); } },
    mute(sock) { sock.muted = true; },
    live() { return [...sessions.values()].filter((s) => !s.closed).map((s) => s.id); },
  };
  window.__pkWs = hooks;

  class FakeSocket extends EventTarget {
    constructor(url) {
      super();
      this.url = String(url);
      this.path = new URL(this.url).pathname;
      this.readyState = 0;
      this.binaryType = 'blob';
      this.sent = [];
      this.muted = false;
      this.onopen = this.onmessage = this.onclose = this.onerror = null;
      this.server = null;
      hooks.sockets.push(this);
      setTimeout(() => {
        this.readyState = 1;
        this._emit(new Event('open'));
      }, 5);
    }
    _emit(ev) {
      this.dispatchEvent(ev);
      const fn = this[`on${ev.type}`];
      if (typeof fn === 'function') fn.call(this, ev);
    }
    send(payload) {
      if (this.readyState !== 1) return;
      const text = typeof payload === 'string';
      const bytes = text ? null : new Uint8Array(payload.buffer ? payload.buffer.slice(payload.byteOffset, payload.byteOffset + payload.byteLength) : payload);
      this.sent.push(text ? JSON.parse(payload) : { binary: new TextDecoder().decode(bytes), size: bytes.length });
      // Like a real socket, a frame sent before close() still reaches the server.
      setTimeout(() => {
        if (this.readyState === 3 && this.serverGone) return;
        if (!this.server) this.server = this.path === '/ws/install' ? window.__pkInstallServer(this, helpers) : new TerminalServer(this);
        if (text) this.server.text(JSON.parse(payload)); else this.server.binary(bytes);
      }, 1);
    }
    close() {
      if (this.readyState >= 2) return;
      this.readyState = 2;
      setTimeout(() => { this.serverGone = true; if (this.server) this.server.gone(); this._finish(1000, ''); }, 1);
    }
    _json(obj) { if (this.readyState === 1 && !this.muted) this._emit(new MessageEvent('message', { data: JSON.stringify(obj) })); }
    _bytes(u8) {
      if (this.readyState !== 1 || this.muted) return;
      for (let i = 0; i < u8.length; i += 65536) {
        const part = u8.slice(i, i + 65536);
        this._emit(new MessageEvent('message', { data: this.binaryType === 'arraybuffer' ? part.buffer : new Blob([part]) }));
      }
    }
    _serverClose(code) { setTimeout(() => this._finish(code, ''), 2); }
    _finish(code, reason) {
      if (this.readyState === 3) return;
      this.readyState = 3;
      this._emit(new CloseEvent('close', { code, reason, wasClean: code !== 1006 }));
    }
  }
  FakeSocket.CONNECTING = 0; FakeSocket.OPEN = 1; FakeSocket.CLOSING = 2; FakeSocket.CLOSED = 3;
  window.WebSocket = FakeSocket;

  /** Shared by the terminal and install servers: auth, prompts, errors. */
  const helpers = {
    wait, hop, byId, byName, conns, trusted, persist,
    authOk(msg) { return msg.type === 'auth' && !hooks.badToken && typeof msg.token === 'string' && msg.token.startsWith('mock-'); },
    fail(sock, code, message, close, extra) { sock._json(Object.assign({ type: 'error', code, message, close }, extra || {})); sock._serverClose(close); },
  };

  function output(s, text) {
    if (!s || s.closed) return;
    const bytes = typeof text === 'string' ? enc.encode(text) : text;
    s.buf += toBin(bytes);
    if (s.buf.length > RING) { s.base += s.buf.length - RING; s.buf = s.buf.slice(s.buf.length - RING); }
    if (s.socket) s.socket._bytes(bytes);
    persist();
  }
  // Like the server: a session that ends while detached is no longer listed, but one attach still
  // gets its last output and the exit; after that it is gone (4410).
  function endSession(s, reason) {
    if (!s || s.closed) return;
    s.closed = true;
    const sock = s.socket;
    s.socket = null;
    if (!sock) s.pendingExit = reason;
    persist();
    if (sock) sendExit(sock, reason);
  }
  function sendExit(sock, reason) {
    sock._json({ type: 'exit', exit_status: reason === 'closed' ? 0 : null, signal: null, reason });
    sock._serverClose(1000);
  }
  const PROMPT = (c) => `${c.username}@${c.name}:~$ `;
  /** A connection saved after the page loaded (by the wizard), from the mock HTTP API. */
  async function lookup(id) {
    const res = await window.fetch('/api/connections', { headers: { Authorization: 'Bearer mock-token' } });
    const found = (await res.json()).connections.find((x) => x.id === id);
    if (found) conns.push(found);
    return found;
  }

  class TerminalServer {
    constructor(sock) { this.sock = sock; this.stage = 'auth'; this.pending = null; this.session = null; this.pid = 0; this.aborted = false; }
    gone() {
      this.aborted = true;
      if (this.pending) this.pending.reject(new Error('gone'));
      if (this.session && this.session.socket === this.sock) this.session.socket = null;
    }
    binary(bytes) { if (this.session && this.stage === 'shell') shell(this.session, new TextDecoder().decode(bytes)); }
    text(msg) {
      const sock = this.sock;
      if (this.stage === 'dead') return;
      if (this.stage === 'auth') {
        if (!helpers.authOk(msg)) { helpers.fail(sock, 'unauthorized', 'Sign in to Port-Key.', 4401); this.stage = 'dead'; return; }
        this.stage = 'start';
        return;
      }
      if (msg.type === 'ping') { sock._json({ type: 'pong' }); return; }
      if (msg.type === 'prompt_reply') { const p = this.pending; if (p && p.id === msg.id) { this.pending = null; p.resolve(msg); } return; }
      if (msg.type === 'resize') { if (this.session) { this.session.size = [msg.cols, msg.rows]; persist(); } return; }
      if (msg.type === 'close') {
        if (this.session) endSession(this.session, 'killed');
        else { this.aborted = true; helpers.fail(sock, 'cancelled', 'Cancelled.', 1000); }
        return;
      }
      if (this.stage === 'start' && msg.type === 'open') { this.stage = 'connecting'; this.open(msg).catch(() => {}); return; }
      if (this.stage === 'start' && msg.type === 'attach') { this.attach(msg); return; }
      helpers.fail(sock, 'bad_message', 'Port-Key did not understand a message from the browser.', 4400);
    }
    ask(fields) {
      this.pid += 1;
      const id = `p${this.pid}`;
      return new Promise((resolve, reject) => {
        this.pending = { id, resolve, reject };
        this.sock._json(Object.assign({ type: 'prompt', id }, fields));
      }).then((reply) => {
        if (reply.cancel) { helpers.fail(this.sock, 'cancelled', 'Cancelled.', 1000); throw new Error('cancelled'); }
        return reply;
      });
    }
    status(phase, h, extra) { this.sock._json(Object.assign({ type: 'status', phase, hop: h }, extra || {})); return wait(hooks.behaviour.slow ? 300 : 15); }
    async open(msg) {
      const sock = this.sock;
      const c = byId(msg.connection_id) || await lookup(msg.connection_id);
      if (!c) { helpers.fail(sock, 'not_found', 'That connection no longer exists.', 4404); return; }
      const kind = hooks.behaviour[c.name] || c.name;
      if (kind === 'too_many') { helpers.fail(sock, 'too_many_sessions', 'Port-Key already has 32 sessions open. Close one and try again.', 4429); return; }
      const chain = c.jump_id ? [byId(c.jump_id), c] : [c];
      const count = chain.length;
      for (let i = 0; i < count - 1; i += 1) {
        const j = hop(chain[i], i, count, 'jump');
        await this.status('connecting', j); await this.status('hostkey', j); await this.status('auth', j, { method: 'publickey', key_name: 'id_ed25519' });
      }
      const t = hop(c, count - 1, count, 'target');
      await this.status('connecting', t);
      if (this.aborted) return;
      if (kind === 'vpn-gw') { helpers.fail(sock, 'timeout', `Couldn't reach ${c.host} on port ${c.port}. The connection timed out after 10 seconds.`, 4502, { hop: t }); return; }
      await this.status('hostkey', t);
      const fp = 'SHA256:pZ8mQ1cWv3Lx5Ny7Oz9Ab1Cd3Ef5Gh7Ij9Kl1MnTw4E';
      if ((kind === 'parser-vm' || kind === 'hostkey_new') && !trusted.has(c.name)) {
        const r = await this.ask({ kind: 'hostkey_new', hop: t, host: c.host, port: c.port, key_type: 'ssh-ed25519', fingerprint: fp });
        if (!r.accept) { helpers.fail(sock, 'hostkey_rejected', 'You did not trust the host key.', 1000, { hop: t }); return; }
        trusted.add(c.name); persist();
      }
      if (kind === 'hostkey_changed') {
        const r = await this.ask({ kind: 'hostkey_changed', hop: t, host: c.host, port: c.port, key_type: 'ssh-ed25519', old_key_type: 'ssh-ed25519',
          old_fingerprint: fp, new_fingerprint: 'SHA256:Rb4tY8uI2oP6aS0dF4gH8jK2lZ6xC0vB4nM8qW2eR6t', first_seen_at: '2026-03-02T10:00:00Z', confirm_name: c.name });
        if (!r.accept || String(r.confirm_name).trim().toLowerCase() !== c.name.toLowerCase()) {
          helpers.fail(sock, 'hostkey_changed', `${c.name}'s host key has changed. Port-Key sent nothing.`, 4409, { hop: t }); return;
        }
      }
      const usePassword = c.auth_method === 'password' || msg.force_password;
      if (!usePassword) {
        await this.status('auth', t, { method: 'publickey', key_name: c.name === 'imaging-01' ? 'id_ed25519_portkey' : 'id_ed25519' });
        if (kind === 'imaging-01') {
          for (let n = 0; ; n += 1) {
            if (n === 3) { helpers.fail(sock, 'key_locked', 'id_ed25519_portkey stayed locked after 3 wrong passphrases.', 4502, { hop: t }); return; }
            const r = await this.ask({ kind: 'passphrase', hop: t, key_fp: 'SHA256:9dTgV4hY6jU8kI0oL2pM4nB6vC8xZ0aS2dF4gH6mE1s', key_name: 'id_ed25519_portkey',
              path: 'C:\\Users\\john\\.ssh\\port-key\\id_ed25519_portkey', retry: n > 0 });
            if (r.value === 'open sesame') { hooks.remembered = r.remember; break; }
          }
        }
        if (kind === 'backup-nas') { helpers.fail(sock, 'auth_failed', 'backup-nas refused your key.', 4502, { hop: t, methods: ['publickey', 'password'] }); return; }
      } else {
        await this.status('auth', t, { method: 'password' });
        for (let n = 0; ; n += 1) {
          if (n === 3) { helpers.fail(sock, 'auth_failed', `${c.name} rejected the password.`, 4502, { hop: t, methods: ['password', 'keyboard-interactive'] }); return; }
          const r = await this.ask({ kind: 'password', hop: t, user: c.username, host: c.host, port: c.port, retry: n > 0 });
          if (r.value === 'pa55word') break;
        }
        if (kind === 'print-srv' || kind === 'hostile') {
          await this.status('auth', t, { method: 'keyboard-interactive' });
          const r = await this.ask(kind === 'hostile'
            ? { kind: 'kbdint', hop: t, name: '<b>Bank</b> login', instructions: '<script>alert(1)</script> Enter the code.', prompts: [{ text: '<img src=x onerror=alert(1)>Code: ', echo: false }], retry: false }
            : { kind: 'kbdint', hop: t, name: '', instructions: '', prompts: [{ text: 'Verification code: ', echo: false }], retry: false });
          if (r.values[0] !== '492039') { helpers.fail(sock, 'auth_failed', `${c.name} rejected the verification code.`, 4502, { hop: t, methods: ['keyboard-interactive'] }); return; }
        }
      }
      await this.status('ready', t);
      if (this.aborted) return;
      seq += 1;
      const s = { id: `s_mock${seq}`, connection_id: c.id, started_at: new Date().toISOString(), buf: '', base: 0, closed: false, socket: sock, input: '', line: '', size: [msg.cols, msg.rows] };
      sessions.set(s.id, s);
      this.session = s;
      this.stage = 'shell';
      sock._json({ type: 'ready', session_id: s.id, connection: c, offset: 0, reset: true, reattached: false });
      output(s, `Welcome to Ubuntu 24.04 LTS (fictional test host ${c.name})\r\n\r\n${PROMPT(c)}`);
    }
    attach(msg) {
      const sock = this.sock;
      const s = sessions.get(msg.session_id);
      hooks.attaches = (hooks.attaches || []).concat([{ session_id: msg.session_id, since: msg.since, cols: msg.cols, rows: msg.rows }]);
      if (!s || (s.closed && !s.pendingExit)) { helpers.fail(sock, 'session_gone', 'That session has ended.', 4410); return; }
      if (s.socket && s.socket !== sock) { const old = s.socket; old._json({ type: 'detached', reason: 'attached_elsewhere' }); old._serverClose(1000); }
      s.socket = sock;
      this.session = s;
      this.stage = 'shell';
      if (msg.cols) s.size = [msg.cols, msg.rows];
      const total = s.base + s.buf.length;
      let offset; let reset;
      if (msg.since === null || msg.since === undefined || msg.since < s.base) { offset = s.base; reset = true; } else { offset = Math.min(msg.since, total); reset = false; }
      sock._json({ type: 'ready', session_id: s.id, connection: byId(s.connection_id), offset, reset, reattached: true });
      const replay = s.buf.slice(offset - s.base);
      if (replay) sock._bytes(fromBin(replay));
      if (s.closed && s.pendingExit) {
        const reason = s.pendingExit;
        s.pendingExit = null;
        s.socket = null;
        persist();
        sendExit(sock, reason);
      }
    }
  }

  function shell(s, text) {
    const c = byId(s.connection_id);
    s.input += text;
    const clean = text.replace(/\x1b\[20[01]~/g, '');
    for (const ch of clean) {
      if (ch === '\r') {
        const cmd = s.line.trim();
        s.line = '';
        output(s, '\r\n');
        run(s, c, cmd);
      } else if (ch === '\x7f') {
        if (s.line) { s.line = s.line.slice(0, -1); output(s, '\b \b'); }
      } else if (ch === '\x03') {
        s.line = '';
        output(s, `^C\r\n${PROMPT(c)}`);
      } else if (ch >= ' ') {
        s.line += ch;
        output(s, ch);
      }
    }
    persist();
  }

  function run(s, c, cmd) {
    const [name, ...args] = cmd.split(/\s+/);
    let out = '';
    if (!name) out = '';
    else if (name === 'echo') out = `${args.join(' ')}\r\n`;
    else if (name === 'seq') { const a = Number(args[0]) || 1; const b = Number(args[1]) || a; for (let i = a; i <= b; i += 1) out += `${i}\r\n`; }
    else if (name === 'burst') { const n = Number(args[0]) || 1000; for (let i = 1; i <= n; i += 1) out += `burst line ${i} ${'x'.repeat(60)}\r\n`; }
    else if (name === 'color') out = '\x1b[31mred\x1b[0m \x1b[38;2;255;128;0mtruecolor\x1b[0m\r\n';
    else if (name === 'exit') { output(s, 'logout\r\n'); endSession(s, 'closed'); return; }
    else out = `${name}: command not found\r\n`;
    output(s, out + PROMPT(c));
  }

  const realFetch = window.fetch;
  window.fetch = function (input, init) {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    const method = ((init && init.method) || 'GET').toUpperCase();
    const json = (status, body) => Promise.resolve(new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));
    if (url.pathname === '/api/sessions' && method === 'GET') {
      return json(200, { sessions: [...sessions.values()].filter((s) => !s.closed).map((s) => ({ session_id: s.id, connection_id: s.connection_id, started_at: s.started_at, attached: Boolean(s.socket), detached_at: s.socket ? null : s.started_at })) });
    }
    const m = /^\/api\/sessions\/([^/]+)$/.exec(url.pathname);
    if (m && method === 'DELETE') {
      const s = sessions.get(decodeURIComponent(m[1]));
      hooks.deleted = (hooks.deleted || []).concat([m[1]]);
      if (!s || s.closed) return json(404, { error: { code: 'session_gone', message: 'That session has ended.' } });
      endSession(s, 'killed');
      return json(204);
    }
    return realFetch.call(this, input, init);
  };
})();
