# RouterAI — Claude Sonnet 5.5, независимый редактор

Интеграция в коде уже добавлена, но **платная генерация пока выключена**: `config/editorial_production.json` содержит `routerai_editor.enabled=false`, а GitHub workflow устанавливает `EDITORIAL_ROUTERAI_EDITOR_ALLOWED=false`. Смена этих полей без контроля баланса и суточной резервации бюджета **запрещена**.

## Подключение

1. В [RouterAI → API-ключи](https://routerai.ru/settings/api-keys) создать обычный API-ключ, предназначенный для нашей редакции. Код со страницы API (пример с `import requests`) в Secrets вставлять не нужно.
2. В [GitHub Actions Secrets](https://github.com/kaokshi/kaokshi.github.io/settings/secrets/actions) добавить единственный секрет `ROUTERAI_API_KEY`, значение — секретная строка ключа RouterAI. В чат и GitHub-код её не присылать.
3. Модель зафиксирована в `src/editorial_providers.py` как `anthropic/claude-sonnet-5.5`, API endpoint `POST https://routerai.ru/api/v1/chat/completions`; заголовок `Authorization: Bearer <secret>`. Формат совместим с OpenAI Chat Completions, SDK `requests` не нужен.
4. Автором остаётся Groq; `src/editorial_brain.py` назначает Claude только этапу `full-edit` при разрешённом RouterAI. Оценка исходной и отредактированной версии остаётся у внутренней системы QA; публикация без согласования не производится.
5. Бюджет-цель — **40 ₽/сутки** и **1200 ₽/30 дней**, максимум два запроса на статью, четыре на цикл. Это **пока конфигурационные цели, а не действующее ограничение расходов**. Не менять `enabled`/ `EDITORIAL_ROUTERAI_EDITOR_ALLOWED` до реализации и тестирования надёжного preflight: чтение текущего баланса с RouterAI, подтверждение стоимости конкретного запроса, долговечная (между запусками GitHub) резервация дневного бюджета и итоговая сверка фактического списания. Локальный подсчёт токенов сам по себе не гарантирует жёсткого денежного лимита; ещё один API-клиент может менять баланс.
6. После реализации guard сначала провести только безопасную проверку ключа/баланса без генерации и один ограниченный синтетический тест при подтверждённом платёжном пределе. Сохранить в приватном журнале расход в рублях; при сетевой ошибке, нехватке баланса или неизвестной цене **не делать запрос**.

Отдельная инфраструктура Gemini и Cloudflare не удаляется. OpenRouter снова не подключается.

См. [RouterAI быстрый старт](https://routerai.ru/docs/guides/overview/quickstart) и [описание модели](https://routerai.ru/models/anthropic/claude-sonnet-5.5).
