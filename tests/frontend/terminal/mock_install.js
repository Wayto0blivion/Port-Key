/*
 * tests/frontend/terminal/mock_install.js — package 8b, TEST ONLY (never served by the app).
 *
 * The /ws/install half of the WebSocket mock (see mock_ws.js, which loads first and calls
 * window.__pkInstallServer for each install socket). Behaviour by the `start` host:
 *   lab-01 / 192.168.10.71  known host key, key refused, password "pa55word", key added -> installed
 *   new-host                first-seen host key, then as lab-01
 *   tfa-host                keyboard-interactive only: "Password: " then "Verification code: " (492039)
 *   keyed-host              key already works -> already_installed
 *   nopass-host             password_not_allowed; check_key then succeeds
 *   badperms-host           key_still_refused; fix_permissions then succeeds
 *   ro-host                 authorized_keys_readonly; save_password_only
 *   win-host                non_posix
 *   expired-host            password accepted, then password_expired
 *   changed-host            hostkey_changed (type the name to go on)
 *   10.9.9.9 / refused-host / nohost   timeout / refused / dns
 * With assume_installed the key is refused first (key_refused). With jump_id the jump connects
 * first. `done` creates or updates the connection through the mock HTTP API, so the store sees it.
 * Every start message is recorded in __pkWs.installs.
 */
