-- =============================================================================
-- SIGMAC — Sistema de Inteligencia Geoespacial y Mapas de Calor
-- Archivo: 02_schema_core.sql
-- Descripción: Esquema principal, tablas geográficas y tabla de incidentes
--              particionada por rango de tiempo
-- Requiere: 01_init_extensions.sql ejecutado previamente
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. Schema principal
-- ---------------------------------------------------------------------------

CREATE SCHEMA IF NOT EXISTS sigmac;

COMMENT ON SCHEMA sigmac IS
    'Schema principal del Sistema de Inteligencia Geoespacial y Mapas de Calor (SIGMAC) — DNTT Bolivia';

-- ---------------------------------------------------------------------------
-- 2. Tabla: geo_departamentos
--    Polígonos límite de los 9 departamentos de Bolivia
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sigmac.geo_departamentos (
    id              SERIAL          PRIMARY KEY,
    nombre          VARCHAR(100)    NOT NULL,
    codigo_ine      CHAR(2)         NOT NULL UNIQUE,
    geom            GEOMETRY(MultiPolygon, 4326) NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_geo_departamentos_geom
    ON sigmac.geo_departamentos USING GIST (geom);

CREATE INDEX IF NOT EXISTS idx_geo_departamentos_codigo_ine
    ON sigmac.geo_departamentos (codigo_ine);

COMMENT ON TABLE  sigmac.geo_departamentos               IS 'Límites departamentales de Bolivia — fuente GADM/IGM, WGS 84 (EPSG:4326)';
COMMENT ON COLUMN sigmac.geo_departamentos.codigo_ine    IS 'Código de dos dígitos del INE: 01=Chuquisaca, 02=Cochabamba, 03=Beni, 04=Oruro, 05=Pando, 06=Potosí, 07=Santa Cruz, 08=Tarija, 09=La Paz';

-- ---------------------------------------------------------------------------
-- 3. Tabla: geo_municipios
--    Polígonos de los 339 municipios de Bolivia
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sigmac.geo_municipios (
    id                  SERIAL          PRIMARY KEY,
    nombre              VARCHAR(150)    NOT NULL,
    codigo_ine          CHAR(6)         NOT NULL UNIQUE,
    id_departamento     INT             NOT NULL
                            REFERENCES sigmac.geo_departamentos (id)
                            ON UPDATE CASCADE ON DELETE RESTRICT,
    geom                GEOMETRY(MultiPolygon, 4326) NOT NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_geo_municipios_geom
    ON sigmac.geo_municipios USING GIST (geom);

CREATE INDEX IF NOT EXISTS idx_geo_municipios_id_departamento
    ON sigmac.geo_municipios (id_departamento);

CREATE INDEX IF NOT EXISTS idx_geo_municipios_codigo_ine
    ON sigmac.geo_municipios (codigo_ine);

COMMENT ON TABLE  sigmac.geo_municipios                    IS 'Límites municipales de Bolivia — fuente GADM/INE, WGS 84 (EPSG:4326)';
COMMENT ON COLUMN sigmac.geo_municipios.codigo_ine         IS 'Código de seis dígitos del INE (los dos primeros coinciden con codigo_ine del departamento)';
COMMENT ON COLUMN sigmac.geo_municipios.id_departamento    IS 'FK al departamento al que pertenece el municipio';

-- ---------------------------------------------------------------------------
-- 4. Tabla PADRE particionada: core_incidentes
--    Particionada por RANGE en el campo timestamp (mensual).
--    La columna geom es generada (GENERATED ALWAYS AS … STORED).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes (
    id                  UUID            NOT NULL DEFAULT gen_random_uuid(),
    latitud             DOUBLE PRECISION NOT NULL,
    longitud            DOUBLE PRECISION NOT NULL,
    geom                GEOMETRY(Point, 4326)
                            GENERATED ALWAYS AS (
                                ST_SetSRID(ST_MakePoint(longitud, latitud), 4326)
                            ) STORED,
    tipo_incidente      VARCHAR(100)    NOT NULL,
    severidad           SMALLINT        NOT NULL
                            CHECK (severidad BETWEEN 1 AND 5),
    timestamp           TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    id_sistema_origen   VARCHAR(50),
    sistema_origen      VARCHAR(20)
                            CHECK (sistema_origen IN ('JUPITER','MINERVA','SIGMAC','MANUAL')),
    metadata            JSONB           NOT NULL DEFAULT '{}',
    PRIMARY KEY (id, timestamp)
) PARTITION BY RANGE (timestamp);

COMMENT ON TABLE  sigmac.core_incidentes                    IS 'Tabla maestra de incidentes geoespaciales — particionada mensualmente por timestamp';
COMMENT ON COLUMN sigmac.core_incidentes.geom               IS 'Punto WGS 84 calculado automáticamente desde latitud/longitud';
COMMENT ON COLUMN sigmac.core_incidentes.id_sistema_origen  IS 'Identificador del registro en el sistema de origen (JÚPITER, Minerva, etc.)';
COMMENT ON COLUMN sigmac.core_incidentes.sistema_origen     IS 'Sistema que originó el registro';
COMMENT ON COLUMN sigmac.core_incidentes.metadata           IS 'Datos adicionales semiestructurados específicos del sistema de origen';

-- ---------------------------------------------------------------------------
-- 5. Particiones mensuales 2024-01 → 2026-06
--    Nomenclatura: core_incidentes_YYYY_MM
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2024_01
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2024-01-01 00:00:00+00') TO ('2024-02-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2024_02
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2024-02-01 00:00:00+00') TO ('2024-03-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2024_03
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2024-03-01 00:00:00+00') TO ('2024-04-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2024_04
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2024-04-01 00:00:00+00') TO ('2024-05-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2024_05
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2024-05-01 00:00:00+00') TO ('2024-06-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2024_06
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2024-06-01 00:00:00+00') TO ('2024-07-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2024_07
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2024-07-01 00:00:00+00') TO ('2024-08-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2024_08
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2024-08-01 00:00:00+00') TO ('2024-09-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2024_09
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2024-09-01 00:00:00+00') TO ('2024-10-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2024_10
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2024-10-01 00:00:00+00') TO ('2024-11-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2024_11
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2024-11-01 00:00:00+00') TO ('2024-12-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2024_12
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2024-12-01 00:00:00+00') TO ('2025-01-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2025_01
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2025-01-01 00:00:00+00') TO ('2025-02-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2025_02
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2025-02-01 00:00:00+00') TO ('2025-03-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2025_03
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2025-03-01 00:00:00+00') TO ('2025-04-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2025_04
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2025-04-01 00:00:00+00') TO ('2025-05-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2025_05
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2025-05-01 00:00:00+00') TO ('2025-06-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2025_06
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2025-06-01 00:00:00+00') TO ('2025-07-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2025_07
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2025-07-01 00:00:00+00') TO ('2025-08-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2025_08
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2025-08-01 00:00:00+00') TO ('2025-09-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2025_09
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2025-09-01 00:00:00+00') TO ('2025-10-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2025_10
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2025-10-01 00:00:00+00') TO ('2025-11-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2025_11
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2025-11-01 00:00:00+00') TO ('2025-12-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2025_12
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2025-12-01 00:00:00+00') TO ('2026-01-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2026_01
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2026-01-01 00:00:00+00') TO ('2026-02-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2026_02
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2026-02-01 00:00:00+00') TO ('2026-03-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2026_03
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2026-03-01 00:00:00+00') TO ('2026-04-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2026_04
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2026-04-01 00:00:00+00') TO ('2026-05-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2026_05
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2026-05-01 00:00:00+00') TO ('2026-06-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_2026_06
    PARTITION OF sigmac.core_incidentes
    FOR VALUES FROM ('2026-06-01 00:00:00+00') TO ('2026-07-01 00:00:00+00');

-- Partición catch-all para datos fuera de rango (evita errores de inserción)
CREATE TABLE IF NOT EXISTS sigmac.core_incidentes_default
    PARTITION OF sigmac.core_incidentes DEFAULT;

-- ---------------------------------------------------------------------------
-- 6. Índices sobre la tabla padre (se propagan a las particiones)
-- ---------------------------------------------------------------------------

-- Índice espacial GiST sobre geom
CREATE INDEX IF NOT EXISTS idx_core_incidentes_geom
    ON sigmac.core_incidentes USING GIST (geom);

-- Índice en timestamp para poda de particiones y ORDER BY
CREATE INDEX IF NOT EXISTS idx_core_incidentes_timestamp
    ON sigmac.core_incidentes (timestamp DESC);

-- Índice compuesto para filtros por tipo e intensidad
CREATE INDEX IF NOT EXISTS idx_core_incidentes_tipo_severidad
    ON sigmac.core_incidentes (tipo_incidente, severidad);

-- Índice GIN para consultas JSONB en metadata
CREATE INDEX IF NOT EXISTS idx_core_incidentes_metadata
    ON sigmac.core_incidentes USING GIN (metadata jsonb_path_ops);

-- Índice en sistema_origen para unión con JÚPITER/Minerva
CREATE INDEX IF NOT EXISTS idx_core_incidentes_sistema_origen
    ON sigmac.core_incidentes (sistema_origen, id_sistema_origen)
    WHERE sistema_origen IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 7. Configuración de pg_partman para nuevas particiones automáticas
--    (gestiona particiones futuras más allá de 2026-06 de forma continua)
-- ---------------------------------------------------------------------------

SELECT partman.create_parent(
    p_parent_table   => 'sigmac.core_incidentes',
    p_control        => 'timestamp',
    p_interval       => '1 month',
    p_start_partition => '2026-07-01 00:00:00+00',
    p_premake        => 3,
    p_automatic_maintenance => 'on'
);

-- Actualizar configuración de pg_partman para el schema sigmac
UPDATE partman.part_config
SET    infinite_time_partitions = TRUE,
       retention                = NULL,
       retention_keep_table     = TRUE
WHERE  parent_table = 'sigmac.core_incidentes';

-- ---------------------------------------------------------------------------
-- 8. Trigger: updated_at automático para tablas geo
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION sigmac.fn_set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_geo_departamentos_updated_at
    BEFORE UPDATE ON sigmac.geo_departamentos
    FOR EACH ROW EXECUTE FUNCTION sigmac.fn_set_updated_at();

CREATE TRIGGER trg_geo_municipios_updated_at
    BEFORE UPDATE ON sigmac.geo_municipios
    FOR EACH ROW EXECUTE FUNCTION sigmac.fn_set_updated_at();
