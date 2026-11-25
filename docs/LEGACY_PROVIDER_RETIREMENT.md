# Очистка редакционного контура ZEN CONTROL 1.0 RC2

Задача основана на аудите от 24 ноября 2026 года и RC2 из PR №90. Рабочая архитектура использует RouterAI, ZEN CORE и ZEN CONTROL. [Аудит](https://github.com/kaokshi/kaokshi.github.io/blob/audit/cleanup-20261124/docs/CLEANUP_AUDIT_20261124.md) · [PR №90](https://github.com/kaokshi/kaokshi.github.io/pull/90) · [Действующая инструкция](EDITORIAL_PRODUCTION.md)

| Компонент | Изменение |
| --- | --- |
| Groq, Klein, прежний OpenRouter | Удалены производственные вызовы, планировщик, резервирование старых квот и ключевые привязки из конфигурации и рабочих процессов |
| Gemini и Cloudflare Workers AI | Удалены диагностические опросы квот, локальные адаптеры и соответствующие тесты. Cloudflare Worker, Durable Objects и аварийное управление сохранены |
| Общие зависимости | Архив вынесен в `editorial_archive`, HTTPS и ошибки в `editorial_http`, действующие модели и запросы RouterAI в `editorial_routerai` |
| GitHub Actions | Убраны разовые проверки и развёртывания. Офлайн-регрессия выделена из старого производственного workflow. Штатное защищённое развёртывание и ручной защищённый пилот RouterAI сохранены |
| История | Сохранены архивные материалы, журнал расходов, редакционная память и проверка происхождения уже созданных пакетов |

Названия прежних провайдеров встречаются в исторических отчётах и читателях архивов. `python -m src.editorial_groq_ledger collect --day YYYY-MM-DD --file /private/historical-groq.json` читает исторические артефакты GitHub Actions. Идентификатор `editorial-production.yml` используется в проверке происхождения ранее зашифрованных пакетов. Токены и исторические артефакты GitHub не удаляются.

Откат кода выполняется отменой коммита очистки. Любой запуск платного контура, обновление Worker и включение выпуска требуют отдельного решения.

## История разовых Actions

Ссылки на историю сохранены вместе с диагностикой RC2. Результаты rollout с ошибками сохранены для диагностики. Штатный deploy проверяет привязки и точную версию ресурсов, а `rollback.mjs` откатывает к зафиксированной версии. [Развёртывание и откат](../telegram-control/README.md#read-only-upgrade-preflight-and-rollback)

- Успешные проверки интерфейса, этапов, публичного ответа и моста. [Mode UI](https://github.com/kaokshi/kaokshi.github.io/actions/runs/45438783525) · [Stage API](https://github.com/kaokshi/kaokshi.github.io/actions/runs/45444842375) · [Probe HTTP](https://github.com/kaokshi/kaokshi.github.io/actions/runs/45437885194) · [Bridge](https://github.com/kaokshi/kaokshi.github.io/actions/runs/45436593988) · [Connection](https://github.com/kaokshi/kaokshi.github.io/actions/runs/45431454045)
- История preflight и проб. [Preflight](https://github.com/kaokshi/kaokshi.github.io/actions/runs/45434370816) · [Isolated probe](https://github.com/kaokshi/kaokshi.github.io/actions/runs/45437211410) · [Probe retest](https://github.com/kaokshi/kaokshi.github.io/actions/runs/45438321019)
- Запуски rollout с ошибками. [V1](https://github.com/kaokshi/kaokshi.github.io/actions/runs/45435222694) · [Stage](https://github.com/kaokshi/kaokshi.github.io/actions/runs/45444557046) · [Recovery](https://github.com/kaokshi/kaokshi.github.io/actions/runs/45446058054)
