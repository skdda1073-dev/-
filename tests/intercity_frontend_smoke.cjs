/** Synthetic provider responses only; these tests do not verify live provider coverage. */
'use strict';
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { openApp, selectTool, noOverflow, intercityFixture, INJECTION } = require('./frontend_smoke.cjs');
const tests = [];
const test = (name, run) => tests.push({ name, run });
const byId = (page, id) => page.locator('#intercity-' + id);

async function openIntercity(app, keyboard = false) {
    if (keyboard) {
        if (!await app.page.locator('#transport-tab-subway').isVisible()) await app.page.locator('#sidebar-toggle').click();
        await app.page.locator('#transport-tab-subway').focus();
        await app.page.keyboard.press('ArrowRight');
    } else await selectTool(app.page, 'intercity');
    await byId(app.page, 'origin').waitFor({ state: 'visible' });
}
async function selectRoute(app, destination = 'TEST_DSE', search) {
    await openIntercity(app);
    await app.page.waitForFunction(() => !document.getElementById('intercity-destination').disabled);
    if (search) await byId(app.page, 'destination-filter').fill(search);
    await byId(app.page, 'destination').selectOption(destination);
}
async function waitForResults(page) { await byId(page, 'results').waitFor({ state: 'visible' }); }
function nonIntercityRequests(app) {
    assert.equal(app.requests.length, 0);
    assert.equal(app.searches.length, 0);
    assert.equal(app.taxiRequests.length, 0);
    assert.equal(app.roomRequests.length, 0);
}

for (const width of [360, 1440]) test(`${width}px lazy keyboard opening, terminal filters, today date and sorted safe results`, async browser => {
    const app = await openApp(browser, { viewport: { width, height: 900 }, timezoneId: 'America/Los_Angeles' });
    try {
        const { page } = app;
        assert.equal(app.intercityRequests.length, 0);
        await openIntercity(app, true);
        await page.waitForFunction(() => !document.getElementById('intercity-destination').disabled);
        assert.equal(app.intercityRequests.length, 3);
        assert.equal(await byId(page, 'origin').inputValue(), 'TEST_CTN');
        assert.equal(await byId(page, 'origin').locator('option').count(), 2);
        assert.equal(await byId(page, 'destination').locator('option').count(), 3);
        assert.deepEqual((await byId(page, 'destination').locator('option').allTextContents()).slice(1), ['동서울', '테스트 터미널']);
        assert.equal(await byId(page, 'destination').inputValue(), '');
        assert.equal(await byId(page, 'destination-heading').innerText(), '춘천 출발 도착지');
        assert.equal(new URLSearchParams(app.intercityRequests.at(-1).query).get('departure_id'), 'TEST_CTN');
        assert.equal(await byId(page, 'date').inputValue(), '2030-10-21');
        assert.equal(await byId(page, 'date').evaluate(el => el.readOnly), true);
        assert.equal(await byId(page, 'load').isDisabled(), true);
        await byId(page, 'destination-filter').fill('동서울');
        assert.equal(await byId(page, 'destination').locator('option').count(), 2);
        await byId(page, 'destination').selectOption('TEST_DSE');
        await byId(page, 'load').click();
        await waitForResults(page);
        const request = app.intercityRequests.at(-1);
        assert.equal(request.pathname, '/api/intercity/timetable');
        const query = new URLSearchParams(request.query);
        assert.equal(query.get('departure_id'), 'TEST_CTN');
        assert.equal(query.get('arrival_id'), 'TEST_DSE');
        assert.equal(query.get('date'), '2030-10-21');
        const rendered = await byId(page, 'trips').locator('li').allTextContents();
        assert.equal(rendered.length, 2);
        assert.match(rendered[0], /09:00/);
        assert.match(rendered[0], /미제공/);
        assert.match(rendered[1], /10:00/);
        assert.match(rendered[1], /12,300원/);
        assert.match(rendered[1], /도착 예상/);
        await noOverflow(page);
        nonIntercityRequests(app);
        await selectTool(page, 'subway');
        await selectTool(page, 'intercity');
        assert.equal(app.intercityRequests.length, 4);
        await byId(page, 'swap').click();
        await page.waitForFunction(() => !document.getElementById('intercity-destination').disabled);
        assert.equal(await byId(page, 'origin').inputValue(), 'TEST_DSE');
        assert.equal(await byId(page, 'destination').inputValue(), 'TEST_CTN');
        assert.equal(await byId(page, 'origin').locator('option').count(), 2);
        assert.equal(await byId(page, 'destination').locator('option').count(), 3);
        assert.equal(await byId(page, 'origin-filter').inputValue(), '');
        assert.equal(await byId(page, 'destination-filter').inputValue(), '');
        assert.equal(await byId(page, 'results').isHidden(), true);
        await byId(page, 'destination-filter').fill('동서울');
        assert.equal(await byId(page, 'destination').inputValue(), '');
        assert.equal(await byId(page, 'destination').locator('option').count(), 1, 'Current-origin filtering must not expose the nationwide catalogue');
        assert.equal(await byId(page, 'load').isDisabled(), true);
    } finally { await app.close(); }
});

