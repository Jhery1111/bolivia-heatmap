-- =============================================================================
-- SIGMAC — Sistema de Inteligencia Geoespacial y Mapas de Calor
-- Archivo: 04_functions_spatial.sql
-- Descripción: Funciones PL/pgSQL espaciales — radio, MVT tiles, estadísticas
-- Requiere: 02_schema_core.sql ejecutado previamente
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. sigmac.fn_incidentes_en_radio
--    Retorna todos los incidentes dentro de un radio dado en metros,
--    calculado en UTM Zona 19S (SRID 32719) para precisión métrica exacta.
--    El parámetro horas_atras filtra por ventana temporal reciente.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION sigmac.fn_incidentes_en_radio(
    lon          DOUBLE PRECISION,
    lat          DOUBLE PRECISION,
    radio_metros INT,
    horas_atras  INT DEFAULT 24
)
RETURNS TABLE (
    id                  UUID,
    latitud             DOUBLE PRECISION,
    longitud            DOUBLE PRECISION,
    geom                GEOMETRY(Point, 4326),
    tipo_incidente      VARCHAR(100),
    severidad           SMALLINT,
    timestamp           TIMESTAMPTZ,
    id_sistema_origen   VARCHAR(50),
    sistema_origen      VARCHAR(20),
    metadata            JSONB,
    distancia_metros    DOUBLE PRECISION
)
LANGUAGE plpgsql
STABLE
PARALLEL SAFE
AS $$
DECLARE
    v_punto_wgs   GEOMETRY(Point, 4326);
    v_punto_utm   GEOMETRY(Point, 32719);
    v_desde       TIMESTAMPTZ;
BEGIN
    IF radio_metros <= 0 THEN
        RAISE EXCEPTION 'radio_metros debe ser un entero positivo, recibido: %', radio_metros;
    END IF;

    IF horas_atras <= 0 THEN
        RAISE EXCEPTION 'horas_atras debe ser un entero positivo, recibido: %', horas_atras;
    END IF;

    IF lon < -180 OR lon > 180 THEN
        RAISE EXCEPTION 'longitud fuera de rango [-180, 180]: %', lon;
    END IF;

    IF lat < -90 OR lat > 90 THEN
        RAISE EXCEPTION 'latitud fuera de rango [-90, 90]: %', lat;
    END IF;

    v_punto_wgs := ST_SetSRID(ST_MakePoint(lon, lat), 4326);
    v_punto_utm := ST_Transform(v_punto_wgs, 32719);
    v_desde     := NOW() - (horas_atras || ' hours')::INTERVAL;

    RETURN QUERY
    SELECT
        i.id,
        i.latitud,
        i.longitud,
        i.geom,
        i.tipo_incidente,
        i.severidad,
        i.timestamp,
        i.id_sistema_origen,
        i.sistema_origen,
        i.metadata,
        ROUND(
            ST_Distance(
                ST_Transform(i.geom, 32719),
                v_punto_utm
            )::NUMERIC,
            2
        )::DOUBLE PRECISION AS distancia_metros
    FROM sigmac.core_incidentes i
    WHERE
        i.timestamp >= v_desde
        AND ST_DWithin(
                ST_Transform(i.geom, 32719),
                v_punto_utm,
                radio_metros::FLOAT8
            )
    ORDER BY distancia_metros ASC;
END;
$$;

COMMENT ON FUNCTION sigmac.fn_incidentes_en_radio(DOUBLE PRECISION, DOUBLE PRECISION, INT, INT) IS
    'Retorna incidentes dentro de radio_metros desde (lon, lat) en las últimas horas_atras horas.
     La distancia se calcula en UTM Zona 19S (SRID 32719) para máxima precisión métrica en Bolivia.
     Devuelve filas ordenadas por distancia_metros ASC.';

-- ---------------------------------------------------------------------------
-- 2. sigmac.fn_mvt_tile
--    Genera un Vector Tile binario (Mapbox Vector Tile) para los incidentes
--    de un tile z/x/y dado. Soporta filtro opcional por tipo_incidente y
--    severidad mínima. Compatible con clientes Mapbox GL JS, MapLibre, Leaflet.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION sigmac.fn_mvt_tile(
    z               INT,
    x               INT,
    y               INT,
    tipo            VARCHAR  DEFAULT NULL,
    severidad_min   INT      DEFAULT 1
)
RETURNS BYTEA
LANGUAGE plpgsql
STABLE
PARALLEL SAFE
AS $$
DECLARE
    v_envelope   GEOMETRY;
    v_tile       BYTEA;
BEGIN
    IF z < 0 OR z > 22 THEN
        RAISE EXCEPTION 'Nivel de zoom z fuera de rango [0, 22]: %', z;
    END IF;

    IF severidad_min < 1 OR severidad_min > 5 THEN
        RAISE EXCEPTION 'severidad_min debe estar entre 1 y 5, recibido: %', severidad_min;
    END IF;

    v_envelope := ST_TileEnvelope(z, x, y);

    SELECT INTO v_tile
        ST_AsMVT(tile_data.*, 'incidentes', 4096, 'mvt_geom')
    FROM (
        SELECT
            i.id::TEXT                                              AS id,
            i.tipo_incidente,
            i.severidad,
            EXTRACT(EPOCH FROM i.timestamp)::BIGINT                AS timestamp_epoch,
            i.sistema_origen,
            i.id_sistema_origen,
            i.metadata,
            ST_AsMVTGeom(
                i.geom,
                v_envelope,
                4096,
                256,
                TRUE
            )                                                       AS mvt_geom
        FROM sigmac.core_incidentes i
        WHERE
            i.geom && ST_Transform(v_envelope, 4326)
            AND ST_Intersects(i.geom, ST_Transform(v_envelope, 4326))
            AND i.severidad >= severidad_min
            AND (tipo IS NULL OR i.tipo_incidente = tipo)
    ) AS tile_data
    WHERE tile_data.mvt_geom IS NOT NULL;

    RETURN COALESCE(v_tile, ''::BYTEA);
