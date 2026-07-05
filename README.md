# Performance Benchmark Comparison

This repository is a publication-oriented benchmark suite for comparing modern
.NET, Java, and Go runtime performance across equivalent workloads.

The initial design targets the current stable releases verified on 2026-07-03:

| Platform | Release target | Source |
| --- | ---: | --- |
| .NET | .NET 10, latest servicing release 10.0.9 | https://dotnet.microsoft.com/en-us/download/dotnet |
| Java | JDK 26.0.1, with JDK 25 LTS as an optional lane | https://www.oracle.com/java/technologies/downloads/ |
| Go | Go 1.26.4 | https://go.dev/dl/ |

Local tool status on this machine:

| Tool | Local status |
| --- | --- |
| .NET | SDK 10.0.300 installed, host runtime 10.0.8 |
| Java | JDK 26.0.1 installed |
| Go | go1.26.4 installed |
| Python | Python 3.14.6 installed for orchestration |

## Repository Layout

| Path | Purpose |
| --- | --- |
| `docs/` | Architecture, methodology, workload, and website design notes |
| `specs/` | Machine-readable runtime and benchmark definitions |
| `schemas/` | Result JSON schema used by the runner and future website |
| `tools/benchctl/` | Neutral orchestration CLI for validation, plans, and summaries |
| `benchmarks/dotnet/` | .NET benchmark harness |
| `benchmarks/java/` | Java benchmark harness |
| `benchmarks/go/` | Go benchmark harness |
| `website/` | Website data contract and future dashboard shell |

## First Commands

```bash
make validate
make plan
make smoke
```

For the benchmark comparison intended for publication, use:

```bash
make compare-all-docker REPEAT=3
```

That command is the canonical .NET vs Java vs Go comparison path. It runs the
benchmark lanes in Linux containers, produces normalized results, compares the
platforms, and writes an HTML report.

Useful shortcuts:

