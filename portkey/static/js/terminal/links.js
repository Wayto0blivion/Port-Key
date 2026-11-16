/*
 * terminal/links.js — private module of terminal/session.js.
 *
 * Hyperlinks a server prints (OSC 8, which xterm.js 5.5 shows on its own) are server output,
 * so they are hostile by default: the visible text can name one site while the link goes to
 * another. xterm's own handler opens them on a plain click after a generic confirm(). Here:
 * - a plain click does nothing; Ctrl+click (Cmd+click on a Mac) asks first;
 * - only http and https links are offered (xterm drops other schemes before this runs);
 * - Port-Key's own dialog names the session and shows the full address and its host;
 * - the page opens with noopener and noreferrer, so it cannot reach back into Port-Key.
 */

import { h } from '../ui/dom.js';
import { confirmDialog } from '../ui/modal.js';

/**
 * The xterm `linkHandler` option. @param {() => string} name the session's connection name
 * @returns {import('@xterm/xterm').ILinkHandler}
 */
export function linkHandler(name) {
  return {
    allowNonHttpProtocols: false,
    activate: (event, uri) => {
      if (!(event.ctrlKey || event.metaKey)) return;
      openLink(uri, name()).catch(() => {});
    },
  };
}

/** @param {string} uri @returns {URL|null} an http(s) URL, or null */
export function safeUrl(uri) {
  try {
    const url = new URL(uri);
    return url.protocol === 'http:' || url.protocol === 'https:' ? url : null;
  } catch {
    return null;
  }
}

/** @param {string} uri @param {string} name */
async function openLink(uri, name) {
  const url = safeUrl(uri);
  if (!url) return;
  const ok = await confirmDialog({
    title: `Open a link from ${name}?`,
    message: h('div', {},
      h('p', { class: 'modal__text' }, `The server printed a link to `, h('strong', { class: 'mono' }, url.host), '. Its text in the terminal can differ from where it goes:'),
      h('p', { class: 'mono modal__text' }, url.href),
      h('p', { class: 'muted' }, 'Open it only if you expected it. Port-Key never asks for your password on another page.')),
    confirmLabel: 'Open link',
    danger: true,
  });
  if (ok) window.open(url.href, '_blank', 'noopener,noreferrer');
}
