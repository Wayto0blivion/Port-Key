// globals.d.ts — owner: package 0. Types for the UMD xterm builds that index.html loads as
// classic scripts (window.Terminal, window.FitAddon.FitAddon, window.SearchAddon.SearchAddon).
// Used only by `tsc --checkJs`; never served as code.

import type { Terminal as XTerminal } from '@xterm/xterm';
import type { FitAddon as XFitAddon } from '@xterm/addon-fit';
import type { SearchAddon as XSearchAddon } from '@xterm/addon-search';

declare global {
  interface Window {
    Terminal: typeof XTerminal;
    FitAddon: { FitAddon: typeof XFitAddon };
    SearchAddon: { SearchAddon: typeof XSearchAddon };
  }
}

export {};
