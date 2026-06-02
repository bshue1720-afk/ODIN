-- Migration 024: KPI Trend Snapshots + Lead Aging
-- kpi_snapshots: daily snapshot of each KPI for week-over-week trends
-- leads.last_contact_date: stamped on every inbound reply for lead aging scan

CREATE TABLE IF NOT EXISTS kpi_snapshots (
    id            SERIAL PRIMARY KEY,
    metric_name   TEXT        NOT NULL,
    spoke         TEXT        NOT NULL,
    value         NUMERIC,
    status        TEXT,        -- green / yellow / red
    snapshot_date DATE        NOT NULL DEFAULT CURRENT_DATE,
    created_at    TIMESTAMP   DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_kpi_snapshots_unique
    ON kpi_snapshots (metric_name, spoke, snapshot_date);

CREATE INDEX IF NOT EXISTS idx_kpi_snapshots_date ON kpi_snapshots(snapshot_date);

ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS last_contact_date DATE;

CREATE INDEX IF NOT EXISTS idx_leads_last_contact_date ON leads(last_contact_date)
    WHERE last_contact_date IS NOT NULL;
