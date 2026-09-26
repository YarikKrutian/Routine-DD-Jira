---
name: triage
description: Automated triage of Jiji production errors from Datadog (celery/uwsgi) — pattern classification, code-origin analysis in Bitbucket, Jira bug tickets and an HTML report. Use when the routine or the user asks to "run", "triage" or check Datadog errors.
---

# Datadog → Jira error triage

Before starting, read `config/triage.yaml` — referred to below as `cfg.*`.
General rules (secrets, PII, failed calls) are in `CLAUDE.md`; they apply at every step.

## 1. Datadog
First run Datadog MCP skill discovery (`load_datadog_skill` `datadog/logs` + `list_datadog_skills`).

The window is `cfg.window` (from/to). No query and no comparison outside it.

1. Patterns: query `cfg.datadog.query`, `use_log_patterns: true`, `pattern_group_by: cfg.datadog.patterns.pattern_group_by`.
2. Break down generic patterns (e.g. `Exception on <wildcard endpoint>`): group by `@endpoint`,
   `clustering_pattern_field = @error.message`.
3. Request only the fields triage needs; no user identifiers.
4. Statistics (same filter and window):
   - total number of error logs;
   - breakdown by service and by country (aggregates only);
   - hourly distribution (`cfg.window.hourly_points` points).

Keep the `logs_explorer_url` from every response — tickets and the report need them.

## 2. Classify each pattern
- 🚫 **Infra noise** — anything in `cfg.classification.infra_noise` (timeouts to ES, Redis/Valkey, Mongo, Telegram, Intercom,
  third-party APIs; `SoftTimeLimitExceeded`/`TimeLimitExceeded`; isolated `OperationalError`/deadlock). **No** tickets.
- ✅ **Already covered** — an existing ticket was found: JQL text search in `project = JIJI` for the unique error string,
  task name or endpoint; also check `label = auto-triage`.
- 🔴 **New** — no ticket. If a closed ticket exists and the error is back at real volume, it is a **regression**:
  a new ticket referencing the old one (a `Relates` link + a mention in the text).

## 3. Code analysis (for each 🔴, before creating the ticket)
Use `scripts/code_origin.py` (see `--help`). It clones the repository, picks the ref, runs blame/log and fetches PRs
without exposing the token. About the commit author it returns only `author_account_id` (Atlassian account id) — no name, email or login.

a) From the stack trace take the deepest frame in project code (not site-packages/stdlib): file, line, function.
   No stack trace (a direct `log.error` or a repr) — take logger/funcName/module from the log and find the code with `--search "<unique text>"`.
b) Take the version from the Datadog `version` tag → `--version`. The script reports whether the ref was found or main was used (`ref_fallback`) — say so.
c) `--file F --line N [--function FN]` — blame ±5 lines and `git log -L` for the function. Add `--search "<error string>"` if needed.
d) For each commit the script returns short SHA, date, message, PRs and Jira keys (`JIJI-\d+` from the message, branch and PR title).
   **Verify every key** with `getJiraIssue` (exists, what it is about).
e) Rate confidence:
   - **high** — the commit directly changed the failing line and a Jira key was found;
   - **medium** — the commit changed the same function / nearby logic;
   - **low** — only indirect signs.
   Do not invent a connection: if nothing is convincing, say so. If the cause is clearly outside the code (third-party service settings, partner data), say so.
f) Do not record names, emails or logins of commit authors — only SHA, date, PR and Jira key.
   Use the source commit's `author_account_id` only for the Developer field (see section 4) — never output it in ticket text, the report or the summary.

If the script returns `"error": "no_access"` — "Code origin: no access to Bitbucket" and carry on.

## 4. Tickets (🔴 only)
Project `JIJI`, type Bug, label `auto-triage`.

**Before the first `createJiraIssue`** load the `jira-bug-report-rules` skill via Skill. The rules below apply whether or not it loads.

