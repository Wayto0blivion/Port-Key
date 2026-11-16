/*
 * main.js — owner: package 8a (frontend shell).
 *
 * Boot: ask /api/auth/status, show views/login.js when signed out (or not set up), otherwise
 * load the store, render shell.js and restore sessions (views/sessions.js, package 8b).
 * Exports: boot().
 *
 * Skeleton behaviour: renders a placeholder and never throws, so the page loads cleanly until
 * package 8a replaces this body.
 */

/** @returns {Promise<void>} */
export async function boot() {
  const root = document.getElementById('app');
  if (root) root.textContent = 'Port-Key is being built.';
}

boot();
