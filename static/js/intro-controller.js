/** Intro owns only one session flag; it never waits for GPS, map or API data. */
(() => {
    'use strict';
    const intro = document.getElementById('app-intro');
    if (!intro) return;
    const key = 'commute-master:intro-seen';
    let fadeTimer, hideTimer;
    function finish() {
        clearTimeout(fadeTimer); clearTimeout(hideTimer);
        intro.hidden = true;
        intro.classList.remove('is-fading');
    }
    try {
        let seen = false;
        try { seen = sessionStorage.getItem(key) === '1'; } catch (_) { /* Storage may be unavailable. */ }
        if (seen) return;
        // Schedule cleanup before revealing the overlay; CSS is an independent fallback.
        hideTimer = setTimeout(finish, 1400);
        fadeTimer = setTimeout(() => {
            try { intro.classList.add('is-fading'); } catch (_) { finish(); }
        }, 1000);
        intro.hidden = false;
        try { sessionStorage.setItem(key, '1'); } catch (_) { /* No user data is persisted. */ }
    } catch (_) { finish(); }
})();
