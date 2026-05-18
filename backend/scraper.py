"""Збір даних про компанію з відкритих джерел.

Стратегія дешева й проста:
1. Спробувати знайти офіційний сайт через DuckDuckGo HTML.
2. Завантажити головну сторінку (та /careers, /about, /contacts якщо знайдемо).
3. Спробувати знайти сторінку компанії на dou.ua (HR-бренд, відгуки).
4. Зібрати останні новини через Google News RSS.

Все це передаємо Claude — він робить висновки.
"""
from __future__ import annotations

import asyncio
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import quote_plus, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8"}
TIMEOUT = httpx.Timeout(15.0, connect=8.0)


@dataclass
class CompanyData:
    name: str
    website: str | None = None
    homepage_html: str = ""           # сирий HTML головної (для соц-парсингу)
    homepage_text: str = ""
    homepage_html_meta: dict = field(default_factory=dict)
    careers_url: str | None = None
    careers_text: str = ""
    about_url: str | None = None
    about_text: str = ""
    dou_url: str | None = None
    dou_text: str = ""
    news_items: list[dict] = field(default_factory=list)
    candidates_tried: list[str] = field(default_factory=list)
    # нові поля
    pagespeed: dict | None = None     # {'scores': {...}, 'cwv': {...}, 'url': ...}
    vacancies: dict | None = None     # {'total': N, 'by_source': {...}, 'sources': {...}}
    socials: dict = field(default_factory=dict)   # {'instagram': 'https://...', ...}
    socials_meta: dict = field(default_factory=dict)  # {'from_footer': [...], 'guessed': [...]}
    errors: list[str] = field(default_factory=list)

    def to_prompt_block(self) -> str:
        parts = [f"# Компанія: {self.name}"]
        if self.website:
            parts.append(f"Сайт: {self.website}")
        if self.homepage_html_meta:
            meta = self.homepage_html_meta
            parts.append("## Мета-теги головної:")
            for k in ("title", "description", "og:title", "og:description"):
                if meta.get(k):
                    parts.append(f"- {k}: {meta[k]}")
        # PageSpeed — ОБ'ЄКТИВНІ ДАНІ, передаємо першими
        if self.pagespeed and self.pagespeed.get("scores"):
            ps = self.pagespeed
            parts.append("## PageSpeed Insights (Google Lighthouse, мобільна версія):")
            for k, v in ps["scores"].items():
                parts.append(f"- {k}: {v}/100")
            if ps.get("cwv"):
                parts.append("Core Web Vitals: " + "; ".join(f"{k}={v}" for k, v in ps["cwv"].items()))
        else:
            parts.append("## PageSpeed Insights: не вдалося отримати (сайт за CDN / Cloudflare?)")
        # Вакансії — ОБ'ЄКТИВНИЙ СИГНАЛ для HR-партнерства
        if self.vacancies and self.vacancies.get("total") is not None:
            v = self.vacancies
            parts.append(f"## Відкриті вакансії (сума з джерел): {v.get('total', 0)}")
            for src, cnt in (v.get("by_source") or {}).items():
                parts.append(f"- {src}: {cnt}")
        else:
            parts.append("## Вакансії: не знайдено публічних оголошень (або компанія не на UA джоб-бордах)")
        # Соцмережі
        if self.socials:
            parts.append("## Соцмережі (URL знайдено / підтверджено):")
            for net, url in self.socials.items():
                parts.append(f"- {net}: {url}")
            if self.socials_meta.get("from_footer"):
                parts.append(f"  (з футера сайту: {', '.join(self.socials_meta['from_footer'])})")
            if self.socials_meta.get("guessed"):
                parts.append(f"  (підтверджено гевристикою: {', '.join(self.socials_meta['guessed'])})")
        else:
            parts.append("## Соцмережі: не виявлено публічних акаунтів")
        if self.homepage_text:
            parts.append("## Текст головної сторінки (фрагмент):")
            parts.append(self.homepage_text[:3500])
        if self.about_text:
            parts.append("## Сторінка 'Про нас':")
            parts.append(self.about_text[:2000])
        if self.careers_text:
            parts.append("## Кар'єрна сторінка:")
            parts.append(self.careers_text[:2500])
        else:
            parts.append("## Кар'єрна сторінка: НЕ ЗНАЙДЕНА на сайті")
        if self.dou_url:
            parts.append(f"## Сторінка на dou.ua: {self.dou_url}")
            if self.dou_text:
                parts.append(self.dou_text[:2500])
        else:
            parts.append("## DOU: профіль компанії не виявлено")
        if self.news_items:
            parts.append("## Останні згадки у новинах (заголовки, для sentiment-аналізу):")
            for i, n in enumerate(self.news_items[:10], 1):
                parts.append(f"{i}. {n.get('title','')} ({n.get('source','')}, {n.get('date','')})")
        if self.errors:
            parts.append("## Технічні зауваження при зборі даних:")
            for e in self.errors:
                parts.append(f"- {e}")
        return "\n".join(parts)


