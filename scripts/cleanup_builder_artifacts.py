#!/usr/bin/env python3
"""Delete GitHub Actions workflow artifacts from selected builder repositories.

Uses Python's standard library only. A fine-grained PAT provides access to
other repositories; the controller repository's GITHUB_TOKEN isn't used.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
API_URL = "https://api.github.com"
API_VERSION = "2026-03-10"


class GitHubAPIError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(f"HTTP {status}: {message}")


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"true", "1", "yes"}


def api_request(token: str, method: str, path: str):
    url = API_URL + path
    req = urllib.request.Request(
        url,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "portable-builder-artifact-cleaner",
        },
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=35) as response:
                if method == "DELETE":
                    if response.status != 204:
                        raise GitHubAPIError(response.status, "Expected HTTP 204")
                    return None
                return json.load(response)
        except urllib.error.HTTPError as exc:
            body = exc.read(3000).decode("utf-8", "replace")
            try:
                message = json.loads(body).get("message", body)
            except (ValueError, AttributeError):
                message = body
            if (exc.code in {429, 500, 502, 503, 504}) and attempt < 2:
                time.sleep(2**attempt)
                continue
            raise GitHubAPIError(exc.code, message) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < 2:
                time.sleep(2**attempt)
                continue
            raise RuntimeError(f"Network error calling GitHub API: {exc}") from None
    raise RuntimeError("Unexpected API retry exhaustion")


def list_all(token: str, path: str, item_key: str | None = None) -> list[dict]:
    """Gather *all* pages before deletion so pagination cannot skip artifacts."""
    results: list[dict] = []
    for page in range(1, 1001):
        separator = "&" if "?" in path else "?"
        payload = api_request(token, "GET", f"{path}{separator}per_page=100&page={page}")
        if item_key is None:
            items = payload
        else:
            items = payload.get(item_key, [])
        if not isinstance(items, list):
            raise RuntimeError(f"Unexpected pagination response from {path}")
        results.extend(items)
        if len(items) < 100:
            return results
    raise RuntimeError(f"Pagination safety limit reached for {path}")


def load_repo_names(path: Path, owner: str) -> set[str]:
    """Parse names with or without owner/ prefix; reject other owners."""
    names: set[str] = set()
    if not path.exists():
        return names
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        val = raw.partition("#")[0].strip()
        if not val:
            continue
        parts = val.split("/")
        if len(parts) == 1:
            repo = parts[0]
        elif len(parts) == 2 and parts[0].lower() == owner.lower():
            repo = parts[1]
        else:
            raise ValueError(f"{path.name}:{line_no}: expected repository belonging to {owner}")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
            raise ValueError(f"{path.name}:{line_no}: invalid repository name")
        names.add(repo.lower())
    return names


def schedule_due(now_utc: dt.datetime, anchor: dt.date, every_days: int) -> bool:
    return (now_utc.date() - anchor).days % every_days == 0


def should_delete(artifact: dict, cutoff: dt.datetime, min_age_hours: int) -> bool:
    if min_age_hours <= 0:
        return True
    when = artifact.get("created_at", "")
    created = dt.datetime.fromisoformat(when.replace("Z", "+00:00"))
    return created <= cutoff


def write_summary(lines: list[str]) -> None:
    content = "\n".join(lines) + "\n"
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as stream:
            stream.write(content)
    print(content)


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    owner = os.environ.get("BUILDER_OWNER", "").strip()
    token = os.environ.get("ARTIFACT_CLEANUP_TOKEN", "").strip()
    manual = env_bool("MANUAL_RUN")
    dry_run = env_bool("DRY_RUN", default=True)
    every_days = int(os.environ.get("CLEANUP_EVERY_DAYS", "3"))
    anchor = dt.date.fromisoformat(os.environ.get("CLEANUP_ANCHOR_DATE", "2026-10-28"))
    regex = re.compile(os.environ.get("BUILDER_NAME_REGEX", r"(?i)builder"))
    keep_latest = int(os.environ.get("KEEP_LATEST_PER_REPO", "0"))
    min_age_hours = int(os.environ.get("MIN_AGE_HOURS", "0"))

    if every_days < 1 or keep_latest < 0 or min_age_hours < 0:
        raise ValueError("Cleanup interval, keep count and minimum age must be nonnegative")
    if not manual and not schedule_due(now, anchor, every_days):
        write_summary([
            "## Universal Builder Artifact Cleanup",
            f"Scheduled check only: **not a cleanup day** ({now.date()}).",
            f"Cleanup is due every {every_days} days from {anchor} (UTC).",
        ])
        return 0
    if not owner or not re.fullmatch(r"[A-Za-z0-9-]+", owner):
        raise ValueError("BUILDER_OWNER is missing or invalid")
    if not token:
        raise RuntimeError(
            "Missing secret ARTIFACT_CLEANUP_TOKEN. Create a fine-grained token with "
            "Actions: Read and Write for your selected builder repositories, "
            "then add it as a controller repository Actions secret."
        )

    extra = load_repo_names(ROOT / "config" / "builders-extra.txt", owner)
    excluded = load_repo_names(ROOT / "config" / "builders-exclude.txt", owner)
    print(f"Discovering repositories belonging to {owner}...")
    repos = list_all(token, "/user/repos?affiliation=owner&visibility=all")
    discovered: dict[str, str] = {}
    for r in repos:
        if r.get("owner", {}).get("login", "").lower() != owner.lower():
            continue
        if r.get("archived") or r.get("disabled"):
            continue
        name = r["name"]
        if regex.search(name):
            discovered[name.lower()] = name
    for name in extra:
        discovered[name] = next((r["name"] for r in repos if r["name"].lower() == name), name)
    for name in excluded:
        discovered.pop(name, None)

    if not discovered:
        write_summary([
            "## Universal Builder Artifact Cleanup",
            "**No builder repositories found.** Check token repository selection and name filtering.",
            "No artifacts were deleted.",
        ])
        return 1

    cutoff = now - dt.timedelta(hours=min_age_hours)
    total_deleted = 0
    total_selected = 0
    total_bytes = 0
    failures: list[str] = []
    report = [
        "## Universal Builder Artifact Cleanup",
        f"**Mode:** {'Preview (dry run)' if dry_run else 'Actual deletion'}",
        f"**UTC date:** {now:%Y-%m-%d %H:%M}",
        f"**Repository owner:** `{owner}`",
        "",
        "| Repository | Artifacts selected | Space represented | Result |",
        "|---|---:|---:|---|",
    ]
    print(f"Repositories matched: {len(discovered)}. Preview mode: {dry_run}")
    for name in sorted(discovered.values(), key=str.lower):
        repo_path = f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}"
        try:
            artifacts = list_all(token, repo_path + "/actions/artifacts", item_key="artifacts")
            artifacts.sort(key=lambda a: a.get("created_at", ""), reverse=True)
            # Excludes the newest N artifacts, if configured.
            candidates = artifacts[keep_latest:]
            candidates = [a for a in candidates if should_delete(a, cutoff, min_age_hours)]
            bytes_represented = sum(int(a.get("size_in_bytes", 0)) for a in candidates)
            selected_count = len(candidates)
            total_selected += selected_count
            total_bytes += bytes_represented
            deleted = 0
            if not dry_run:
                for a in candidates:
                    try:
                        api_request(token, "DELETE", repo_path + f"/actions/artifacts/{int(a['id'])}")
                        deleted += 1
                    except GitHubAPIError as exc:
                        # Already removed by retention or parallel deletion: harmless.
                        if exc.status == 404:
                            continue
                        failures.append(f"{name}: artifact {a['id']}: {exc}")
            total_deleted += deleted
            status = ("Preview only" if dry_run else f"Deleted {deleted}")
            report.append(f"| `{name}` | {selected_count} | {bytes_represented / 1024**2:,.1f} MiB | {status} |")
            print(f"{name}: {selected_count} artifacts selected, {status.lower()}")
        except (GitHubAPIError, RuntimeError, ValueError) as exc:
            failures.append(f"{name}: {exc}")
            report.append(f"| `{name}` | — | — | Error (see log) |")
            print(f"WARNING {name}: {exc}")

    report += [
        "",
        f"**Total selected:** {total_selected} artifacts (~{total_bytes / 1024**2:,.1f} MiB).",
        f"**Actually deleted:** {total_deleted} artifacts.",
        "Counts represent stored artifacts; GitHub billing dashboards may update later.",
    ]
    if failures:
        report += ["", f"**Errors:** {len(failures)}. Check this run's log. Some repositories may not be selected for the token."]
    write_summary(report)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, GitHubAPIError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
