"""Демо-режим: повертає реалістичний мок-звіт без виклику Claude.
Активується автоматично якщо немає ANTHROPIC_API_KEY або DEMO_MODE=true.
"""
from services import get_service_by_id


def _svc(sid: str) -> dict:
    s = get_service_by_id(sid)
    if not s:
        return None
    return {
        "id": s["id"], "name": s["name"], "url": s["url"],
        "category": s["category"], "summary": s["summary"],
        "price_hint": s["price_hint"],
    }


def build_demo_report(name: str) -> dict:
    return {
        "company": {
            "name": name,
            "industry": "FMCG / Демо",
            "country": "Україна",
            "summary": f"{name} — приклад звіту у демо-режимі. Підключіть ANTHROPIC_API_KEY у backend/.env щоб отримати справжній AI-аналіз.",
        },
        "overall_score": 62,
        "scores": {
            "website": 68,
            "seo_traffic": 55,
            "social_media": 74,
            "hr_brand": 42,
            "reputation": 71,
        },
        "score_reasoning": {
            "website": "Сайт є, але дизайн застарілий; мобільна версія працює; немає блогу.",
            "seo_traffic": "Видимість у пошуку середня, мало контенту під довгий хвіст ключових слів.",
            "social_media": "Активна Instagram-сторінка, але engagement rate нижче бенчмарку галузі.",
            "hr_brand": "Немає окремої кар'єрної сторінки; рейтинг на dou.ua нижче 3.5; відгуки старіші 6 міс.",
            "reputation": "Згадки у медіа переважно нейтральні, негативу мало.",
        },
        "problems": [
            {
                "category": "HR-бренд",
                "title": "Відсутня окрема кар'єрна сторінка для молоді",
                "description": "На офіційному сайті немає виділеного розділу 'Кар'єра' для студентів і випускників. Інформація про вакансії розкидана або відсутня.",
                "evidence": "Не знайдено сторінки /careers або /кар'єра на сайті. Останній пост у блозі про команду — більше 7 місяців тому.",
                "severity": "high",
                "service_id": "brand_page",
                "service": _svc("brand_page"),
                "potential_kpi": "Може дозволити вийти у топ-5 Google за запитами 'робота в [компанія]' і збільшити пул заявок від мотивованих кандидатів-студентів (індикативно, без гарантій).",
            },
            {
                "category": "Контент / SEO",
                "title": "Слабкий контент-маркетинг про компанію як роботодавця",
                "description": "Немає регулярних публікацій про команду, культуру, історії співробітників, що обмежує органічний пошуковий трафік за HR-запитами.",
                "evidence": "За останні 6 місяців знайдено менше 3 згадок у профільних HR-медіа (потребує верифікації).",
                "severity": "medium",
                "service_id": "minimal_partner",
                "service": _svc("minimal_partner"),
                "potential_kpi": "Регулярний контент-потік (2–3 матеріали/міс) може забезпечити охоплення до 10 000 молодих спеціалістів через платформу Stud-Point.",
            },
            {
                "category": "Залучення молоді",
                "title": "Немає системних кампусних активностей і партнерств з університетами",
                "description": "За даними відкритих джерел відсутні згадки про лекції, стажування або співпрацю з ВНЗ — це знижує впізнаваність серед майбутніх випускників.",
                "evidence": "У публічних анонсах університетів за останній рік згадок не знайдено.",
                "severity": "medium",
                "service_id": "partner",
                "service": _svc("partner"),
                "potential_kpi": "Партнерства з 1–2 університетами на пів року дозволять побудувати прямий канал залучення випускників.",
            },
            {
                "category": "Performance / Рекрутинг",
                "title": "Потенційно високий обсяг вакансій без таргетованої digital-промо",
                "description": "Якщо у компанії є потокові позиції (інтерни, junior), відсутність налаштованої таргетованої реклами на молодь обмежує швидкість найму.",
                "evidence": "Інформацію про обсяг вакансій потребує верифікації після підключення Claude.",
                "severity": "low",
                "service_id": "premium_partner",
                "service": _svc("premium_partner"),
                "potential_kpi": "До 8 активних вакансій/міс з таргетованою рекламою і логотипом на головній сторінці платформи.",
            },
        ],
        "competitors": [
            {"name": "Конкурент А", "estimated_score": 78, "note": "Сильніший HR-бренд, активна кар'єрна сторінка з EVP."},
            {"name": "Конкурент Б", "estimated_score": 65, "note": "Подібний рівень, але кращий SEO за брендовими запитами."},
            {"name": "Конкурент В", "estimated_score": 52, "note": "Слабша цифрова присутність — можливість для випередження."},
        ],
        "executive_summary": (
            "У демо-звіті: HR-бренд компанії — найслабше місце (42/100), при тому що digital-присутність "
            "загалом середня. Пріоритет — кар'єрна вітрина + контент про команду; це може закрити "
            "розрив із конкурентом A за 3–6 місяців. КПІ індикативні, не є гарантією."
        ),
        "disclaimer": (
            "Це ДЕМО-звіт із прикладними даними. Для реального аналізу — додайте ANTHROPIC_API_KEY у backend/.env. "
            "У бойовому режимі аналіз базується на відкритих джерелах і знаннях моделі, КПІ є індикативними."
        ),
        "_meta": {
            "demo": True,
            "website_found": "https://example.com",
            "careers_url": "https://example.com/careers",
            "about_url": "https://example.com/about",
            "dou_url": "https://dou.ua/companies/example/",
            "news_items": [
                {"title": "Прикладова згадка в медіа", "source": "Демо-джерело", "date": "2026-05-15"},
                {"title": "Інша демо-згадка", "source": "Демо-медіа", "date": "2026-05-10"},
            ],
            "grounding_sources": [
                {"title": "DOU.ua — профіль компанії", "uri": "https://dou.ua/companies/example/"},
                {"title": "Кар'єрна сторінка", "uri": "https://example.com/careers"},
            ],
            "candidates_tried": ["https://example.com", "https://example.ua"],
            "errors": [],
        },
    }
