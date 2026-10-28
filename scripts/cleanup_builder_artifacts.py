#!/usr/bin/env python3
"""Safely discover and clean Actions artifacts, logs, and caches in owned repos.

Scheduled policy may differ from manual defaults; scheduled_policy_preview gives an
exact dry-run of scheduled targets, age limits, and latest-artifact protection.

No third-party dependencies, no shell commands, and no tokens in log messages.
Cross-repo deletes require a fine-grained PAT with Actions: Write.
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
REPO_NAME = re.compile(r"[A-Za-z0-9_.-]+\Z")
OWNER_NAME = re.compile(r"[A-Za-z0-9-]+\Z")
VALID_MODES = {"builders", "forks", "builders_and_forks", "keyword", "specific_repos"}


class GitHubAPIError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(f"HTTP {status}: {message}")


def boolean(value: str, default: bool = False) -> bool:
    return value.strip().lower() in {"1", "yes", "true", "on"} if value else default


def numeric(value: str, label: str, upper: int = 99999) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a whole number") from None
    if not 0 <= number <= upper:
        raise ValueError(f"{label} must be between 0 and {upper}")
    return number


def timestamp(value: str) -> dt.datetime:
    if not value:
        raise ValueError("GitHub response missing timestamp")
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("GitHub response timestamp lacks timezone")
    return parsed


def api_request(token: str, method: str, path: str):
    request = urllib.request.Request(
        API_URL + path,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "universal-actions-cleaner",
        },
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=35) as response:
                if method == "DELETE":
                    if response.status != 204:
                        raise GitHubAPIError(response.status, "Unexpected delete response")
                    return None
                return json.load(response)
        except urllib.error.HTTPError as exc:
            body = exc.read(1024).decode("utf-8", "replace")
            try:
                msg = json.loads(body).get("message", "API request failed")
            except (ValueError, AttributeError):
                msg = "API request failed"
            # Never print raw response bodies; they might contain user-provided data.
            retry = exc.code in (429, 500, 502, 503, 504)
            if exc.code == 403 and exc.headers.get("Retry-After"):
                retry = True
            if retry and attempt < 3:
                wait = min(int(exc.headers.get("Retry-After", "0") or "0") or 2 ** (attempt + 1), 30)
                time.sleep(wait)
                continue
            raise GitHubAPIError(exc.code, str(msg)) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < 3:
                time.sleep(2 ** (attempt + 1))
                continue
            raise RuntimeError(f"GitHub connection failed: {type(exc).__name__}") from None
    raise RuntimeError("API retry limit reached")


def list_all(token: str, path: str, key: str | None = None) -> list[dict]:
    """Collect pages BEFORE deleting to avoid skipping shifting items."""
    records = []
    for page in range(1, 1001):
        glue = "&" if "?" in path else "?"
        data = api_request(token, "GET", f"{path}{glue}per_page=100&page={page}")
        batch = data if key is None else data.get(key, [])
        if not isinstance(batch, list):
            raise RuntimeError("Unexpected paginated API response")
        records.extend(batch)
        if len(batch) < 100:
            return records
    raise RuntimeError("Too many API pages; stopped for safety")


def parse_names(raw: str, owner: str) -> set[str]:
    """Comma-separated owned repository names, optionally owner/name."""
    names = set()
    for item in raw.replace("\n", ",").split(","):
        value = item.strip()
        if not value:
            continue
        components = value.split("/")
        if len(components) == 2 and components[0].lower() == owner.lower():
            value = components[1]
        elif len(components) != 1:
            raise ValueError("Only repository names owned by the controller owner are allowed")
        if not REPO_NAME.fullmatch(value):
            raise ValueError(f"Invalid repository name: {value!r}")
        names.add(value.lower())
    return names


def load_names(file: Path, owner: str) -> set[str]:
    if not file.exists():
        return set()
    contents = "\n".join(line.partition("#")[0] for line in file.read_text(encoding="utf-8").splitlines())
    return parse_names(contents, owner)


def select_repos(repos: list[dict], owner: str, mode: str, keywords: str,
                 requested: str, extra: set[str], excluded: set[str],
                 controller: str, builder_regex: re.Pattern) -> dict[str, str]:
    if mode not in VALID_MODES:
        raise ValueError(f"Invalid target mode {mode!r}")
    own = {r["name"].lower(): r for r in repos
           if r.get("owner", {}).get("login", "").lower() == owner.lower()
           and not r.get("archived") and not r.get("disabled")}
    selected: set[str] = set()
    if mode == "specific_repos":
        selected = parse_names(requested, owner)
        if not selected:
            raise ValueError("Specific repository mode requires at least one repository name")
        missing = selected - own.keys()
        if missing:
            raise ValueError("Repositories not accessible to token: " + ", ".join(sorted(missing)))
    elif mode == "keyword":
        needles = [s.strip().lower() for s in keywords.split(",") if s.strip()]
        if not needles:
            raise ValueError("Keyword mode requires one or more keywords")
        selected = {n for n in own if any(word in n for word in needles)}
    else:
        for name, repo in own.items():
            is_builder = bool(builder_regex.search(name)) or name in extra
            is_fork = bool(repo.get("fork"))
            if ((mode in {"builders", "builders_and_forks"} and is_builder)
                or (mode in {"forks", "builders_and_forks"} and is_fork)):
                selected.add(name)
    selected.difference_update(excluded)
    selected.discard(controller.lower())
    return {key: own[key]["name"] for key in selected if key in own}


def should_run(now: dt.datetime, anchor: dt.date, days: int) -> bool:
    return (now.date() - anchor).days % days == 0


def summary(lines: list[str]) -> None:
    message = "\n".join(lines) + "\n"
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as file:
            file.write(message)
    print(message)


def eligible_artifacts(artifacts: list[dict], cutoff: dt.datetime,
                       keep_latest: int) -> list[dict]:
    ordered = sorted(artifacts, key=lambda x: x.get("created_at", ""), reverse=True)
    return [a for a in ordered[keep_latest:] if timestamp(a["created_at"]) <= cutoff]


def eligible_caches(caches: list[dict], cutoff: dt.datetime) -> list[dict]:
    return [a for a in caches if timestamp(a.get("last_accessed_at") or a["created_at"]) <= cutoff]


def eligible_logs(runs: list[dict], cutoff: dt.datetime, limit: int) -> list[dict]:
    # API called with status=completed. Verify status here as another precaution.
    done = [r for r in runs if r.get("status") == "completed" and timestamp(r["updated_at"]) <= cutoff]
    return done[:limit]


def delete_candidates(token: str, repo_path: str, records: list[dict], suffix: str,
                      dry_run: bool) -> tuple[int, int]:
    deleted = 0
    vanished = 0
    if dry_run:
        return deleted, vanished
    for obj in records:
        try:
            api_request(token, "DELETE", f"{repo_path}/actions/{suffix.format(id=int(obj['id']))}")
            deleted += 1
        except GitHubAPIError as exc:
            if exc.status == 404:
                vanished += 1
            else:
                raise
    return deleted, vanished


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    owner = os.getenv("CLEANUP_OWNER", "").strip()
    if not OWNER_NAME.fullmatch(owner):
        raise ValueError("Invalid or missing CLEANUP_OWNER")
    manual = boolean(os.getenv("MANUAL_RUN", "false"))
    anchor = dt.date.fromisoformat(os.getenv("CLEANUP_ANCHOR_DATE", "2026-10-28"))
    interval = numeric(os.getenv("CLEANUP_EVERY_DAYS", "3"), "Cleanup interval", 365)
    if interval == 0:
        raise ValueError("Cleanup interval cannot be zero")
    if not manual and not should_run(now, anchor, interval):
        summary(["## Universal Builder and Fork Cleanup", "No cleanup due today.",
                 f"Next cycle is every {interval} days from {anchor.isoformat()} (UTC)."])
        return 0

    token = os.getenv("ARTIFACT_CLEANUP_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing ARTIFACT_CLEANUP_TOKEN secret (Actions: Read/Write required)")
    requested_mode = os.getenv("MANUAL_TARGET_MODE", "builders_and_forks") if manual else ""
    scheduled_preview = manual and requested_mode == "scheduled_policy_preview"
    prefix = "SCHEDULED" if (not manual or scheduled_preview) else "MANUAL"
    # The scheduled-policy preview ALWAYS forces dry-run, regardless of checkbox.
    dry_run = True if scheduled_preview else (boolean(os.getenv("MANUAL_DRY_RUN", "true")) if manual else False)
    mode = os.getenv(f"{prefix}_TARGET_MODE", "builders" if prefix == "SCHEDULED" else "builders_and_forks")
    keywords = os.getenv("MANUAL_KEYWORDS", "") if manual else ""
    specific = os.getenv("MANUAL_REPOSITORIES", "") if manual else ""
    do_artifacts = boolean(os.getenv(f"{prefix}_DELETE_ARTIFACTS", "true"), True)
    do_logs = boolean(os.getenv(f"{prefix}_DELETE_LOGS", "false"))
    do_caches = boolean(os.getenv(f"{prefix}_DELETE_CACHES", "false"))
    artifact_age = numeric(os.getenv(f"{prefix}_ARTIFACT_AGE_HOURS", "72"), "Artifact age (hours)")
    log_age = numeric(os.getenv(f"{prefix}_LOG_AGE_DAYS", "14"), "Log age (days)")
    cache_idle = numeric(os.getenv(f"{prefix}_CACHE_IDLE_DAYS", "14"), "Cache idle days")
    latest_env = (os.getenv("SCHEDULED_KEEP_LATEST_PER_REPO", "0")
                  if prefix == "SCHEDULED" else os.getenv("KEEP_LATEST_PER_REPO", "1"))
    keep_latest = numeric(latest_env, "Keep newest artifacts")
    max_logs = numeric(os.getenv("MAX_LOGS_PER_REPO", "100"), "Max logs per repo")
    if not any((do_artifacts, do_logs, do_caches)):
        raise ValueError("Select at least one resource type to clean")
    regex = re.compile(os.getenv("BUILDER_NAME_REGEX", r"(?i)builder"))
    extra = load_names(ROOT / "config" / "builders-extra.txt", owner)
    excluded = load_names(ROOT / "config" / "builders-exclude.txt", owner)

    repos = list_all(token, "/user/repos?affiliation=owner&visibility=all")
    selected = select_repos(repos, owner, mode, keywords, specific, extra, excluded,
                            os.getenv("CONTROLLER_REPOSITORY", "fork-sync-controller"), regex)
    if not selected:
        summary(["## Universal Builder and Fork Cleanup", "No matching accessible owned repositories.",
                 "Check token's repository access, filters, and exclusions. No deletions occurred."])
        return 1

    counts = {kind: [0, 0, 0] for kind in ("Artifacts", "Logs", "Caches")}
    errors: list[str] = []
    lines = ["## Universal Builder and Fork Cleanup", f"**Mode:** {'PREVIEW ONLY' if dry_run else 'REAL DELETION'}",
             f"**Selection:** `{mode}`{' (scheduled policy preview)' if scheduled_preview else ''} | **Repositories:** {len(selected)} | **UTC:** {now:%Y-%m-%d %H:%M}",
             f"**Artifact age:** {artifact_age}h, latest {keep_latest} per repo protected",
             f"**Log age:** {log_age}d | **Cache idle time:** {cache_idle}d", "",
             "| Repository | Artifact ZIPs | Logs | Caches | Outcome |",
             "|---|---:|---:|---:|---|"]
    for name in sorted(selected.values(), key=str.casefold):
        path = "/repos/" + urllib.parse.quote(owner) + "/" + urllib.parse.quote(name)
        per_repo = {"Artifacts": 0, "Logs": 0, "Caches": 0}
        try:
            if do_artifacts:
                artifacts = list_all(token, path + "/actions/artifacts", "artifacts")
                items = eligible_artifacts(artifacts, now - dt.timedelta(hours=artifact_age), keep_latest)
                counts["Artifacts"][0] += len(items)
                counts["Artifacts"][2] += sum(int(a.get("size_in_bytes", 0)) for a in items)
                per_repo["Artifacts"] = len(items)
                deleted, _ = delete_candidates(token, path, items, "artifacts/{id}", dry_run)
                counts["Artifacts"][1] += deleted
            if do_logs:
                runs = list_all(token, path + "/actions/runs?status=completed", "workflow_runs")
                items = eligible_logs(runs, now - dt.timedelta(days=log_age), max_logs)
                counts["Logs"][0] += len(items)
                per_repo["Logs"] = len(items)
                deleted, _ = delete_candidates(token, path, items, "runs/{id}/logs", dry_run)
                counts["Logs"][1] += deleted
            if do_caches:
                caches = list_all(token, path + "/actions/caches", "actions_caches")
                items = eligible_caches(caches, now - dt.timedelta(days=cache_idle))
                counts["Caches"][0] += len(items)
                counts["Caches"][2] += sum(int(a.get("size_in_bytes", 0)) for a in items)
                per_repo["Caches"] = len(items)
                deleted, _ = delete_candidates(token, path, items, "caches/{id}", dry_run)
                counts["Caches"][1] += deleted
            lines.append(f"| `{name}` | {per_repo['Artifacts'] if do_artifacts else 'Off'} | "
                         f"{per_repo['Logs'] if do_logs else 'Off'} | "
                         f"{per_repo['Caches'] if do_caches else 'Off'} | "
                         f"{'Preview' if dry_run else 'Completed'} |")
        except (GitHubAPIError, RuntimeError, ValueError) as err:
            errors.append(f"{name}: {err}")
            lines.append(f"| `{name}` | — | — | — | Error |")
            print(f"ERROR: {name}: {err}", file=sys.stderr)

    lines += ["", "### Totals (selected / actually deleted)",
              *(f"- **{kind}:** {counter[0]} / {counter[1]}"
                + (f" ({counter[2] / 1024 ** 2:,.1f} MiB selected)" if kind != "Logs" else "")
                for kind, counter in counts.items()),
              "Counts are estimated from GitHub API metadata. Log sizes are unavailable.",
              "Deleting logs preserves run history; deleting caches can slow future builds.",
             "Only eligible objects older than each threshold are deleted; fresh artifacts/logs are protected."]
    if errors:
        lines.append(f"**Errors:** {len(errors)}. See the job log for details. Other repositories may have been cleaned.")
    summary(lines)
    return 1 if errors else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, GitHubAPIError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
