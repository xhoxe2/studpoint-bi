"""FastAPI бекенд: приймає назву компанії → повертає аналіз."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()

from analyzer import analyze  # noqa: E402
from scraper import collect  # noqa: E402
from demo import build_demo_report  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"

app = FastAPI(title="Stud-Point Business Analyzer", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    company: str = Field(min_length=2, max_length=120)
    website: str | None = None


@app.post("/api/analyze")
async def analyze_endpoint(req: AnalyzeRequest):
    demo_mode = (
        os.getenv("DEMO_MODE", "").lower() == "true"
        or not os.getenv("GEMINI_API_KEY")
    )
    if demo_mode:
        # імітуємо невелику затримку, щоб користувач побачив лоадер
        await asyncio.sleep(2.5)
        return build_demo_report(req.company)
    try:
        data = await collect(req.company, req.website)
        report = await asyncio.to_thread(analyze, data)
        # Зливаємо meta зі скрапера й з аналізатора (grounding_sources)
        existing_meta = report.get("_meta", {}) or {}
        # Зливаємо sentiment з відповіді моделі в новинні елементи
        news_items = [dict(n) for n in data.news_items[:10]]
        sentiment_map = {}
        for s in (report.get("news_sentiment") or []):
            idx = s.get("index")
            if isinstance(idx, int) and 1 <= idx <= len(news_items):
                sentiment_map[idx - 1] = {
                    "sentiment": s.get("sentiment", "neutral"),
                    "reason": s.get("reason", ""),
                }
        for i, n in enumerate(news_items):
            if i in sentiment_map:
                n["sentiment"] = sentiment_map[i]["sentiment"]
                n["sentiment_reason"] = sentiment_map[i]["reason"]

        # Зведена статистика тональності
        neg = sum(1 for n in news_items if n.get("sentiment") == "negative")
        pos = sum(1 for n in news_items if n.get("sentiment") == "positive")
        neu = sum(1 for n in news_items if n.get("sentiment") == "neutral")

        # Зворотна сумісність: якщо модель повернула старе поле competitors, додаємо як direct
        if report.get("competitors") and not report.get("direct_competitors"):
            report["direct_competitors"] = report.get("competitors")
        # Звідки взято сайт — для UI confirmation banner
        website_source = "manual" if req.website else None
        if not website_source and data.candidates_tried:
            first = data.candidates_tried[0] if data.candidates_tried else ""
            if first.startswith("[wiki]"): website_source = "wikipedia"
            elif first.startswith("[gemini]"): website_source = "gemini_search"
            else: website_source = "heuristic"
        existing_meta.update({
            "website_found": data.website,
            "website_source": website_source,
            "careers_url": data.careers_url,
            "about_url": data.about_url,
            "dou_url": data.dou_url,
            "news_items": news_items,
            "news_sentiment_stats": {"positive": pos, "neutral": neu, "negative": neg, "total": len(news_items)},
            "pagespeed": data.pagespeed,
            "vacancies_scraped": data.vacancies,
            "vacancies_estimate": report.get("vacancies_estimate"),
            "hiring_insight": report.get("hiring_insight"),
            "socials": data.socials,
            "socials_meta": data.socials_meta,
            "candidates_tried": data.candidates_tried,
            "errors": data.errors,
        })
        report["_meta"] = existing_meta
        return report
    except Exception as e:
        raise HTTPException(500, f"Помилка аналізу: {e}")


@app.get("/api/health")
async def health():
    return {"ok": True, "model": os.getenv("MODEL", "gemini-2.5-flash")}


# Віддаємо фронт як статику. Mount у кінці — щоб /api/* мали пріоритет.
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
