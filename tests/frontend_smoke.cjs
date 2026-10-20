/**
 * Browser smoke checks for the Flask UI. All POI, transit, map and GPS data is
 * mocked: this suite does not contact external APIs or prove live data quality.
 *
 * Start Flask with a non-secret NAVER_MAP_CLIENT_ID, then run:
 *   NODE_PATH=/path/to/node_modules node tests/frontend_smoke.cjs
 * Optional: CAPSTONE_BASE_URL, CAPSTONE_BROWSER_EXECUTABLE,
 *           CAPSTONE_SCREENSHOT_DIR (explicitly labelled preview images),
 *           CAPSTONE_PREVIEW_FONT_DIR (local Pretendard WOFF2 files).
 */
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('playwright');

const BASE_URL = process.env.CAPSTONE_BASE_URL || 'http://127.0.0.1:8765';
const BASE_ORIGIN = new URL(BASE_URL).origin;
const SCREENSHOT_DIR = process.env.CAPSTONE_SCREENSHOT_DIR;
const PREVIEW_FONT_DIR = process.env.CAPSTONE_PREVIEW_FONT_DIR;
const DESTINATION = { name: '남춘천역', sub: '강원특별자치도 춘천시 퇴계동', lat: 37.8642, lon: 127.7238 };
const INJECTION = '<img src=x onerror="window.__injected=true">';
const clone = value => JSON.parse(JSON.stringify(value));
let previewFontCssPromise;

function loadPreviewFontCSS() {
    if (!PREVIEW_FONT_DIR) return Promise.resolve(null);
    if (!previewFontCssPromise) {
        previewFontCssPromise = Promise.all([
            fs.readFile(path.join(PREVIEW_FONT_DIR, 'Pretendard-Regular.woff2')),
            fs.readFile(path.join(PREVIEW_FONT_DIR, 'Pretendard-Bold.woff2')),
        ]).then(([regular, bold]) => [
            `@font-face{font-family:'Pretendard';font-style:normal;font-weight:400;font-display:swap;src:url('data:font/woff2;base64,${regular.toString('base64')}') format('woff2');}`,
            `@font-face{font-family:'Pretendard';font-style:normal;font-weight:700;font-display:swap;src:url('data:font/woff2;base64,${bold.toString('base64')}') format('woff2');}`,
        ].join('\n'));
    }
    return previewFontCssPromise;
}

// Shapes are the public response contracts in services/route_planner.py and
// services/transit_service.py. Times and service names here are test data.
function journeyFixture(payload = {}, unknown = false) {
    return {
        mode: 'journeys',
        origin: { name: payload.location || '공학관', lat: 37.88695, lon: 127.73585 },
        destination: { name: payload.dest_name || DESTINATION.name, lat: payload.dest_lat || DESTINATION.lat, lon: payload.dest_lon || DESTINATION.lon },
        source: { source: 'api', sequence_basis: 'api_direction' },
        arrival_status: { status: unknown ? 'unavailable' : 'ok', code: unknown ? 'NETWORK' : null, message: unknown ? '실시간 도착정보를 확인하지 못했습니다.' : '정류장별 실시간 도착정보를 조회했습니다.' },
        warnings: ['테스트용 경로입니다. 실제 운행 정보가 아닙니다.', '출발·목적지 반경 500m 정류장 기준, 동일 정류장 내 1회 환승까지 검색합니다.'],
        search: { max_transfers: 1, nearby_radius_m: 500, max_stops_per_endpoint: 5, max_routes: 24, max_refined_candidates: 80 },
        journeys: [{
            transfer_count: 0, total_min: unknown ? null : 28,
            total_unavailable_reason: unknown ? 'wait_unknown' : null,
            known_min: unknown ? 21 : 28, walk_min: 7, ride_min: 14,
            wait_min: unknown ? null : 7, has_live_departure: !unknown,
            access_walk: { minutes: 4, distance_m: 280 },
            egress_walk: { minutes: 3, distance_m: 210 }, transfer_buffer_min: 0,
            warnings: [unknown ? '이동 후 탈 수 있는 실시간 도착 정보가 없어 대기 시간과 총 소요 시간을 알 수 없습니다.' : '버스 탑승 시간은 정류장 순서와 구간 거리로 추정하며 실제 교통 상황과 다를 수 있습니다.'],
            is_estimate: true,
            legs: [{
                route_id: 'TEST_200_1', route_no: '200-1', direction: '1',
                sequence_basis: 'api_direction', direction_verified: true,
                route_number_verified: true, arrival_reference_only: false,
                board: { id: 'TEST_BOARD', name: '한림대학교 정문', lat: 37.8848, lon: 127.7381 },
                alight: { id: 'TEST_ALIGHT', name: '남춘천역 환승센터', lat: 37.8649, lon: 127.7251 },
                stop_count: 9, ride_min: 14, arrival_min: unknown ? null : 11,
                wait_min: unknown ? null : 7,
                path: [{ lat: 37.8848, lon: 127.7381 }, { lat: 37.8748, lon: 127.7301 }, { lat: 37.8649, lon: 127.7251 }],
            }],
        }],
    };
}