```bash
make help
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
make compare-cache-smoke
make compare-cache
make docker-web-build
make compare-web-docker-smoke
make compare-web-docker
make compare-all-docker-smoke
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

`make smoke` runs the full pipeline in short verification mode: run, normalize,
and summarize. `make compare-smoke` does the same but prints the .NET/Java/Go
comparison table. `make full`, `make compare`, and `make compare-all` run
without smoke-mode reductions.

## Which Command Should I Use?

| Goal | Command |
| --- | --- |
| Publication-grade Linux/Docker comparison | `make compare-all-docker REPEAT=3` |
| Full Linux/Docker preflight check | `make compare-all-docker-smoke` |
| Quick Linux/Docker API verification | `make compare-web-docker-smoke` |
| Full Docker web/API-only comparison | `make compare-web-docker REPEAT=3` |
| Host-machine full comparison | `make compare-all` |
| Regenerate latest HTML report | `make report-latest` |
| Publish report as a Gist | `make publish-report-gist RUN_ID=<run-id>` |

Use `make compare-all-docker` when comparing languages/frameworks for typical
Linux container deployment. Use `make compare-all` only when you specifically
want host-machine results.
Use `make compare-all-docker-smoke` first when you want to validate the full
Docker/Linux path before spending hours on a publication run.

## Recommended Publication Run

For publication-style results, use the Docker-backed full comparison. This runs
the .NET, Java, and Go benchmark lanes inside Linux containers, generates the
comparison, and writes the HTML report:

```bash
make compare-all-docker REPEAT=3
make db-down
make redis-down
```

For a quick preflight of the same Docker/Linux benchmark surface, run:

```bash
make compare-all-docker-smoke
```

This uses smoke-mode benchmark durations and iterations, but still builds the
Docker images, starts Postgres and Redis, runs the micro, web, DB, cache, and
gRPC lanes, and writes an HTML report.

`compare-all-docker` uses dedicated defaults of `DB_PORT=56543` and
`REDIS_PORT=56380` so it does not collide with common local Postgres and Redis
ports. You can still override them explicitly when needed:

```bash
make compare-all-docker DB_PORT=56643 REDIS_PORT=56480 REPEAT=3
```

The generated report is written to `results/reports/<RUN_ID>.html`. The report
metadata will show `Docker / Linux` when the benchmark lanes ran in Linux
containers. Results are categorized in the HTML by workload type, including CPU,
data, collections, Web API, serialization, database, cache, and RPC.

Docker API runs include an extra `.NET Tuned` lane. It keeps the baseline
`.NET` and `.NET PGO` lanes intact, then adds source-generated JSON handling and
runtime/container settings commonly used when investigating ASP.NET Core
throughput: Tiered PGO, no ReadyToRun image, quick loop JIT, server GC, and no
Kestrel server header.

Docker coverage for `compare-all-docker`:

| Workload family | Docker/Linux? | Notes |
| --- | --- | --- |
| .NET microbenchmarks | Yes | Runs BenchmarkDotNet inside the .NET SDK container |
| Java microbenchmarks | Yes | Runs JMH inside the Maven/JDK container |
| Go microbenchmarks | Yes | Runs `go test -bench` inside the Go SDK container |
| Web API benchmarks | Yes | Starts each API server lane in a Linux container |
| Postgres DB benchmarks | Yes | API servers and Postgres share a Docker network |
| Redis cache benchmarks | Yes | API servers and Redis share a Docker network |
| gRPC benchmarks | Yes | Starts real gRPC servers in Linux containers |

Use `make compare-all` only when you intentionally want the host-run version of
the full suite on the local machine.

For the complete host-run benchmark suite, including micro, web API,
gRPC/protobuf, serialization, fan-out, JSON request processing, Postgres-backed
DB benchmarks, and Redis cache benchmarks:

```bash
make compare-all DB_PORT=56543 REDIS_PORT=56379 REPEAT=3
make db-down
make redis-down
```

`DB_PORT` is only needed when the default `55432` is already allocated.
`REDIS_PORT` is only needed when the default `56379` is already allocated.
`REPEAT` controls repeated web/API measurements.

Web API benchmark shortcuts:

```bash
make web-smoke
make compare-web-smoke
make web
make compare-web
make compare-web-docker-smoke
make compare-web-docker
make compare-db-smoke
make compare-db
make compare-cache-smoke
make compare-cache
make grpc-smoke
make compare-grpc-smoke
make grpc
make compare-grpc
```

The web API targets start equivalent local HTTP servers for .NET, Java, and Go,
drive load with the built-in runner, then normalize throughput and latency
results.

Use `REPEAT=3` or higher on web targets to collect repeated measurements and
average them during comparison:

```bash
make compare-web-docker REPEAT=3
```

Generate a standalone HTML report for the latest normalized run:

```bash
make report-latest
```

Publish a report as a GitHub Gist and print a rendered preview URL:

```bash
make publish-report-gist RUN_ID=local-20260703T181021Z
make publish-report-gist REPORT=results/reports/local-20260703T181021Z.html
```

The target requires an authenticated GitHub CLI session from `gh auth login`.

Use `make compare-web-docker-smoke` for a quick Linux/container verification
run. Use `make compare-web-docker` when you want only the Docker-backed web
results for review. Use `make compare-all-docker` for the full suite with
microbenchmark, web, DB, cache, and gRPC lanes running inside Linux containers.
Use `make dotnet-tfb-validate` for an isolated, bounded validation pass of the
TechEmpower-style .NET HTTP lane before committing to a multi-hour full run.

The web profile also includes extra diagnostic lanes:

- `dotnet-pgo`: .NET with `DOTNET_TieredPGO=1` and `DOTNET_ReadyToRun=0`.
- `dotnet-tuned`: Docker-only .NET API lane with source-generated JSON, Tiered
  PGO, no ReadyToRun, quick loop JIT, server GC, and Kestrel server header
  disabled.
- `dotnet-tfb`: Docker-only .NET API diagnostic lane that uses a terminal
  ASP.NET Core request delegate for hot HTTP endpoints, source-generated JSON,
  Tiered PGO, no ReadyToRun, quick loop JIT, server GC, and no Kestrel server
  header. It is intended to approximate the style of specialized
  TechEmpower-like ASP.NET Core implementations while keeping the baseline
  .NET lanes visible.
- `java`: JDK `HttpServer` baseline using a fixed thread pool.
- `java-virtual`: JDK `HttpServer` baseline using virtual threads.
- `java-spring`: Spring Boot MVC on embedded Tomcat, included as the common
  enterprise Java API baseline.
- `java-vertx`: Vert.x HTTP server lane for a maintained production Java API stack.

Additional real-work API benchmarks now include JSON request processing,
same-server HTTP fan-out, JSON/binary serialization format endpoints,
Postgres-backed lookup/page/write profiles, a Redis cache-hit profile, and a
real unary gRPC/protobuf quote endpoint.

gRPC benchmark shortcuts:

```bash
make compare-grpc-smoke
make compare-grpc REPEAT=3
```

Database benchmark shortcuts:

```bash
make compare-db-smoke
make compare-db DB_PORT=56543
make db-down
```

Cache benchmark shortcuts:

```bash
make compare-cache-smoke
make compare-cache REDIS_PORT=56379
make redis-down
```

See [docs/linux-web-runs.md](/Users/tyomidi/Source/Performance/docs/linux-web-runs.md)
for Linux/container web runs.

Language-specific full benchmark execution:

```bash
make run-dotnet
make run-java
make run-go
make run
```

## Design Principles

1. Measure language/runtime behavior, not framework convenience.
2. Use the best native benchmarking tool for each ecosystem where practical.
3. Keep benchmark inputs, workload semantics, and correctness checks shared.
4. Publish raw results, normalized summaries, machine metadata, and methodology.
5. Treat the website as a reader over immutable result artifacts.

See [docs/architecture.md](/Users/tyomidi/Source/Performance/docs/architecture.md)
,
[docs/benchmark-methodology.md](/Users/tyomidi/Source/Performance/docs/benchmark-methodology.md),
and [docs/runner.md](/Users/tyomidi/Source/Performance/docs/runner.md)
for the detailed plan.
