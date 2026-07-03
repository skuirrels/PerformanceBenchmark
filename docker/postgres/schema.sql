drop table if exists order_writes;
drop table if exists orders;

create table orders (
  id integer primary key,
  customer_id text not null,
  total_cents integer not null,
  status text not null,
  created_at timestamptz not null default now()
);

create table order_writes (
  id bigint generated always as identity primary key,
  customer_id text not null,
  total_cents integer not null,
  status text not null,
  created_at timestamptz not null default now()
);

insert into orders (id, customer_id, total_cents, status)
select
  value,
  'customer-' || (value % 250),
  1000 + (value % 50000),
  case when value = 42 then 'ready' else 'open' end
from generate_series(1, 100000) as value;

create index ix_orders_customer_id_id on orders (customer_id, id);

analyze orders;
