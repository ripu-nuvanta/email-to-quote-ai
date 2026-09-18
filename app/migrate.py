"""Keep an existing database usable after an upgrade.

create_all() adds new tables but never new columns, so a quotes.db from an older version would break.
This adds any missing *nullable* columns. It is deliberately small; for a production Postgres/Supabase
database apply the SQL files in db/migrations instead.
"""

from __future__ import annotations

from sqlalchemy import Engine, MetaData, inspect, text


def ensure_schema(engine: Engine, metadata: MetaData) -> list[str]:
    metadata.create_all(engine)
    inspector = inspect(engine)
    quote = engine.dialect.identifier_preparer.quote
    added: list[str] = []
    with engine.begin() as conn:
        for table in metadata.sorted_tables:
            existing = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                if not column.nullable:
                    raise RuntimeError(
                        f"Database is missing required column {table.name}.{column.name}; apply db/migrations first"
                    )
                ddl = column.type.compile(dialect=engine.dialect)
                conn.execute(text(f"ALTER TABLE {quote(table.name)} ADD COLUMN {quote(column.name)} {ddl}"))
                added.append(f"{table.name}.{column.name}")
    return added
