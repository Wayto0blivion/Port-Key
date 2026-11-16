/*
 * api.js — owner: package 8a (frontend shell). Function names and signatures are frozen by
 * package 0 (docs/api.md); bodies are 8a's.
 *
 * HTTP client for /api. Every request sends `Authorization: Bearer <token>` when a token is
 * stored; every non-GET request sends `Content-Type: application/json`, `X-Port-Key: 1` and a
 * JSON body (`{}` when there are no fields). Errors reject with ApiError built from the
 * `{error: {code, message, ...}}` envelope. A 401 clears the token and calls the handler set
 * with onUnauthorized() on every call except login and password change: those answer 401
 * `bad_password` for a wrong app password, which is not a sign-out (setup and status never
 * answer 401, and logout clears the token anyway). A password change whose own token has
 * expired (401 `unauthorized`) does sign out. The token lives in localStorage under TOKEN_KEY
 * and never goes into a URL.
 *
 * Every fetch keeps the default mode ("cors"). Never set `mode: 'same-origin'` or 'no-cors':
 * with Referrer-Policy no-referrer the browser would then send `Origin: null` on POST, and the
 * server's Origin check refuses every mutating request.
 *
 * Network failures reject with ApiError(0, "network", ...).
 */

/** @typedef {import('./types.js').Connection} Connection */
/** @typedef {import('./types.js').ConnectionInput} ConnectionInput */
/** @typedef {import('./types.js').BulkChange} BulkChange */
/** @typedef {import('./types.js').Reachability} Reachability */
/** @typedef {import('./types.js').Group} Group */
/** @typedef {import('./types.js').TagInfo} TagInfo */
/** @typedef {import('./types.js').KeyInfo} KeyInfo */
/** @typedef {import('./types.js').KeyList} KeyList */
/** @typedef {import('./types.js').GenerateKeyInput} GenerateKeyInput */
/** @typedef {import('./types.js').KeyUpdate} KeyUpdate */
/** @typedef {import('./types.js').SuggestionList} SuggestionList */
/** @typedef {import('./types.js').HostKey} HostKey */
/** @typedef {import('./types.js').SessionInfo} SessionInfo */
/** @typedef {import('./types.js').Settings} Settings */
/** @typedef {import('./types.js').DiagReport} DiagReport */
/** @typedef {import('./types.js').ResolveResult} ResolveResult */
/** @typedef {import('./types.js').AuthStatus} AuthStatus */
/** @typedef {import('./types.js').TokenResponse} TokenResponse */

export const TOKEN_KEY = 'portkey.token';

export class ApiError extends Error {
  /**
   * @param {number} status
   * @param {string} code
   * @param {string} message
   * @param {Record<string, unknown>} [details]  extra envelope fields (fields, dependants, retry_after)
   */
  constructor(status, code, message, details = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

// --- token ------------------------------------------------------------------------------------

/** In-memory copy, used when localStorage is unavailable (private mode, blocked storage). */
/** @type {string|null} */
let memoryToken = null;
/** @type {(() => void)|null} */
let unauthorizedHandler = null;

/** @returns {string|null} */
export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? memoryToken;
  } catch {
    return memoryToken;
  }
}
/** @param {string} token */
export function setToken(token) {
  memoryToken = token;
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // memory only
  }
}
export function clearToken() {
  memoryToken = null;
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    // nothing stored
  }
}
/** @param {() => void} handler  called after a 401 clears the token */
export function onUnauthorized(handler) {
  unauthorizedHandler = handler;
}

// --- transport --------------------------------------------------------------------------------

const GENERIC = {
  network: "Port-Key isn't responding. Check that its container is running, then try again.",
  http: 'Port-Key could not do that. Try again.',
};

/**
 * @param {string} method
 * @param {string} path            starting with /api/
 * @param {{body?: unknown, auth?: boolean, expect?: 'json'|'text'|'none', signOutOn401?: boolean}} [opts]
 * @returns {Promise<any>}
 */
async function request(method, path, opts = {}) {
  /** @type {Record<string, string>} */
  const headers = { Accept: opts.expect === 'text' ? 'text/plain' : 'application/json' };
  const token = opts.auth === false ? null : getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  // No `mode`: the default ("cors") is what makes the browser send the page's real Origin.
  /** @type {RequestInit} */
  const init = { method, headers, cache: 'no-store', credentials: 'omit', redirect: 'error' };
  if (method !== 'GET' && method !== 'HEAD') {
    headers['Content-Type'] = 'application/json';
    headers['X-Port-Key'] = '1';
    init.body = JSON.stringify(opts.body ?? {});
  }
  /** @type {Response} */
  let res;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, 'network', GENERIC.network);
  }
  if (!res.ok) throw await toError(res, opts.signOutOn401 !== false);
  if (opts.expect === 'none' || res.status === 204) return undefined;
  if (opts.expect === 'text') return res.text();
  try {
    return await res.json();
  } catch {
    throw new ApiError(res.status, 'bad_response', GENERIC.http);
  }
}

