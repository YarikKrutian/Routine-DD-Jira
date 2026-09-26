# `datadog-jira-triage` routine prompt

The text to set in the routine once this repository is attached to it (or to its environment) as a source.
All logic lives in `CLAUDE.md`, `.claude/skills/triage/SKILL.md` and `config/triage.yaml`; the prompt only starts it.

Settings that stay in the routine itself (not in the repository):
- schedule: `CRON_TZ=Europe/Kyiv 57 8,20 * * *`;
- connectors: Atlassian, Datadog, JIJI-MCP-portal;
- environment variables: `BITBUCKET_REPO_SLUG`, `BITBUCKET_EMAIL`, `BITBUCKET_API_TOKEN`;
- notifications: push.

---

```text
You are running automated triage of Jiji production errors. No human is present — act autonomously but carefully.

Read CLAUDE.md, then follow the skill .claude/skills/triage/SKILL.md step by step, using the parameters in config/triage.yaml.
Analysis window: strictly the last 12 hours (now-12h … now).

Do not commit or push to any repository, including this one.
If CLAUDE.md or the skill file is missing, stop and report that the repository is not attached to the routine — do not improvise the triage.
```
