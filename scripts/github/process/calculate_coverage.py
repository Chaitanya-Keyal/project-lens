#!/usr/bin/env python3
"""Calculate test coverage at PR merge points.

For each merged PR, checks out key commits in the dedicated clone, runs
the test suite with coverage, and stores the results in
pr_assessments.coverage_evolution.

Coverage harnesses are pluggable — each language/tool gets a harness class
that knows how to detect, set up, run, and parse coverage for that ecosystem.
Currently implemented: PytestHarness (Python projects using pytest-cov).

Usage:
    python scripts/calculate_coverage.py                        # all eligible repos
    python scripts/calculate_coverage.py SeedSigner/seedsigner  # specific repo
    python scripts/calculate_coverage.py --limit 5              # process N PRs then stop
    python scripts/calculate_coverage.py --dry-run              # show what would be processed
"""
import asyncio
import json
import subprocess
import sys
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from project_lens.settings import settings
from project_lens.database import engine, async_session
from project_lens.models import Base, GithubPR, PRAssessment
from project_lens.project_config import load_project_config


# ---------------------------------------------------------------------------
# Coverage harness interface
# ---------------------------------------------------------------------------

class CoverageHarness(ABC):
    """Base class for language/tool-specific coverage harnesses.

    Each harness knows how to detect whether it applies to a repo, set up the
    environment, and run tests with coverage output in a standardized format.

    To add support for a new language or test framework:
      1. Subclass CoverageHarness
      2. Implement all abstract methods
      3. Add an instance to HARNESSES at the bottom of this section

    The standardized coverage result returned by run_coverage():
      {
        "test_passed": bool,
        "totals": {"percent_covered": float, "num_statements": int, "covered_lines": int},
        "files": {"path": {"num_statements": int, "covered_lines": int}},
      }
    On failure, return {"error": "<reason>", "test_passed": False, ...} instead.
    """

    @abstractmethod
    def detect(self, clone_path: Path) -> bool:
        """Return True if this harness can handle this repo."""

    @abstractmethod
    def setup(self, clone_path: Path) -> None:
        """One-time environment setup (create venv, install deps, build, etc.).

        Called once per repo before processing any PRs. Should be idempotent —
        if the environment already exists, return quickly.
        """

    @abstractmethod
    def prepare_checkout(self, clone_path: Path) -> None:
        """Per-checkout preparation after git checkout + submodule update.

        Called after each commit checkout. Use this for steps like compiling
        source, generating files, or rebuilding that depend on the checkout.
        """

    @abstractmethod
    def run_coverage(self, clone_path: Path) -> dict:
        """Run tests with coverage and return a standardized result dict."""

    @property
    @abstractmethod
    def source_extensions(self) -> set[str]:
        """File extensions this harness considers source code (e.g. {'.py'}).

        Used to filter which PR-changed files count toward new_code coverage.
        """


# ---------------------------------------------------------------------------
# Pytest harness (Python projects)
# ---------------------------------------------------------------------------

