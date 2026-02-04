-- Migration: Add contact_name and extension to customer_phones table

ALTER TABLE customer_phones 
ADD COLUMN IF NOT EXISTS contact_name VARCHAR(100),
ADD COLUMN IF NOT EXISTS extension VARCHAR(20);
