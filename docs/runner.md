# Runner

`benchctl` is the shared orchestration command for the benchmark suite.

## Commands

```bash
make help
make validate
make env
make plan
make smoke
make compare-smoke
make compare-all-smoke
make web-smoke
make compare-web-smoke
make compare-grpc-smoke
make full
make compare
make compare-all
make web
make compare-web
make grpc
make compare-grpc
make compare-db-smoke
make compare-db
make docker-web-build
make compare-web-docker-smoke
make compare-web-docker
make compare-all-docker
make smoke-java
make smoke-dotnet
make smoke-go
make summarize
make resources-latest
make report-latest
make publish-report-gist
make compare-latest
```

## Run Modes

`--smoke` shortens BenchmarkDotNet, JMH, and Go repetitions enough to verify the
pipeline. Smoke numbers are not publishable.

Full comparison runs should use `make compare-all` and should be executed on a
quiet, controlled machine. You can provide a stable run id with:

```bash
make compare-all RUN_ID=lab-m4-pro-001 DB_PORT=56543 REPEAT=3
make db-down
```

`make normalize`, `make summarize`, and `make compare-latest` use the most
recent Make-driven run by default, or an explicit run id:

```bash
make normalize RUN_ID=lab-m4-pro-001
make summarize RUN_ID=lab-m4-pro-001
make resources-latest RUN_ID=lab-m4-pro-001
make report-latest RUN_ID=lab-m4-pro-001
make compare-latest RUN_ID=lab-m4-pro-001
```

## Comparison

`make compare-all` runs all benchmark workloads across .NET, Java, and Go, then
groups normalized rows by benchmark id, operation, and workload size. Each group
prints mean `ns/op`, fastest-relative ratio, and allocation bytes per operation
where the harness reports it.

It also generates:

```text
results/reports/<run-id>.html
```

Use `make compare-smoke` when you only want to verify the comparison pipeline.
Smoke comparison output is not publishable benchmark evidence.

## Web API Benchmarks

The `web-api` profile starts each platform's local HTTP server, waits for
`/health`, drives concurrent requests, then stops the server and writes raw JSON
results.

```bash
make compare-web-smoke
make compare-web
make compare-web-docker-smoke
make compare-web-docker
```

Current web workloads:

- `http.plaintext`: `GET /plaintext`
- `http.json`: `GET /json`, static prebuilt JSON bytes
- `http.json-serde`: `GET /json-serde`, per-request JSON serialization
- `http.quote`: `POST /orders/quote`, JSON body parse, validation-style
  calculation, and JSON response
- `http.fanout`: `GET /fanout`, three same-server downstream HTTP calls and
  aggregated JSON response
- `format.json`: `GET /serialize/json`, JSON serialization format response
- `format.binary`: `GET /serialize/binary`, compact binary response
- `http.db-lookup`: `GET /db/orders/42`, Postgres indexed lookup and JSON
  projection
- `http.db-page`: `GET /db/orders?customerId=customer-42&limit=50`, filtered
  page read plus JSON response
- `http.db-write`: `POST /db/orders`, generated-id insert transaction
- `http.cache-hit`: `GET /cache/orders/42`, Redis hot-key read
- `grpc.quote`: unary `perfbench.QuoteService/Quote` over gRPC/protobuf

The normalized API rows use `requests/s` as the comparison unit and include p95
latency in milliseconds.

Repeat runs:

```bash
make compare-web-docker REPEAT=3
```

Each repeat is stored as a separate normalized row with a `repeat` index.
`benchctl compare` averages repeated rows for the same platform, endpoint, and
concurrency before ranking them, and prints `n=<repeat count>` in the comparison
table.

Web rows also include a `resource` object when sampling is available. Docker
runs collect container CPU percent and memory usage with `docker stats`; host
runs attempt to sample the server process group with `ps`.

Print resource samples for a normalized run with:

```bash
make resources-latest RUN_ID=lab-m4-pro-001
```

Generate a standalone HTML report with scorecards, winner charts, comparison
tables, and resource tables:

```bash
make report-latest RUN_ID=lab-m4-pro-001
```

