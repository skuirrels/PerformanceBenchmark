#!/usr/bin/env python3
import argparse
import json
import math
import os
import platform
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_SPEC = ROOT / "specs" / "benchmarks.json"
RUNTIME_POLICY = ROOT / "specs" / "runtime-policy.json"
RESULTS_DIR = ROOT / "results"
PLATFORMS = ("dotnet", "java", "go")
GO_BENCHMARK_PATTERN = re.compile(
    r"^(Benchmark\S+)-\d+\s+\d+\s+([0-9.]+)\s+(\S+)(?:\s+([0-9.]+)\s+B/op)?(?:\s+([0-9.]+)\s+allocs/op)?"
)


@dataclass(frozen=True)
class CommandResult:
    platform: str
    benchmark_id: str
    command: str
    stdout_path: Path
    exit_code: int


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_specs() -> int:
    errors: list[str] = []

    runtime_policy = load_json(RUNTIME_POLICY)
    benchmarks = load_json(BENCHMARK_SPEC)

    if runtime_policy.get("verifiedDate") is None:
        errors.append("runtime-policy.json must include verifiedDate")

    main_lanes = runtime_policy.get("mainLanes", {})
    for platform_name in PLATFORMS:
        if platform_name not in main_lanes:
            errors.append(f"missing runtime policy for {platform_name}")

    ids: set[str] = set()
    for benchmark in benchmarks.get("benchmarks", []):
        benchmark_id = benchmark.get("id")
        if not benchmark_id:
            errors.append("benchmark is missing id")
            continue
        if benchmark_id in ids:
            errors.append(f"duplicate benchmark id: {benchmark_id}")
        ids.add(benchmark_id)

        commands = benchmark.get("commands", {})
        for platform_name in PLATFORMS:
            if platform_name not in commands:
                errors.append(f"{benchmark_id} is missing {platform_name} command")

        if "correctness" not in benchmark:
            errors.append(f"{benchmark_id} is missing correctness definition")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(f"validated {len(ids)} benchmark definitions")
    return 0


def print_plan(platform_filter: str | None) -> int:
    benchmarks = load_json(BENCHMARK_SPEC)["benchmarks"]
    platforms = [platform_filter] if platform_filter else list(PLATFORMS)

    for benchmark in benchmarks:
        print(f"{benchmark['id']} - {benchmark['title']}")
        for platform_name in platforms:
            command = benchmark["commands"].get(platform_name)
            if command:
                print(f"  {platform_name}: {command}")
    return 0


def collect_environment() -> dict:
    return {
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "os": platform.platform(),
        "arch": platform.machine(),
        "cpu": cpu_name(),
        "memoryBytes": memory_bytes(),
        "python": sys.version.split()[0],
        "tools": {
            "dotnet": version_command(["dotnet", "--version"]),
            "java": version_command(["java", "-version"]),
            "maven": version_command(["mvn", "-version"]),
            "go": version_command(["go", "version"])
        }
    }


def print_env() -> int:
    metadata = collect_environment()
    print(json.dumps(metadata, indent=2))
    return 0


def cpu_name() -> str:
    if sys.platform == "darwin":
        value = version_command(["sysctl", "-n", "machdep.cpu.brand_string"])
        if value and value != "not installed" and not value.startswith("sysctl:"):
            return value
    return platform.processor() or platform.machine() or "unknown"


def memory_bytes() -> int | None:
    if sys.platform == "darwin":
        value = version_command(["sysctl", "-n", "hw.memsize"])
        return int(value) if value.isdigit() else None
    return None