class PytestHarness(CoverageHarness):
    """Coverage harness for Python projects using pytest + pytest-cov."""

    @property
    def source_extensions(self) -> set[str]:
        return {".py"}

    def detect(self, clone_path: Path) -> bool:
        return (clone_path / "tests").is_dir() and (
            (clone_path / "pyproject.toml").exists()
            or (clone_path / "pytest.ini").exists()
        )

    def setup(self, clone_path: Path) -> None:
        venv_path = clone_path / ".coverage-venv"
        if (venv_path / "bin" / "python").exists():
            return

        python_bin = self._find_python()
        print(f"  Creating venv at {venv_path} ({Path(python_bin).name})...")
        subprocess.run(
            [python_bin, "-m", "venv", str(venv_path)],
            check=True,
            capture_output=True,
        )

        pip = str(venv_path / "bin" / "pip")

        def pip_install(*args: str):
            result = subprocess.run(
                [pip, "install", *args],
                capture_output=True, text=True,
                cwd=str(clone_path),
            )
            if result.returncode != 0:
                print(f"    pip install failed:\n{result.stderr[-1000:]}")
                raise RuntimeError(f"pip install {' '.join(args[-2:])} failed")

        # Project runtime deps
        project_reqs = clone_path / "requirements.txt"
        if project_reqs.exists():
            print("  Installing project dependencies from requirements.txt...")
            pip_install("-r", str(project_reqs))

        # Project itself in editable mode
        print("  Installing project in editable mode...")
        pip_install("-e", str(clone_path))

        # Test deps
        test_reqs = clone_path / "tests" / "requirements.txt"
        if test_reqs.exists():
            print("  Installing test dependencies...")
            pip_install("-r", str(test_reqs))

        # setuptools + babel for projects that compile locale catalogs
        pip_install("setuptools", "babel")

    def prepare_checkout(self, clone_path: Path) -> None:
        """Compile locale catalogs if the project uses them."""
        python = str(clone_path / ".coverage-venv" / "bin" / "python")
        setup_py = clone_path / "setup.py"
        if setup_py.exists():
            subprocess.run(
                [python, "setup.py", "compile_catalog"],
                capture_output=True,
                cwd=str(clone_path),
            )

    def run_coverage(self, clone_path: Path) -> dict:
        python = str(clone_path / ".coverage-venv" / "bin" / "python")
        cov_json_path = clone_path / "coverage.json"
        cov_json_path.unlink(missing_ok=True)

        result = subprocess.run(
            [python, "-m", "pytest",
             "--cov", "--cov-report=json",
             "--tb=no", "-q"],
            capture_output=True, text=True,
            cwd=str(clone_path),
            timeout=300,
        )

        test_passed = result.returncode == 0

        if not cov_json_path.exists():
            return {
                "error": "no_coverage_json",
                "test_passed": test_passed,
                "stderr": result.stderr[-500:] if result.stderr else None,
            }

        with open(cov_json_path) as f:
            cov_data = json.load(f)

        return {
            "test_passed": test_passed,
            "totals": cov_data.get("totals", {}),
            "files": {
                path: info.get("summary", {})
                for path, info in cov_data.get("files", {}).items()
            },
        }

    @staticmethod
    def _find_python() -> str:
        """Find a suitable Python interpreter for the venv.

        Prefers pyenv-managed versions over the system Python, since target
        projects may require a specific minor version.
        """
        pyenv_root = Path.home() / ".pyenv" / "versions"
        if pyenv_root.is_dir():
            # Use the newest 3.12.x available (widely compatible)
            for version_dir in sorted(pyenv_root.iterdir(), reverse=True):
                if version_dir.name.startswith("3.12"):
                    candidate = version_dir / "bin" / "python3.12"
                    if candidate.exists():
                        return str(candidate)
        # Fall back to system python
        return sys.executable


# ---------------------------------------------------------------------------
# Future harness stubs — uncomment and implement when needed
# ---------------------------------------------------------------------------

# class GcovHarness(CoverageHarness):
#     """Coverage harness for C/C++ projects using gcov/lcov."""
#
#     @property
#     def source_extensions(self) -> set[str]:
#         return {".c", ".h", ".cpp", ".hpp", ".cc"}
#
#     def detect(self, clone_path: Path) -> bool:
#         return (clone_path / "Makefile").exists() or (clone_path / "CMakeLists.txt").exists()
#
#     def setup(self, clone_path: Path) -> None:
#         # Verify gcov/lcov are available; configure build with coverage flags
#         ...
#
#     def prepare_checkout(self, clone_path: Path) -> None:
#         # make clean && make (with -fprofile-arcs -ftest-coverage)
#         ...
#
#     def run_coverage(self, clone_path: Path) -> dict:
#         # make test && lcov --capture && lcov --summary
#         # Parse lcov output into standardized format
#         ...


# class CargoHarness(CoverageHarness):
#     """Coverage harness for Rust projects using cargo-tarpaulin or cargo-llvm-cov."""
#
#     @property
#     def source_extensions(self) -> set[str]:
#         return {".rs"}
#
#     def detect(self, clone_path: Path) -> bool:
#         return (clone_path / "Cargo.toml").exists()
#
#     def setup(self, clone_path: Path) -> None:
#         # Verify cargo-tarpaulin is installed
#         ...
#
#     def prepare_checkout(self, clone_path: Path) -> None:
#         # cargo clean (optional)
#         ...
#
#     def run_coverage(self, clone_path: Path) -> dict:
#         # cargo tarpaulin --out Json
#         # Parse into standardized format
#         ...


