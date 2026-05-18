"""Аналіз компанії через Gemini 2.5 Flash з Google Search Grounding.

Перевага Gemini тут — нативний доступ до Google Search прямо в API,
тому модель отримує свіжі дані про компанію (вакансії, новини, dou.ua)
без додаткових тулів і за ~безкоштовно (1500 пошуків/день у free-tier).

Обмеження SDK: коли увімкнено `google_search` tool, не можна одночасно
використовувати `response_mime_type='application/json'`. Тому:
1. Просимо JSON у промпті
2. Парсимо вручну з захистом (вирізаємо все від першої { до останньої })
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from google import genai
from google.genai import types
from google.genai import errors as genai_errors

from services import services_for_prompt, get_service_by_id, STUD_POINT_SERVICES
from scraper import CompanyData


MODEL = os.getenv("MODEL", "gemini-2.5-flash")
USE_GROUNDING = os.getenv("USE_GROUNDING", "true").lower() == "true"


SYSTEM_PROMPT_TEMPLATE = """Ти — старший бізнес-аналітик і консультант з employer brand для української платформи STUD-POINT (працевлаштування для студентів і молоді).

Завдання: проаналізувати компанію за наданими даними + результатами Google Search (якщо є), виявити РЕАЛЬНІ бізнес-проблеми (маркетинг, цифрова присутність, HR-бренд, репутація), і запропонувати послуги STUD-POINT під ці проблеми.

# Каталог послуг STUD-POINT

{services}

# Правила

1. **Користуйся Google Search** для перевірки фактів: вакансії компанії, рейтинг на dou.ua, останні новини, наявність кар'єрної сторінки. Це критично.
2. **Тільки факти**. Якщо точна метрика невідома навіть після пошуку — пиши діапазон або "потребує верифікації". Не вигадуй цифри.
3. **Жодних обіцянок**. У `potential_kpi` завжди формулюй як можливість ("може дозволити", "потенційно", "за досвідом подібних кейсів"), а не гарантію.
4. **Конкретика**. Замість "слабкий HR-бренд" — "немає окремої кар'єрної сторінки; рейтинг на dou.ua 3.2/5".
4a. **ОБОВ'ЯЗКОВО** скористайся Google Search щоб знайти приблизну кількість відкритих вакансій на: jobs.dou.ua/companies/{{slug}}/, work.ua (запит "{{назва}} вакансії"), robota.ua. Заповни `vacancies_estimate`.
5. **Скоринг 0–100** для 5 категорій:
   - `website` — якість/свіжість сайту, мобільна версія
   - `seo_traffic` — індикатори SEO, видимість у пошуку, контент
   - `social_media` — активність і якість соцмереж
   - `hr_brand` — кар'єрна сторінка, EVP, dou.ua, відгуки
   - `reputation` — медіа, новини, тональність
6. **Загальний скор** — середньозважене (HR-бренд і сайт вага 1.2, інші 1.0), округлити.
7. **Конкуренти — ОБОВ'ЯЗКОВО 2 групи:**
   - `direct_competitors` (2–3): пряма конкуренція — той самий продукт/сегмент/цільова аудиторія.
     Наприклад: Sense Bank → Monobank, ПриватБанк, Райффайзен.
   - `indirect_competitors` (2–3): альтернатива з іншої категорії що бореться за той самий бюджет/час клієнта.
     Наприклад: Sense Bank → Wise (фінтех міжнарод.), Revolut, криптогаманці.
   Для кожного — оціночний бал з обґрунтуванням у `note`.
8. **Послуги**: для кожної суттєвої проблеми вкажи 1 послугу з каталогу (її `id`). Пропусти проблему, якщо жодна не підходить.
9. **Мова відповіді — українська**.
10. **3–5 проблем**.
11. **legal_info** — обов'язково спробуй знайти через Search:
    - офіційну юр-назву (АТ/ТОВ/ПрАТ)
    - ЄДРПОУ (8-значний код) — з opendatabot, youcontrol, clarity-project, wikipedia
    - рік заснування
    - приблизну кількість співробітників (з Wikipedia, річних звітів, ЗМІ)
    - розмір (small <50, medium 50-500, large 500-5000, enterprise 5000+)
    Якщо щось не знайшов — постав null, не вигадуй.
12. **company_size** використовуй для калібрування скорингу та `hiring_insight`.

# Об'єктивні метрики (передаються тобі вище)

Якщо у вхідних даних є PageSpeed-скори — ВИКОРИСТОВУЙ їх для оцінки `website`.
Якщо є кількість відкритих вакансій — це ВАЖЛИВИЙ сигнал HR-потреби (10+ вакансій = active hiring → потужний привід для партнерства Stud-Point).
Якщо є соцмережі — оцінюй соцприсутність на основі наявних URL.
Якщо є новинні заголовки — класифікуй їх тональність (positive/neutral/negative) на основі тексту заголовка.

# Формат відповіді

ПОВЕРНИ ЛИШЕ JSON, без markdown-обгортки, без пояснень до чи після:

