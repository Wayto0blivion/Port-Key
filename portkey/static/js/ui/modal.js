/*
 * modal.js — owner: package 8a.
 *
 * The modal primitive used by every dialog (8a's and 8b's): backdrop, focus trap on the
 * first field (safe button first in danger dialogs), Esc closes, full width below 600px.
 */

const todo = () => {
  throw new Error('not implemented (package 8a)');
};

/**
 * @param {{title: string, body: Node, buttons?: {label: string, kind?: 'primary'|'secondary'|'danger', value: string}[],
 *   danger?: boolean, width?: number}} spec
 * @returns {{element: HTMLElement, result: Promise<string|null>, close: (value?: string|null) => void}}
 */
export function openModal(spec) { return todo(); }
