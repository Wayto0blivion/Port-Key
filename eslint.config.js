// ESLint flat config for portkey/static/js (run by the lint-js compose service).
// Self-contained on purpose: no plugin imports, so it loads from the read-only /src mount
// while eslint itself lives in the lint-js image.

const browserGlobals = Object.fromEntries(
  [
    'window', 'document', 'navigator', 'location', 'history', 'console', 'fetch', 'Headers',
    'Request', 'Response', 'URL', 'URLSearchParams', 'WebSocket', 'localStorage',
    'sessionStorage', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval',
    'requestAnimationFrame', 'cancelAnimationFrame', 'queueMicrotask', 'structuredClone',
    'atob', 'btoa', 'TextEncoder', 'TextDecoder', 'AbortController', 'Event', 'CustomEvent',
    'EventTarget', 'KeyboardEvent', 'MouseEvent', 'DragEvent', 'ResizeObserver',
    'MutationObserver', 'IntersectionObserver', 'HTMLElement', 'HTMLInputElement',
    'HTMLButtonElement', 'HTMLSpanElement', 'HTMLDialogElement', 'Element', 'Node',
    'DocumentFragment', 'Blob', 'ClipboardItem', 'getComputedStyle', 'matchMedia',
    'performance', 'crypto',
  ].map((name) => [name, 'readonly']),
);

export default [
  {
    ignores: ['portkey/static/vendor/**', '**/*.d.ts'],
  },
  {
    files: ['portkey/static/js/**/*.js'],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: 'module',
      globals: browserGlobals,
    },
    linterOptions: {
      reportUnusedDisableDirectives: 'error',
    },
    rules: {
      'no-undef': 'error',
      'no-unused-vars': ['error', { args: 'none', caughtErrors: 'none' }],
      'no-var': 'error',
      'prefer-const': 'error',
      eqeqeq: ['error', 'always', { null: 'ignore' }],
      'no-eval': 'error',
      'no-implied-eval': 'error',
      'no-new-func': 'error',
      'no-script-url': 'error',
      'no-unsafe-finally': 'error',
      'no-unreachable': 'error',
      'no-dupe-keys': 'error',
      'no-duplicate-imports': 'error',
      'no-self-compare': 'error',
      'no-template-curly-in-string': 'error',
      'no-restricted-properties': [
        'error',
        { object: 'document', property: 'write', message: 'Not allowed.' },
      ],
      'no-restricted-syntax': [
        'error',
        {
          selector: "AssignmentExpression[left.property.name='innerHTML']",
          message: 'Build DOM with ui/dom.js; never assign innerHTML (server output is hostile).',
        },
        {
          selector: "AssignmentExpression[left.property.name='outerHTML']",
          message: 'Never assign outerHTML.',
        },
      ],
    },
  },
];
