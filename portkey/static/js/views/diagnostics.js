/*
 * diagnostics.js — owner: package 8a.
 *
 * The Diagnostics tab: mounts, writability, unreadable keys with reasons, the
 * ssh-agent note, "Check a host" (resolve + port check) and the version.
 *
 * Every yes/no is written out with a ✓ or ✕ as well as colour. The report reloads with
 * [Check again]; "Check a host" keeps its own result area so a reload does not wipe it.
 */

import * as api from '../api.js';
import { h, clear } from '../ui/dom.js';
import { field, setFieldError } from '../parts/fields.js';

/** @typedef {import('../types.js').DiagReport} DiagReport */
/** @typedef {import('../types.js').ResolveResult} ResolveResult */

const REASONS = {
  ppk: 'PuTTY format, not supported', not_private_key: 'Not a private key', unreadable: 'Unreadable',
  unsupported_type: 'Key type not supported',
};
const AGENT_NOTE = "If your key lives only in the Windows ssh-agent, Port-Key can't reach it. Generate a key here and install it with the wizard.";

/** @param {HTMLElement} container @returns {void} */
export function renderDiagnostics(container) {
  container.classList.add('page', 'diagnostics');
  clear(container);
  const report = h('div', { class: 'diag__report' });
  const reload = h('button', { type: 'button', class: 'btn btn--secondary', onClick: () => load() }, 'Check again');
  container.append(
    h('div', { class: 'page__head' },
      h('div', { class: 'page__titles' }, h('h1', { class: 'page__title' }, 'Diagnostics'),
        h('p', { class: 'muted' }, 'What Port-Key can see from inside its container.')),
      reload),
    report,
    checkHost());
  const load = async () => {
    clear(report);
    report.appendChild(h('p', { class: 'muted', role: 'status' }, 'Checking…'));
    try {
      drawReport(report, await api.getDiagnostics());
    } catch (err) {
      clear(report);
      report.appendChild(h('p', { class: 'error-text', role: 'alert' },
        `Couldn't run diagnostics. ${err instanceof Error ? err.message : ''}`));
    }
  };
  load();
}

/** @param {boolean} ok @param {string} yes @param {string} no */
function yesNo(ok, yes, no) {
  return h('span', { class: `state ${ok ? 'state--ok' : 'state--bad'}` }, ok ? '✓ ' : '✕ ', ok ? yes : no);
}

/** @param {HTMLElement} el @param {DiagReport} d */
function drawReport(el, d) {
  clear(el);
  /** @param {string} label @param {...(Node|string)} value */
  const row = (label, ...value) => h('div', { class: 'about__row' }, h('dt', {}, label), h('dd', {}, ...value));
  const ssh = d.ssh_dir;
  el.appendChild(h('section', { class: 'panel', 'aria-labelledby': 'dg-mounts' },
    h('h2', { id: 'dg-mounts', class: 'section-title' }, 'Folders'),
    h('dl', { class: 'about' },
      row('.ssh folder', h('span', { class: 'mono' }, ssh.path), ' ', yesNo(ssh.present && ssh.readable, 'found', ssh.present ? 'not readable' : 'not found')),
      row('Keys found', String(ssh.key_count)),
      row('known_hosts', yesNo(ssh.known_hosts, 'found', 'not found')),
      row('Generated keys', h('span', { class: 'mono' }, d.keys_dir.path), ' ',
        yesNo(d.keys_dir.present && d.keys_dir.writable, 'writable', d.keys_dir.present ? 'not writable' : 'not found')),
      row('Data', h('span', { class: 'mono' }, d.data_dir.path), ' ', yesNo(d.data_dir.writable, 'writable', 'not writable'))),
    ssh.mode_note ? h('p', { class: 'muted' }, ssh.mode_note) : null));

  const notes = [...d.notes];
  if (ssh.key_count === 0 && !notes.some((n) => n.includes('ssh-agent'))) notes.push(AGENT_NOTE);
  if (d.key_problems.length || notes.length) {
    el.appendChild(h('section', { class: 'panel', 'aria-labelledby': 'dg-keys' },
      h('h2', { id: 'dg-keys', class: 'section-title' }, 'Keys that could not be used'),
      d.key_problems.length
        ? h('ul', { class: 'plain-list' }, ...d.key_problems.map((p) => h('li', {},
          h('span', { class: 'mono' }, p.path), ` — ${REASONS[p.reason] ?? p.reason}`,
          p.detail && p.detail !== REASONS[p.reason] ? h('span', { class: 'muted' }, ` (${p.detail})`) : null)))
        : h('p', { class: 'muted' }, 'None.'),
      ...notes.map((n) => h('p', { class: 'notice' }, n))));
  }
  el.appendChild(h('p', { class: 'muted' }, `Port-Key ${d.version} · Python ${d.python} · asyncssh ${d.asyncssh}`));
}

