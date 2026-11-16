/*
 * tagfield.js — owner: package 8a.
 *
 * Tags token field with autocomplete from existing tags; normalises like the server
 * (trim, drop a leading #, lower-case, pattern [a-z0-9][a-z0-9._-]{0,31}). Tags have no colour.
 *
 * createTagField(opts) returns a wrapper with the chips ("#prod ×", each × is a real button)
 * and a combobox input (give opts.id for a <label for>). Typing shows matching existing tags
 * in a listbox: Up/Down choose, Enter or Tab-free comma/space adds, Backspace on an empty
 * input removes the last chip, Esc closes the list. Pasting "prod, web" adds both. Text left
 * in the input is added on blur. onChange receives the full sorted list after every change.
 *
 * Before saving a form, call commitTagField(wrapper): it adds pending text and returns false
 * when that text is not a valid tag (the field then shows why and keeps focus).
 */

import { h, clear } from './dom.js';
import { icon } from '../parts/icons.js';

const PATTERN = /^[a-z0-9][a-z0-9._-]{0,31}$/;
export const MAX_TAGS = 32;
export const TAG_HELP = 'Lower-case letters, digits, dot, dash and underscore.';

/** @type {WeakMap<HTMLElement, () => boolean>} */
const committers = new WeakMap();
let seq = 0;

