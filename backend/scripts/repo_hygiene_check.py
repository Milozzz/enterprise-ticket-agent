"""Fail CI when generated data, resumes, or secrets are tracked."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]


def find_violations(paths: list[str]) -> list[str]:
    violations: list[str] = []
    generated_pattern = re.compile(r"\.(db|sqlite|sqlite3|pdf)$", re.I)
    result_pattern = re.compile(r"backend/evals/results_.*\.json$", re.I)
    for path in paths:
        name = Path(path).name.lower()
        if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
            violations.append(f"tracked secret file: {path}")
        elif generated_pattern.search(path):
            violations.append(f"tracked generated/binary file: {path}")
        elif ".worktrees/" in f"/{path}" or path.startswith(".worktrees/"):
            violations.append(f"tracked worktree internals: {path}")
        elif result_pattern.search(path):
            violations.append(f"tracked historical eval result: {path}")
        elif "张昊" in path or "resume" in name or "简历" in path:
            violations.append(f"tracked resume file: {path}")
    return violations


def main() -> None:
    violations = find_violations(tracked_files())
    if violations:
        raise SystemExit("Repository hygiene gate failed:\n- " + "\n- ".join(violations))
    print("Repository hygiene gate passed")


if __name__ == "__main__":
    main()
