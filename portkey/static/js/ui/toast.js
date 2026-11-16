/*
 * toast.js — owner: package 8a.
 *
 * Toasts: what was copied ("Public key copied"), errors, and 10-second Undo toasts.
 */

const todo = () => {
  throw new Error('not implemented (package 8a)');
};

/** @param {string} message @param {{undo?: () => void, timeout?: number, kind?: 'info'|'error'}} [opts] */
export function toast(message, opts = {}) { todo(); }
