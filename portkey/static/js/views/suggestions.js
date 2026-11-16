/*
 * suggestions.js — owner: package 8a.
 *
 * Suggestions from known_hosts: merged rows ("parser-vm · 192.168.10.27", key type),
 * saved hosts hidden, [Add] opens the wizard prefilled, [Dismiss] with a 10 s Undo toast
 * (restore), and the hashed-entries footer.
 *
 * renderSuggestions(container, {limit, compact, onSeeAll}) renders into `container` from the
 * store (loading the list first if needed). Used by the sidebar section (compact), the Home
 * panel and the first-run screen (limit 3 with "See all 14").
 */

import * as api from '../api.js';
import * as store from '../store.js';
import { h, clear, isIpAddress, sshKeyTypeLabel } from '../ui/dom.js';
import { toast, toastError } from '../ui/toast.js';
import { addConnection } from '../parts/actions.js';

/** @typedef {import('../types.js').Suggestion} Suggestion */
/** @typedef {import('../types.js').SuggestionList} SuggestionList */

/** @param {SuggestionList|null} list @returns {Suggestion[]} */
export function unsavedSuggestions(list) {
  return list ? list.suggestions.filter((s) => !s.saved) : [];
}

/** "parser-vm · 192.168.10.27", with ":2222" when the port is not 22. @param {Suggestion} s */
export function suggestionLabel(s) {
  const port = s.port === 22 ? '' : `:${s.port}`;
  const alias = s.aliases.find((a) => a !== s.host);
  return alias ? `${s.host} · ${alias}${port}` : `${s.host}${port}`;
}

/** A connection name for the wizard: the first host name that is not an address. @param {Suggestion} s */
function suggestedName(s) {
  const name = [s.host, ...s.aliases].find((n) => !isIpAddress(n));
  return name ? name.split('.')[0] : '';
}

/** @param {Suggestion} s */
async function dismiss(s) {
  try {
    await api.dismissSuggestion(s.host, s.port);
    await store.refreshSuggestions();
  } catch (err) {
    if (!(err instanceof api.ApiError && err.code === 'unauthorized')) toastError(err);
    return;
  }
  toast(`Dismissed ${s.host}.`, {
    undo: async () => {
      try {
        await api.restoreSuggestion(s.host, s.port);
        await store.refreshSuggestions();
      } catch (err) {
        toastError(err);
      }
    },
  });
}

/**
 * @param {HTMLElement} container
 * @param {{limit?: number, compact?: boolean, onSeeAll?: () => void}} [opts]
 * @returns {Promise<void>}
 */
export async function renderSuggestions(container, opts = {}) {
  const state = store.getState();
  if (state.status.suggestions === 'idle') {
    draw(container, opts);
    await store.refreshSuggestions().catch(() => {});
  }
  draw(container, opts);
}

/** @param {HTMLElement} container @param {{limit?: number, compact?: boolean, onSeeAll?: () => void}} opts */
function draw(container, opts) {
  const state = store.getState();
  clear(container);
  container.classList.add('suggestions');
  container.classList.toggle('suggestions--compact', Boolean(opts.compact));
  if (state.status.suggestions === 'loading' || state.status.suggestions === 'idle') {
    container.appendChild(h('p', { class: 'muted suggestions__note', role: 'status' }, 'Loading suggestions…'));
    return;
  }
  if (state.status.suggestions === 'error') {
    container.append(
      h('p', { class: 'suggestions__note suggestions__note--error' }, `Couldn't read known_hosts. ${state.errors.suggestions ?? ''}`),
      h('button', { type: 'button', class: 'btn btn--secondary btn--sm', onClick: () => store.refreshSuggestions().catch(() => {}) }, 'Try again'));
    return;
  }
  const all = unsavedSuggestions(state.suggestions);
  const shown = opts.limit ? all.slice(0, opts.limit) : all;
  if (!all.length) {
    container.appendChild(h('p', { class: 'muted suggestions__note' }, 'No new hosts in known_hosts.'));
  } else {
    const list = h('ul', { class: 'suggestions__list' });
    for (const s of shown) {
      const label = suggestionLabel(s);
      list.appendChild(h('li', { class: 'suggestion' },
        h('span', { class: 'suggestion__host mono' }, label),
        opts.compact ? null : h('span', { class: 'suggestion__type' }, s.key_types.map(sshKeyTypeLabel).join(' · ')),
        h('button', {
          type: 'button', class: 'btn btn--secondary btn--sm', 'aria-label': `Add ${label}`,
          onClick: () => addConnection({ host: s.host, port: s.port, name: suggestedName(s) }),
        }, 'Add'),
        h('button', {
          type: 'button', class: 'btn btn--ghost btn--sm', 'aria-label': `Dismiss ${label}`,
          onClick: () => dismiss(s),
        }, 'Dismiss')));
    }
    container.appendChild(list);
  }
  if (opts.limit && all.length > shown.length) {
    container.appendChild(h('button', {
      type: 'button', class: 'link-btn suggestions__all',
      onClick: () => {
        if (opts.onSeeAll) opts.onSeeAll();
        else draw(container, { ...opts, limit: undefined });
      },
    }, `See all ${all.length}`));
  }
  const hashed = state.suggestions?.hashed_count ?? 0;
  if (hashed > 0 && !opts.compact) {
    container.appendChild(h('p', { class: 'muted suggestions__note' },
      `${hashed} hashed ${hashed === 1 ? "entry can't" : "entries can't"} be listed. Their names are hidden by HashKnownHosts.`));
  }
}