function nearbyFixture(payload = {}) {
    return [{
        start_name: payload.location || '공학관', station_name: '한림대학교 정문', station_no: '1010',
        station_lat: 37.8848, station_lon: 127.7381,
        direction: '방향은 승차 전 정류장 안내를 확인하세요.',
        walk_min: 4, walk_dist: 280,
        buses: [{ route: '200-1', destination: '노선 참고: 남춘천역', min_left: 8, walk_min: 4, spare_min: 4,
            status_badge: '여유', status_type: 'relax', guide_msg: '도보 도착 후 약 4분의 여유가 있습니다.', prev_cnt: 4, is_recommended: false }],
        has_recommended_bus: false, matched_routes: [], target_lat: 37.88695, target_lon: 127.73585,
        arrival_error: null, source: 'api',
    }, {
        start_name: payload.location || '공학관', station_name: '교동초등학교', station_no: '1020',
        station_lat: 37.8835, station_lon: 127.7351,
        direction: '방향은 승차 전 정류장 안내를 확인하세요.',
        walk_min: 6, walk_dist: 420, buses: [], has_recommended_bus: false, matched_routes: [],
        target_lat: 37.88695, target_lon: 127.73585, arrival_error: null, source: 'api',
    }];
}

function taxiFixture(payload = {}, options = {}) {
    const amount = options.amount === undefined ? 10001 : options.amount;
    const at = options.at || Date.now();
    return {
        status: amount === null ? 'partial' : 'ok', code: null,
        origin: { name: payload.location || '테스트 공개 장소', lat: payload.lat || 37.88695, lon: payload.lon || 127.73585 },
        destination: { name: payload.dest_name || DESTINATION.name, lat: payload.dest_lat || DESTINATION.lat, lon: payload.dest_lon || DESTINATION.lon },
        ride_duration_seconds: 600, distance_m: 2500,
        fare: { amount_krw: amount, basis: 'provider_estimate', provider_total_fare_krw: 0,
            toll_included: null, surcharge_included: null, call_fee_included: null },
        pickup_wait_seconds: null, door_to_door_seconds: null,
        fetched_at: new Date(at).toISOString(), provider_data_at: null,
        expires_at: new Date(at + (options.ttl || 60) * 1000).toISOString(), cache_ttl_seconds: options.ttl || 60,
        from_cache: false, cache_age_seconds: 0, warnings: ['자동화 검증용 견적이며 실제 춘천 운임이 아닙니다.'],
        split_preview: [1, 2, 3, 4].map(count => ({ count, amounts_krw: amount === null ? null
            : Array.from({ length: count }, (_, index) => Math.floor(amount / count) + (index < amount % count ? 1 : 0)) })),
    };
}