### Mandatory formatting
- **Language:** title and entire body in English. The last node of the description is a Ukrainian translation of the WHOLE body in a **collapsed** block.
  Create and edit with `contentFormat: "adf"`; the last top-level node:
  `{"type":"expand","attrs":{"title":"Українською"},"content":[...]}`.
  Do not use `{expand}` wiki markup or HTML `<details>` — Jira Cloud does not collapse them.
  The translation block is mandatory and does not count as an "extra section".
- **Component/s:** always fill `customfield_10100` — 3–5 lowercase tags: `backend`, `prod`, the runtime (`celery` or `uwsgi`)
  and 1–2 topical tags for the module/feature. Before the first ticket look at existing values: `cfg.jira.components.existing_values_jql`.
- **Developer №1** (`cfg.jira.developer.field`, user picker): the author of the commit named as the source in Code origin.
  Only with **high/medium** confidence and a non-empty `author_account_id` for that commit — pass it to `createJiraIssue`
  via `additional_fields`: `{"customfield_10600": {"accountId": "<author_account_id>"}}`.
  With **low** confidence, no Bitbucket access or an `author_error` — leave it empty and give the reason in the summary ("Developer: not set — <reason>").
  If Jira rejects the create because of this field — create the ticket without it and give the reason. Do not touch the assignee.
- **Verification:** after creating each ticket — `getJiraIssue` (fields: `description`, `customfield_10100`, `customfield_10600`, `responseContentFormat: adf`).
  Confirm: body in English, the description ends with the "Українською" expand node, `customfield_10100` is not empty,
  `customfield_10600` is set when it should be (accountId matches). If anything is wrong — fix it with `editJiraIssue`.

### Title
- task: `[celery] <task_name> fails: <summary>`
- API: `Exception on <endpoint> [METHOD] — <error message>`

### Body
Skeleton — `templates/bug_ticket.adf.json`: replace the `{{...}}` placeholders and remove the `_comment` field.
Exactly these sections, no others (+ the collapsed translation at the end):
1. A paragraph: what fails, the exact error, env/version.
2. **Datadog evidence**: service, task/endpoint, count and window, hosts/countries (prod only), version. Numbers consistent with each other.
3. Link — ONLY the `logs_explorer_url` from the tool response.
4. **Trace** — a code block with the stack trace (no tokens or PII).
5. **Code origin**: file:line and function; commit (short SHA + link `cfg.bitbucket.commit_url`), date, PR (link from the API response),
   the Jira task in which it was implemented; confidence high/medium/low with a one-sentence justification; the ref the analysis ran on.
6. **Root cause** — a hypothesis, explicitly marked as a hypothesis (based on Code origin).
7. **Impact** — which flows, how many countries, since when.
8. At the end: _Auto-created by the Datadog error-triage routine for review._
9. Then the "Українською" expand with the translation of all the sections above.

Do not add sections like "Possible next step", "Suggested fix" or recommendations.

### Links
- **high/medium** confidence and the source task exists → `createIssueLink` of type `Problem/Incident` so that the new bug "is caused by" the source:
  `inwardIssue` = source task, `outwardIssue` = new bug. Then verify the direction with `getJiraIssue` and fix it if wrong.
- **low** → no link, only mention the task in Code origin as a "possible source".
- Regression → additionally `Relates` to the old closed ticket.
- Do not edit or comment on the source task — only the link.

## 5. Limits
- At most `cfg.limits.max_new_tickets_per_run` (15) new tickets per run. If there are more candidates — create the 15 with the highest counts
  and list the rest in the report and the summary.
- Other constraints (numbers, PII, secrets, failed calls) — `CLAUDE.md`.

