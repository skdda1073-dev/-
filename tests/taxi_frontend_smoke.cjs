/** Ordinary taxi-tab browser checks, with GPS/providers mocked by openApp.
 * A runnable browser and an approved local UI server are required.
 * These checks do not establish real TMAP data quality or a user's permissions.
 */
'use strict';
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { openApp, selectTool, activateRealMobility, chooseBuilding, selectDestination, clearDestination,
    noOverflow, taxiFixture } = require('./frontend_smoke.cjs');
const tests = [];
const test = (name, run) => tests.push({ name, run });
const visibleText = (page, selector, text) => page.locator(selector).filter({ hasText: text }).waitFor({ state: 'visible' });
async function prepare(page) {
    await chooseBuilding(page); await selectDestination(page);
    await selectTool(page, 'taxi');
}

for (const width of [360, 390, 1440]) test(`${width}px ordinary taxi tab queries directly and preserves the demo`, async browser => {
    const app = await openApp(browser, { viewport: { width, height: 900 } });
    try {
        const page = app.page;
        assert.equal(app.taxiRequests.length, 0); assert.equal(app.requests.length, 0);
        await selectTool(page, 'taxi');
        assert.equal(await page.locator('#taxi-section').isVisible(), true);
        assert.equal(await page.locator('#taxi-room-actions').isVisible(), false);
        assert.equal(await page.locator('#demo-party-form').isVisible(), true);
        assert.equal(await page.locator('#ride-room-section').count(), 0);
        assert.equal(await page.locator('script[src$="/taxi-controller.js"]').count(), 1);
        assert.equal(app.taxiRequests.length, 0); assert.equal(app.roomRequests.length, 0);
        await chooseBuilding(page); await selectDestination(page);
        await selectTool(page, 'taxi');
        await page.locator('#taxi-refresh').click();
        await visibleText(page, '#taxi-content', '예상요금 약 10,001원');
        assert.equal(app.taxiRequests.length, 1); assert.equal(app.requests.length, 0);
        assert.equal(app.taxiRequests[0].lat, undefined);
        assert.equal(app.roomRequests.length, 0);
        assert.deepEqual(await page.evaluate(() => window.__testGPSCalls), []);
        assert.equal(await page.locator('#taxi-content .taxi-splits li').count(), 3);
        assert.match(await page.locator('#taxi-content .taxi-splits').innerText(), /5,001원.*5,000원/);
        assert.match(await page.locator('#taxi-content .taxi-splits').innerText(), /3,334원.*3,334원.*3,333원/);
        assert.equal(await page.locator('#taxi-refresh').isEnabled(), true);
        await noOverflow(page);
    } finally { await app.close(); }
});

test('missing destination never asks for GPS; denied GPS permits a building retry', async browser => {
    const app = await openApp(browser, { denyGPS: true });
    try {
        const page = app.page; await selectTool(page, 'taxi');
        await page.locator('#taxi-refresh').click();
        assert.deepEqual(await page.evaluate(() => window.__testGPSCalls), []);
        assert.equal(app.taxiRequests.length, 0);
        await selectDestination(page); await page.locator('#origin-gps').click();
        await selectTool(page, 'taxi');
        await page.locator('#taxi-refresh').click();
        await selectTool(page, 'journey');
        await visibleText(page, '#origin-status', '위치 권한');
        assert.equal(app.taxiRequests.length, 0);
        await selectTool(page, 'taxi');
        assert.equal(await page.locator('#taxi-refresh').isEnabled(), true);
        await chooseBuilding(page); await selectTool(page, 'taxi'); await page.locator('#taxi-refresh').click();
        await visibleText(page, '#taxi-content', '예상요금 약 10,001원');
        assert.equal(app.taxiRequests.length, 1); assert.equal(app.requests.length, 0);
    } finally { await app.close(); }
});