# Register all available harnesses. Order matters — first match wins.
HARNESSES: list[CoverageHarness] = [
    PytestHarness(),
    # GcovHarness(),
    # CargoHarness(),
]


def detect_harness(clone_path: Path) -> CoverageHarness | None:
    """Return the first harness that matches this repo, or None."""
    for harness in HARNESSES:
        if harness.detect(clone_path):
            return harness
    return None


# ---------------------------------------------------------------------------
# Git helpers (language-agnostic)
# ---------------------------------------------------------------------------

def clone_dir_name(repo: str) -> str:
    """Convert 'SeedSigner/seedsigner' to 'SeedSigner_seedsigner'."""
    return repo.replace("/", "_")


def find_merge_commit(clone_path: Path, pr_number: int) -> str | None:
    """Find the merge commit hash for a PR in the git history."""
    result = subprocess.run(
        ["git", "log", "--all", "--oneline",
         f"--grep=Merge pull request #{pr_number} from"],
        capture_output=True, text=True,
        cwd=str(clone_path),
    )
    lines = result.stdout.strip().splitlines()
    if not lines:
        return None
    return lines[0].split()[0]


def get_current_branch(clone_path: Path) -> str:
    """Get the current branch or commit of the clone."""
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True,
        cwd=str(clone_path),
    )
    branch = result.stdout.strip()
    if branch == "HEAD":
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True,
            cwd=str(clone_path),
        )
        return result.stdout.strip()
    return branch


def checkout(clone_path: Path, ref: str) -> bool:
    """Checkout a specific ref. Returns True on success."""
    result = subprocess.run(
        ["git", "checkout", ref],
        capture_output=True, text=True,
        cwd=str(clone_path),
    )
    return result.returncode == 0


def update_submodules(clone_path: Path):
    """Initialize and update all git submodules for the current checkout."""
    subprocess.run(
        ["git", "submodule", "update", "--init", "--recursive"],
        capture_output=True,
        cwd=str(clone_path),
    )


def get_commit_date(clone_path: Path, commit_hash: str) -> str | None:
    """Get the author date of a commit as YYYY-MM-DD."""
    result = subprocess.run(
        ["git", "log", "-1", "--format=%aI", commit_hash],
        capture_output=True, text=True,
        cwd=str(clone_path),
    )
    date_str = result.stdout.strip()
    return date_str[:10] if date_str else None


def get_pr_commits(clone_path: Path, merge_hash: str) -> list[dict]:
    """Get the list of commits on the PR branch (oldest first).

    For a merge commit M: returns commits reachable from M^2 but not M^1.
    Each entry is {"hash": "<short>", "full_hash": "<full>", "date": "YYYY-MM-DD"}.
    Returns empty list if not a merge commit.
    """
    result = subprocess.run(
        ["git", "rev-parse", f"{merge_hash}^2"],
        capture_output=True, text=True,
        cwd=str(clone_path),
    )
    if result.returncode != 0:
        return []

    result = subprocess.run(
        ["git", "log", "--reverse", "--format=%h %H %aI",
         f"{merge_hash}^1..{merge_hash}^2"],
        capture_output=True, text=True,
        cwd=str(clone_path),
    )
    commits = []
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        parts = line.split()
        commits.append({
            "hash": parts[0],
            "full_hash": parts[1],
            "date": parts[2][:10],
        })
    return commits


def get_pr_changed_files(clone_path: Path, merge_hash: str) -> list[str]:
    """Get the list of files changed by a PR (diff between merge parents)."""
    result = subprocess.run(
        ["git", "rev-parse", f"{merge_hash}^2"],
        capture_output=True, text=True,
        cwd=str(clone_path),
    )
    if result.returncode != 0:
        diff_spec = f"{merge_hash}^..{merge_hash}"
    else:
        diff_spec = f"{merge_hash}^1..{merge_hash}^2"

    result = subprocess.run(
        ["git", "diff", "--name-only", diff_spec],
        capture_output=True, text=True,
        cwd=str(clone_path),
    )
    return [f for f in result.stdout.strip().splitlines() if f]


# ---------------------------------------------------------------------------
# Coverage computation (language-agnostic)
# ---------------------------------------------------------------------------

