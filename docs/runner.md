# Runner

`benchctl` is the shared orchestration command for the benchmark suite.

## Commands

```bash
python3 tools/benchctl/benchctl.py validate
python3 tools/benchctl/benchctl.py env
python3 tools/benchctl/benchctl.py plan
python3 tools/benchctl/benchctl.py run --smoke --run-id smoke-local
python3 tools/benchctl/benchctl.py normalize smoke-local
python3 tools/benchctl/benchctl.py summarize results/normalized/smoke-local.json
```

## Run Modes

`--smoke` shortens BenchmarkDotNet, JMH, and Go repetitions enough to verify the
pipeline. Smoke numbers are not publishable.

Full runs should omit `--smoke` and should be executed on a quiet, controlled
machine.

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

