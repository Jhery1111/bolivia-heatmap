/**
 * SigmacAPI — comunicación con la API SIGMAC backend
 */

const TOKEN_KEY = 'sigmac_jwt_token';

export class SigmacAPI {
  /**
   * @param {string} baseUrl  e.g. '' (relativo, via proxy Vite) o 'http://host:8000'
   */
  constructor(baseUrl = '') {
    this.baseUrl = baseUrl;
  }

  // ── Auth ──────────────────────────────────────────────────

  async login(username, password) {
    const body = new URLSearchParams({ username, password, grant_type: 'password' });
    const res = await fetch(`${this.baseUrl}/api/v1/auth/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Login fallido (${res.status})`);
    }
    const data = await res.json();
    localStorage.setItem(TOKEN_KEY, data.access_token);
    return data;
  }

  logout() {
    localStorage.removeItem(TOKEN_KEY);
  }

  getToken() {
    return localStorage.getItem(TOKEN_KEY);
  }

  isAuthenticated() {
    return !!this.getToken();
  }

  getAuthHeaders() {
    const token = this.getToken();
    if (!token) throw new Error('No autenticado');
    return { Authorization: `Bearer ${token}` };
  }

  // ── Helper ────────────────────────────────────────────────

  async _get(path, params = {}) {
    const url = new URL(`${this.baseUrl}${path}`, window.location.href);
    Object.entries(params).forEach(([k, v]) => {
      if (v !== null && v !== undefined) url.searchParams.set(k, v);
    });
    const res = await fetch(url.toString(), {
      headers: this.getAuthHeaders(),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Error ${res.status}`);
    }
    return res.json();
  }

  async _post(path, body) {
    const res = await fetch(`${this.baseUrl}${path}`, {
      method: 'POST',
      headers: { ...this.getAuthHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Error ${res.status}`);
    }
    return res.json();
  }

  // ── Tiles ─────────────────────────────────────────────────

  /**
   * Retorna la URL de template para fuente de tiles MapLibre.
   * No requiere token ya que los tiles son públicos.
   */
  getTilesTemplateUrl(tipo = null, severidadMin = 1) {
    const base = `${this.baseUrl}/api/v1/tiles/{z}/{x}/{y}.pbf`;
    const params = new URLSearchParams();
    if (tipo) params.set('tipo', tipo);
    if (severidadMin > 1) params.set('severidad_min', severidadMin);
    const qs = params.toString();
    return qs ? `${base}?${qs}` : base;
  }

  // ── Heatmap / Analytics ───────────────────────────────────

  /**
   * @param {[number,number,number,number]} bbox  [minLon, minLat, maxLon, maxLat]
   */
  async getHeatmap(bbox, zoom = 10, hoursBack = 24, tipo = null, severidadMin = 1) {
    return this._get('/api/v1/heatmap', {
      bbox: bbox.join(','),
      zoom,
      hours_back: hoursBack,
      tipo,
      severidad_min: severidadMin,
    });
  }

  async getAnomalies(bbox, hoursBack = 6) {
    return this._get('/api/v1/anomalies', {
      bbox: bbox.join(','),
      hours_back: hoursBack,
    });
  }

  async getSummary(bbox) {
    return this._get('/api/v1/summary', { bbox: bbox.join(',') });
  }

  // ── Incidentes ────────────────────────────────────────────

  async getIncidentes({ limit = 20, offset = 0, tipo = null, severidadMin = null, fechaDesde = null, fechaHasta = null } = {}) {
    return this._get('/api/v1/incidentes', {
      limit, offset, tipo,
      severidad_min: severidadMin,
      fecha_desde: fechaDesde,
      fecha_hasta: fechaHasta,
    });
  }

  async getIncidente(id) {
    return this._get(`/api/v1/incidentes/${id}`);
  }

  async createIncidente({ tipo, severidad, lat, lon, descripcion = '' }) {
    return this._post('/api/v1/incidentes', { tipo_incidente: tipo, severidad, latitud: lat, longitud: lon, descripcion });
  }

  async getStatsdepartamentos(fechaDesde = null, fechaHasta = null) {
    return this._get('/api/v1/stats/departamentos', { fecha_desde: fechaDesde, fecha_hasta: fechaHasta });
  }
}