def version_command(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except FileNotFoundError:
        return "not installed"

    output = completed.stdout.strip()
    if not output:
        return f"exit {completed.returncode}"
    return output.splitlines()[0]


def make_run_id() -> str:
    return "local-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def selected_benchmarks(benchmark_id: str | None) -> list[dict]:
    benchmarks = load_json(BENCHMARK_SPEC)["benchmarks"]
    if benchmark_id is None:
        return benchmarks

    selected = [benchmark for benchmark in benchmarks if benchmark["id"] == benchmark_id]
    if not selected:
        raise ValueError(f"unknown benchmark id: {benchmark_id}")
    return selected


def benchmark_lookup() -> dict[str, dict]:
    return {benchmark["id"]: benchmark for benchmark in load_json(BENCHMARK_SPEC)["benchmarks"]}


def expand_command(command: str, run_id: str, platform_name: str, smoke: bool) -> str:
    maven_repo = str(ROOT / ".cache" / "m2")
    go_cache = str(ROOT / ".cache" / "go-build")
    replacements = {
        "RUN_ID": run_id,
        "MAVEN_REPO": maven_repo,
        "GOCACHE": go_cache,
        "JMH_ARGS": "-wi 1 -i 1 -f 1" if smoke else "",
        "BDN_ARGS": "--job short --iterationCount 1 --warmupCount 1" if smoke else ""
    }

    expanded = command
    for key, value in replacements.items():
        expanded = expanded.replace("${" + key + "}", value)

    if smoke and platform_name == "go":
        expanded = expanded.replace("-count 10", "-count 1")

    return expanded


def run_benchmarks(platform_filter: str | None, benchmark_id: str | None, run_id: str | None, smoke: bool) -> int:
    actual_run_id = run_id or make_run_id()
    platforms = [platform_filter] if platform_filter else list(PLATFORMS)
    raw_dir = RESULTS_DIR / "raw" / actual_run_id
    raw_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "schemaVersion": 1,
        "runId": actual_run_id,
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "smoke": smoke,
        "environment": collect_environment(),
        "commands": []
    }

    failures = 0
    for benchmark in selected_benchmarks(benchmark_id):
        for platform_name in platforms:
            command_template = benchmark["commands"][platform_name]
            command = expand_command(command_template, actual_run_id, platform_name, smoke)
            result = run_command(actual_run_id, platform_name, benchmark["id"], command)
            metadata["commands"].append({
                "platform": result.platform,
                "benchmarkId": result.benchmark_id,
                "command": result.command,
                "stdoutPath": str(result.stdout_path.relative_to(ROOT)),
                "exitCode": result.exit_code
            })
            if result.exit_code != 0:
                failures += 1

    metadata_path = raw_dir / "run-metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"run id: {actual_run_id}")
    print(f"metadata: {metadata_path.relative_to(ROOT)}")
    return 1 if failures else 0


def run_command(run_id: str, platform_name: str, benchmark_id: str, command: str) -> CommandResult:
    platform_dir = RESULTS_DIR / "raw" / run_id / platform_name
    platform_dir.mkdir(parents=True, exist_ok=True)
    safe_benchmark_id = benchmark_id.replace(".", "-")
    stdout_path = platform_dir / f"{safe_benchmark_id}.stdout.txt"

    print(f"[{platform_name}] {benchmark_id}")
    print(f"  {command}")
    completed = subprocess.run(
        command,
        cwd=ROOT,
        shell=True,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**os.environ, "RUN_ID": run_id},
    )
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    print(f"  exit={completed.returncode} output={stdout_path.relative_to(ROOT)}")
    return CommandResult(platform_name, benchmark_id, command, stdout_path, completed.returncode)