Publish the generated HTML report as a GitHub Gist and print both the source
Gist URL and a rendered HTMLPreview URL:

```bash
make publish-report-gist RUN_ID=lab-m4-pro-001
make publish-report-gist REPORT=results/reports/lab-m4-pro-001.html
```

Use `GIST_VISIBILITY=private` for a private gist. The target requires `gh` to be
authenticated with `gh auth login -h github.com`.

Reports are written to:

```text
results/reports/<run-id>.html
```

## Database API Benchmarks

The `db-api` profile starts a local Postgres container, seeds deterministic
order rows, then runs lookup, page, and write endpoints through the same web
runner.

```bash
make compare-db-smoke
make compare-db DB_PORT=56543
make db-down
```

The default DB port is `55432`, but `DB_PORT` can be overridden when the port is
already allocated locally. The runner injects platform-specific connection
strings for Npgsql, JDBC PostgreSQL, and pgx.

## Cache API Benchmarks

The `cache-api` profile starts a local Redis container, seeds `order:42`, then
runs the cache endpoint through the same web runner. The endpoint supports
cache-aside fallback to Postgres when both Redis and DB are configured, but the
benchmarked path is the seeded Redis hit.

```bash
make compare-cache-smoke
make compare-cache REDIS_PORT=56379
make redis-down
```

The default Redis port is `56379`, and the runner injects `PERFBENCH_REDIS` as
`127.0.0.1:<port>`.

## gRPC API Benchmarks

The `grpc-api` profile starts real gRPC servers generated from
`proto/quote.proto`, waits for the TCP listener, drives unary protobuf calls
with the compiled Go gRPC load generator, then normalizes throughput, p95
latency, and resource samples.

```bash
make compare-grpc-smoke
make compare-grpc REPEAT=3
```

The implemented workload is `grpc.quote`, a unary quote request with the same
business shape as the HTTP JSON quote benchmark: customer id, item count, unit
price, expedited flag, calculated total, and accepted response.

The full web profile uses:

- published .NET API binaries, not `dotnet run`;
- an additional `dotnet-pgo` lane with `DOTNET_TieredPGO=1` and
  `DOTNET_ReadyToRun=0`;
- compiled Go API binaries, not `go run`;
- the shaded Java benchmark jar as the API classpath;
- a `java` lane using the JDK `HttpServer` as a simple fixed-thread baseline;
- an additional `java-virtual` lane using JDK virtual threads;
- an additional `java-vertx` lane using Vert.x as a maintained production Java HTTP stack;
- a compiled Go load generator instead of the Python runner loop;
- warmup before measurement;
- concurrency sweep at `1`, `16`, `64`, and `128`.

Smoke web runs use one short `16`-concurrency pass to keep pipeline validation
fast.

Host API runs are the default. Docker-backed runs build one image per lane and
start each API server with `docker run -p 127.0.0.1:<port>:8080`; the same
compiled Go load generator drives requests from the host and the results flow
through the same normalization and comparison path. `make compare-all-docker`
runs the full benchmark set with web, DB, cache, and gRPC API server lanes in
Linux containers; CPU/data/collection microbenchmark lanes still run as host
benchmark processes.

Long-running Make targets print a UTC start timestamp before Docker builds and
a UTC finish timestamp after HTML report generation.
`benchctl run` also prints the run id, local start time, mode, web runner, and
elapsed time for each benchmark lane.

## Output

Raw harness output:

```text
results/raw/<run-id>/<platform>/
```

Normalized website-ready output:

```text
results/normalized/<run-id>.json
website/data/latest.json
```

`results/` and generated website data are intentionally ignored by git. Published
runs should be attached or copied by the release process, not mixed with source
changes.

## Java

The Java harness uses JDK 26 and JMH. JDK 26 requires explicit annotation
processing configuration for JMH metadata generation. The Maven build creates a
shaded executable JMH jar at:

```text
benchmarks/java/target/benchmarks.jar
```

The runner executes Java benchmarks with `java -jar` so forked JMH runs receive
the full runtime classpath.
