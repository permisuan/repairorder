-- Initialize database schema for RepairOrder system

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'tech',
    pay_rate DECIMAL(10,2) DEFAULT 0,
    tech_level VARCHAR(50),
    employee_number INTEGER,
    is_active BOOLEAN DEFAULT TRUE,
    hire_date DATE,
    status_history TEXT
);

-- Customers table
CREATE TABLE IF NOT EXISTS customers (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    billing_address TEXT,
    contact_info TEXT
);

-- Customer emails (one-to-many)
CREATE TABLE IF NOT EXISTS customer_emails (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER REFERENCES customers(id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL,
    is_primary BOOLEAN DEFAULT FALSE
);

-- Customer phones (one-to-many)
CREATE TABLE IF NOT EXISTS customer_phones (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER REFERENCES customers(id) ON DELETE CASCADE,
    phone VARCHAR(50) NOT NULL,
    contact_name VARCHAR(100),
    extension VARCHAR(20),
    is_primary BOOLEAN DEFAULT FALSE
);

-- Vehicles table
CREATE TABLE IF NOT EXISTS vehicles (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER REFERENCES customers(id),
    vin VARCHAR(17),
    unit_number VARCHAR(50),
    year INTEGER,
    make VARCHAR(100),
    model VARCHAR(100),
    engine_type VARCHAR(100),
    license_plate VARCHAR(20),
    mileage INTEGER,
    engine_hours DECIMAL(10,2)
);

-- Repair Orders table
CREATE TABLE IF NOT EXISTS repair_orders (
    id SERIAL PRIMARY KEY,
    vehicle_id INTEGER REFERENCES vehicles(id),
    ro_number VARCHAR(50) UNIQUE,
    assigned_tech_id INTEGER REFERENCES users(id),
    status VARCHAR(50) DEFAULT 'Open',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    tech_notes TEXT,
    is_warranty BOOLEAN DEFAULT FALSE
);

-- Job Lines table
CREATE TABLE IF NOT EXISTS job_lines (
    id SERIAL PRIMARY KEY,
    ro_id INTEGER REFERENCES repair_orders(id),
    title VARCHAR(255) NOT NULL,
    notes TEXT
);

-- Labor Lines table (time tracking)
CREATE TABLE IF NOT EXISTS labor_lines (
    id SERIAL PRIMARY KEY,
    job_line_id INTEGER REFERENCES job_lines(id),
    tech_id VARCHAR(100),
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    duration_hours DECIMAL(10,2),
    notes TEXT
);

-- Shop Settings table
CREATE TABLE IF NOT EXISTS shop_settings (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255),
    address TEXT,
    phone VARCHAR(50),
    labor_rate DECIMAL(10,2) DEFAULT 0
);

-- RO Number Sequence
CREATE SEQUENCE IF NOT EXISTS ro_sequence START 1000;

-- Insert default shop settings
INSERT INTO shop_settings (id, name, address, phone, labor_rate)
VALUES (1, 'My Repair Shop', '', '', 85.00)
ON CONFLICT (id) DO NOTHING;

-- Create a default admin user (password: admin123)
-- The hash is bcrypt for 'admin123'
INSERT INTO users (username, password_hash, role, employee_number, is_active)
VALUES ('admin', '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/X4PQ9D.fQQOvqKWXa', 'admin', 3001, TRUE)
ON CONFLICT (username) DO NOTHING;
