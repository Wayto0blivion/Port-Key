/*
 * protocol.js — owner: package 8b.
 *
 * WebSocket framing for /ws/terminal and /ws/install (docs/ws-protocol.md): connect to
 * ws://<same host>/ws/..., send {type:"auth", token} first, dispatch server messages by type,
 * answer prompts with prompt_reply, ping every 25 s and treat 2 missed pongs as a dead link.
 * Binary frames are terminal bytes in both directions (64 KB cap per input frame).
 */

/** @typedef {import('../types.js').Prompt} Prompt */
/** @typedef {import('../types.js').PromptReply} PromptReply */
/** @typedef {import('../types.js').WsError} WsError */

const todo = () => {
  throw new Error('not implemented (package 8b)');
};

/** Close codes (frozen, docs/ws-protocol.md). */
export const CLOSE = Object.freeze({
  NORMAL: 1000,
  TOO_BIG: 1009,
  INTERNAL: 1011,
  BAD_MESSAGE: 4400,
  AUTH: 4401,
  ORIGIN: 4403,
  UNKNOWN_CONNECTION: 4404,
  PROMPT_TIMEOUT: 4408,
  HOSTKEY_CHANGED: 4409,
  SESSION_GONE: 4410,
  TOO_MANY_SESSIONS: 4429,
  SSH_FAILURE: 4502,
});

/**
 * @typedef {object} SocketHandlers
 * @property {(msg: Record<string, unknown>) => void} [onMessage]  every JSON message
 * @property {(prompt: Prompt) => Promise<PromptReply>} [onPrompt]
 * @property {(data: Uint8Array) => void} [onOutput]
 * @property {(err: WsError) => void} [onError]
 * @property {(code: number, reason: string) => void} [onClose]
 */

export class PortKeySocket {
  /** @param {'/ws/terminal'|'/ws/install'} path @param {SocketHandlers} handlers */
  constructor(path, handlers) {
    this.path = path;
    this.handlers = handlers;
  }

  /** Open and authenticate. @returns {Promise<void>} */
  async connect() { return todo(); }

  /** @param {Record<string, unknown>} msg */
  send(msg) { todo(); }

  /** @param {Uint8Array|string} data terminal input */
  sendInput(data) { todo(); }

  close() { todo(); }
}
