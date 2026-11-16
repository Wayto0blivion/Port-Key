/*
 * wizard.js — owner: package 8b.
 *
 * The key install wizard dialog over /ws/install: step 1 Host details (including the
 * Folder picker and Tags field from ui/folderpicker.js and ui/tagfield.js), 2 Check host
 * identity, 3 Sign in once (password and 2FA fields from prompts), 4 Verify key login checklist,
 * the final "Connection saved" card, the manual path, and every failure panel in
 * docs/ux-spec.md with its actions (sent as `action` messages).
 */

/** @typedef {import('../types.js').Connection} Connection */

const todo = () => {
  throw new Error('not implemented (package 8b)');
};

/**
 * @param {{host?: string, port?: number, username?: string, name?: string, group_id?: string|null,
 *   tags?: string[], key_fp?: string|null, jump_id?: string|null, connection_id?: string}} [prefill]
 * @param {{startAt?: 'host'|'signin'}} [opts]
 * @returns {Promise<Connection|null>} the saved connection, or null if closed
 */
export async function openWizard(prefill = {}, opts = {}) { return todo(); }