def checkout_and_run(
    clone_path: Path,
    harness: CoverageHarness,
    commit_hash: str,
) -> dict:
    """Checkout a commit, prepare the environment, and run coverage."""
    if not checkout(clone_path, commit_hash):
        return {"error": "checkout_failed", "test_passed": False}

    update_submodules(clone_path)
    harness.prepare_checkout(clone_path)
    return harness.run_coverage(clone_path)


def compute_pr_coverage_entry(
    commit_hash: str,
    commit_date: str | None,
    changed_files: list[str],
    cov_result: dict,
    harness: CoverageHarness,
    label: str = "merged",
) -> dict:
    """Build a coverage_evolution entry from raw coverage data."""
    if "error" in cov_result:
        return {
            "commit": commit_hash,
            "label": label,
            "date": commit_date,
            "error": cov_result["error"],
            "test_passed": cov_result.get("test_passed", False),
        }

    totals = cov_result["totals"]
    files_cov = cov_result["files"]

    overall_pct = totals.get("percent_covered", 0)
    overall_stmts = totals.get("num_statements", 0)
    overall_covered = totals.get("covered_lines", 0)

    # Coverage of files this PR changed, filtered by the harness's source extensions
    new_stmts = 0
    new_covered = 0
    files_untested = []
    extensions = harness.source_extensions

    for changed_file in changed_files:
        if not any(changed_file.endswith(ext) for ext in extensions):
            continue
        # Look up file in coverage report (try exact match, then suffix match)
        file_summary = files_cov.get(changed_file)
        if file_summary is None:
            for cov_path, cov_summary in files_cov.items():
                if cov_path.endswith(changed_file) or changed_file.endswith(cov_path):
                    file_summary = cov_summary
                    break
        if file_summary is None:
            continue
        stmts = file_summary.get("num_statements", 0)
        covered = file_summary.get("covered_lines", 0)
        new_stmts += stmts
        new_covered += covered
        if stmts > 0 and covered == 0:
            files_untested.append(changed_file)

    new_code_pct = round(100.0 * new_covered / new_stmts, 2) if new_stmts > 0 else None

    return {
        "commit": commit_hash,
        "label": label,
        "date": commit_date,
        "overall": round(overall_pct, 2),
        "overall_statements": overall_stmts,
        "overall_covered": overall_covered,
        "new_code": new_code_pct,
        "new_code_statements": new_stmts,
        "new_code_covered": new_covered,
        "files_untested": files_untested,
        "test_passed": cov_result["test_passed"],
    }


def self_report(entry: dict):
    """Print a one-line summary of a coverage entry."""
    if entry.get("error"):
        print(f"      {entry['label']}: ERROR {entry['error']}")
    else:
        overall = entry.get("overall", "?")
        new_code = entry.get("new_code", "N/A")
        passed = "PASS" if entry.get("test_passed") else "FAIL"
        print(f"      {entry['label']}: {passed} | overall: {overall}% | new_code: {new_code}%")


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------

async def get_merged_prs_without_coverage(repo: str | None = None) -> list[tuple[str, int]]:
    """Get (repo, pr_number) pairs for merged PRs that lack coverage data."""
    async with async_session() as session:
        q = select(GithubPR.repo, GithubPR.number, GithubPR.merged_at_gh).where(
            GithubPR.merged_at_gh.is_not(None)
        )
        if repo:
            q = q.where(GithubPR.repo == repo)
        q = q.order_by(GithubPR.merged_at_gh.desc())
        result = await session.execute(q)
        merged_prs = result.all()

        cq = select(PRAssessment.repo, PRAssessment.pr_number).where(
            PRAssessment.coverage_evolution.is_not(None)
        )
        if repo:
            cq = cq.where(PRAssessment.repo == repo)
        result = await session.execute(cq)
        has_coverage = {(r[0], r[1]) for r in result.all()}

        return [
            (pr.repo, pr.number)
            for pr in merged_prs
            if (pr.repo, pr.number) not in has_coverage
        ]


