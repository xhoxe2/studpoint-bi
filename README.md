# Stud-Point — AI Business Intelligence

Аналітичний AI-агент для платформи Stud-Point. Користувач вводить назву компанії —
система знаходить сайт, парсить публічні дані (DOU, robota.ua, Djinni, Wikipedia,
Google News, соцмережі), запускає AI-аналіз і формує редакційний звіт із:

- Скоринг 0–100 (5 категорій: сайт, SEO, соцмережі, HR-бренд, репутація)
- Об'єктивні метрики: PageSpeed Lighthouse, відкриті вакансії, sentiment новин
- Юр-факти (ЄДРПОУ, рік заснування, кількість співробітників)
- Знайдені проблеми + конкретні послуги Stud-Point + потенційний КПІ
- Бенчмарк проти прямих і непрямих конкурентів
- Compare mode — порівняння двох компаній side-by-side

## Стек

- **Backend:** Python 3.13 + FastAPI + Gemini 2.5 Flash + curl_cffi (для обходу Cloudflare)
- **Frontend:** статичний HTML + TailwindCSS CDN + Alpine.js + GSAP
- **AI:** Gemini 2.5 Flash з Google Search Grounding
- **Public APIs:** Wikipedia, Google News RSS, robota.ua GraphQL, PageSpeed Insights, thum.io

## Локальний запуск

```bash
cd backend
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env  # додати GEMINI_API_KEY і PAGESPEED_API_KEY
.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8765 --reload
```

Відкрити http://127.0.0.1:8765

## Деплой

Підтримується автодеплой на:
- **Railway** (рекомендовано) — `nixpacks.toml` + `Procfile` у корені
- **Render** — Procfile сумісний
- **Fly.io** — потрібен Dockerfile

Обов'язкові env-змінні: `GEMINI_API_KEY`, `PAGESPEED_API_KEY`.

## Структура

```
backend/         — FastAPI + scraper + analyzer
frontend/        — HTML/CSS/JS (статика, без React)
Procfile         — команда старту для PaaS
nixpacks.toml    — конфіг збірки Railway
runtime.txt      — Python версія
```

## Disclaimer

КПІ у звітах — індикативні, не є офертою. Дані — з відкритих джерел.
