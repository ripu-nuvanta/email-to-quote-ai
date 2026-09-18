-- Upgrade a Postgres / Supabase database created from the version 1 schema to version 2:
-- attachments & forwarded emails, customer price lists, shipping API details, n8n sending.
-- Safe to run more than once.

alter table quotes add column if not exists source_provider_id    varchar(255);
alter table quotes add column if not exists forwarded_by          varchar(320);
alter table quotes add column if not exists attachments           jsonb;
alter table quotes add column if not exists shipping_service      varchar(100);
alter table quotes add column if not exists shipping_source       varchar(20);
alter table quotes add column if not exists shipping_transit_days integer;
alter table quotes add column if not exists cover_message         text;
alter table quotes add column if not exists send_claim_token      varchar(64);
alter table quotes add column if not exists send_claimed_at       timestamptz;

alter table quotes drop constraint if exists quotes_status_check;
alter table quotes add constraint quotes_status_check
    check (status in ('received','ignored','pending_approval','approved','sending','rejected','sent'));

alter table quote_lines add column if not exists price_source varchar(20);

alter table customers add column if not exists is_sample boolean;

create table if not exists customer_prices (
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
create index if not exists ix_customer_prices_customer on customer_prices (customer_id);
create index if not exists ix_customer_prices_product on customer_prices (product_id);
alter table customer_prices enable row level security;