def _clean_text(html: str, limit: int = 6000) -> tuple[str, dict]:
    soup = BeautifulSoup(html, "html.parser")
    meta = {}
    if soup.title and soup.title.string:
        meta["title"] = soup.title.string.strip()[:200]
    for tag in soup.find_all("meta"):
        name = (tag.get("name") or tag.get("property") or "").lower()
        if name in {"description", "og:title", "og:description", "keywords"}:
            content = (tag.get("content") or "").strip()
            if content:
                meta[name] = content[:300]
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return text[:limit], meta


async def _fetch(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        r = await client.get(url, headers=HEADERS, follow_redirects=True, timeout=TIMEOUT)
        if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
            return r.text
    except Exception:
        return None
    return None


UA_TO_LAT = str.maketrans({
    "а":"a","б":"b","в":"v","г":"h","ґ":"g","д":"d","е":"e","є":"ie",
    "ж":"zh","з":"z","и":"y","і":"i","ї":"i","й":"i","к":"k","л":"l",
    "м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t","у":"u",
    "ф":"f","х":"kh","ц":"ts","ч":"ch","ш":"sh","щ":"sch","ь":"",
    "ю":"iu","я":"ia","'":"","’":"",
    "А":"A","Б":"B","В":"V","Г":"H","Ґ":"G","Д":"D","Е":"E","Є":"Ie",
    "Ж":"Zh","З":"Z","И":"Y","І":"I","Ї":"I","Й":"I","К":"K","Л":"L",
    "М":"M","Н":"N","О":"O","П":"P","Р":"R","С":"S","Т":"T","У":"U",
    "Ф":"F","Х":"Kh","Ц":"Ts","Ч":"Ch","Ш":"Sh","Щ":"Sch","Ь":"",
    "Ю":"Iu","Я":"Ia",
})


def _slug(name: str) -> str:
    s = name.translate(UA_TO_LAT)
    s = re.sub(r"[^a-zA-Z0-9]+", "", s).lower()
    return s


def _domain_candidates(name: str) -> list[str]:
    """Генеруємо вірогідні домени для назви компанії."""
    raw = name.strip()
    parts_ua = re.split(r"\s+", raw)
    parts_lat = [_slug(p) for p in parts_ua if _slug(p)]
    combined = "".join(parts_lat)         # senseban
    hyphenated = "-".join(parts_lat)       # sense-bank
    first = parts_lat[0] if parts_lat else combined

    tlds = [".com.ua", ".ua", ".com", ".co"]
    bases = list(dict.fromkeys([combined, hyphenated, first]))
    bases = [b for b in bases if b and len(b) > 1]

    out: list[str] = []
    for b in bases:
        for t in tlds:
            out.append(f"https://{b}{t}")
            out.append(f"https://www.{b}{t}")
    # дедуп зі збереженням порядку
    seen, dedup = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            dedup.append(u)
    return dedup[:16]


def _host_matches_name(host: str, name: str) -> bool:
    """Чи хост містить назву бренду (slug або hyphenated-форму)."""
    host = host.lower()
    slug = _slug(name)
    parts = [_slug(p) for p in re.split(r"\s+", name) if _slug(p)]
    hyphenated = "-".join(parts)
    # викидаємо www. та беремо лише першу частину домену
    host_clean = host.replace("www.", "")
    naked = host_clean.split(".")[0]
    # точне співпадіння першого лейблу домену зі slug або hyphenated
    if slug and naked == slug:
        return True
    if hyphenated and naked == hyphenated:
        return True
    # або naked починається/містить slug (для коротких назв) — обережно
    if slug and len(slug) >= 5 and slug in naked:
        return True
    return False


async def _validate_candidate(client: httpx.AsyncClient, url: str, company_name: str) -> str | None:
    """Запит до кандидата з редиректами; повертає КАНОНІЧНУ URL якщо домен підтверджено."""
    try:
        r = await client.get(url, headers=HEADERS, follow_redirects=True, timeout=TIMEOUT)
        final = str(r.url)
        final_host = r.url.host or ""
        # 1) Якщо фінальний хост містить slug бренду — приймаємо незалежно від статусу
        #    (це покриває кейс sensebank.com.ua → sensebank.ua навіть під Incapsula)
        if _host_matches_name(final_host, company_name):
            return f"{r.url.scheme}://{final_host}"
        # 2) Інакше — 200 + перевірка вмісту (заголовок повинен містити токени бренду)
        if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
            head_html = r.text[:8000].lower()
            soup = BeautifulSoup(head_html, "html.parser")
            title = (soup.title.string if soup.title and soup.title.string else "").lower()
            slug = _slug(company_name)
            parts = [p.lower() for p in re.split(r"\s+", company_name) if len(p) > 2]
            parts_slug = [_slug(p) for p in parts]
            # потрібен повний slug у title АБО усі частини в title
            if slug and slug in title:
                return f"{r.url.scheme}://{final_host}"
            if parts and all(p in title or ps in title for p, ps in zip(parts, parts_slug)):
                return f"{r.url.scheme}://{final_host}"
    except Exception:
        return None
    return None


async def _find_website_by_guessing(client: httpx.AsyncClient, name: str, tried: list[str]) -> str | None:
    candidates = _domain_candidates(name)
    for url in candidates:
        tried.append(url)
        canonical = await _validate_candidate(client, url, name)
        if canonical:
            return canonical
    return None


async def _find_website_via_duckduckgo(client: httpx.AsyncClient, name: str) -> str | None:
    """Резервний пошук через DuckDuckGo HTML."""
    for query in (f'"{name}" офіційний сайт', f"{name} site:.ua", f"{name} компанія"):
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        try:
            r = await client.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.select("a.result__a"):
                href = a.get("href", "")
                m = re.search(r"uddg=([^&]+)", href)
                if m:
                    from urllib.parse import unquote
                    real = unquote(m.group(1))
                else:
                    real = href
                parsed = urlparse(real)
                host = parsed.netloc.lower()
                if not host:
                    continue
                skip = ("facebook.com","instagram.com","linkedin.com","wikipedia.org",
                        "youtube.com","twitter.com","x.com","dou.ua","work.ua",
                        "rabota.ua","robota.ua","djinni.co","hh.ua","google.",
                        "duckduckgo.com","t.me","tiktok.com")
                if any(s in host for s in skip):
                    continue
                return f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            continue
    return None


# ============================================================
# Wikipedia infobox lookup — найточніший free-метод для відомих компаній
# ============================================================
# Поля у infobox що містять офіційний сайт (порядок = пріоритет).
# `url` НЕ включаємо — він масово зустрічається в {{cite web|url=…}} у ref-тегах.
WIKI_SITE_FIELDS = ("сайт", "вебсайт", "веб-сайт", "веб‐сайт",
                    "website", "homepage")


def _clean_wiki_url(raw: str) -> str:
    raw = raw.strip().rstrip(".,;) ]").rstrip("/")
    if not raw:
        return raw
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    return raw


def _extract_infobox(wikitext: str) -> str | None:
    """Витягує блок {{Інфобокс ...}} / {{Infobox ...}} зі збалансованими дужками."""
    if not wikitext:
        return None
    # шукаємо стартову позицію
    m = re.search(r"\{\{\s*(?:Інфобокс|Infobox|Картка)\b", wikitext, re.IGNORECASE)
    if not m:
        return None
    start = m.start()
    # ходимо балансом {{ ... }}
    depth = 0
    i = start
    while i < len(wikitext) - 1:
        ch2 = wikitext[i:i+2]
        if ch2 == "{{":
            depth += 1
            i += 2
        elif ch2 == "}}":
            depth -= 1
            i += 2
            if depth == 0:
                return wikitext[start:i]
        else:
            i += 1
    return wikitext[start:start + 8000]  # фолбек


def _extract_site_from_infobox(infobox: str) -> str | None:
    """Шукає URL у визначених полях infobox. Поля шукаються СУВОРО — рядок
    починається з `|`, потім пробіли, потім ім'я поля, потім `=`. Це відсікає
    `url=` всередині шаблонів типу `{{cite web|url=...}}`."""
    if not infobox:
        return None
    for field in WIKI_SITE_FIELDS:
        # `(?m)^` — поле має починатись на новому рядку (це позиція у багаторядковому infobox)
        pat = r"(?m)^\s*\|\s*" + re.escape(field) + r"\s*="
        fm = re.search(pat, infobox, re.IGNORECASE)
        if not fm:
            continue
        end = fm.end()
        rest = infobox[end:end + 600]
        # значення поля закінчується або новим `\n|` або `}}`
        cut = re.search(r"\n\s*\||\}\}\s*$", rest)
        chunk = rest[:cut.start()] if cut else rest

        # 1) {{Official website|domain.com}} / {{URL|domain.com}} / {{Офіційний сайт|...}}
        m = re.search(r"\{\{\s*(?:URL|Official\s*website|Official\s*URL|Офіційний\s*сайт|Сайт)\s*\|\s*([^\|\}\s]+)",
                      chunk, re.IGNORECASE)
        if m:
            return _clean_wiki_url(m.group(1))
        # 2) {{cite web|url=...|title=...}} — рідко, але буває
        m = re.search(r"\{\{\s*cite[^}]*?\|\s*url\s*=\s*(https?://[^\s\|\}\n]+)", chunk, re.IGNORECASE)
        if m:
            return _clean_wiki_url(m.group(1))
        # 3) звичайний URL у тексті значення
        m = re.search(r"https?://[^\s\]\|\}\n<>]+", chunk)
        if m:
            return _clean_wiki_url(m.group(0))
        # 4) голий домен
        m = re.search(r"\b((?:www\.)?[a-z0-9][a-z0-9\-]*\.[a-z][a-z\.]{1,8}(?:/[^\s\]\|\}\n<>]*)?)\b",
                      chunk, re.IGNORECASE)
        if m:
            return _clean_wiki_url(m.group(1))
    return None


WIKI_HEADERS = {
    "User-Agent": "StudPointBot/1.0 (https://stud-point.com; analytics)",
    "Accept": "application/json",
    "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8",
}


async def fetch_wikipedia_website(client: httpx.AsyncClient, name: str) -> str | None:
    """Знаходить official_website у Wikipedia infobox (укр → англ)."""
    for lang in ("uk", "en"):
        try:
            api = f"https://{lang}.wikipedia.org/w/api.php"
            # 1. шукаємо сторінку за назвою
            search = {
                "action": "query", "list": "search",
                "srsearch": name, "format": "json", "srlimit": 1, "utf8": 1,
            }
            r = await client.get(api, params=search, headers=WIKI_HEADERS, timeout=TIMEOUT, follow_redirects=True)
            if r.status_code != 200:
                continue
            hits = r.json().get("query", {}).get("search", []) or []
            if not hits:
                continue
            page_title = hits[0].get("title")
            if not page_title:
                continue
            # 2. дістаємо вікітекст і шукаємо паттерни
            parse = {
                "action": "parse", "page": page_title,
                "prop": "wikitext", "format": "json", "utf8": 1,
            }
            r = await client.get(api, params=parse, headers=WIKI_HEADERS, timeout=TIMEOUT, follow_redirects=True)
            if r.status_code != 200:
                continue
            wikitext = (r.json().get("parse", {}).get("wikitext", {}) or {}).get("*", "")
            if not wikitext:
                continue
            infobox = _extract_infobox(wikitext)
            if not infobox:
                continue
            url = _extract_site_from_infobox(infobox)
            if not url:
                continue
            # відсікаємо явні соцмережі та архіви
            skip = ("facebook.com", "instagram.com", "linkedin.com",
                    "twitter.com", "x.com", "youtube.com",
                    "web.archive.org", "opendatabot.ua", "youcontrol.com.ua",
                    "wikipedia.org")
            if any(s in url for s in skip):
                continue
            return url
        except Exception:
            continue
    return None


# ============================================================
# Pre-flight Gemini: швидкий запит "офіційний сайт {name}?"
# Викликається паралельно — додає не більше 5с до загального часу.
# ============================================================
async def gemini_find_website(name: str) -> str | None:
    """Запитує у Gemini Search Grounding офіційний URL. Sync SDK через to_thread."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        from google import genai
        from google.genai import types as gt

        def _call() -> str | None:
            client = genai.Client(api_key=api_key)
            resp = client.models.generate_content(
                model=os.getenv("MODEL", "gemini-2.5-flash"),
                contents=(
                    f"What is the OFFICIAL website URL of the company '{name}' "
                    f"(operating primarily in Ukraine)? "
                    f"Reply with ONLY the URL (e.g. https://example.com), "
                    f"or 'unknown' if you can't verify. No explanations."
                ),
                config=gt.GenerateContentConfig(
                    tools=[gt.Tool(google_search=gt.GoogleSearch())],
                    temperature=0.0,
                    max_output_tokens=200,
                ),
            )
            text = (resp.text or "").strip()
            if not text or "unknown" in text.lower():
                return None
            m = re.search(r"https?://[^\s\)\]<>\"',]+", text)
            return m.group(0).rstrip("/.,;") if m else None

        return await asyncio.wait_for(asyncio.to_thread(_call), timeout=20.0)
    except Exception:
        return None


async def _find_website(client: httpx.AsyncClient, name: str, tried: list[str]) -> str | None:
    """Каскад: Wikipedia → доменні гевристики → Gemini → DuckDuckGo."""
    # 1) Wikipedia — найточніше для відомих публічних компаній
    try:
        wiki = await asyncio.wait_for(fetch_wikipedia_website(client, name), timeout=8.0)
    except asyncio.TimeoutError:
        wiki = None
    if wiki:
        tried.append(f"[wiki] {wiki}")
        canonical = await _validate_candidate(client, wiki, name)
        if canonical:
            return canonical
        # Вікі довіряємо навіть якщо валідація провалилась (Cloudflare etc.)
        # але тільки якщо хост перевіряється на slug
        from urllib.parse import urlparse as _up
        host = (_up(wiki).hostname or "").lower()
        if _host_matches_name(host, name):
            return wiki

    # 2) Прямі домен-гіпотези
    site = await _find_website_by_guessing(client, name, tried)
    if site:
        return site

    # 3) Pre-flight Gemini (повільніше, але універсальне)
    try:
        g = await gemini_find_website(name)
    except Exception:
        g = None
    if g:
        tried.append(f"[gemini] {g}")
        canonical = await _validate_candidate(client, g, name)
        if canonical:
            return canonical
        from urllib.parse import urlparse as _up
        host = (_up(g).hostname or "").lower()
        if _host_matches_name(host, name):
            return g

    # 4) Останній резерв — DuckDuckGo
    return await _find_website_via_duckduckgo(client, name)


async def _find_subpage(client: httpx.AsyncClient, base: str, keywords: list[str]) -> str | None:
    """Шукає на головній посилання що містять keywords (career, jobs, vacancies тощо)."""
    html = await _fetch(client, base)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        text = (a.get_text() or "").lower()
        for kw in keywords:
            if kw in href or kw in text:
                return urljoin(base, a["href"])
    return None


async def _fetch_dou(client: httpx.AsyncClient, name: str) -> tuple[str | None, str]:
    """Прямі гевристики dou.ua/companies/<slug> + DuckDuckGo як резерв."""
    slug = _slug(name)
    parts = [_slug(p) for p in re.split(r"\s+", name) if _slug(p)]
    hyphen = "-".join(parts)
    candidates = []
    for s in dict.fromkeys([slug, hyphen, *parts]):
        if s and len(s) > 1:
            candidates.append(f"https://jobs.dou.ua/companies/{s}/")
    # пряма перевірка
    for url in candidates:
        try:
            r = await client.get(url, headers=HEADERS, follow_redirects=True, timeout=TIMEOUT)
            if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
                # перевіряємо що це сторінка компанії а не 404
                low = r.text[:5000].lower()
                if "companies" in str(r.url) and (slug in low or any(p in low for p in parts)):
                    text, _ = _clean_text(r.text, limit=3000)
                    return str(r.url), text
        except Exception:
            continue
    # резерв: DuckDuckGo
    query = f"{name} site:jobs.dou.ua/companies"
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    try:
        r = await client.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return None, ""
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            m = re.search(r"uddg=([^&]+)", href)
            real = href
            if m:
                from urllib.parse import unquote
                real = unquote(m.group(1))
            if "dou.ua/companies/" in real:
                page = await _fetch(client, real)
                if page:
                    text, _ = _clean_text(page, limit=3000)
                    return real, text
    except Exception:
        pass
    return None, ""


async def _fetch_news(client: httpx.AsyncClient, name: str) -> list[dict]:
    """Останні згадки через Google News RSS (без ключа). Парсимо stdlib-ом."""
    url = f"https://news.google.com/rss/search?q={quote_plus(name)}&hl=uk&gl=UA&ceid=UA:uk"
    try:
        r = await client.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
        items = []
        for it in root.iterfind(".//item"):
            t = it.findtext("title") or ""
            src = it.find("source")
            src_text = (src.text if src is not None else "") or ""
            pub = it.findtext("pubDate") or ""
            link = it.findtext("link") or ""
            items.append({
                "title": t[:200],
                "source": src_text[:80],
                "date": pub[:25],
                "link": link[:300],
            })
            if len(items) >= 10:
                break
        return items
    except Exception:
        return []


# ============================================================
# PageSpeed Insights (free Google API, без ключа працює з лімітами)
# ============================================================
async def fetch_pagespeed(client: httpx.AsyncClient, url: str) -> dict | None:
    """Lighthouse score через PageSpeed Insights API. ~20с на запит."""
    api = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
    params = [
        ("url", url),
        ("strategy", "mobile"),
        ("category", "performance"),
        ("category", "seo"),
        ("category", "accessibility"),
        ("category", "best-practices"),
    ]
    # PageSpeed API: можна вказати окремий ключ через PAGESPEED_API_KEY,
    # або викоистати GEMINI_API_KEY (з активованим PageSpeed Insights API у проекті).
    key = os.getenv("PAGESPEED_API_KEY") or os.getenv("GEMINI_API_KEY")
    if key:
        params.append(("key", key))
    try:
        r = await client.get(api, params=params, timeout=httpx.Timeout(60.0))
        if r.status_code != 200:
            return None
        data = r.json()
        cats = (data.get("lighthouseResult") or {}).get("categories") or {}
        scores: dict[str, int] = {}
        labels = {
            "performance": "performance",
            "seo": "seo",
            "accessibility": "accessibility",
            "best-practices": "best_practices",
        }
        for k_in, k_out in labels.items():
            v = (cats.get(k_in) or {}).get("score")
            if v is not None:
                scores[k_out] = round(v * 100)
        audits = (data.get("lighthouseResult") or {}).get("audits") or {}
        cwv: dict[str, str] = {}
        for friendly, audit_id in (
            ("LCP", "largest-contentful-paint"),
            ("CLS", "cumulative-layout-shift"),
            ("TBT", "total-blocking-time"),
            ("Speed Index", "speed-index"),
        ):
            v = (audits.get(audit_id) or {}).get("displayValue")
            if v:
                cwv[friendly] = v
        return {"url": url, "scores": scores, "cwv": cwv} if scores else None
    except Exception:
        return None


# ============================================================
# Відкриті вакансії: jobs.dou.ua + work.ua + robota.ua
# ============================================================
async def fetch_vacancies(client: httpx.AsyncClient, name: str, dou_url: str | None) -> dict:
    """Підраховує відкриті вакансії з трьох джерел."""
    slug = _slug(name)
    parts = [_slug(p) for p in re.split(r"\s+", name) if _slug(p)]
    hyphen = "-".join(parts)
    by_source: dict[str, int] = {}
    sources_urls: dict[str, str] = {}

    # ---- 1) jobs.dou.ua ----
    async def _dou_count() -> tuple[int, str] | None:
        # маємо dou_url зазвичай вигляду https://jobs.dou.ua/companies/{slug}/
        candidates: list[str] = []
        if dou_url:
            base = dou_url.rstrip("/")
            candidates.append(f"{base}/vacancies/")
            candidates.append(base + "/")
        for s in dict.fromkeys([slug, hyphen]):
            if s:
                candidates.append(f"https://jobs.dou.ua/companies/{s}/vacancies/")
        for url in candidates:
            try:
                r = await client.get(url, headers=HEADERS, follow_redirects=True, timeout=TIMEOUT)
                if r.status_code != 200:
                    continue
                html = r.text
                # шукаємо точне число у форматі "Вакансії: N" або у заголовку
                m = re.search(r"вакансі[іїй]\s*[—–:]?\s*(\d+)", html, re.I)
                if m:
                    return int(m.group(1)), url
                # альтернативно — рахуємо посилання на вакансії
                soup = BeautifulSoup(html, "html.parser")
                links = soup.select("a[href*='/companies/'][href*='/vacancies/']")
                # це посилання на сторінки інших компаній — не годиться. Беремо <li class="l-vacancy"> або заголовки.
                vac_blocks = soup.select(".l-vacancy, .vacancy, .vt")
                if vac_blocks:
                    return len(vac_blocks), url
            except Exception:
                continue
        return None

    # ---- 2) work.ua ----
    async def _work_ua_count() -> tuple[int, str] | None:
        # шукаємо роботодавця через search-сторінку work.ua
        candidates = [
            f"https://www.work.ua/jobs-{slug}/",  # пошук по ключу
            f"https://www.work.ua/jobs-{hyphen}/" if hyphen != slug else None,
        ]
        # також пробуємо знайти ID-сторінку через employer search
        try:
            q = quote_plus(name)
            r = await client.get(f"https://www.work.ua/employers/?search={q}", headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                # перше посилання на employer
                a = soup.select_one("a[href^='/employer/']")
                if a and a.get("href"):
                    emp_url = "https://www.work.ua" + a["href"]
                    candidates.insert(0, emp_url)
        except Exception:
            pass

        for url in candidates:
            if not url:
                continue
            try:
                r = await client.get(url, headers=HEADERS, follow_redirects=True, timeout=TIMEOUT)
                if r.status_code != 200:
                    continue
                html = r.text
                # work.ua пише "N вакансій" або "Знайдено N вакансій"
                m = re.search(r"(?:Знайдено\s+)?(\d+)\s+вакансі", html, re.I)
                if m:
                    return int(m.group(1)), url
                # альтернатива — кількість job-карток
                soup = BeautifulSoup(html, "html.parser")
                cards = soup.select("h2 a[href^='/jobs/']")
                if cards:
                    return len(cards), url
            except Exception:
                continue
        return None

    # ---- 3) robota.ua через GraphQL — 2-ступенева точність ----
    #   Step 1: знаходимо companyIds за назвою (через query `companies`).
    #   Step 2: фільтруємо вакансії саме за цією компанією, а не keyword.
    #   Якщо companyIds не знайдено — фолбек на keyword.
    async def _robota_count() -> tuple[int, str] | None:
        try:
            from curl_cffi import requests as cf
        except ImportError:
            return None

        def _call():
            # Step 1: пошук компанії
            companies_q = """
            query FindCompany($q: String!) {
              companies(first: 5, where: { name: { contains: $q } }) {
                nodes { id name }
              }
            }
            """
            company_ids: list[str] = []
            try:
                r = cf.post(
                    "https://dracula.robota.ua/graphql",
                    json={"query": companies_q, "variables": {"q": name}},
                    impersonate="chrome120",
                    timeout=15,
                )
                if r.status_code == 200:
                    d = r.json()
                    if "errors" not in d:
                        nodes = ((d.get("data") or {}).get("companies") or {}).get("nodes") or []
                        # фільтр на близьку відповідність
                        nl = name.lower()
                        for node in nodes:
                            cn = (node.get("name") or "").lower()
                            if cn and (nl in cn or cn in nl):
                                company_ids.append(node.get("id"))
            except Exception:
                pass

            # Step 2: вакансії
            if company_ids:
                vac_q = """
                query VacByCompany($ids: [ID!]) {
                  publishedVacancies(filter: {companyIds: $ids}, pagination: {page: 1, count: 1}) {
                    totalCount
                  }
                }
                """
                try:
                    r = cf.post(
                        "https://dracula.robota.ua/graphql",
                        json={"query": vac_q, "variables": {"ids": company_ids}},
                        impersonate="chrome120",
                        timeout=15,
                    )
                    if r.status_code == 200:
                        d = r.json()
                        if "errors" not in d:
                            total = (d.get("data") or {}).get("publishedVacancies", {}).get("totalCount")
                            if isinstance(total, int):
                                return total, "https://robota.ua/company-search"
                except Exception:
                    pass

            # Фолбек: keyword (як раніше)
            kw_q = """
            query Search($kw: String) {
              publishedVacancies(filter: {keywords: $kw}, pagination: {page: 1, count: 1}) {
                totalCount
              }
            }
            """
            try:
                r = cf.post(
                    "https://dracula.robota.ua/graphql",
                    json={"query": kw_q, "variables": {"kw": name}},
                    impersonate="chrome120",
                    timeout=15,
                )
                if r.status_code == 200:
                    d = r.json()
                    if "errors" not in d:
                        total = (d.get("data") or {}).get("publishedVacancies", {}).get("totalCount")
                        if isinstance(total, int):
                            return total, f"https://robota.ua/zapros/{quote_plus(name)}"
            except Exception:
                pass
            return None
        try:
            return await asyncio.wait_for(asyncio.to_thread(_call), timeout=25.0)
        except Exception:
            return None

    # ---- 4) Djinni keyword search — реальна кількість з пагінації ----
    async def _djinni_count() -> tuple[int, str] | None:
        try:
            from curl_cffi import requests as cf
        except ImportError:
            return None
        base = f"https://djinni.co/jobs/?keywords={quote_plus(name)}"
        def _call():
            # Парсимо першу сторінку — там зазвичай показано "Знайдено N" або є пагінація.
            r = cf.get(base, impersonate="chrome120", timeout=15)
            if r.status_code != 200:
                return None
            html = r.text
            # 1) пошук точного числа у заголовку результатів
            #    Приклади: "Knowledge Base: 12 jobs" / "12 вакансій" / "12 jobs found"
            for pat in (r"(\d+)\s+jobs?\b", r"(\d+)\s+вакан", r"of\s+(\d+)\s+jobs?"):
                m = re.search(pat, html, re.IGNORECASE)
                if m:
                    return int(m.group(1)), base
            # 2) рахуємо унікальні job-IDs на всіх сторінках
            all_ids = set(re.findall(r"/jobs/(\d+)", html))
            # парсимо пагінацію — посилання типу ?page=2
            pages = set(re.findall(r"[?&]page=(\d+)", html))
            max_page = max((int(p) for p in pages), default=1)
            # обмежуємо щоб не довбити сервер
            for p in range(2, min(max_page, 5) + 1):
                rr = cf.get(f"{base}&page={p}", impersonate="chrome120", timeout=15)
                if rr.status_code != 200:
                    break
                ids_p = set(re.findall(r"/jobs/(\d+)", rr.text))
                if not ids_p:
                    break
                all_ids |= ids_p
            if not all_ids:
                return None
            # Якщо max_page > 5 — додаємо «+» приблизно
            return len(all_ids), base
        try:
            return await asyncio.wait_for(asyncio.to_thread(_call), timeout=30.0)
        except Exception:
            return None

    # ---- 5) work.ua — Cloudflare JS-challenge, прямо не парситься.
    #         Залишаємо на Gemini Search (через grounding у analyzer).

    # паралельно
    dou_res, robota_res, djinni_res = await asyncio.gather(
        _dou_count(), _robota_count(), _djinni_count()
    )
    if dou_res:
        by_source["jobs.dou.ua"] = dou_res[0]
        sources_urls["jobs.dou.ua"] = dou_res[1]
    if robota_res:
        by_source["robota.ua"] = robota_res[0]
        sources_urls["robota.ua"] = robota_res[1]
    if djinni_res:
        by_source["djinni.co"] = djinni_res[0]
        sources_urls["djinni.co"] = djinni_res[1]

    return {
        "by_source": by_source,
        "sources": sources_urls,
        "total": sum(by_source.values()) if by_source else None,
        "note": "DOU, robota.ua, Djinni — прямі дані. work.ua блокує скрапінг (Cloudflare) — оцінка через Google Search.",
    }


# ============================================================
# Соцмережі: з футера + гевристичні HEAD-перевірки
# ============================================================
SOCIAL_DOMAINS = {
    "instagram": ["instagram.com"],
    "facebook":  ["facebook.com", "fb.com"],
    "linkedin":  ["linkedin.com"],
    "youtube":   ["youtube.com", "youtu.be"],
    "tiktok":    ["tiktok.com"],
    "twitter":   ["twitter.com", "x.com"],
    "telegram":  ["t.me", "telegram.me"],
}


def extract_socials_from_html(html: str) -> dict[str, str]:
    """Витягує посилання на соцмережі з HTML (зазвичай футер компанії)."""
    if not html:
        return {}
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        lo = href.lower()
        for net, domains in SOCIAL_DOMAINS.items():
            if net in found:
                continue
            if any(d in lo for d in domains):
                # відсікаємо share-посилання (sharer.php, /share/)
                if "share" in lo or "/intent/" in lo:
                    continue
                found[net] = href if href.startswith("http") else "https:" + href if href.startswith("//") else href
    return found


async def verify_socials_head(client: httpx.AsyncClient, urls: dict[str, str]) -> dict[str, str]:
    """HEAD-запит щоб переконатись що сторінка існує."""
    async def _check(net: str, url: str):
        try:
            r = await client.head(url, headers=HEADERS, follow_redirects=True, timeout=TIMEOUT)
            if r.status_code < 400:
                return net, url
            # деякі соцмережі не приймають HEAD — пробуємо GET
            r = await client.get(url, headers=HEADERS, follow_redirects=True, timeout=TIMEOUT)
            if r.status_code < 400:
                return net, url
        except Exception:
            return None
        return None

    results = await asyncio.gather(*[_check(n, u) for n, u in urls.items()])
    return {net: url for r in results if r for net, url in [r]}


async def guess_socials(client: httpx.AsyncClient, name: str) -> dict[str, str]:
    """Гевристика: пробуємо name на популярних соцмережах."""
    slug = _slug(name)
    if not slug or len(slug) < 3:
        return {}
    candidates = {
        "instagram": f"https://www.instagram.com/{slug}/",
        "facebook":  f"https://www.facebook.com/{slug}",
        "linkedin":  f"https://www.linkedin.com/company/{slug}/",
        "youtube":   f"https://www.youtube.com/@{slug}",
        "tiktok":    f"https://www.tiktok.com/@{slug}",
    }
    return await verify_socials_head(client, candidates)


async def collect(name: str, user_website: str | None = None) -> CompanyData:
    data = CompanyData(name=name)
    async with httpx.AsyncClient(http2=False) as client:
        # 1. сайт
        if user_website:
            data.website = user_website if user_website.startswith("http") else f"https://{user_website}"
        else:
            data.website = await _find_website(client, name, data.candidates_tried)
            if not data.website:
                data.errors.append(
                    f"Не вдалося автоматично знайти офіційний сайт "
                    f"(спробували {len(data.candidates_tried)} доменів)"
                )

        # 2. паралельно: головна, dou, news
        tasks = []
        if data.website:
            tasks.append(_fetch(client, data.website))
        else:
            tasks.append(asyncio.sleep(0, result=None))
        tasks.append(_fetch_dou(client, name))
        tasks.append(_fetch_news(client, name))
        homepage_html, (dou_url, dou_text), news = await asyncio.gather(*tasks)

        data.dou_url = dou_url
        data.dou_text = dou_text
        data.news_items = news

        if homepage_html:
            data.homepage_html = homepage_html
            text, meta = _clean_text(homepage_html)
            data.homepage_text = text
            data.homepage_html_meta = meta

            # 3. знайти кар'єрну та про нас
            careers_url = await _find_subpage(client, data.website, [
                "career", "careers", "/jobs", "vacanc", "карʼєр", "кар'єр", "карьер", "робота"
            ])
            about_url = await _find_subpage(client, data.website, [
                "about", "/про", "/about-us", "company"
            ])
            data.careers_url = careers_url
            data.about_url = about_url
            subtasks = []
            if careers_url:
                subtasks.append(_fetch(client, careers_url))
            else:
                subtasks.append(asyncio.sleep(0, result=None))
            if about_url:
                subtasks.append(_fetch(client, about_url))
            else:
                subtasks.append(asyncio.sleep(0, result=None))
            careers_html, about_html = await asyncio.gather(*subtasks)
            if careers_html:
                data.careers_text, _ = _clean_text(careers_html, limit=4000)
            if about_html:
                data.about_text, _ = _clean_text(about_html, limit=3000)
        else:
            data.errors.append("Не вдалося завантажити головну сторінку сайту")

        # ---- Паралельно: PageSpeed + вакансії + соцмережі (гевристика) ----
        async def _gather_extras():
            ps_task = fetch_pagespeed(client, data.website) if data.website else asyncio.sleep(0, result=None)
            vac_task = fetch_vacancies(client, name, data.dou_url)
            guess_task = guess_socials(client, name)
            return await asyncio.gather(ps_task, vac_task, guess_task)

        try:
            ps, vac, guessed_socials = await _gather_extras()
        except Exception as e:
            ps, vac, guessed_socials = None, None, {}
            data.errors.append(f"Помилка збору додаткових метрик: {e}")

        if ps:
            data.pagespeed = ps
        if vac:
            data.vacancies = vac

        # Об'єднуємо соцмережі: з футера + з гевристики
        from_footer = extract_socials_from_html(data.homepage_html) if data.homepage_html else {}
        # Спершу перевіримо те що з футера (швидко)
        verified_footer: dict[str, str] = {}
        if from_footer:
            verified_footer = await verify_socials_head(client, from_footer)

        socials_combined: dict[str, str] = {}
        socials_combined.update(verified_footer)  # пріоритет — з футера
        for net, url in (guessed_socials or {}).items():
            if net not in socials_combined:
                socials_combined[net] = url

        data.socials = socials_combined
        data.socials_meta = {
            "from_footer": list(verified_footer.keys()),
            "guessed": [n for n in (guessed_socials or {}).keys() if n not in verified_footer],
        }

    return data
