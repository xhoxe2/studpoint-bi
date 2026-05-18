// Stud-Point AI Business Intelligence · WordPress embed build
// API_BASE автоматично береться з URL цього скрипта.
// Приклад: <script src="https://studpoint-bi.onrender.com/wp.js"></script>
// → API_BASE = "https://studpoint-bi.onrender.com"
const API_BASE = (function () {
  try {
    const s = document.currentScript || document.querySelector('script[src*="wp.js"]');
    if (s && s.src) {
      const u = new URL(s.src);
      return `${u.protocol}//${u.host}`;
    }
  } catch (_) {}
  return '';
})();

function analyzer() {
  return {
    company: '',
    website: '',
    view: 'hero',          // hero | loading | results | error | compare-form | compare-loading | compare-results
    report: null,
    error: '',
    websiteEdit: false,
    websiteEditValue: '',

    // Compare mode
    compareA: '',
    compareB: '',
    compareData: null,

    loadingStep: '',
    loadingIndex: 0,
    loadingSteps: [
      'Пошук офіційного сайту',
      'Завантаження головної сторінки',
      'Аналіз кар\'єрного розділу',
      'Перевірка профілю на dou.ua',
      'Збір згадок у медіа',
      'Підрахунок вакансій (DOU + robota.ua + Djinni)',
      'AI-пошук свіжих даних',
      'Формування скорингу 0-100',
      'Підбір послуг під проблеми',
      'Конкурентний бенчмарк',
    ],
    elapsedSec: 0,
    _loadingTimer: null,
    _elapsedTimer: null,

    init() {
      this.$nextTick(() => this.animateHero());
    },

    // ===== Допоміжні =====
    issueNumber() {
      const d = new Date();
      const y = d.getFullYear();
      const dayOfYear = Math.floor((d - new Date(y, 0, 0)) / 86400000);
      return `${y}.${String(dayOfYear).padStart(3, '0')}`;
    },
    dateString() {
      const d = new Date();
      const months = ['СІЧ','ЛЮТ','БЕР','КВІТ','ТРАВ','ЧЕРВ','ЛИП','СЕРП','ВЕР','ЖОВТ','ЛИСТ','ГРУД'];
      return `${String(d.getDate()).padStart(2,'0')} ${months[d.getMonth()]} ${d.getFullYear()}`;
    },

    categoryList() {
      return [
        { key: 'website',      label: 'Сайт',        num: '§ 01.1' },
        { key: 'seo_traffic',  label: 'SEO / трафік', num: '§ 01.2' },
        { key: 'social_media', label: 'Соцмережі',   num: '§ 01.3' },
        { key: 'hr_brand',     label: 'HR-бренд',    num: '§ 01.4' },
        { key: 'reputation',   label: 'Репутація',   num: '§ 01.5' },
      ];
    },

    scoreColor(v) {
      // Залишаємось у палітрі — без неонів. Темна earth-палітра.
      if (v >= 75) return 'var(--moss)';   // forest
      if (v >= 55) return 'var(--ink)';    // ink (нейтрально)
      if (v >= 35) return 'var(--ochre)';  // ochre
      return 'var(--clay)';                // clay
    },

    scoreLabel(v) {
      if (v >= 75) return 'Сильно';
      if (v >= 55) return 'Стабільно';
      if (v >= 35) return 'Слабко';
      return 'Критично';
    },
    scoreVerdict(v) {
      if (v >= 75) return 'Лідер у категорії · сильні позиції';
      if (v >= 60) return 'Середній бенчмарк · є простір';
      if (v >= 40) return 'Нижче середнього · потрібні дії';
      return 'Критична зона · негайні втручання';
    },

    severityLabel(s) {
      return ({ high: 'Критично', medium: 'Середньо', low: 'Низько' })[s] || s || '';
    },

    // ===== Об'єктивні метрики =====
    hasObjectiveMetrics() {
      const m = this.report?._meta || {};
      return !!(m.pagespeed?.scores
        || this.hasVacanciesData()
        || Object.keys(m.socials || {}).length
        || (m.news_sentiment_stats?.total || 0) > 0);
    },

    pagespeedRows() {
      const s = this.report?._meta?.pagespeed?.scores || {};
      const labels = {
        performance: 'Performance',
        seo: 'SEO',
        accessibility: 'A11y',
        best_practices: 'Best Practices',
      };
      return Object.entries(labels)
        .filter(([k]) => s[k] !== undefined)
        .map(([k, label]) => ({ key: k, label, value: s[k] }));
    },

    hasVacanciesData() {
      const m = this.report?._meta || {};
      const scraped = m.vacancies_scraped?.total;
      const est = m.vacancies_estimate?.total_approx;
      return (scraped && scraped > 0) || (est && est > 0);
    },

    vacanciesTotal() {
      const m = this.report?._meta || {};
      return m.vacancies_scraped?.total ?? m.vacancies_estimate?.total_approx ?? null;
    },

    vacanciesBySource() {
      // повертає масив {source, count, mode} відсортований за кількістю
      const m = this.report?._meta || {};
      const est = m.vacancies_estimate?.by_source || {};
      const scraped = m.vacancies_scraped?.by_source || {};
      const out = {};
      Object.entries(est).forEach(([k, v]) => {
        if (v && !out[k]) out[k] = { source: k, count: v, mode: 'estimate' };
      });
      Object.entries(scraped).forEach(([k, v]) => {
        if (v) out[k] = { source: k, count: v, mode: 'direct' };
      });
      return Object.values(out).sort((a, b) => b.count - a.count);
    },

    vacancyColor() {
      const n = this.vacanciesTotal() ?? 0;
      if (n >= 20) return 'var(--moss)';
      if (n >= 5)  return 'var(--ochre)';
      if (n >= 1)  return 'var(--ink2)';
      return 'var(--clay)';
    },
    vacanciesLabel() {
      const n = this.vacanciesTotal() ?? 0;
      if (n >= 30) return 'активний рекрутинг';
      if (n >= 10) return 'регулярний найм';
      if (n >= 3)  return 'точковий найм';
      if (n >= 1)  return 'мінімальна активність';
      return 'не виявлено';
    },

    socialSource(net) {
      const m = this.report?._meta?.socials_meta || {};
      if ((m.from_footer || []).includes(net)) return 'з футера сайту';
      if ((m.guessed || []).includes(net))     return 'підтверджено';
      return 'знайдено';
    },

    shortUrl(url) {
      try {
        const u = new URL(url);
        return u.host.replace('www.', '') + u.pathname.replace(/\/$/, '');
      } catch { return url; }
    },

    sentimentColor(s) {
      return ({ positive: 'var(--moss)', negative: 'var(--clay)', neutral: 'rgba(26,24,20,0.15)' })[s] || 'rgba(26,24,20,0.15)';
    },

    // ===== Конкуренти + дельта =====
    hasCompetitors() {
      const r = this.report || {};
      return (r.direct_competitors || []).length || (r.indirect_competitors || []).length || (r.competitors || []).length;
    },

    deltaValue(competitorScore) {
      // Дельта = СКІЛЬКИ конкурент кращий за нас.
      // Якщо competitor > us → конкурент сильніший (для нас погано) → +N (clay)
      // Якщо competitor < us → конкурент слабший (для нас добре) → -N (moss)
      const us = this.report?.overall_score ?? 0;
      return (competitorScore ?? 0) - us;
    },
    deltaText(competitorScore) {
      const d = this.deltaValue(competitorScore);
      if (d > 0) return `+${d}`;
      if (d < 0) return `−${Math.abs(d)}`;
      return '±0';
    },
    deltaColor(competitorScore) {
      const d = this.deltaValue(competitorScore);
      if (d > 0) return 'text-clay';   // конкурент сильніший
      if (d < 0) return 'text-moss';   // ми сильніші
      return 'text-ink2';
    },

    // ===== Юр-факти =====
    hasLegalInfo() {
      const li = this.report?.legal_info;
      if (!li) return false;
      return li.registered_name || li.edrpou || li.year_founded || li.approx_employees;
    },
    sizeLabel(s) {
      return ({
        small: 'малий бізнес',
        medium: 'середній бізнес',
        large: 'великий бізнес',
        enterprise: 'корпорація',
      })[s] || '';
    },
    sizeColor(s) {
      return ({
        small: 'text-ink3',
        medium: 'text-ochre',
        large: 'text-moss',
        enterprise: 'text-clay',
      })[s] || 'text-ink3';
    },

    // ===== Скрін головної через thum.io (безкоштовно, без ключа) =====
    siteScreenshot() {
      const url = this.report?._meta?.website_found;
      if (!url) return null;
      const clean = url.replace(/^https?:\/\//, '');
      return `https://image.thum.io/get/width/1200/crop/800/${url}`;
    },

    // ===== Confirmation banner =====
    websiteSourceLabel() {
      const s = this.report?._meta?.website_source;
      return ({
        manual: 'вказано вручну',
        wikipedia: 'знайдено у Wikipedia',
        heuristic: 'домен-гевристикою',
        gemini_search: 'через AI-пошук',
      })[s] || 'автоматично';
    },
    websiteSourceColor() {
      const s = this.report?._meta?.website_source;
      return ({
        manual: 'text-moss',
        wikipedia: 'text-moss',
        heuristic: 'text-ink2',
        gemini_search: 'text-ochre',
      })[s] || 'text-ink3';
    },
    startWebsiteEdit() {
      this.websiteEditValue = this.report?._meta?.website_found || '';
      this.websiteEdit = true;
    },
    async reanalyzeWithUrl() {
      const url = this.websiteEditValue.trim();
      if (!url) return;
      this.websiteEdit = false;
      this.website = url;
      // company зберігається у стейті — просто запускаємо повторно
      await this.run();
    },

    // ===== Анімації =====
    animateHero() {
      const tl = gsap.timeline({ defaults: { ease: 'power3.out' } });
      tl.from('[data-anim="h1"]', { y: 18, opacity: 0, duration: 0.7 })
        .from('[data-anim="h2"]', { y: 40, opacity: 0, duration: 1.0 }, '-=0.4')
        .from('[data-anim="h3"]', { y: 20, opacity: 0, duration: 0.7 }, '-=0.6')
        .from('[data-anim="h4"]', { y: 20, opacity: 0, duration: 0.7 }, '-=0.5')
        .from('[data-anim="h5"] > div', { y: 24, opacity: 0, duration: 0.7, stagger: 0.12 }, '-=0.4');
    },

    animateResults() {
      this.$nextTick(() => {
        const ids = ['r-confirm','r-screen','r-cover','r-scores','r-metrics','r-legal','r-exec','r-problems','r-comp','r-sources','r-foot'];
        ids.forEach((id, i) => {
          const el = document.querySelector(`[data-anim="${id}"]`);
          if (!el) return;
          gsap.from(el, { y: 30, opacity: 0, duration: 0.9, delay: i * 0.10, ease: 'power3.out' });
        });

        // Counter-up для головного скору
        const big = document.querySelector('[data-anim="r-cover"] .font-serif.font-light');
      });
    },

    startLoading() {
      this.loadingIndex = 0;
      this.loadingStep = this.loadingSteps[0];
      this.elapsedSec = 0;
      this._elapsedTimer = setInterval(() => { this.elapsedSec += 1; }, 1000);
      const tick = () => {
        this.loadingIndex = Math.min(this.loadingIndex + 1, this.loadingSteps.length - 1);
        this.loadingStep = this.loadingSteps[this.loadingIndex];
        // повільніше: ~3.5с на крок → ~35с на весь цикл
        this._loadingTimer = setTimeout(tick, 3200 + Math.random() * 1200);
      };
      this._loadingTimer = setTimeout(tick, 2400);
    },
    stopLoading() {
      if (this._loadingTimer) clearTimeout(this._loadingTimer);
      if (this._elapsedTimer) clearInterval(this._elapsedTimer);
      this._loadingTimer = null;
      this._elapsedTimer = null;
    },

    loadingHint() {
      if (this.elapsedSec < 15) return 'AI шукає свіжі дані в інтернеті…';
      if (this.elapsedSec < 30) return 'Триває аналіз. Зазвичай це займає 20–60 секунд.';
      if (this.elapsedSec < 50) return 'Майже готово — формуємо звіт. Можливо до 60с при пошуку.';
      return 'AI зайнятий — є retry. Зачекайте ще трохи або перезавантажте.';
    },

    async run() {
      const name = this.company.trim();
      if (!name) return;
      this.view = 'loading';
      this.report = null;
      this.error = '';
      this.startLoading();

      try {
        const resp = await fetch(API_BASE + '/api/analyze', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ company: name, website: this.website || null }),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || 'Невідома помилка');
        this.report = data;
        this.view = 'results';
        this.stopLoading();
        this.animateResults();
        window.scrollTo({ top: 0, behavior: 'smooth' });
      } catch (e) {
        this.stopLoading();
        this.error = e.message || String(e);
        this.view = 'error';
      }
    },

    reset() {
      this.view = 'hero';
      this.company = '';
      this.website = '';
      this.report = null;
      this.compareA = '';
      this.compareB = '';
      this.compareData = null;
      this.$nextTick(() => this.animateHero());
    },

    // ===== Compare mode =====
    openCompare() {
      this.view = 'compare-form';
    },
    async runCompare() {
      const a = this.compareA.trim();
      const b = this.compareB.trim();
      if (!a || !b) return;
      this.view = 'loading';
      this.report = null;
      this.compareData = null;
      this.startLoading();
      try {
        const [resA, resB] = await Promise.all([
          fetch(API_BASE + '/api/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ company: a }),
          }).then(r => r.json()),
          fetch(API_BASE + '/api/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ company: b }),
          }).then(r => r.json()),
        ]);
        if (resA.detail || resB.detail) {
          throw new Error(resA.detail || resB.detail);
        }
        this.compareData = { A: resA, B: resB };
        this.view = 'compare-results';
        this.stopLoading();
        window.scrollTo({ top: 0, behavior: 'smooth' });
      } catch (e) {
        this.stopLoading();
        this.error = e.message || String(e);
        this.view = 'error';
      }
    },
    compareDeltaText(catKey) {
      const a = this.compareData?.A?.scores?.[catKey] ?? 0;
      const b = this.compareData?.B?.scores?.[catKey] ?? 0;
      const d = a - b; // позитивне → A кращий
      if (d > 0) return `A +${d}`;
      if (d < 0) return `B +${Math.abs(d)}`;
      return '=';
    },
    compareDeltaColor(catKey) {
      const a = this.compareData?.A?.scores?.[catKey] ?? 0;
      const b = this.compareData?.B?.scores?.[catKey] ?? 0;
      if (a > b) return 'text-moss';
      if (a < b) return 'text-clay';
      return 'text-ink2';
    },
    compareVerdict() {
      const A = this.compareData?.A;
      const B = this.compareData?.B;
      if (!A || !B) return '';
      const sa = A.overall_score ?? 0;
      const sb = B.overall_score ?? 0;
      const nA = A.company?.name || 'A';
      const nB = B.company?.name || 'B';
      if (sa === sb) {
        return `<strong>${nA}</strong> та <strong>${nB}</strong> на одному рівні (<span class="font-mono">${sa}</span>). Виграє той, хто першим закриє слабкі категорії.`;
      }
      const winner = sa > sb ? nA : nB;
      const loser  = sa > sb ? nB : nA;
      const gap = Math.abs(sa - sb);
      return `<strong>${winner}</strong> попереду на <span class="font-mono text-moss">+${gap}</span> балів. <strong>${loser}</strong> має наздогнати — починати з найслабших категорій.`;
    },
  };
}