test('nationwide origin search stays bounded and only an exact Chuncheon name is preselected', async browser => {
    const app = await openApp(browser, { intercityHandler: (route, url) => {
        const body = intercityFixture(url.pathname, url.searchParams);
        if (url.pathname.endsWith('/terminals')) {
            body.terminals[0].name = '춘천 외곽';
            body.terminals.push(...Array.from({ length: 65 }, (_, index) => ({
                id: 'TEST_SEARCH_' + index, name: '검색 후보 ' + String(index).padStart(2, '0'), city: '후보시',
            })));
        }
        return route.fulfill({ json: body });
    } });
    try {
        await openIntercity(app);
        const { page } = app;
        await page.waitForFunction(() => !document.getElementById('intercity-origin').disabled);
        assert.equal(await byId(page, 'origin').inputValue(), '');
        assert.equal(await byId(page, 'origin').locator('option').count(), 1);
        assert.equal(await byId(page, 'destination').locator('option').count(), 1);
        await byId(page, 'origin-filter').fill('후보시');
        assert.equal(await byId(page, 'origin').locator('option').count(), 31, 'At most 30 candidates plus the placeholder');
        await byId(page, 'origin').selectOption('TEST_SEARCH_0');
        await page.waitForFunction(() => !document.getElementById('intercity-destination').disabled);
        await byId(page, 'origin-filter').fill('');
        assert.equal(await byId(page, 'origin').inputValue(), 'TEST_SEARCH_0');
        assert.equal(await byId(page, 'origin').locator('option').count(), 2, 'An empty search keeps only the current selection');
        await byId(page, 'origin-filter').fill('동서울');
        assert.equal(await byId(page, 'origin').inputValue(), '', 'A nonmatching search clears the old selection');
        assert.equal(await byId(page, 'load').isDisabled(), true);
        assert.equal(app.intercityRequests.filter(item => item.pathname.endsWith('/destinations')).length, 1, 'Only choosing a new origin requests its destination list');
    } finally { await app.close(); }
});