END;
$$;

COMMENT ON FUNCTION sigmac.fn_mvt_tile(INT, INT, INT, VARCHAR, INT) IS
    'Genera un Vector Tile MVT binario (Mapbox Vector Tile spec 2.1) para el tile z/x/y.
     Capa de salida: "incidentes". Extensión de tile: 4096px con buffer de 256px.
     Filtros opcionales: tipo (tipo_incidente exacto) y severidad_min (1–5).
     Retorna BYTEA vacío (no NULL) si el tile no contiene incidentes.';

-- ---------------------------------------------------------------------------
-- 3. sigmac.fn_estadisticas_departamento
--    Estadísticas agregadas de incidentes para un departamento y rango
--    de fechas dado. Útil para dashboards y reportes ejecutivos.
-- ---------------------------------------------------------------------------

CREATE TYPE IF NOT EXISTS sigmac.t_estadisticas_departamento AS (
    id_departamento         INT,
    nombre_departamento     VARCHAR(100),
    codigo_ine              CHAR(2),
    total_incidentes        BIGINT,
    severidad_promedio      NUMERIC(5,4),
    severidad_maxima        SMALLINT,
    severidad_minima        SMALLINT,
    incidentes_criticos     BIGINT,
    tipos_distintos         BIGINT,
    tipo_mas_frecuente      VARCHAR(100),
    conteo_tipo_frecuente   BIGINT,
    sistemas_origen         TEXT[],
    primer_incidente        TIMESTAMPTZ,
    ultimo_incidente        TIMESTAMPTZ,
    periodo_dias            NUMERIC(10,2)
);

CREATE OR REPLACE FUNCTION sigmac.fn_estadisticas_departamento(
    id_dep      INT,
    fecha_desde TIMESTAMPTZ,
    fecha_hasta TIMESTAMPTZ
)
RETURNS SETOF sigmac.t_estadisticas_departamento
LANGUAGE plpgsql
STABLE
PARALLEL SAFE
AS $$
DECLARE
    v_departamento  sigmac.geo_departamentos%ROWTYPE;
BEGIN
    IF fecha_desde IS NULL OR fecha_hasta IS NULL THEN
        RAISE EXCEPTION 'fecha_desde y fecha_hasta no pueden ser NULL';
    END IF;

    IF fecha_desde >= fecha_hasta THEN
        RAISE EXCEPTION 'fecha_desde (%) debe ser anterior a fecha_hasta (%)', fecha_desde, fecha_hasta;
    END IF;

    SELECT * INTO v_departamento
    FROM sigmac.geo_departamentos
    WHERE id = id_dep;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'No existe departamento con id = %', id_dep;
    END IF;

    RETURN QUERY
    WITH incidentes_dep AS (
        SELECT
            i.id,
            i.tipo_incidente,
            i.severidad,
            i.timestamp,
            i.sistema_origen,
            i.geom
        FROM sigmac.core_incidentes i
        WHERE
            i.timestamp BETWEEN fecha_desde AND fecha_hasta
            AND ST_Within(i.geom, v_departamento.geom)
    ),
    base_stats AS (
        SELECT
            COUNT(*)                            AS total_incidentes,
            ROUND(AVG(severidad)::NUMERIC, 4)   AS severidad_promedio,
            MAX(severidad)                      AS severidad_maxima,
            MIN(severidad)                      AS severidad_minima,
            COUNT(*) FILTER (WHERE severidad = 5)
                                                AS incidentes_criticos,
            COUNT(DISTINCT tipo_incidente)      AS tipos_distintos,
            ARRAY_AGG(DISTINCT sistema_origen ORDER BY sistema_origen)
                FILTER (WHERE sistema_origen IS NOT NULL)
                                                AS sistemas_origen,
            MIN(timestamp)                      AS primer_incidente,
            MAX(timestamp)                      AS ultimo_incidente
        FROM incidentes_dep
    ),
    tipo_frecuente AS (
        SELECT
            tipo_incidente,
            COUNT(*) AS conteo
        FROM incidentes_dep
        GROUP BY tipo_incidente
        ORDER BY conteo DESC
        LIMIT 1
    )
    SELECT
        v_departamento.id,
        v_departamento.nombre,
        v_departamento.codigo_ine,
        bs.total_incidentes,
        bs.severidad_promedio,
        bs.severidad_maxima,
        bs.severidad_minima,
        bs.incidentes_criticos,
        bs.tipos_distintos,
        tf.tipo_incidente,
        tf.conteo,
        bs.sistemas_origen,
        bs.primer_incidente,
        bs.ultimo_incidente,
        ROUND(
            EXTRACT(EPOCH FROM (fecha_hasta - fecha_desde)) / 86400.0,
            2
        )
    FROM base_stats bs
    LEFT JOIN tipo_frecuente tf ON TRUE;
END;
$$;

COMMENT ON FUNCTION sigmac.fn_estadisticas_departamento(INT, TIMESTAMPTZ, TIMESTAMPTZ) IS
    'Devuelve estadísticas agregadas de incidentes para el departamento id_dep en el rango [fecha_desde, fecha_hasta].
     Incluye totales, severidad promedio/máx/mín, incidentes críticos (severidad=5), tipo más frecuente
     y lista de sistemas de origen. Lanza EXCEPTION si el departamento no existe o el rango es inválido.';
