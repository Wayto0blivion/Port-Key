/*
 * api.js — owner: package 8a (frontend shell). Function names and signatures are frozen by
 * package 0 (docs/api.md); bodies are 8a's.
 *
 * HTTP client for /api. Every request sends `Authorization: Bearer <token>` when a token is
 * stored; every non-GET request sends `Content-Type: application/json`, `X-Port-Key: 1` and a
 * JSON body (`{}` when there are no fields). Errors reject with ApiError built from the
 * `{error: {code, message, ...}}` envelope; a 401 on any call except login/setup clears the
 * token and calls the handler set with onUnauthorized(). The token lives in localStorage under
 * TOKEN_KEY and never goes into a URL.
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

const todo = () => {
  throw new Error('not implemented (package 8a)');
};

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

/** @returns {string|null} */
export function getToken() { return todo(); }
/** @param {string} token */
export function setToken(token) { todo(); }
export function clearToken() { todo(); }
/** @param {() => void} handler  called after a 401 clears the token */
export function onUnauthorized(handler) { todo(); }

// --- auth -------------------------------------------------------------------------------------

/** @returns {Promise<{status: string, version: string}>} */
export async function health() { return todo(); }
/** @returns {Promise<AuthStatus>} */
export async function authStatus() { return todo(); }
/** @param {string} password @returns {Promise<TokenResponse>} stores the token */
export async function setupPassword(password) { return todo(); }
/** @param {string} password @returns {Promise<TokenResponse>} stores the token */
export async function login(password) { return todo(); }
/** @returns {Promise<void>} clears the token */
export async function logout() { return todo(); }
/** @param {string} oldPassword @param {string} newPassword @returns {Promise<void>} */
export async function changePassword(oldPassword, newPassword) { return todo(); }

// --- connections ------------------------------------------------------------------------------

/** @returns {Promise<Connection[]>} */
export async function listConnections() { return todo(); }
/** @param {ConnectionInput} data @returns {Promise<Connection>} */
export async function createConnection(data) { return todo(); }
/** @param {string} id @param {Partial<ConnectionInput>} changes @returns {Promise<Connection>} */
export async function updateConnection(id, changes) { return todo(); }
/** @param {string} id @returns {Promise<void>} rejects with code "jump_in_use" (details.dependants) */
export async function deleteConnection(id) { return todo(); }
/** @param {string} id @returns {Promise<Connection>} */
export async function duplicateConnection(id) { return todo(); }
/** @param {BulkChange} change @returns {Promise<Connection[]>} */
export async function bulkUpdateConnections(change) { return todo(); }
/** @param {string[]} ids @returns {Promise<Reachability[]>} */
export async function checkReachability(ids) { return todo(); }

// --- folders and tags -------------------------------------------------------------------------

/** @returns {Promise<Group[]>} */
export async function listGroups() { return todo(); }
/** @param {string} name @param {string|null} [parentId] @returns {Promise<Group>} */
export async function createGroup(name, parentId = null) { return todo(); }
/** @param {string} id @param {{name?: string, parent_id?: string|null, sort_order?: number}} changes @returns {Promise<Group>} */
export async function updateGroup(id, changes) { return todo(); }
/** @param {string} id @returns {Promise<void>} */
export async function deleteGroup(id) { return todo(); }
/** @returns {Promise<TagInfo[]>} */
export async function listTags() { return todo(); }
/** @param {string} name @param {string} newName @returns {Promise<TagInfo>} */
export async function renameTag(name, newName) { return todo(); }
/** @param {string} name @returns {Promise<void>} */
export async function deleteTag(name) { return todo(); }

// --- keys -------------------------------------------------------------------------------------

/** @returns {Promise<KeyList>} */
export async function listKeys() { return todo(); }
/** @param {string} keyId @returns {Promise<string>} the public key line */
export async function getPublicKey(keyId) { return todo(); }
/** @param {string} name @returns {Promise<{name: string, available: boolean, reason: 'taken'|'invalid'|null}>} */
export async function checkKeyName(name) { return todo(); }
/** @param {GenerateKeyInput} spec @returns {Promise<KeyInfo>} */
export async function generateKey(spec) { return todo(); }
/** @param {string} keyId @param {KeyUpdate} changes @returns {Promise<KeyInfo>} */
export async function updateKey(keyId, changes) { return todo(); }
/** @param {string} keyId @param {string} passphrase @param {boolean} remember @returns {Promise<void>} */
export async function unlockKey(keyId, passphrase, remember) { return todo(); }
/** @returns {Promise<void>} */
export async function lockKeys() { return todo(); }

// --- hosts ------------------------------------------------------------------------------------

/** @returns {Promise<SuggestionList>} */
export async function listSuggestions() { return todo(); }
/** @param {string} host @param {number} port @returns {Promise<void>} */
export async function dismissSuggestion(host, port) { return todo(); }
/** @param {string} host @param {number} port @returns {Promise<void>} */
export async function restoreSuggestion(host, port) { return todo(); }
/** @returns {Promise<HostKey[]>} */
export async function listHostKeys() { return todo(); }
/** @param {number} id @param {string} confirmHost @returns {Promise<void>} */
export async function forgetHostKey(id, confirmHost) { return todo(); }

// --- sessions, settings, diagnostics ----------------------------------------------------------

/** @returns {Promise<SessionInfo[]>} */
export async function listSessions() { return todo(); }
/** @param {string} sessionId @returns {Promise<void>} */
export async function killSession(sessionId) { return todo(); }
/** @returns {Promise<Settings>} */
export async function getSettings() { return todo(); }
/** @param {Partial<Omit<Settings, 'about'>>} changes @returns {Promise<Settings>} */
export async function updateSettings(changes) { return todo(); }
/** @returns {Promise<DiagReport>} */
export async function getDiagnostics() { return todo(); }
/** @param {string} host @param {number} [port] @returns {Promise<ResolveResult>} */
export async function resolveHost(host, port = 22) { return todo(); }