def normalize_run(run_id: str) -> int:
    raw_dir = RESULTS_DIR / "raw" / run_id
    metadata_path = raw_dir / "run-metadata.json"
    if not metadata_path.exists():
        print(f"missing raw metadata: {metadata_path}", file=sys.stderr)
        return 1

    metadata = load_json(metadata_path)
    rows: list[dict] = []
    lookup = benchmark_lookup()

    for command in metadata.get("commands", []):
        platform_name = command["platform"]
        benchmark_id = command["benchmarkId"]
        stdout_path = ROOT / command["stdoutPath"]
        if platform_name == "go":
            rows.extend(parse_go_output(stdout_path, benchmark_id, lookup[benchmark_id]))
        elif platform_name == "java":
            rows.extend(parse_jmh_output(raw_dir / "java", benchmark_id, lookup[benchmark_id]))
        elif platform_name == "dotnet":
            rows.extend(parse_benchmarkdotnet_output(raw_dir / "dotnet", benchmark_id, lookup[benchmark_id]))

    machine = {
        "os": metadata["environment"]["os"],
        "arch": metadata["environment"]["arch"],
        "cpu": metadata["environment"].get("cpu") or "unknown",
    }
    if metadata["environment"].get("memoryBytes") is not None:
        machine["memoryBytes"] = metadata["environment"]["memoryBytes"]

    normalized = {
        "schemaVersion": 1,
        "run": {
            "id": run_id,
            "startedAt": metadata["startedAt"],
            "git": git_metadata(),
            "runtimePolicy": str(RUNTIME_POLICY.relative_to(ROOT))
        },
        "machine": machine,
        "results": rows,
        "tools": metadata["environment"].get("tools", {})
    }

    output_dir = RESULTS_DIR / "normalized"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{run_id}.json"
    output_path.write_text(json.dumps(normalized, indent=2) + "\n", encoding="utf-8")

    latest_path = ROOT / "website" / "data" / "latest.json"
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    latest_path.write_text(json.dumps(normalized, indent=2) + "\n", encoding="utf-8")

    print(f"normalized {len(rows)} rows")
    print(f"output: {output_path.relative_to(ROOT)}")
    print(f"website latest: {latest_path.relative_to(ROOT)}")
    return 0


def parse_go_output(path: Path, benchmark_id: str, benchmark: dict) -> list[dict]:
    rows = []
    runtime_version = version_command(["go", "version"])
    for line in path.read_text(encoding="utf-8").splitlines():
        match = GO_BENCHMARK_PATTERN.match(line.strip())
        if not match:
            continue

        name, mean, unit, bytes_per_operation, allocs_per_operation = match.groups()
        rows.append({
            "benchmarkId": benchmark_id,
            "case": name,
            "platform": "go",
            "runtimeVersion": runtime_version,
            "profile": benchmark.get("profile"),
            "category": benchmark.get("category"),
            "unit": unit,
            "statistics": {
                "mean": float(mean),
                "median": float(mean),
                "sampleCount": 1
            },
            "allocationBytesPerOperation": float(bytes_per_operation) if bytes_per_operation else None,
            "allocationsPerOperation": float(allocs_per_operation) if allocs_per_operation else None,
            "rawOutputPath": str(path.relative_to(ROOT))
        })
    return rows


def parse_jmh_output(raw_java_dir: Path, benchmark_id: str, benchmark: dict) -> list[dict]:
    expected_files = {
        "cpu.nbody": "nbody.json",
        "data.json-serde": "json-serde.json",
        "collections.hash-map": "hash-map.json",
    }
    path = raw_java_dir / expected_files[benchmark_id]
    if not path.exists():
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    rows = []
    runtime_version = version_command(["java", "-version"])
    for item in payload:
        primary = item.get("primaryMetric", {})
        confidence = primary.get("scoreConfidence") or [None, None]
        statistics = {
            "mean": finite_number(primary.get("score"), default=0.0),
            "median": finite_number(primary.get("score"), default=0.0),
            "sampleCount": len(primary.get("rawData", [])),
        }
        add_optional_number(statistics, "stddev", primary.get("scoreError"))
        add_optional_number(statistics, "confidenceInterval95Low", confidence[0])
        add_optional_number(statistics, "confidenceInterval95High", confidence[1])
        rows.append({
            "benchmarkId": benchmark_id,
            "case": item.get("benchmark"),
            "platform": "java",
            "runtimeVersion": runtime_version,
            "profile": benchmark.get("profile"),
            "category": benchmark.get("category"),
            "size": json.dumps(item.get("params", {}), sort_keys=True) if item.get("params") else None,
            "unit": primary.get("scoreUnit", "unknown"),
            "statistics": statistics,
            "rawOutputPath": str(path.relative_to(ROOT))
        })
    return rows


