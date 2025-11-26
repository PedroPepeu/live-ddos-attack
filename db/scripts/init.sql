-- ativar extensões
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Create user
DO
$do$
BEGIN
   IF NOT EXISTS (
      SELECT FROM pg_catalog.pg_roles
      WHERE  rolname = 'example') THEN

      CREATE ROLE example WITH LOGIN PASSWORD 'example';
   END IF;
END
$do$;

-- Grant permissions
GRANT ALL PRIVILEGES ON DATABASE example TO example;

-- tabela "attacks"
CREATE TABLE IF NOT EXISTS attacks (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  src_ip TEXT,
  src_asn INTEGER,
  src_country TEXT,
  src_lat DOUBLE PRECISION,
  src_lon DOUBLE PRECISION,
  dst_ip TEXT,
  dst_port INTEGER,
  attack_type TEXT,
  score DOUBLE PRECISION,
  meta JSONB,
  geom geography(POINT, 4326)  -- PostGIS geography point
);

-- transformar em hypertable (timescale)
SELECT create_hypertable('attacks', 'ts', if_not_exists => TRUE);

-- índices úteis
CREATE INDEX IF NOT EXISTS idx_attacks_ts ON attacks (ts DESC);
CREATE INDEX IF NOT EXISTS idx_attacks_geom ON attacks USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_attacks_src_ip ON attacks (src_ip);