test('a destination change leaves the card usable and ignores an uncancellable old quote', async browser => {
    let release, began;
    const gate = new Promise(resolve => { release = resolve; });
    const started = new Promise(resolve => { began = resolve; });
    const app = await openApp(browser, { taxiHandler: async (route, payload, count) => {
        if (count === 1) { began(); await gate; }
        return route.fulfill({ json: taxiFixture(payload, { amount: count === 1 ? 12345 : 20001 }) }).catch(() => {});
    } });
    try {
        const page = app.page;
        await page.evaluate(() => {
            const original = window.fetch.bind(window);
            window.fetch = (url, options) => String(url).includes('/api/taxi/estimate')
                ? original(url, { ...options, signal: undefined }) : original(url, options);
        });
        await prepare(page); await page.locator('#taxi-refresh').click(); await started;
        await clearDestination(page); await selectDestination(page);
        const returned = page.waitForResponse(response => new URL(response.url()).pathname === '/api/taxi/estimate');
        release(); await returned;
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        await selectTool(page, 'taxi');
        assert.equal(await page.locator('#taxi-section').isVisible(), true);
        assert.doesNotMatch(await page.locator('#taxi-content').innerText(), /12,345원/);
        await page.locator('#taxi-refresh').click(); await visibleText(page, '#taxi-content', '20,001원');
        assert.equal(app.requests.length, 0);
    } finally { release(); await app.close(); }
});

test('partial, expired and retried quotes never fabricate a zero fare', async browser => {
    const app = await openApp(browser, { taxiHandler: (route, payload, count) => route.fulfill({
        json: taxiFixture(payload, { amount: count === 1 ? null : 10001, ttl: 1 }),
    }) });
    try {
        const page = app.page; await prepare(page); await page.locator('#taxi-refresh').click();
        await visibleText(page, '#taxi-content', '요금 조회 불가');
        assert.match(await page.locator('#taxi-content').innerText(), /택시 이동 약 10분/);
        assert.doesNotMatch(await page.locator('#taxi-content').innerText(), /0원|NaN|undefined/);
        await visibleText(page, '#taxi-content', '견적이 만료됐어요');
        assert.equal(app.taxiRequests.length, 1);
        await page.locator('#taxi-refresh').click(); await visibleText(page, '#taxi-content', '10,001원');
        assert.equal(app.taxiRequests.length, 2); assert.equal(app.requests.length, 0);
    } finally { await app.close(); }
});

test('room test activation preserves the existing taxi card and never duplicates scripts or IDs', async browser => {
    const app = await openApp(browser);
    try {
        const page = app.page; await prepare(page); await page.locator('#taxi-refresh').click();
        await visibleText(page, '#taxi-content', '10,001원');
        await activateRealMobility(page); await activateRealMobility(page);
        await visibleText(page, '#taxi-content', '10,001원');
        assert.equal(await page.locator('#taxi-section').count(), 1);
        assert.equal(await page.locator('#ride-room-section').count(), 1);
        assert.equal(await page.locator('script[src$="/taxi-controller.js"]').count(), 1);
        assert.equal(await page.locator('script[src$="/ride-room-controller.js"]').count(), 1);
        assert.equal(await page.locator('#taxi-room-actions').isVisible(), true);
        assert.equal(app.taxiRequests.length, 1);
        assert.equal(await page.evaluate(() => {
            const ids = [...document.querySelectorAll('[id]')].map(node => node.id);
            return ids.length === new Set(ids).size;
        }), true);
        await noOverflow(page);
    } finally { await app.close(); }
});

(async () => {
    const browser = await chromium.launch({ headless: true,
        ...(process.env.CAPSTONE_BROWSER_EXECUTABLE ? { executablePath: process.env.CAPSTONE_BROWSER_EXECUTABLE } : {}) });
    let failed = 0;
    try {
        for (const item of tests) {
            try { await item.run(browser); console.log('PASS ' + item.name); }
            catch (error) { failed++; console.error('FAIL ' + item.name + '\n' + (error.stack || error)); }
        }
    } finally { await browser.close(); }
    console.log(`\n${tests.length - failed}/${tests.length} taxi browser checks passed; provider/GPS responses were mocked.`);
    if (failed) process.exitCode = 1;
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
