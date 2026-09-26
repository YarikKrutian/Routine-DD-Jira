# Routine-DD-Jira

Логіка рутини `datadog-jira-triage`: двічі на день бере продакшн-помилки celery/uwsgi з Datadog за останні 12 годин,
класифікує їх (🔴 нове / ✅ покрито / 🚫 інфра-шум), шукає в Bitbucket коміт і Jira-задачу, що спричинили помилку,
створює баг-тікети в Jira та публікує HTML-звіт.

| Файл | Що в ньому |
|---|---|
| `CLAUDE.md` | сталий контекст і жорсткі правила (секрети, PII, збої викликів) |
| `.claude/skills/triage/SKILL.md` | покроковий процес тріажу |
| `config/triage.yaml` | запит, вікно, список шуму, ліміти, пороги статусу, теги |
| `scripts/code_origin.py` | blame / log -L / log -S + PR і Jira-ключі з Bitbucket → JSON |
| `templates/bug_ticket.adf.json` | каркас опису тікета в ADF з блоком "Українською" |
| `templates/report.html` | шаблон звіту; рутина підставляє лише JSON з даними |
| `ROUTINE_PROMPT.md` | короткий промпт, який має стояти в рутині |

## Як змінювати поведінку
- Новий тип інфраструктурного шуму, інший ліміт чи поріг статусу — правка `config/triage.yaml`.
- Зміна формату тікета — `templates/bug_ticket.adf.json` і розділ 4 скілу.
- Зміна вигляду звіту — `templates/report.html` (схема даних описана в коментарі над JSON-блоком).

## Перевірка скрипта локально
```bash
python3 scripts/code_origin.py --repo-dir <local clone> --no-clone --no-api --file path.py --line 42 --function fn
```
