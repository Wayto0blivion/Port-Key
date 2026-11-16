/*
 * parts/fields.js — owner: package 8a (private module; 8b's wizard may reuse it).
 *
 * Form building blocks that match docs/design (label above, helper below, error under that):
 *   field({id, label, control, help, wide})        -> div.field
 *   setFieldError(control, message|null)           -> shows/clears the error, aria-invalid
 *   colorSwatches({name, value, onChange, labelId}) -> radio group of the six colour labels
 *   COLORS                                          -> ['red', 'amber', ...]
 * Errors are linked to their control with aria-describedby, so screen readers read them.
 */

import { h } from '../ui/dom.js';
import { icon } from './icons.js';

/** @typedef {import('../types.js').Color} Color */

/** @type {Color[]} */
export const COLORS = ['red', 'amber', 'green', 'blue', 'purple', 'grey'];
const COLOR_NAMES = { red: 'Red', amber: 'Amber', green: 'Green', blue: 'Blue', purple: 'Purple', grey: 'Grey' };

/**
 * @param {{id: string, label: string, control: HTMLElement, help?: string, className?: string, labelTag?: 'label'|'span'}} opts
 * @returns {HTMLElement}
 */
export function field(opts) {
  const helpId = `${opts.id}-help`;
  const errorId = `${opts.id}-error`;
  const labelEl = opts.labelTag === 'span'
    ? h('span', { id: `${opts.id}-label`, class: 'field__label' }, opts.label)
    : h('label', { class: 'field__label', for: opts.id }, opts.label);
  const describedBy = [opts.help ? helpId : null, errorId].filter(Boolean).join(' ');
  const target = opts.control.matches('input, select, textarea')
    ? opts.control
    : opts.control.querySelector('input, select, textarea');
  if (target) {
    const existing = target.getAttribute('aria-describedby');
    target.setAttribute('aria-describedby', existing ? `${existing} ${describedBy}` : describedBy);
  }
  return h('div', { class: `field${opts.className ? ` ${opts.className}` : ''}` },
    labelEl,
    opts.control,
    opts.help ? h('span', { id: helpId, class: 'field__help' }, opts.help) : null,
    h('span', { id: errorId, class: 'field__error', hidden: true, dataset: { errorFor: opts.id } }));
}

/**
 * Show or clear the error under a control built with field().
 * @param {HTMLElement} control @param {string|null} message
 */
export function setFieldError(control, message) {
  const box = control.closest('.field')?.querySelector('.field__error');
  if (box instanceof HTMLElement) {
    box.textContent = message ?? '';
    box.hidden = !message;
  }
  const target = control.matches('input, select, textarea') ? control : control.querySelector('input, select, textarea');
  if (message) target?.setAttribute('aria-invalid', 'true');
  else target?.removeAttribute('aria-invalid');
}

/**
 * Six colour-label swatches as a native radio group (arrow keys move, Space selects). The
 * selected swatch shows a check mark, so the choice does not rely on colour alone.
 * @param {{name: string, value: Color, onChange: (color: Color) => void, labelId: string}} opts
 * @returns {HTMLElement}
 */
export function colorSwatches(opts) {
  const group = h('div', { class: 'swatches', role: 'radiogroup', 'aria-labelledby': opts.labelId });
  for (const color of COLORS) {
    const input = /** @type {HTMLInputElement} */ (h('input', {
      type: 'radio', name: opts.name, value: color, class: 'swatch__input', checked: color === opts.value,
      onChange: () => opts.onChange(color),
    }));
    group.appendChild(h('label', { class: `swatch lbl-${color}` },
      input,
      h('span', { class: 'swatch__dot' }, icon('check', { size: 14, strokeWidth: 3 })),
      h('span', { class: 'visually-hidden' }, COLOR_NAMES[color])));
  }
  return group;
}
