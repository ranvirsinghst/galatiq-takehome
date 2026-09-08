CREATE TABLE inventory (item TEXT PRIMARY KEY, stock INTEGER NOT NULL CHECK(stock >= 0));
CREATE TABLE run_state (run_id TEXT PRIMARY KEY, generation INTEGER NOT NULL DEFAULT 0);
CREATE TABLE payments (
 vendor TEXT NOT NULL, invoice_number TEXT NOT NULL, fingerprint TEXT NOT NULL,
 payment_id TEXT NOT NULL UNIQUE, amount_usd TEXT NOT NULL, source_id TEXT NOT NULL,
 run_id TEXT NOT NULL, committed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(vendor, invoice_number)
);
