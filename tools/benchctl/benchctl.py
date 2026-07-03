#!/usr/bin/env python3
import argparse
import html as html_lib
import http.client
import json
import math
import os
import platform
import re
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_SPEC = ROOT / "specs" / "benchmarks.json"
RUNTIME_POLICY = ROOT / "specs" / "runtime-policy.json"
RESULTS_DIR = ROOT / "results"
PLATFORMS = ("dotnet", "java", "go")
COMPARE_PLATFORM_ORDER = ("dotnet", "dotnet-pgo", "java", "java-virtual", "java-vertx", "go")
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
        web_api = benchmark.get("webApi")
        if web_api:
            server_commands = web_api.get("serverCommands", {})
            for platform_name in PLATFORMS:
                if platform_name not in server_commands:
                    errors.append(f"{benchmark_id} is missing {platform_name} web API server command")
            if not web_api.get("path"):
                errors.append(f"{benchmark_id} is missing web API path")
        else:
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

    for benchmark in benchmarks:
        platforms = selected_platforms(platform_filter, benchmark)
        print(f"{benchmark['id']} - {benchmark['title']}")
        for platform_name in platforms:
            if "webApi" in benchmark:
                command = benchmark["webApi"].get("serverCommands", {}).get(platform_name)
            else:
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


def runner_environment() -> dict[str, str]:
    return {
        **os.environ,
        "GOCACHE": str(ROOT / ".cache" / "go-build"),
        "GOMODCACHE": str(ROOT / ".cache" / "go-mod"),
    }


def make_run_id() -> str:
    return "local-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def selected_benchmarks(benchmark_id: str | None, profile: str | None) -> list[dict]:
    benchmarks = load_json(BENCHMARK_SPEC)["benchmarks"]
    if benchmark_id is None and profile is None:
        return benchmarks

    selected = benchmarks
    if benchmark_id is not None:
        selected = [benchmark for benchmark in selected if benchmark["id"] == benchmark_id]
    if profile is not None:
        selected = [benchmark for benchmark in selected if benchmark.get("profile") == profile]
    if not selected:
        raise ValueError(f"no benchmarks matched benchmark={benchmark_id!r} profile={profile!r}")
    return selected


def benchmark_lookup() -> dict[str, dict]:
    return {benchmark["id"]: benchmark for benchmark in load_json(BENCHMARK_SPEC)["benchmarks"]}


def expand_command(command: str, run_id: str, platform_name: str, smoke: bool) -> str:
    maven_repo = str(ROOT / ".cache" / "m2")
    go_cache = str(ROOT / ".cache" / "go-build")
    go_mod_cache = str(ROOT / ".cache" / "go-mod")
    db_port = os.environ.get("PERFBENCH_DB_PORT", "55432")
    redis_port = os.environ.get("PERFBENCH_REDIS_PORT", "56379")
    replacements = {
        "RUN_ID": run_id,
        "MAVEN_REPO": maven_repo,
        "GOCACHE": go_cache,
        "GOMODCACHE": go_mod_cache,
        "DOTNET_DB": f"Host=127.0.0.1;Port={db_port};Database=perfbench;Username=perfbench;Password=perfbench;Maximum Pool Size=64",
        "GO_DB": f"postgres://perfbench:perfbench@127.0.0.1:{db_port}/perfbench?pool_max_conns=64&sslmode=disable",
        "JAVA_DB": f"jdbc:postgresql://127.0.0.1:{db_port}/perfbench?user=perfbench&password=perfbench",
        "REDIS": f"127.0.0.1:{redis_port}",
        "JMH_ARGS": "-wi 1 -i 1 -f 1 -w 1s -r 1s" if smoke else "-wi 2 -i 3 -f 1 -w 2s -r 2s",
        "BDN_ARGS": "--job short --iterationCount 1 --warmupCount 1" if smoke else ""
    }

    expanded = command
    for key, value in replacements.items():
        expanded = expanded.replace("${" + key + "}", value)

    if smoke and platform_name == "go":
        expanded = expanded.replace("-count 10", "-count 1")

    return expanded


def run_benchmarks(
    platform_filter: str | None,
    benchmark_id: str | None,
    profile: str | None,
    run_id: str | None,
    smoke: bool,
    web_runner: str,
    repeat: int,
) -> int:
    actual_run_id = run_id or make_run_id()
    raw_dir = RESULTS_DIR / "raw" / actual_run_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()

    metadata = {
        "schemaVersion": 1,
        "runId": actual_run_id,
        "startedAt": started_at.isoformat(),
        "smoke": smoke,
        "repeat": repeat,
        "environment": collect_environment(),
        "commands": []
    }

    print(f"run id: {actual_run_id}", flush=True)
    print(f"started at: {format_timestamp(started_at)}", flush=True)
    print(f"mode: {'smoke' if smoke else 'full'}, web runner: {web_runner}, repeat: {repeat}", flush=True)

    failures = 0
    failed_commands: list[dict[str, object]] = []
    for benchmark in selected_benchmarks(benchmark_id, profile):
        platforms = selected_platforms(platform_filter, benchmark)
        for platform_name in platforms:
            if "webApi" in benchmark:
                result = run_web_api_benchmark(actual_run_id, platform_name, benchmark, smoke, web_runner, repeat)
            else:
                if repeat != 1:
                    print("ERROR: --repeat is currently supported for web API benchmarks only", file=sys.stderr)
                    return 1
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
                failed_commands.append({
                    "platform": result.platform,
                    "benchmarkId": result.benchmark_id,
                    "stdoutPath": str(result.stdout_path.relative_to(ROOT)),
                    "exitCode": result.exit_code,
                })

    metadata["failures"] = failed_commands
    metadata_path = raw_dir / "run-metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"finished at: {format_timestamp(datetime.now(timezone.utc))}", flush=True)
    print(f"elapsed: {format_duration(time.monotonic() - started_monotonic)}", flush=True)
    print(f"metadata: {metadata_path.relative_to(ROOT)}")
    if failed_commands:
        print("failed lanes:", flush=True)
        for failure in failed_commands:
            print(
                f"  {failure['platform']} {failure['benchmarkId']} "
                f"exit={failure['exitCode']} log={failure['stdoutPath']}",
                flush=True,
            )
    return 1 if failures else 0


