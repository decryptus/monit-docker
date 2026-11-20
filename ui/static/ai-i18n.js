'use strict';

// i18next handles catalogs, fallback, interpolation and plural forms locally.
// Authentication and incident assistance share this choice. Other dashboard
// views retain their existing language pending separate localization work.
window.MonitAIText = (() => {
  const LANGUAGES = ['en', 'fr'];
  const STORAGE_KEY = 'monit.ai.language.v1';
  const FALLBACK = 'en';
  const normalize = value => typeof value === 'string' ? value.toLowerCase().split('-')[0] : '';
  let stored;
  try { stored = localStorage.getItem(STORAGE_KEY); } catch (_) { /* Storage may be blocked. */ }
  const detected = (navigator.languages || [navigator.language]).map(normalize).find(code => LANGUAGES.includes(code));
  let language = LANGUAGES.includes(stored) ? stored : detected || FALLBACK;
  let available = false;
  const engine = window.i18next?.createInstance();
  const ready = (async () => {
    try {
      if (!engine) return false;
      const catalogs = await Promise.all(LANGUAGES.map(async code => {
        const response = await fetch(`/locales/ai-${code}.json`, {credentials: 'same-origin', signal: AbortSignal.timeout(10000)});
        if (!response.ok) throw new Error('catalog_unavailable');
        return [code, {translation: await response.json()}];
      }));
      await engine.init({lng: language, fallbackLng: FALLBACK, supportedLngs: LANGUAGES,
        resources: Object.fromEntries(catalogs), interpolation: {escapeValue: false}});
      available = true;
      return true;
    } catch (_) { return false; } // Monitoring remains available if catalogs fail.
  })();
  return {
    ready,
    get language() { return language; },
    get available() { return available; },
    t: (key, options) => engine.t(key, options),
    number: value => Number.isFinite(value) ? new Intl.NumberFormat(language).format(value) : '—',
    async select(value) {
      if (!LANGUAGES.includes(value) || !await ready) return false;
      await engine.changeLanguage(value);
      language = value;
      document.dispatchEvent(new CustomEvent("monit-language", {detail: value}));
      try { localStorage.setItem(STORAGE_KEY, value); } catch (_) { /* In-memory choice still works. */ }
      return true;
    }
  };
})();
