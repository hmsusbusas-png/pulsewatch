import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
git_path = shutil.which("git")
if git_path is None:
    raise SystemExit("git is required")
GIT: str = git_path
RULES = {
    "telegram": r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b",
    "api-key": r"\b(?:sk|pk)[_-][A-Za-z0-9_-]{20,}\b",
    "jwt": r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
    "private-key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "aws": r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    "database-credentials": r"postgres(?:ql)?(?:\+asyncpg)?://[^\s/:]+:[^\s@]+@",
    "personal-path": r"(?i)(?:[A-Z]:[\\/](?:Users|Projects)[\\/]|/h[o]me/[^/\s]+)",
    "default-secret": (
        r"(?i)(?:jwt_secret|fernet_key|encryption_key)\s*"
        r"""(?::\s*str)?\s*=\s*["'][^"']{8,}["']"""
    ),
}


def findings(name: str, content: str) -> list[dict[str, object]]:
    found = []
    for number, line in enumerate(content.splitlines(), 1):
        for rule, pattern in RULES.items():
            for match in re.finditer(pattern, line):
                if rule == "database-credentials" and "${" in match.group():
                    continue
                found.append({"file": name, "line": number, "rule": rule})
    return found


def candidates() -> list[str]:
    result = subprocess.run(
        [GIT, "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return sorted(set(result.stdout.decode().strip("\0").split("\0")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--inventory", type=Path)
    args = parser.parse_args()
    paths = candidates()
    found = []
    for name in paths:
        path = ROOT / name
        if (
            any(
                re.fullmatch(
                    r"\.venv.*|__pycache__|\.mypy_cache|\.pytest_cache|\.ruff_cache|node_modules|dist|coverage|htmlcov|test-results|playwright-report|.*\.egg-info",
                    part,
                )
                for part in path.relative_to(ROOT).parts
            )
            or name.startswith("screenshots/")
            or name.endswith((".log", ".db", ".sqlite", ".dump"))
            or name.startswith("docs/internal/")
            or (path.name.startswith(".env") and path.name != ".env.example")
        ):
            found.append(
                {"file": name, "line": 0, "rule": "forbidden-publication-file"}
            )
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            continue
        found.extend(findings(name, content))
    if args.history:
        commits = subprocess.run(
            [GIT, "rev-list", "--all"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        for commit in commits:
            assert re.fullmatch(r"[0-9a-f]{40,64}", commit)
            result = subprocess.run(
                [GIT, "show", "--format=", "--no-ext-diff", "--end-of-options", commit],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            found.extend(findings(f"history:{commit[:12]}", result.stdout))
    if args.inventory:
        args.inventory.parent.mkdir(parents=True, exist_ok=True)
        args.inventory.write_text("\n".join(paths) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"files": len(paths), "findings": found, "ok": not found},
            ensure_ascii=False,
        )
    )
    raise SystemExit(bool(found))


if __name__ == "__main__":
    main()
