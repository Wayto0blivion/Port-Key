/*
 * parts/icons.js — owner: package 8a (private module of the frontend shell; 8b may import it).
 *
 * Inline SVG icons drawn with createElementNS (no markup strings). Every icon is decorative
 * (aria-hidden) unless a label is given, in which case it gets role="img" and aria-label.
 * Exports: icon(name, opts).
 */

import { svg } from '../ui/dom.js';

/** @typedef {'key'|'home'|'chevron'|'lock'|'menu'|'more'|'close'|'folder'|'plus'|'check'|'cross'|'search'} IconName */

/** @type {Record<IconName, {d: string[], circle?: number[], rect?: number[], fill?: boolean}>} */
const PATHS = {
  key: { d: ['M10.5 12H21M17 12v3.5M20.5 12v2.5'], circle: [7, 12, 3.5] },
  home: { d: ['M4 11l8-7 8 7v9h-5v-6H9v6H4z'] },
  chevron: { d: ['M6 9l6 6 6-6'] },
  lock: { d: ['M8 11V7a4 4 0 0 1 7.5-2'], rect: [5, 11, 14, 9] },
  menu: { d: ['M4 6h16M4 12h16M4 18h16'] },
  more: { d: ['M5 12h.01M12 12h.01M19 12h.01'] },
  close: { d: ['M6 6l12 12M18 6L6 18'] },
  folder: { d: ['M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z'] },
  plus: { d: ['M12 5v14M5 12h14'] },
  check: { d: ['M5 12.5l4.5 4.5L19 7.5'] },
  cross: { d: ['M6 6l12 12M18 6L6 18'] },
  search: { d: ['M16 16l4.5 4.5'], circle: [10.5, 10.5, 6] },
};

/**
 * @param {IconName} name
 * @param {{size?: number, label?: string, className?: string, strokeWidth?: number}} [opts]
 * @returns {SVGElement}
 */
export function icon(name, opts = {}) {
  const spec = PATHS[name];
  const size = opts.size ?? 16;
  /** @type {Record<string, string|number>} */
  const attrs = {
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    'stroke-width': opts.strokeWidth ?? (name === 'more' ? 3 : 2),
    'stroke-linecap': 'round',
    'stroke-linejoin': 'round',
    class: `icon icon-${name}${opts.className ? ` ${opts.className}` : ''}`,
    focusable: 'false',
  };
  if (opts.label) {
    attrs.role = 'img';
    attrs['aria-label'] = opts.label;
  } else {
    attrs['aria-hidden'] = 'true';
  }
  const children = [];
  if (spec.circle) {
    const [cx, cy, r] = spec.circle;
    children.push(svg('circle', { cx, cy, r }));
  }
  if (spec.rect) {
    const [x, y, width, height] = spec.rect;
    children.push(svg('rect', { x, y, width, height, rx: 2 }));
  }
  for (const d of spec.d) children.push(svg('path', { d }));
  return svg('svg', attrs, ...children);
}