/** @param {string} raw @returns {string|null} the normalised tag, or null if invalid */
export function normalizeTag(raw) {
  const tag = String(raw).trim().replace(/^#+/, '').toLowerCase();
  return PATTERN.test(tag) ? tag : null;
}

/**
 * @param {{value: string[], suggestions: string[], onChange: (tags: string[]) => void, id?: string,
 *   placeholder?: string, describedBy?: string}} opts
 * @returns {HTMLElement}
 */
export function createTagField(opts) {
  seq += 1;
  const id = opts.id ?? `tags-${seq}`;
  const listId = `${id}-list`;
  const errorId = `${id}-tagerror`;
  /** @type {string[]} */
  let tags = [...new Set(opts.value.map((t) => normalizeTag(t)).filter((t) => t !== null))].sort();
  /** @type {string[]} */
  let matches = [];
  let activeIndex = -1;

  const chips = h('span', { class: 'tagfield__chips' });
  const input = /** @type {HTMLInputElement} */ (h('input', {
    id,
    class: 'tagfield__input',
    role: 'combobox',
    'aria-autocomplete': 'list',
    'aria-expanded': 'false',
    'aria-controls': listId,
    'aria-describedby': [opts.describedBy, errorId].filter(Boolean).join(' '),
    autocomplete: 'off',
    spellcheck: 'false',
    placeholder: opts.placeholder ?? 'Add a tag',
  }));
  const list = h('ul', { id: listId, class: 'tagfield__list', role: 'listbox', hidden: true, 'aria-label': 'Existing tags' });
  const error = h('span', { id: errorId, class: 'field__error', hidden: true });
  const box = h('div', { class: 'tagfield__box' }, chips, input);
  const wrap = h('div', { class: 'tagfield' }, box, list, error);
  box.addEventListener('mousedown', (e) => {
    if (e.target === box || e.target === chips) {
      e.preventDefault();
      input.focus();
    }
  });

  const emit = () => {
    tags.sort();
    renderChips();
    opts.onChange([...tags]);
  };
  const showError = (/** @type {string|null} */ message) => {
    error.textContent = message ?? '';
    error.hidden = !message;
    if (message) input.setAttribute('aria-invalid', 'true');
    else input.removeAttribute('aria-invalid');
  };
  /** @param {string} raw @returns {boolean} */
  const add = (raw) => {
    const parts = raw.split(/[\s,]+/).filter(Boolean);
    let ok = true;
    for (const part of parts) {
      const tag = normalizeTag(part);
      if (!tag) {
        showError(`"${part.slice(0, 40)}" is not a valid tag. ${TAG_HELP}`);
        ok = false;
        continue;
      }
      if (tags.includes(tag)) continue;
      if (tags.length >= MAX_TAGS) {
        showError(`A connection can have at most ${MAX_TAGS} tags.`);
        ok = false;
        break;
      }
      tags.push(tag);
    }
    if (ok) showError(null);
    emit();
    return ok;
  };
  const remove = (/** @type {string} */ tag) => {
    tags = tags.filter((t) => t !== tag);
    emit();
    input.focus();
  };
  const commit = () => {
    const text = input.value.trim();
    if (!text) return true;
    const ok = add(text);
    if (ok) input.value = '';
    closeList();
    return ok;
  };
  committers.set(wrap, () => {
    const ok = commit();
    if (!ok) input.focus();
    return ok;
  });

  function renderChips() {
    clear(chips);
    for (const tag of tags) {
      chips.appendChild(h('span', { class: 'chip chip--tag' },
        h('span', { class: 'chip__text' }, `#${tag}`),
        h('button', {
          type: 'button', class: 'chip__remove', 'aria-label': `Remove tag ${tag}`, onClick: () => remove(tag),
        }, icon('close', { size: 10 }))));
    }
  }
  function closeList() {
    matches = [];
    activeIndex = -1;
    list.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
  }
  function renderList() {
    const q = input.value.trim().replace(/^#+/, '').toLowerCase();
    if (!q) {
      closeList();
      return;
    }
    const pool = opts.suggestions.filter((s) => !tags.includes(s));
    matches = [...pool.filter((s) => s.startsWith(q)), ...pool.filter((s) => !s.startsWith(q) && s.includes(q))].slice(0, 8);
    clear(list);
    if (!matches.length) {
      closeList();
      return;
    }
    activeIndex = Math.min(activeIndex, matches.length - 1);
    matches.forEach((m, i) => {
      list.appendChild(h('li', {
        id: `${listId}-${i}`,
        class: `tagfield__option${i === activeIndex ? ' is-active' : ''}`,
        role: 'option',
        'aria-selected': String(i === activeIndex),
        onMousedown: (/** @type {MouseEvent} */ e) => {
          e.preventDefault();
          pick(m);
        },
      }, `#${m}`));
    });
    list.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    if (activeIndex >= 0) input.setAttribute('aria-activedescendant', `${listId}-${activeIndex}`);
    else input.removeAttribute('aria-activedescendant');
  }
  const pick = (/** @type {string} */ tag) => {
    input.value = '';
    add(tag);
    closeList();
    input.focus();
  };

  input.addEventListener('input', () => {
    activeIndex = -1;
    if (/[\s,]/.test(input.value)) {
      const text = input.value;
      const tail = /[\s,]$/.test(text) ? '' : text.split(/[\s,]+/).pop() ?? '';
      const head = tail ? text.slice(0, text.length - tail.length) : text;
      if (add(head)) input.value = tail;
    }
    renderList();
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (!matches.length) renderList();
      if (!matches.length) return;
      e.preventDefault();
      const step = e.key === 'ArrowDown' ? 1 : -1;
      if (activeIndex === -1) activeIndex = step > 0 ? 0 : matches.length - 1;
      else {
        activeIndex += step;
        if (activeIndex < 0 || activeIndex >= matches.length) activeIndex = -1;
      }
      renderList();
    } else if (e.key === 'Enter') {
      if (activeIndex >= 0 && matches[activeIndex]) {
        e.preventDefault();
        pick(matches[activeIndex]);
      } else if (input.value.trim()) {
        e.preventDefault();
        commit();
      }
    } else if (e.key === 'Escape' && !list.hidden) {
      e.preventDefault();
      e.stopPropagation();
      closeList();
    } else if (e.key === 'Backspace' && !input.value && tags.length) {
      remove(tags[tags.length - 1]);
    }
  });
  input.addEventListener('blur', () => {
    commit();
  });
  renderChips();
  return wrap;
}

/** Add pending text; false when it is not a valid tag. @param {HTMLElement} wrapper */
export function commitTagField(wrapper) {
  return committers.get(wrapper)?.() ?? true;
}