(function () {
  'use strict';
  const FP_NEW = 'SHA256:Qd03vLxB7cD9eF1gH2iJ4kL6mN8oP0qR2sT4uV6h7Rk';
  const PUB = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeFakeFakeForTestsOnly10 john@WORKPC';

  window.__pkInstallServer = function (sock, helpers) {
    const hooks = window.__pkWs;
    let stage = 'auth';
    let pid = 0;
    let pending = null;
    let actionWait = null;
    let gone = false;
    let start = null;
    const step = (name, state, extra) => { sock._json(Object.assign({ type: 'step', step: name, state }, extra || {})); return helpers.wait(10); };
    const fail = (code, message, close, extra) => { gone = true; helpers.fail(sock, code, message, close, extra); throw new Error('ended'); };
    const ask = (fields) => {
      pid += 1;
      const id = `p${pid}`;
      return new Promise((resolve, reject) => { pending = { id, resolve, reject }; sock._json(Object.assign({ type: 'prompt', id }, fields)); })
        .then((r) => { if (r.cancel) fail('cancelled', 'Cancelled.', 1000); return r; });
    };
    const needs = (code, actions, extra) => new Promise((resolve, reject) => {
      actionWait = { resolve, reject };
      sock._json(Object.assign({ type: 'needs_action', code, message: code, actions, public_key: PUB,
        install_command: `mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo '${PUB}' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys` }, extra || {}));
    }).then((a) => { if (a === 'cancel') fail('cancelled', 'Cancelled.', 1000); return a; });

    async function save(outcome) {
      const s = start;
      const body = { name: s.name, host: s.host, port: s.port, username: s.username, auth_method: outcome === 'saved_password_only' ? 'password' : 'key',
        key_fp: outcome === 'saved_password_only' ? null : s.key_fp, jump_id: s.jump_id, group_id: s.group_id, tags: s.tags, color: s.color, pinned: s.pinned, notes: s.notes };
      await step('save', 'running');
      const res = await window.fetch(s.connection_id ? `/api/connections/${s.connection_id}` : '/api/connections', {
        method: s.connection_id ? 'PATCH' : 'POST', headers: { 'Content-Type': 'application/json', 'X-Port-Key': '1', Authorization: 'Bearer mock-token' }, body: JSON.stringify(body),
      });
      const conn = await res.json();
      if (!res.ok) fail('internal', conn.error ? conn.error.message : 'save failed', 1011);
      await step('save', 'ok');
      gone = true;
      sock._json({ type: 'done', connection: conn, outcome });
      sock._serverClose(1000);
    }

    async function passwordLogin(target, host) {
      await step('password_login', 'running');
      if (host === 'tfa-host') {
        const a = await ask({ kind: 'kbdint', hop: target, name: '', instructions: '', prompts: [{ text: 'Password: ', echo: false }], retry: false });
        if (a.values[0] !== 'pa55word') fail('auth_failed', `${start.name} rejected the password.`, 4502, { hop: target, methods: ['publickey', 'keyboard-interactive'] });
        const b = await ask({ kind: 'kbdint', hop: target, name: '', instructions: '', prompts: [{ text: 'Verification code: ', echo: false }], retry: false });
        if (b.values[0] !== '492039') fail('auth_failed', `${start.name} rejected the verification code.`, 4502, { hop: target });
      } else {
        for (let n = 0; ; n += 1) {
          if (n === 3) fail('auth_failed', `${start.name} rejected the password.`, 4502, { hop: target, methods: ['publickey', 'password'] });
          const r = await ask({ kind: 'password', hop: target, user: start.username, host: start.host, port: start.port, retry: n > 0 });
          if (r.value === 'pa55word') break;
        }
      }
      if (host === 'expired-host') fail('password_expired', `${start.username}'s password has expired.`, 4502, { hop: target });
      await step('password_login', 'ok');
    }

    async function run(s) {
      start = s;
      hooks.installs.push(s);
      const host = s.host;
      await step('validate', 'ok');
      const jump = s.jump_id ? helpers.byId(s.jump_id) : null;
      const count = jump ? 2 : 1;
      if (jump) await step('connect', 'ok', { hop: helpers.hop(jump, 0, 2, 'jump') });
      const target = { index: count - 1, count, connection_id: s.connection_id, name: s.name, host: s.host, port: s.port, role: 'target' };
      await step('connect', 'running', { hop: target });
      if (host === '10.9.9.9') fail('timeout', `Couldn't reach ${host} on port ${s.port}. The connection timed out after 10 seconds.`, 4502, { hop: target });
      if (host === 'refused-host') fail('refused', `${host} refused the connection on port ${s.port}.`, 4502, { hop: target });
      if (host === 'nohost') fail('dns', `Couldn't find a host named ${host}.`, 4502, { hop: target });
      await step('connect', 'ok', { hop: target });
      if (host === 'new-host' && !helpers.trusted.has(host)) {
        const r = await ask({ kind: 'hostkey_new', hop: target, host, port: s.port, key_type: 'ssh-ed25519', fingerprint: FP_NEW });
        if (!r.accept) fail('hostkey_rejected', 'You did not trust the host key.', 1000, { hop: target });
        await step('hostkey', 'ok', { code: 'accepted' });
      } else if (host === 'changed-host') {
        const r = await ask({ kind: 'hostkey_changed', hop: target, host, port: s.port, key_type: 'ssh-ed25519', old_key_type: 'ssh-ed25519',
          old_fingerprint: 'SHA256:pZ8mQ1cWv3Lx5Ny7Oz9Ab1Cd3Ef5Gh7Ij9Kl1MnTw4E', new_fingerprint: FP_NEW, first_seen_at: null, confirm_name: s.name });
        if (!r.accept || String(r.confirm_name).trim().toLowerCase() !== s.name.toLowerCase()) fail('hostkey_changed', 'The host key has changed.', 4409, { hop: target });
        await step('hostkey', 'ok', { code: 'accepted' });
      } else {
        await step('hostkey', 'ok', { code: 'known' });
      }
      await step('try_key', 'running');
      if (host === 'keyed-host') {
        await step('try_key', 'ok', { code: 'already_installed' });
        await save('already_installed');
        return;
      }
      await step('try_key', 'warn', { code: 'refused' });
      if (s.assume_installed) {
        const a = await needs('key_refused', ['install_with_password', 'save_password_only', 'cancel']);
        if (a === 'save_password_only') { await save('saved_password_only'); return; }
      }
      if (host === 'nopass-host' || host === 'win-host') {
        const code = host === 'win-host' ? 'non_posix' : 'password_not_allowed';
        await needs(code, ['check_key', 'cancel'], { output: host === 'win-host' ? "'sh' is not recognized as an internal or external command" : 'Server offered: publickey' });
        await step('try_key', 'ok', { code: 'already_installed' });
        await save('already_installed');
        return;
      }
      await passwordLogin(target, host);
      await step('install', 'running');
      if (host === 'ro-host') {
        await step('install', 'fail');
        const a = await needs('authorized_keys_readonly', ['save_password_only', 'cancel'], { output: 'sh: can\'t create /root/.ssh/authorized_keys: Read-only file system' });
        if (a === 'save_password_only') { await save('saved_password_only'); return; }
      }
      await step('install', host === 'badperms-host' ? 'warn' : 'ok', { code: host === 'badperms-host' ? 'perms_warning' : 'added' });
      await step('verify', 'running');
      if (host === 'badperms-host') {
        await step('verify', 'fail');
        const a = await needs('key_still_refused', ['fix_permissions', 'save_password_only', 'cancel'], { output: 'Authentication refused: bad ownership or modes for directory /home/john/.ssh' });
        if (a === 'save_password_only') { await save('saved_password_only'); return; }
        await step('verify', 'running');
      }
      await step('verify', 'ok');
      await step('discard_password', 'ok');
      await save('installed');
    }

    return {
      gone() { gone = true; if (pending) pending.reject(new Error('gone')); if (actionWait) actionWait.reject(new Error('gone')); },
      binary() {},
      text(msg) {
        if (stage === 'dead') return;
        if (stage === 'auth') {
          if (!helpers.authOk(msg)) { stage = 'dead'; helpers.fail(sock, 'unauthorized', 'Sign in to Port-Key.', 4401); return; }
          stage = 'start';
          return;
        }
        if (msg.type === 'ping') { sock._json({ type: 'pong' }); return; }
        if (msg.type === 'prompt_reply') { const p = pending; if (p && p.id === msg.id) { pending = null; p.resolve(msg); } return; }
        if (msg.type === 'action') { const w = actionWait; actionWait = null; if (w) w.resolve(msg.action); return; }
        if (msg.type === 'close') { if (!gone) { gone = true; helpers.fail(sock, 'cancelled', 'Cancelled.', 1000); } return; }
        if (stage === 'start' && msg.type === 'start') { stage = 'run'; run(msg).catch(() => {}); return; }
        helpers.fail(sock, 'bad_message', 'Port-Key did not understand a message from the browser.', 4400);
      },
    };
  };
})();
