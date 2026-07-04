# Database Benchmarks

Database-backed API benchmarks should use one shared Postgres schema and one
database container per run. These workloads are intentionally separate from the
current low-level HTTP profile because they measure driver behavior, connection
pooling, query planning, serialization, and transaction costs together.

Planned workloads:

| ID | Endpoint | Measures |
| --- | --- | --- |
| `http.db-lookup` | `GET /db/orders/{id}` | indexed lookup plus JSON response |
| `http.db-page` | `GET /db/orders?customerId=...&limit=...` | filtered paged query plus JSON array |
| `http.db-write` | `POST /db/orders` | insert transaction plus generated id response |

Implemented now:

```bash
make compare-db-smoke
make compare-db DB_PORT=56543
make db-down
```

The current DB target seeds 100,000 deterministic `orders` rows plus an
`order_writes` table into a local Postgres container. It benchmarks indexed
lookup, filtered page reads, and generated-id writes.

Rules:

- Use Postgres for all platforms.
- Start each server with the same connection string.
- Use the same connection-pool size per platform.
- Seed deterministic rows before the benchmark starts.
- Keep database startup and migration time outside the measured window.
- Write benchmarks insert into `order_writes` so repeated benchmark runs do not
  conflict with seeded `orders` IDs.
- Truncate `order_writes` between write benchmark measurement cells so long
  Docker comparison runs do not fill Postgres table/WAL storage while still
  measuring insert transaction throughput per cell.
- Report database container resource use separately from API server resource
  use.

Implemented drivers:

- .NET: `Npgsql`
- Java: PostgreSQL JDBC
- Go: `pgx`