function checkHost() {
  const host = /** @type {HTMLInputElement} */ (h('input', { id: 'dg-host', class: 'input mono', placeholder: 'build-box', autocomplete: 'off', spellcheck: 'false' }));
  const port = /** @type {HTMLInputElement} */ (h('input', { id: 'dg-port', class: 'input mono', value: '22', inputmode: 'numeric' }));
  const result = h('div', { class: 'diag__result', 'aria-live': 'polite' });
  const button = /** @type {HTMLButtonElement} */ (h('button', { type: 'submit', class: 'btn btn--primary' }, 'Check'));
  const form = h('form', { class: 'form', novalidate: true },
    h('div', { class: 'form__row form__row--end' },
      field({ id: 'dg-host', label: 'Host name or address', control: host, className: 'field--grow' }),
      field({ id: 'dg-port', label: 'Port', control: port, className: 'field--port' }),
      button),
    result);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    setFieldError(host, null);
    setFieldError(port, null);
    const name = host.value.trim();
    const p = Number(port.value.trim() || '22');
    if (!name || /[\s@/]/.test(name)) {
      setFieldError(host, 'Enter a host name or IP address without spaces, @ or /.');
      host.focus();
      return;
    }
    if (!Number.isInteger(p) || p < 1 || p > 65535) {
      setFieldError(port, 'Use a port from 1 to 65535.');
      port.focus();
      return;
    }
    button.disabled = true;
    clear(result);
    result.appendChild(h('p', { class: 'muted' }, `Checking ${name}…`));
    try {
      drawResolve(result, await api.resolveHost(name, p));
    } catch (err) {
      clear(result);
      result.appendChild(h('p', { class: 'error-text' }, err instanceof Error ? err.message : String(err)));
    } finally {
      button.disabled = false;
    }
  });
  return h('section', { class: 'panel', 'aria-labelledby': 'dg-check' },
    h('h2', { id: 'dg-check', class: 'section-title' }, 'Check a host'),
    h('p', { class: 'muted' }, 'Port-Key looks the name up from inside its container and tries the port. Names that exist only in the Windows hosts file may not resolve here.'),
    form);
}

/** @param {HTMLElement} el @param {ResolveResult} r */
function drawResolve(el, r) {
  clear(el);
  if (r.resolve_error || !r.addresses.length) {
    el.appendChild(h('p', {}, yesNo(false, '', `Couldn't resolve ${r.host}`), `: ${r.resolve_error ?? 'no addresses'}.`));
    return;
  }
  el.appendChild(h('p', {}, yesNo(true, `${r.host} resolves to `, ''), h('span', { class: 'mono' }, r.addresses.join(', ')), '.'));
  const t = r.tcp;
  if (t.state === 'up') el.appendChild(h('p', {}, yesNo(true, `Port ${r.port} answered in ${t.ms ?? 0} ms.`, '')));
  else if (t.state === 'down') {
    const why = t.error === 'refused' ? 'the connection was refused' : t.error === 'timeout' ? 'it timed out' : 'the host is unreachable';
    el.appendChild(h('p', {}, yesNo(false, '', `Port ${r.port} did not answer: ${why}.`)));
  } else el.appendChild(h('p', { class: 'muted' }, 'The port was not checked.'));
}