def selected_platforms(platform_filter: str | None, benchmark: dict) -> list[str]:
    if platform_filter:
        return [platform_filter]
    if "webApi" in benchmark:
        return list(benchmark["webApi"].get("serverCommands", {}).keys())
    return list(PLATFORMS)


def run_command(run_id: str, platform_name: str, benchmark_id: str, command: str) -> CommandResult:
    platform_dir = RESULTS_DIR / "raw" / run_id / platform_name
    platform_dir.mkdir(parents=True, exist_ok=True)
    safe_benchmark_id = benchmark_id.replace(".", "-")
    stdout_path = platform_dir / f"{safe_benchmark_id}.stdout.txt"

    started = time.monotonic()
    print(f"[{platform_name}] {benchmark_id} started={format_timestamp(datetime.now(timezone.utc))}", flush=True)
    print(f"  {command}")
    exit_code = run_streaming_command(command, stdout_path, {"RUN_ID": run_id})
    print(f"  exit={exit_code} elapsed={format_duration(time.monotonic() - started)} output={stdout_path.relative_to(ROOT)}", flush=True)
    return CommandResult(platform_name, benchmark_id, command, stdout_path, exit_code)


def run_streaming_command(command: str, stdout_path: Path, extra_env: dict[str, str]) -> int:
    with stdout_path.open("w", encoding="utf-8") as stdout:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={**runner_environment(), **extra_env},
        )
        assert process.stdout is not None
        for line in process.stdout:
            stdout.write(line)
            stdout.flush()
            print(f"    {line}", end="", flush=True)
        return process.wait()


def run_web_api_benchmark(run_id: str, platform_name: str, benchmark: dict, smoke: bool, web_runner: str, repeat: int) -> CommandResult:
    if web_runner == "docker":
        return run_web_api_benchmark_docker(run_id, platform_name, benchmark, smoke, repeat)

    benchmark_id = benchmark["id"]
    web_api = benchmark["webApi"]
    platform_dir = RESULTS_DIR / "raw" / run_id / platform_name
    platform_dir.mkdir(parents=True, exist_ok=True)
    safe_benchmark_id = benchmark_id.replace(".", "-")
    stdout_path = platform_dir / f"{safe_benchmark_id}.server.stdout.txt"
    result_path = platform_dir / f"{safe_benchmark_id}.json"

    port = find_free_port()
    command_template = web_api["serverCommands"][platform_name]
    command = expand_command(command_template, run_id, platform_name, smoke).replace("${PORT}", str(port))
    duration_seconds = float(web_api.get("smokeDurationSeconds" if smoke else "durationSeconds", 10))
    warmup_seconds = float(web_api.get("smokeWarmupSeconds" if smoke else "warmupSeconds", 0))
    concurrency_values = web_api.get("smokeConcurrency" if smoke else "concurrency", 16)
    if not isinstance(concurrency_values, list):
        concurrency_values = [concurrency_values]

    started = time.monotonic()
    print(f"[{platform_name}] {benchmark_id} started={format_timestamp(datetime.now(timezone.utc))}", flush=True)
    print(f"  {command}")
    with stdout_path.open("w", encoding="utf-8") as stdout:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            shell=True,
            stdout=stdout,
            stderr=subprocess.STDOUT,
            text=True,
            env={**runner_environment(), "RUN_ID": run_id, "PORT": str(port)},
            start_new_session=True,
        )
        try:
            wait_for_health(port, process)
            results = []
            for concurrency in concurrency_values:
                for repeat_index in range(1, repeat + 1):
                    sampler = ResourceSampler(lambda: sample_process_group_resource(process.pid))
                    sampler.start()
                    try:
                        result = run_go_http_load(
                            platform_name=platform_name,
                            benchmark=benchmark,
                            port=port,
                            concurrency=int(concurrency),
                            duration_seconds=duration_seconds,
                            warmup_seconds=warmup_seconds,
                            repeat_index=repeat_index,
                        )
                    finally:
                        sampler.stop()
                    result["resource"] = sampler.summary()
                    results.append(result)
            result_path.write_text(json.dumps({"results": results}, indent=2) + "\n", encoding="utf-8")
            exit_code = 0 if all(result["failures"] == 0 and result["requests"] > 0 for result in results) else 1
        except Exception as exc:
            result_path.write_text(json.dumps({
                "benchmarkId": benchmark_id,
                "platform": platform_name,
                "error": str(exc)
            }, indent=2) + "\n", encoding="utf-8")
            exit_code = 1
        finally:
            stop_process_group(process)

    print(
        f"  exit={exit_code} elapsed={format_duration(time.monotonic() - started)} "
        f"output={result_path.relative_to(ROOT)} serverLog={stdout_path.relative_to(ROOT)}",
        flush=True,
    )
    return CommandResult(platform_name, benchmark_id, command, stdout_path, exit_code)


