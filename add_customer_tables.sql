-- Migration: Add customer_emails and customer_phones tables

CREATE TABLE IF NOT EXISTS customer_emails (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER REFERENCES customers(id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL,
    is_primary BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS customer_phones (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER REFERENCES customers(id) ON DELETE CASCADE,
    phone VARCHAR(50) NOT NULL,
    is_primary BOOLEAN DEFAULT FALSE
);
