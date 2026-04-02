/**
 * SigmacMap — motor principal de visualización MapLibre GL JS
 * Gestiona capas: Vector Tiles MVT, Heatmap, Pulse (tiempo real)
 */

// Carto Dark Matter basemap (no requiere token)
const DARK_STYLE = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json';

// IDs de capas y fuentes
const SRC_INCIDENTS  = 'sigmac-incidents-src';
const SRC_HEATMAP    = 'sigmac-heatmap-src';
const SRC_PULSE      = 'sigmac-pulse-src';

const LYR_INCIDENTS  = 'sigmac-incidents-layer';
const LYR_HEATMAP    = 'sigmac-heatmap-layer';
const LYR_PULSE_FILL = 'sigmac-pulse-fill';
const LYR_PULSE_RING = 'sigmac-pulse-ring';

// Gradiente térmico: azul → verde → amarillo → naranja → rojo
const HEATMAP_COLOR = [
  'interpolate', ['linear'], ['heatmap-density'],
  0,    'rgba(0,0,255,0)',
  0.15, '#0055ff',
  0.35, '#00aaff',
  0.50, '#00ff88',
  0.65, '#ffcc00',
  0.80, '#ff6600',
  1.0,  '#ff0000',
];

// Color de puntos por severidad
const CIRCLE_COLOR = [
  'match', ['get', 'severidad'],
  1, '#66aaff',
  2, '#00ff88',
  3, '#ffcc00',
  4, '#ff8800',
  5, '#ff2244',
  '#aaaaaa',
];

const CIRCLE_RADIUS = [
  'interpolate', ['linear'], ['zoom'],
  6,  ['match', ['get', 'severidad'], 5, 5, 4, 4, 3],
  14, ['match', ['get', 'severidad'], 5, 14, 4, 10, 7],
];

export class SigmacMap {
  /**
   * @param {string} containerId  ID del elemento DOM donde montar el mapa
   * @param {string} tilesUrl     URL template de los Vector Tiles MVT
   */
  constructor(containerId, tilesUrl) {
    this._tilesUrl = tilesUrl;
    this._pulseFeatures = new Map(); // id → feature temporal

    this._map = new maplibregl.Map({
      container: containerId,
      style: DARK_STYLE,
      center: [-64.9, -16.7],   // Bolivia centrado
      zoom: 5.5,
      minZoom: 4,
      maxZoom: 18,
      pitchWithRotate: false,
    });

    this._map.on('load', () => {
      this._initSources();
      this._initLayers();
      this._initInteractions();
    });
  }

  // ── Init ──────────────────────────────────────────────────