## 6. Report (Artifact)
Do not design the page from scratch or hand-write the HTML — use only `templates/report.html`.
(The 25.09 PM and 26.09 PM reports were laid out by hand, each with its own design; the 26.09 PM layout had to be fixed after publishing.)
1. Copy the template to the scratchpad as `error-triage-<YYYY-MM-DD>-<am|pm>.html` (a new file per run → a new artifact).
2. Replace `{{TITLE}}` in `<title>` with `Error triage <YYYY-MM-DD> <AM|PM>` (Kyiv time).
3. Replace the contents of `<script id="report-data" type="application/json">` with real data following the schema in the comment above it.
   Do not change the rest of the markup, CSS or scripts. A field the schema lacks goes into `note` or `methodology`, not new markup.
4. Check: `python3 scripts/check_report.py <file> --screenshots <scratchpad>/shots`.
   Data errors → fix the data and rerun. Publish only on `"ok": true`.
   If errors remain after two fixes — publish anyway, but add each error to `failed_calls` (`call: "check_report"`) and to the session summary.
5. Publish via Artifact with `icon: "chart"` and a short `description`. Output the URL.

### Report data rules
Derived from the 25.09 PM and 26.09 PM reports; `check_report.py` checks them automatically.
- **One unit per metric.** `kpi.new` / `kpi.covered` / `kpi.noise` are the number of table **rows** of that class; `kpi.patterns` is their sum.
  The template computes per-class event volume from `patterns[].count`. (In 26.09 PM the "🚫 144" tile showed events while 🔴 2 / ✅ 7 next to it were patterns.)
- **Everything adds up to `kpi.total_errors`:** the sums of `by_service`, `by_country`, `hourly` and `patterns[].count` all equal the total.
  - Table: measured patterns as separate rows + one `Інфраструктурний шум (решта)` row of class `noise` = total − sum of the other rows.
    Its `note` lists only exact sub-counts from separate queries in the same window; write the unmeasured part as «поодинокі (≤N подій)» without an estimated number.
  - Countries: top 6 + `Інші (N країн)`; logs without a country tag — a separate `невідомо` row.
- **Hourly chart — exactly 12 one-hour buckets** from `window.from` (e.g. 08:57–09:57), labelled with the bucket start in Kyiv time.
  Not calendar hours: the window is not hour-aligned, so calendar hours give 13 points with partial edge bars (as in both reports).
- **Every table row has a real count > 0 in the window.** A ticket whose pattern did not occur in the window is not a table row
  (in 25.09 PM two ✅ rows showed «—»). Mention it in `methodology` if needed.
- **No approximations:** no `≈`, `~N`, «близько». No exact number — no number.
- **Consistency across sections:** a pattern's service, count and countries are the same in the table, in `created_tickets`, in the status conclusion and in the ticket itself
  (in 26.09 PM Twilio was `celery` in the table and `celery + uwsgi` in the ticket list). Every key in `created_tickets` is a `new` row in the table.
- **`status.level`** is computed only from the number of `new` rows using `cfg.status`; `status.rule` names the rule; `conclusion` is 2–3 sentences with the same numbers as the table.
- **`datadog_url`** is the `logs_explorer_url` of the query that produced that row's count. No separate query — `null`, not another row's URL.
- **Strict JSON:** no comments or trailing commas; write `</` inside strings as `<\/`. Broken JSON = an empty page.
- No PII or secrets (the script also catches emails, IPv4 addresses and phone-like numbers).

Page content (the template builds it from the data): window (UTC and Kyiv), run time; product status + rule + conclusion;
KPIs, hourly chart, breakdown by service and country; pattern table with Jira and Datadog links, and for 🔴 the source and confidence;
created tickets; candidates over the limit; methodology (exact query/filters, window, definition of every metric and status threshold,
how the source was determined and what the confidence levels mean, list of failed calls). The page is in Ukrainian.

## 7. Session summary
Product status; `check_report.py` result (ok or the list of errors); list of 🔴/✅/🚫 with ticket links; source tasks; ticket formatting check result
(language + expand + Component/s + Developer: "set" or "not set — <reason>", no names); artifact link.
