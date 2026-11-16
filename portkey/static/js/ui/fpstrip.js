/*
 * fpstrip.js — owner: package 0 (complete, shared by 8a and 8b).
 *
 * Fingerprint strips: a deterministic 8-segment colour strip drawn from the first 8 bytes of a
 * SHA256 fingerprint, so a changed host key looks different at a glance (docs/ux-spec.md 5).
 * Colours are the --fp-0 .. --fp-7 tokens in css/base.css. Styles are set through the CSSOM
 * (element.style), which the CSP allows.
 *
 * Exports: fpBytes(fp), stripColors(fp), stripGradient(fp), renderStrip(fp, opts), shortFp(fp)
 */

export const STRIP_SEGMENTS = 8;

/**
 * Decode "SHA256:<base64 without padding>" to bytes. Returns an empty array when malformed.
 * @param {string} fp
 * @returns {Uint8Array}
 */
export function fpBytes(fp) {
  const b64 = fp.replace(/^SHA256:/, '');
  if (!/^[A-Za-z0-9+/]+$/.test(b64)) return new Uint8Array(0);
  const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4);
  try {
    const bin = atob(padded);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i += 1) out[i] = bin.charCodeAt(i);
    return out;
  } catch {
    return new Uint8Array(0);
  }
}

/**
 * CSS colour values (custom-property references) for each segment.
 * @param {string} fp
 * @returns {string[]}
 */
export function stripColors(fp) {
  const bytes = fpBytes(fp);
  const colors = [];
  for (let i = 0; i < STRIP_SEGMENTS; i += 1) {
    const index = bytes.length > i ? bytes[i] % STRIP_SEGMENTS : 0;
    colors.push(`var(--fp-${index})`);
  }
  return colors;
}

/**
 * A hard-stop linear-gradient for the strip.
 * @param {string} fp
 * @returns {string}
 */
export function stripGradient(fp) {
  const step = 100 / STRIP_SEGMENTS;
  const stops = stripColors(fp).map(
    (color, i) => `${color} ${(i * step).toFixed(1)}% ${((i + 1) * step).toFixed(1)}%`,
  );
  return `linear-gradient(90deg, ${stops.join(', ')})`;
}

/**
 * A strip element (64 x 10 px by default) labelled for screen readers.
 * @param {string} fp
 * @param {{width?: number, height?: number}} [opts]
 * @returns {HTMLSpanElement}
 */
export function renderStrip(fp, opts = {}) {
  const el = document.createElement('span');
  el.className = 'fp-strip';
  el.setAttribute('role', 'img');
  el.setAttribute('aria-label', `Colour strip for ${shortFp(fp)}`);
  el.style.display = 'inline-block';
  el.style.flex = 'none';
  el.style.width = `${opts.width ?? 64}px`;
  el.style.height = `${opts.height ?? 10}px`;
  el.style.borderRadius = '2px';
  el.style.background = stripGradient(fp);
  return el;
}

/**
 * "SHA256:Xk3fP0…r9Qa" — prefix, first 6 and last 4 characters.
 * @param {string} fp
 * @returns {string}
 */
export function shortFp(fp) {
  const body = fp.replace(/^SHA256:/, '');
  if (body.length <= 12) return fp;
  return `SHA256:${body.slice(0, 6)}…${body.slice(-4)}`;
}
