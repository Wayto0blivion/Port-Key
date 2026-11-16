// TEST-ONLY cross-origin attack for the Port-Key e2e suite (package 7). See index.html.
(function () {
  'use strict';
  var params = new URLSearchParams(window.location.search);
  var wsUrl = params.get('ws') || 'ws://localhost:8822/ws/terminal';
  var apiUrl = params.get('api') || 'http://localhost:8822';
  var token = params.get('token') || 'stolen-token';
  var timeoutMs = Number(params.get('timeout') || 5000);

  var result = {
    origin: window.location.origin,
    ws: { url: wsUrl, opened: false, error: false, closeCode: null, messages: [], done: false },
    fetch: { url: apiUrl + '/api/connections', blocked: null, status: null, done: false },
    done: false
  };
  window.__evilResult = result;

  function render() {
    result.done = result.ws.done && result.fetch.done;
    var el = document.getElementById('result');
    el.textContent = JSON.stringify(result, null, 2);
    el.setAttribute('data-done', String(result.done));
  }

  // 1. Cross-site WebSocket hijacking: the server must refuse the handshake (HTTP 403).
  var ws;
  try {
    ws = new WebSocket(wsUrl);
  } catch (e) {
    result.ws.error = true;
    result.ws.done = true;
  }
  if (ws) {
    ws.onopen = function () {
      result.ws.opened = true;
      ws.send(JSON.stringify({ type: 'auth', token: token }));
      ws.send(JSON.stringify({ type: 'open', connection_id: '00000000-0000-4000-8000-000000000000', cols: 80, rows: 24 }));
    };
    ws.onmessage = function (event) {
      result.ws.messages.push(String(event.data).slice(0, 200));
    };
    ws.onerror = function () {
      result.ws.error = true;
    };
    ws.onclose = function (event) {
      result.ws.closeCode = event.code;
      result.ws.done = true;
      render();
    };
  }

  // 2. A mutating request from another origin (CORS preflight must not be granted).
  fetch(result.fetch.url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Port-Key': '1', 'Authorization': 'Bearer ' + token },
    body: JSON.stringify({ name: 'evil', host: '192.168.10.42', username: 'john' })
  }).then(function (response) {
    result.fetch.blocked = false;
    result.fetch.status = response.status;
  }, function () {
    result.fetch.blocked = true;
  }).then(function () {
    result.fetch.done = true;
    render();
  });

  setTimeout(function () {
    if (!result.ws.done) {
      result.ws.done = true;
      result.ws.timedOut = true;
      try { ws.close(); } catch (e) { /* ignore */ }
    }
    result.fetch.done = true;
    render();
  }, timeoutMs);
  render();
})();
