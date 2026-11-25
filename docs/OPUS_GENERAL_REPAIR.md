# Ремонт четырёх регрессий — Opus, 26.11.2026

Ветка `development/opus-codex-handover-20261125`, только исходники, тесты и эта заметка.
Провайдеры не вызывались, заказы не создавались, `publication_enabled=false`, RSS, бюджеты,
модели и пороги не менялись. Локальный прогон тестов не выполнялся (доступны только
Read/Glob/Grep/Edit/Write); проверка — по коду и независимому полному прогону.

## 1. `render_diagram` падал с `NameError` после отрисовки

`src/editorial_routerai_diagrams.py:364` записывал в метаданные `"source": url`, но имя
`url` в модуле не определено. Ошибка возникала уже после успешной отрисовки, поэтому
любой шаблон, включая пещерные и астероидные, не возвращал результат. Исправлено на
`entry.source` — фактический источник выбранного шаблона из каталога. Это же устраняет
`NameError` в `test_known_cave_concepts_have_distinct_source_bound_templates` и
`test_asteroid_templates_reject_unrelated_sources_and_draw_distinct_outputs`.

## 2. `cave_diagram_replacement` терял `groundwater-scale`

`CAVE_TEMPLATES` собирался по `family == "cave"`, тогда как `groundwater-scale` — семейство
`water` с `eligible_from=("cave",)`. Допустимость применения в каталоге выражена полем
`eligible_from`, а `family` — только тематическая группировка для ранжирования. Теперь
набор строится по контракту применимости (`family == "cave"` или `"cave" in eligible_from`),
поэтому замена для «aquifer scale» возвращается, а отрисовка по-прежнему требует реальных
опубликованных значений USGS (`evidence_terms` из `WATER_STORAGE`) и падает закрыто без них.

## 3. Фикстура плана не проходила проверку вида

`test_plan_rejects_an_unrenderable_schematic_before_paid_stages` подавал обложку без
`kind` при `licensed_photos=True`. По контракту `plan_images` в этом режиме каждая строка
обязана явно указать `kind`, а набор видов должен быть ровно `{"photo","diagram"}`; фото
дополнительно требует `query`. Фикстура была неполной и корректно получала
`image_plan_kind_invalid`. Добавлены `kind: photo` и `query` обложке; проверка шаблона
теперь срабатывает и даёт `image_plan_template_invalid`. Продукционная логика не менялась,
старые проверки не ослаблены.

## Границы

- Каталог шаблонов остаётся данными: новая тема добавляется строкой `DiagramTemplate`, без
  правок логики восстановления.
- Никаких тематических исключений, ручных подстановок и обхода проверок источников.
- Вне области: `editorial_zen_executor.py`, план-чекпойнт, прочие файлы — не изменялись.
