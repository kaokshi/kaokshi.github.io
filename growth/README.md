# Нулевая стоимость: локальный расчёт статистики (без сети)

Работа только с **обезличенными и разрешёнными** экспортами. Никаких паролей, токенов и действий в рекламном кабинете.

`python growth/report.py --organic /path/to/organic.csv --ads /path/to/ads.csv --output /path/to/weekly-report.md`

Формат organic.csv:
```csv
date,channel,article_id,impressions,opens,reads,subscribers_delta
2026-11-01,cinema,example-id,1000,50,30,2
```
Формат ads.csv:
```csv
date,channel,campaign_id,spend_rub,impressions,clicks,paid_subscriptions,paid_reads
2026-11-01,cinema,test-campaign,300,6000,40,3,20
```
Примеры цифр вымышленные, это **не текущая статистика**.

Не складывать `impressions` из разных систем в одну метрику охвата. Если целевые показатели отсутствуют, они оставляются пустыми, а стоимость цели считается как N/A. Сначала подтвердить соответствие колонок реальным экспортам платформы. Настоящие CSV держать вне публичного репозитория.