// This illustration only exists inside the test SDK. It is deliberately
// labelled a preview, and contains no external tiles or claimed real geography.
const MAP_SDK = String.raw`(() => {
    class LatLng {
        constructor(lat, lon) { this._lat = Number(lat); this._lon = Number(lon); this.y = this._lat; this.x = this._lon; }
        lat() { return this._lat; } lng() { return this._lon; }
        equals(other) { return !!other && this.lat() === other.lat() && this.lng() === other.lng(); }
    }
    class LatLngBounds {
        constructor() { this.points = []; }
        extend(point) { this.points.push(point); return this; }
        getCenter() { return this.points[0] || new LatLng(37.8863, 127.7381); }
    }
    class Map {
        constructor(id, options = {}) {
            this.element = typeof id === 'string' ? document.getElementById(id) : id;
            this.center = options.center; this.zoom = options.zoom || 15;
            this.element.insertAdjacentHTML('beforeend', '<svg data-test-map-preview="true" aria-label="Preview map, illustrative test data" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000" preserveAspectRatio="xMidYMid slice" style="position:absolute;inset:0;width:100%;height:100%;pointer-events:none"><defs><pattern id="preview-blocks" width="160" height="155" patternUnits="userSpaceOnUse"><rect width="160" height="155" fill="#e9eddd"/><rect x="10" y="10" width="65" height="55" rx="8" fill="#e1e6d6"/><rect x="88" y="10" width="63" height="95" rx="7" fill="#dee4d4"/><path d="M0 80H160M80 0V155" fill="none" stroke="#fdfdf7" stroke-width="13"/></pattern></defs><rect width="1000" height="1000" fill="url(#preview-blocks)"/><path d="M50 -100C450 160 250 360 590 430S680 830 1130 1010" fill="none" stroke="#c5dfd0" stroke-width="155"/><path d="M50 -100C450 160 250 360 590 430S680 830 1130 1010" fill="none" stroke="#b2d8e2" stroke-width="95"/><path d="M-50 660L1050 240M250 -50L650 1050" fill="none" stroke="#dcded0" stroke-width="28"/><path d="M-50 660L1050 240M250 -50L650 1050" fill="none" stroke="#fffef3" stroke-width="20"/><g transform="translate(525 660)"><rect x="-168" y="-25" width="336" height="66" rx="14" fill="#fff" fill-opacity=".95"/><text text-anchor="middle" fill="#31493e" font-family="sans-serif" font-size="22" font-weight="700">PREVIEW MAP</text><text y="25" text-anchor="middle" fill="#506259" font-family="sans-serif" font-size="16">Illustration / Test route data</text></g></svg>');
            this.markerLayer = document.createElement('div');
            this.markerLayer.style.cssText = 'position:absolute;inset:0;pointer-events:none;overflow:hidden';
            this.element.append(this.markerLayer);
            window.__testMapReady = true;
        }
        panTo(position) { this.center = position; }
        setCenter(position) { this.center = position; }
        getCenter() { return this.center; }
        fitBounds(bounds) { this.bounds = bounds; }
        setZoom(zoom) { this.zoom = zoom; }
        getZoom() { return this.zoom; }
        getElement() { return this.element; }
        getPanes() { return { overlayLayer: this.markerLayer, floatPane: this.markerLayer }; }
        setOptions() {} refresh() {} autoResize() {}
    }
    class Marker {
        constructor(options = {}) {
            this.position = options.position; this.element = document.createElement('div');
            this.element.style.cssText = 'position:absolute;left:52%;top:46%;transform:translate(-50%,-50%)';
            if (options.icon && typeof options.icon.content === 'string') this.element.innerHTML = options.icon.content;
            if (options.icon && options.icon.content instanceof Element) this.element.append(options.icon.content);
            this.setMap(options.map);
        }
        setMap(map) { this.map = map; this.element.remove(); if (map && map.markerLayer) map.markerLayer.append(this.element); }
        setPosition(position) { this.position = position; }
        getPosition() { return this.position; }
        setIcon(icon) { if (icon && typeof icon.content === 'string') this.element.innerHTML = icon.content; }
        setZIndex() {} setTitle() {}
    }
    class InfoWindow { constructor(options) { this.options = options; } open() {} close() {} setContent() {} }
    class Polyline { constructor(options) { this.options = options; } setMap() {} setPath() {} }
    class Point { constructor(x, y) { this.x = x; this.y = y; } }
    window.naver = { maps: { Map, LatLng, LatLngBounds, Marker, Point, Size: Point, InfoWindow, Polyline,
        Position: { TOP_RIGHT: 1, RIGHT_TOP: 1, BOTTOM_RIGHT: 2 }, ZoomControlStyle: { SMALL: 1 },
        Event: {
            addListener(target, event, callback) {
                if (event === 'tilesloaded' && typeof callback === 'function') {
                    setTimeout(() => callback.call(target), 0);
                }
                return {};
            },
            removeListener() {}, trigger() {}
        } } };
})();`;

