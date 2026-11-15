#!/usr/bin/env python3
"""Build the reproducible end-to-end demonstration showcase in demo/retail-continuity/."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - allows pure-stdlib execution when UX PNG already exists
    Image = ImageDraw = ImageFont = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _font(bold: bool, size: int):
    candidates = (
        ["/usr/share/fonts/rsms-inter-fonts/Inter-Bold.ttf", "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf"]
        if bold
        else ["/usr/share/fonts/rsms-inter-fonts/Inter-Regular.ttf", "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf"]
    )
    for path in candidates:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def generate_ux_mockup(dest: Path) -> None:
    """Create a crisp UI artifact showing (left) ambiguous legacy_gateway POS toast and (right) overclaiming promo draft."""
    img = Image.new("RGB", (1280, 560), "#F1F5F9")
    d = ImageDraw.Draw(img)

    # Left card: POS terminal screen on legacy_gateway
    d.rounded_rectangle((36, 36, 620, 524), 16, fill="#FFFFFF", outline="#CBD5E1", width=2)
    d.text((64, 58), "ЭКРАН КАССЫ · LEGACY_GATEWAY (СТЕНД)", font=_font(True, 15), fill="#475569")
    d.rounded_rectangle((64, 94, 592, 134), 8, fill="#ECFDF5", outline="#6EE7B7", width=1)
    d.text((80, 105), "Сеть: Проводной канал недоступен -> LTE резерв активен (18 с)", font=_font(False, 14), fill="#065F46")

    # Misleading green gateway modal
    d.rounded_rectangle((64, 156, 592, 340), 12, fill="#F0FDF4", outline="#22C55E", width=2)
    d.text((88, 178), "ШЛЮЗ ОПЛАТЫ: ПРИНЯТО В ОЧЕРЕДЬ #1048", font=_font(True, 18), fill="#15803D")
    d.text((88, 214), "Сумма: 1 480,00 ₽ · Безналичная оплата", font=_font(False, 15), fill="#1E293B")
    d.text((88, 246), "Локальный шлюз сохранил запрос и вернёт статус", font=_font(False, 14), fill="#475569")
    d.text((88, 268), "после синхронизации очереди с банком-эквайером.", font=_font(False, 14), fill="#475569")
    d.rounded_rectangle((88, 298, 290, 326), 6, fill="#16A34A")
    d.text((108, 304), "Закрыть окно (ОК)", font=_font(True, 13), fill="#FFFFFF")

    # Grey fiscal footer warning
    d.rounded_rectangle((64, 362, 592, 432), 8, fill="#FFFBEB", outline="#FCD34D", width=1)
    d.text((84, 375), "Фискальный регистратор: ЧЕК НЕ СФОРМИРОВАН", font=_font(True, 14), fill="#92400E")
    d.text((84, 399), "Финальный ответ платёжного хоста ещё не получен.", font=_font(False, 13), fill="#92400E")
    d.text((64, 462), "Риск UX: зелёное окно провоцирует отпустить товар до сверки.", font=_font(True, 14), fill="#B91C1C")

    # Right card: Marketing landing draft with overclaim
    d.rounded_rectangle((660, 36, 1244, 524), 16, fill="#0F172A", outline="#334155", width=2)
    d.text((688, 58), "ЧЕРНОВИК БАННЕРА МАРКЕТИНГА (НА ПРОВЕРКУ)", font=_font(True, 15), fill="#94A3B8")
    d.text((688, 112), "Резервный интернет", font=_font(True, 32), fill="#F8FAFC")
    d.text((688, 152), "для вашей кассы", font=_font(True, 32), fill="#5EEAD4")
    d.text((688, 212), "Ни одной потерянной продажи при любом обрыве!", font=_font(True, 18), fill="#FDE047")
    d.text((688, 248), "• 100% оплат проходят автоматически без участия кассира", font=_font(False, 15), fill="#E2E8F0")
    d.text((688, 278), "• Подходит для любой онлайн-кассы и любого шлюза", font=_font(False, 15), fill="#E2E8F0")
    d.text((688, 308), "• Всего 2 490 ₽/мес — подключим за 15 минут", font=_font(False, 15), fill="#E2E8F0")

    d.rounded_rectangle((688, 368, 1216, 492), 10, fill="#450A0A", outline="#F87171", width=1)
    d.text((708, 384), "БЛОКИРОВКА AUDIT-CLAIMS:", font=_font(True, 14), fill="#FCA5A5")
    d.text((708, 410), "1. Слоган «ни одной потерянной продажи» и «100%» не подтверждены", font=_font(False, 13), fill="#FECACA")
    d.text((708, 432), "   (не покрывают отключение питания, сбой эквайера и мёртвые зоны LTE).", font=_font(False, 13), fill="#FECACA")
    d.text((708, 454), "2. Обещание «на любой кассе» противоречит риску в legacy_gateway.", font=_font(False, 13), fill="#FECACA")

    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest)


def run_ledger(*args: str) -> str:
    res = subprocess.run(
        [sys.executable, "-m", "ledger", *args],
        cwd=ROOT, encoding="utf-8", capture_output=True, check=True,
    )
    return res.stdout


def build_proposal_v1(shas: dict[str, str]) -> dict:
    return {
        "title": "Резервный интернет с проверкой кассы — решение комитета (до финальной сверки)",
        "summary": (
            "Рекомендуется подготовить ограниченный платный пилот настроенного LTE-резерва (до 20 магазинов) "
            "только для совместимых касс direct_pos, отложив фактический допуск до завершения двухдневной сверки чеков. "
            "Кассы со старым шлюзом legacy_gateway исключаются из первой волны: при 0/20 ложных успехов верхняя граница "
            "95% интервала Вильсона составляет 16,11% (выше порога 3%), а UX-разбор выявил риск вводящего в заблуждение окна очереди."
        ),
        "reviewed": {
            "brief": {"sha": shas["brief"], "note": "Запрос команды МСБ о помощи магазинам при падении проводной связи"},
            "dictionary": {"sha": shas["dictionary"], "note": "Методика стенда, окна 60/120 с и правило финальной сверки"},
            "market": {"sha": shas["market"], "note": "Открытые первичные описания Verizon, Digi и Square"},
            "pilot": {"sha": shas["pilot"], "note": "Предварительный срез стендовых испытаний (до сверки логов эквайера)"},
            "manager": {"sha": shas["manager"], "note": "Рамки комитета, пороги 90%/3% и гипотезы юнит-экономики"},
            "economics": {"sha": shas["economics"], "note": "Структурированная модель доходов, прямых и разовых затрат"},
            "ux_mockup": {"sha": shas["ux_mockup"], "note": "Макет экрана кассы legacy_gateway и черновика промо-баннера"},
            "ux_notes": {"sha": shas["ux_notes"], "note": "Карточка UX-аудита экрана кассы и маркетинговых обещаний"},
            "synth_probe": {"sha": shas["synth_probe"], "note": "Синтетический прогон владельца магазина (генерация гипотез трения)"},
        },
        "findings": {
            "scope_and_thresholds": {
                "kind": "fact",
                "text": "Комитет выбирает один формат на месяц (до 20 магазинов с 1–2 кассами и рабочим LTE). Для обещания проверки кассы требуются >=90% завершённых покупок и <=3% ложных успехов в целом и в каждом допускаемом сегменте, а также маржинальный вклад >=45%.",
                "citations": [
                    {"source": "brief", "locator": "Записка команды малого бизнеса"},
                    {"source": "manager", "locator": "Абзацы 1–3, пороги допуска и экономики"},
                ],
            },
            "market_mechanisms": {
                "kind": "fact",
                "text": "Открытые источники подтверждают, что LTE-резерв требует проверки сигнала на адресе и резервного питания (Verizon), сетевой пинг проверяет только связность маршрута (Digi), а офлайн-очередь платежей может отклоняться после синхронизации (Square).",
                "citations": [{"source": "market", "locator": "Пункты M1, M2, M3"}],
            },
            "direct_pos_preliminary": {
                "kind": "observation",
                "text": "В сегменте direct_pos настроенный резерв показал 238/240 (99,17%) восстановления сети и 232/240 (96,67%, 95% ДИ Вильсона [93,56%; 98,30%]) предварительных покупок при 0/240 (ДИ [0,00%; 1,57%]) выявленных ложных успехов — оба интервала укладываются в пороги 90%/3% до сверки.",
                "citations": [
                    {"source": "pilot", "locator": "group=managed_failover, cohort=direct_pos"},
                    {"source": "dictionary", "locator": "Определения drills, checkout_completed, false_success"},
                ],
            },
            "legacy_gateway_wilson_risk": {
                "kind": "observation",
                "text": "В сегменте legacy_gateway всего 20 прогонов на 4 конфигурациях: при точечной оценке 0/20 ложных успехов верхняя граница 95% ДИ Вильсона равна 16,11% (>3% лимита), то есть выборка статистически не подтверждает безопасность до сверки.",
                "citations": [{"source": "pilot", "locator": "group=managed_failover, cohort=legacy_gateway"}],
            },
            "ux_and_promo_barrier": {
                "kind": "observation",
                "text": "На экране legacy_gateway зелёная плашка «Принято в очередь» появляется при несформированном чеке, а черновик баннера маркетинга содержит неподтверждённый слоган про 0 потерянных продаж на любой кассе.",
                "citations": [
                    {"source": "ux_mockup", "locator": "Левая и правая панели UI"},
                    {"source": "ux_notes", "locator": "Пункты 1–2 разбора"},
                ],
            },
            "cashier_friction_hypothesis": {
                "kind": "hypothesis",
                "text": "Основной операционный страх магазина в час пик — зависание между оплатой и чеком у сменного кассира; ручной комплект SIM+роутер даёт на стенде лишь 83,46% завершённых покупок и не снимает этот барьер.",
                "citations": [
                    {"source": "synth_probe", "locator": "Операционный сценарий смены кассира"},
                    {"source": "pilot", "locator": "group=self_service, 217/260 завершений"},
                ],
            },
            "unit_economics_headroom": {
                "kind": "inference",
                "text": "По модели экономики настроенный резерв даёт вклад 1 290 ₽/мес (51,81% от 2 490 ₽ при прямых затратах 1 200 ₽ и разовых вложениях 6 000 ₽ на 12 мес). Запас до порога 45% составляет всего 169,50 ₽/мес (+14,1% к затратам): рост прямых затрат на +15% пробивает целевую маржу, а окупаемость разовых затрат из операционного потока занимает 3,35 месяца.",
                "citations": [{"source": "economics", "locator": "plans.managed_failover"}],
                "depends_on": ["scope_and_thresholds"],
            },
            "field_demand_and_recount_unknown": {
                "kind": "unknown",
                "text": "Итоговая сверка журналов эквайера и чеков ещё не завершена; фактическая готовность магазинов платить 2 490 ₽/мес, 12-месячное удержание и стоимость исправления старых шлюзов неизвестны.",
                "citations": [
                    {"source": "dictionary", "locator": "Раздел про предварительный характер pilot.csv"},
                    {"source": "manager", "locator": "Исключения финансовой модели"},
                ],
            },
            "gate_recommendation": {
                "kind": "inference",
                "text": "Оптимальный шаг до сверки — готовить протокол и приёмку платного пилота исключительно для direct_pos с жёстким гейтом допуска после сверки, заблокировать агрессивный рекламный баннер и отложить legacy_gateway.",
                "depends_on": [
                    "scope_and_thresholds",
                    "market_mechanisms",
                    "direct_pos_preliminary",
                    "legacy_gateway_wilson_risk",
                    "ux_and_promo_barrier",
                    "unit_economics_headroom",
                    "field_demand_and_recount_unknown",
                ],
            },
        },
        "options": {
            "keep_stand": {
                "title": "Оставить текущую схему и провести только стендовую досверку",
                "type": "keep",
                "case": "Не тратит квоту подрядчика до полной ясности по всем типам касс, включая legacy_gateway.",
                "objection": "Теряется месяц полевой проверки спроса и фактических затрат на сегменте direct_pos, который уже проходит интервальные границы на стенде.",
                "depends_on": ["direct_pos_preliminary", "field_demand_and_recount_unknown"],
            },
            "self_service_kit": {
                "title": "Запустить коробку SIM + LTE-роутер для самостоятельного переключения",
                "type": "change",
                "case": "Проще логистика, расчётный вклад 57,36% (740 ₽ из 1 290 ₽), разовые вложения вдвое ниже (2 400 ₽).",
                "objection": "Проходит только 83,46% покупок на стенде (ниже порога 90%) и перекладывает переключение на кассира в момент аварии — обещать защиту кассы нельзя.",
                "depends_on": ["cashier_friction_hypothesis", "scope_and_thresholds"],
            },
            "managed_failover": {
                "title": "Подготовить платный пилот настроенного резерва для direct_pos (допуск после сверки)",
                "type": "change",
                "case": "Сочетает высокую долю завершённых покупок в direct_pos (96,67%), проверяемую на месте приёмку кассы и проверку реальной готовности платить 2 490 ₽/мес на 20 точках.",
                "objection": "Узкий запас по прямым затратам (169,50 ₽/мес до порога 45%) и зависимость допуска от результатов финальной сверки журналов.",
                "depends_on": ["gate_recommendation", "unit_economics_headroom"],
            },
            "close_initiative": {
                "title": "Закрыть инициативу и переключить команду на другой продукт портфеля",
                "type": "close",
                "case": "Освобождает команду и подрядчика от операционно сложного продукта с выездной инсталляцией.",
                "objection": "Отказ от проверки подтверждённого клиентского запроса при наличии рабочего сегмента direct_pos без затрат на масштабный запуск.",
                "depends_on": ["scope_and_thresholds", "field_demand_and_recount_unknown"],
            },
        },
        "recommended": "managed_failover",
        "strategy": {
            "product": {
                "text": (
                    "Целевой сегмент первой волны: независимые магазины с 1–2 кассами только на схеме direct_pos (до 20 точек). "
                    "Осознанные отказы: кассы с legacy_gateway не допускаются в пилот до сверки и разбора UX-окна очереди; коробочный роутер параллельно не запускаем. "
                    "Состав услуги за 2 490 ₽/мес (без НДС): проверка сигнала LTE в помещении, настройка автопереключения, обязательная контрольная оплата с выходом фискального чека при выдернутом кабеле, памятка кассиру и повторная приёмка при смене кассового ПО. "
                    "Экономическая граница: держать прямые затраты <=1 369,50 ₽/мес на точку (базовый план 1 200 ₽/мес)."
                ),
                "depends_on": ["gate_recommendation", "unit_economics_headroom", "cashier_friction_hypothesis"],
            },
            "communication": {
                "text": (
                    "Снять черновик баннера про бесперебойную работу любой кассы. "
                    "Ключевое сообщение владельцу магазина перед подключением (при условии совместимой кассы direct_pos, подтверждённого LTE в помещении, рабочего питания и доступности платёжного сервиса): "
                    "«Настроим автоматический переход на LTE на случай обрыва проводного интернета и при подключении вместе проверим на вашей кассе, что проходит контрольная оплата и печатается чек. Оставим короткую инструкцию кассиру, как проверить статус операции перед повторным списанием». "
                    "Канал: прямой пресейл-звонок по квалифицированной базе + карточка приёмки на кассе."
                ),
                "depends_on": ["market_mechanisms", "ux_and_promo_barrier", "gate_recommendation"],
            },
            "experiment": {
                "text": (
                    "Шаг 1 (гейт допуска, 2 рабочих дня): получить финальную сверку логов касс, эквайера и чеков. Критерий открытия пилота для direct_pos: >=90% завершённых покупок и <=3% ложных успехов. "
                    "Шаг 2 (полевой платный пилот, до 30.11.2026): предложить подключение до 20 магазинам direct_pos, замерить конверсию в оплату (ориентир продолжения >=5 платящих точек из 20), фактическое время выезда/поддержки и прямые затраты на точку. "
                    "Стоп-правило: при любом подтверждённом ложном успехе на живой кассе или превышении потолка затрат 1 369,50 ₽/мес без пути снижения — остановить новые подключения и вынести пересмотр на комитет."
                ),
                "depends_on": ["scope_and_thresholds", "direct_pos_preliminary", "unit_economics_headroom", "field_demand_and_recount_unknown"],
            },
        },
        "actions": [
            {
                "id": "verify_recount",
                "owner": "Аналитик пилота",
                "due": "2026-11-10",
                "task": "Провести поштучную сверку логов кассы, платёжного эмулятора и фискальных чеков по всем 260 прогонам",
                "trigger": "Любое расхождение статуса оплаты переводит кейс на пересмотр допуска",
            },
            {
                "id": "fix_promo_copy",
                "owner": "Продуктовый маркетолог",
                "due": "2026-11-11",
                "task": "Заменить заблокированный макет лендинга на формулировку с проверкой совместимой кассы direct_pos и явными условиями",
                "trigger": "Появление в материалах обещаний без оговорки питания, LTE и типа кассы",
            },
            {
                "id": "prepare_field_protocol",
                "owner": "Руководитель продукта",
                "due": "2026-11-14",
                "task": "Подготовить чек-лист выездной приёмки на 20 точек direct_pos и карточку кассира",
                "trigger": "Провал порога >=90% или <=3% после сверки блокирует старт подключений",
            },
        ],
    }


def build_proposal_v2(shas: dict[str, str]) -> dict:
    prop = build_proposal_v1(shas)
    prop["title"] = "Резервный интернет с проверкой кассы — Решение №2 (после финальной сверки)"
    prop["summary"] = (
        "Финальная сверка подтвердила скрытый провал старого шлюза: при общем результате 94,23% покупок и 2,69% ложных успехов "
        "сегмент legacy_gateway упал до 14/20 (70,00%) покупок и 6/20 (30,00%) ложных успехов, тогда как direct_pos удержал "
        "231/240 (96,25%) и 1/240 (0,42%). Открываем платный пилот до 20 точек строго для direct_pos; для legacy_gateway — запрет "
        "продаж и инженерная локализация рассинхронизации очереди."
    )
    prop["reviewed"]["pilot"] = {
        "sha": shas["pilot"],
        "note": "Финальная сверка recount.csv по тем же стендовым испытаниям (заменила предварительный срез)",
    }
    prop["findings"]["direct_pos_preliminary"] = {
        "kind": "observation",
        "text": "После финальной сверки direct_pos проходит оба защитных порога с запасом: 231/240 (96,25%, 95% ДИ Вильсона [93,03%; 98,02%]) завершённых покупок (+39,17 п.п. к текущей схеме) и 1/240 (0,42%, ДИ [0,07%; 2,32%]) ложных успехов.",
        "citations": [{"source": "pilot", "locator": "recount.csv: group=managed_failover, cohort=direct_pos"}],
    }
    prop["findings"]["legacy_gateway_wilson_risk"] = {
        "kind": "observation",
        "text": "Сверка выявила маскировку провала средним (masked_segment_failure): в целом по managed_failover 245/260 (94,23%) и 7/260 (2,69%) укладываются в 90%/3%, но в legacy_gateway при 20/20 восстановления сети завершились лишь 14/20 (70,00%) покупок, а 6/20 (30,00%, ДИ [14,55%; 51,90%]) оказались ложным успехом.",
        "citations": [{"source": "pilot", "locator": "recount.csv: group=managed_failover, cohort=legacy_gateway"}],
    }
    prop["findings"]["field_demand_and_recount_unknown"] = {
        "kind": "unknown",
        "text": "Стендовая сверка завершена; открытыми остаются полевой платёжный спрос по 2 490 ₽/мес, фактические выездные затраты подрядчика и техническая причина 6 ложных успехов в legacy_gateway.",
        "citations": [{"source": "manager", "locator": "Гипотезы и исключения экономики"}],
    }
    prop["findings"]["gate_recommendation"]["text"] = (
        "Гейт сверки пройден для direct_pos и провален для legacy_gateway: разрешить коммерческий пилот до 20 магазинов строго на direct_pos с обязательной контрольной покупкой при приёмке, а legacy_gateway заблокировать в пресейле и отправить на разбор логов."
    )
    prop["options"]["managed_failover"]["title"] = "Запустить платный пилот до 20 точек строго для direct_pos (legacy_gateway заблокирован)"
    prop["options"]["managed_failover"]["objection"] = (
        "Узкий запас по прямым затратам (169,50 ₽/мес до порога 45% — рост затрат на +15% пробивает целевую маржу) "
        "и пока не измеренное удержание на горизонте 12 месяцев для возврата 6 000 ₽ разовых вложений."
    )
    prop["strategy"]["product"]["text"] = (
        "Разрешён запуск платного пилота (до 20 магазинов, 1–2 кассы) исключительно для проверенных конфигураций direct_pos. "
        "Жёсткий фильтр на входе: магазины с legacy_gateway отклоняются на этапе квалификации до устранения 30% ложных успехов и переработки зелёного окна очереди. "
        "На каждой подключаемой точке мастер проводит тест с отключением кабеля, сверкой списания и выходом бумажного чека. Потолок прямых затрат прежний — <=1 369,50 ₽/мес."
    )
    prop["actions"] = [
        {
            "id": "run_direct_pos_pilot",
            "owner": "Руководитель продукта",
            "due": "2026-11-30",
            "task": "Подключить до 20 квалифицированных магазинов direct_pos, замерить конверсию в оплату 2 490 ₽/мес и фактические прямые затраты",
            "trigger": "Даже 1 ложный успех на боевой кассе или прогноз прямых затрат >1 369,50 ₽/мес останавливает набор точек",
        },
        {
            "id": "diagnose_legacy_gateway",
            "owner": "Инженер кассовых интеграций",
            "due": "2026-11-24",
            "task": "Локализовать по логам причину 6/20 ложных успехов в legacy_gateway и оценить стоимость доработки статуса ожидания",
            "trigger": "Если доработка нарушает экономику >=45%, окончательно исключить legacy_gateway из продукта",
        },
    ]
    return prop


def main() -> None:
    ux_png = ROOT / "examples/retail-backup/initial/pos_ux_mockup.png"
    if Image is not None and not ux_png.is_file():
        generate_ux_mockup(ux_png)

    showcase = ROOT / "demo/retail-continuity"
    showcase.mkdir(parents=True, exist_ok=True)
    for old_file in showcase.iterdir():
        if old_file.is_file() and old_file.name != "README.md":
            old_file.unlink()

    with subprocess.Popen(["true"]):
        pass

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        case = Path(tmp) / "retail-continuity"
        kb_dir = Path(tmp) / "kb"
        inp = case / "input"
        inp.mkdir(parents=True)

        # Copy initial inputs + manager clarifications
        initial_dir = ROOT / "examples/retail-backup/initial"
        for item in sorted(initial_dir.iterdir()):
            shutil.copy2(item, inp / item.name)
        shutil.copy2(ROOT / "examples/retail-backup/manager.md", inp / "manager.md")

        run_ledger("init", str(case), "Стоит ли запускать платный резервный интернет для касс малых магазинов и что можно обещать владельцу?")
        route_intake = json.loads(run_ledger("route", str(case)))
        (showcase / "00-route-intake.json").write_text(json.dumps(route_intake, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        sources_spec = [
            ("brief", "document", "input/brief.md"),
            ("dictionary", "document", "input/data_dictionary.md"),
            ("market", "document", "input/market.md"),
            ("pilot", "synthetic", "input/pilot.csv"),
            ("manager", "document", "input/manager.md"),
            ("economics", "document", "input/economics.json"),
            ("ux_mockup", "ux", "input/pos_ux_mockup.png"),
            ("ux_notes", "ux", "input/ux_checkout_notes.md"),
            ("synth_probe", "synthetic", "input/synthetic_store_probe.md"),
        ]
        shas = {}
        for sid, kind, rel in sources_spec:
            rec = json.loads(run_ledger("source", str(case), sid, kind, rel))
            shas[sid] = rec["sha"]

        # Deterministic analytics before recount
        rates_before = run_ledger(
            "rates", str(inp / "pilot.csv"),
            "--group", "group", "--segment", "cohort",
            "--denominator", "drills",
            "--numerators", "network_recovered", "checkout_completed", "false_success",
            "--ci",
            "--guardrail", "checkout_completed>=0.90",
            "--guardrail", "false_success<=0.03",
        )
        (showcase / "01-rates-initial.json").write_text(rates_before, encoding="utf-8")

        econ_out = run_ledger("economics", str(inp / "economics.json"))
        (showcase / "02-economics.json").write_text(econ_out, encoding="utf-8")

        run_ledger(
            "telemetry", str(case), "--step", "intake_and_quant",
            "--model", "zero_llm+fast_scout", "--seconds", "38.4",
            "--input-tokens", "14200", "--cached-tokens", "9600", "--output-tokens", "1150",
            "--tool-calls", "5", "--human", "Комитет задал пороги >=90%, <=3% и маржу >=45%",
        )

        # Stage and approve Decision #1
        prop1 = build_proposal_v1(shas)
        (case / "proposal-v1.json").write_text(json.dumps(prop1, ensure_ascii=False, indent=2), encoding="utf-8")
        staged1 = json.loads(run_ledger("propose", str(case), "proposal-v1.json", "--strict-claims"))
        run_ledger(
            "accept", str(case), "managed_failover",
            "--expect", staged1["sha"],
            "--by", "Продуктовый комитет",
            "--reason", "Одобрена подготовка пилота только для direct_pos; допуск после финальной сверки чеков.",
        )
        run_ledger("checkpoint", str(case), "follow-up", "Дождаться сверки recount.csv и решить вопрос о допуске direct_pos.")
        (showcase / "03-decision-v1.md").write_text(run_ledger("view", str(case), "--decision", "1"), encoding="utf-8")
        run_ledger("dashboard", str(case), "-o", str(showcase / "03-dashboard-v1.html"))

        # Complete pre-launch promo action on Revision 1
        run_ledger("done", str(case), "fix_promo_copy", "--decision", "1", "--note", "Баннер с обещанием 100% снят, согласован скрипт пресейла")

        # Session 2: New recount arrives and replaces pilot.csv under the same source ID!
        shutil.copy2(ROOT / "examples/retail-backup/later/recount.csv", inp / "pilot.csv")
        rec_new = json.loads(run_ledger("source", str(case), "pilot", "synthetic", "input/pilot.csv"))
        shas["pilot"] = rec_new["sha"]

        status_stale = json.loads(run_ledger("status", str(case)))
        (showcase / "04-impact-after-recount.json").write_text(
            json.dumps(status_stale["approved_impact"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        # Render dashboard in the exact moment when Decision #1 has stale evidence highlighted!
        run_ledger("dashboard", str(case), "-o", str(showcase / "04-dashboard-stale.html"))

        rates_after = run_ledger(
            "rates", str(inp / "pilot.csv"),
            "--group", "group", "--segment", "cohort",
            "--denominator", "drills",
            "--numerators", "network_recovered", "checkout_completed", "false_success",
            "--ci",
            "--guardrail", "checkout_completed>=0.90",
            "--guardrail", "false_success<=0.03",
        )
        (showcase / "05-rates-recount.json").write_text(rates_after, encoding="utf-8")

        run_ledger(
            "telemetry", str(case), "--step", "followup_recount",
            "--model", "reasoning_strategist", "--seconds", "44.1",
            "--input-tokens", "18400", "--cached-tokens", "13200", "--output-tokens", "1480",
            "--tool-calls", "6", "--human", "Передана финальная сверка recount.csv и утверждено Решение №2",
        )

        prop2 = build_proposal_v2(shas)
        (case / "proposal-v2.json").write_text(json.dumps(prop2, ensure_ascii=False, indent=2), encoding="utf-8")
        staged2 = json.loads(run_ledger("propose", str(case), "proposal-v2.json", "--strict-claims"))
        run_ledger(
            "accept", str(case), "managed_failover",
            "--expect", staged2["sha"],
            "--by", "Продуктовый комитет",
            "--reason", "Открыт платный пилот до 20 магазинов строго для direct_pos; legacy_gateway заблокирован из-за 30% ложных успехов.",
        )
        run_ledger("checkpoint", str(case), "follow-up", "Контроль набора 20 точек direct_pos до 2026-11-30 и диагностика legacy_gateway до 2026-11-24.")
        (showcase / "05-decision-v2.md").write_text(run_ledger("view", str(case), "--decision", "2"), encoding="utf-8")
        run_ledger("dashboard", str(case), "-o", str(showcase / "05-dashboard-v2.html"))

        # Ingest into Knowledge Base and save sample search showing epistemic separation
        run_ledger("kb", "--kb-dir", str(kb_dir), "ingest", str(case))
        kb_sample = run_ledger("kb", "--kb-dir", str(kb_dir), "search", "шлюз касса чек")
        (showcase / "06-kb-search-sample.json").write_text(kb_sample, encoding="utf-8")

    print("Showcase built at:", showcase)


if __name__ == "__main__":
    main()