/** @param {Response} res @param {boolean} signOutOn401 @returns {Promise<ApiError>} */
async function toError(res, signOutOn401) {
  /** @type {any} */
  let envelope = null;
  try {
    envelope = await res.json();
  } catch {
    envelope = null;
  }
  const body = envelope && typeof envelope.error === 'object' && envelope.error ? envelope.error : {};
  const code = typeof body.code === 'string' ? body.code : `http_${res.status}`;
  const message = typeof body.message === 'string' && body.message ? body.message : GENERIC.http;
  /** @type {Record<string, unknown>} */
  const details = {};
  for (const [key, value] of Object.entries(body)) if (key !== 'code' && key !== 'message') details[key] = value;
  const err = new ApiError(res.status, code, message, details);
  if (res.status === 401 && code !== 'bad_password' && signOutOn401) {
    clearToken();
    unauthorizedHandler?.();
  }
  return err;
}

/** @param {string} s */
const enc = (s) => encodeURIComponent(s);

// --- auth -------------------------------------------------------------------------------------

/** @returns {Promise<{status: string, version: string}>} */
export async function health() { return request('GET', '/api/health', { auth: false }); }
/** @returns {Promise<AuthStatus>} */
export async function authStatus() { return request('GET', '/api/auth/status', { signOutOn401: false }); }
/**
 * First-visit setup. `code` is the one-time setup code from the container log, as typed (any
 * case, separators optional). Rejects with 403 `bad_setup_code`, 429 `rate_limited`, 409
 * `setup_needs_restart` or `already_setup`.
 * @param {string} password @param {string} code @returns {Promise<TokenResponse>} stores the token
 */
export async function setupPassword(password, code) {
  /** @type {TokenResponse} */
  const res = await request('POST', '/api/auth/setup', { body: { password, code }, auth: false, signOutOn401: false });
  setToken(res.token);
  return res;
}
/** @param {string} password @returns {Promise<TokenResponse>} stores the token */
export async function login(password) {
  /** @type {TokenResponse} */
  const res = await request('POST', '/api/auth/login', { body: { password }, auth: false, signOutOn401: false });
  setToken(res.token);
  return res;
}
/** @returns {Promise<void>} clears the token */
export async function logout() {
  try {
    await request('POST', '/api/auth/logout', { expect: 'none', signOutOn401: false });
  } finally {
    clearToken();
  }
}
/** @param {string} oldPassword @param {string} newPassword @returns {Promise<void>} */
export async function changePassword(oldPassword, newPassword) {
  return request('POST', '/api/auth/password', { body: { old: oldPassword, new: newPassword }, expect: 'none' });
}

// --- connections ------------------------------------------------------------------------------

/** @returns {Promise<Connection[]>} */
export async function listConnections() { return (await request('GET', '/api/connections')).connections; }
/** @param {ConnectionInput} data @returns {Promise<Connection>} */
export async function createConnection(data) { return request('POST', '/api/connections', { body: data }); }
/** @param {string} id @param {Partial<ConnectionInput>} changes @returns {Promise<Connection>} */
export async function updateConnection(id, changes) {
  return request('PATCH', `/api/connections/${enc(id)}`, { body: changes });
}
/** @param {string} id @returns {Promise<void>} rejects with code "jump_in_use" (details.dependants) */
export async function deleteConnection(id) {
  return request('DELETE', `/api/connections/${enc(id)}`, { expect: 'none' });
}
/** @param {string} id @returns {Promise<Connection>} */
export async function duplicateConnection(id) {
  return request('POST', `/api/connections/${enc(id)}/duplicate`);
}
/** @param {BulkChange} change @returns {Promise<Connection[]>} */
export async function bulkUpdateConnections(change) {
  return (await request('POST', '/api/connections/bulk', { body: change })).connections;
}
/** @param {string[]} ids @returns {Promise<Reachability[]>} */
export async function checkReachability(ids) {
  return (await request('POST', '/api/connections/reachability', { body: { ids } })).results;
}

// --- folders and tags -------------------------------------------------------------------------

