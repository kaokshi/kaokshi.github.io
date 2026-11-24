# Аудит очистки редакции — 24.11.2026

Снимок интеграции: `2d1ea3a5c6050ac436c86fc8636a17b6fb64da80`, дерево `66cb6829344bbe8b4fcd62c2450160e7a78174ce`.
Ветка аудита независима от Codex; не объединять до окончания его разработки.
Проверено полное дерево, избранные исходники, регламент AGENTS.md, LEGACY_PROVIDER_RETIREMENT.md, конфигурация и триггеры ключевых workflows. Не является полной проверкой транзитивных зависимостей всех файлов.

## Инвентаризация

- 314 файлов, 20 каталогов (334 объекта дерева), 41 workflow.
- Суммарный размер blob текущего дерева 2 281 227 байт; это не размер истории Git и не квота хранилища.
- В отслеживаемом дереве не найдено путей node_modules, __pycache__, .venv, dist, .cache или файлов .pyc/.tmp/.log/.bak/.zip.
- Крупнейшие файлы — UI, рабочие исходники, документация, эталонные тесты. Массовая очистка по размеру неоправданна.

## P0: не удалять до развязки зависимостей

`src/editorial_zen_executor.py` использует `editorial_providers.Blocked`, `editorial_routerai_smoke` и `editorial_routerai_two_articles`; при выполнении также импортирует archive из `editorial_brain`. Последний напрямую импортирует `editorial_groq_budget`, `editorial_gemini_guard`, `klein` и text_call. `editorial_providers` напрямую импортирует Groq/Gemini utilities. `editorial_review` и `editorial_routerai_two_articles` также используют общие сетевые функции editorial_providers. Следовательно:
- Groq, Klein, Gemini: выводить из активного производства, но не удалять общий модуль, бюджетные утилиты и тесты до извлечения нужных shared функций и проверки всех потребителей.
- Не удалять файлы с названиями smoke/two_articles только по названию: это реальные зависимости нового исполнителя.
- Старая конфигурация editorial_production.json содержит лимиты и ключи режимов Groq/Klein/Gemini, прежний бюджет RouterAI 150/4500. Согласование с zen_core.json 300+50 и rolling 9000 выполняет Codex при миграции. Не менять параллельно.
- Сохранять CLIP/визуальные проверки, LanguageTool, Model A и другие независимые QA; их нельзя считать устаревшими провайдерами генерации.
- Сохранять редакционные материалы, историю, benchmark, журналы, ключи и приватное хранилище; paused cinema не означает удаление.

## P1: кандидаты на вывод после релиза, не удалены

- .github/workflows/editorial-production.yml: старое задание выполнения уже отключено literal false; оставлены PR-тесты и ручной storage-test. Перенести покрытие, потом убрать старую оболочку.
- .github/workflows/cloudflare-quota.yml и gemini-quota.yml: уже ручные read-only diagnostics, без расписания. Cloudflare Worker — действующий хостинг ZEN CONTROL, не удалять его и токены.
- .github/workflows/routerai-one-time-smoke.yml: ручной тест на main, проверить возможность убрать workflow после замены; сам модуль smoke используется исполнителем.
- .github/workflows/routerai-two-channel-evaluation.yml: оставить до подтверждения полной производственной замены.
- Одноразовые rollout/audit/probe workflows ZEN CONTROL (v1-approved-rollout, v1-isolated-probe, v1-probe-retest, mode-ui-rollout, stage-rollout, recovery-rollout, stage-api-diagnosis, probe-http-diagnosis, bridge-audit, connection-audit, v1-preflight): инвентаризация кандидатов, не утверждение, что все безопасно удалить. Recovery rollout пока не завершил проверку. Архивировать ссылки на успешные и неуспешные runs, убедиться, что новые стабильные deploy/preflight/rollback/CI заменяют их. Не удалять диагностические evidence и историю.
- Старые экспериментальные workflows research/cinema/manual/pilot: проверить ручные сценарии, тесты и зависимости владельца отдельно; не удалять автоматически.

## Безопасный порядок

1. Дождаться завершения Codex, зафиксировать новый head, повторить дерево и сравнить с этим снимком.
2. Составить граф импортов, динамических импортов, subprocess/module calls, workflow paths, требований, документации и тестов. Выделить общий HTTP/ошибки/архив из legacy, без изменения поведения.
3. В отдельном PR убрать только доказанно неиспользуемые маршруты и workflows, сохранить их историю в Git. Не переносить ключи, не раскрывать закрытые данные. Не удалять ветки Codex или приватные данные.
4. Запустить unit tests, cross-runtime mode/intake/recovery/finance tests, проверки расписания, RSS и build; убедиться, что все production/publication/RSS flags по-прежнему выключены. Не вызывать платные модели.
5. Согласовать итоговый diff с владельцем и Codex; объединить только после успешных проверок, предусмотреть revert.

## Результат этого прохода

Документ аудита добавлен в отдельную ветку. Ни один рабочий файл не удалён. Основная и интеграционная ветки не изменены. Запуск моделей, деплой и публикация не производились. Полный граф зависимостей и удаление остаются задачей после стабилизации текущего релиза.
