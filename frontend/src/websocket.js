/**
 * RealtimeClient — cliente WebSocket con reconexión exponencial
 * para incidentes en tiempo real del canal SIGMAC.
 */

export class RealtimeClient {
  /**
   * @param {string}   wsUrl       URL base ws:// o wss://
   * @param {string}   token       JWT para autenticación
   * @param {Function} onIncident  callback(incidentData)
   * @param {Function} onAlert     callback(alertData)
   */
  constructor(wsUrl, token, onIncident, onAlert) {
    this._wsUrl = wsUrl;
    this._token = token;
    this._onIncident = onIncident;
    this._onAlert = onAlert;

    this._ws = null;
    this._reconnectDelay = 1000;   // 1 s inicial
    this._maxDelay = 30_000;       // 30 s máximo
    this._reconnectTimer = null;
    this._connected = false;
    this._destroyed = false;
  }

  // ── Public API ────────────────────────────────────────────

  connect() {
    this._destroyed = false;
    this._doConnect();
  }

  disconnect() {
    this._destroyed = true;
    clearTimeout(this._reconnectTimer);
    if (this._ws) {
      this._ws.close(1000, 'User disconnect');
      this._ws = null;
    }
    this._connected = false;
  }

  get isConnected() {
    return this._connected;
  }

  // ── Internal ──────────────────────────────────────────────

  _doConnect() {
    if (this._destroyed) return;

    const url = `${this._wsUrl}?token=${encodeURIComponent(this._token)}`;

    try {
      this._ws = new WebSocket(url);
    } catch (e) {
      this._scheduleReconnect();
      return;
    }

    this._ws.onopen = () => {
      this._connected = true;
      this._reconnectDelay = 1000; // reset backoff on success
      this._dispatchStatus('connected');
    };

    this._ws.onmessage = (event) => {
      this._handleMessage(event.data);
    };

    this._ws.onerror = () => {
      // onclose will fire after onerror, handle there
    };

    this._ws.onclose = (event) => {
      this._connected = false;
      if (!this._destroyed) {
        this._dispatchStatus('reconnecting');
        this._scheduleReconnect();
      } else {
        this._dispatchStatus('disconnected');
      }
    };
  }

  _handleMessage(raw) {
    let msg;
    try {
      msg = JSON.parse(raw);
    } catch {
      return;
    }

    switch (msg.type) {
      case 'NEW_INCIDENT':
        if (typeof this._onIncident === 'function') this._onIncident(msg.data);
        break;
      case 'ALERT':
        if (typeof this._onAlert === 'function') this._onAlert(msg.data);
        break;
      case 'PING':
        if (this._ws && this._ws.readyState === WebSocket.OPEN) {
          this._ws.send(JSON.stringify({ type: 'PONG' }));
        }
        break;
      case 'CONNECTED':
        // welcome message, nothing to do
        break;
      default:
        break;
    }
  }

  _scheduleReconnect() {
    if (this._destroyed) return;
    this._reconnectTimer = setTimeout(() => {
      this._reconnectDelay = Math.min(this._reconnectDelay * 2, this._maxDelay);
      this._doConnect();
    }, this._reconnectDelay);
  }

  _dispatchStatus(status) {
    window.dispatchEvent(new CustomEvent('sigmac:ws-status', { detail: { status } }));
  }
}
