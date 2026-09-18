-- PostgreSQL / Supabase schema for Email -> Quote automation (version 2).
-- Mirrors app/models.py. For a NEW database run this file in the Supabase SQL editor or via psql,
-- then set DATABASE_URL=postgresql+psycopg://... and SEED_DEMO_DATA=false.
-- Upgrading a database created from version 1? Run db/migrations/002_v2_features.sql instead.

create extension if not exists pg_trgm;

create table customers (
    id              bigserial primary key,
    name            varchar(200) not null,
    company         varchar(200),
    email           varchar(320) not null unique,
    email_domain    varchar(255) not null,
    tax_region      varchar(20),
    discount_pct    numeric(5,2) not null default 0 check (discount_pct between 0 and 100),
    payment_terms   varchar(50)  not null default 'Net 30',
    billing_address text,
    is_verified     boolean      not null default true,
    is_sample       boolean,                          -- built-in sample customers are never emailed for real
    created_at      timestamptz  not null default now()
);
create index ix_customers_email_domain on customers (email_domain);

create table products (
    id          bigserial primary key,
    sku         varchar(64)  not null unique,
    name        varchar(300) not null,
    description text,
    aliases     jsonb        not null default '[]'::jsonb,
    unit        varchar(20)  not null default 'ea',
    units_per_pack integer,                 -- e.g. 100 for a box of 100; used by the unit guardrail
    base_price  numeric(12,2) not null check (base_price >= 0),
    weight_kg   numeric(10,3) not null default 0,
    active      boolean       not null default true
);
-- For large catalogues, pre-filter candidates in SQL before Python scoring:
--   select sku, name, similarity(name, :q) s from products where name % :q order by s desc limit 20;
create index ix_products_name_trgm on products using gin (name gin_trgm_ops);

create table price_tiers (
    id          bigserial primary key,
    product_id  bigint not null references products(id) on delete cascade,
    min_qty     integer not null check (min_qty > 0),
    unit_price  numeric(12,2) not null check (unit_price >= 0),
    unique (product_id, min_qty)
);

-- Negotiated prices. Used before list/tier prices; the customer's general discount is not added on top.
-- Also applies to other verified contacts with the same (non-webmail) email domain.
create table customer_prices (
    id           bigserial primary key,
    customer_id  bigint not null references customers(id) on delete cascade,
    product_id   bigint not null references products(id) on delete cascade,
    min_qty      integer not null default 1 check (min_qty > 0),
    unit_price   numeric(12,2) not null check (unit_price >= 0),
    valid_from   date,
    valid_until  date,
    note         varchar(200),
    unique (customer_id, product_id, min_qty)
);
create index ix_customer_prices_customer on customer_prices (customer_id);
create index ix_customer_prices_product on customer_prices (product_id);

-- Only used when INVENTORY_API_URL is empty; otherwise stock comes from the external system.
create table inventory (
    product_id                bigint primary key references products(id) on delete cascade,
    warehouse                 varchar(50) not null default 'MAIN',
    on_hand                   integer not null default 0,
    lead_time_days_in_stock   integer not null default 2,
    lead_time_days_backorder  integer not null default 21
);

create table tax_rates (
    region  varchar(20) primary key,
    label   varchar(100) not null,
    rate    numeric(6,4) not null check (rate >= 0)
);

create table quotes (
    id                    bigserial primary key,
    number                varchar(30) unique,
    status                varchar(30) not null default 'received'
                          check (status in ('received','ignored','pending_approval','approved','sending','rejected','sent')),
    customer_id           bigint references customers(id),
    revision_of_id        bigint references quotes(id),   -- follow-up on an approved/sent quote creates a revision
    revision              integer,                        -- 1, 2, ... for Q-...-R1, R2
    source_message_id     varchar(512) not null unique,   -- idempotency key
    source_thread_id      varchar(255),
    source_provider_id    varchar(255),                   -- Gmail / Graph message id, for threaded replies
    source_from           varchar(320) not null,
    source_subject        varchar(998) not null default '',
    source_body           text not null default '',
    forwarded_by          varchar(320),
    attachments           jsonb,                          -- what was read from each attachment
    extraction            jsonb,                          -- raw structured output, for audit / evals
    currency              char(3) not null default 'USD',
    subtotal              numeric(12,2) not null default 0,
    discount_total        numeric(12,2) not null default 0,
    shipping_total        numeric(12,2) not null default 0,
    shipping_manual       boolean not null default false,
    shipping_service      varchar(100),
    shipping_source       varchar(20),                    -- rules | api | rules_fallback | manual
    shipping_transit_days integer,
    tax_rate              numeric(6,4) not null default 0,
    tax_total             numeric(12,2) not null default 0,
    total                 numeric(12,2) not null default 0,
    valid_until           date,
    requested_delivery    varchar(200),
    shipping_address      text,
    customer_notes        text,
    internal_notes        text,
    cover_message         text,
    pdf_path              varchar(500),
    approved_by           varchar(200),
    approved_at           timestamptz,
    rejection_reason      text,
    send_claim_token      varchar(64),                    -- prevents sending the same quote twice
    send_claimed_at       timestamptz,
    sent_at               timestamptz,
    created_at            timestamptz not null default now(),
    updated_at            timestamptz not null default now()
);
create index ix_quotes_status on quotes (status, created_at desc);

create table quote_lines (
    id                bigserial primary key,
    quote_id          bigint not null references quotes(id) on delete cascade,
    position          integer not null,
    product_id        bigint references products(id),
    requested_text    text not null default '',
    requested_sku     varchar(64),
    sku               varchar(64),
    description       text not null default '',
    unit              varchar(20),
    quantity          numeric(12,3) not null check (quantity > 0),
    unit_price        numeric(12,2) not null default 0,
    price_overridden  boolean not null default false,
    price_source      varchar(20),                        -- customer_price | list_price | manual
    discount_pct      numeric(5,2) not null default 0,
    line_total        numeric(12,2) not null default 0,
    weight_kg         numeric(10,3) not null default 0,
    match_method      varchar(20),
    match_confidence  double precision,
    alternatives      jsonb not null default '[]'::jsonb,
    stock_status      varchar(20),
    on_hand           integer,
    lead_time_days    integer,
    needs_review      boolean not null default false,
    review_reason     text
);
create index ix_quote_lines_quote on quote_lines (quote_id);

create table quote_events (
    id        bigserial primary key,
    quote_id  bigint not null references quotes(id) on delete cascade,
    at        timestamptz not null default now(),
    actor     varchar(200) not null,
    event     varchar(50) not null,
    detail    jsonb
);
create index ix_quote_events_quote on quote_events (quote_id);

-- Customer follow-up emails in a quote's conversation (the original request is stored on the quote).
create table quote_messages (
    id           bigserial primary key,
    quote_id     bigint not null references quotes(id) on delete cascade,
    message_id   varchar(512) not null unique,
    received_at  timestamptz not null default now(),
    from_email   varchar(320) not null,
    from_name    varchar(200),
    subject      varchar(998) not null default '',
    body         text not null default '',
    attachments  jsonb,
    summary      text,
    outcome      varchar(30) not null,                 -- updated | revision_created | no_changes
    changes      jsonb
);
create index ix_quote_messages_quote on quote_messages (quote_id);
alter table quote_messages enable row level security;

-- Supabase: the API connects with a service role. Enable RLS so the anon key cannot read this data.
alter table customers       enable row level security;
alter table customer_prices enable row level security;
alter table quotes          enable row level security;
alter table quote_lines     enable row level security;
alter table quote_events    enable row level security;