async function openApp(browser, options = {}) {
    const previewFontCSS = await loadPreviewFontCSS();
    const context = await browser.newContext({
        viewport: options.viewport || { width: 390, height: 844 },
        deviceScaleFactor: 1, reducedMotion: 'reduce', locale: 'ko-KR', timezoneId: options.timezoneId || 'Asia/Seoul',
    });
    const requests = [], searches = [], taxiRequests = [], roomRequests = [], errors = [], blocked = [];
    await context.addInitScript(({ denyGPS }) => {
        window.__testGPSCalls = [];
        window.__injected = false;
        const locate = (success, error, type) => {
            window.__testGPSCalls.push(type);
            queueMicrotask(() => denyGPS
                ? error && error({ code: 1, message: 'Test permission denied' })
                : success && success({ coords: { latitude: 37.88695, longitude: 127.73585, accuracy: 20 }, timestamp: Date.now() }));
        };
        Object.defineProperty(navigator, 'geolocation', { configurable: true, value: {
            getCurrentPosition(success, error) { locate(success, error, 'getCurrentPosition'); },
            watchPosition(success, error) { locate(success, error, 'watchPosition'); return 1; },
            clearWatch() {},
        } });
    }, { denyGPS: !!options.denyGPS });
    await context.route('**/*', async route => {
        const request = route.request();
        const url = new URL(request.url());
        if (/\.map\.naver\.com$/.test(url.hostname) && /\/maps\.js$/.test(url.pathname)) {
            if (options.mapUnavailable) return route.abort('failed');
            if (options.mapSDKGate) await options.mapSDKGate;
            return route.fulfill({ contentType: 'application/javascript', body: MAP_SDK });
        }
        if (previewFontCSS && url.hostname === 'cdn.jsdelivr.net' && /\/pretendard[^/]*\.css$/i.test(url.pathname)) {
            return route.fulfill({ contentType: 'text/css', body: previewFontCSS });
        }
        if (url.origin !== BASE_ORIGIN) {
            blocked.push(url.origin);
            return route.abort('blockedbyclient');
        }
        if (url.pathname === '/search_poi') {
            const query = url.searchParams.get('q') || '';
            searches.push({ query, campus: url.searchParams.get('campus') });
            if (options.searchHandler) return options.searchHandler(route, query, url);
            return route.fulfill({ json: [clone(DESTINATION)] });
        }
        if (url.pathname === '/get_route_info') {
            const payload = request.postDataJSON();
            requests.push(payload);
            if (options.routeHandler) return options.routeHandler(route, payload, requests.length);
            return route.fulfill({ json: payload.dest_name ? journeyFixture(payload, !!options.unknownWait) : nearbyFixture(payload) });
        }
        if (url.pathname === '/api/taxi/estimate') {
            const payload = request.postDataJSON();
            taxiRequests.push(payload);
            if (options.taxiHandler) return options.taxiHandler(route, payload, taxiRequests.length);
            return route.fulfill({ json: taxiFixture(payload) });
        }
        if (url.pathname.startsWith('/api/ride-rooms')) {
            roomRequests.push({ method: request.method(), pathname: url.pathname, query: url.search,
                body: request.method() === 'POST' ? request.postDataJSON() : null, headers: request.headers() });
            if (options.roomHandler) return options.roomHandler(route, url, roomRequests.at(-1), roomRequests.length);
            if (!options.liveRooms) return route.fulfill({ status: 503, json: { code: 'CHAT_DISABLED', error: '자동화 검증에서 채팅 기능을 사용하지 않아요.' } });
        }
        return route.continue();
    });
    const page = await context.newPage();
    page.setDefaultTimeout(6500);
    page.on('pageerror', error => errors.push(error.message));
    page.on('dialog', dialog => { errors.push(`Unexpected blocking dialog: ${dialog.message()}`); dialog.dismiss().catch(() => {}); });
    try {
        await page.goto(BASE_URL, { waitUntil: options.mapSDKGate ? 'domcontentloaded' : 'networkidle' });
        await page.locator('#route-submit').waitFor({ state: 'visible' });
        assert.deepEqual(await page.evaluate(() => window.__testGPSCalls), [], 'Loading the app must not request GPS permission');
        assert.deepEqual(errors, [], 'App startup has no unhandled JavaScript errors');
    } catch (error) {
        await context.close();
        throw error;
    }
    return { page, context, requests, searches, taxiRequests, roomRequests, errors, blocked, async close() {
        const captured = [...errors];
        await context.close();
        assert.deepEqual(captured, [], 'No unhandled JavaScript errors or blocking dialogs');
    } };
}

