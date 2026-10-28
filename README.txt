UNIVERSAL BUILDER + FORK CLEANUP v1.2
===================================
Scheduled builder-only maintenance + flexible manual cleanup.
Designed for: https://github.com/vhick/fork-sync-controller

UPGRADE FROM YOUR WORKING v1.1
------------------------------
1. In GitHub Desktop, open the fork-sync-controller repository.
2. Choose Repository -> Show in Explorer.
3. Save a backup of these existing files, if customized:
   .github/workflows/universal-artifact-cleanup.yml
   scripts/cleanup_builder_artifacts.py
4. Extract this ZIP. Copy .github, scripts, config, and tests into the root
   of the local repo. MERGE folders; never delete the existing .github folder.
5. Only replace the matching cleanup workflow / Python files. Keep any custom
   entries in config/builders-extra.txt and config/builders-exclude.txt.
6. GitHub Desktop -> commit changed files -> Push origin.
7. Your original 6-hour fork-sync workflow is untouched.
8. Keep existing ARTIFACT_CLEANUP_TOKEN secret; no need to create a classic PAT
   if the fine-grained token has Actions: write on all of your repositories.

WHAT AUTOMATIC CLEANUP DOES
--------------------------
- Every 3 days it automatically discovers all active, owned builder repositories:
  names containing 'builder' (case-insensitive), plus the names explicitly
  listed in config/builders-extra.txt.
- Excludes all other repositories, including developer forks such as vhick/etkg,
  unless they contain 'builder' in the name or are explicitly listed as extra.
- Deletes ALL artifacts at least 72 hours old (including newest IF old enough).
- Deletes logs of COMPLETED workflow runs last updated at least 7 days ago;
  history of run records remains, logs cannot be recovered.
- Deletes caches whose last_accessed_at is at least 15 days ago.
- Never deletes workflow runs, source code, GitHub Releases, branches, tags,
  Packages, or the sync controller's own files.
- New builder repositories are included on the next scheduled cleanup once
  authorized by the existing fine-grained token (All repositories access).
- No stored artifacts are protected FOREVER automatically: the latest artifact
  becomes eligible when it reaches age 72h. Downloads must happen before then.

SCHEDULED FREQUENCY
-------------------
The workflow runs a tiny daily check at 04:23 UTC (12:23 in Singapore) and
performs cleanup every 3 days from 2026-10-28 UTC. This avoids the '*/3' cron
trap: 'every 3rd day of month' restarts its count on the 1st of each month.
GitHub schedules can be delayed or dropped under heavy load; this is
best-effort periodic maintenance, not guaranteed execution at the exact minute.

HOW TO PREVIEW EXACT AUTOMATIC POLICY (BEFORE FIRST RUN)
--------------------------------------------------------
1. On GitHub: fork-sync-controller -> Actions -> Universal Builder and Fork Cleanup
   -> Run workflow.
2. Set target_mode to 'scheduled_policy_preview'.
3. Keep any dry-run checkbox setting (preview mode FORCES dry-run even if unchecked).
4. Run workflow and open the job summary.
5. It will show EXACTLY which builder repositories and objects qualify under
   the scheduled settings, without deleting anything.
   Note: Preview counts can change before the future cleanup date.
6. The next automatic cycle will delete the qualifying objects.

MANUAL CLEANUP STILL WORKS AS IN v1.1
------------------------------------
- Existing modes unchanged: builders_and_forks, builders, forks, keyword,
  specific_repos; newly added: scheduled_policy_preview.
- Existing manual booleans and age thresholds remain unchanged.
- Manual mode retains newest artifact per repository (KEEP_LATEST_PER_REPO=1).
- Manual default: PREVIEW ONLY, artifacts true, logs false, caches false,
  min artifact age 72h, min log age 14d, min cache idle 14d.
- Use manual mode to clean developer forks, such as etkg.
- Explicitly excluded repos are excluded even when requested manually.

ADJUST AUTOMATIC RULES IF NEEDED
--------------------------------
Edit .github/workflows/universal-artifact-cleanup.yml:
 SCHEDULED_TARGET_MODE: 'builders'          # builders only (recommended)
 SCHEDULED_ARTIFACT_AGE_HOURS: '72'         # keep ZIPs for 3 days
 SCHEDULED_KEEP_LATEST_PER_REPO: '0'        # allow every ZIP to expire
 SCHEDULED_LOG_AGE_DAYS: '7'               # allow a week to troubleshoot
 SCHEDULED_CACHE_IDLE_DAYS: '15'           # delete only old unused caches
 SCHEDULED_DELETE_ARTIFACTS: 'true'
 SCHEDULED_DELETE_LOGS: 'true'
 SCHEDULED_DELETE_CACHES: 'true'
 CLEANUP_EVERY_DAYS: '3'
 MAX_LOGS_PER_REPO: '100'                  # avoids excessive API requests

If you need to keep failed logs longer, set SCHEDULED_LOG_AGE_DAYS to '14'.
If you want build ZIPs for longer, set ARTIFACT_AGE_HOURS to '120' (5 days)
or '168' (7 days). Download important ZIPs before automatic deletion.

WHY WE USE THESE LIMITS
-----------------------
- GitHub's default cache eviction for unused caches is 7 days. A 15-day
  manual maintenance pass may find none unless you changed that retention.
- Cache storage is a separate 10 GB per-repository allowance on Free, not
  the shared 500 MB artifacts-and-packages pool.
- GitHub Free's public repositories using standard hosted runners do not
  consume the 2,000 included monthly Actions minutes. Larger runners differ.
- Deleting artifacts reduces CURRENT storage; already accrued monthly storage
  does not reverse.
- GitHub's own per-repository retention settings and artifact retention-days
  can reduce storage more cheaply than waiting for the controller.
- If the repo stays inactive for 60 days, GitHub can disable scheduled workflows
  on public repositories; check the Actions page periodically.

REPOSITORY SELECTION
--------------------
Names containing 'builder' are found automatically. Extra builder names go in
config/builders-extra.txt (one per line); exclusions go in
config/builders-exclude.txt. The controller repository always is excluded.
Set token resource owner 'vhick', Repository access: 'All repositories',
Repository Actions permission: 'Read and write'. Store ONLY as a repository
secret named ARTIFACT_CLEANUP_TOKEN; never paste it in committed files.
Fine-grained All repositories avoids broader classic PAT permissions.

CLEANUP REPORTS & LIMITATIONS
-----------------------------
The Actions job summary reports candidate/deleted artifact/log/cache counts,
per-repository outcomes, and estimated artifact+cache bytes from API metadata.
GitHub does not provide individual workflow log sizes in this API response;
do not treat log counts as log-byte savings. Cache bytes are a SEPARATE quota.

To limit REST calls, only 100 eligible completed-run logs per repository are
removed per cycle by default. If you have a large backlog, a few cycles may be
needed; errors are surfaced and partial cleanup is possible.

This ZIP was locally tested with simulated GitHub API calls. An actual run
against your GitHub account has NOT been performed here. Always use the
scheduled_policy_preview option before letting the new policy delete items.

OFFICIAL SOURCES
----------------
https://docs.github.com/en/rest/actions/artifacts
https://docs.github.com/en/rest/actions/workflow-runs
https://docs.github.com/en/rest/actions/cache
https://docs.github.com/en/billing/concepts/product-billing/github-actions
https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching
https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
