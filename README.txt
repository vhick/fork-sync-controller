UNIVERSAL BUILDER ARTIFACT CLEANUP - READY-TO-USE KIT
======================================================
For your PUBLIC Universal Fork Sync Controller repository.

WHAT IT DOES
------------
- Performs a tiny scheduled check every day at 04:23 UTC (12:23 Singapore).
- ACTUALLY CLEANS every 3 days, counting from October 28, 2026 UTC.
  This stays consistent across month and year boundaries.
- Finds repositories YOU OWN whose names contain "builder" (case-insensitive).
- Deletes ALL Actions artifacts in those repositories (both old and new).
- Does NOT delete source code, workflow definitions, Releases, GitHub Packages,
  workflow run logs, or caches.
- Offers a manual safe preview (Dry Run) before doing actual deletions.
- Produces a per-repository cleanup report on the workflow run page.
- Does not upload any artifacts of its own.

IMPORTANT
---------
Artifact deletions are PERMANENT. Download any portable ZIP you want to
keep first. A freshly created ZIP may also be deleted on cleanup day.
Deletion frees stored space, but GitHub's monthly accrued billing meter
may take time to update or only reset at the next billing cycle.

SETUP USING GITHUB DESKTOP (NO FORK REQUIRED)
---------------------------------------------
1. In GitHub Desktop, select your existing Universal Fork Sync Controller repo.
   If not downloaded locally, use File > Clone repository.
2. Choose Repository > Show in Explorer.
3. Unzip this kit. Copy the .github, scripts and config folders into the
   ROOT of the local controller repository. IMPORTANT: .github is a hidden
   dot-folder and must be included.
4. In GitHub Desktop, review the new files, enter a commit message such as
   "Add automatic builder artifact cleanup", then click Commit to main.
   Click Push origin.

CREATE THE REQUIRED FINE-GRAINED GITHUB TOKEN
---------------------------------------------
5. While signed into GitHub, open:
   https://github.com/settings/personal-access-tokens/new
6. Token name: Portable Builder Artifact Cleanup
7. Resource owner: YOUR GitHub account, the owner of your builders.
8. Repository access: "Only select repositories".
   Select EVERY builder repository you want cleaned. This is safer than
   granting access to all repositories. New builder repos must be added to
   this token's selected repositories later.
9. Repository permissions > Actions > Read and write.
   Metadata read is provided automatically.
   Do NOT grant Contents write or Administration write.
10. Choose an expiration you can maintain; when it expires, update the token.
    Generate token and COPY it. Never put it in repo files or commit history.
11. Open your CONTROLLER repository on github.com and select:
    Settings > Secrets and variables > Actions > New repository secret.
    Secret name EXACTLY: ARTIFACT_CLEANUP_TOKEN
    Secret value: paste the token. Click Add secret.

PREVIEW, THEN ENABLE
--------------------
12. Open controller repository > Actions > Universal Builder Artifact Cleanup.
13. Click Run workflow. Leave "Preview only" checked. Run.
14. Open the completed run and inspect its Summary. Check which repositories
    and how many artifacts WOULD be deleted.
15. If it looks right, use Run workflow again with "Preview only" unchecked
    to do an immediate cleanup. Otherwise, simply leave it scheduled.
16. Scheduled runs automatically perform REAL deletion on cleanup days.
    Other scheduled days only check the date and exit quickly.

ADDING OR PROTECTING REPOSITORIES
--------------------------------
- By default, the cleaner targets repos whose names contain "builder".
- To include a builder repo without that word in its name, add its name to
  config/builders-extra.txt. ALSO add that repo to the token access list.
- To protect a matched repo, add it to config/builders-exclude.txt.
- A repo must belong to your account and be accessible to the token.
- If your controller is under a DIFFERENT account than your builders, edit
  BUILDER_OWNER in .github/workflows/universal-artifact-cleanup.yml to
  your builder owner username. The token's resource owner must match too.

OPTIONAL SETTINGS IN THE WORKFLOW FILE
--------------------------------------
- CLEANUP_EVERY_DAYS: '3'  -> change cleanup interval.
- CLEANUP_ANCHOR_DATE: '2026-10-28' -> date on which cycle starts (UTC).
- BUILDER_NAME_REGEX: '(?i)builder' -> name filter for auto-discovery.
- KEEP_LATEST_PER_REPO: '0' -> zero deletes ALL artifacts. '1' keeps latest.
- MIN_AGE_HOURS: '0' -> zero deletes regardless of age; '24' protects last day.

WHEN THERE IS A PROBLEM
-----------------------
- "Missing secret": check secret name ARTIFACT_CLEANUP_TOKEN.
- HTTP 403: token lacks Actions Read/Write for the named repository, or
  a GitHub permission/rate limit policy is blocking it.
- HTTP 404: repository may be inaccessible to the token, or renamed.
- "No builder repositories found": check builder names, token repository
  selection, and owner. A manual extra name may help.
- If scheduled workflows stop: GitHub can disable scheduled workflows in
  a public repository after 60 days of repository inactivity. Re-enable in
  the Actions tab, or make a repository commit as appropriate.
- Scheduled runs may be delayed/dropped during high GitHub Actions load.

NOTES ABOUT BILLING
-------------------
- Normal GitHub-hosted runners for PUBLIC repositories do not consume
  your included private-repository Actions minutes.
- Deleting artifacts reduces stored bytes. Monthly usage is a separate
  billing measure, so the dashboard is not necessarily immediately zero.
- GitHub Releases assets are NOT deleted by this script.

SOURCES (OFFICIAL GITHUB DOCUMENTATION)
---------------------------------------
https://docs.github.com/en/rest/actions/artifacts
https://docs.github.com/en/rest/repos/repos
https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens
https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
https://docs.github.com/en/actions/how-tos/manage-workflow-runs/remove-workflow-artifacts