test('all main destinations have clean selectable names and other stops require opt-in', async browser => {
    const additions = Array.from({ length: 65 }, (_, index) => ({ id: 'TEST_ADDED_' + index,
        name: '합성 도착지 ' + String(index).padStart(2, '0'), city: '합성시' }));
    const app = await openApp(browser, { viewport: { width: 360, height: 900 }, intercityHandler: (route, url) => {
        const body = intercityFixture(url.pathname, url.searchParams);
        if (url.pathname.endsWith('/terminals')) body.terminals.push(...additions);
        if (url.pathname.endsWith('/destinations')) {
            body.destinations.push(...additions,
                { id: 'TEST_SAME_A', name: '동명 합성 터미널', city: '합성남도', booking_mode: 'one_way' },
                { id: 'TEST_SAME_B', name: '동명 합성 터미널', city: '합성북도', booking_mode: 'on_site' });
            body.additional_stops = [{ id: 'TEST_STOP', name: '다른 하차지', city: '다른시' }];
        }
        return route.fulfill({ json: body });
    } });
    try {
        await openIntercity(app);
        const { page } = app;
        await page.waitForFunction(() => !document.getElementById('intercity-destination').disabled);
        assert.equal(await byId(page, 'destination').locator('option').count(), 70, 'All 69 returned entries plus the placeholder');
        assert.equal(await byId(page, 'destination').locator('option:disabled').count(), 0);
        const labels = await byId(page, 'destination').locator('option').allTextContents();
        assert.ok(labels.includes('동명 합성 터미널 · 합성남도'));
        assert.ok(labels.includes('동명 합성 터미널 · 합성북도'));
        assert.ok(labels.includes('동서울'));
        assert.doesNotMatch(labels.join(' '), /인터넷 예매|현장 구매|연결 확인|SOURCE_/);
        assert.equal(await byId(page, 'include-stops').isChecked(), false);
        assert.equal(await byId(page, 'destination').locator('option[value="TEST_STOP"]').count(), 0);
        await byId(page, 'destination').selectOption('TEST_ADDED_64');
        assert.equal(await byId(page, 'load').isEnabled(), true);
        await byId(page, 'destination-filter').fill('합성');
        assert.equal(await byId(page, 'destination').locator('option').count(), 68, 'Optional filtering also has no 30-entry cap');
        await byId(page, 'destination-filter').fill('');
        assert.equal(await byId(page, 'destination').locator('option').count(), 70);
        assert.equal(await byId(page, 'destination').inputValue(), 'TEST_ADDED_64');
        assert.equal(app.intercityRequests.length, 3);
        await byId(page, 'include-stops').check();
        assert.equal(await byId(page, 'destination').locator('option').count(), 71);
        await byId(page, 'destination').selectOption('TEST_STOP');
        await byId(page, 'load').click(); await waitForResults(page);
        assert.equal(new URLSearchParams(app.intercityRequests.at(-1).query).get('arrival_id'), 'TEST_STOP');
        await byId(page, 'include-stops').uncheck();
        assert.equal(await byId(page, 'destination').locator('option').count(), 70);
        assert.equal(await byId(page, 'destination').inputValue(), '');
        assert.equal(await byId(page, 'results').isHidden(), true);
        assert.equal(await byId(page, 'load').isDisabled(), true);
        await noOverflow(page);
    } finally { await app.close(); }
});

test('null query IDs reject the response instead of becoming disabled destination options', async browser => {
    const app = await openApp(browser, { intercityHandler: (route, url) => {
        const body = intercityFixture(url.pathname, url.searchParams);
        if (url.pathname.endsWith('/destinations')) body.destinations.push({ id: null, name: '조회 ID 없는 합성 항목', city: '합성시' });
        return route.fulfill({ json: body });
    } });
    try {
        await openIntercity(app);
        const { page } = app;
        await byId(page, 'destinations-retry').waitFor({ state: 'visible' });
        assert.match(await byId(page, 'status').innerText(), /응답 형식/);
        assert.equal(await byId(page, 'destination').locator('option').count(), 1);
        assert.equal(await byId(page, 'load').isDisabled(), true);
    } finally { await app.close(); }
});

test('destination failure retries only that list and never substitutes nationwide terminals', async browser => {
    let failed = true;
    const app = await openApp(browser, { intercityHandler: (route, url) => url.pathname.endsWith('/destinations') && failed
        ? route.fulfill({ status: 502, json: { status: 'error', error: '등록 도착지를 불러오지 못했습니다.' } })
        : route.fulfill({ json: intercityFixture(url.pathname, url.searchParams) }) });
    try {
        await openIntercity(app);
        const { page } = app;
        await byId(page, 'destinations-retry').waitFor({ state: 'visible' });
        assert.equal(await byId(page, 'destination').locator('option').count(), 1);
        assert.equal(await byId(page, 'destination').isDisabled(), true);
        assert.equal(await byId(page, 'origin').isEnabled(), true);
        assert.equal(await byId(page, 'retry').isHidden(), true);
        failed = false; await byId(page, 'destinations-retry').click();
        await page.waitForFunction(() => !document.getElementById('intercity-destination').disabled);
        assert.equal(await byId(page, 'destination').locator('option').count(), 3);
        assert.equal(app.intercityRequests.filter(item => item.pathname.endsWith('/config')).length, 1);
        assert.equal(app.intercityRequests.filter(item => item.pathname.endsWith('/terminals')).length, 1);
        assert.equal(app.intercityRequests.filter(item => item.pathname.endsWith('/destinations')).length, 2);
    } finally { await app.close(); }
});

