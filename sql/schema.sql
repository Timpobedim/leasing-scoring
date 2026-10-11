-- Хранилище шлюза: заявки и история смены статусов (FR-09).
-- Деньги — целые рубли; время — ISO 8601 в UTC.

CREATE TABLE IF NOT EXISTS applications (
    id               TEXT PRIMARY KEY,                    -- LA-000001
    idempotency_key  TEXT NOT NULL UNIQUE,                -- он же requestId в банке
    payload_hash     TEXT NOT NULL,                       -- повтор ключа с другими данными → 409
    inn              TEXT NOT NULL,
    company_name     TEXT NOT NULL,
    asset_type       TEXT NOT NULL CHECK (asset_type IN ('CAR', 'TRUCK', 'EQUIPMENT')),
    amount           INTEGER NOT NULL CHECK (amount BETWEEN 100000 AND 50000000),
    advance_percent  INTEGER NOT NULL CHECK (advance_percent BETWEEN 10 AND 49),
    term_months      INTEGER NOT NULL CHECK (term_months BETWEEN 12 AND 60),
    status           TEXT NOT NULL,
    bank_id          TEXT UNIQUE,                         -- SC-00000001
    attempts         INTEGER NOT NULL DEFAULT 0,
    approved_amount  INTEGER,
    rate_percent     REAL,
    reason_code      TEXT,
    error_field      TEXT,
    error_message    TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS status_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id  TEXT NOT NULL REFERENCES applications (id),
    from_status     TEXT,                                 -- NULL — заявка создана
    to_status       TEXT NOT NULL,
    at              TEXT NOT NULL,
    note            TEXT
);

CREATE INDEX IF NOT EXISTS ix_history_app ON status_history (application_id, at);
CREATE INDEX IF NOT EXISTS ix_app_status ON applications (status);
