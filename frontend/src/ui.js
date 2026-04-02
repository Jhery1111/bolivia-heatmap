/**
 * UI Controllers — TimeSlider, FilterControls, StatsPanel, LoginModal
 */

// ── Helpers ───────────────────────────────────────────────────────────────────

const SEVERITY_LABELS = { 1: 'Bajo', 2: 'Moderado', 3: 'Medio', 4: 'Alto', 5: 'Crítico' };
const ALERT_LABELS    = ['SIN ACTIVIDAD', 'ACTIVIDAD NORMAL', 'ACTIVIDAD ELEVADA', 'ALERTA NARANJA', 'ALERTA ROJA'];

function relativeTime(isoString) {
  const diff = Date.now() - new Date(isoString).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60)  return `hace ${s}s`;
  if (s < 3600) return `hace ${Math.floor(s/60)}m`;
  return `hace ${Math.floor(s/3600)}h`;
}

function formatDateTime(isoString) {
  return new Date(isoString).toLocaleString('es-BO', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

// ── TimeSlider ────────────────────────────────────────────────────────────────

export class TimeSlider {
  /**
   * @param {string}   sliderId   ID del input[type=range]
   * @param {string}   labelId    ID del span de etiqueta
   * @param {Function} onChange   callback(hours: number)
   */
  constructor(sliderId, labelId, onChange) {
    this._slider = document.getElementById(sliderId);
    this._label  = document.getElementById(labelId);
    this._onChange = onChange;

    if (!this._slider) return;

    this._slider.addEventListener('input', () => {
      const h = parseInt(this._slider.value, 10);
      this._updateLabel(h);
      this._updateSliderBackground(h);
      if (this._onChange) this._onChange(h);
    });

    // Inicializar
    this._updateLabel(parseInt(this._slider.value, 10));
    this._updateSliderBackground(parseInt(this._slider.value, 10));
  }

  _updateLabel(hours) {
    if (!this._label) return;
    if (hours === 1)  { this._label.textContent = 'Última hora'; return; }
    if (hours === 24) { this._label.textContent = 'Últimas 24h'; return; }
    this._label.textContent = `Últimas ${hours}h`;
  }

  _updateSliderBackground(hours) {
    if (!this._slider) return;
    const pct = ((hours - 1) / (24 - 1)) * 100;
    this._slider.style.background =
      `linear-gradient(to right, var(--accent-blue) ${pct}%, var(--border-color) ${pct}%)`;
  }

  getValue() {
    return parseInt(this._slider?.value ?? '24', 10);
  }
}

// ── FilterControls ────────────────────────────────────────────────────────────

export class FilterControls {
  /**
   * @param {string}   containerId  ID del contenedor de botones
   * @param {Function} onFilter     callback({tipo: string|null, severidadMin: number})
   */
  constructor(containerId, onFilter) {
    this._container = document.getElementById(containerId);
    this._onFilter  = onFilter;
    this._activeTipo = null;
    this._severidadMin = 1;

    if (!this._container) return;

    this._container.querySelectorAll('.filter-btn').forEach(btn => {
      btn.addEventListener('click', () => this._handleClick(btn));
    });
  }

  _handleClick(btn) {
    // Quitar active de todos
    this._container.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');

    const filter = btn.dataset.filter;
    const tipo   = btn.dataset.tipo ?? null;

    if (filter === 'all') {
      this._activeTipo    = null;
      this._severidadMin  = 1;
    } else if (filter === 'severidad-alta') {
      this._activeTipo    = null;
      this._severidadMin  = 4;
    } else if (tipo) {
      this._activeTipo    = tipo;
      this._severidadMin  = 1;
    }

    if (this._onFilter) {
      this._onFilter({ tipo: this._activeTipo, severidadMin: this._severidadMin });
    }
  }

  getFilters() {
    return { tipo: this._activeTipo, severidadMin: this._severidadMin };
  }
}

// ── StatsPanel ────────────────────────────────────────────────────────────────

export class StatsPanel {
  constructor() {
    this._totalEl    = document.getElementById('stat-total');
    this._criticosEl = document.getElementById('stat-criticos');
    this._alertEl    = document.getElementById('alert-banner');
    this._alertText  = document.getElementById('alert-text');
    this._hotspotEl  = document.getElementById('hotspot-info');
    this._listEl     = document.getElementById('incident-list');
    this._lastUpd    = document.getElementById('last-update');
  }

  /**
   * Actualiza estadísticas con el resumen del backend
   * @param {Object} summary  { total, criticos, alert_level (0-4), hotspot }
   */
  update(summary) {
    if (!summary) return;

    if (this._totalEl)    this._totalEl.textContent    = summary.total ?? '—';
    if (this._criticosEl) this._criticosEl.textContent = summary.criticos ?? '—';

    const level = summary.alert_level ?? 0;
    if (this._alertEl) {
      this._alertEl.className = `alert-banner alert-${level}`;
    }
    if (this._alertText) {
      this._alertText.textContent = ALERT_LABELS[level] ?? 'DESCONOCIDO';
    }

    if (this._hotspotEl && summary.hotspot) {
      const h = summary.hotspot;
      this._hotspotEl.innerHTML = `
        <strong style="color:var(--text-primary)">${h.municipio ?? 'Zona desconocida'}</strong>
        <br/>
        <span style="color:var(--text-secondary);font-size:11px">
          ${h.conteo ?? '?'} incidentes · Severidad prom. ${(h.severidad_prom ?? 0).toFixed(1)}
        </span>`;
    }

    if (this._lastUpd) {
      this._lastUpd.textContent = new Date().toLocaleTimeString('es-BO');
    }
  }

  /**
   * Agrega un incidente nuevo a la lista reciente (máximo 10 items)
   * @param {Object} incident
   */
  addRecentIncident(incident) {
    if (!this._listEl) return;

    const sev = parseInt(incident.severidad ?? incident.severity ?? 1, 10);
    const card = document.createElement('div');
    card.className = `incident-card sev-${sev}`;
    card.innerHTML = `
      <div class="incident-card-header">
        <span class="incident-tipo">${incident.tipo_incidente ?? incident.tipo ?? 'Incidente'}</span>
        <span class="incident-sev">SEV ${sev}</span>
      </div>
      <div class="incident-meta">
        ${incident.sistema_origen ?? 'SIGMAC'} · ${relativeTime(incident.timestamp ?? new Date().toISOString())}
      </div>`;

    this._listEl.prepend(card);

    // Limitar a 10 items
    while (this._listEl.children.length > 10) {
      this._listEl.removeChild(this._listEl.lastChild);
    }
  }
}

// ── LoginModal ────────────────────────────────────────────────────────────────

export class LoginModal {
  /**
   * @param {string}   modalId   ID del div.modal-overlay
   * @param {Function} onLogin   callback(username, password) → debe retornar Promise
   */
  constructor(modalId, onLogin) {
    this._modal   = document.getElementById(modalId);
    this._form    = document.getElementById('login-form');
    this._errorEl = document.getElementById('login-error');
    this._btn     = document.getElementById('login-btn');
    this._onLogin = onLogin;

    if (!this._form) return;
    this._form.addEventListener('submit', (e) => {
      e.preventDefault();
      this._handleSubmit();
    });
  }

  show() {
    this._modal?.classList.remove('hidden');
  }

  hide() {
    this._modal?.classList.add('hidden');
  }

  async _handleSubmit() {
    const username = document.getElementById('username')?.value?.trim();
    const password = document.getElementById('password')?.value;

    if (!username || !password) {
      this._showError('Ingrese usuario y contraseña.');
      return;
    }

    this._setLoading(true);
    this._hideError();

    try {
      await this._onLogin(username, password);
      this.hide();
    } catch (err) {
      this._showError(err.message ?? 'Error de autenticación. Verifique sus credenciales.');
    } finally {
      this._setLoading(false);
    }
  }

  _showError(msg) {
    if (!this._errorEl) return;
    this._errorEl.textContent = msg;
    this._errorEl.classList.remove('hidden');
  }

  _hideError() {
    this._errorEl?.classList.add('hidden');
  }

  _setLoading(loading) {
    if (!this._btn) return;
    this._btn.disabled = loading;
    this._btn.textContent = loading ? 'AUTENTICANDO...' : 'INICIAR SESIÓN';
  }
}

// ── WebSocket Status UI ───────────────────────────────────────────────────────

export function updateWsStatus(status) {
  const el = document.getElementById('ws-status');
  if (!el) return;
  const labels = {
    connected:    ['ws-connected',    '● CONECTADO'],
    disconnected: ['ws-disconnected', '● DESCONECTADO'],
    reconnecting: ['ws-reconnecting', '◌ RECONECTANDO...'],
  };
  const [cls, text] = labels[status] ?? ['ws-disconnected', '● DESCONECTADO'];
  el.className = `ws-status ${cls}`;
  el.innerHTML = `<span class="ws-dot"></span> ${text.replace('● ','').replace('◌ ','')}`;
}

// ── Incident Popup ────────────────────────────────────────────────────────────

export class IncidentPopup {
  constructor() {
    this._el         = document.getElementById('incident-popup');
    this._closeBtn   = document.getElementById('popup-close');
    this._tipoEl     = document.getElementById('popup-tipo');
    this._sevEl      = document.getElementById('popup-severidad');
    this._sistemaEl  = document.getElementById('popup-sistema');
    this._timestampEl= document.getElementById('popup-timestamp');
    this._coordsEl   = document.getElementById('popup-coords');

    this._closeBtn?.addEventListener('click', () => this.hide());
  }

  show(props) {
    if (!this._el) return;

    const sev = parseInt(props.severidad ?? 1, 10);
    const sevColors = { 1:'#66aaff',2:'#00ff88',3:'#ffcc00',4:'#ff8800',5:'#ff2244' };
    const col = sevColors[sev] ?? '#aaa';

    if (this._tipoEl)      this._tipoEl.textContent     = props.tipo_incidente ?? 'Incidente';
    if (this._sevEl) {
      this._sevEl.textContent = `SEV ${sev} — ${SEVERITY_LABELS[sev] ?? ''}`;
      this._sevEl.style.cssText = `background:${col}22;color:${col};border:1px solid ${col}44`;
    }
    if (this._sistemaEl)   this._sistemaEl.textContent  = props.sistema_origen ?? '—';
    if (this._timestampEl) this._timestampEl.textContent = props.timestamp_epoch
      ? formatDateTime(new Date(props.timestamp_epoch * 1000).toISOString())
      : '—';

    this._el.classList.remove('hidden');
  }

  hide() {
    this._el?.classList.add('hidden');
  }
}
