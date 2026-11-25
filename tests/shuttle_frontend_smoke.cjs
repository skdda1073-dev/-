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

async function memoryUI(handler = params => fixture(params), { shell } = {}) {
    let focused;
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
        getClientRects() { return this.hidden || this.layoutHidden ? [] : [{}]; }
        focus() { focused = this; this.focusCount = (this.focusCount || 0) + 1; }
        scrollIntoView() { this.scrollCount = (this.scrollCount || 0) + 1; }
        async emit(type) {
            for (const callback of this.listeners.get(type) || []) await callback({ preventDefault() {} });
            await flush();
        }
    }
    const ids = ['route', 'direction', 'at', 'board', 'alight', 'query', 'refresh', 'now', 'status', 'source', 'warnings',
        'trips', 'candidates', 'candidates-heading', 'next', 'next-content', 'later', 'segment-status', 'segment-warnings',
        'segment-form', 'results', 'context', 'segment-details'];
    const elements = Object.fromEntries(ids.map(id => [id, new Element(['route', 'direction', 'board', 'alight'].includes(id) ? 'select' : 'div')]));
    const root = new Element(), panel = new Element(); panel.hidden = true;
    elements.next.hidden = elements.later.hidden = elements.results.hidden = true;
    root.querySelector = selector => elements[selector.replace('#shuttle-', '')];
    const requests = []; let observe;
    class FixedDate extends Date { constructor(...args) { super(...(args.length ? args : [FIXED])); } static now() { return new Date(FIXED).getTime(); } }
    const context = {
        window: { AppModules: shell ? { shell } : {} },
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
    return { elements, requests, root, panel, get focused() { return focused; }, text: name => allText(elements[name]),
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
        assert.equal(app.elements.next.hidden, true);
        assert.equal(app.elements.later.hidden, true);
        assert.equal(app.elements.results.hidden, true);
        assert.equal(app.focused, undefined, 'Background full schedule loading never moves focus');
        assert.match(app.text('trips'), /09:15\s+춘천역\s+예정 출발/);
        assert.match(app.text('trips'), /미확인\s+대학교\s+시간표 시각/);
        await app.submit();
        assert.equal(app.requests.length, 2);
        assert.equal(app.requests[1].board, 'r1-a');
        assert.equal(app.elements.next.hidden, false);
        assert.equal(app.elements.results.hidden, false);
        assert.equal(app.elements['next-content'].children[0].dataset.tripId, 't1');
        assert.match(app.text('next-content'), /타는 정류장\s+춘천역\s+09:15\s+예정 출발/);
        assert.match(app.text('next-content'), /내리는 정류장\s+대학교\s+미확인\s+도착 시각/);
        assert.match(app.text('context'), /2026.*10.*19.*07:50 이후 · 제1노선 · 등교/);
        assert.match(app.text('segment-warnings'), /정류장까지 걷는 시간과 승차 여유 시간/);
        assert.match(app.text('next-content'), /승강장 위치는 학교 안내/);
        assert.equal(app.elements.candidates.children.length, 1);
        assert.equal(app.elements.candidates.children[0].dataset.tripId, 't1-later');
        assert.match(app.text('candidates'), /09:20/);
        assert.equal(app.focused, app.elements['candidates-heading']);
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
        assert.equal(app.elements.next.hidden, true);
        assert.equal(app.elements.later.hidden, true);
        assert.equal(app.text('trips'), full);
        assert.match(app.text('segment-status'), /남은 시간표가 없습니다/);
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
            assert.equal(app.elements.next.hidden, true);
            assert.match(app.text('segment-status'), /미운행일/);
        } else if (status === 'unknown') {
            assert.match(app.text('warnings'), /운행 조건 미확인/);
            assert.match(app.text('next-content'), /운행 조건 미확인/);
        } else assert.equal(app.elements.candidates.children.length, 1);
    });
    test('Friday exclusion is marked in full schedule and excluded from candidates', async () => {
        const app = await memoryUI(); await app.open(); await app.date('2026-10-23T07:50'); await app.submit();
        assert.equal(app.elements.trips.children.length, 2);
        assert.match(allText(app.elements.trips.children[0]), /미운행 조건/);
        assert.equal(app.elements.candidates.children.length, 0);
        assert.equal(app.elements['next-content'].children[0].dataset.tripId, 't1-later');
        assert.equal(app.elements.later.hidden, true);
    });
    test('direction switch, duplicate names with distinct IDs, reverse and disconnected trips', async () => {
        const app = await memoryUI(); await app.open();
        await app.change('direction', '하교');
        assert.match(app.text('trips'), /17:30/);
        await app.change('board', 'r1-d'); await app.change('alight', 'r1-c'); await app.submit();
        assert.equal(app.elements.candidates.children.length, 0);
        assert.match(app.text('segment-status'), /이 순서로 두 정류장에 들르는 편이 없습니다/);
        await app.change('route', '제2노선');
        const duplicate = app.elements.board.children.filter(item => item.textContent.startsWith('동명 정류장'));
        assert.deepEqual(duplicate.map(item => item.value), ['r2-b', 'r2-c']);
        duplicate.forEach(item => assert.match(item.textContent, /ID r2-/));
        await app.change('board', 'r2-a'); await app.change('alight', 'r2-d'); await app.submit();
        assert.match(app.text('segment-status'), /이 순서로 두 정류장에 들르는 편이 없습니다/);
        await app.change('alight', 'r2-b'); await app.submit();
        assert.equal(app.elements.candidates.children.length, 0);
        assert.equal(app.elements.next.hidden, false);
        assert.match(app.text('next-content'), /정류장 시간표 시각/);
        assert.doesNotMatch(app.text('next-content'), /예정 출발/);
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
    test('verified service conditions never turn an unspecified stop time into a departure', async () => {
        const app = await memoryUI(params => {
            const data = fixture(params);
            data.timetable_candidates.forEach(item => {
                item.scheduled_departure_at = null;
                item.scheduled_alight_stop_at = '2026-10-19T09:40:00+09:00';
                item.service_date_confirmed = true;
            });
            return data;
        });
        await app.open(); await app.submit();
        assert.match(app.text('next-content'), /타는 정류장\s+춘천역\s+09:15\s+정류장 시간표 시각/);
        assert.match(app.text('next-content'), /내리는 정류장\s+대학교\s+09:40\s+정류장 시간표 시각/);
        assert.doesNotMatch(app.text('next-content'), /예정 출발|예정 도착/);
        assert.match(app.text('candidates'), /09:40\s+내리는 정류장 시간표/);
    });
    test('an arrival on the following day shows its own date alongside the time', async () => {
        const app = await memoryUI(params => {
            const data = fixture(params);
            if (data.timetable_candidates.length) {
                data.timetable_candidates[0].scheduled_arrival_at = '2026-10-20T00:10:00+09:00';
            }
            return data;
        });
        await app.open(); await app.submit();
        assert.match(app.text('next-content'), /00:10\s+10월 20일\s+예정 도착/);
    });
    test('every query change immediately clears next and later without moving focus', async () => {
        const changes = [
            app => app.change('board', 'r1-b'), app => app.change('alight', 'r1-a'),
            app => app.change('route', '제2노선'), app => app.change('direction', '하교'),
            app => app.date('2026-10-19T08:00'), app => app.elements.refresh.emit('click'),
            app => app.elements.now.emit('click'),
        ];
        for (const change of changes) {
            const app = await memoryUI(); await app.open(); await app.submit();
            assert.equal(app.elements.next.hidden, false); assert.equal(app.elements.later.hidden, false);
            const focusCount = app.elements['candidates-heading'].focusCount;
            await change(app);
            assert.equal(app.elements.next.hidden, true); assert.equal(app.elements.later.hidden, true);
            assert.equal(app.elements.results.hidden, true);
            assert.equal(app.elements['next-content'].children.length, 0);
            assert.equal(app.elements.candidates.children.length, 0);
            assert.equal(app.elements['candidates-heading'].focusCount, focusCount);
        }
    });
    test('retry preserves the selected date; current time is a separate action', async () => {
        const app = await memoryUI(); await app.open();
        await app.date('2026-10-23T08:10'); await app.elements.refresh.emit('click');
        assert.equal(app.requests.at(-1).at, '2026-10-23T08:10:00+09:00');
        await app.elements.now.emit('click');
        assert.equal(app.elements.at.value, '2026-10-19T07:50');
        assert.equal(app.requests.at(-1).at, FIXED);
        assert.equal(app.elements.next.hidden, true, 'Changing the full schedule never submits a segment');
        assert.equal(app.focused, undefined);
    });
    test('full warnings remain available once and new segment warnings remain visible', async () => {
        const app = await memoryUI(params => {
            const data = fixture(params);
            data.warnings = ['원본 안내'];
            if (params.has('board')) data.warnings.push('구간 전용 안내');
            return data;
        });
        await app.open(); await app.submit();
        assert.match(app.text('warnings'), /원본 안내/);
        assert.doesNotMatch(app.text('segment-warnings'), /원본 안내/);
        assert.match(app.text('segment-warnings'), /구간 전용 안내/);
    });
    test('empty and failed explicit queries focus the result and remove an earlier next card', async () => {
        const app = await memoryUI((params, count) => count === 3 ? { httpStatus: 502 } : fixture(params));
        await app.open(); await app.submit(); const full = app.text('trips');
        await app.submit();
        assert.equal(app.elements.next.hidden, true); assert.equal(app.elements.later.hidden, true);
        assert.equal(app.elements['next-content'].children.length, 0);
        assert.equal(app.elements['segment-status'].dataset.state, 'error');
        assert.equal(app.elements['candidates-heading'].focusCount, 2);
        assert.equal(app.elements['candidates-heading'].scrollCount, 2);
        assert.equal(app.text('trips'), full);
        await app.date('2026-10-19T23:59'); await app.submit();
        assert.equal(app.elements.next.hidden, true);
        assert.match(app.text('segment-status'), /남은 시간표가 없습니다/);
        assert.equal(app.elements['candidates-heading'].focusCount, 3);
    });
    test('a result completed after its panel closes cannot steal focus or scroll', async () => {
        for (const visibility of ['hidden', 'layoutHidden']) {
            let release;
            const app = await memoryUI(params => params.has('board')
                ? new Promise(resolve => { release = () => resolve(fixture(params)); }) : fixture(params));
            await app.open(); const pending = app.submit(); await flush();
            app.panel[visibility] = true; release(); await pending;
            assert.equal(app.focused, undefined);
            assert.equal(app.elements['candidates-heading'].scrollCount, undefined);
        }
    });
    test('leaving and returning to the panel prevents old requests from taking focus', async () => {
        let navigation = 1, release, delayed = true;
        const app = await memoryUI(params => {
            if (params.has('board') && delayed) {
                delayed = false;
                return new Promise(resolve => { release = () => resolve(fixture(params)); });
            }
            return fixture(params);
        }, { shell: { getNavigation: () => navigation } });
        await app.open(); const pending = app.submit(); await flush();
        app.panel.hidden = true; ++navigation;
        app.panel.hidden = false; ++navigation;
        release(); await pending;
        assert.equal(app.elements.next.hidden, false, 'A current query result can still render after returning');
        assert.equal(app.focused, undefined);
        assert.equal(app.elements['candidates-heading'].scrollCount, undefined);
        await app.submit();
        assert.equal(app.focused, app.elements['candidates-heading'], 'A new explicit query can move focus');
        assert.equal(app.elements['candidates-heading'].focusCount, 1);
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
        assert.equal(app.elements.next.hidden, true);
        assert.equal(app.elements['next-content'].children.length, 0);
        assert.equal(app.elements.later.hidden, true);
        assert.equal(app.focused, undefined, 'Discarded requests never move focus');
        assert.equal(app.elements.results.hidden, true);
        assert.equal(app.text('segment-status'), '');
    });
} else {
    const { openApp, selectTool, noOverflow } = require('./frontend_smoke.cjs');
    for (const width of [360, 390, 1440]) test(`${width}px shuttle tab, full schedule, candidates and independent campus state`, async browser => {
        const longStop = '휴먼타운A(505동 앞) 버스정류장 · 승강장 위치 확인 필요';
        const app = await openApp(browser, { viewport: { width, height: width < 800 ? 720 : 900 }, fixedNow: FIXED, timezoneId: 'America/Los_Angeles',
            shuttleHandler(route, url) {
                const data = fixture(url.searchParams);
                data.stops.forEach(item => { if (item.id === 'r2-a') item.name = longStop; });
                data.trips.forEach(item => item.events.forEach(event => { if (event.stop_id === 'r2-a') event.name = longStop; }));
                data.timetable_candidates.forEach(item => { if (item.board.id === 'r2-a') item.board.name = longStop; });
                return route.fulfill({ json: data });
            } });
        try {
            const page = app.page;
            assert.equal(app.shuttleRequests.length, 0);
            await page.locator('#tab-knu').click();
            const state = await page.evaluate(() => ({ campus: selectedCampus, mode: originMode, destination: AppState.getDestination() }));
            await selectTool(page, 'shuttle');
            await page.waitForFunction(() => !document.getElementById('shuttle-query').disabled);
            assert.equal(await page.locator('#transport-panel-shuttle > h2').innerText(), '한림대학교 셔틀 시간표');
            assert.equal(await page.locator('#shuttle-results').isVisible(), false);
            assert.equal(await page.locator('#shuttle-at').inputValue(), '2026-10-19T07:50');
            assert.deepEqual(await page.evaluate(() => ({ campus: selectedCampus, mode: originMode, destination: AppState.getDestination() })), state);
            await page.locator('#shuttle-query').click();
            await page.waitForFunction(() => document.querySelectorAll('.shuttle-next-card').length === 1
                && document.querySelectorAll('.shuttle-candidate').length === 1);
            assert.equal(app.shuttleRequests.length, 2);
            assert.equal(await page.locator('.shuttle-trip').count(), 2);
            assert.match(await page.locator('#shuttle-context').innerText(), /07:50 이후 · 제1노선 · 등교/);
            assert.equal(await page.locator('.shuttle-next-time').innerText(), '09:15');
            assert.equal(await page.locator('.shuttle-next-time').getAttribute('datetime'), '2026-10-19T09:15:00+09:00');
            assert.equal(await page.locator('.shuttle-next-arrival').innerText(), '미확인');
            assert.match(await page.locator('#shuttle-candidates').innerText(), /09:20/);
            assert.equal(await page.evaluate(() => document.activeElement.id), 'shuttle-candidates-heading');
            assert.equal(await page.locator('#shuttle-trips').isVisible(), false, 'Full schedule starts collapsed');
            if (width < 800) assert.equal(await page.locator('.shuttle-next-time').evaluate(item => getComputedStyle(item).fontSize), '32px');
            await noOverflow(page);
            await page.locator('#shuttle-route').selectOption('제2노선');
            await page.waitForFunction(() => document.querySelector('#shuttle-board option[value="r2-b"]'));
            assert.equal(await page.locator('#shuttle-board option').filter({ hasText: '동명 정류장' }).count(), 2);
            assert.equal(await page.locator('#shuttle-next').isVisible(), false);
            assert.equal(await page.locator('#shuttle-later').isVisible(), false);
            await page.locator('#shuttle-board').selectOption('r2-a');
            await page.locator('#shuttle-alight').selectOption('r2-b');
            await page.locator('#shuttle-query').click();
            await page.locator('#shuttle-next').waitFor({ state: 'visible' });
            assert.match(await page.locator('.shuttle-next-route').first().innerText(), /휴먼타운A/);
            assert.equal(await page.locator('#shuttle-later').isVisible(), false);
            await noOverflow(page);
            await page.locator('#shuttle-full-heading').click();
            assert.equal(await page.locator('#shuttle-trips').isVisible(), true);
            await noOverflow(page);
            const before = app.shuttleRequests.length;
            await selectTool(page, 'subway');
            await selectTool(page, 'shuttle');
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
