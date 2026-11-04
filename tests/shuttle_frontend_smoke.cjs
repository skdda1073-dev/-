/** --unit uses Node built-ins only: no Flask import, .env, browser or network.
 * Browser mode requires a separately approved local UI server and Playwright.
 * All timetable responses below are synthetic fixtures with a fixed clock.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const FIXED = '2026-10-19T07:50:00+09:00';
const tests = [];
const test = (name, run) => tests.push({ name, run });
const unit = process.argv.includes('--unit');
const project = path.resolve(__dirname, '..');

// Extract only the self-contained synthetic fixture; never load the app or
// the browser helper's dependencies in unit mode.
function fixture(params) {
    const source = fs.readFileSync(path.join(__dirname, 'frontend_smoke.cjs'), 'utf8');
    const start = source.indexOf('function shuttleFixture(');
    const end = source.indexOf('\nasync function openApp(', start);
    assert.ok(start >= 0 && end > start);
    return JSON.parse(JSON.stringify(vm.runInNewContext(source.slice(start, end) + '\nshuttleFixture(params)', { params })));
}

async function memoryUI(handler = params => fixture(params)) {
    class Element {
        constructor(tag = 'div') {
            this.tagName = tag; this.children = []; this.listeners = new Map(); this.dataset = {};
            this.attributes = {}; this._value = ''; this.textContent = ''; this.disabled = false; this.hidden = false;
        }
        get value() { return this._value; }
        set value(value) { this._value = value; }
        replaceChildren(...items) {
            this.children = items;
            if (this.tagName === 'select') this.value = items[0]?.value || '';
        }
        append(...items) { this.children.push(...items); }
        addEventListener(type, callback) { this.listeners.set(type, [...(this.listeners.get(type) || []), callback]); }
        setAttribute(key, value) { this.attributes[key] = value; }
        checkValidity() { return !!this.value; }
        async emit(type) {
            for (const callback of this.listeners.get(type) || []) await callback({ preventDefault() {} });
            await flush();
        }
    }
    const ids = ['route', 'direction', 'at', 'board', 'alight', 'query', 'refresh', 'status', 'source', 'warnings',
        'trips', 'candidates', 'segment-status', 'segment-warnings', 'segment-form'];
    const elements = Object.fromEntries(ids.map(id => [id, new Element(['route', 'direction', 'board', 'alight'].includes(id) ? 'select' : 'div')]));
    const root = new Element(), panel = new Element(); panel.hidden = true;
    root.querySelector = selector => elements[selector.replace('#shuttle-', '')];
    const requests = []; let observe;
    class FixedDate extends Date { constructor(...args) { super(...(args.length ? args : [FIXED])); } static now() { return new Date(FIXED).getTime(); } }
    const context = {
        document: { getElementById: id => id === 'shuttle-timetable' ? root : id === 'transport-panel-shuttle' ? panel : null,
            createElement: tag => new Element(tag) },
        MutationObserver: class { constructor(callback) { observe = callback; } observe() {} },
        Intl, Date: FixedDate, URLSearchParams, AbortController,
        fetch: async (url, init) => {
            const params = new URL(url, 'http://memory.invalid').searchParams;
            requests.push(Object.fromEntries(params));
            const result = await handler(params, requests.length, init.signal);
            return { ok: result?.httpStatus ? false : true, status: result?.httpStatus || 200,
                json: async () => result?.body === undefined ? result : result.body };
        },
    };
    vm.runInNewContext(fs.readFileSync(path.join(project, 'static/js/shuttle-timetable.js'), 'utf8'), context);
    return { elements, requests, root, text: name => allText(elements[name]),
        async open() { panel.hidden = false; observe(); await flush(); },
        async reopen() { panel.hidden = true; observe(); panel.hidden = false; observe(); await flush(); },
        async change(name, value) { elements[name].value = value; await elements[name].emit('change'); },
        async submit() { await elements['segment-form'].emit('submit'); },
        async date(value) { elements.at.value = value; await elements.at.emit('change'); },
    };
}
function allText(element) { return [element.textContent, ...element.children.map(allText)].join('\n'); }
async function flush() { for (let i = 0; i < 12; i++) await Promise.resolve(); }

if (unit) {
    test('lazy initialization, Korean clock, preserved full timetable and one request per action', async () => {
        const app = await memoryUI();
        assert.equal(app.requests.length, 0);
        await app.open();
        assert.equal(app.requests.length, 1);
        assert.equal(app.requests[0].at, FIXED);
        assert.equal(app.elements.route.value, '제1노선');
        assert.equal(app.elements.direction.value, '등교');
        assert.equal(app.elements.board.value, 'r1-a');
        assert.equal(app.elements.alight.value, 'r1-b');
        assert.equal(app.elements.trips.children.length, 2);
        assert.match(app.text('trips'), /예정 출발 09:15/);
        assert.match(app.text('trips'), /시간표 시각 미확인/);
        await app.submit();
        assert.equal(app.requests.length, 2);
        assert.equal(app.requests[1].board, 'r1-a');
        assert.equal(app.elements.candidates.children.length, 2);
        assert.match(app.text('candidates'), /승차 소요시간: 미확인/);
        assert.match(app.text('source'), /프로필 확인일: 2026-10-18/);
        const full = app.text('trips');
        await app.reopen(); await app.reopen();
        assert.equal(app.requests.length, 2);
        assert.equal(app.text('trips'), full);
        for (const element of Object.values(app.elements)) {
            for (const callbacks of element.listeners.values()) assert.equal(callbacks.length, 1);
        }
        await app.date('2026-10-19T23:59'); await app.submit();
        assert.equal(app.elements.candidates.children.length, 0);
        assert.equal(app.text('trips'), full);
        assert.match(app.text('segment-status'), /남은 시간표 후보가 없습니다/);
    });
    for (const [date, status] of [
        ['2026-10-19T07:50', 'ok'], ['2026-10-17T07:50', 'not_operating'],
        ['2026-10-18T07:50', 'not_operating'], ['2026-10-09T07:50', 'not_operating'],
        ['2026-07-01T07:50', 'not_operating'], ['2027-01-04T07:50', 'unknown'],
    ]) test(`calendar presentation at fixed ${date}`, async () => {
        const app = await memoryUI(); await app.open(); await app.date(date); await app.submit();
        assert.equal(app.elements.trips.children.length, 2);
        if (status === 'not_operating') {
            assert.equal(app.elements.candidates.children.length, 0);
            assert.match(app.text('segment-status'), /미운행 조건/);
        } else if (status === 'unknown') {
            assert.match(app.text('segment-warnings'), /운행 조건 미확인/);
            assert.match(app.text('candidates'), /운행 조건 미확인/);
        } else assert.equal(app.elements.candidates.children.length, 2);
    });
    test('Friday exclusion is marked in full schedule and excluded from candidates', async () => {
        const app = await memoryUI(); await app.open(); await app.date('2026-10-23T07:50'); await app.submit();
        assert.equal(app.elements.trips.children.length, 2);
        assert.match(allText(app.elements.trips.children[0]), /미운행 조건/);
        assert.equal(app.elements.candidates.children.length, 1);
        assert.equal(app.elements.candidates.children[0].dataset.tripId, 't1-later');
    });
    test('direction switch, duplicate names with distinct IDs, reverse and disconnected trips', async () => {
        const app = await memoryUI(); await app.open();
        await app.change('direction', '하교');
        assert.match(app.text('trips'), /17:30/);
        await app.change('board', 'r1-d'); await app.change('alight', 'r1-c'); await app.submit();
        assert.equal(app.elements.candidates.children.length, 0);
        assert.match(app.text('segment-status'), /연결되는 구간이 없습니다/);
        await app.change('route', '제2노선');
        const duplicate = app.elements.board.children.filter(item => item.textContent.startsWith('동명 정류장'));
        assert.deepEqual(duplicate.map(item => item.value), ['r2-b', 'r2-c']);
        duplicate.forEach(item => assert.match(item.textContent, /ID r2-/));
        await app.change('board', 'r2-a'); await app.change('alight', 'r2-d'); await app.submit();
        assert.match(app.text('segment-status'), /연결되는 구간이 없습니다/);
        await app.change('alight', 'r2-b'); await app.submit();
        assert.equal(app.elements.candidates.children.length, 1);
        assert.match(app.text('candidates'), /타는 정류장 시간표 시각/);
        assert.doesNotMatch(app.text('candidates'), /예정 출발/);
    });
    test('malformed response, HTTP error, network error and explicit retry', async () => {
        for (const failure of [() => ({ bad: true }), () => ({ httpStatus: 502 }), () => { throw new Error('Synthetic network failure'); }]) {
            const app = await memoryUI((params, count) => count === 1 ? failure() : fixture(params));
            await app.open();
            assert.equal(app.elements.status.dataset.state, 'error');
            assert.equal(app.elements.query.disabled, true);
            await app.elements.refresh.emit('click');
            assert.equal(app.elements.status.dataset.state, 'ok');
            assert.equal(app.elements.query.disabled, false);
        }
    });
    test('segment failure keeps full timetable; profile fallback preserves warnings', async () => {
        const app = await memoryUI(params => {
            if (params.has('board')) throw new Error('Synthetic segment failure');
            const result = fixture(params);
            result.profile_status = { status: 'error', code: 'SHUTTLE_DATA' };
            result.calendar_status = 'unknown';
            result.source.official_profile = undefined;
            result.warnings = ['원본 시간표만 조회했습니다.'];
            return result;
        });
        await app.open(); const full = app.text('trips'); await app.submit();
        assert.equal(app.text('trips'), full);
        assert.equal(app.elements['segment-status'].dataset.state, 'error');
        assert.match(app.text('warnings'), /공식 보강정보를 적용하지 못했습니다/);
        assert.match(app.text('source'), /프로필 확인일 미확인/);
    });
    test('late full and segment responses cannot overwrite a newer selection', async () => {
        let releaseFull, releaseSegment;
        const app = await memoryUI((params, count) => {
            if (count === 2) return new Promise(resolve => { releaseFull = () => resolve(fixture(params)); });
            if (params.has('board')) return new Promise(resolve => { releaseSegment = () => resolve(fixture(params)); });
            return fixture(params);
        });
        await app.open();
        const oldFull = app.change('direction', '하교'); // intentionally ignore abort in the mock
        await flush();
        await app.change('route', '제2노선');
        const current = app.text('trips'); releaseFull(); await oldFull;
        assert.equal(app.text('trips'), current);
        await app.change('board', 'r2-a'); await app.change('alight', 'r2-b');
        const pending = app.submit(); await flush();
        assert.equal(app.elements.query.disabled, true);
        const count = app.requests.length;
        await app.submit();
        assert.equal(app.requests.length, count, 'Repeated submit while loading does not duplicate a request');
        await app.change('alight', 'r2-d'); releaseSegment(); await pending;
        assert.equal(app.elements.candidates.children.length, 0);
        assert.match(app.text('segment-status'), /선택한 뒤/);
    });
} else {
    const { openApp, noOverflow } = require('./frontend_smoke.cjs');
    for (const width of [360, 390, 1440]) test(`${width}px shuttle tab, full schedule, candidates and independent campus state`, async browser => {
        const app = await openApp(browser, { viewport: { width, height: 900 }, fixedNow: FIXED, timezoneId: 'America/Los_Angeles' });
        try {
            const page = app.page;
            assert.equal(app.shuttleRequests.length, 0);
            await page.locator('#tab-knu').click();
            const state = await page.evaluate(() => ({ campus: selectedCampus, mode: originMode, destination: AppState.getDestination() }));
            await page.locator('#transport-tab-shuttle').click();
            await page.waitForFunction(() => !document.getElementById('shuttle-query').disabled);
            assert.match(await page.locator('#transport-panel-shuttle').innerText(), /한림대학교 자료 · 저장된 시간표 기반/);
            assert.equal(await page.locator('#shuttle-at').inputValue(), '2026-10-19T07:50');
            assert.deepEqual(await page.evaluate(() => ({ campus: selectedCampus, mode: originMode, destination: AppState.getDestination() })), state);
            await page.locator('#shuttle-query').click();
            await page.waitForFunction(() => document.querySelectorAll('.shuttle-candidate').length === 2);
            assert.equal(app.shuttleRequests.length, 2);
            assert.equal(await page.locator('.shuttle-trip').count(), 2);
            assert.match(await page.locator('#shuttle-candidates').innerText(), /승차 소요시간: 미확인/);
            await noOverflow(page);
            await page.locator('#shuttle-route').selectOption('제2노선');
            await page.waitForFunction(() => document.querySelector('#shuttle-board option[value="r2-b"]'));
            assert.equal(await page.locator('#shuttle-board option').filter({ hasText: '동명 정류장' }).count(), 2);
            const before = app.shuttleRequests.length;
            await page.locator('#transport-tab-subway').click();
            await page.locator('#transport-tab-shuttle').click();
            assert.equal(app.shuttleRequests.length, before);
            await noOverflow(page);
        } finally { await app.close(); }
    });
}

(async () => {
    let browser, failures = 0;
    if (!unit) {
        const { chromium } = require('playwright');
        browser = await chromium.launch({ headless: true,
            ...(process.env.CAPSTONE_BROWSER_EXECUTABLE ? { executablePath: process.env.CAPSTONE_BROWSER_EXECUTABLE } : {}) });
    }
    try {
        for (const item of tests) {
            let timeout;
            try {
                await Promise.race([item.run(browser), new Promise((_, reject) => {
                    timeout = setTimeout(() => reject(new Error('Test did not finish within 30 seconds')), 30000);
                })]);
                console.log('PASS ' + item.name);
            }
            catch (error) { failures++; console.error('FAIL ' + item.name + '\n' + error.stack); }
            finally { clearTimeout(timeout); }
        }
    } finally { if (browser) await browser.close(); }
    console.log(`${tests.length - failures}/${tests.length} ${unit ? 'memory DOM' : 'browser'} checks passed`);
    if (failures) process.exitCode = 1;
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
