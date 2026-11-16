/*
 * tests/frontend/mock/data.js — package 8a, TEST AND DEV ONLY (never served by the app).
 *
 * Fictional fixture data for the mock API in server.js, built per scenario:
 *   normal   7 connections in nested folders, 3 keys, 14 suggestions (the docs/ux-spec hosts)
 *   empty    no connections, a key found          (first run, key found)
 *   nokey    no connections, no key               (first run, no key)
 *   ppk      no connections, only a PuTTY key     (first run, unsupported key)
 *   many     320 connections, 22 folders 3 deep, 30 tags (speed check)
 *   setup    no app password yet                  (Set a password)
 *   signedout  password set, stored token invalid (Sign in)
 *   error    the connections list fails with 500
 *   slow     like normal, every response 1.2 s late (loading states)
 * Loaded as a classic script before server.js; exposes window.__pkMockData(scenario).
 */
(function () {
  'use strict';

  function iso(daysAgo, hh, mm) {
    const d = new Date();
    d.setDate(d.getDate() - daysAgo);
    d.setHours(hh, mm, 0, 0);
    return d.toISOString().replace(/\.\d{3}Z$/, 'Z');
  }

  function uuid(n) {
    const hex = (n * 2654435761 >>> 0).toString(16).padStart(8, '0');
    const tail = String(n).padStart(12, '0');
    return `${hex}-${hex.slice(0, 4)}-4${hex.slice(1, 4)}-8${hex.slice(5, 8)}-${tail}`;
  }

  const FP = {
    ed: 'SHA256:Xk3fP0aB7cD9eF1gH2iJ4kL6mN8oP0qR2sT4uV6r9Qa',
    rsa: 'SHA256:b7Lw2nQ4rT6yU8iO0pA2sD4fG6hJ8kL0zX2cV4bKq0c',
    pk: 'SHA256:9dTgV4hY6jU8kI0oL2pM4nB6vC8xZ0aS2dF4gH6mE1s',
  };

  function key(name, type, bits, fp, source, extra) {
    const dir = source === 'generated' ? 'C:\\Users\\john\\.ssh\\port-key' : 'C:\\Users\\john\\.ssh';
    const alg = type === 'rsa' ? 'ssh-rsa' : 'ssh-ed25519';
    return Object.assign({
      id: fp.slice(7).replace(/\+/g, '-').replace(/\//g, '_'), fp, name, label: null, type, bits,
      comment: 'john@WORKPC', path: `${dir}\\${name}`, source, encrypted: false, unlocked: false,
      public_key: `${alg} AAAAC3NzaC1lZDI1NTE5AAAAIFakeFakeFakeForTestsOnly${name.length} john@WORKPC`,
      is_default: false, hidden: false, used_by: [],
    }, extra || {});
  }

  function conn(n, name, user, host, port, color, group, tags, extra) {
    return Object.assign({
      id: uuid(n), name, host, port, username: user, auth_method: 'key', key_fp: null, jump_id: null,
      group_id: group, tags: tags.slice().sort(), color, pinned: false, notes: '', sort_order: 0,
      created_at: iso(40, 9, 15), updated_at: iso(3, 10, 0), last_used_at: null, use_count: 0,
    }, extra || {});
  }

  function group(n, name, parent) {
    return { id: uuid(1000 + n), name, parent_id: parent, sort_order: 0, connection_count: 0 };
  }

  function normal() {
    const office = group(1, 'Office LAN', null);
    const servers = group(2, 'Servers', office.id);
    const appl = group(3, 'Appliances', null);
    const vpn = group(4, 'VPN', null);
    const buildBox = conn(2, 'build-box', 'john', '192.168.10.20', 22, 'blue', servers.id, ['docker', 'linux'],
      { pinned: true, notes: 'CI builds', last_used_at: iso(1, 17, 40), use_count: 30 });
    const connections = [
      conn(1, 'web-prod', 'john', '192.168.10.42', 22, 'red', servers.id, ['docker', 'linux', 'prod'],
        { pinned: true, notes: 'Production. Red on purpose.', last_used_at: iso(0, 14, 2), use_count: 41 }),
      buildBox,
      conn(3, 'backup-nas', 'admin', '192.168.10.60', 22, 'purple', servers.id, ['backup', 'linux'],
        { last_used_at: iso(14, 11, 5), use_count: 4 }),
      conn(4, 'print-srv', 'root', '192.168.10.15', 22, 'grey', office.id, ['legacy'],
        { auth_method: 'password', notes: 'No key yet', last_used_at: iso(26, 9, 30), use_count: 2 }),
      conn(5, 'imaging-01', 'admin', '192.168.10.31', 2222, 'amber', appl.id, ['appliance'],
        { key_fp: FP.pk, last_used_at: iso(0, 9, 12), use_count: 12 }),
      conn(6, 'parser-vm', 'root', '192.168.10.27', 22, 'amber', appl.id, ['appliance', 'linux'],
        { jump_id: buildBox.id, last_used_at: iso(7, 15, 20), use_count: 7 }),
      conn(7, 'vpn-gw', 'john', '10.8.0.1', 22, 'green', vpn.id, ['network'],
        { notes: 'Needs VPN up', last_used_at: iso(10, 8, 0), use_count: 9 }),
    ];
    const hosts = [
      ['lab-01', '192.168.10.71', 22, 'ssh-ed25519'], ['192.168.10.88', null, 2200, 'ssh-rsa'],
      ['helpdesk.office.lan', '192.168.10.12', 22, 'ssh-ed25519'], ['nas-02', '192.168.10.61', 22, 'ssh-ed25519'],
      ['switch-core', '192.168.10.2', 22, 'ssh-rsa'], ['ups-mgmt', '192.168.10.5', 22, 'ssh-rsa'],
      ['test-vm', '192.168.10.80', 22, 'ssh-ed25519'], ['192.168.10.90', null, 22, 'ecdsa-sha2-nistp256'],
      ['camera-nvr', '192.168.10.95', 22, 'ssh-rsa'], ['wiki', '192.168.10.33', 22, 'ssh-ed25519'],
      ['git-mirror', '192.168.10.34', 22, 'ssh-ed25519'], ['dev-db', '192.168.10.50', 22, 'ssh-ed25519'],
      ['old-web', '192.168.10.41', 22, 'ssh-rsa'], ['monitor', '192.168.10.36', 22, 'ssh-ed25519'],
    ];
    const suggestions = hosts.map(([h, alias, port, kt]) => ({ host: h, port, aliases: alias ? [alias] : [], key_types: [kt] }));
    // Already saved (web-prod's address): the mock server leaves it out, as the real API does.
    suggestions.push({ host: 'web-prod', port: 22, aliases: ['192.168.10.42'], key_types: ['ssh-ed25519'] });
    return {
      groups: [office, servers, appl, vpn],
      connections,
      keys: [
        key('id_ed25519', 'ed25519', 256, FP.ed, 'ssh_dir', { is_default: true }),
        key('id_rsa', 'rsa', 4096, FP.rsa, 'ssh_dir'),
        key('id_ed25519_portkey', 'ed25519', 256, FP.pk, 'generated', { encrypted: true, unlocked: true, comment: 'john@WORKPC port-key 2026-11-14' }),
      ],
      problems: [{ path: 'C:\\Users\\john\\.ssh\\work.ppk', reason: 'ppk', detail: 'PuTTY format, not supported' }],
      defaultFp: FP.ed,
      suggestions,
      hashed: 6,
      hostKeys: [
        { id: 7, host: '192.168.10.42', port: 22, key_type: 'ssh-ed25519', fp_sha256: 'SHA256:pZ8mQ1cWv3Lx5Ny7Oz9Ab1Cd3Ef5Gh7Ij9Kl1MnTw4E', public_key: 'ssh-ed25519 AAAA', first_seen_at: '2026-03-02T10:00:00Z', last_seen_at: iso(0, 14, 2), via_id: null, via_name: null, distrusted: false },
        { id: 8, host: '192.168.10.31', port: 2222, key_type: 'ssh-rsa', fp_sha256: 'SHA256:Rb4tY8uI2oP6aS0dF4gH8jK2lZ6xC0vB4nM8qW2eR6t', public_key: 'ssh-rsa AAAA', first_seen_at: '2026-06-11T08:30:00Z', last_seen_at: iso(0, 9, 12), via_id: null, via_name: null, distrusted: false },
        { id: 9, host: '10.8.0.1', port: 22, key_type: 'ssh-ed25519', fp_sha256: 'SHA256:Qw2eR4tY6uI8oP0aS2dF4gH6jK8lZ0xC2vB4nM6qW8e', public_key: 'ssh-ed25519 AAAA', first_seen_at: '2026-09-20T12:00:00Z', last_seen_at: iso(10, 8, 0), via_id: null, via_name: null, distrusted: false },
        { id: 10, host: '192.168.10.27', port: 22, key_type: 'ssh-ed25519', fp_sha256: 'SHA256:Lm3nB5vC7xZ9aS1dF3gH5jK7lQ9wE1rT3yU5iO7pA2s', public_key: 'ssh-ed25519 AAAA', first_seen_at: '2026-10-01T09:00:00Z', last_seen_at: iso(7, 15, 20), via_id: buildBox.id, via_name: 'build-box', distrusted: false },
        { id: 11, host: 'switch-core', port: 22, key_type: 'ssh-rsa', fp_sha256: 'SHA256:Zx8cV6bN4mQ2wE0rT8yU6iO4pA2sD0fG8hJ6kL4zX2c', public_key: 'ssh-rsa AAAA', first_seen_at: '2026-10-05T09:00:00Z', last_seen_at: '2026-10-05T09:00:00Z', via_id: null, via_name: null, distrusted: true },
      ],
    };
  }

  function many() {
    const tagPool = ['prod', 'web', 'dell', 'hp', 'linux', 'docker', 'db', 'backup', 'printer', 'camera', 'vpn', 'lab',
      'windows', 'switch', 'ups', 'nas', 'k8s', 'dev', 'staging', 'legacy', 'monitor', 'mail', 'dns', 'dhcp', 'proxy',
      'git', 'ci', 'storage', 'edge', 'iot'];
    const colors = ['red', 'amber', 'green', 'blue', 'purple', 'grey'];
    const groups = [];
    let g = 0;
    for (const top of ['Office LAN', 'Branch office', 'Data centre']) {
      const t = group(++g, top, null);
      groups.push(t);
      for (const mid of ['Servers', 'Appliances', 'Network']) {
        const m = group(++g, mid, t.id);
        groups.push(m);
        if (mid !== 'Network') groups.push(group(++g, mid === 'Servers' ? 'Linux' : 'Printers', m.id));
      }
    }
    const connections = [];
    for (let i = 1; i <= 320; i += 1) {
      const tags = [tagPool[i % 30], tagPool[(i * 7) % 30]].filter((t, j, a) => a.indexOf(t) === j);
      const gid = i % 9 === 0 ? null : groups[i % groups.length].id;
      connections.push(conn(100 + i, `srv-${String(i).padStart(3, '0')}`, i % 5 ? 'john' : 'admin',
        `192.168.${10 + (i % 5)}.${(i * 3) % 250 + 2}`, i % 11 ? 22 : 2222, colors[i % 6], gid, tags,
        { pinned: i % 53 === 0, last_used_at: i % 4 ? iso(i % 30, 8 + (i % 10), i % 60) : null,
          auth_method: i % 13 ? 'key' : 'password', notes: i % 6 ? '' : 'Rack B, shelf 2' }));
    }
    const base = normal();
    return Object.assign(base, { groups, connections });
  }

  window.__pkMockData = function (scenario) {
    let d = scenario === 'many' ? many() : normal();
    if (scenario === 'empty' || scenario === 'nokey' || scenario === 'ppk') {
      d = Object.assign(d, { connections: [], groups: [] });
      d.suggestions.pop();
    }
    if (scenario === 'nokey') Object.assign(d, { keys: [], problems: [], defaultFp: null });
    if (scenario === 'ppk') Object.assign(d, { keys: [], defaultFp: null });
    return d;
  };
})();