test('changing origin rejects a late destination response and swap does not assume a reverse route', async browser => {
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    const app = await openApp(browser, { intercityHandler: async (route, url) => {
        const body = intercityFixture(url.pathname, url.searchParams);
        if (url.pathname.endsWith('/destinations')) {
            if (url.searchParams.get('departure_id') === 'TEST_CTN') await gate;
            if (url.searchParams.get('departure_id') === 'TEST_OTHER') body.destinations = body.destinations.filter(item => item.id !== 'TEST_DSE');
        }
        await route.fulfill({ json: body }).catch(() => {});
    } });
    try {
        await openIntercity(app);
        const { page } = app;
        await page.waitForFunction(() => !document.getElementById('intercity-origin').disabled);
        await byId(page, 'origin-filter').fill('동서울');
        await byId(page, 'origin').selectOption('TEST_DSE');
        await page.waitForFunction(() => !document.getElementById('intercity-destination').disabled);
        release(); await page.waitForTimeout(100);
        assert.equal(await byId(page, 'destination-heading').innerText(), '동서울 출발 도착지');
        assert.equal(await byId(page, 'destination').locator('option[value="TEST_DSE"]').count(), 0);
        await byId(page, 'destination').selectOption('TEST_OTHER');
        await byId(page, 'swap').click();
        await page.waitForFunction(() => !document.getElementById('intercity-destination').disabled);
        assert.equal(await byId(page, 'origin').inputValue(), 'TEST_OTHER');
        assert.equal(await byId(page, 'destination').inputValue(), '');
        assert.equal(await byId(page, 'destination').locator('option[value="TEST_DSE"]').count(), 0);
        assert.match(await byId(page, 'status').innerText(), /이전 출발지가 없어 자동 선택하지 않았어요/);
        assert.equal(await byId(page, 'load').isDisabled(), true);
    } finally { release(); await app.close(); }
});

test('empty response is not described as no bus service and keeps booking links', async browser => {
    const app = await openApp(browser, { intercityHandler: (route, url) => route.fulfill({ json: url.pathname.endsWith('/timetable')
        ? { ...intercityFixture(url.pathname, url.searchParams), status: 'empty', trips: [] }
        : intercityFixture(url.pathname, url.searchParams) }) });
    try {
        await selectRoute(app); await byId(app.page, 'load').click();
        await byId(app.page, 'status').filter({ hasText: '조회 결과가 없어요' }).waitFor();
        assert.match(await byId(app.page, 'status').innerText(), /데이터 미제공/);
        assert.equal(await byId(app.page, 'results').isHidden(), true);
        assert.equal(await app.page.locator('.intercity-booking-links a').count(), 2);
    } finally { await app.close(); }
});

test('missing configuration avoids terminal call and allows successful retry', async browser => {
    let configured = false;
    const app = await openApp(browser, { intercityHandler: (route, url) => route.fulfill({ json: url.pathname.endsWith('/config')
        ? { ...intercityFixture(url.pathname), configured }
        : intercityFixture(url.pathname, url.searchParams) }) });
    try {
        await openIntercity(app);
        await byId(app.page, 'retry').waitFor({ state: 'visible' });
        assert.equal(app.intercityRequests.length, 1);
        assert.equal(await byId(app.page, 'load').isDisabled(), true);
        assert.match(await byId(app.page, 'status').innerText(), /공식 예매처/);
        configured = true;
        await byId(app.page, 'retry').click();
        await app.page.waitForFunction(() => !document.getElementById('intercity-destination').disabled);
        assert.equal(app.intercityRequests.length, 4);
        assert.equal(await byId(app.page, 'retry').isHidden(), true);
    } finally { await app.close(); }
});

test('provider auth failure is visible and never becomes an empty timetable', async browser => {
    const app = await openApp(browser, { intercityHandler: (route, url) => url.pathname.endsWith('/timetable')
        ? route.fulfill({ status: 502, json: { status: 'error', code: 'INTERCITY_AUTH', error: '시간표 제공 서비스의 연결 권한을 확인해 주세요.' } })
        : route.fulfill({ json: intercityFixture(url.pathname, url.searchParams) }) });
    try {
        await selectRoute(app); await byId(app.page, 'load').click();
        await byId(app.page, 'status').filter({ hasText: '연결 권한' }).waitFor();
        assert.equal(await byId(app.page, 'status').getAttribute('data-state'), 'error');
        assert.equal(await byId(app.page, 'load').isEnabled(), true);
        assert.equal(await byId(app.page, 'results').isHidden(), true);
    } finally { await app.close(); }
});

