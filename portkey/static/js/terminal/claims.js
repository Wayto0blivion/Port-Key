/*
 * terminal/claims.js — private module of views/sessions.js.
 *
 * Which sessions another tab or window of Port-Key is showing right now. A duplicated browser
 * tab (Duplicate tab, Edge's Ctrl+Shift+K) starts with a copy of this tab's sessionStorage, so
 * on restore it would attach to every remembered session and take them all from the original
 * window. Before attaching a remembered session the server lists as attached, the restoring page
 * asks the others over a BroadcastChannel; a page that holds it answers, and the restoring page
 * shows that tab as "open in another tab" instead of taking it. After a plain reload nobody
 * answers (the old page is gone), so the session is attached as before.
 *
 * Only session ids cross the channel, between pages of the same origin.
 */

const CHANNEL = 'portkey.sessions';
const WAIT_MS = 300;
/** This page, so it never answers its own question. */
const PAGE = `${Date.now()}-${Math.random().toString(36).slice(2)}`;

/** @type {BroadcastChannel|null} */
let channel = null;

/**
 * Answer other pages' questions with the ids this page holds. Safe to call more than once.
 * @param {() => string[]} held session ids this page is showing or attaching
 */
export function answerClaims(held) {
  if (channel || typeof BroadcastChannel === 'undefined') return;
  try {
    channel = new BroadcastChannel(CHANNEL);
  } catch {
    channel = null;
    return;
  }
  channel.addEventListener('message', (e) => {
    const msg = e.data;
    if (!msg || msg.type !== 'who-has' || msg.from === PAGE || !Array.isArray(msg.ids)) return;
    const mine = new Set(held());
    const ids = msg.ids.filter((/** @type {unknown} */ id) => typeof id === 'string' && mine.has(id));
    if (ids.length) channel?.postMessage({ type: 'has', nonce: msg.nonce, ids });
  });
}

/**
 * The ids among `ids` that another page says it holds (waits a moment for answers).
 * @param {string[]} ids @returns {Promise<Set<string>>}
 */
export async function claimedElsewhere(ids) {
  const claimed = new Set();
  if (!ids.length || typeof BroadcastChannel === 'undefined') return claimed;
  /** @type {BroadcastChannel} */
  let ask;
  try {
    ask = new BroadcastChannel(CHANNEL);
  } catch {
    return claimed;
  }
  const nonce = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const wanted = new Set(ids);
  ask.addEventListener('message', (e) => {
    const msg = e.data;
    if (!msg || msg.type !== 'has' || msg.nonce !== nonce || !Array.isArray(msg.ids)) return;
    for (const id of msg.ids) if (wanted.has(id)) claimed.add(id);
  });
  ask.postMessage({ type: 'who-has', from: PAGE, nonce, ids });
  await new Promise((resolve) => setTimeout(resolve, WAIT_MS));
  ask.close();
  return claimed;
}