async function noOverflow(page) {
    const result = await page.evaluate(() => {
        const width = document.documentElement.clientWidth;
        const outside = [...document.querySelectorAll('#planner-panel input, #planner-panel textarea, #planner-panel select, #planner-panel button, #result-container button, #result-container summary')]
            .filter(el => el.getClientRects().length && (el.getBoundingClientRect().left < -1 || el.getBoundingClientRect().right > width + 1))
            .map(el => el.id || el.textContent.trim().slice(0, 80));
        return { scrollWidth: document.documentElement.scrollWidth, viewport: window.innerWidth, outside };
    });
    assert.ok(result.scrollWidth <= result.viewport + 1, `Horizontal overflow: ${JSON.stringify(result)}`);
    assert.deepEqual(result.outside, [], 'Interactive controls remain inside the viewport');
}

async function chooseBuilding(page, campus = 'hallym') {
    await page.locator(`#tab-${campus}`).click();
    await page.locator('#origin-building').click();
    const building = await page.locator('#building-select option').evaluateAll(options => options.find(option => option.value)?.value);
    assert.ok(building, `${campus} has a selectable building from Flask data`);
    await page.locator('#building-select').selectOption(building);
    return building;
}

async function selectDestination(page, query = '남춘천', expectedName = DESTINATION.name) {
    const input = page.locator('#dest-input');
    await input.fill(query);
    await page.locator('#autocomplete-list [role="option"]').first().waitFor({ state: 'visible' });
    await input.press('ArrowDown');
    assert.ok(await input.getAttribute('aria-activedescendant'), 'Keyboard option has an accessible active descendant');
    await input.press('Enter');
    await page.locator('#dest-selected-tag').waitFor({ state: 'visible' });
    assert.ok((await page.locator('#dest-selected-text').textContent()).includes(expectedName), 'Chosen destination is visible');
    await page.locator('#autocomplete-list').waitFor({ state: 'hidden' });
}

async function clearDestination(page) {
    await page.locator('#dest-selected-tag button').click();
    await page.locator('#dest-input').waitFor({ state: 'visible' });
    await page.locator('#dest-selected-tag').waitFor({ state: 'hidden' });
}

async function resultContains(page, pattern) {
    await page.locator('#result-container').filter({ hasText: pattern }).waitFor({ state: 'visible' });
}

async function screenshot(page, name) {
    if (!SCREENSHOT_DIR) return;
    await fs.mkdir(SCREENSHOT_DIR, { recursive: true });
    await page.evaluate(() => document.fonts.ready);
    await page.evaluate(() => window.scrollTo({ top: 0, left: 0, behavior: 'instant' }));
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, name), fullPage: true, animations: 'disabled' });
}

const tests = [];
const test = (name, run) => tests.push({ name, run });

