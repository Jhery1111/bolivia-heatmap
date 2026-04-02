-- =============================================================================
-- SIGMAC — Sistema de Inteligencia Geoespacial y Mapas de Calor
-- Archivo: 03_views_materialized.sql
-- Descripción: Vistas materializadas para grillas hexagonales H3 y hotspots
-- Requiere: 02_schema_core.sql ejecutado previamente
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. Vista materializada: mv_hex_res7 — Grilla H3 resolución 7 (barrios)
--    Área aproximada por celda: ~5.16 km²
--    h3_lat_lng_to_cell(lat, lng, resolution) devuelve H3Index (BIGINT)
-- ---------------------------------------------------------------------------

CREATE MATERIALIZED VIEW IF NOT EXISTS sigmac.mv_hex_res7 AS
WITH hex_cells AS (
    SELECT
        h3_lat_lng_to_cell(latitud, longitud, 7)    AS h3_index,
        severidad
    FROM sigmac.core_incidentes
    WHERE geom IS NOT NULL
),
aggregated AS (
    SELECT
        h3_index,
        COUNT(*)                                     AS conteo_incidentes,
        AVG(severidad)                               AS severidad_promedio,
        MAX(severidad)                               AS severidad_maxima,
        MIN(severidad)                               AS severidad_minima
    FROM hex_cells
    GROUP BY h3_index
)
SELECT
    a.h3_index,
    a.conteo_incidentes,
    ROUND(a.severidad_promedio::NUMERIC, 4)          AS severidad_promedio,
    a.severidad_maxima,
    a.severidad_minima,
    h3_cell_to_boundary_wkb(a.h3_index)::GEOMETRY(Polygon, 4326)
                                                     AS geom,
    NOW()                                            AS calculado_en
FROM aggregated a
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS uidx_mv_hex_res7_h3_index
    ON sigmac.mv_hex_res7 (h3_index);

CREATE INDEX IF NOT EXISTS idx_mv_hex_res7_geom
    ON sigmac.mv_hex_res7 USING GIST (geom);

CREATE INDEX IF NOT EXISTS idx_mv_hex_res7_conteo
    ON sigmac.mv_hex_res7 (conteo_incidentes DESC);

COMMENT ON MATERIALIZED VIEW sigmac.mv_hex_res7 IS
    'Grilla H3 resolución 7 (~5.16 km²/celda) con conteo de incidentes y severidad promedio — refresco concurrente habilitado';

-- ---------------------------------------------------------------------------
-- 2. Vista materializada: mv_hex_res5 — Grilla H3 resolución 5 (zonas)
--    Área aproximada por celda: ~252 km²
-- ---------------------------------------------------------------------------

CREATE MATERIALIZED VIEW IF NOT EXISTS sigmac.mv_hex_res5 AS
WITH hex_cells AS (
    SELECT
        h3_lat_lng_to_cell(latitud, longitud, 5)    AS h3_index,
        severidad
    FROM sigmac.core_incidentes
    WHERE geom IS NOT NULL
),
aggregated AS (
    SELECT
        h3_index,
        COUNT(*)                                     AS conteo_incidentes,
        AVG(severidad)                               AS severidad_promedio,
        MAX(severidad)                               AS severidad_maxima,
        MIN(severidad)                               AS severidad_minima
    FROM hex_cells
    GROUP BY h3_index
)
SELECT
    a.h3_index,
    a.conteo_incidentes,
    ROUND(a.severidad_promedio::NUMERIC, 4)          AS severidad_promedio,
    a.severidad_maxima,
    a.severidad_minima,
    h3_cell_to_boundary_wkb(a.h3_index)::GEOMETRY(Polygon, 4326)
                                                     AS geom,
    NOW()                                            AS calculado_en
FROM aggregated a
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS uidx_mv_hex_res5_h3_index
    ON sigmac.mv_hex_res5 (h3_index);

CREATE INDEX IF NOT EXISTS idx_mv_hex_res5_geom
    ON sigmac.mv_hex_res5 USING GIST (geom);

CREATE INDEX IF NOT EXISTS idx_mv_hex_res5_conteo
    ON sigmac.mv_hex_res5 (conteo_incidentes DESC);

