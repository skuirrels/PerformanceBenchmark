#!/usr/bin/env python3
import argparse
import json
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAST_RUN_FILE = ROOT / ".cache" / "last-run-id"


def main() -> int:
    parser = argparse.ArgumentParser(prog="publish_report_gist")
    parser.add_argument("--run-id")
    parser.add_argument("--path", type=Path)
    parser.add_argument("--visibility", choices=("public", "private"), default="public")
    parser.add_argument("--desc")
    args = parser.parse_args()

    report_path = resolve_report_path(args.path, args.run_id)
    if not report_path.exists():
        generated = try_generate_report(report_path)
        if not generated:
            print(f"missing report: {display_path(report_path)}", file=sys.stderr)
            return 1

    gh = shutil.which("gh")
    if gh is None:
        print("GitHub CLI is not installed. Install gh or publish the report manually.", file=sys.stderr)
        return 1

    auth = subprocess.run(
        [gh, "auth", "status"],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if auth.returncode != 0:
        print(auth.stdout.strip(), file=sys.stderr)
        print("Run `gh auth login -h github.com`, then retry this target.", file=sys.stderr)
        return 1

    description = args.desc or f"Performance benchmark report: {report_path.stem}"
    create_command = [
        gh,
        "gist",
        "create",
        str(report_path),
        "--desc",
        description,
        f"--{args.visibility}",
    ]
    created = subprocess.run(
        create_command,
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if created.returncode != 0:
        print(created.stdout.strip(), file=sys.stderr)
        return created.returncode

    gist_url = last_output_line(created.stdout)
    gist_id = gist_url.rstrip("/").split("/")[-1]
    raw_url = gist_raw_url(gh, gist_id, report_path.name)
    if raw_url is None:
        print(f"gist: {gist_url}")
        print("preview: unable to resolve raw gist URL", file=sys.stderr)
        return 1

    preview_url = "https://htmlpreview.github.io/?" + urllib.parse.quote(raw_url, safe="")
    print(f"report: {display_path(report_path)}")
    print(f"gist: {gist_url}")
    print(f"raw: {raw_url}")
    print(f"preview: {preview_url}")
    return 0


def resolve_report_path(path: Path | None, run_id: str | None) -> Path:
    if path is not None:
        return absolute_path(path)

    actual_run_id = run_id
    if actual_run_id is None:
        if not LAST_RUN_FILE.exists():
            raise SystemExit("No previous run id found. Pass RUN_ID=... or REPORT=...")
        actual_run_id = LAST_RUN_FILE.read_text(encoding="utf-8").strip()

    return ROOT / "results" / "reports" / f"{actual_run_id}.html"


def try_generate_report(report_path: Path) -> bool:
    run_id = report_path.stem
    normalized_path = ROOT / "results" / "normalized" / f"{run_id}.json"
    if not normalized_path.exists():
        return False

    completed = subprocess.run(
        [sys.executable, "tools/benchctl/benchctl.py", "report", str(normalized_path)],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.returncode != 0:
        print(completed.stdout.strip(), file=sys.stderr)
        return False
    return report_path.exists()


def gist_raw_url(gh: str, gist_id: str, filename: str) -> str | None:
    completed = subprocess.run(
        [gh, "api", f"gists/{gist_id}"],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.returncode != 0:
        print(completed.stdout.strip(), file=sys.stderr)
        return None

    payload = json.loads(completed.stdout)
    files = payload.get("files", {})
    if filename in files:
        return files[filename].get("raw_url")
    for file_payload in files.values():
        raw_url = file_payload.get("raw_url")
        if raw_url:
            return raw_url
    return None


def absolute_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def last_output_line(value: str) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if not lines:
        raise SystemExit("gh gist create did not return a gist URL")
    return lines[-1]


if __name__ == "__main__":
    raise SystemExit(main())