test('390px initial layout, campus data, keyboard destination selection and clear', async browser => {
    const app = await openApp(browser);
    try {
        const { page } = app;
        await page.locator('#map-status').waitFor({ state: 'hidden' });
        await noOverflow(page);
        await screenshot(page, 'mobile-initial-preview.png');
        const hallym = await chooseBuilding(page);
        await selectDestination(page);
        const knu = await chooseBuilding(page, 'knu');
        assert.notEqual(knu, hallym, 'Campus switch replaces building options');
        assert.ok((await page.locator('#dest-selected-text').textContent()).includes(DESTINATION.name), 'Campus change preserves selected destination');
        assert.equal(await page.locator('#tab-knu').getAttribute('aria-pressed'), 'true');
        assert.equal(app.requests.length, 0, 'Input selection waits for the explicit route action');
        await clearDestination(page);
        assert.equal(await page.locator('#dest-input').inputValue(), '');
        await noOverflow(page);
    } finally { await app.close(); }
});

test('building journey request preserves campus and destination; known total renders', async browser => {
    const app = await openApp(browser);
    try {
        const { page } = app;
        const building = await chooseBuilding(page, 'knu');
        await selectDestination(page);
        await page.locator('#route-submit').click();
        await resultContains(page, '총 소요 추정');
        const request = app.requests.at(-1);
        assert.equal(request.campus, 'knu');
        assert.equal(request.location, building);
        assert.equal(request.dest_name, DESTINATION.name);
        assert.equal(request.dest_lat, DESTINATION.lat);
        assert.equal(request.dest_lon, DESTINATION.lon);
        assert.equal(app.searches.at(-1).campus, 'knu');
        const text = await page.locator('#result-container').innerText();
        assert.match(text, /28\s*분/);
        assert.match(text, /200-1/);
        assert.doesNotMatch(text, /총 소요 미확인/);
        await noOverflow(page);
        await screenshot(page, 'mobile-journey-preview.png');
    } finally { await app.close(); }
});

test('360px layout preserves unknown wait and total', async browser => {
    const app = await openApp(browser, { viewport: { width: 360, height: 800 }, unknownWait: true });
    try {
        const { page } = app;
        await noOverflow(page);
        await chooseBuilding(page);
        await selectDestination(page);
        await page.locator('#route-submit').click();
        await resultContains(page, '총 소요 미확인');
        await resultContains(page, '대기 시간 미확인');
        assert.doesNotMatch(await page.locator('#result-container').innerText(), /총 소요 추정|NaN|undefined/);
        await noOverflow(page);
        await screenshot(page, 'mobile-360-unknown-wait-preview.png');
    } finally { await app.close(); }
});

test('1440px desktop layout and nearby station arrays', async browser => {
    const app = await openApp(browser, { viewport: { width: 1440, height: 1000 } });
    try {
        const { page } = app;
        await noOverflow(page);
        await chooseBuilding(page);
        await page.locator('#route-submit').click();
        await resultContains(page, '한림대학교 정문');
        await resultContains(page, '교동초등학교');
        await resultContains(page, '200-1');
        assert.equal(app.requests.at(-1).dest_name, '');
        await noOverflow(page);
        await selectDestination(page);
        await page.locator('#route-submit').click();
        await resultContains(page, '총 소요 추정');
        await noOverflow(page);
        await screenshot(page, 'desktop-journey-preview.png');
    } finally { await app.close(); }
});

test('empty place search and network failure recover on the next query', async browser => {
    const app = await openApp(browser, { searchHandler: async (route, query) => {
        if (query === '없는장소') return route.fulfill({ json: [] });
        if (query === '연결실패') return route.abort('failed');
        return route.fulfill({ json: [clone(DESTINATION)] });
    } });
    try {
        const { page } = app;
        await page.locator('#dest-input').fill('없는장소');
        await page.locator('#search-status').filter({ hasText: /검색 결과가 없습니다/ }).waitFor({ state: 'visible' });
        await page.locator('#autocomplete-list').waitFor({ state: 'hidden' });
        await page.locator('#dest-input').fill('연결실패');
        await page.locator('#search-status').filter({ hasText: /연결|실패|다시/ }).waitFor({ state: 'visible' });
        await selectDestination(page);
        await noOverflow(page);
    } finally { await app.close(); }
});