/** @returns {Promise<Group[]>} */
export async function listGroups() { return (await request('GET', '/api/groups')).groups; }
/** @param {string} name @param {string|null} [parentId] @returns {Promise<Group>} */
export async function createGroup(name, parentId = null) {
  return request('POST', '/api/groups', { body: { name, parent_id: parentId } });
}
/** @param {string} id @param {{name?: string, parent_id?: string|null, sort_order?: number}} changes @returns {Promise<Group>} */
export async function updateGroup(id, changes) {
  return request('PATCH', `/api/groups/${enc(id)}`, { body: changes });
}
/** @param {string} id @returns {Promise<void>} */
export async function deleteGroup(id) { return request('DELETE', `/api/groups/${enc(id)}`, { expect: 'none' }); }
/** @returns {Promise<TagInfo[]>} */
export async function listTags() { return (await request('GET', '/api/tags')).tags; }
/** @param {string} name @param {string} newName @returns {Promise<TagInfo>} */
export async function renameTag(name, newName) {
  return request('PATCH', `/api/tags/${enc(name)}`, { body: { name: newName } });
}
/** @param {string} name @returns {Promise<void>} */
export async function deleteTag(name) { return request('DELETE', `/api/tags/${enc(name)}`, { expect: 'none' }); }

// --- keys -------------------------------------------------------------------------------------

/** @returns {Promise<KeyList>} */
export async function listKeys() { return request('GET', '/api/keys'); }
/** @param {string} keyId @returns {Promise<string>} the public key line */
export async function getPublicKey(keyId) {
  return request('GET', `/api/keys/${enc(keyId)}/public`, { expect: 'text' });
}
/** @param {string} name @returns {Promise<{name: string, available: boolean, reason: 'taken'|'invalid'|null}>} */
export async function checkKeyName(name) {
  return request('GET', `/api/keys/name-check?name=${enc(name)}`);
}
/** @param {GenerateKeyInput} spec @returns {Promise<KeyInfo>} */
export async function generateKey(spec) { return request('POST', '/api/keys/generate', { body: spec }); }
/** @param {string} keyId @param {KeyUpdate} changes @returns {Promise<KeyInfo>} */
export async function updateKey(keyId, changes) {
  return request('PATCH', `/api/keys/${enc(keyId)}`, { body: changes });
}
/** @param {string} keyId @param {string} passphrase @param {boolean} remember @returns {Promise<void>} */
export async function unlockKey(keyId, passphrase, remember) {
  return request('POST', `/api/keys/${enc(keyId)}/unlock`, { body: { passphrase, remember }, expect: 'none' });
}
/** @returns {Promise<void>} */
export async function lockKeys() { return request('POST', '/api/keys/lock', { expect: 'none' }); }

// --- hosts ------------------------------------------------------------------------------------

/** @returns {Promise<SuggestionList>} */
export async function listSuggestions() { return request('GET', '/api/hosts/suggestions'); }
/** @param {string} host @param {number} port @returns {Promise<void>} */
export async function dismissSuggestion(host, port) {
  return request('POST', '/api/hosts/suggestions/dismiss', { body: { host, port }, expect: 'none' });
}
/** @param {string} host @param {number} port @returns {Promise<void>} */
export async function restoreSuggestion(host, port) {
  return request('POST', '/api/hosts/suggestions/restore', { body: { host, port }, expect: 'none' });
}
/** @returns {Promise<HostKey[]>} */
export async function listHostKeys() { return (await request('GET', '/api/hostkeys')).host_keys; }
/** @param {number} id @param {string} confirmHost @returns {Promise<void>} */
export async function forgetHostKey(id, confirmHost) {
  return request('DELETE', `/api/hostkeys/${enc(String(id))}`, { body: { confirm_host: confirmHost }, expect: 'none' });
}

// --- sessions, settings, diagnostics ----------------------------------------------------------

/** @returns {Promise<SessionInfo[]>} */
export async function listSessions() { return (await request('GET', '/api/sessions')).sessions; }
/** @param {string} sessionId @returns {Promise<void>} */
export async function killSession(sessionId) {
  return request('DELETE', `/api/sessions/${enc(sessionId)}`, { expect: 'none' });
}
/** @returns {Promise<Settings>} */
export async function getSettings() { return request('GET', '/api/settings'); }
/** @param {Partial<Omit<Settings, 'about'>>} changes @returns {Promise<Settings>} */
export async function updateSettings(changes) { return request('PATCH', '/api/settings', { body: changes }); }
/** @returns {Promise<DiagReport>} */
export async function getDiagnostics() { return request('GET', '/api/diag'); }
/** @param {string} host @param {number} [port] @returns {Promise<ResolveResult>} */
export async function resolveHost(host, port = 22) {
  return request('POST', '/api/diag/resolve', { body: { host, port } });
}
