# Benchmark Methodology

This suite should be defensible before it is fast.

## Runtime Policy

Use current stable releases for the main comparison lane:

- .NET 10 latest servicing release.
- JDK 26 latest feature release.
- Go 1.26 latest patch release.

Also keep optional lanes for long-term support comparisons:

- Java 25 LTS.
- Older .NET LTS releases only when answering compatibility questions.

Preview releases should not be part of the default published comparison.

## Machine Control

Published runs should capture:

- CPU model, core count, architecture, and frequency policy.
- RAM size and memory pressure.
- OS name, version, and kernel.
- Runtime versions and compiler versions.
- Git commit, dirty status, benchmark spec checksum, and runner version.
- Power mode and thermal state where available.

Preferred dedicated-run settings:

- close heavy background applications;
- plug in laptop machines;
- disable automatic OS updates during the run;
- run each platform in alternating order to reduce time drift;
- repeat complete suites at least 5 times.

## Statistics

For each benchmark/platform pair, publish:

- mean;
- median;
- standard deviation;
- min/max;
- p95 where the harness exposes distributions;
- operations per second where useful;
- allocation bytes/op where available;
- confidence interval;
- sample count.

Use geometric means only inside clearly scoped benchmark groups. Do not use a
single grand total as the headline.

## Warmup

JIT runtimes need warmup. The default policy is:

| Platform | Warmup |
| --- | --- |
| .NET | BenchmarkDotNet managed warmup |
| Java | JMH managed warmup |
| Go | `testing.B` calibration and repeated process runs |

Cold-start benchmarks must be separate from steady-state benchmarks.

Web API benchmarks also run an explicit HTTP warmup phase before measurement.
Smoke mode uses a short warmup for pipeline validation only; full web runs use
the configured warmup and duration from `specs/benchmarks.json`.

## Correctness

Every workload needs a deterministic correctness check. For example:

- hash workloads compare a final digest;
- parser workloads compare parsed field counts and selected values;
- sort workloads compare checksum and ordering;
- numeric workloads compare within a fixed tolerance.

Timing an incorrect implementation invalidates the result.

## Publication Rules

Each published comparison should include:

1. Runtime version table.
2. Machine metadata.
3. Benchmark catalog and workload sizes.
4. Raw result artifact links.
5. Methodology caveats.
6. Summary charts with confidence intervals.
7. Changelog since the previous run.

## Web API Rules

Web API comparisons must separate endpoint behavior:

- plaintext response throughput;
- static/prebuilt JSON response throughput;
- per-request JSON serialization throughput.

Published web runs should use compiled server artifacts, a compiled load
generator, concurrency sweeps, and warmup. Prefer Docker-backed Linux runs for
publication review when a dedicated Linux host is not available. Local smoke
runs are useful for catching harness regressions, but they are not publication
evidence.

Diagnostic lanes are allowed when clearly labeled. The current web profile uses
`dotnet-pgo` to test dynamic PGO/no-ReadyToRun behavior, `java-virtual` to
separate fixed-thread and virtual-thread JDK HTTP server behavior, and
`java-vertx` to compare the JDK baseline against a maintained production Java
HTTP stack.
Docker API runs also include `dotnet-tuned`, a clearly labeled .NET lane that
adds source-generated JSON handling plus runtime/container settings commonly
used for ASP.NET Core throughput investigation. Keep the baseline `.NET` and
`.NET PGO` lanes in reports so the tuned lane does not hide default behavior.

## Common Pitfalls

- Comparing optimized Go code against idiomatic but allocation-heavy managed code.
- Measuring JSON libraries instead of runtime behavior without stating that scope.
- Letting Java and .NET warm up while Go only runs once, or the reverse.
- Publishing CI machine results as if they are stable lab results.
- Mixing startup and throughput numbers in one ranking.
