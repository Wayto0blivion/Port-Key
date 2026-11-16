/*
 * dialogs.js — owner: package 8b.
 *
 * Connection-time dialogs (copy in docs/ux-spec.md section 4): host key first seen (and
 * the jump-host variant), host key changed with typed-name confirmation ([Keep it closed] has
 * initial focus), passphrase (Keep unlocked for 8 hours), password, keyboard-interactive, paste
 * guard ("Paste 14 lines into web-prod?"), and close-all confirmation. Built on ui/modal.js.
 */

/** @typedef {import('../types.js').Prompt} Prompt */
/** @typedef {import('../types.js').PromptReply} PromptReply */

const todo = () => {
  throw new Error('not implemented (package 8b)');
};

/** @param {Prompt} prompt @returns {Promise<PromptReply>} */
export async function answerPrompt(prompt) { return todo(); }

/** @param {number} lines @param {string} name @returns {Promise<boolean>} */
export async function confirmPaste(lines, name) { return todo(); }

/** @param {number} count @returns {Promise<boolean>} */
export async function confirmCloseAll(count) { return todo(); }
