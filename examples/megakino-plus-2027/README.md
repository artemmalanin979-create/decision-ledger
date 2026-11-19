# Открытый кейс переносимости: контентные мультиподписки МегаФона («МегаКино» / партнёрские витрины / «МегаСилы») на 2027 год

Независимый открытый продуктовый кейс **вне антифрода и спама**, собранный из реальных публичных веб-снимков от **19 ноября 2026 года** и многолистовой книги `.xlsx`. Проверяет переносимость ядра `Decision Ledger` на задачу монетизации развлекательных подписок ПАО «МегаФон» при насыщении рынка экосистем и вступлении в силу **№ 376-ФЗ** (с 1 марта 2026 г.).

## Состав входных открытых данных (`initial/`)

| Источник | Тип | Происхождение и дата снимка (UTC) |
| --- | --- | --- |
| `web/megafon_megakino.md` | `web` | `https://moscow.megafon.ru/services/megakino/` (`2026-11-19T11:29:07Z`) — цена `399 ₽ / 30 дн.`, правило `3` бесплатных переключений кинотеатров (далее `30 ₽`), отдельная оплата Иви |
| `web/megafon_yandex_plus.md` | `web` | `https://moscow.megafon.ru/services/joy/yandex_plus/` (`2026-11-19T11:29:11Z`) — `30 дней бесплатно` на первом экране без вывода цены после промо в заголовок |
| `web/megafon_megasily.md` | `web` | `https://moscow.megafon.ru/services/megapowers/megasily/` (`2026-11-19T11:29:20Z`) — опции внутри линейки «МегаФон 3.0» |
| `web/cnews_icmr_2026.md` | `web` | `https://www.cnews.ru/news/line/2026-03-26_issledovanie_icmr_bolee_poloviny` (`2026-11-19T11:29:28Z`) — проникновение экосистемных подписок в городах РФ `>50%`, доля платящих `~38%` |
| `web/cnews_ecosystems_2026.md` | `web` | `https://www.cnews.ru/reviews/karta_rossijskih_tsifrovyh_ekosistem_2026/articles/top-5_trendov_na_rynke_rossijskih` (`2026-11-19T11:29:35Z`) — переход рынка от экстенсивного набора к удержанию и LTV |
| `web/rskrf_376fz_2026.md` | `web` | `https://rskrf.ru/tips/eksperty-obyasnyayut/kak-zashchitit-sebya-ot-avtomaticheskikh-spisaniy-za-podpiski/` (`2026-11-19T11:29:36Z`) — требования № 376-ФЗ с `01.03.2026` (ст. 16.1 ЗоЗПП) |
| `web/yandex_plus_449.md` | `web` | `https://3dnews.ru/1131783/stoimost-podpiski-yandeks-plyus-uvelichitsya-s-1-dekabrya` (`2026-11-19T11:29:40Z`) — повышение базового тарифа «Яндекс Плюс» до `449 ₽/мес` |
| `tables/subscription_benchmarks_2026.xlsx` | `table` | Двухлистовая книга OpenXML (`.xlsx`: `PublicTariffs` и `DataDictionary`), читаемая `stdlib`-парсером `ledger xlsx` без внешних зависимостей |
| `economics_inputs_2027.json` | `document` | Разделение публичных цен (`fact`) и сценарных модельных допущений (`assumption`: роялти, CVM `CAC 25 ₽` vs внешний платный `CAC 290 ₽`, удержание) без предзаготовленных вариантов |
| `brief_2027.md` | `document` | Управленческая рамка владельца направления (целевой вклад `>= 35%`, соблюдение `376-ФЗ`, фиксация закрытых метрик биллинга как `unknown`) |

## Событие пересмотра (`later/`)

- `later/megafon_plus_20260604.md` — реальный снимок официального информационного сообщения ПАО «МегаФон» (`https://moscow.megafon.ru/help/info/popular/information/20260604-1108.html`, снят `2026-11-19T11:29:42Z`): индексация подписки **«МегаФон Плюс»** с `299 -> 345 ₽` и с `399 -> 460 ₽` за 30 дней. Регистрируется во втором сеансе через `ledger source ... --updates subscription_benchmarks_2026 economics_plans`.

Результаты реального двухсессионного агентного прогона с верифицированными логами находятся в [`demo/megakino-plus-2027/`](../../demo/megakino-plus-2027/README.md).
