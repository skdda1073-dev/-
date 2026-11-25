/** Browser checks for preparation advice. Every provider response is synthetic. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('playwright');
const { openApp, selectTool, noOverflow, adviceFixture, INJECTION } = require('./frontend_smoke.cjs');
const tests = [], test = (name, run) => tests.push({ name, run });
const ready = page => page.locator('#commute-advice-body[aria-busy="false"]').waitFor({ state: 'attached' });
const details = page => page.locator('#commute-advice-details');
async function openDetails(page) {
    if (!await details(page).isVisible()) await page.locator('#commute-advice-toggle').click();
    await details(page).waitFor({ state: 'visible' });
}
const alerts = [
    { id: 'rain', severity: 'notice', title: '우산을 챙기세요', message: '오후 비 예보가 있어요. 우산을 준비해 주세요.', source: '기상청', at: '2030-10-21T13:00:00+09:00' },
    { id: 'dust', severity: 'warning', title: '마스크를 챙기세요', message: '초미세먼지가 나쁨 수준이에요. 외출 전 마스크를 챙기세요.', source: '에어코리아', at: '2030-10-21T08:00:00+09:00' },
    { id: 'cold_wind', severity: 'notice', title: '장갑이나 목도리를 준비해요', message: '기온이 낮고 바람이 불어요. 따뜻하게 입고 나가세요.', source: '기상청', at: '2030-10-21T09:00:00+09:00' },
];
function fullAdvice(selected, overrides = {}) {
    return adviceFixture(selected, { alerts, summary: '출발 전에 챙기면 좋은 준비물이 있어요.',
        weather: { temperature_c: 3, wind_speed_ms: 4.3, rain_probability: 70 },
        air: { pm25: { value: 52, grade: '나쁨' } }, ...overrides });
}
async function bounds(page) {
    const result = await page.locator('#commute-advice-panel').evaluate(panel => {
        const rect = panel.getBoundingClientRect();
        return { left: rect.left, right: rect.right, bottom: rect.bottom, top: rect.top,
            scroll: panel.scrollWidth, width: panel.clientWidth, viewport: innerWidth, height: innerHeight };
    });
    assert.ok(result.left >= 0 && result.right <= result.viewport + 1, JSON.stringify(result));
    assert.ok(result.top >= 64 && result.bottom <= result.height + 1, JSON.stringify(result));
    assert.ok(result.scroll <= result.width + 1, JSON.stringify(result));
    await noOverflow(page);
}

for (const width of [320, 390, 1440]) test(`${width}px advice shows rain, dust and cold together with honest source times`, async browser => {
    const app = await openApp(browser, { viewport: { width, height: 900 }, keepCommuteAdviceOpen: true,
        adviceHandler: (route, url) => route.fulfill({ json: fullAdvice(url.searchParams.get('campus')) }) });
    try {
        const page = app.page;
        await ready(page);
        assert.equal(await page.locator('#commute-advice-panel').isVisible(), false);
        assert.equal(await details(page).isVisible(), false);
        assert.equal(await page.locator('#commute-advice-toggle').getAttribute('aria-expanded'), 'false');
        assert.notEqual(await page.evaluate(() => document.activeElement.id), 'commute-advice-body');
        await openDetails(page);
        assert.equal(await page.locator('#commute-advice-alerts > li').count(), 3);
        assert.equal(await page.locator('#commute-advice-count').innerText(), '3');
        assert.equal(await page.locator('#commute-weather-temperature').innerText(), '3℃');
        assert.equal(await page.locator('#commute-weather-wind').innerText(), '4.3m/s');
        assert.match(await page.locator('#commute-weather-time').innerText(), /예보.*발표/);
        assert.match(await page.locator('#commute-air-time').innerText(), /중앙로.*춘천 참고값.*측정/);
        assert.equal(await page.locator('#commute-air-pm25-grade').innerText(), '나쁨');
        assert.equal(app.adviceRequests.length, 1);
        assert.deepEqual(await page.evaluate(() => window.__testGPSCalls), []);
        await bounds(page);
        if (process.env.CAPSTONE_ADVICE_SCREENSHOT_DIR) {
            await fs.mkdir(process.env.CAPSTONE_ADVICE_SCREENSHOT_DIR, { recursive: true });
            await page.screenshot({ path: path.join(process.env.CAPSTONE_ADVICE_SCREENSHOT_DIR, `advice-synthetic-${width}.png`), fullPage: false });
        }
        await page.locator('#commute-advice-close').click();
        assert.equal(await page.locator('#commute-advice-panel').isVisible(), false);
        assert.equal(await details(page).isVisible(), false);
        assert.equal(await page.locator('#commute-advice-toggle').isVisible(), true);
        assert.equal(await page.evaluate(() => document.activeElement.id), 'commute-advice-toggle');
        await page.locator('#commute-advice-toggle').press('Enter');
        assert.equal(await page.evaluate(() => document.activeElement.id), 'commute-advice-body');
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('#commute-advice-panel').isVisible(), false);
        assert.equal(await details(page).isVisible(), false);
        await page.locator('#commute-advice-toggle').click();
        await selectTool(page, 'taxi');
        assert.equal(await page.locator('#commute-advice-panel').isVisible(), false, 'Changing tools closes the overlay');
        assert.equal(await page.locator('#commute-advice-toggle').isVisible(), true);
        assert.equal(await page.locator('#transport-panel-taxi').isVisible(), true);
        assert.equal(app.adviceRequests.length, 1, 'Opening or closing does not re-query fresh data');
        assert.deepEqual(app.errors, []);
    } finally { await app.close(); }
});

test('partial service failures keep available weather and never invent clean air', async browser => {
    const app = await openApp(browser, { keepCommuteAdviceOpen: true, adviceHandler: (route, url) => route.fulfill({ json: fullAdvice(url.searchParams.get('campus'), {
        status: 'partial', summary: '날씨 예보를 확인했지만 대기질을 확인하지 못했어요.',
        air: { status: 'unavailable', code: 'AIR_PROVIDER_AUTH', observed_at: null, pm10: { value: null, grade: null }, pm25: { value: null, grade: null }, message: '대기질 정보를 불러오지 못했어요.' },
    }) }) });
    try {
        await ready(app.page);
        await openDetails(app.page);
        assert.equal(await app.page.locator('#commute-weather-values').isVisible(), true);
        assert.equal(await app.page.locator('#commute-air-values').isVisible(), false);
        assert.equal(await app.page.locator('[data-advice="dust"]').count(), 0);
        assert.equal(await app.page.locator('[data-advice="rain"]').count(), 1);
        assert.doesNotMatch(await app.page.locator('#commute-advice-panel').innerText(), /AIR_PROVIDER_AUTH|API|좋음|안전/);
    } finally { await app.close(); }
});

test('failed and malformed refreshes remove old advice while the app stays usable', async browser => {
    const app = await openApp(browser, { keepCommuteAdviceOpen: true, adviceHandler: (route, url, count) => count === 1
        ? route.fulfill({ json: fullAdvice(url.searchParams.get('campus')) })
        : count === 2 ? route.fulfill({ status: 503, json: { error: 'private provider failure' } })
            : route.fulfill({ json: { status: 'ok', campus: { id: 'hallym' }, weather: null, air: null } }) });
    try {
        await ready(app.page);
        await openDetails(app.page);
        for (let i = 0; i < 2; i++) {
            await app.page.locator('#commute-advice-refresh').click();
            await ready(app.page);
            assert.equal(await app.page.locator('#commute-advice-alerts > li').count(), 0);
            assert.equal(await app.page.locator('#commute-weather-values').isVisible(), false);
            assert.equal(await app.page.locator('#commute-air-values').isVisible(), false);
            assert.match(await app.page.locator('#commute-advice-status').innerText(), /불러오지 못/);
            assert.doesNotMatch(await app.page.locator('#commute-advice-panel').innerText(), /private provider|안전|맑음/);
            assert.equal(await app.page.locator('#commute-advice-refresh').isEnabled(), true);
        }
        await app.page.locator('#commute-advice-close').click();
        await selectTool(app.page, 'intercity');
        assert.equal(await app.page.locator('#transport-panel-intercity').isVisible(), true);
        assert.equal(await app.page.locator('#commute-advice-panel').isVisible(), false);
        assert.deepEqual(app.errors, []);
    } finally { await app.close(); }
});

test('changing campus rejects an uncancellable old response and keeps details folded', async browser => {
    let release, began;
    const gate = new Promise(resolve => { release = resolve; });
    const started = new Promise(resolve => { began = resolve; });
    const app = await openApp(browser, { keepCommuteAdviceOpen: true, adviceHandler: async (route, url, count) => {
        if (count === 2) { began(); await gate; }
        return route.fulfill({ json: fullAdvice(url.searchParams.get('campus'), { summary: count === 2 ? '오래된 한림대 응답' : '새로운 캠퍼스의 안내' }) }).catch(() => {});
    } });
    try {
        const page = app.page;
        await ready(page);
        await openDetails(page);
        await page.evaluate(() => {
            const original = window.fetch.bind(window);
            window.fetch = (url, options) => String(url).includes('/api/commute-advice')
                ? original(url, { ...options, signal: undefined }) : original(url, options);
        });
        await page.locator('#commute-advice-refresh').click();
        await started;
        await page.locator('#commute-advice-close').click();
        await page.locator('#tab-knu').click();
        await page.waitForFunction(() => document.getElementById('commute-advice-body').getAttribute('aria-busy') === 'false' && document.getElementById('commute-advice-campus').textContent.includes('강원'));
        const returned = page.waitForResponse(response => new URL(response.url()).pathname === '/api/commute-advice' && new URL(response.url()).searchParams.get('campus') === 'hallym');
        release(); await returned;
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        assert.equal(await page.locator('#commute-advice-panel').isVisible(), false);
        assert.equal(await details(page).isVisible(), false);
        await page.locator('#commute-advice-toggle').click();
        assert.match(await page.locator('#commute-advice-campus').innerText(), /강원대학교/);
        assert.doesNotMatch(await page.locator('#commute-advice-status').innerText(), /오래된/);
        assert.deepEqual(app.adviceRequests.map(item => item.campus), ['hallym', 'hallym', 'knu']);
    } finally { release(); await app.close(); }
});

test('stale data is visibly delayed and source text stays literal', async browser => {
    const app = await openApp(browser, { keepCommuteAdviceOpen: true, adviceHandler: (route, url) => route.fulfill({ json: fullAdvice(url.searchParams.get('campus'), {
        status: 'partial', weather: { status: 'stale', message: '최신 예보를 확인하지 못했어요.' },
        alerts: [...alerts, { id: 'dust', title: INJECTION, message: INJECTION, severity: 'warning', source: '에어코리아' }],
        air: { station_name: INJECTION, message: INJECTION },
    }) }) });
    try {
        await ready(app.page);
        await openDetails(app.page);
        assert.equal(await app.page.locator('#commute-weather-badge').innerText(), '자료 지연');
        assert.equal(await app.page.locator('[data-advice="rain"]').count(), 0);
        assert.equal(await app.page.locator('[data-advice="cold_wind"]').count(), 0);
        assert.equal(await app.page.locator('[data-advice="dust"]').count(), 1, 'Repeated alert IDs are not repeated');
        assert.match(await app.page.locator('#commute-air-message').innerText(), /<img/);
        assert.equal(await app.page.locator('#commute-advice-panel img').count(), 0);
        assert.equal(await app.page.evaluate(() => window.__injected), false);
    } finally { await app.close(); }
});

test('initialization is idempotent and hidden pages pause the fifteen-minute refresh', async browser => {
    const app = await openApp(browser, { keepCommuteAdviceOpen: true });
    try {
        const page = app.page;
        await ready(page);
        await openDetails(page);
        await page.clock.install();
        await page.evaluate(() => {
            window.AppModules.commuteAdvice.init();
            window.__adviceHidden = false;
            Object.defineProperty(document, 'hidden', { configurable: true, get: () => window.__adviceHidden });
        });
        await page.locator('#commute-advice-refresh').click();
        await ready(page);
        assert.equal(app.adviceRequests.length, 2);
        await page.evaluate(() => { window.__adviceHidden = true; document.dispatchEvent(new Event('visibilitychange')); });
        await page.clock.fastForward(16 * 60 * 1000);
        assert.equal(app.adviceRequests.length, 2, 'No automatic request while the tab is hidden');
        await page.evaluate(() => { window.__adviceHidden = false; document.dispatchEvent(new Event('visibilitychange')); });
        await ready(page);
        assert.equal(app.adviceRequests.length, 3, 'Returning to a visible tab refreshes due data once');
        assert.deepEqual(app.errors, []);
    } finally { await app.close(); }
});

async function run() {
    const browser = await chromium.launch({ headless: true,
        ...(process.env.CAPSTONE_BROWSER_EXECUTABLE ? { executablePath: process.env.CAPSTONE_BROWSER_EXECUTABLE } : {}) });
    let failures = 0;
    try {
        for (const item of tests) {
            try { await item.run(browser); console.log('PASS ' + item.name); }
            catch (error) { failures++; console.error('FAIL ' + item.name + '\n' + (error.stack || error)); }
        }
    } finally { await browser.close(); }
    console.log(`${tests.length - failures}/${tests.length} preparation-advice browser checks passed; all weather and air data were synthetic.`);
    if (failures) process.exitCode = 1;
}
if (require.main === module) run().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
module.exports = { fullAdvice, alerts };