test('partial result and provider strings render as text, with no invented amounts', async browser => {
    const app = await openApp(browser, { intercityHandler: (route, url) => {
        const body = intercityFixture(url.pathname, url.searchParams);
        if (url.pathname.endsWith('/destinations')) body.destinations.find(item => item.id === 'TEST_OTHER').name = INJECTION;
        if (url.pathname.endsWith('/timetable')) { body.status = 'partial'; body.warnings = [INJECTION]; body.trips[0].grade = INJECTION; }
        return route.fulfill({ json: body });
    } });
    try {
        await selectRoute(app, 'TEST_OTHER', INJECTION); await byId(app.page, 'load').click(); await waitForResults(app.page);
        assert.match(await byId(app.page, 'warnings').innerText(), /일부 운행정보/);
        assert.ok((await byId(app.page, 'warnings').innerText()).includes(INJECTION));
        assert.equal(await byId(app.page, 'trips').locator('img').count(), 0);
        assert.equal(await app.page.evaluate(() => window.__injected), false);
        assert.equal(await byId(app.page, 'status').getAttribute('data-state'), 'partial');
    } finally { await app.close(); }
});

test('changing destination invalidates a slow request and only the newer route remains', async browser => {
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    let calls = 0;
    const app = await openApp(browser, { intercityHandler: async (route, url) => {
        const body = intercityFixture(url.pathname, url.searchParams);
        if (url.pathname.endsWith('/timetable') && ++calls === 1) {
            await gate;
            body.trips[0].grade = 'OLD_RESPONSE_MUST_NOT_RENDER';
        }
        await route.fulfill({ json: body }).catch(() => {});
    } });
    try {
        await selectRoute(app); await byId(app.page, 'load').click();
        await app.page.waitForFunction(() => document.getElementById('intercity-timetable').getAttribute('aria-busy') === 'true');
        await byId(app.page, 'destination-filter').fill('테스트 터미널');
        assert.equal(await byId(app.page, 'destination').inputValue(), '');
        await byId(app.page, 'destination').selectOption('TEST_OTHER');
        await byId(app.page, 'load').click(); await waitForResults(app.page);
        release();
        await app.page.waitForTimeout(100);
        assert.equal(calls, 2);
        assert.match(await byId(app.page, 'results-heading').innerText(), /테스트 터미널/);
        assert.doesNotMatch(await byId(app.page, 'trips').innerText(), /OLD_RESPONSE/);
    } finally { release(); await app.close(); }
});

test('a stale date from midnight is replaced using the server date and requires another query', async browser => {
    const app = await openApp(browser, { intercityHandler: (route, url) => url.pathname.endsWith('/timetable')
        ? route.fulfill({ status: 400, json: { status: 'error', code: 'INTERCITY_DATE', error: '오늘만 조회할 수 있어요.', today: '2030-10-22' } })
        : route.fulfill({ json: intercityFixture(url.pathname, url.searchParams) }) });
    try {
        await selectRoute(app); await byId(app.page, 'load').click();
        await byId(app.page, 'status').filter({ hasText: '날짜가 바뀌었어요' }).waitFor();
        assert.equal(await byId(app.page, 'date').inputValue(), '2030-10-22');
        assert.equal(await byId(app.page, 'results').isHidden(), true);
        assert.equal(await byId(app.page, 'load').isEnabled(), true);
    } finally { await app.close(); }
});

async function run() {
    const browser = await chromium.launch({ headless: true,
        ...(process.env.CAPSTONE_BROWSER_EXECUTABLE ? { executablePath: process.env.CAPSTONE_BROWSER_EXECUTABLE } : {}) });
    let failed = 0;
    try {
        for (const item of tests) {
            try { await item.run(browser); console.log('PASS ' + item.name); }
            catch (error) { failed += 1; console.error('FAIL ' + item.name + '\n' + error.stack); }
        }
    } finally { await browser.close(); }
    console.log(`${tests.length - failed}/${tests.length} intercity browser checks passed (mocked API responses).`);
    process.exitCode = failed ? 1 : 0;
}
if (require.main === module) run().catch(error => { console.error(error); process.exitCode = 1; });