{{
  "company": {{
    "name": "...",
    "industry": "...",
    "country": "...",
    "summary": "1-2 речення"
  }},
  "overall_score": 72,
  "scores": {{
    "website": 65, "seo_traffic": 70, "social_media": 80, "hr_brand": 45, "reputation": 78
  }},
  "score_reasoning": {{
    "website": "...", "seo_traffic": "...", "social_media": "...", "hr_brand": "...", "reputation": "..."
  }},
  "problems": [
    {{
      "category": "HR-бренд",
      "title": "...",
      "description": "...",
      "evidence": "конкретний факт/цифра (бажано підтверджений пошуком чи PageSpeed)",
      "severity": "high",
      "service_id": "brand_page",
      "potential_kpi": "Може дозволити... (індикативно, без гарантій)"
    }}
  ],
  "direct_competitors": [
    {{ "name": "Monobank", "estimated_score": 90, "note": "Сильніший digital, активний бренд у молоді" }}
  ],
  "indirect_competitors": [
    {{ "name": "Wise", "estimated_score": 85, "note": "Бере той самий бюджет на міжнародні платежі" }}
  ],
  "legal_info": {{
    "registered_name": "АТ «Сенс Банк»",
    "edrpou": "23494714",
    "year_founded": 2001,
    "approx_employees": "5000+",
    "employees_source": "офіційний звіт / Wikipedia / новинні джерела",
    "company_size": "large",
    "office_locations": ["Київ", "регіональні відділення"]
  }},
  "news_sentiment": [
    {{ "index": 1, "sentiment": "negative", "reason": "коротко чому" }},
    {{ "index": 2, "sentiment": "neutral", "reason": "..." }}
  ],
  "vacancies_estimate": {{
    "total_approx": 45,
    "by_source": {{ "work.ua": 30, "robota.ua": 12, "jobs.dou.ua": 3 }},
    "confidence": "medium",
    "note": "Знайдено через Google Search, потребує верифікації"
  }},
  "hiring_insight": "Короткий висновок про HR-потребу на основі кількості вакансій (1-2 речення)",
  "executive_summary": "2-3 речення для керівника",
  "disclaimer": "..."
}}

`index` у news_sentiment — порядковий номер заголовка зі списку нижче (1, 2, 3…).
severity ∈ {{"high","medium","low"}}.
sentiment ∈ {{"positive","neutral","negative"}}.
service_id ∈ [{service_ids}].
"""


def build_system_prompt() -> str:
    ids = ", ".join(f'"{s["id"]}"' for s in STUD_POINT_SERVICES)
    return SYSTEM_PROMPT_TEMPLATE.format(services=services_for_prompt(), service_ids=ids)


def _user_message(company: CompanyData) -> str:
    return (
        "Проаналізуй цю компанію. Якщо відкритих даних мало — обовʼязково перевір "
        "через Google Search (dou.ua, останні новини, кар'єрна сторінка, вакансії). "
        "Поверни ЛИШЕ JSON за схемою.\n\n"
        + company.to_prompt_block()
    )


def _extract_json(text: str) -> dict:
    """Витягує JSON навіть якщо є markdown-обгортка або зайвий текст."""
    if not text:
        raise ValueError("Порожня відповідь моделі")
    t = text.strip()
    # зняти ```json ... ``` якщо є
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```\s*$", t, re.DOTALL)
    if fence:
        t = fence.group(1).strip()
    # вирізати від першої { до останньої }
    start = t.find("{")
    end = t.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"Не знайдено JSON у відповіді: {text[:300]}")
    return json.loads(t[start:end + 1])


def _enrich_with_service_details(report: dict) -> dict:
    for p in report.get("problems", []):
        sid = p.get("service_id")
        if sid:
            svc = get_service_by_id(sid)
            if svc:
                p["service"] = {
                    "id": svc["id"],
                    "name": svc["name"],
                    "url": svc["url"],
                    "category": svc["category"],
                    "summary": svc["summary"],
                    "price_hint": svc["price_hint"],
                }
    return report


def analyze(company: CompanyData) -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY не встановлено")

    client = genai.Client(api_key=api_key)

    tools: list[types.Tool] = []
    if USE_GROUNDING:
        tools.append(types.Tool(google_search=types.GoogleSearch()))

    config = types.GenerateContentConfig(
        system_instruction=build_system_prompt(),
        temperature=0.4,
        tools=tools if tools else None,
        # ⚠️ response_mime_type не сумісний з google_search — JSON парсимо вручну
    )

    # Gemini вільний рівень буває перевантажений (503) — до 5 спроб з агресивним бекоффом.
    last_err: Exception | None = None
    resp = None
    text = ""
    MAX_ATTEMPTS = 5
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = client.models.generate_content(
                model=MODEL,
                contents=_user_message(company),
                config=config,
            )
            text = (resp.text or "").strip()
            if text:
                break
            last_err = RuntimeError("empty response")
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(2 + attempt * 2)
                continue
        except genai_errors.APIError as e:
            last_err = e
            code = getattr(e, "code", None)
            if code in (429, 503) and attempt < MAX_ATTEMPTS - 1:
                # 503/429: 3с, 6с, 10с, 15с
                time.sleep(3 + attempt * 3)
                continue
            raise
    if resp is None or not text:
        raise RuntimeError(f"Gemini не повернув контент після {MAX_ATTEMPTS} спроб: {last_err}")

    try:
        data = _extract_json(text)
    except (ValueError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Gemini повернув не-JSON: {e}\n---\n{text[:1500]}")

    # Додаємо метадані з grounding (які URL модель реально читала)
    grounding_urls = []
    try:
        for cand in (resp.candidates or []):
            gm = getattr(cand, "grounding_metadata", None)
            if not gm:
                continue
            chunks = getattr(gm, "grounding_chunks", None) or []
            for ch in chunks:
                web = getattr(ch, "web", None)
                if web and getattr(web, "uri", None):
                    grounding_urls.append({
                        "title": getattr(web, "title", ""),
                        "uri": web.uri,
                    })
    except Exception:
        pass

    report = _enrich_with_service_details(data)
    if grounding_urls:
        report.setdefault("_meta", {})["grounding_sources"] = grounding_urls[:12]
    return report
