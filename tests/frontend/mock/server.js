/*
 * tests/frontend/mock/server.js — package 8a, TEST AND DEV ONLY (never served by the app).
 *
 * A stateful stand-in for the Port-Key HTTP API (docs/api.md), installed by replacing
 * window.fetch for /api/ paths. Injected with Playwright's add_init_script after data.js, so
 * production code is untouched. It enforces the request rules the real server enforces
 * (Bearer token, X-Port-Key and JSON content type on mutating calls, JSON bodies) so the
 * client is checked against the contract.
 *
 * Scenario: window.__PK_MOCK_SCENARIO, else ?mock=<name> on the page URL, else "normal".
 * Valid token: "mock-token" (except in "signedout"/"setup") plus any token issued by login.
 * App password: "correct horse battery". Test hooks on window.__pkMock:
 *   calls      every request: {method, path, auth, csrf, json, body} (passwords removed)
 *   expire()   invalidate every token: the next call gets 401
 *   failNext(pathPart, status, code, message)  one-shot failure for a matching path
 */
(function () {
  'use strict';
  const params = new URLSearchParams(location.search);
  const scenario = window.__PK_MOCK_SCENARIO || params.get('mock') || 'normal';
  const d = window.__pkMockData(scenario);
  const PASSWORD = 'correct horse battery';
  const delay = scenario === 'slow' ? 1200 : 30;
  const db = {
    setup: scenario !== 'setup',
    tokens: new Set(scenario === 'signedout' || scenario === 'setup' ? [] : ['mock-token']),
    failures: 0,
    connections: d.connections, groups: d.groups, keys: d.keys, problems: d.problems, defaultFp: d.defaultFp,
    suggestions: d.suggestions, dismissed: new Set(), hostKeys: d.hostKeys,
    settings: { default_key_fp: null, default_user: 'john', passphrase_ttl: 28800, detach_grace: 900, font_size: 14,
      windows_copy_paste: true, leave_warning: true,
      about: { ssh_dir: 'C:\\Users\\john\\.ssh', keys_dir: 'C:\\Users\\john\\.ssh\\port-key', data_dir: '/data', version: '0.1.0' } },
  };
  const hooks = { calls: [], oneShot: [], expire() { db.tokens.clear(); }, failNext(part, status, code, message) { hooks.oneShot.push({ part, status, code, message }); } };
  window.__pkMock = hooks;
  const realFetch = window.fetch.bind(window);
  let seq = 0;

  class Fail extends Error {
    constructor(status, code, message, extra) { super(message); Object.assign(this, { status, code, extra: extra || {} }); }
  }
  const json = (status, body) => new Response(body === undefined ? null : JSON.stringify(body),
    { status, headers: { 'Content-Type': 'application/json' } });
  const now = () => new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
  const norm = (t) => String(t).trim().replace(/^#+/, '').toLowerCase();
  const TAG = /^[a-z0-9][a-z0-9._-]{0,31}$/;
  const byId = (id) => db.connections.find((c) => c.id === id);
  const groupDepth = (id) => { let n = 0; let g = db.groups.find((x) => x.id === id); while (g && n < 10) { n += 1; g = db.groups.find((x) => x.id === g.parent_id); } return n; };
  const groupHeight = (id) => 1 + Math.max(0, ...db.groups.filter((g) => g.parent_id === id).map((g) => groupHeight(g.id)));
  const descendants = (id) => db.groups.filter((g) => g.parent_id === id).flatMap((g) => [g.id, ...descendants(g.id)]);
  const newId = () => { seq += 1; return `5eed${String(seq).padStart(4, '0')}-0000-4000-8000-${String(Date.now()).slice(-12).padStart(12, '0')}`; };

  function groupsOut() {
    return db.groups.map((g) => {
      const ids = new Set([g.id, ...descendants(g.id)]);
      return Object.assign({}, g, { connection_count: db.connections.filter((c) => ids.has(c.group_id)).length });
    });
  }
  function tagsOut() {
    const counts = new Map();
    for (const c of db.connections) for (const t of c.tags) counts.set(t, (counts.get(t) || 0) + 1);
    return [...counts].map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  }
  function keysOut() {
    const def = db.settings.default_key_fp || db.defaultFp;
    return db.keys.map((k) => Object.assign({}, k, {
      is_default: k.fp === def,
      used_by: db.connections.filter((c) => c.auth_method === 'key' && (c.key_fp === k.fp || (!c.key_fp && k.fp === def))).map((c) => ({ id: c.id, name: c.name })),
    }));
  }
  function sortedConnections() {
    return db.connections.slice().sort((a, b) => (b.pinned - a.pinned) || a.sort_order - b.sort_order || a.name.localeCompare(b.name));
  }
  function checkTags(tags) {
    const out = [];
    for (const raw of tags || []) {
      const t = norm(raw);
      if (!TAG.test(t)) throw new Fail(422, 'tag_invalid', `"${raw}" is not a valid tag. Use lower-case letters, digits, dot, dash and underscore.`);
      if (!out.includes(t)) out.push(t);
    }
    return out.sort();
  }
  function checkConnection(c, selfId) {
    const clash = db.connections.find((x) => x.id !== selfId && x.name.toLowerCase() === c.name.toLowerCase());
    if (clash) throw new Fail(409, 'name_taken', `A connection named ${clash.name} already exists. Choose another name.`);
    if (c.group_id && !db.groups.some((g) => g.id === c.group_id)) throw new Fail(422, 'group_unknown', 'That folder no longer exists.');
    let hop = c.jump_id;
    let hops = 0;
    while (hop) {
      if (hop === selfId) throw new Fail(422, 'jump_cycle', 'That jump host is reached through this connection, which would make a loop.');
      const j = byId(hop);
      if (!j) throw new Fail(422, 'jump_unknown', 'That jump host no longer exists.');
      hops += 1;
      if (hops > 3) throw new Fail(422, 'jump_too_deep', 'Jump chains can be at most 3 hosts long.');
      hop = j.jump_id;
    }
  }
  function reach(c) {
    if (c.jump_id) return { id: c.id, state: 'skipped' };
    const h = [...c.id].reduce((a, ch) => (a * 31 + ch.charCodeAt(0)) >>> 0, 7);
    return c.name === 'vpn-gw' || h % 7 === 0 ? { id: c.id, state: 'down' } : { id: c.id, state: 'up', ms: (h % 20) + 1 };
  }

  const routes = [
    ['GET', /^\/api\/health$/, () => ({ status: 'ok', version: '0.1.0' })],
    ['GET', /^\/api\/auth\/status$/, (req) => ({ setup: db.setup, authenticated: db.tokens.has(req.token) })],
    ['POST', /^\/api\/auth\/setup$/, (req) => {
      if (db.setup) throw new Fail(409, 'already_setup', 'A password is already set.');
      if (!req.body.password || req.body.password.length < 8) throw new Fail(422, 'validation', 'Check the highlighted fields.', { fields: [{ loc: ['body', 'password'], msg: 'Too short', type: 'string_too_short' }] });
      db.setup = true;
      return issue();
    }],
    ['POST', /^\/api\/auth\/login$/, (req) => {
      if (!db.setup) throw new Fail(409, 'not_setup', 'Set a password first.');
      if (req.body.password !== PASSWORD) {
        db.failures += 1;
        if (db.failures >= 3) throw new Fail(429, 'rate_limited', 'Too many attempts.', { retry_after: 30 });
        throw new Fail(401, 'bad_password', 'That password is not correct.');
      }
      db.failures = 0;
      return issue();
    }],
    ['POST', /^\/api\/auth\/logout$/, (req) => { db.tokens.delete(req.token); }],
    ['POST', /^\/api\/auth\/password$/, (req) => {
      if (req.body.old !== PASSWORD) throw new Fail(401, 'bad_password', 'That password is not correct.');
    }],
    ['GET', /^\/api\/connections$/, () => {
      if (scenario === 'error') throw new Fail(500, 'internal', 'Something went wrong inside Port-Key.');
      return { connections: sortedConnections() };
    }],
    ['POST', /^\/api\/connections$/, (req) => {
      const c = Object.assign({ port: 22, key_fp: null, jump_id: null, group_id: null, tags: [], color: 'grey', pinned: false, notes: '' }, req.body,
        { id: newId(), sort_order: 0, created_at: now(), updated_at: now(), last_used_at: null, use_count: 0 });
      c.tags = checkTags(c.tags);
      checkConnection(c, c.id);
      db.connections.push(c);
      return [201, c];
    }],
    ['POST', /^\/api\/connections\/bulk$/, (req) => {
      const items = req.body.ids.map((id) => byId(id));
      if (items.some((c) => !c)) throw new Fail(404, 'not_found', 'One of those connections no longer exists.');
      const add = checkTags(req.body.add_tags);
      const remove = checkTags(req.body.remove_tags);
      if ('set_group_id' in req.body && req.body.set_group_id && !db.groups.some((g) => g.id === req.body.set_group_id)) throw new Fail(422, 'group_unknown', 'That folder no longer exists.');
      for (const c of items) {
        if ('set_group_id' in req.body) c.group_id = req.body.set_group_id;
        c.tags = [...new Set([...c.tags.filter((t) => !remove.includes(t)), ...add])].sort();
        c.updated_at = now();
      }
      return { connections: items };
    }],
    ['POST', /^\/api\/connections\/reachability$/, (req) => ({ results: req.body.ids.map(byId).filter(Boolean).map(reach) })],
    ['PATCH', /^\/api\/connections\/([^/]+)$/, (req, id) => {
      const c = byId(id);
      if (!c) throw new Fail(404, 'not_found', 'Not found.');
      const next = Object.assign({}, c, req.body);
      if (req.body.tags) next.tags = checkTags(req.body.tags);
      checkConnection(next, id);
      Object.assign(c, next, { updated_at: now() });
      return c;
    }],
    ['DELETE', /^\/api\/connections\/([^/]+)$/, (req, id) => {
      const c = byId(id);
      if (!c) throw new Fail(404, 'not_found', 'Not found.');
      const deps = db.connections.filter((x) => x.jump_id === id);
      if (deps.length) {
        throw new Fail(409, 'jump_in_use', `${c.name} is the jump host for ${deps.length} connection${deps.length === 1 ? '' : 's'}: ${deps.map((x) => x.name).join(', ')}. Change their Jump via setting first.`,
          { dependants: deps.map((x) => ({ id: x.id, name: x.name })) });
      }
      db.connections = db.connections.filter((x) => x.id !== id);
    }],
    ['POST', /^\/api\/connections\/([^/]+)\/duplicate$/, (req, id) => {
      const c = byId(id);
      if (!c) throw new Fail(404, 'not_found', 'Not found.');
      let name = `${c.name} (copy)`;
      for (let n = 2; db.connections.some((x) => x.name.toLowerCase() === name.toLowerCase()); n += 1) name = `${c.name} (copy ${n})`;
      const copy = Object.assign({}, c, { id: newId(), name, pinned: false, last_used_at: null, use_count: 0, created_at: now(), updated_at: now() });
      db.connections.push(copy);
      return [201, copy];
    }],
    ['GET', /^\/api\/groups$/, () => ({ groups: groupsOut() })],
    ['POST', /^\/api\/groups$/, (req) => {
      const parent = req.body.parent_id || null;
      if (parent && !db.groups.some((g) => g.id === parent)) throw new Fail(422, 'group_unknown', 'That folder no longer exists.');
      if (parent && groupDepth(parent) >= 3) throw new Fail(422, 'group_too_deep', 'Folders nest at most 3 levels deep.');
      const name = String(req.body.name || '').trim();
      if (db.groups.some((g) => g.parent_id === parent && g.name.toLowerCase() === name.toLowerCase())) throw new Fail(409, 'name_taken', `A folder named ${name} already exists there.`);
      const g = { id: newId(), name, parent_id: parent, sort_order: 0, connection_count: 0 };
      db.groups.push(g);
      return [201, g];
    }],
    ['PATCH', /^\/api\/groups\/([^/]+)$/, (req, id) => {
      const g = db.groups.find((x) => x.id === id);
      if (!g) throw new Fail(404, 'not_found', 'Not found.');
      if ('parent_id' in req.body) {
        const p = req.body.parent_id;
        if (p === id || descendants(id).includes(p)) throw new Fail(422, 'group_cycle', "A folder can't go inside itself.");
        if (p && groupDepth(p) + groupHeight(id) > 3) throw new Fail(422, 'group_too_deep', 'Folders nest at most 3 levels deep.');
        g.parent_id = p;
      }
      if (req.body.name) g.name = req.body.name.trim();
      return groupsOut().find((x) => x.id === id);
    }],
    ['DELETE', /^\/api\/groups\/([^/]+)$/, (req, id) => {
      const g = db.groups.find((x) => x.id === id);
      if (!g) throw new Fail(404, 'not_found', 'Not found.');
      for (const child of db.groups.filter((x) => x.parent_id === id)) {
        child.parent_id = g.parent_id;
        let n = 2;
        const base = child.name;
        while (db.groups.some((x) => x !== child && x.parent_id === child.parent_id && x.name.toLowerCase() === child.name.toLowerCase())) child.name = `${base} (${n++})`;
      }
      for (const c of db.connections) if (c.group_id === id) c.group_id = g.parent_id;
      db.groups = db.groups.filter((x) => x.id !== id);
    }],
    ['GET', /^\/api\/tags$/, () => ({ tags: tagsOut() })],
    ['PATCH', /^\/api\/tags\/([^/]+)$/, (req, name) => {
      const to = checkTags([req.body.name])[0];
      for (const c of db.connections) if (c.tags.includes(name)) c.tags = [...new Set(c.tags.map((t) => (t === name ? to : t)))].sort();
      return tagsOut().find((t) => t.name === to);
    }],
    ['DELETE', /^\/api\/tags\/([^/]+)$/, (req, name) => { for (const c of db.connections) c.tags = c.tags.filter((t) => t !== name); }],
    ['GET', /^\/api\/keys$/, () => ({ keys: keysOut(), problems: db.problems, default_fp: db.settings.default_key_fp || db.defaultFp,
      unlocked_count: db.keys.filter((k) => k.unlocked).length })],
    ['GET', /^\/api\/keys\/name-check$/, (req) => {
      const name = req.query.get('name') || '';
      const invalid = !/^[A-Za-z0-9_][A-Za-z0-9._-]*$/.test(name) || /\.(pub|ppk)$/.test(name) || name.length > 64;
      // Like the real API: only the generated-keys folder (.ssh\port-key) can clash, so a
      // name used in .ssh itself (id_rsa) is free there.
      const taken = db.keys.some((k) => k.source === 'generated' && k.name.toLowerCase() === name.toLowerCase());
      return { name, available: !invalid && !taken, reason: invalid ? 'invalid' : taken ? 'taken' : null };
    }],
    ['POST', /^\/api\/keys\/generate$/, (req) => {
      if (db.keys.some((k) => k.source === 'generated' && k.name.toLowerCase() === req.body.name.toLowerCase())) throw new Fail(409, 'key_name_taken', `A key named ${req.body.name} already exists.`);
      const fp = `SHA256:${btoa(String(Date.now()) + req.body.name).replace(/=/g, '').padEnd(43, 'A').slice(0, 43).replace(/[^A-Za-z0-9+/]/g, 'x')}`;
      const k = { id: fp.slice(7).replace(/\+/g, '-').replace(/\//g, '_'), fp, name: req.body.name, label: null, type: req.body.type || 'ed25519',
        bits: req.body.type === 'rsa' ? 4096 : 256, comment: req.body.comment || '', path: `C:\\Users\\john\\.ssh\\port-key\\${req.body.name}`,
        source: 'generated', encrypted: Boolean(req.body.passphrase), unlocked: false, public_key: `ssh-ed25519 AAAAGeneratedForTests ${req.body.comment || ''}`.trim(),
        is_default: false, hidden: false, used_by: [] };
      db.keys.push(k);
      return [201, k];
    }],
    ['PATCH', /^\/api\/keys\/([^/]+)$/, (req, id) => {
      const k = db.keys.find((x) => x.id === id);
      if (!k) throw new Fail(404, 'key_unknown', 'That key is no longer there.');
      const out = keysOut().find((x) => x.id === id);
      if (req.body.hidden && out.used_by.length && !req.body.reassign) {
        throw new Fail(409, 'key_in_use', `${k.name} is used by ${out.used_by.length} connections.`, { dependants: out.used_by });
      }
      if (req.body.hidden && req.body.reassign) {
        for (const ref of out.used_by) Object.assign(byId(ref.id), { auth_method: req.body.reassign.auth_method, key_fp: req.body.reassign.key_fp || null });
        if ((db.settings.default_key_fp || db.defaultFp) === k.fp) { db.settings.default_key_fp = null; db.defaultFp = null; }
      }
      if ('hidden' in req.body) k.hidden = req.body.hidden;
      if ('label' in req.body) k.label = req.body.label;
      return keysOut().find((x) => x.id === id);
    }],
    ['POST', /^\/api\/keys\/lock$/, () => { for (const k of db.keys) k.unlocked = false; }],
    ['GET', /^\/api\/hosts\/suggestions$/, () => {
      // Like the real API: dismissed hosts and hosts a saved connection uses are left out.
      const saved = new Set(db.connections.map((c) => `${c.host.toLowerCase()}:${c.port}`));
      const hidden = (s) => db.dismissed.has(`${s.host}:${s.port}`) || [s.host, ...s.aliases].some((n) => saved.has(`${n.toLowerCase()}:${s.port}`));
      return { suggestions: db.suggestions.filter((s) => !hidden(s)), hashed_count: d.hashed };
    }],
    ['POST', /^\/api\/hosts\/suggestions\/dismiss$/, (req) => { db.dismissed.add(`${req.body.host}:${req.body.port}`); }],
    ['POST', /^\/api\/hosts\/suggestions\/restore$/, (req) => { db.dismissed.delete(`${req.body.host}:${req.body.port}`); }],
    ['GET', /^\/api\/hostkeys$/, () => ({ host_keys: db.hostKeys })],
    ['DELETE', /^\/api\/hostkeys\/(\d+)$/, (req, id) => {
      const k = db.hostKeys.find((x) => String(x.id) === id);
      if (!k) throw new Fail(404, 'not_found', 'Not found.');
      if (String(req.body.confirm_host).toLowerCase() !== k.host.toLowerCase()) throw new Fail(422, 'confirm_mismatch', 'Type the host name exactly.');
      db.hostKeys = db.hostKeys.filter((x) => x !== k);
    }],
    ['GET', /^\/api\/sessions$/, () => ({ sessions: [] })],
    ['GET', /^\/api\/settings$/, () => db.settings],
    ['PATCH', /^\/api\/settings$/, (req) => {
      if ('font_size' in req.body && (req.body.font_size < 8 || req.body.font_size > 32)) throw new Fail(422, 'validation', 'Use a size from 8 to 32.');
      Object.assign(db.settings, req.body);
      return db.settings;
    }],
    ['GET', /^\/api\/diag$/, () => ({ version: '0.1.0', python: '3.12.8', asyncssh: '2.18.0',
      ssh_dir: { path: 'C:\\Users\\john\\.ssh', present: true, readable: true, writable: false, key_count: db.keys.length, known_hosts: true,
        mode_note: 'Files in the mounted folder show as mode 0777. Port-Key does not need stricter modes.' },
      keys_dir: { path: 'C:\\Users\\john\\.ssh\\port-key', present: true, readable: true, writable: true },
      data_dir: { path: '/data', present: true, readable: true, writable: true }, key_problems: db.problems, notes: [] })],
    ['POST', /^\/api\/diag\/resolve$/, (req) => {
      const host = req.body.host;
      const known = { 'build-box': '192.168.10.20', 'web-prod': '192.168.10.42', 'vpn-gw': '10.8.0.1' };
      const addr = known[host] || (/^[\d.]+$/.test(host) ? host : null);
      if (!addr) return { host, port: req.body.port, addresses: [], resolve_error: 'Name or service not known', tcp: { state: 'skipped', ms: null, error: null } };
      const up = addr !== '10.8.0.1';
      return { host, port: req.body.port, addresses: [addr], resolve_error: null, tcp: up ? { state: 'up', ms: 4, error: null } : { state: 'down', ms: null, error: 'timeout' } };
    }],
  ];
  const PUBLIC = /^\/api\/(health|auth\/status|auth\/setup|auth\/login)$/;

  function issue() {
    const token = `mock-${Math.random().toString(36).slice(2)}`;
    db.tokens.add(token);
    return { token, expires_at: '2026-12-15T14:02:00Z' };
  }

  async function handle(method, url, init) {
    const headers = new Headers(init.headers || {});
    const auth = headers.get('Authorization') || '';
    const token = auth.startsWith('Bearer ') ? auth.slice(7) : null;
    const req = { token, query: url.searchParams, body: {} };
    const record = { method, path: url.pathname + url.search, auth: Boolean(token), csrf: headers.get('X-Port-Key'), json: headers.get('Content-Type'), body: null };
    hooks.calls.push(record);
    await new Promise((r) => setTimeout(r, delay));
    if (method !== 'GET') {
      if (headers.get('X-Port-Key') !== '1') return json(403, { error: { code: 'missing_csrf_header', message: 'Missing X-Port-Key header.' } });
      if (headers.get('Content-Type') !== 'application/json') return json(415, { error: { code: 'unsupported_media_type', message: 'Send JSON.' } });
      try { req.body = JSON.parse(init.body); } catch { return json(422, { error: { code: 'validation', message: 'Body is not JSON.' } }); }
      record.body = Object.fromEntries(Object.entries(req.body).filter(([k]) => !/pass|old|new/.test(k)));
    }
    if (!PUBLIC.test(url.pathname) && !db.tokens.has(token)) return json(401, { error: { code: 'unauthorized', message: 'Sign in to Port-Key.' } });
    const shot = hooks.oneShot.findIndex((f) => url.pathname.includes(f.part));
    if (shot >= 0) {
      const f = hooks.oneShot.splice(shot, 1)[0];
      return json(f.status, { error: { code: f.code, message: f.message } });
    }
    if (/^\/api\/keys\/[^/]+\/public$/.test(url.pathname) && method === 'GET') {
      const k = db.keys.find((x) => url.pathname.includes(x.id));
      return k ? new Response(`${k.public_key}\n`, { status: 200, headers: { 'Content-Type': 'text/plain' } }) : json(404, { error: { code: 'key_unknown', message: 'Unknown key.' } });
    }
    if (/^\/api\/keys\/[^/]+\/unlock$/.test(url.pathname)) {
      const k = db.keys.find((x) => url.pathname.includes(x.id));
      if (!k) return json(404, { error: { code: 'key_unknown', message: 'Unknown key.' } });
      k.unlocked = true;
      return new Response(null, { status: 204 });
    }
    for (const [m, re, fn] of routes) {
      const match = method === m && url.pathname.match(re);
      if (!match) continue;
      try {
        const out = fn(req, ...match.slice(1).map(decodeURIComponent));
        if (Array.isArray(out)) return json(out[0], out[1]);
        return out === undefined ? new Response(null, { status: 204 }) : json(200, out);
      } catch (err) {
        if (err instanceof Fail) return json(err.status, { error: Object.assign({ code: err.code, message: err.message }, err.extra) });
        return json(500, { error: { code: 'internal', message: String(err) } });
      }
    }
    return json(404, { error: { code: 'not_found', message: 'Not found.' } });
  }

  window.fetch = function (input, init) {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    if (url.origin === location.origin && url.pathname.startsWith('/api/')) {
      return handle(((init && init.method) || 'GET').toUpperCase(), url, init || {});
    }
    return realFetch(input, init);
  };
})();