def parse_benchmarkdotnet_output(raw_dotnet_dir: Path, benchmark_id: str, benchmark: dict) -> list[dict]:
    expected_files = {
        "cpu.nbody": "PerfBenchmarks.NBodyBenchmarks-report-full-compressed.json",
        "data.json-serde": "PerfBenchmarks.JsonSerdeBenchmarks-report-full-compressed.json",
        "collections.hash-map": "PerfBenchmarks.HashMapBenchmarks-report-full-compressed.json",
    }
    rows = []
    runtime_version = version_command(["dotnet", "--info"])
    path = raw_dotnet_dir / "results" / expected_files[benchmark_id]
    if not path.exists():
        return []

    payload = json.loads(path.read_text(encoding="utf-8"))
    for report in payload.get("Benchmarks", []):
        raw_statistics = report.get("Statistics") or {}
        statistics = {
            "mean": finite_number(raw_statistics.get("Mean"), default=0.0),
            "median": finite_number(raw_statistics.get("Median"), default=finite_number(raw_statistics.get("Mean"), default=0.0)),
            "sampleCount": int(raw_statistics.get("N") or 0)
        }
        add_optional_number(statistics, "stddev", raw_statistics.get("StandardDeviation"))
        add_optional_number(statistics, "min", raw_statistics.get("Min"))
        add_optional_number(statistics, "max", raw_statistics.get("Max"))

        row = {
            "benchmarkId": benchmark_id,
            "case": report.get("DisplayInfo") or report.get("FullName"),
            "platform": "dotnet",
            "runtimeVersion": runtime_version,
            "profile": benchmark.get("profile"),
            "category": benchmark.get("category"),
            "size": json.dumps(report.get("Parameters", {}), sort_keys=True) if report.get("Parameters") else None,
            "unit": "ns",
            "statistics": statistics,
            "rawOutputPath": str(path.relative_to(ROOT))
        }

        memory = report.get("Memory") or {}
        bytes_per_operation = finite_number(memory.get("BytesAllocatedPerOperation"))
        if bytes_per_operation is not None:
            row["allocationBytesPerOperation"] = bytes_per_operation

        rows.append(row)
    return rows


def git_metadata() -> dict:
    commit = version_command(["git", "rev-parse", "HEAD"])
    status = version_command(["git", "status", "--porcelain"])
    if commit.startswith("fatal:"):
        return {"commit": "not-a-git-repo", "dirty": False}
    return {"commit": commit, "dirty": bool(status)}


def finite_number(value: object, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def add_optional_number(target: dict, key: str, value: object) -> None:
    number = finite_number(value)
    if number is not None:
        target[key] = number


def summarize(path: Path) -> int:
    payload = load_json(path)
    rows = payload.get("results", [])
    if not rows:
        print("no result rows found")
        return 0

    for row in rows:
        stats = row.get("statistics", {})
        print(
            f"{row.get('benchmarkId')} {row.get('platform')} "
            f"mean={stats.get('mean')} {row.get('unit')} "
            f"samples={stats.get('sampleCount')}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="benchctl")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("validate")

    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--platform", choices=PLATFORMS)

    subparsers.add_parser("env")

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--platform", choices=PLATFORMS)
    run_parser.add_argument("--benchmark")
    run_parser.add_argument("--run-id")
    run_parser.add_argument("--smoke", action="store_true")

    normalize_parser = subparsers.add_parser("normalize")
    normalize_parser.add_argument("run_id")

    summarize_parser = subparsers.add_parser("summarize")
    summarize_parser.add_argument("path", type=Path)

    args = parser.parse_args()

    os.chdir(ROOT)

    if args.command == "validate":
        return validate_specs()
    if args.command == "plan":
        return print_plan(args.platform)
    if args.command == "env":
        return print_env()
    if args.command == "run":
        return run_benchmarks(args.platform, args.benchmark, args.run_id, args.smoke)
    if args.command == "normalize":
        return normalize_run(args.run_id)
    if args.command == "summarize":
        return summarize(args.path)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