COMMENT ON MATERIALIZED VIEW sigmac.mv_hex_res5 IS
    'Grilla H3 resolución 5 (~252 km²/celda) con conteo de incidentes y severidad promedio — refresco concurrente habilitado';

-- ---------------------------------------------------------------------------
-- 3. Vista materializada: mv_incidentes_24h
--    Incidentes de las últimas 24 horas con peso de decaimiento temporal.
--    Fórmula: peso = EXP(- edad_en_segundos / 86400)
--    El peso va de 1.0 (ahora mismo) a ~0.368 (hace exactamente 24 h).
-- ---------------------------------------------------------------------------

CREATE MATERIALIZED VIEW IF NOT EXISTS sigmac.mv_incidentes_24h AS
SELECT
    i.id,
    i.latitud,
    i.longitud,
    i.geom,
    i.tipo_incidente,
    i.severidad,
    i.timestamp,
    i.sistema_origen,
    i.id_sistema_origen,
    i.metadata,
    EXP(
        -EXTRACT(EPOCH FROM (NOW() - i.timestamp)) / 86400.0
    )                                                   AS peso_temporal,
    i.severidad * EXP(
        -EXTRACT(EPOCH FROM (NOW() - i.timestamp)) / 86400.0
    )                                                   AS peso_ponderado
FROM sigmac.core_incidentes i
WHERE i.timestamp >= NOW() - INTERVAL '24 hours'
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS uidx_mv_incidentes_24h_id_timestamp
    ON sigmac.mv_incidentes_24h (id, timestamp);

CREATE INDEX IF NOT EXISTS idx_mv_incidentes_24h_geom
    ON sigmac.mv_incidentes_24h USING GIST (geom);

CREATE INDEX IF NOT EXISTS idx_mv_incidentes_24h_peso_ponderado
    ON sigmac.mv_incidentes_24h (peso_ponderado DESC);

CREATE INDEX IF NOT EXISTS idx_mv_incidentes_24h_tipo
    ON sigmac.mv_incidentes_24h (tipo_incidente);

COMMENT ON MATERIALIZED VIEW sigmac.mv_incidentes_24h IS
    'Incidentes de las últimas 24 horas con decaimiento temporal exponencial: peso = EXP(-edad_seg/86400)';

-- ---------------------------------------------------------------------------
-- 4. Vista materializada: mv_hotspots_municipio
--    Top hotspots por municipio con agrupamiento H3 resolución 7.
--    Usa ST_Within para asignar cada hexágono al municipio correspondiente.
-- ---------------------------------------------------------------------------

CREATE MATERIALIZED VIEW IF NOT EXISTS sigmac.mv_hotspots_municipio AS
WITH incidentes_hex AS (
    SELECT
        h3_lat_lng_to_cell(latitud, longitud, 7)    AS h3_index,
        COUNT(*)                                     AS conteo,
        AVG(severidad)                               AS sev_promedio,
        SUM(severidad)                               AS sev_total
    FROM sigmac.core_incidentes
    WHERE geom IS NOT NULL
    GROUP BY h3_index
),
hex_con_geom AS (
    SELECT
        ih.h3_index,
        ih.conteo,
        ih.sev_promedio,
        ih.sev_total,
        h3_cell_to_boundary_wkb(ih.h3_index)::GEOMETRY(Polygon, 4326) AS geom_hex
    FROM incidentes_hex ih
),
hex_municipio AS (
    SELECT
        hg.h3_index,
        hg.conteo,
        hg.sev_promedio,
        hg.sev_total,
        hg.geom_hex,
        m.id                AS id_municipio,
        m.nombre            AS nombre_municipio,
        m.codigo_ine        AS codigo_ine_municipio,
        d.id                AS id_departamento,
        d.nombre            AS nombre_departamento,
        d.codigo_ine        AS codigo_ine_departamento
    FROM hex_con_geom hg
    JOIN sigmac.geo_municipios  m ON ST_Within(ST_Centroid(hg.geom_hex), m.geom)
    JOIN sigmac.geo_departamentos d ON m.id_departamento = d.id
),
ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY id_municipio
            ORDER BY conteo DESC, sev_total DESC
        ) AS rank_en_municipio
    FROM hex_municipio
)
SELECT
    r.h3_index,
    r.conteo,
    ROUND(r.sev_promedio::NUMERIC, 4)               AS sev_promedio,
    r.sev_total,
    r.geom_hex,
    r.id_municipio,
    r.nombre_municipio,
    r.codigo_ine_municipio,
    r.id_departamento,
    r.nombre_departamento,
    r.codigo_ine_departamento,
    r.rank_en_municipio,
    NOW()                                           AS calculado_en
