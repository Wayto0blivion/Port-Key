/*
 * types.js — owner: package 0 (frozen contract). JSDoc typedefs mirroring
 * portkey/api/schemas and docs/api.md / docs/ws-protocol.md, for `tsc --checkJs`.
 * Import types with: /** @typedef {import('./types.js').Connection} Connection *\/
 * This module exports nothing at runtime.
 */

/** @typedef {'red'|'amber'|'green'|'blue'|'purple'|'grey'} Color */
/** @typedef {'key'|'password'} AuthMethod */

/**
 * @typedef {object} Connection
 * @property {string} id
 * @property {string} name
 * @property {string} host
 * @property {number} port
 * @property {string} username
 * @property {AuthMethod} auth_method
 * @property {string|null} key_fp    null = the default key
 * @property {string|null} jump_id
 * @property {string|null} group_id  null = ungrouped
 * @property {string[]} tags
 * @property {Color} color
 * @property {boolean} pinned
 * @property {string} notes
 * @property {number} sort_order
 * @property {string} created_at     ISO-8601 UTC
 * @property {string} updated_at
 * @property {string|null} last_used_at
 * @property {number} use_count
 */

/**
 * @typedef {object} ConnectionInput  body of POST /api/connections; PATCH takes any subset
 * @property {string} name
 * @property {string} host
 * @property {number} [port]
 * @property {string} username
 * @property {AuthMethod} auth_method
 * @property {string|null} [key_fp]
 * @property {string|null} [jump_id]
 * @property {string|null} [group_id]
 * @property {string[]} [tags]
 * @property {Color} [color]
 * @property {boolean} [pinned]
 * @property {string} [notes]
 * @property {number} [sort_order]
 */

/**
 * @typedef {object} BulkChange
 * @property {string[]} ids
 * @property {string|null} [set_group_id]  omit to leave folders alone; null = Ungrouped
 * @property {string[]} [add_tags]
 * @property {string[]} [remove_tags]
 */

/** @typedef {{id: string, state: 'up'|'down'|'skipped', ms?: number}} Reachability */

/**
 * @typedef {object} Group
 * @property {string} id
 * @property {string} name
 * @property {string|null} parent_id
 * @property {number} sort_order
 * @property {number} connection_count  includes descendants
 */

/** @typedef {{name: string, count: number}} TagInfo */
/** @typedef {{id: string, name: string}} ConnectionRef */

/**
 * @typedef {object} KeyInfo
 * @property {string} id        URL-safe fingerprint, use in /api/keys/{id}
 * @property {string} fp        "SHA256:..."
 * @property {string} name      file name
 * @property {string|null} label
 * @property {'ed25519'|'rsa'|'ecdsa'} type
 * @property {number} bits
 * @property {string} comment
 * @property {string} path      Windows path
 * @property {'ssh_dir'|'generated'} source
 * @property {boolean} encrypted
 * @property {boolean} unlocked
 * @property {string} public_key
 * @property {boolean} is_default
 * @property {boolean} hidden
 * @property {ConnectionRef[]} used_by
 */

/** @typedef {{path: string, reason: 'ppk'|'not_private_key'|'unreadable'|'unsupported_type', detail: string}} KeyProblem */

/**
 * @typedef {object} KeyList
 * @property {KeyInfo[]} keys
 * @property {KeyProblem[]} problems
 * @property {string|null} default_fp
 * @property {number} unlocked_count
 */

/**
 * @typedef {object} GenerateKeyInput
 * @property {string} name
 * @property {'ed25519'|'rsa'|'ecdsa'} [type]
 * @property {number|null} [bits]
 * @property {string} [comment]
 * @property {string|null} [passphrase]
 */

/**
 * @typedef {object} KeyUpdate
 * @property {string|null} [label]
 * @property {boolean} [hidden]
 * @property {{auth_method: AuthMethod, key_fp?: string|null}} [reassign]
 */

// The server leaves out dismissed hosts and hosts a saved connection already uses.
/** @typedef {{host: string, port: number, aliases: string[], key_types: string[]}} Suggestion */
/** @typedef {{suggestions: Suggestion[], hashed_count: number}} SuggestionList */

/**
 * @typedef {object} HostKey
 * @property {number} id
 * @property {string} host
 * @property {number} port
 * @property {string} key_type
 * @property {string} fp_sha256
 * @property {string} public_key
 * @property {string} first_seen_at
 * @property {string} last_seen_at
 */

/** @typedef {{session_id: string, connection_id: string, started_at: string, attached: boolean, detached_at: string|null}} SessionInfo */

/**
 * @typedef {object} Settings
 * @property {string|null} default_key_fp
 * @property {string} default_user
 * @property {number} passphrase_ttl   seconds
 * @property {number} detach_grace     seconds
 * @property {number} font_size
 * @property {boolean} windows_copy_paste
 * @property {boolean} leave_warning
 * @property {{ssh_dir: string, keys_dir: string, data_dir: string, version: string}} about
 */

/** @typedef {{path: string, present: boolean, readable: boolean, writable: boolean}} DirStatus */

/**
 * @typedef {object} DiagReport
 * @property {string} version
 * @property {string} python
 * @property {string} asyncssh
 * @property {DirStatus & {key_count: number, known_hosts: boolean, mode_note: string|null}} ssh_dir
 * @property {DirStatus} keys_dir
 * @property {DirStatus} data_dir
 * @property {KeyProblem[]} key_problems
 * @property {string[]} notes
 */

/**
 * @typedef {object} ResolveResult
 * @property {string} host
 * @property {number} port
 * @property {string[]} addresses
 * @property {string|null} resolve_error
 * @property {{state: 'up'|'down'|'skipped', ms: number|null, error: 'refused'|'timeout'|'unreachable'|null}} tcp
 */

/** @typedef {{setup: boolean, authenticated: boolean}} AuthStatus */
/** @typedef {{token: string, expires_at: string}} TokenResponse */

/**
 * @typedef {object} Hop  which hop of a jump chain a WebSocket message refers to
 * @property {number} index   0 = outermost jump host; count - 1 = the target
 * @property {number} count
 * @property {string|null} connection_id
 * @property {string} name
 * @property {string} host
 * @property {number} port
 * @property {'jump'|'target'} role
 */

/**
 * @typedef {object} Prompt  a server "prompt" message; fields depend on kind
 * @property {'prompt'} type
 * @property {string} id
 * @property {'hostkey_new'|'hostkey_changed'|'passphrase'|'password'|'kbdint'} kind
 * @property {Hop} hop
 * @property {string} [host]
 * @property {number} [port]
 * @property {string} [key_type]
 * @property {string} [fingerprint]
 * @property {string} [old_key_type]
 * @property {string} [old_fingerprint]
 * @property {string} [new_fingerprint]
 * @property {string|null} [first_seen_at]
 * @property {string} [confirm_name]
 * @property {string} [key_fp]
 * @property {string} [key_name]
 * @property {string} [path]
 * @property {string} [user]
 * @property {boolean} [retry]
 * @property {string} [name]
 * @property {string} [instructions]
 * @property {{text: string, echo: boolean}[]} [prompts]
 */

/**
 * @typedef {object} PromptReply
 * @property {boolean} [cancel]
 * @property {boolean} [accept]
 * @property {string} [confirm_name]
 * @property {string} [value]
 * @property {boolean} [remember]
 * @property {string[]} [values]
 */

/** @typedef {{type: 'error', code: string, message: string, close: number, hop?: Hop, methods?: string[], cause?: string}} WsError */

export {};
