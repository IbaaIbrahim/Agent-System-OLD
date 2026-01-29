-- PostgreSQL initialization script
-- This runs on first container startup to set up the database

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Create schemas for logical separation
CREATE SCHEMA IF NOT EXISTS tenants;
CREATE SCHEMA IF NOT EXISTS billing;
CREATE SCHEMA IF NOT EXISTS jobs;

-- Grant permissions
GRANT ALL ON SCHEMA tenants TO agent;
GRANT ALL ON SCHEMA billing TO agent;
GRANT ALL ON SCHEMA jobs TO agent;

-- Create indexes tablespace (optional optimization)
-- Note: Actual table and index creation is handled by Alembic migrations

-- Utility function for updated_at timestamps
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Log initialization
DO $$
BEGIN
    RAISE NOTICE 'Database initialization complete. Run Alembic migrations to create tables.';
END $$;
