/*
 * terminal/messages.js — owner: package 8b (private module of terminal/ and views/sessions.js).
 *
 * User-facing copy for terminal sessions (docs/ux-spec.md "Session states"), kept pure so it can
 * be unit-tested: connecting checklist lines, the Disconnected bar, the connect-failure panels
 * and small formatters. Server messages are shown as text, never as markup.
 */

/** @typedef {import('../types.js').Hop} Hop */
/** @typedef {import('../types.js').WsError} WsError */

/** @param {Date} d */
export function clockTime(d) {
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/** "Connected just now", "Connected 12 min", "Connected 2 h 5 min". @param {number} ms */
export function connectedFor(ms) {
  const min = Math.floor(Math.max(0, ms) / 60000);
  if (min < 1) return 'Connected just now';
  if (min < 60) return `Connected ${min} min`;
  const h = Math.floor(min / 60);
  const rest = min % 60;
  return rest ? `Connected ${h} h ${rest} min` : `Connected ${h} h`;
}

/** @param {string} host @param {number} port */
export function hostPort(host, port) {
  return host.includes(':') ? `[${host}]:${port}` : `${host}:${port}`;
}

/**
 * The checklist line for one status message.
 * @param {{phase: string, hop: Hop, method?: string, key_name?: string}} s
 * @returns {{key: string, text: string}|null} null for "ready"
 */
export function statusLine(s) {
  const hop = s.hop;
  if (hop.role === 'jump') return { key: `hop${hop.index}`, text: `Reaching ${hop.name}…` };
  if (s.phase === 'connecting') return { key: 'reach', text: `Reaching ${hostPort(hop.host, hop.port)}…` };
  if (s.phase === 'hostkey') return { key: 'hostkey', text: 'Checking host key' };
  if (s.phase === 'auth') {
    if (s.method === 'publickey') return { key: 'auth', text: `Signing in with ${s.key_name || 'your key'}…` };
    if (s.method === 'password') return { key: 'auth', text: 'Signing in with a password…' };
    if (s.method === 'keyboard-interactive') return { key: 'auth', text: 'Signing in with a verification prompt…' };
    return { key: 'auth', text: 'Signing in…' };
  }
  return null;
}

/**
 * The Disconnected bar after `exit` (reason closed, lost or killed) or a vanished session:
 * `gone` when this page had not shown it yet (restored after a reload), `ended` when it was live
 * here and vanished while the link to Port-Key was down (a Port-Key restart, most often).
 * @param {string} name @param {'closed'|'lost'|'killed'|'gone'|'ended'} reason @param {Date} at
 * @returns {{title: string, body: string}}
 */
export function disconnectedText(name, reason, at) {
  const t = clockTime(at);
  const next = 'Press Enter or select Reconnect to open a new shell.';
  if (reason === 'lost') return { title: `Connection to ${name} lost at ${t}`, body: `No response for 90 seconds. ${next}` };
  if (reason === 'killed') return { title: `Disconnected from ${name} at ${t}`, body: `The session was closed in Port-Key. ${next}` };
  if (reason === 'gone') return { title: `Disconnected from ${name}`, body: `The session ended while this page was away. ${next}` };
  if (reason === 'ended') return { title: `Disconnected from ${name}`, body: `Port-Key restarted, or the session ended while this page could not reach Port-Key. ${next}` };
  return { title: `Disconnected from ${name} at ${t}`, body: `The remote host closed the connection. ${next}` };
}

/**
 * Title and body for a connection that failed before the shell opened.
 * `askedPassword`: a password or verification prompt was shown for the target in this attempt,
 * so a failure is about those answers, not the key.
 * @param {WsError} err @param {{name: string, keyAuth: boolean, askedPassword: boolean}} conn
 * @returns {{title: string, body: string, kind: 'key_refused'|'hostkey'|'failed'}}
 */
export function failureText(err, conn) {
  const hopName = err.hop?.name || conn.name;
  switch (err.code) {
    case 'auth_failed':
      if (conn.keyAuth && !conn.askedPassword && (!err.hop || err.hop.role === 'target')) {
        return { kind: 'key_refused', title: `${hopName} refused your key`, body: err.message };
      }
      return { kind: 'failed', title: `${hopName} didn't accept the sign-in`, body: err.message };
    case 'jump_failed':
      return {
        kind: 'failed',
        title: `Couldn't reach ${conn.name}`,
        body: `Couldn't reach ${conn.name} because the jump host ${hopName} didn't connect. ${err.message}`,
      };
    case 'hostkey_changed':
      return {
        kind: 'hostkey',
        title: `${hopName}'s host key has changed`,
        body: 'Port-Key kept the connection closed and sent no password and no key. If you know why the key changed, connect again and type the name to replace the stored key.',
      };
    case 'hostkey_revoked':
      return { kind: 'failed', title: `${hopName}'s host key is revoked`, body: err.message };
    case 'password_not_allowed':
      return { kind: 'failed', title: `${hopName} does not accept passwords`, body: err.message };
    case 'password_expired':
      return { kind: 'failed', title: 'The password has expired', body: err.message };
    case 'key_locked':
      return { kind: 'failed', title: 'The key stayed locked', body: err.message };
    case 'key_unreadable':
      return { kind: 'failed', title: "Port-Key can't read the key", body: err.message };
    case 'prompt_timeout':
      return { kind: 'failed', title: 'No answer in time', body: err.message };
    case 'not_found':
      return { kind: 'failed', title: 'That connection no longer exists', body: err.message };
    case 'too_many_sessions':
      return { kind: 'failed', title: 'Too many sessions', body: err.message };
    default:
      return { kind: 'failed', title: `Couldn't connect to ${conn.name}`, body: err.message };
  }
}

/** "Server offered: publickey, password". @param {string[]|undefined} methods */
export function offeredText(methods) {
  return methods && methods.length ? `Server offered: ${methods.join(', ')}` : '';
}

/** Lines in pasted text; a single trailing line break does not count as another line. @param {string} text */
export function pasteLines(text) {
  const body = text.replace(/(\r\n|\r|\n)$/, '');
  if (!body) return 0;
  return body.split(/\r\n|\r|\n/).length;
}
