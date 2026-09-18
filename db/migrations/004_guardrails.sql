-- Upgrade a Postgres / Supabase database for the quantity & unit guardrails. Safe to run more than once.
-- Fill units_per_pack for products sold in boxes/packs (e.g. 100 for a box of 100). If it is empty, the app
-- reads the pack size from names like "(box of 100)".

alter table products add column if not exists units_per_pack integer;
