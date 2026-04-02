-- =============================================================================
-- SIGMAC — Sistema de Inteligencia Geoespacial y Mapas de Calor
-- Archivo: 01_init_extensions.sql
-- Descripción: Inicialización de extensiones PostgreSQL 16+ / PostGIS 3+
-- =============================================================================

-- Requerir superusuario o permisos CREATE EXTENSION
-- Ejecutar como: psql -U postgres -d sigmac_db -f 01_init_extensions.sql

-- ---------------------------------------------------------------------------
-- 1. Extensiones espaciales y geométricas
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
CREATE EXTENSION IF NOT EXISTS postgis_raster;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;
CREATE EXTENSION IF NOT EXISTS postgis_tiger_geocoder;

-- ---------------------------------------------------------------------------
-- 2. Generación de UUIDs
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- 3. H3 — grilla hexagonal discreta de Uber
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS h3;
CREATE EXTENSION IF NOT EXISTS h3_postgis CASCADE;

-- ---------------------------------------------------------------------------
-- 4. Índices GiST con soporte para rangos (btree_gist)
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS btree_gin;

-- ---------------------------------------------------------------------------
-- 5. Particionamiento automático
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS pg_partman;

-- ---------------------------------------------------------------------------
-- 6. Utilidades de rendimiento y monitoreo
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------------------
-- 7. Validar instalación
-- ---------------------------------------------------------------------------

DO $$
DECLARE
    v_postgis       TEXT;
    v_h3            TEXT;
    v_partman       TEXT;
BEGIN
    SELECT extversion INTO v_postgis   FROM pg_extension WHERE extname = 'postgis';
    SELECT extversion INTO v_h3        FROM pg_extension WHERE extname = 'h3';
    SELECT extversion INTO v_partman   FROM pg_extension WHERE extname = 'pg_partman';

    RAISE NOTICE '=== SIGMAC Extension Check ===';
    RAISE NOTICE 'PostGIS   : %', COALESCE(v_postgis,  'NOT INSTALLED');
    RAISE NOTICE 'H3        : %', COALESCE(v_h3,       'NOT INSTALLED');
    RAISE NOTICE 'pg_partman: %', COALESCE(v_partman,  'NOT INSTALLED');
    RAISE NOTICE '==============================';
END;
$$;