async def store_coverage(repo: str, pr_number: int, coverage_entries: list[dict]):
    """Store coverage_evolution data on the PR assessment."""
    async with async_session() as session:
        result = await session.execute(
            select(PRAssessment).where(
                PRAssessment.repo == repo,
                PRAssessment.pr_number == pr_number,
            )
        )
        assessment = result.scalar_one_or_none()

        if assessment:
            assessment.coverage_evolution = coverage_entries
            assessment.updated_at = datetime.now(timezone.utc)
        else:
            assessment = PRAssessment(
                repo=repo,
                pr_number=pr_number,
                coverage_evolution=coverage_entries,
            )
            session.add(assessment)

        await session.commit()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    repo_filter = None
    limit = None
    dry_run = False

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--limit":
            limit = int(args[i + 1])
            i += 2
        elif args[i] == "--dry-run":
            dry_run = True
            i += 1
        elif not args[i].startswith("--"):
            repo_filter = args[i]
            i += 1
        else:
            i += 1

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    config = load_project_config()
    clones_base = settings.clones_path

    # Detect which repos have a matching coverage harness
    eligible_repos: dict[str, tuple[Path, CoverageHarness]] = {}
    for rc in config.repos:
        clone_path = clones_base / clone_dir_name(rc.full_name)
        if not clone_path.exists():
            continue
        if repo_filter and rc.full_name != repo_filter:
            continue
        harness = detect_harness(clone_path)
        if harness:
            eligible_repos[rc.full_name] = (clone_path, harness)

    if not eligible_repos:
        print("No eligible repos with a recognized test suite found.")
        return

    for repo, (_, harness) in eligible_repos.items():
        print(f"  {repo} → {harness.__class__.__name__}")

    # Get PRs that need coverage
    prs_to_process = await get_merged_prs_without_coverage(repo_filter)
    prs_to_process = [(r, n) for r, n in prs_to_process if r in eligible_repos]

    if limit:
        prs_to_process = prs_to_process[:limit]

    print(f"PRs to process: {len(prs_to_process)}")

    if dry_run:
        for repo, pr_num in prs_to_process:
            print(f"  {repo}#{pr_num}")
        return

    if not prs_to_process:
        return

    # Process each repo
    for repo, (clone_path, harness) in eligible_repos.items():
        repo_prs = [(r, n) for r, n in prs_to_process if r == repo]
        if not repo_prs:
            continue

        print(f"\n=== {repo} ({len(repo_prs)} PRs, {harness.__class__.__name__}) ===")

        original_ref = get_current_branch(clone_path)
        harness.setup(clone_path)

        processed = 0
        errors = 0

        try:
            for _, pr_number in repo_prs:
                print(f"\n  PR #{pr_number}...")

                merge_hash = find_merge_commit(clone_path, pr_number)
                if not merge_hash:
                    print(f"    No merge commit found — skipping")
                    await store_coverage(repo, pr_number, [{
                        "label": "merged",
                        "error": "no_merge_commit",
                        "test_passed": False,
                    }])
                    errors += 1
                    continue

                pr_commits = get_pr_commits(clone_path, merge_hash)
                changed_files = get_pr_changed_files(clone_path, merge_hash)
                entries = []

                # Initial submission (first commit on PR branch)
                if len(pr_commits) >= 2:
                    first = pr_commits[0]
                    print(f"    initial_submission ({first['hash']})...")
                    cov_result = checkout_and_run(clone_path, harness, first["full_hash"])
                    entry = compute_pr_coverage_entry(
                        first["hash"], first["date"], changed_files,
                        cov_result, harness, label="initial_submission",
                    )
                    entries.append(entry)
                    self_report(entry)

                # Merge commit (final state)
                merge_date = get_commit_date(clone_path, merge_hash)
                print(f"    merged ({merge_hash})...")
                cov_result = checkout_and_run(clone_path, harness, merge_hash)
                entry = compute_pr_coverage_entry(
                    merge_hash, merge_date, changed_files,
                    cov_result, harness, label="merged",
                )
                entries.append(entry)
                self_report(entry)

                await store_coverage(repo, pr_number, entries)

                if any(e.get("error") for e in entries):
                    errors += 1
                processed += 1
        finally:
            checkout(clone_path, original_ref)
            print(f"\n  Restored clone to {original_ref}")

        print(f"\n  Done: {processed} processed, {errors} errors")


if __name__ == "__main__":
    asyncio.run(main())
