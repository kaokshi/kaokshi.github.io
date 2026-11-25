# Аудит снапшота после PR99–104 — Opus, 26.11.2026

База: `development/opus-codex-handover-20261125`. Продовый run 45694877554 не затрагивался,
checkpoint'ы не перезаписывались, живых вызовов и живых утверждений нет.
Изменения только в исходниках и тестах; publication/RSS/лимиты/модели не менялись.

## Исправлено (1 критический воспроизводимый дефект)

**1. `src/editorial_zen_executor.py:186-188` — незавершённый блок плана валит executor без записи стадии.**
Было: `plan = json.loads(json.loads(data)["output"])` и `plan["title"]`. Ответ платной модели
уже сохранён в `qwen-plan.json` (`Session.save` → `store.immutable`), после чего отсутствие
`title` даёт `KeyError`, а нестроковый/невалидный JSON — `ValueError`/`TypeError`.
`execute()` перехватывает только `(Blocked, CoreBlocked, UnsafeToTest)` (`:452`), поэтому
исключение уходит наружу: ни `stage_result`, ни `blocked`-запись не создаются, снапшот не
печатается. При повторном запуске checkpoint читается заново и падение повторяется —
статья навсегда застревает без причины, пригодной для `recovery_status`.
Исправление: тело плана разбирается в `try/except`, регистрация заголовка выполняется только
для строки длиной 4–240. Дальнейшая проверка остаётся за `field(plan,"title",180)`
(`editorial_routerai_two_articles.py:766`), которая даёт штатный `UnsafeToTest`.
Регрессия: `tests/test_editorial_zen_plan_checkpoint.py` (синтетические фикстуры, без
реального текста статей).

## Подтверждённые наблюдения по коду (не исправлялись)

**2. `editorial_zen_core.py:389-394` — после owner-release стадии `images` платное
пересоздание слота невозможно.** `classify()` (`editorial_zen_executor.py:46-48`) для
`image-NN-attempt-1` возвращает `missing_image`, а `reserve_call` требует
`image_correction`, если `prior >= 1`. Строки `spend` при `clear_blocked_stage`
(`:436-471`) сохраняются специально, поэтому любой ранее оплаченный слот даёт
`invalid_image_attempt_reason`. Причина не входит ни в `AUTO_RETRY`, ни в
`OWNER_RELEASE` (`editorial_zen_recovery.py:8-16, 62`) → тупик восстановления.
Сейчас недостижимо: `config/zen_core.json:9` задаёт `licensed_photos`, `MeteredRouter`
даёт `image_price = 0` и `paid_image_generation_disabled` (`editorial_zen_executor.py:94`).
Затрагивает только будущий режим `seedream`; менять финансовую/слотовую семантику без
согласования владельца не стал.

**3. `editorial_routerai_diagrams.py:45-58` — замена астероидной схемы не привязана к
исходному шаблону.** `cave_diagram_replacement` проверяет `template == "cave-formations"`
(`:23`), астероидная ветка ключуется только по `subject`. Сцена кометы или пещеры с
подстрокой `risk probability` / `close approaches` / `deflect` молча подменится на
астероидную схему. Это зафиксировано существующим тестом
(`tests/test_editorial_routerai_diagrams.py:50-64`), поэтому это осознанный контракт
(каталог допускает только `asteroid-observations`), а не регрессия; остаточный риск —
коллизия подстрок между темами. Требует решения владельца, не патча.

**4. `editorial_routerai_two_articles.py:346-374` (`science_asset`) — выдача фото
детерминирована смещением `attempt-1`.** Кэш запросов общий на статью, `seen` — локальный,
поэтому повторный выбор того же `asset_url` между слотами ловится только
`duplicate_reason` в `ImageIndex.save` (`editorial_images.py:171-177, 216`). Это
работает, но третий кандидат недоступен без трёх неудач QA; при исчерпании
`commons_photos` статья уходит в `verified_photo_unavailable` → `image_qa=failed`.
Наблюдение, не дефект.

**5. `editorial_zen_executor.py:296-321` (`Session.document`) и `editorial_package.py:194-206`.**
`p.text` для CTA-гиперссылки (`add_word_hyperlink`, `:44-66`) попадает в
`Paragraph.text` только в python-docx ≥ 1.1 (обход `w:hyperlink`). При более старой
версии round-trip даст `document_roundtrip_failed`. Версия в окружении не проверялась —
**живое неизвестное**, требует сверки с `requirements-production.txt` при офлайн-прогоне.

**6. Финансы.** `budget()` (`editorial_zen_core.py:336-357`) считает rolling по
резервам, а не по факту, `settle`/`reconcile` (`:417-488`) не дают расширять резерв и
оставляют `actual > резерва` в `pending` — расхождений с AGENTS.md не нашёл.

## Живые неизвестные (проверить нельзя без сети и приватного хранилища)

- Фактические статусы шести статей и остаток бюджета ключа Opus.
- Доступность терминов `ASTEROID_DIAGRAMS`/`CAVE_SOURCE` на живых страницах ESA/NPS.
- Состав каталога `sources/cinema.json` и валидность `private_path` для stills.
- Реальная версия python-docx в проде.
