/*
 * wizard/hostkey.js — owner: package 8b (private module of ui/dialogs.js and views/wizard.js).
 *
 * The host key bodies from docs/ux-spec.md section 4, built as DOM so both the connection-time
 * dialogs and the wizard's step 2 show the same thing:
 * - firstSeenBody: "This is the first time Port-Key has connected to web-prod (192.168.10.42).
 *   It presented this key:", the full fingerprint with its colour strip, the ssh-keygen command
 *   to compare with, and "Nothing has been sent to the host yet." A jump host's body starts
 *   "web-prod is reached through build-box (192.168.10.20)."
 * - changedBody: the explanation, the bold "sent no password and no key" line, old and new
 *   fingerprints side by side with strips, and the typed-name field.
 */

import { h, formatDate, sshKeyTypeLabel } from '../ui/dom.js';
import { renderStrip, shortFp } from '../ui/fpstrip.js';

/** @typedef {import('../types.js').Prompt} Prompt */
/** @typedef {import('../types.js').Hop} Hop */

/** "192.168.10.42", or "192.168.10.31:2222" off port 22. @param {string} host @param {number} port */
export function addr(host, port) {
  if (port === 22) return host;
  return host.includes(':') ? `[${host}]:${port}` : `${host}:${port}`;
}

/** The command that prints the server's own fingerprint for this key type. @param {string} keyType */
export function keygenCommand(keyType) {
  let file = 'ed25519';
  if (/rsa/.test(keyType)) file = 'rsa';
  else if (/ecdsa/.test(keyType)) file = 'ecdsa';
  else if (/dss/.test(keyType)) file = 'dsa';
  return `ssh-keygen -lf /etc/ssh/ssh_host_${file}_key.pub`;
}

/** @param {Prompt} p @returns {string} */
export function firstSeenTitle(p) {
  return p.hop.role === 'jump' ? "Check the jump host's identity" : "Check this host's identity";
}

/**
 * "Jump host 1 of 2: build-box" for prompts in a chain; null for a direct connection.
 * @param {Hop} hop @returns {HTMLElement|null}
 */
export function hopLabel(hop) {
  if (!hop || hop.count < 2) return null;
  const what = hop.role === 'jump' ? `Jump host ${hop.index + 1} of ${hop.count - 1}` : 'Target';
  return h('p', { class: 'prompt__hop' }, `${what}: `, h('strong', {}, hop.name), h('span', { class: 'mono muted' }, ` ${addr(hop.host, hop.port)}`));
}

/** @param {string} fp @param {string} keyType @param {boolean} full */
function fingerprint(fp, keyType, full) {
  const strip = renderStrip(fp, { height: 14 });
  strip.style.width = '100%';
  return h('div', { class: 'fpbox' },
    strip,
    h('span', { class: 'fpbox__text mono' }, `${sshKeyTypeLabel(keyType)} ${full ? fp : shortFp(fp)}`));
}

/** @param {Prompt} p @param {string} targetName the connection being opened (for a jump's body) */
export function firstSeenBody(p, targetName) {
  const host = p.host ?? p.hop.host;
  const port = p.port ?? p.hop.port;
  const keyType = p.key_type ?? '';
  const intro = p.hop.role === 'jump'
    ? [`${targetName} is reached through ${p.hop.name} (${addr(host, port)}). This is the first time Port-Key has connected to `,
      h('strong', {}, p.hop.name), '. It presented this key:']
    : ['This is the first time Port-Key has connected to ', h('strong', {}, p.hop.name), ` (${addr(host, port)}). It presented this key:`];
  return h('div', { class: 'prompt prompt--hostkey' },
    hopLabel(p.hop),
    h('p', { class: 'prompt__lead' }, ...intro),
    fingerprint(p.fingerprint ?? '', keyType, true),
    h('div', { class: 'prompt__compare' },
      h('span', { class: 'muted' }, 'To be sure, run this on the server and compare:'),
      h('code', { class: 'cmdbox' }, keygenCommand(keyType))),
    h('p', { class: 'muted prompt__note' },
      'Port-Key remembers this key and stops you if the host ever presents a different one. Nothing has been sent to the host yet.'));
}

/** @param {Prompt} p */
export function changedTitle(p) {
  return `${p.hop.name}'s host key has changed`;
}

/**
 * @param {Prompt} p @param {HTMLInputElement} input the typed-name field (id set by the caller)
 */
export function changedBody(p, input) {
  const host = p.host ?? p.hop.host;
  const port = p.port ?? p.hop.port;
  const when = p.first_seen_at ? formatDate(p.first_seen_at) : '';
  const trusted = when ? `the one you trusted on ${when}` : 'the one in your known_hosts file';
  const name = p.confirm_name ?? p.hop.name;
  input.placeholder = name;
  return h('div', { class: 'prompt prompt--changed' },
    hopLabel(p.hop),
    h('p', { class: 'prompt__lead' },
      `The key ${p.hop.name} (${addr(host, port)}) presented doesn't match ${trusted}. This is expected after a reinstall or when an IP address is reused. It is also what an intercepted connection looks like.`),
    h('p', { class: 'prompt__strong' }, 'Port-Key has sent no password and no key. The connection is closed.'),
    h('div', { class: 'fpcompare' },
      h('div', { class: 'fpcard' },
        h('span', { class: 'fpcard__label' }, when ? `Trusted since ${when}` : 'In your known_hosts file'),
        fingerprint(p.old_fingerprint ?? '', p.old_key_type ?? p.key_type ?? '', false)),
      h('div', { class: 'fpcard fpcard--new' },
        h('span', { class: 'fpcard__label' }, 'Presented just now'),
        fingerprint(p.new_fingerprint ?? '', p.key_type ?? '', false))),
    when ? null : knownHostsNote(host, port),
    h('div', { class: 'field' },
      h('label', { class: 'prompt__confirm-label', for: input.id },
        'If you know why it changed, type ', h('strong', { class: 'mono' }, name), ' to replace the stored key.'),
      input));
}

/**
 * Replacing a key that came from known_hosts: Port-Key distrusts the old line itself, but never
 * edits the file, so Windows ssh would still trust it.
 * @param {string} host @param {number} port
 */
function knownHostsNote(host, port) {
  const target = port === 22 ? host : `"[${host}]:${port}"`;
  return h('p', { class: 'muted prompt__note' },
    'Replacing it makes Port-Key stop trusting the old key. Your known_hosts file is not changed; to stop Windows ssh trusting the old key too, run ',
    h('code', { class: 'mono' }, `ssh-keygen -R ${target}`), '.');
}

/** The typed name matches: trimmed, ignoring case (docs/ws-protocol.md decision 8). @param {string} typed @param {string} expected */
export function nameMatches(typed, expected) {
  return typed.trim().toLowerCase() === expected.trim().toLowerCase() && expected.trim() !== '';
}
