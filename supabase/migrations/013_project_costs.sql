-- What a project actually cost: tokens by kind and the dollar total at the model's price.
alter table projects add column if not exists cache_read_tokens bigint not null default 0;
alter table projects add column if not exists cache_write_tokens bigint not null default 0;
alter table projects add column if not exists cost_usd numeric(10, 4) not null default 0;
