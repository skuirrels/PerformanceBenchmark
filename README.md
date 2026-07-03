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

Language-specific benchmark execution comes after the relevant SDKs and
benchmarking libraries are installed:

```bash
make run
python3 tools/benchctl/benchctl.py normalize <run-id>
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