test('null, HTTP error and empty journey results do not break another search', async browser => {
    const app = await openApp(browser, { routeHandler: async (route, payload, count) => {
        if (count === 1) return route.fulfill({ contentType: 'application/json', body: 'null' });
        if (count === 2) return route.fulfill({ status: 503, json: { error: '연결을 확인하고 다시 검색해 주세요.' } });
        const data = journeyFixture(payload);
        if (count === 3) data.journeys = [];
        return route.fulfill({ json: data });
    } });
    try {
        const { page } = app;
        await chooseBuilding(page);
        await page.locator('#route-submit').click();
        await resultContains(page, /정류장을 찾지 못했어요|조회 결과|응답/);
        await page.locator('#route-submit').click();
        await resultContains(page, '연결을 확인하고 다시 검색해 주세요.');
        await selectDestination(page);
        await page.locator('#route-submit').click();
        await resultContains(page, '검색 범위 안에서 경로를 찾지 못했어요');
        await page.locator('#route-submit').click();
        await resultContains(page, '총 소요 추정');
        await noOverflow(page);
    } finally { await app.close(); }
});

test('POI, route and map marker HTML payloads remain text', async browser => {
    const app = await openApp(browser, {
        searchHandler: route => route.fulfill({ json: [{ ...DESTINATION, name: INJECTION, sub: INJECTION }] }),
        routeHandler: (route, payload) => {
            const data = journeyFixture(payload);
            data.journeys[0].legs[0].board.name = INJECTION;
            data.journeys[0].warnings = [INJECTION];
            return route.fulfill({ json: data });
        },
    });
    try {
        const { page } = app;
        await chooseBuilding(page);
        await page.locator('#dest-input').fill('텍스트테스트');
        await page.locator('#autocomplete-list [role="option"]').first().waitFor({ state: 'visible' });
        assert.ok((await page.locator('#autocomplete-list').innerText()).includes(INJECTION));
        assert.equal(await page.locator('#autocomplete-list img').count(), 0);
        await page.locator('#dest-input').press('ArrowDown');
        await page.locator('#dest-input').press('Enter');
        await page.locator('#route-submit').click();
        await resultContains(page, INJECTION);
        assert.equal(await page.locator('#result-container img, #dest-selected-tag img, #map img[src="x"]').count(), 0);
        assert.equal(await page.evaluate(() => window.__injected), false);
        await noOverflow(page);
    } finally { await app.close(); }
});

test('failed or delayed map SDK leaves search and route results usable', async browser => {
    const app = await openApp(browser, { mapUnavailable: true });
    try {
        const { page } = app;
        await page.locator('#map-status').filter({ hasText: /지도/ }).waitFor({ state: 'visible' });
        await chooseBuilding(page);
        await selectDestination(page);
        await page.locator('#route-submit').click();
        await resultContains(page, '총 소요 추정');
        assert.equal(await page.evaluate(() => typeof window.naver), 'undefined');
    } finally { await app.close(); }

    let releaseSDK;
    const mapSDKGate = new Promise(resolve => { releaseSDK = resolve; });
    let delayedApp;
    try {
        delayedApp = await openApp(browser, { mapSDKGate });
        const { page } = delayedApp;
        assert.equal(await page.evaluate(() => typeof window.naver), 'undefined');
        await chooseBuilding(page, 'knu');
        await selectDestination(page);
        await page.locator('#route-submit').click();
        await resultContains(page, '총 소요 추정');
        assert.equal(await page.evaluate(() => typeof window.naver), 'undefined', 'Search and transit rendering work before the SDK response');
        releaseSDK();
        await page.waitForFunction(() => window.__testMapReady === true);
        await page.locator('#map-status').waitFor({ state: 'hidden' });
        await resultContains(page, '총 소요 추정');
    } finally {
        releaseSDK();
        if (delayedApp) await delayedApp.close();
    }
});

