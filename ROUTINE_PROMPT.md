# Промпт рутини `datadog-jira-triage`

Текст, який має стояти в рутині після того, як до неї (або до її середовища) буде підключено цей репозиторій як джерело.
Уся логіка — у `CLAUDE.md`, `.claude/skills/triage/SKILL.md` і `config/triage.yaml`; промпт лише запускає її.

Налаштування, які лишаються в самій рутині (не в репозиторії):
- розклад: `CRON_TZ=Europe/Kyiv 57 8,20 * * *`;
- конектори: Atlassian, Datadog, JIJI-MCP-portal;
- змінні оточення середовища: `BITBUCKET_REPO_SLUG`, `BITBUCKET_EMAIL`, `BITBUCKET_API_TOKEN`;
- сповіщення: push.

---

```text
You are running automated triage of Jiji production errors. No human is present — act autonomously but carefully.

Read CLAUDE.md, then follow the skill .claude/skills/triage/SKILL.md step by step, using the parameters in config/triage.yaml.
Analysis window: strictly the last 12 hours (now-12h … now).

Do not commit or push to any repository, including this one.
If CLAUDE.md or the skill file is missing, stop and report that the repository is not attached to the routine — do not improvise the triage.
```