FROM ranked r
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS uidx_mv_hotspots_municipio_h3_index
    ON sigmac.mv_hotspots_municipio (h3_index);

CREATE INDEX IF NOT EXISTS idx_mv_hotspots_municipio_geom
    ON sigmac.mv_hotspots_municipio USING GIST (geom_hex);

CREATE INDEX IF NOT EXISTS idx_mv_hotspots_municipio_id
    ON sigmac.mv_hotspots_municipio (id_municipio, rank_en_municipio);

CREATE INDEX IF NOT EXISTS idx_mv_hotspots_municipio_dep
    ON sigmac.mv_hotspots_municipio (id_departamento, conteo DESC);

COMMENT ON MATERIALIZED VIEW sigmac.mv_hotspots_municipio IS
    'Hexágonos H3-res7 ordenados por densidad de incidentes dentro de cada municipio — facilita identificar zonas calientes por unidad administrativa';

-- ---------------------------------------------------------------------------
-- 5. Función: refresh_all_materialized_views()
--    Refresca todas las vistas materializadas CONCURRENTEMENTE
--    (no bloquea lecturas durante el refresco gracias al UNIQUE INDEX).
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION sigmac.refresh_all_materialized_views()
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_start     TIMESTAMPTZ := clock_timestamp();
    v_step      TIMESTAMPTZ;
    v_elapsed   INTERVAL;
BEGIN
    RAISE NOTICE '[SIGMAC] Iniciando refresco de vistas materializadas — %', v_start;

    v_step := clock_timestamp();
    REFRESH MATERIALIZED VIEW CONCURRENTLY sigmac.mv_hex_res7;
    v_elapsed := clock_timestamp() - v_step;
    RAISE NOTICE '[SIGMAC] mv_hex_res7 refrescada en %', v_elapsed;

    v_step := clock_timestamp();
    REFRESH MATERIALIZED VIEW CONCURRENTLY sigmac.mv_hex_res5;
    v_elapsed := clock_timestamp() - v_step;
    RAISE NOTICE '[SIGMAC] mv_hex_res5 refrescada en %', v_elapsed;

    v_step := clock_timestamp();
    REFRESH MATERIALIZED VIEW CONCURRENTLY sigmac.mv_incidentes_24h;
    v_elapsed := clock_timestamp() - v_step;
    RAISE NOTICE '[SIGMAC] mv_incidentes_24h refrescada en %', v_elapsed;

    v_step := clock_timestamp();
    REFRESH MATERIALIZED VIEW CONCURRENTLY sigmac.mv_hotspots_municipio;
    v_elapsed := clock_timestamp() - v_step;
    RAISE NOTICE '[SIGMAC] mv_hotspots_municipio refrescada en %', v_elapsed;

    RAISE NOTICE '[SIGMAC] Refresco total completado en %', clock_timestamp() - v_start;
END;
$$;

COMMENT ON FUNCTION sigmac.refresh_all_materialized_views() IS
    'Refresca todas las vistas materializadas SIGMAC de forma concurrente. Programar con pg_cron u orquestador externo (ej. cada 5 minutos para mv_incidentes_24h).';

-- ---------------------------------------------------------------------------
-- 6. Ejemplo de programación con pg_cron (descomentar si pg_cron disponible)
-- ---------------------------------------------------------------------------

-- SELECT cron.schedule(
--     'sigmac_refresh_views',
--     '*/5 * * * *',
--     $$ SELECT sigmac.refresh_all_materialized_views(); $$
-- );
