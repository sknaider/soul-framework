"""Public-release hygiene contracts."""

import re
import tomllib
from importlib.metadata import version
from pathlib import Path

import soul_framework


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_and_distribution_versions_match_project_metadata() -> None:
    project_version = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    assert soul_framework.__version__ == project_version
    assert version("soul-framework") == project_version


def test_public_source_has_no_internal_repository_path() -> None:
    """Public Python modules must not expose the private monorepo path."""
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        if "proyecto-seal" in path.read_text(encoding="utf-8"):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_github_release_job_checks_out_repository() -> None:
    """gh release must run inside the tagged repository checkout."""
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    publish_job = workflow.split("\n  publish-github:\n", maxsplit=1)[1]
    assert "actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in publish_job
    assert publish_job.index("actions/checkout@") < publish_job.index("gh release create")


def test_shipped_tree_has_no_credential_patterns() -> None:
    """Reject common live-secret forms from every text file shipped in releases."""
    patterns = {
        "credentialed_dsn": re.compile(r"postgres(?:ql)?://[^\s:/]+:[^\s@]+@"),
        "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
        "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
        "private_key": re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    }
    offenders: list[str] = []
    roots = [ROOT / "src", ROOT / "tests", ROOT / "docs", ROOT / ".github"]
    files = [ROOT / "README.md", ROOT / "pyproject.toml"]
    for root in roots:
        if root.exists():
            files.extend(path for path in root.rglob("*") if path.is_file())
    for path in files:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in patterns.items():
            if pattern.search(content):
                offenders.append(f"{label}:{path.relative_to(ROOT).as_posix()}")
    assert offenders == []
