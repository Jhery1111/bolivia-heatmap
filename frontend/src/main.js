/**
 * SIGMAC Dashboard — Punto de entrada principal
 * Orquesta: mapa, API, WebSocket, UI controls
 */

import { SigmacAPI }                                          from './api.js';
import { RealtimeClient }                                     from './websocket.js';
import { SigmacMap }                                         from './map.js';
import { TimeSlider, FilterControls, StatsPanel,
         LoginModal, IncidentPopup, updateWsStatus }          from './ui.js';

// ── Configuración ──────────────────────────────────────────────────────────────

const API_BASE = '';                                 // relativo — proxy Vite en dev
const WS_BASE  = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}`;

let api;
let map;
let realtimeClient;
let statsPanel;
let popup;
let hoursBack     = 24;
let activeFilters = { tipo: null, severidadMin: 1 };

let heatmapInterval = null;
let summaryInterval = null;

// ── Bootstrap ──────────────────────────────────────────────────────────────────

function init() {
  api        = new SigmacAPI(API_BASE);
  statsPanel = new StatsPanel();
  popup      = new IncidentPopup();

  const loginModal = new LoginModal('login-modal', async (username, password) => {
    await api.login(username, password);
    await startDashboard();
  });

  if (api.isAuthenticated()) {
    loginModal.hide();
    startDashboard().catch(() => {
      // Token might be expired
      api.logout();
      loginModal.show();
    });
  } else {
    loginModal.show();
  }
}

// ── Dashboard principal ────────────────────────────────────────────────────────

async function startDashboard() {
  // 1. Mapa
  const tilesUrl = api.getTilesTemplateUrl();
  map = new SigmacMap('map', tilesUrl);

  // 2. Esperar a que el mapa cargue antes de continuar con capas
  map.raw.on('load', () => {
    startDataLoops();
  });

  // 3. Time Slider
  new TimeSlider('time-slider', 'time-label', (hours) => {
    hoursBack = hours;
    refreshHeatmap();
  });

  // 4. Filtros
  const filterControls = new FilterControls('controls', ({ tipo, severidadMin }) => {
    activeFilters = { tipo, severidadMin };
    map.filterByType(tipo);
    map.filterBySeverity(severidadMin);
    // Actualizar URL de tiles para que los query params de filtro lleguen al backend
    const newUrl = api.getTilesTemplateUrl(tipo, severidadMin);
    map.updateTilesUrl(newUrl);
    refreshHeatmap();
  });

  // 5. Layer toggles
  document.getElementById('toggle-heatmap')?.addEventListener('change', (e) => {
    map.setLayerVisible('heatmap', e.target.checked);
  });
  document.getElementById('toggle-incidents')?.addEventListener('change', (e) => {
    map.setLayerVisible('incidents', e.target.checked);
  });
  document.getElementById('toggle-pulse')?.addEventListener('change', (e) => {
    map.setLayerVisible('pulse', e.target.checked);
  });

  // 6. Popup al hacer click en incidente
  window.addEventListener('sigmac:incident-click', (e) => {
    popup.show(e.detail);
  });

  // 7. WebSocket para tiempo real
  startWebSocket();
}

// ── Loops de datos ─────────────────────────────────────────────────────────────

function startDataLoops() {
  // Carga inicial
  refreshSummary();
  refreshHeatmap();

  // Summary cada 30s
  summaryInterval = setInterval(refreshSummary, 30_000);

  // Heatmap cada 60s
  heatmapInterval = setInterval(refreshHeatmap, 60_000);
}

async function refreshSummary() {
  if (!map) return;
  try {
    const bbox    = map.getBbox();
    const summary = await api.getSummary(bbox);
    statsPanel.update(summary);
  } catch (err) {
    console.warn('[SIGMAC] Error al obtener resumen:', err.message);
  }
}

async function refreshHeatmap() {
  if (!map) return;
  try {
    const bbox   = map.getBbox();
    const zoom   = map.getZoom();
    const { tipo, severidadMin } = activeFilters;

    const geojson = await api.getHeatmap(bbox, zoom, hoursBack, tipo, severidadMin);
    map.updateHeatmap(geojson);
  } catch (err) {
    console.warn('[SIGMAC] Error al actualizar heatmap:', err.message);
  }
}

// ── WebSocket ──────────────────────────────────────────────────────────────────

function startWebSocket() {
  const token = api.getToken();
  if (!token) return;

  realtimeClient = new RealtimeClient(
    `${WS_BASE}/ws/live-incidents`,
    token,
    handleNewIncident,
    handleAlert,
  );

  realtimeClient.connect();

  // Actualizar indicador de estado de conexión
  window.addEventListener('sigmac:ws-status', (e) => {
    updateWsStatus(e.detail.status);
  });
}

function handleNewIncident(incident) {
  // Añadir punto pulsante al mapa
  if (map) {
    map.addPulseIncident(incident);
  }
  // Actualizar lista reciente en sidebar
  statsPanel.addRecentIncident(incident);

  // Si el incidente es crítico (sev 5), refrescar summary
  if ((incident.severidad ?? incident.severity ?? 0) >= 5) {
    refreshSummary();
  }
}

function handleAlert(alertData) {
  console.info('[SIGMAC] Alerta recibida:', alertData);
  refreshSummary();
}

// ── Refrescar heatmap al mover el mapa ────────────────────────────────────────

// Debounce para no saturar el backend en cada movimiento
let moveDebounce = null;
function onMapMove() {
  clearTimeout(moveDebounce);
  moveDebounce = setTimeout(() => {
    refreshSummary();
    refreshHeatmap();
  }, 800);
}

// Se configura cuando el mapa esté listo
function attachMapEvents() {
  if (!map) return;
  map.on('moveend', onMapMove);
}

// ── Arranque ───────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  init();
  attachMapEvents();
});
