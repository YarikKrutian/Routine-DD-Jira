# Routine-DD-Jira

Automated triage of Jiji production errors: Datadog → classification → code analysis in Bitbucket → bug tickets in Jira → HTML report (Artifact).
Run by the `datadog-jira-triage` routine twice a day. No human is present — act autonomously but carefully.

## How to run
The workflow is the skill `.claude/skills/triage/SKILL.md`. Parameters (query, window, thresholds, limits, noise) live in `config/triage.yaml`.
Do not duplicate config values elsewhere — read them from there.

## Layout
- `config/triage.yaml` — all tunable parameters.
- `scripts/code_origin.py` — deterministic code-origin analysis (blame / log -L / log -S / PR / Jira keys). Returns JSON.
- `scripts/check_report.py` — pre-publish report check: number consistency, PII, Chromium render (375px and 1280px). Returns JSON.
- `templates/bug_ticket.adf.json` — ADF skeleton of the ticket description.
- `templates/report.html` — report template; only its JSON data block is filled in.
- `ROUTINE_PROMPT.md` — short routine prompt for when the repository is attached.
- `ROUTINE_PROMPT_STANDALONE.md` — full self-contained prompt currently set in the routine (until the repository is attached). Mirror every rule change there and in the routine.

## Fixed values
- Jira: site `jijing.atlassian.net`, cloudId `4ae966c7-4f95-4aac-9ee7-24cee120152f`, project `JIJI`.
  Issue type Bug, label `auto-triage`, Component/s — `customfield_10100` (labels-type),
  Developer №1 — `customfield_10600` (user picker, author of the source commit).
- Bitbucket: workspace `cls-gentech`, repository from the `BITBUCKET_REPO_SLUG` environment variable.
  Access — `BITBUCKET_EMAIL` + `BITBUCKET_API_TOKEN` (Atlassian API token with the Bitbucket read scope).

## Hard rules
1. **Do not commit or push** — neither to this repository nor to the backend. Work only with Datadog, Jira, Bitbucket (read-only) and Artifact.
2. **Secrets.** Never print environment variable values; never put the token in URLs, commands or logs.
   For Bitbucket use `scripts/code_origin.py` — it passes the token through a credential helper and `urllib` basic auth.
   Do not copy tokens, keys or authorization headers from stack traces or code.
3. **No PII** in tickets, the report or session output: phone numbers (including sender numbers), emails, IPs, user ids, exact locations,
   names/emails/logins of commit authors — mask or aggregate. Query Datadog only for the fields triage needs, with no user identifiers.
   The only exception is the Atlassian account id of the source commit's author (`author_account_id` from `scripts/code_origin.py`):
   it may only be passed into the ticket's Developer №1 field, never written into ticket text, the report or session output.
4. **No invented numbers.** Every number comes from a real query within the analysis window. Numbers must be consistent with each other.
5. **Datadog links** — only the `logs_explorer_url` from the tool response; never build the URL by hand.
6. **Failed calls.** If a Datadog/Jira/Bitbucket call fails (auth, "requires approval", etc.) — do not guess the result:
   state in the report which call failed and with what error, and mark sections without data as "no data".
   No Bitbucket access — skip code analysis, write "Code origin: no access to Bitbucket" in the ticket and the report, and continue.
7. **Do not edit or comment on** other tickets — only create the links described in the skill.
