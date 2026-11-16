/*
 * parts/folders.js — owner: package 8a (private module; pure, no DOM).
 *
 * Folder-tree helpers over the flat GET /api/groups list: children in display order (sort_order,
 * then name), depth (1 = top level), full paths ("Office LAN / Servers"), descendants, and the
 * depth-first option list used by the folder picker and the sidebar. Folders nest at most
 * MAX_DEPTH levels.
 */

/** @typedef {import('../types.js').Group} Group */

export const MAX_DEPTH = 3;

/**
 * @typedef {object} FolderIndex
 * @property {Map<string, Group>} byId
 * @property {(parentId: string|null) => Group[]} children
 * @property {(id: string|null) => string} path      "" for null / unknown
 * @property {(id: string|null) => number} depth     0 for null; 1 for a top-level folder
 * @property {(id: string) => Set<string>} descendants  not including id
 * @property {(id: string) => number} height         levels below and including id (leaf = 1)
 * @property {{group: Group, depth: number, path: string}[]} ordered  depth-first
 */

/** @param {Group[]} groups @returns {FolderIndex} */
export function buildFolderIndex(groups) {
  const byId = new Map(groups.map((g) => [g.id, g]));
  /** @type {Map<string|null, Group[]>} */
  const kids = new Map();
  for (const g of groups) {
    const parent = g.parent_id && byId.has(g.parent_id) ? g.parent_id : null;
    if (!kids.has(parent)) kids.set(parent, []);
    kids.get(parent)?.push(g);
  }
  for (const list of kids.values()) {
    list.sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
  }
  /** @type {Map<string, string>} */
  const paths = new Map();
  /** @type {Map<string, number>} */
  const depths = new Map();
  /** @type {{group: Group, depth: number, path: string}[]} */
  const ordered = [];
  const walk = (/** @type {string|null} */ parent, /** @type {number} */ depth, /** @type {string} */ prefix) => {
    for (const g of kids.get(parent) ?? []) {
      if (depths.has(g.id)) continue; // cycle guard against bad data
      const path = prefix ? `${prefix} / ${g.name}` : g.name;
      paths.set(g.id, path);
      depths.set(g.id, depth);
      ordered.push({ group: g, depth, path });
      walk(g.id, depth + 1, path);
    }
  };
  walk(null, 1, '');
  const descendants = (/** @type {string} */ id) => {
    /** @type {Set<string>} */
    const out = new Set();
    const stack = [...(kids.get(id) ?? [])];
    while (stack.length) {
      const g = /** @type {Group} */ (stack.pop());
      if (out.has(g.id)) continue;
      out.add(g.id);
      stack.push(...(kids.get(g.id) ?? []));
    }
    return out;
  };
  const height = (/** @type {string} */ id) => {
    let max = 0;
    for (const d of descendants(id)) max = Math.max(max, (depths.get(d) ?? 0) - (depths.get(id) ?? 0));
    return max + 1;
  };
  return {
    byId,
    children: (parentId) => kids.get(parentId) ?? [],
    path: (id) => (id ? paths.get(id) ?? '' : ''),
    depth: (id) => (id ? depths.get(id) ?? 0 : 0),
    descendants,
    height,
    ordered,
  };
}
