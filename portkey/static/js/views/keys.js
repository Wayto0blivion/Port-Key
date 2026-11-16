/*
 * keys.js — owner: package 8a.
 *
 * The Keys tab: table (Name with Default pill, Type, Fingerprint with strip, Source,
 * Passphrase, Used by), detail pane (Windows path, public key box, Copy public key, Copy install
 * command, Install on a host…, Set as default, Hide from Port-Key), hidden keys behind "Show
 * hidden keys", the Generate key dialog and the hide-with-reassign dialog.
 */

const todo = () => {
  throw new Error('not implemented (package 8a)');
};

/** @param {HTMLElement} container @returns {void} */
export function renderKeys(container) { todo(); }

/** @returns {Promise<import('../types.js').KeyInfo|null>} */
export async function openGenerateKey() { return todo(); }