test('GPS denial offers building fallback that remains usable', async browser => {
    const app = await openApp(browser, { denyGPS: true });
    try {
        const { page } = app;
        await page.locator('#origin-gps').click();
        await page.locator('#route-submit').click();
        await page.locator('#origin-status').filter({ hasText: /위치 권한.*교내 건물/ }).waitFor({ state: 'visible' });
        assert.equal(app.requests.length, 0, 'Denied GPS is not sent as a transit request');
        assert.ok((await page.evaluate(() => window.__testGPSCalls)).length > 0);
        await chooseBuilding(page);
        await page.locator('#route-submit').click();
        await resultContains(page, '한림대학교 정문');
    } finally { await app.close(); }
});

test('an older autocomplete response cannot replace the newest search', async browser => {
    let releaseOld, markOldStarted;
    const oldStarted = new Promise(resolve => { markOldStarted = resolve; });
    const oldGate = new Promise(resolve => { releaseOld = resolve; });
    const app = await openApp(browser, { searchHandler: async (route, query) => {
        if (query === '이전검색') {
            markOldStarted();
            await oldGate;
            return route.fulfill({ json: [{ ...DESTINATION, name: '이전검색의 오래된 장소' }] }).catch(() => {});
        }
        return route.fulfill({ json: [{ ...DESTINATION, name: '최근검색의 올바른 장소' }] });
    } });
    try {
        const { page } = app;
        // Simulate a transport where cancellation cannot stop the remote reply:
        // the request-generation guard must still discard that stale response.
        await page.evaluate(() => {
            const originalFetch = window.fetch.bind(window);
            window.fetch = (input, init) => String(input).includes('/search_poi')
                ? originalFetch(input, init ? { ...init, signal: undefined } : init)
                : originalFetch(input, init);
        });
        await page.locator('#dest-input').fill('이전검색');
        await Promise.race([oldStarted, new Promise((_, reject) => setTimeout(() => reject(new Error('Older POI request was not sent')), 6500))]);
        await page.locator('#dest-input').fill('최근검색');
        await page.locator('#autocomplete-list').filter({ hasText: '최근검색의 올바른 장소' }).waitFor({ state: 'visible' });
        const oldResponse = page.waitForResponse(response => new URL(response.url()).searchParams.get('q') === '이전검색');
        releaseOld();
        await oldResponse;
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        assert.ok((await page.locator('#autocomplete-list').innerText()).includes('최근검색의 올바른 장소'));
        assert.ok(!(await page.locator('#autocomplete-list').innerText()).includes('이전검색의 오래된 장소'));
        await page.locator('#dest-input').press('ArrowDown');
        await page.locator('#dest-input').press('Enter');
        assert.ok((await page.locator('#dest-selected-text').innerText()).includes('최근검색의 올바른 장소'));
    } finally { releaseOld(); await app.close(); }
});

async function run() {
    const browser = await chromium.launch({ headless: true,
        ...(process.env.CAPSTONE_BROWSER_EXECUTABLE ? { executablePath: process.env.CAPSTONE_BROWSER_EXECUTABLE } : {}),
    });
    const failures = [];
    try {
        for (const item of tests) {
            try {
                await item.run(browser);
                console.log(`PASS ${item.name}`);
            } catch (error) {
                failures.push({ name: item.name, error });
                console.error(`FAIL ${item.name}\n${error.stack || error}`);
            }
        }
    } finally { await browser.close(); }
    console.log(`\n${tests.length - failures.length}/${tests.length} browser smoke checks passed. External transit, POI, GPS and map services were mocked.`);
    if (SCREENSHOT_DIR) console.log(`Preview screenshots: ${SCREENSHOT_DIR}`);
    if (failures.length) process.exitCode = 1;
}

module.exports = { openApp, chooseBuilding, selectDestination, clearDestination, noOverflow, resultContains,
    taxiFixture, journeyFixture, nearbyFixture, DESTINATION, INJECTION, BASE_URL };
if (require.main === module) run().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