def run_web_api_benchmark_docker(run_id: str, platform_name: str, benchmark: dict, smoke: bool, repeat: int) -> CommandResult:
    benchmark_id = benchmark["id"]
    web_api = benchmark["webApi"]
    platform_dir = RESULTS_DIR / "raw" / run_id / platform_name
    platform_dir.mkdir(parents=True, exist_ok=True)
    safe_benchmark_id = benchmark_id.replace(".", "-")
    stdout_path = platform_dir / f"{safe_benchmark_id}.server.stdout.txt"
    result_path = platform_dir / f"{safe_benchmark_id}.json"

    port = find_free_port()
    container_name = docker_container_name(run_id, platform_name, benchmark_id)
    image = docker_image_for(platform_name, web_api)
    command_args = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "-p",
        f"127.0.0.1:{port}:8080",
        image,
    ]
    command = shlex.join(command_args)
    duration_seconds = float(web_api.get("smokeDurationSeconds" if smoke else "durationSeconds", 10))
    warmup_seconds = float(web_api.get("smokeWarmupSeconds" if smoke else "warmupSeconds", 0))
    concurrency_values = web_api.get("smokeConcurrency" if smoke else "concurrency", 16)
    if not isinstance(concurrency_values, list):
        concurrency_values = [concurrency_values]

    started = time.monotonic()
    print(f"[{platform_name}] {benchmark_id} started={format_timestamp(datetime.now(timezone.utc))}", flush=True)
    print(f"  {command}")
    with stdout_path.open("w", encoding="utf-8") as stdout:
        process = subprocess.Popen(
            command_args,
            cwd=ROOT,
            stdout=stdout,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            wait_for_health(port, process)
            results = []
            for concurrency in concurrency_values:
                for repeat_index in range(1, repeat + 1):
                    sampler = ResourceSampler(lambda: sample_docker_resource(container_name))
                    sampler.start()
                    try:
                        result = run_go_http_load(
                            platform_name=platform_name,
                            benchmark=benchmark,
                            port=port,
                            concurrency=int(concurrency),
                            duration_seconds=duration_seconds,
                            warmup_seconds=warmup_seconds,
                            repeat_index=repeat_index,
                        )
                    finally:
                        sampler.stop()
                    result["resource"] = sampler.summary()
                    results.append(result)
            result_path.write_text(json.dumps({"results": results}, indent=2) + "\n", encoding="utf-8")
            exit_code = 0 if all(result["failures"] == 0 and result["requests"] > 0 for result in results) else 1
        except Exception as exc:
            result_path.write_text(json.dumps({
                "benchmarkId": benchmark_id,
                "platform": platform_name,
                "error": str(exc)
            }, indent=2) + "\n", encoding="utf-8")
            exit_code = 1
        finally:
            stop_docker_container(container_name)
            stop_process_group(process)

    print(
        f"  exit={exit_code} elapsed={format_duration(time.monotonic() - started)} "
        f"output={result_path.relative_to(ROOT)} serverLog={stdout_path.relative_to(ROOT)}",
        flush=True,
    )
    return CommandResult(platform_name, benchmark_id, command, stdout_path, exit_code)


def format_timestamp(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def format_duration(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


class ResourceSampler:
    def __init__(self, sample_once, interval_seconds: float = 1.0):
        self.sample_once = sample_once
        self.interval_seconds = interval_seconds
        self.samples: list[dict] = []
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)

    def summary(self) -> dict:
        cpu_values = [sample["cpuPercent"] for sample in self.samples if sample.get("cpuPercent") is not None]
        memory_values = [sample["memoryBytes"] for sample in self.samples if sample.get("memoryBytes") is not None]
        summary = {"sampleCount": len(self.samples)}
        if cpu_values:
            summary["cpuPercent"] = {
                "mean": sum(cpu_values) / len(cpu_values),
                "max": max(cpu_values),
            }
        if memory_values:
            summary["memoryBytes"] = {
                "mean": sum(memory_values) / len(memory_values),
                "max": max(memory_values),
            }
        return summary

    def _run(self) -> None:
        while not self.stop_event.is_set():
            sample = self.sample_once()
            if sample:
                sample["sampledAt"] = datetime.now(timezone.utc).isoformat()
                self.samples.append(sample)
            self.stop_event.wait(self.interval_seconds)


def docker_image_for(platform_name: str, web_api: dict) -> str:
    images = web_api.get("dockerImages", {})
    if platform_name in images:
        return images[platform_name]
    return f"perfapi-{platform_name}"


def docker_container_name(run_id: str, platform_name: str, benchmark_id: str) -> str:
    raw_name = f"perf-{run_id}-{platform_name}-{benchmark_id}"
    return re.sub(r"[^a-zA-Z0-9_.-]", "-", raw_name)[:120]


def stop_docker_container(container_name: str) -> None:
    subprocess.run(
        ["docker", "stop", "--time", "5", container_name],
        cwd=ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def sample_docker_resource(container_name: str) -> dict | None:
    completed = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", container_name],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except json.JSONDecodeError:
        return None
    return {
        "cpuPercent": parse_percent(payload.get("CPUPerc")),
        "memoryBytes": parse_memory_usage(payload.get("MemUsage")),
    }


def sample_process_group_resource(pid: int) -> dict | None:
    completed = subprocess.run(
        ["ps", "-o", "%cpu=", "-o", "rss=", "-g", str(pid)],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return None

    cpu_total = 0.0
    rss_total_kb = 0.0
    samples = 0
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            cpu_total += float(parts[0])
            rss_total_kb += float(parts[1])
        except ValueError:
            continue
        samples += 1
    if samples == 0:
        return None
    return {
        "cpuPercent": cpu_total,
        "memoryBytes": int(rss_total_kb * 1024),
    }


def parse_percent(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().removesuffix("%")
    return finite_number(text)


def parse_memory_usage(value: object) -> int | None:
    if value is None:
        return None
    first = str(value).split("/", 1)[0].strip()
    return parse_size_to_bytes(first)


def parse_size_to_bytes(value: str) -> int | None:
    match = re.fullmatch(r"([0-9.]+)\s*([KMGT]?i?B|B)", value.strip(), re.IGNORECASE)
    if not match:
        return None
    number = finite_number(match.group(1))
    if number is None:
        return None
    unit = match.group(2).lower()
    multipliers = {
        "b": 1,
        "kb": 1000,
        "mb": 1000**2,
        "gb": 1000**3,
        "tb": 1000**4,
        "kib": 1024,
        "mib": 1024**2,
        "gib": 1024**3,
        "tib": 1024**4,
    }
    multiplier = multipliers.get(unit)
    return int(number * multiplier) if multiplier else None


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_health(port: int, process: subprocess.Popen, timeout_seconds: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "server did not respond"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server exited before health check: {process.returncode}")
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1.0)
            connection.request("GET", "/health")
            response = connection.getresponse()
            body = response.read().decode("utf-8", errors="replace")
            connection.close()
            if response.status == 200 and body == "ok":
                return
            last_error = f"unexpected health response {response.status}: {body!r}"
        except OSError as exc:
            last_error = str(exc)
        time.sleep(0.1)
    raise RuntimeError(f"timed out waiting for server health: {last_error}")


def stop_process_group(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_go_http_load(
    platform_name: str,
    benchmark: dict,
    port: int,
    concurrency: int,
    duration_seconds: float,
    warmup_seconds: float,
    repeat_index: int,
) -> dict:
    web_api = benchmark["webApi"]
    path = web_api["path"]
    method = web_api.get("method", "GET")
    loadgen = ensure_loadgen()
    command = [
        str(loadgen),
        "--url", f"http://127.0.0.1:{port}{path}",
        "--method", method,
        "--concurrency", str(concurrency),
        "--duration", f"{duration_seconds}s",
        "--warmup", f"{warmup_seconds}s",
        "--expected-status", str(web_api.get("expectedStatus", 200)),
    ]
    if "expectedBody" in web_api:
        command.extend(["--expected-body", web_api["expectedBody"]])
    if "expectedJson" in web_api:
        command.extend(["--expected-json", json.dumps(web_api["expectedJson"], separators=(",", ":"))])
    if "requestBody" in web_api:
        command.extend(["--body", json.dumps(web_api["requestBody"], separators=(",", ":"))])
        command.extend(["--content-type", web_api.get("requestContentType", "application/json")])

    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"load generator failed: {completed.stderr.strip() or completed.stdout.strip()}")

    payload = json.loads(completed.stdout)
    payload.update({
        "benchmarkId": benchmark["id"],
        "title": benchmark["title"],
        "platform": platform_name,
        "operation": f"{method} {path}",
        "profile": benchmark.get("profile"),
        "category": benchmark.get("category"),
        "path": path,
        "configuredDurationSeconds": duration_seconds,
        "configuredWarmupSeconds": warmup_seconds,
        "repeat": repeat_index,
        "startedAt": datetime.now(timezone.utc).isoformat(),
    })
    return payload


def ensure_loadgen() -> Path:
    output = ROOT / ".cache" / "bin" / "loadgen"
    source = ROOT / "benchmarks" / "go" / "cmd" / "loadgen" / "main.go"
    if output.exists() and output.stat().st_mtime >= source.stat().st_mtime:
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        ["go", "-C", "benchmarks/go", "build", "-o", "../../.cache/bin/loadgen", "./cmd/loadgen"],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=runner_environment(),
    )
    if completed.returncode != 0:
        raise RuntimeError(f"failed to build load generator: {completed.stdout}")
    return output


def validate_http_response(status: int, body: bytes, web_api: dict) -> None:
    expected_status = int(web_api.get("expectedStatus", 200))
    if status != expected_status:
        raise RuntimeError(f"expected HTTP {expected_status}, got {status}")
    if "expectedBody" in web_api:
        actual = body.decode("utf-8", errors="replace")
        if actual != web_api["expectedBody"]:
            raise RuntimeError("unexpected response body")
    if "expectedJson" in web_api:
        actual_json = json.loads(body.decode("utf-8"))
        for key, value in web_api["expectedJson"].items():
            if actual_json.get(key) != value:
                raise RuntimeError(f"unexpected JSON field {key}")


def latency_summary(latencies: list[int]) -> dict:
    if not latencies:
        return {"sampleCount": 0}
    sorted_latencies = sorted(latencies)
    return {
        "sampleCount": len(sorted_latencies),
        "mean": sum(sorted_latencies) / len(sorted_latencies),
        "median": percentile(sorted_latencies, 50),
        "p95": percentile(sorted_latencies, 95),
        "min": sorted_latencies[0],
        "max": sorted_latencies[-1],
    }


def percentile(sorted_values: list[int], percentile_value: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (len(sorted_values) - 1) * (percentile_value / 100.0)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return float(sorted_values[int(rank)])
    weight = rank - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


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
        if "webApi" in lookup[benchmark_id]:
            rows.extend(parse_web_api_output(raw_dir / platform_name, benchmark_id, lookup[benchmark_id]))
        elif platform_name == "go":
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
        operation, parameters = go_case_metadata(name, benchmark_id)
        rows.append({
            "benchmarkId": benchmark_id,
            "case": name,
            "operation": operation,
            "platform": "go",
            "runtimeVersion": runtime_version,
            "profile": benchmark.get("profile"),
            "category": benchmark.get("category"),
            "size": json.dumps(parameters, sort_keys=True) if parameters else None,
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
        parameters = canonical_parameters(item.get("params", {}))
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
            "operation": java_operation(item.get("benchmark")),
            "platform": "java",
            "runtimeVersion": runtime_version,
            "profile": benchmark.get("profile"),
            "category": benchmark.get("category"),
            "size": json.dumps(parameters, sort_keys=True) if parameters else None,
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
        parameters = canonical_parameters(report.get("Parameters"))
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
            "operation": dotnet_operation(report.get("Method")),
            "platform": "dotnet",
            "runtimeVersion": runtime_version,
            "profile": benchmark.get("profile"),
            "category": benchmark.get("category"),
            "size": json.dumps(parameters, sort_keys=True) if parameters else None,
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


def parse_web_api_output(raw_platform_dir: Path, benchmark_id: str, benchmark: dict) -> list[dict]:
    path = raw_platform_dir / f"{benchmark_id.replace('.', '-')}.json"
    if not path.exists():
        return []
    payload = load_json(path)
    if "error" in payload:
        return []

    results = payload.get("results", [payload])
    rows = []
    for result in results:
        latency = result.get("latencyNs", {})
        latency_ms = {
            key: value / 1_000_000
            for key, value in latency.items()
            if key != "sampleCount" and isinstance(value, (int, float))
        }

        row = {
            "benchmarkId": benchmark_id,
            "case": result.get("operation"),
            "operation": result.get("operation"),
            "platform": result["platform"],
            "runtimeVersion": version_command(runtime_command(result["platform"])),
            "profile": benchmark.get("profile"),
            "category": benchmark.get("category"),
            "size": json.dumps({
                "concurrency": str(result.get("concurrency")),
                "durationSeconds": format_configured_seconds(result.get("configuredDurationSeconds", result.get("durationSeconds", 0.0))),
                "warmupSeconds": format_configured_seconds(result.get("configuredWarmupSeconds", result.get("warmupSeconds", 0.0))),
            }, sort_keys=True),
            "unit": "requests/s",
            "statistics": {
                "mean": float(result.get("throughputRequestsPerSecond", 0.0)),
                "median": float(result.get("throughputRequestsPerSecond", 0.0)),
                "sampleCount": int(result.get("requests", 0)),
            },
            "latencyMs": latency_ms,
            "failures": int(result.get("failures", 0)),
            "rawOutputPath": str(path.relative_to(ROOT)),
        }
        if result.get("repeat") is not None:
            row["repeat"] = int(result["repeat"])
        if result.get("resource"):
            row["resource"] = result["resource"]
        rows.append(row)
    return rows


def runtime_command(platform_name: str) -> list[str]:
    if platform_name.startswith("dotnet"):
        return ["dotnet", "--info"]
    if platform_name.startswith("java"):
        return ["java", "-version"]
    if platform_name == "go":
        return ["go", "version"]
    return ["true"]


def format_configured_seconds(value: object) -> str:
    number = finite_number(value, default=0.0) or 0.0
    nearest = round(number)
    if abs(number - nearest) < 0.01:
        return str(int(nearest))
    if number.is_integer():
        return str(int(number))
    return f"{number:.3f}".rstrip("0").rstrip(".")


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


def canonical_parameters(value: object) -> dict[str, str]:
    if not value:
        return {}

    if isinstance(value, dict):
        return {lower_first(str(key)): str(val) for key, val in value.items()}

    parameters: dict[str, str] = {}
    for part in str(value).split(","):
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        parameters[lower_first(key.strip())] = val.strip()
    return parameters


def go_case_metadata(name: str, benchmark_id: str) -> tuple[str, dict[str, str]]:
    simple = name.removeprefix("Benchmark")
    match = re.match(r"(.+?)(\d+)$", simple)
    prefix = match.group(1) if match else simple
    size = match.group(2) if match else ""

    if benchmark_id == "cpu.nbody":
        return "simulate", {"steps": size} if size else {}
    if benchmark_id == "data.json-serde":
        operation = lower_first(prefix.removeprefix("JsonSerde"))
        return operation, {"itemCount": size} if size else {}
    if benchmark_id == "collections.hash-map":
        return "buildAndLookup", {"itemCount": size} if size else {}
    return lower_first(prefix), {"size": size} if size else {}


def java_operation(value: object) -> str:
    if not value:
        return "unknown"
    return str(value).rsplit(".", 1)[-1]


def dotnet_operation(value: object) -> str:
    return lower_first(str(value)) if value else "unknown"


def lower_first(value: str) -> str:
    return value[:1].lower() + value[1:] if value else value


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


def resources(path: Path) -> int:
    payload = load_json(path)
    rows = [row for row in payload.get("results", []) if row.get("resource")]
    if not rows:
        print("no resource samples found")
        return 0

    for row in rows:
        resource = row["resource"]
        cpu = resource.get("cpuPercent", {})
        memory = resource.get("memoryBytes", {})
        print(
            f"{row.get('benchmarkId')} {row.get('platform')} "
            f"{format_size(row.get('size') or '{}')} "
            f"repeat={row.get('repeat', 1)} "
            f"cpu_mean={format_optional_number(cpu.get('mean'))}% "
            f"cpu_max={format_optional_number(cpu.get('max'))}% "
            f"mem_max={format_optional_bytes(memory.get('max'))} "
            f"samples={resource.get('sampleCount', 0)}"
        )
    return 0


def report(path: Path, output_path: Path | None) -> int:
    payload = load_json(path)
    rows = payload.get("results", [])
    if not rows:
        print("no result rows found")
        return 1

    output = output_path or RESULTS_DIR / "reports" / f"{payload['run']['id']}.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_html_report(payload), encoding="utf-8")
    print(f"report: {output.relative_to(ROOT) if output.is_relative_to(ROOT) else output}")
    return 0


def render_html_report(payload: dict) -> str:
    groups = comparison_groups(payload.get("results", []))
    platforms = sorted({platform for group in groups for platform in group["platforms"]}, key=platform_sort_key)
    winner_counts = {platform: 0 for platform in platforms}
    relative_scores = {platform: [] for platform in platforms}
    for group in groups:
        if group["winner"]:
            winner_counts[group["winner"]] = winner_counts.get(group["winner"], 0) + 1
        for platform, row in group["platforms"].items():
            relative_scores.setdefault(platform, []).append(row["relativeScore"])

    geomeans = {
        platform: geometric_mean(scores)
        for platform, scores in relative_scores.items()
        if scores
    }
    best_geomean_platform = max(geomeans, key=geomeans.get) if geomeans else "n/a"
    best_win_platform = max(winner_counts, key=winner_counts.get) if winner_counts else "n/a"
    run = payload.get("run", {})
    machine = payload.get("machine", {})
    tools = payload.get("tools", {})
    row_count = len(payload.get("results", []))

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(run.get('id', 'benchmark-report'))} | Performance Report</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f8fb;
      --ink: #18212f;
      --muted: #617088;
      --line: #dce3ec;
      --panel: #ffffff;
      --accent: #0f766e;
      --accent-2: #2563eb;
      --accent-3: #b45309;
      --good: #15803d;
      --warn: #b45309;
      --shadow: 0 10px 30px rgba(20, 32, 54, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header {{
      background: #111827;
      color: #f9fafb;
      padding: 34px max(24px, calc((100vw - 1180px) / 2)) 30px;
    }}
    header h1 {{
      margin: 0 0 10px;
      font-size: clamp(30px, 4vw, 52px);
      line-height: 1.02;
      letter-spacing: 0;
    }}
    header p {{ margin: 0; color: #cbd5e1; max-width: 900px; }}
    main {{
      width: min(1180px, calc(100% - 32px));
      margin: 24px auto 56px;
    }}
    section {{ margin-top: 28px; }}
    h2 {{ margin: 0 0 14px; font-size: 22px; letter-spacing: 0; }}
    .meta {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-top: -48px;
    }}
    .metric, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }}
    .metric {{ padding: 16px; min-height: 96px; }}
    .metric .label {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .08em;
    }}
    .metric .value {{
      margin-top: 8px;
      font-size: 24px;
      font-weight: 750;
      overflow-wrap: anywhere;
    }}
    .panel {{ padding: 18px; }}
    .grid-2 {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 16px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-variant-numeric: tabular-nums;
    }}
    th, td {{
      padding: 10px 9px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: middle;
    }}
    th {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }}
    tr:last-child td {{ border-bottom: 0; }}
    .num {{ text-align: right; white-space: nowrap; }}
    .platform {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-weight: 700;
    }}
    .swatch {{ width: 10px; height: 10px; border-radius: 50%; background: var(--accent-2); display: inline-block; }}
    .platform-dotnet .swatch {{ background: #2563eb; }}
    .platform-dotnet-pgo .swatch {{ background: #0f766e; }}
    .platform-java .swatch {{ background: #b45309; }}
    .platform-java-virtual .swatch {{ background: #7c3aed; }}
    .platform-java-vertx .swatch {{ background: #16a34a; }}
    .platform-go .swatch {{ background: #0891b2; }}
    .bar-track {{
      height: 10px;
      background: #e8edf4;
      border-radius: 999px;
      overflow: hidden;
      min-width: 120px;
    }}
    .bar {{ height: 100%; background: var(--accent-2); border-radius: inherit; }}
    .best {{ color: var(--good); font-weight: 750; }}
    .muted {{ color: var(--muted); }}
    .pill {{
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 2px 8px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #f8fafc;
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }}
    .chart-list {{ display: grid; gap: 12px; }}
    .chart-row {{
      display: grid;
      grid-template-columns: 150px minmax(160px, 1fr) 72px;
      gap: 12px;
      align-items: center;
    }}
    .details {{
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
    }}
    .details table {{ min-width: 980px; }}
    .section-note {{ margin-top: -8px; margin-bottom: 14px; color: var(--muted); }}
    footer {{
      width: min(1180px, calc(100% - 32px));
      margin: 0 auto 28px;
      color: var(--muted);
      font-size: 12px;
    }}
    @media (max-width: 900px) {{
      .meta, .grid-2 {{ grid-template-columns: 1fr; }}
      .meta {{ margin-top: 16px; }}
      header {{ padding-bottom: 28px; }}
      .chart-row {{ grid-template-columns: 120px minmax(120px, 1fr) 60px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Performance Benchmark Report</h1>
    <p>{escape(run.get('id', 'unknown run'))} compares .NET, Java, and Go across normalized benchmark workloads with winner summaries, throughput ratios, latency, repeat counts, and resource samples where available.</p>
  </header>
  <main>
    <section class="meta" aria-label="Run summary">
      {metric_card("Best Overall Consistency", platform_label(best_geomean_platform), f"{geomeans.get(best_geomean_platform, 0):.3f} geomean relative score")}
      {metric_card("Most Cell Wins", platform_label(best_win_platform), f"{winner_counts.get(best_win_platform, 0)} wins")}
      {metric_card("Result Rows", str(row_count), f"{len(groups)} comparison groups")}
      {metric_card("Machine", escape(machine.get("cpu", "unknown")), escape(machine.get("os", "unknown")))}
    </section>
    <section class="grid-2">
      <div class="panel">
        <h2>Winner Count</h2>
        <div class="chart-list">
          {winner_chart_html(winner_counts)}
        </div>
      </div>
      <div class="panel">
        <h2>Relative Consistency</h2>
        <p class="section-note">Geometric mean relative to the best platform in each comparable cell. Higher is better.</p>
        <div class="chart-list">
          {score_chart_html(geomeans)}
        </div>
      </div>
    </section>
    <section class="panel">
      <h2>Run Metadata</h2>
      {metadata_table_html(run, machine, tools)}
    </section>
    <section>
      <h2>Comparison Matrix</h2>
      <p class="section-note">Rows are grouped by benchmark, operation, and workload size. Repeated runs are averaged before ranking.</p>
      <div class="details">
        {comparison_table_html(groups)}
      </div>
    </section>
    <section>
      <h2>Resource Samples</h2>
      <p class="section-note">Shown when captured by Docker stats or host process sampling.</p>
      <div class="details">
        {resource_table_html(groups)}
      </div>
    </section>
  </main>
  <footer>
    Generated from {escape(run.get('id', 'unknown'))}. Git commit {escape(run.get('git', {}).get('commit', 'unknown'))}; dirty tree: {escape(str(run.get('git', {}).get('dirty', 'unknown')).lower())}.
  </footer>
</body>
</html>
"""


def comparison_groups(rows: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str, str], dict[str, list[dict]]] = {}
    for row in rows:
        if not is_comparable_unit(row.get("unit")):
            continue
        key = (
            row.get("benchmarkId") or "unknown",
            row.get("operation") or row.get("case") or "unknown",
            row.get("size") or "{}",
        )
        grouped.setdefault(key, {}).setdefault(row["platform"], []).append(row)

    groups = []
    for key in sorted(grouped):
        platforms = {
            platform_name: aggregate_repeat_rows(platform_rows)
            for platform_name, platform_rows in grouped[key].items()
        }
        unit = next(iter(platforms.values())).get("unit", "")
        higher_is_better = is_higher_better_unit(unit)
        best = (
            max(row["statistics"]["mean"] for row in platforms.values())
            if higher_is_better
            else min(row["statistics"]["mean"] for row in platforms.values())
        )
        winner = None
        for platform_name, row in platforms.items():
            mean = row["statistics"]["mean"]
            row["relativeScore"] = (mean / best if higher_is_better else best / mean) if best and mean else 0.0
            if mean == best:
                winner = platform_name
        groups.append({
            "benchmarkId": key[0],
            "operation": key[1],
            "size": key[2],
            "unit": unit,
            "platforms": platforms,
            "winner": winner,
        })
    return groups


def metric_card(label: str, value: str, detail: str) -> str:
    return f"""<div class="metric">
        <div class="label">{escape(label)}</div>
        <div class="value">{value}</div>
        <div class="muted">{detail}</div>
      </div>"""


def winner_chart_html(winner_counts: dict[str, int]) -> str:
    max_value = max(winner_counts.values()) if winner_counts else 1
    rows = []
    for platform, count in sorted(winner_counts.items(), key=lambda item: (-item[1], platform_sort_key(item[0]))):
        width = (count / max_value * 100) if max_value else 0
        rows.append(f"""<div class="chart-row">
            <div>{platform_badge(platform)}</div>
            <div class="bar-track"><div class="bar" style="width:{width:.1f}%"></div></div>
            <div class="num">{count}</div>
          </div>""")
    return "\n".join(rows)


def score_chart_html(scores: dict[str, float]) -> str:
    rows = []
    for platform, score in sorted(scores.items(), key=lambda item: (-item[1], platform_sort_key(item[0]))):
        rows.append(f"""<div class="chart-row">
            <div>{platform_badge(platform)}</div>
            <div class="bar-track"><div class="bar" style="width:{score * 100:.1f}%"></div></div>
            <div class="num">{score:.3f}</div>
          </div>""")
    return "\n".join(rows)


def metadata_table_html(run: dict, machine: dict, tools: dict) -> str:
    rows = [
        ("Run id", run.get("id", "unknown")),
        ("Started", run.get("startedAt", "unknown")),
        ("CPU", machine.get("cpu", "unknown")),
        ("OS", machine.get("os", "unknown")),
        ("Arch", machine.get("arch", "unknown")),
        ("Memory", format_optional_bytes(machine.get("memoryBytes"))),
        ("Git", run.get("git", {}).get("commit", "unknown")),
        ("Dirty tree", str(run.get("git", {}).get("dirty", "unknown")).lower()),
    ]
    for name, value in tools.items():
        rows.append((f"Tool: {name}", value))
    body = "\n".join(f"<tr><th>{escape(name)}</th><td>{escape(value)}</td></tr>" for name, value in rows)
    return f"<table><tbody>{body}</tbody></table>"


def comparison_table_html(groups: list[dict]) -> str:
    body = []
    for group in groups:
        sorted_platforms = sorted(group["platforms"].items(), key=lambda item: platform_sort_key(item[0]))
        for platform, row in sorted_platforms:
            stats = row.get("statistics", {})
            latency = row.get("latencyMs", {})
            body.append(f"""<tr>
              <td>{escape(group["benchmarkId"])}</td>
              <td>{escape(group["operation"])}</td>
              <td>{escape(format_size(group["size"]))}</td>
              <td>{platform_badge(platform)}</td>
              <td class="num">{stats.get("mean", 0):,.2f}</td>
              <td>{escape(group["unit"])}</td>
              <td class="num">{row.get("relativeScore", 0):.2f}x</td>
              <td class="num">{format_optional_number(latency.get("p95"))}</td>
              <td class="num">{row.get("repeatCount", 1)}</td>
              <td>{'<span class="best">Best</span>' if platform == group["winner"] else '<span class="muted">-</span>'}</td>
            </tr>""")
    return f"""<table>
      <thead><tr><th>Benchmark</th><th>Operation</th><th>Size</th><th>Platform</th><th class="num">Mean</th><th>Unit</th><th class="num">Relative</th><th class="num">p95 ms</th><th class="num">Repeats</th><th>Result</th></tr></thead>
      <tbody>{''.join(body)}</tbody>
    </table>"""


def resource_table_html(groups: list[dict]) -> str:
    body = []
    for group in groups:
        for platform, row in sorted(group["platforms"].items(), key=lambda item: platform_sort_key(item[0])):
            resource = row.get("resource")
            if not resource:
                continue
            cpu = resource.get("cpuPercent", {})
            memory = resource.get("memoryBytes", {})
            body.append(f"""<tr>
              <td>{escape(group["benchmarkId"])}</td>
              <td>{escape(format_size(group["size"]))}</td>
              <td>{platform_badge(platform)}</td>
              <td class="num">{format_optional_number(cpu.get("mean"))}%</td>
              <td class="num">{format_optional_number(cpu.get("max"))}%</td>
              <td class="num">{format_optional_bytes(memory.get("max"))}</td>
              <td class="num">{resource.get("sampleCount", 0)}</td>
            </tr>""")
    if not body:
        body.append('<tr><td colspan="7" class="muted">No resource samples found.</td></tr>')
    return f"""<table>
      <thead><tr><th>Benchmark</th><th>Size</th><th>Platform</th><th class="num">CPU Mean</th><th class="num">CPU Max</th><th class="num">Memory Max</th><th class="num">Samples</th></tr></thead>
      <tbody>{''.join(body)}</tbody>
    </table>"""


def platform_badge(platform: str) -> str:
    return f'<span class="platform platform-{css_class(platform)}"><span class="swatch"></span>{escape(platform_label(platform))}</span>'


def platform_label(platform: str) -> str:
    labels = {
        "dotnet": ".NET",
        "dotnet-pgo": ".NET PGO",
        "java": "Java",
        "java-virtual": "Java Virtual",
        "java-vertx": "Java Vert.x",
        "go": "Go",
    }
    return labels.get(platform, platform)


def css_class(value: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", value.lower())


def platform_sort_key(platform: str) -> tuple[int, str]:
    try:
        return (COMPARE_PLATFORM_ORDER.index(platform), platform)
    except ValueError:
        return (len(COMPARE_PLATFORM_ORDER), platform)


def geometric_mean(values: list[float]) -> float:
    safe_values = [max(value, 1e-12) for value in values if value > 0]
    if not safe_values:
        return 0.0
    return math.exp(sum(math.log(value) for value in safe_values) / len(safe_values))


def escape(value: object) -> str:
    return html_lib.escape(str(value), quote=True)


def compare(path: Path, require_all_platforms: bool) -> int:
    payload = load_json(path)
    rows = payload.get("results", [])
    if not rows:
        print("no result rows found")
        return 1

    grouped: dict[tuple[str, str, str], dict[str, list[dict]]] = {}
    for row in rows:
        if not is_comparable_unit(row.get("unit")):
            continue
        key = (
            row.get("benchmarkId") or "unknown",
            row.get("operation") or row.get("case") or "unknown",
            row.get("size") or "{}",
        )
        grouped.setdefault(key, {}).setdefault(row["platform"], []).append(row)

    if not grouped:
        print("no comparable ns/op rows found")
        return 1

    missing = 0
    for key in sorted(grouped):
        platforms = {
            platform_name: aggregate_repeat_rows(platform_rows)
            for platform_name, platform_rows in grouped[key].items()
        }
        expected_platforms = comparison_platforms(platforms)
        if require_all_platforms and set(platforms) != set(expected_platforms):
            missing += 1
            continue

        benchmark_id, operation, size = key
        print(f"\n{benchmark_id} / {operation} / {format_size(size)}")
        unit = next(iter(platforms.values())).get("unit", "")
        higher_is_better = is_higher_better_unit(unit)
        best = (
            max(row["statistics"]["mean"] for row in platforms.values())
            if higher_is_better
            else min(row["statistics"]["mean"] for row in platforms.values())
        )
        for platform_name in expected_platforms:
            row = platforms.get(platform_name)
            if row is None:
                print(f"  {platform_name:6} missing")
                continue
            mean = row["statistics"]["mean"]
            ratio = (mean / best if higher_is_better else mean / best) if best else 0
            allocation = row.get("allocationBytesPerOperation")
            allocation_text = f", {allocation:.0f} B/op" if allocation is not None else ""
            latency = row.get("latencyMs", {})
            latency_text = f", p95={latency['p95']:.2f} ms" if "p95" in latency else ""
            repeat_count = row.get("repeatCount")
            repeat_text = f", n={repeat_count}" if repeat_count and repeat_count > 1 else ""
            marker = " best" if mean == best else ""
            print(f"  {platform_name:6} {mean:12.2f} {unit:10} {ratio:6.2f}x{allocation_text}{latency_text}{repeat_text}{marker}")

    if require_all_platforms and missing:
        print(f"\nskipped {missing} incomplete comparison groups", file=sys.stderr)
    return 0


def aggregate_repeat_rows(rows: list[dict]) -> dict:
    if len(rows) == 1:
        row = dict(rows[0])
        row["repeatCount"] = 1
        return row

    row = dict(rows[0])
    means = [item["statistics"]["mean"] for item in rows]
    medians = [item["statistics"].get("median", item["statistics"]["mean"]) for item in rows]
    sample_counts = [int(item["statistics"].get("sampleCount", 0)) for item in rows]
    row["statistics"] = {
        "mean": sum(means) / len(means),
        "median": sum(medians) / len(medians),
        "sampleCount": sum(sample_counts),
        "min": min(means),
        "max": max(means),
    }
    row["repeatCount"] = len(rows)

    if any(item.get("latencyMs") for item in rows):
        row["latencyMs"] = average_nested_metric(rows, "latencyMs")
    if any(item.get("resource") for item in rows):
        row["resource"] = aggregate_resource(rows)
    row["failures"] = sum(int(item.get("failures", 0)) for item in rows)
    return row


def average_nested_metric(rows: list[dict], key: str) -> dict:
    result = {}
    metric_keys = sorted({metric for row in rows for metric in row.get(key, {})})
    for metric in metric_keys:
        values = [
            row[key][metric]
            for row in rows
            if isinstance(row.get(key, {}).get(metric), (int, float))
        ]
        if values:
            result[metric] = sum(values) / len(values)
    return result


def aggregate_resource(rows: list[dict]) -> dict:
    resources = [row["resource"] for row in rows if row.get("resource")]
    summary = {"sampleCount": sum(int(resource.get("sampleCount", 0)) for resource in resources)}
    for group in ("cpuPercent", "memoryBytes"):
        means = [
            resource[group]["mean"]
            for resource in resources
            if isinstance(resource.get(group, {}).get("mean"), (int, float))
        ]
        maxes = [
            resource[group]["max"]
            for resource in resources
            if isinstance(resource.get(group, {}).get("max"), (int, float))
        ]
        if means or maxes:
            summary[group] = {}
            if means:
                summary[group]["mean"] = sum(means) / len(means)
            if maxes:
                summary[group]["max"] = max(maxes)
    return summary


def comparison_platforms(platforms: dict[str, dict]) -> list[str]:
    ordered = [platform for platform in COMPARE_PLATFORM_ORDER if platform in platforms]
    ordered.extend(sorted(platform for platform in platforms if platform not in ordered))
    return ordered


def is_comparable_unit(unit: object) -> bool:
    return str(unit).lower() in {"ns", "ns/op", "requests/s"}


def is_higher_better_unit(unit: object) -> bool:
    return str(unit).lower() in {"requests/s"}


def format_size(value: str) -> str:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value
    if not parsed:
        return "default"
    return ", ".join(f"{key}={val}" for key, val in sorted(parsed.items()))


def format_optional_number(value: object) -> str:
    number = finite_number(value)
    return f"{number:.2f}" if number is not None else "n/a"


def format_optional_bytes(value: object) -> str:
    number = finite_number(value)
    if number is None:
        return "n/a"
    if number >= 1024**3:
        return f"{number / 1024**3:.2f}GiB"
    if number >= 1024**2:
        return f"{number / 1024**2:.2f}MiB"
    if number >= 1024:
        return f"{number / 1024:.2f}KiB"
    return f"{number:.0f}B"


def main() -> int:
    parser = argparse.ArgumentParser(prog="benchctl")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("validate")

    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--platform")

    subparsers.add_parser("env")

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--platform")
    run_parser.add_argument("--benchmark")
    run_parser.add_argument("--profile")
    run_parser.add_argument("--run-id")
    run_parser.add_argument("--smoke", action="store_true")
    run_parser.add_argument("--web-runner", choices=("host", "docker"), default="host")
    run_parser.add_argument("--repeat", type=int, default=1)

    normalize_parser = subparsers.add_parser("normalize")
    normalize_parser.add_argument("run_id")

    summarize_parser = subparsers.add_parser("summarize")
    summarize_parser.add_argument("path", type=Path)

    resources_parser = subparsers.add_parser("resources")
    resources_parser.add_argument("path", type=Path)

    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("path", type=Path)
    report_parser.add_argument("--output", type=Path)

    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("path", type=Path)
    compare_parser.add_argument("--allow-missing", action="store_true")

    args = parser.parse_args()

    os.chdir(ROOT)

    if args.command == "validate":
        return validate_specs()
    if args.command == "plan":
        return print_plan(args.platform)
    if args.command == "env":
        return print_env()
    if args.command == "run":
        if args.repeat < 1:
            print("ERROR: --repeat must be >= 1", file=sys.stderr)
            return 1
        return run_benchmarks(args.platform, args.benchmark, args.profile, args.run_id, args.smoke, args.web_runner, args.repeat)
    if args.command == "normalize":
        return normalize_run(args.run_id)
    if args.command == "summarize":
        return summarize(args.path)
    if args.command == "resources":
        return resources(args.path)
    if args.command == "report":
        return report(args.path, args.output)
    if args.command == "compare":
        return compare(args.path, require_all_platforms=not args.allow_missing)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
