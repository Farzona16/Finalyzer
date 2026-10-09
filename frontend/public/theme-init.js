/* Sets data-theme and lang before the first paint. Keep in sync with src/theme/store.ts and
   src/i18n/detect.ts (a test compares the storage keys). */
;(function () {
  try {
    var root = document.documentElement
    var theme = localStorage.getItem('fz_theme')
    if (theme !== 'light' && theme !== 'dark' && theme !== 'system') {
      var coarse = window.matchMedia && matchMedia('(pointer: coarse)').matches
      var lowMem = navigator.deviceMemory && navigator.deviceMemory <= 4
      theme = coarse || lowMem ? 'light' : 'system'
    }
    var dark =
      theme === 'dark' ||
      (theme === 'system' && !(window.matchMedia && matchMedia('(prefers-color-scheme: light)').matches))
    root.setAttribute('data-theme', dark ? 'dark' : 'light')
    var lang = localStorage.getItem('fz_lang')
    if (lang === 'uz' || lang === 'en') root.setAttribute('lang', lang)
  } catch {
    /* storage can be blocked; the defaults in index.html apply */
  }
})()
