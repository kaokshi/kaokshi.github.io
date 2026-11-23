# Мониторинг лимитов моделей — безопасное подключение

Статус: отдельный модуль, PR в main. **Не меняет производство, публикация и RSS выключены.**

## Архитектура доступа

- Отчёты записываются **только** в закрытый репозиторий `kaokshi/zen-editorial-review` по пути `quota-monitor/latest.json` и в `quota-monitor/history/`. GitHub connector ChatGPT может прочитать эти отчёты по просьбе владельца без доступа к секретам.
- Скрипт `src/editorial_quota_monitor.py` работает в GitHub Actions с секретами; он не публикует ключи и не изменяет настройки аккаунтов.
- `groq`: существующий `GROQ_API_KEY`. Для получения реальных rate headers нужна **одна минимальная генерация** в Groq — это расходует один запрос, поэтому стандартное расписание ограничено двумя проверками в день. Запросы в день и токены в минуту — разные окна.
- `cloudflare`: отдельный `CLOUDFLARE_ANALYTICS_API_TOKEN`, только чтение Analytics; он **не** используется для генерации и не получает права записи.
- `gemini`: действующий API-ключ проверяет только доступность. Точного остатка в каталоге моделей нет. Опционально запрашиваются Cloud Monitoring метрики через **Workload Identity Federation** без долгоживущего ключа сервисного аккаунта. На бесплатном Gemini метрики могут быть недоступны; это отображается как `not_available`, а не подставляется ноль.
- Сумма расхода Gemini за сутки UTC не равна официальному остатку RPD. Актуальные лимиты и usage подтверждаются в [Google AI Studio](https://aistudio.google.com/usage).

## Владелец: подключить Cloudflare за 3 минуты

1. Откройте [Cloudflare → My Profile → API Tokens](https://dash.cloudflare.com/profile/api-tokens), выберите **Create Token → Create Custom Token**.
2. Название: `Zen Quota Viewer`. Добавьте **Account → Account Analytics → Read**. В *Account Resources* укажите **только свой аккаунт**, где запущен Workers AI. Другие права, включая Edit/Write, не добавляйте. Ограничение IP допускается лишь если GitHub-hosted runner сможет его использовать (обычно нет).
3. Скопируйте токен непосредственно в [GitHub → Settings → Secrets and variables → Actions](https://github.com/kaokshi/kaokshi.github.io/settings/secrets/actions). **New repository secret**: `CLOUDFLARE_ANALYTICS_API_TOKEN`. Не отправляйте токен в ChatGPT, в коммиты или в issue.
4. Существующие `CLOUDFLARE_ACCOUNT_ID` и `CLOUDFLARE_API_TOKEN` оставьте без изменений. Генерация и мониторинг используют **разные** ключи.

Точное подтверждение остатков доступно только если Cloudflare GraphQL вернёт данные. Аналитика может отставать, а расчёт free-квоты ориентировочный.

## Groq

Дополнительный токен не нужен. Пробный запуск подтвердил API-заголовки `x-ratelimit-remaining-requests` (дневные запросы) и `x-ratelimit-remaining-tokens` (минутные токены). Не интерпретируйте TPM как суточный баланс.

## Google Gemini — опциональный доступ, не блокирует работу

Убедитесь, что ключ Gemini привязан к ожидаемому Google Cloud project. Для считывания **Cloud Monitoring**, не для доступа к Gemini-моделям:

1. Откройте Google Cloud Console, выберите проект Gemini.
2. Создайте отдельный сервисный аккаунт `zen-quota-viewer`, назначьте **Monitoring Viewer** (`roles/monitoring.viewer`) на этот проект. Не назначайте Owner, Editor, Service Account Token Creator или Project Editor.
3. Настройте **Workload Identity Federation** для GitHub Actions, ограничьте доверие репозиторием `kaokshi/kaokshi.github.io` и веткой `refs/heads/main`. Дайте идентификатору пула право `Workload Identity User` для **только** этого сервисного аккаунта.
4. В GitHub Secrets добавьте `GOOGLE_WIF_PROVIDER` (полный путь `projects/NUMBER/locations/global/workloadIdentityPools/POOL/providers/PROVIDER`) и `GOOGLE_QUOTA_VIEWER_SA_EMAIL`.
5. В GitHub Actions **Variables**, не Secrets, добавьте `GOOGLE_QUOTA_MONITOR_PROJECT` со строковым идентификатором этого же проекта.
6. Workflow получает короткоживущий токен с `monitoring.read`, смотрит только доступные метрики. Если бесплатный план не позволяет запрос Cloud Monitoring или нет данных, отчёт честно фиксирует неизвестный остаток. **Никаких платных функций Google ради квот не включать** без отдельного согласования.

Для простого ручного просмотра в Google AI Studio откройте [Usage](https://aistudio.google.com/usage) и [Rate limits](https://aistudio.google.com/rate-limit). Не сообщайте в чат API-ключ.

## Запуск

Workflow: `.github/workflows/provider-quota-monitor.yml`.

- Пока PR не включён в `main`, тестовая ветка запускает один контрольный прогон после добавления workflow.
- После проверки и слияния в `main`: автоматические запуски **07:30 и 10:30 МСК**, плюс ручной `workflow_dispatch` через интерфейс GitHub Actions.
- Приватный отчёт: `https://github.com/kaokshi/zen-editorial-review/blob/main/quota-monitor/latest.json`. История: `quota-monitor/history/`.
- При сбое записи в GitHub внешний API не вызывается повторно: отчёт сохраняется в Actions artifact, можно повторить только публикацию отчёта.
- На время разработки Codex изменений в `main` не вносить до проверки PR. Включение этого мониторинга не включает редакционную публикацию и RSS.

## Значения статусов

- `inference_headers_captured` — измерены Groq RPD/TPM в момент запроса.
- `analytics_observed` — Cloudflare предоставил подсчёт Neurons, остаток приблизителен.
- `read_only_analytics_token_not_configured` — требуется отдельный токен Analytics Read.
- `model_catalog_accessible` — Gemini-ключ действителен; это **не** измеренный остаток.
- `not_configured` / `not_available` — Google Cloud Monitoring не подключён или не показывает данные; не интерпретировать как отсутствие квоты.

Ни одна из этих проверок не гарантирует защиту от превышения лимитов при параллельной работе редакции: локальные бюджеты и fail-closed-политики сохраняются.