  _initSources() {
    // Vector Tiles desde el backend (MVT binario via PostGIS ST_AsMVT)
    this._map.addSource(SRC_INCIDENTS, {
      type: 'vector',
      tiles: [this._tilesUrl],
      minzoom: 4,
      maxzoom: 16,
    });

    // GeoJSON para heatmap analítico (se reemplaza periódicamente)
    this._map.addSource(SRC_HEATMAP, {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] },
    });

    // GeoJSON para incidentes en tiempo real (pulse)
    this._map.addSource(SRC_PULSE, {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] },
    });
  }

  _initLayers() {
    // 1. Heatmap analítico (debajo de los puntos)
    this._map.addLayer({
      id: LYR_HEATMAP,
      type: 'heatmap',
      source: SRC_HEATMAP,
      maxzoom: 18,
      paint: {
        'heatmap-weight': ['coalesce', ['get', 'density'], 0.5],
        'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 4, 0.6, 14, 2.5],
        'heatmap-color': HEATMAP_COLOR,
        'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 4, 15, 14, 40],
        'heatmap-opacity': 0.75,
      },
    });

    // 2. Puntos de incidentes via Vector Tiles
    this._map.addLayer({
      id: LYR_INCIDENTS,
      type: 'circle',
      source: SRC_INCIDENTS,
      'source-layer': 'incidentes',
      paint: {
        'circle-radius': CIRCLE_RADIUS,
        'circle-color': CIRCLE_COLOR,
        'circle-opacity': 0.85,
        'circle-stroke-width': 1,
        'circle-stroke-color': 'rgba(255,255,255,0.25)',
      },
    });

    // 3. Pulse fill (círculo relleno para tiempo real)
    this._map.addLayer({
      id: LYR_PULSE_FILL,
      type: 'circle',
      source: SRC_PULSE,
      paint: {
        'circle-radius': 8,
        'circle-color': ['match', ['get', 'severidad'], 5, '#ff2244', 4, '#ff8800', '#ffcc00'],
        'circle-opacity': 0.9,
        'circle-stroke-width': 2,
        'circle-stroke-color': '#ffffff',
      },
    });

    // 4. Pulse ring (anillo animado via WebGL expression, radius crece por timestamp)
    this._map.addLayer({
      id: LYR_PULSE_RING,
      type: 'circle',
      source: SRC_PULSE,
      paint: {
        'circle-radius': 20,
        'circle-color': 'rgba(0,0,0,0)',
        'circle-stroke-width': 2,
        'circle-stroke-color': ['match', ['get', 'severidad'], 5, '#ff2244', 4, '#ff8800', '#ffcc00'],
        'circle-stroke-opacity': ['interpolate', ['linear'], ['get', 'age'], 0, 0.9, 1, 0],
      },
    });
  }

  _initInteractions() {
    // Cursor pointer sobre incidentes
    this._map.on('mouseenter', LYR_INCIDENTS, () => {
      this._map.getCanvas().style.cursor = 'pointer';
    });
    this._map.on('mouseleave', LYR_INCIDENTS, () => {
      this._map.getCanvas().style.cursor = 'crosshair';
    });

    // Click en incidente → popup
    this._map.on('click', LYR_INCIDENTS, (e) => {
      const props = e.features?.[0]?.properties;
      if (!props) return;
      window.dispatchEvent(new CustomEvent('sigmac:incident-click', { detail: props }));
    });
  }

  // ── Public API ────────────────────────────────────────────

  /** Retorna el bbox actual del viewport [minLon, minLat, maxLon, maxLat] */
  getBbox() {
    const b = this._map.getBounds();
    return [
      parseFloat(b.getWest().toFixed(5)),
      parseFloat(b.getSouth().toFixed(5)),
      parseFloat(b.getEast().toFixed(5)),
      parseFloat(b.getNorth().toFixed(5)),
    ];
  }

  /** Zoom actual */
  getZoom() {
    return Math.round(this._map.getZoom());
  }

  /** Actualiza el source del heatmap con nuevo GeoJSON */
  updateHeatmap(geojson) {
    if (!this._map.isStyleLoaded()) return;
    const src = this._map.getSource(SRC_HEATMAP);
    if (src) src.setData(geojson);
  }

  /** Agrega incidente en tiempo real con animación de pulso, se elimina tras ttl ms */
  addPulseIncident(incident, ttl = 12_000) {
    const id = incident.id || `pulse-${Date.now()}`;
    const feature = {
      type: 'Feature',
      id,
      properties: {
        ...incident,
        age: 0,
      },
      geometry: {
        type: 'Point',
        coordinates: [incident.lon ?? incident.longitud, incident.lat ?? incident.latitud],
      },
    };

    this._pulseFeatures.set(id, feature);
    this._refreshPulseSource();

    // Animar age 0→1 durante ttl
    const start = Date.now();
    const animate = () => {
      const age = Math.min((Date.now() - start) / ttl, 1);
      const f = this._pulseFeatures.get(id);
      if (f) {
        f.properties.age = age;
        this._refreshPulseSource();
        if (age < 1) requestAnimationFrame(animate);
        else this._removePulseIncident(id);
      }
    };
    requestAnimationFrame(animate);
  }

  _removePulseIncident(id) {
    this._pulseFeatures.delete(id);
    this._refreshPulseSource();
  }

  _refreshPulseSource() {
    if (!this._map.isStyleLoaded()) return;
    const src = this._map.getSource(SRC_PULSE);
    if (src) {
      src.setData({
        type: 'FeatureCollection',
        features: Array.from(this._pulseFeatures.values()),
      });
    }
  }

  /** Filtra la capa de vector tiles por tipo de incidente */
  filterByType(tipo) {
    if (!this._map.isStyleLoaded()) return;
    const filter = tipo
      ? ['==', ['get', 'tipo_incidente'], tipo]
      : null;
    this._map.setFilter(LYR_INCIDENTS, filter);
  }

  /** Filtra por severidad mínima */
  filterBySeverity(minSev) {
    if (!this._map.isStyleLoaded()) return;
    const filter = minSev > 1
      ? ['>=', ['get', 'severidad'], minSev]
      : null;
    this._map.setFilter(LYR_INCIDENTS, filter);
  }

  /** Actualiza URL de tiles (por ejemplo al cambiar filtros query string) */
  updateTilesUrl(newUrl) {
    if (!this._map.isStyleLoaded()) return;
    this._tilesUrl = newUrl;
    const src = this._map.getSource(SRC_INCIDENTS);
    if (src) {
      src.setTiles([newUrl]);
    }
  }

  /** Muestra/oculta capas */
  setLayerVisible(layer, visible) {
    if (!this._map.isStyleLoaded()) return;
    const layerMap = {
      heatmap:   LYR_HEATMAP,
      incidents: LYR_INCIDENTS,
      pulse:     LYR_PULSE_FILL,
    };
    const id = layerMap[layer];
    if (id) {
      this._map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
      if (layer === 'pulse') {
        this._map.setLayoutProperty(LYR_PULSE_RING, 'visibility', visible ? 'visible' : 'none');
      }
    }
  }

  /** Fly to coordinates */
  flyTo(lon, lat, zoom = 13) {
    this._map.flyTo({ center: [lon, lat], zoom, duration: 1200 });
  }

  /** Event passthrough para eventos MapLibre */
  on(event, layerOrCallback, callback) {
    if (callback) {
      this._map.on(event, layerOrCallback, callback);
    } else {
      this._map.on(event, layerOrCallback);
    }
  }

  /** MapLibre instance (acceso avanzado) */
  get raw() {
    return this._map;
  }
}
