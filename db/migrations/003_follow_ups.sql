-- Upgrade a Postgres / Supabase database to support customer follow-up emails and quote revisions.
-- Safe to run more than once.

alter table quotes add column if not exists revision_of_id bigint references quotes(id);
alter table quotes add column if not exists revision integer;

create table if not exists quote_messages (
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
    outcome      varchar(30) not null,
    changes      jsonb
);
create index if not exists ix_quote_messages_quote on quote_messages (quote_id);
alter table quote_messages enable row level security;
