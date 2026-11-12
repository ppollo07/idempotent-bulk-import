-- UNIQUE (source_id) is the idempotency guarantee: the write path relies on
-- INSERT ... ON CONFLICT (source_id) DO NOTHING and nothing else. See ADR 0001.
-- payload_hash is the SHA-256 of the normalised business fields: it tells an identical
-- retry (already_existed) from a record whose data changed (conflict). See ADR 0002.
CREATE TABLE IF NOT EXISTS clients (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id text NOT NULL UNIQUE,
    first_name text NOT NULL,
    last_name text NOT NULL,
    email text,
    payload_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS patients (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id text NOT NULL UNIQUE,
    name text NOT NULL,
    species text NOT NULL,
    date_of_birth date,
    -- owner_id is the real link, resolved once from owner_source_id at write time, so a
    -- patient can never point at a client that does not exist.
    owner_id uuid NOT NULL REFERENCES clients (id),
    owner_source_id text NOT NULL,
    payload_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Postgres indexes the parent side of a foreign key, never the child. Without this, deleting a
-- client or counting patients per owner reads the whole table.
CREATE INDEX IF NOT EXISTS patients_owner_id_idx ON patients (owner_id);
