/*
 * tagfield.js — owner: package 8a.
 *
 * Tags token field with autocomplete from existing tags; normalises like the server
 * (trim, drop a leading #, lower-case, pattern [a-z0-9][a-z0-9._-]{0,31}). Tags have no colour.
 */

const todo = () => {
  throw new Error('not implemented (package 8a)');
};

/** @param {{value: string[], suggestions: string[], onChange: (tags: string[]) => void}} opts @returns {HTMLElement} */
export function createTagField(opts) { return todo(); }

/** @param {string} raw @returns {string|null} the normalised tag, or null if invalid */
export function normalizeTag(raw) { return todo(); }
