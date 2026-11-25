/** Run production taxi/API/GPS controllers with an in-memory DOM and clock.
 * No browser, credentials, real location, Flask process or external requests.
 * Browser layout coverage is separate in taxi_frontend_smoke.cjs.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const project = path.resolve(__dirname, '..');
const markup = fs.readFileSync(path.join(project, 'templates/index.html'), 'utf8');
const helper = fs.readFileSync(path.join(__dirname, 'frontend_smoke.cjs'), 'utf8');
const fixtureStart = helper.indexOf('function taxiFixture(');
const fixtureEnd = helper.indexOf('\n// This illustration', fixtureStart);
assert.ok(fixtureStart >= 0 && fixtureEnd > fixtureStart);
const fixtureCode = helper.slice(fixtureStart, fixtureEnd);
const destination = { name: '합성 목적지', lat: 37.8642, lon: 127.7238 };
const tests = [];
const test = (name, run) => tests.push({ name, run });
const flush = async () => { for (let index = 0; index < 40; index++) await Promise.resolve(); };
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

function memoryUI(handler) {
    const elements = {}, events = new Map(), requests = [], locations = [], timers = new Map();
    let now = Date.parse('2030-10-21T04:00:00Z'), timerId = 0;
    const NativeDate = Date;
    class ClockDate extends NativeDate {
        constructor(...args) { super(...(args.length ? args : [now])); }
        static now() { return now; }
    }
    class Element {
        constructor(tag = 'div') {
            this.tagName = tag.toUpperCase(); this.children = []; this.listeners = new Map();
            this.attributes = {}; this.dataset = {}; this.style = {}; this.value = ''; this._text = '';
            this.hidden = false; this.disabled = false; this.classes = new Set();
            this.classList = {
                add: (...names) => names.forEach(name => this.classes.add(name)),
                remove: (...names) => names.forEach(name => this.classes.delete(name)),
                contains: name => this.classes.has(name),
                toggle: (name, force) => {
                    const enable = force === undefined ? !this.classes.has(name) : Boolean(force);
                    if (enable) this.classes.add(name); else this.classes.delete(name);
                    return enable;
                },
            };
        }
        get className() { return [...this.classes].join(' '); }
        set className(value) { this.classes = new Set(String(value).split(/\s+/).filter(Boolean)); }
        get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
        set textContent(value) { this._text = String(value); this.children = []; }
        set innerHTML(value) { throw new Error('Unexpected HTML insertion: ' + value); }
        replaceChildren(...children) { this._text = ''; this.children = [...children]; }
        appendChild(child) { this.children.push(child); return child; }
        append(...children) { this.children.push(...children); }
        setAttribute(name, value) { this.attributes[name] = String(value); }
        getAttribute(name) { return this.attributes[name] ?? null; }
        removeAttribute(name) { delete this.attributes[name]; }
        focus() { document.activeElement = this; }
        addEventListener(type, callback) { this.listeners.set(type, [...(this.listeners.get(type) || []), callback]); }
        async emit(type) {
            if (type === 'click' && this.disabled) return;
            for (const callback of this.listeners.get(type) || []) callback({ target: this, preventDefault() {} });
            await flush();
        }
    }
    for (const match of markup.matchAll(/<(\w+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
        const element = new Element(match[1]);
        element.id = match[3]; element.hidden = /\bhidden\b/.test(match[2]);
        element.disabled = /\bdisabled\b/.test(match[2]);
        elements[match[3]] = element;
    }
    // main.js accepts this local test building list without any server startup.
    elements['campus-building-data'].textContent = JSON.stringify({ hallym: ['공학관', '도서관'], knu: ['공과대학1호관'] });
    const document = {
        readyState: 'loading', activeElement: null,
        getElementById: id => elements[id] || null,
        createElement: tag => new Element(tag),
        addEventListener: (type, callback) => events.set(type, [...(events.get(type) || []), callback]),
    };
    function quote(payload = {}, settings = {}) {
        return JSON.parse(JSON.stringify(vm.runInNewContext(fixtureCode + '\ntaxiFixture(payload, settings)', {
            payload, settings, Date: ClockDate, DESTINATION: destination,
        })));
    }
    const window = {
        document, Date: ClockDate, Intl, AbortController, Error, TypeError, SyntaxError, Promise, console,
        isSecureContext: true,
        setTimeout(callback, delay = 0) {
            const id = ++timerId; timers.set(id, { at: now + Math.max(0, Number(delay) || 0), callback }); return id;
        },
        clearTimeout(id) { timers.delete(id); },
        navigator: { geolocation: { getCurrentPosition(success, error, options) { locations.push({ success, error, options }); } } },
        fetch: async (url, options = {}) => {
            const request = { url, pathname: new URL(url, 'http://memory.invalid').pathname,
                payload: options.body ? JSON.parse(options.body) : null, options, signal: options.signal };
            requests.push(request);
            // Intentionally ignore AbortSignal at this boundary: stale-response
            // protection must also work for a request the transport cannot cancel.
            const reply = handler ? await handler(request, requests.length, quote)
                : request.pathname === '/api/taxi/estimate' ? quote(request.payload) : { notice: '합성 버스 응답' };
            return { ok: !reply?.httpStatus || reply.httpStatus < 400,
                json: async () => {
                    if (reply?.decodeError) throw reply.decodeError;
                    if (reply?.bodyPromise) return reply.bodyPromise;
                    return reply?.body === undefined ? reply : reply.body;
                } };
        },
    };
    window.window = window;
    vm.createContext(window);
    for (const filename of ['main.js', 'api-service.js', 'gps-controller.js', 'taxi-controller.js']) {
        vm.runInContext(fs.readFileSync(path.join(project, 'static/js', filename), 'utf8'), window, { filename });
    }
    document.readyState = 'interactive';
    for (const callback of events.get('DOMContentLoaded') || []) callback();
    const invalidate = () => window.AppModules.api.cancelPendingRequests();
    return {
        window, elements, requests, locations, quote,
        text: id => elements[id].textContent,
        async click(id = 'taxi-refresh') { await elements[id].emit('click'); },
        chooseBuilding(name = '공학관', campus = 'hallym') {
            window.AppState.setSelectedCampus(campus); window.AppState.setOriginMode('building');
            elements['building-select'].value = name; invalidate();
        },
        chooseGPS() { window.AppState.setOriginMode('gps'); invalidate(); },
        chooseDestination(place = destination) { window.AppState.setDestination(place.name, place.lat, place.lon); invalidate(); },
        clearDestination() { window.AppState.clearDestination(); invalidate(); },
        async location(index = 0, value = { latitude: 37.88695, longitude: 127.73585 }) {
            locations[index].success({ coords: value }); await flush();
        },
        async denyLocation(index = 0) { locations[index].error({ code: 1 }); await flush(); },
        async advance(milliseconds) {
            const target = now + milliseconds;
            for (let count = 0; count < 1000; count++) {
                const next = [...timers.entries()].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
                if (!next) break;
                timers.delete(next[0]); now = next[1].at; next[1].callback(); await flush();
                if (count === 999) throw new Error('Unexpected repeating timer');
            }
            now = target; await flush();
        },
    };
}

const taxiCalls = app => app.requests.filter(request => request.pathname === '/api/taxi/estimate');
const busCalls = app => app.requests.filter(request => request.pathname === '/get_route_info');
const prepare = app => { app.chooseBuilding(); app.chooseDestination(); };
function descendants(element) { return element.children.flatMap(child => [child, ...descendants(child)]); }
const taxiNode = (app, name) => descendants(app.elements['taxi-content']).find(node => node.classList.contains(name));
const splitButtons = app => descendants(app.elements['taxi-content']).filter(node => node.classList.contains('taxi-split-choice'));
const selectCount = async (app, count) => { await splitButtons(app).find(button => button.dataset.count === String(count)).emit('click'); };

test('ordinary markup loads taxi and shared rooms once, without initial GPS or requests', async () => {
    const app = memoryUI(); await flush();
    assert.doesNotMatch(markup, /real-mobility-template/);
    assert.ok(markup.includes('id="ride-room-section"'));
    assert.equal((markup.match(/filename='js\/ride-room-controller\.js'/g) || []).length, 1);
    assert.equal((markup.match(/filename='js\/taxi-controller\.js'/g) || []).length, 1);
    assert.ok(markup.indexOf('id="taxi-section"') > markup.indexOf('id="transport-panel-taxi"'));
    assert.equal(app.elements['taxi-section'].hidden, false);
    assert.equal(app.elements['taxi-room-actions'].hidden, true);
    assert.equal(app.elements['taxi-refresh'].disabled, false);
    assert.equal(app.requests.length, 0); assert.equal(app.locations.length, 0);
    app.window.AppModules.taxi.init(); app.window.AppModules.taxi.init();
    assert.equal(app.elements['taxi-refresh'].listeners.get('click').length, 1);
});

test('an explicitly selected destination is required before GPS or any estimate request', async () => {
    const app = memoryUI();
    app.elements['dest-input'].value = '아직 선택하지 않은 검색어';
    await app.click();
    assert.equal(app.requests.length, 0); assert.equal(app.locations.length, 0);
    assert.match(app.text('taxi-content'), /목적지|도착/);
    assert.equal(app.elements['taxi-refresh'].disabled, false);
});

test('building mode requires a selected building and never requests GPS', async () => {
    const app = memoryUI(); app.chooseBuilding(''); app.chooseDestination(); await app.click();
    assert.equal(app.requests.length, 0); assert.equal(app.locations.length, 0);
    assert.match(app.text('taxi-content'), /건물/);
});

test('the taxi button sends only the building estimate and shows exact provider split amounts', async () => {
    const app = memoryUI(); prepare(app); await app.click();
    assert.equal(taxiCalls(app).length, 1); assert.equal(busCalls(app).length, 0); assert.equal(app.locations.length, 0);
    assert.deepEqual(taxiCalls(app)[0].payload, {
        location: '공학관', campus: 'hallym', dest_lat: destination.lat, dest_lon: destination.lon, dest_name: destination.name,
    });
    assert.equal(taxiCalls(app)[0].options.method, 'POST');
    assert.equal(taxiCalls(app)[0].options.credentials, 'same-origin');
    assert.match(app.text('taxi-content'), /택시 이동 약 10분/);
    assert.match(app.text('taxi-content'), /2\.5\s?km/);
    assert.match(app.text('taxi-content'), /10,001원/);
    assert.deepEqual(app.elements['taxi-content'].children.slice(0, 3).map(node => node.className),
        ['taxi-fare', 'taxi-split-section', 'taxi-duration']);
    const buttons = splitButtons(app);
    assert.deepEqual(buttons.map(button => button.textContent), ['2명', '3명', '4명']);
    assert.equal(taxiNode(app, 'taxi-split-options').getAttribute('role'), 'group');
    assert.deepEqual(buttons.map(button => button.getAttribute('aria-pressed')), ['true', 'false', 'false']);
    for (const [count, values] of [[2, [5001, 5000]], [3, [3334, 3334, 3333]], [4, [2501, 2500, 2500, 2500]]]) {
        await selectCount(app, count);
        const displayed = [...taxiNode(app, 'taxi-split-breakdown').textContent.matchAll(/([\d,]+)원/g)]
            .map(match => Number(match[1].replaceAll(',', '')));
        assert.deepEqual(displayed, values); assert.equal(displayed.reduce((sum, amount) => sum + amount, 0), 10001);
        assert.equal(buttons.filter(button => button.getAttribute('aria-pressed') === 'true').length, 1);
        assert.equal(buttons.find(button => button.getAttribute('aria-pressed') === 'true').dataset.count, String(count));
    }
    assert.match(taxiNode(app, 'taxi-split-amount').textContent, /2,500~2,501원/);
    assert.doesNotMatch(taxiNode(app, 'taxi-split-result').textContent, /내 몫|내가 낼/);
    assert.equal(taxiCalls(app).length, 1, 'Changing the assumed people count never requests a new quote');
});

test('equal server allocations show one amount and preserve the server order for remainder won', async () => {
    const app = memoryUI((request, count, quote) => {
        const body = quote(request.payload, { amount: count === 1 ? 12000 : 10001 });
        if (count === 2) body.split_preview.find(item => item.count === 3).amounts_krw = [3333, 3334, 3334];
        return body;
    });
    prepare(app); await app.click(); await selectCount(app, 3);
    assert.equal(taxiNode(app, 'taxi-split-amount').textContent, '4,000원');
    await app.click(); await selectCount(app, 3);
    assert.equal(taxiNode(app, 'taxi-split-amount').textContent, '3,333~3,334원');
    assert.equal(taxiNode(app, 'taxi-split-breakdown').textContent, '분담 예시 · 3,333원 / 3,334원 / 3,334원');
});

test('invalid server allocations disable selection without calculating replacement amounts', async () => {
    const invalid = [
        [{ count: 2, amounts_krw: [5000, 5000] }],
        [{ count: 2, amounts_krw: [10001] }],
        [{ count: 2, amounts_krw: [5001.5, 4999.5] }],
        [{ count: 2, amounts_krw: [-1, 10002] }],
        [{ count: 2, amounts_krw: [5003, 4998] }],
        [{ count: 2, amounts_krw: ['5001', 5000] }],
        [{ count: 2, amounts_krw: [5001, 5000] }, { count: 2, amounts_krw: [5001, 5000] }],
        [null], null,
    ];
    for (const split_preview of invalid) {
        const app = memoryUI((request, count, quote) => count === 1 ? quote(request.payload)
            : { ...quote(request.payload), split_preview });
        prepare(app); await app.click(); await selectCount(app, 3); await app.click();
        assert.match(app.text('taxi-content'), /10,001원/);
        assert.match(app.text('taxi-content'), /분담금 계산 불가/);
        assert.equal(splitButtons(app).length, 3);
        splitButtons(app).forEach(button => {
            assert.equal(button.disabled, true); assert.equal(button.getAttribute('aria-pressed'), 'false');
        });
        assert.equal(taxiNode(app, 'taxi-split-amount'), undefined);
        assert.doesNotMatch(app.text('taxi-content'), /3,333|3,334|5,000|5,001|NaN|undefined/);
    }
});

test('only valid server allocation choices can become selected', async () => {
    const app = memoryUI((request, count, quote) => {
        const body = quote(request.payload);
        body.split_preview = body.split_preview.filter(item => item.count !== 2);
        return body;
    });
    prepare(app); await app.click();
    assert.equal(splitButtons(app)[0].disabled, true);
    assert.deepEqual(splitButtons(app).map(button => button.getAttribute('aria-pressed')), ['false', 'true', 'false']);
    await selectCount(app, 2);
    assert.equal(taxiNode(app, 'taxi-split-label').textContent, '3명 가정 · 1인당 예상');
    await selectCount(app, 4);
    assert.equal(taxiNode(app, 'taxi-split-label').textContent, '4명 가정 · 1인당 예상');
});

test('the bus route action does not silently request a taxi estimate', async () => {
    const app = memoryUI(); prepare(app); await app.window.AppModules.api.findRoutes(); await flush();
    assert.equal(busCalls(app).length, 1); assert.equal(taxiCalls(app).length, 0);
    assert.equal(app.locations.length, 0);
});

test('GPS is acquired only after explicit taxi action and a fresh position is reused', async () => {
    const app = memoryUI(); app.chooseDestination(); await app.click();
    assert.equal(app.locations.length, 1); assert.equal(app.requests.length, 0);
    await app.location();
    assert.equal(taxiCalls(app).length, 1); assert.equal(busCalls(app).length, 0);
    assert.deepEqual(taxiCalls(app)[0].payload, { lat: 37.88695, lon: 127.73585, location: '내 실시간 위치',
        campus: 'hallym', dest_lat: destination.lat, dest_lon: destination.lon, dest_name: destination.name });
    await app.click();
    assert.equal(app.locations.length, 1); assert.equal(taxiCalls(app).length, 2);
});

test('denied GPS leaves no estimate and a later explicit retry can succeed', async () => {
    const app = memoryUI(); app.chooseDestination(); await app.click(); await app.denyLocation();
    assert.equal(app.requests.length, 0); assert.equal(app.elements['taxi-refresh'].disabled, false);
    assert.match(app.text('taxi-content') + app.text('origin-status'), /위치|권한/);
    await app.click(); assert.equal(app.locations.length, 2); await app.location(1);
    assert.equal(taxiCalls(app).length, 1);
});

test('a late GPS callback cannot query after the user switches to a building', async () => {
    const app = memoryUI(); app.chooseDestination(); await app.click();
    assert.equal(app.locations.length, 1);
    app.chooseBuilding(); await app.location();
    assert.equal(app.requests.length, 0);
    assert.equal(app.window.AppState.getOriginMode(), 'building');
    await app.click();
    assert.equal(taxiCalls(app).length, 1); assert.equal(taxiCalls(app)[0].payload.lat, undefined);
});

test('a taxi action shares a pending bus GPS lookup and both requests release their loading state', async () => {
    const app = memoryUI(); app.chooseDestination();
    const bus = app.window.AppModules.gps.findRoutesByGPS(); await flush();
    assert.equal(app.locations.length, 1); assert.equal(app.elements['route-submit'].disabled, true);
    await app.click();
    assert.equal(app.locations.length, 1, 'Two explicit actions can share one pending location lookup');
    assert.equal(app.elements['taxi-refresh'].disabled, true);
    await app.location(); await bus; await flush();
    assert.equal(busCalls(app).length, 1); assert.equal(taxiCalls(app).length, 1);
    assert.equal(app.elements['route-submit'].disabled, false);
    assert.equal(app.elements['taxi-refresh'].disabled, false);
    assert.equal(app.elements.loading.hidden, true);
    assert.match(app.text('taxi-content'), /10,001원/);
});

test('changing inputs cancels both consumers of a pending shared GPS lookup', async () => {
    const app = memoryUI(); app.chooseDestination();
    const bus = app.window.AppModules.gps.findRoutesByGPS(); await flush(); await app.click();
    assert.equal(app.locations.length, 1);
    app.chooseDestination({ ...destination, name: '변경된 합성 목적지' });
    await bus; await flush(); await app.location();
    assert.equal(app.requests.length, 0, 'The late callback must not start either old search');
    assert.equal(app.elements['route-submit'].disabled, false);
    assert.equal(app.elements['taxi-refresh'].disabled, false);
    assert.equal(app.elements.loading.hidden, true);
    await app.click(); assert.equal(app.locations.length, 2); await app.location(1);
    assert.equal(busCalls(app).length, 0); assert.equal(taxiCalls(app).length, 1);
    assert.equal(taxiCalls(app)[0].payload.dest_name, '변경된 합성 목적지');
});

for (const selection of ['destination', 'campus', 'building']) {
    test(`changing ${selection} aborts and discards an uncancellable previous estimate`, async () => {
        const old = deferred();
        const app = memoryUI((request, count, quote) => count === 1 ? old.promise : quote(request.payload, { amount: 20001 }));
        prepare(app); await app.click(); const request = taxiCalls(app)[0];
        assert.equal(app.elements['taxi-refresh'].disabled, true);
        if (selection === 'destination') app.chooseDestination({ ...destination, name: '새 합성 목적지', lat: 37.85 });
        if (selection === 'campus') app.chooseBuilding('공과대학1호관', 'knu');
        if (selection === 'building') app.chooseBuilding('도서관');
        assert.equal(request.signal.aborted, true);
        assert.equal(app.elements['taxi-section'].hidden, false);
        old.resolve(app.quote(request.payload, { amount: 12345 })); await flush();
        assert.doesNotMatch(app.text('taxi-content'), /12,345원/);
        await app.click();
        assert.equal(taxiCalls(app).length, 2); assert.equal(busCalls(app).length, 0);
        assert.match(app.text('taxi-content'), /20,001원/);
    });
}

test('partial and zero-or-missing fares keep time and distance without fake free splits', async () => {
    for (const amount of [null, 0, undefined]) {
        const app = memoryUI((request, count, quote) => {
            if (count === 1) return quote(request.payload);
            const body = quote(request.payload, { amount: null });
            body.status = 'partial'; body.code = 'TAXI_FARE_UNAVAILABLE';
            body.fare.amount_krw = amount;
            body.split_preview = [2, 3, 4].map(count => ({ count, amounts_krw: Array(count).fill(0) }));
            return body;
        });
        prepare(app); await app.click(); await selectCount(app, 4); await app.click();
        assert.match(app.text('taxi-content'), /택시 이동 약 10분/);
        assert.match(app.text('taxi-content'), /요금 조회 불가/);
        assert.doesNotMatch(app.text('taxi-content'), /0원|NaN|undefined/);
        assert.equal(splitButtons(app).length, 0);
        assert.equal(taxiNode(app, 'taxi-split-amount'), undefined);
        assert.doesNotMatch(app.text('taxi-content'), /2,500|2,501|10,001/);
    }
});

test('the backend no-travel result says travel is unnecessary and omits split prices', async () => {
    const app = memoryUI((request, count, quote) => count === 1 ? quote(request.payload) : ({ ...quote(request.payload, { amount: null }),
        status: 'no_travel_required', code: 'TAXI_NOT_NEEDED', ride_duration_seconds: 0, distance_m: 0,
        warnings: ['출발지와 도착지가 같아 택시 이동이 필요하지 않아요.'] }));
    prepare(app); await app.click(); await selectCount(app, 3); await app.click();
    assert.match(app.text('taxi-content'), /이동이 필요하지|이동 불필요/);
    assert.doesNotMatch(app.text('taxi-content'), /0원|NaN|undefined/);
    assert.equal(splitButtons(app).length, 0);
    assert.doesNotMatch(app.text('taxi-content'), /3,333|3,334|10,001/);
});

test('provider failures display a retry state and retry only on another button click', async () => {
    const app = memoryUI((request, count, quote) => count === 1
        ? { httpStatus: 503, body: { error: '검증용 연결 실패', code: 'TAXI_PROVIDER_NETWORK' } }
        : quote(request.payload));
    prepare(app); await app.click();
    assert.match(app.text('taxi-content'), /검증용 연결 실패/);
    assert.equal(app.elements['taxi-refresh'].disabled, false);
    await app.advance(60000); assert.equal(taxiCalls(app).length, 1);
    await app.click(); assert.equal(taxiCalls(app).length, 2);
    assert.match(app.text('taxi-content'), /10,001원/);
});

test('request timeout cancels work, enables retry and ignores its later response', async () => {
    const old = deferred();
    const app = memoryUI((request, count, quote) => count === 1 ? old.promise : quote(request.payload, { amount: 20001 }));
    prepare(app); await app.click(); await app.advance(15000);
    assert.equal(taxiCalls(app)[0].signal.aborted, true);
    assert.equal(app.elements['taxi-refresh'].disabled, false);
    assert.match(app.text('taxi-content'), /늦|시간|다시/);
    await app.click(); assert.match(app.text('taxi-content'), /20,001원/);
    old.resolve(app.quote({}, { amount: 12345 })); await flush();
    assert.doesNotMatch(app.text('taxi-content'), /12,345원/);
});

test('the deadline covers a stalled JSON body after successful response headers', async () => {
    const body = deferred();
    const app = memoryUI((request, count, quote) => count === 1
        ? { bodyPromise: body.promise } : quote(request.payload, { amount: 20001 }));
    prepare(app); await app.click();
    assert.equal(app.elements['taxi-refresh'].disabled, true);
    await app.advance(15000);
    assert.equal(taxiCalls(app)[0].signal.aborted, true);
    assert.equal(app.elements['taxi-refresh'].disabled, false);
    assert.match(app.text('taxi-content'), /늦|시간|다시/);
    await app.click(); assert.match(app.text('taxi-content'), /20,001원/);
    body.resolve(app.quote({}, { amount: 12345 })); await flush();
    assert.doesNotMatch(app.text('taxi-content'), /12,345원/);
    assert.equal(taxiCalls(app).length, 2);
});

test('expired estimates clear values without silently fetching again', async () => {
    const app = memoryUI((request, count, quote) => quote(request.payload, { ttl: 1 }));
    prepare(app); await app.click(); assert.match(app.text('taxi-content'), /10,001원/);
    await selectCount(app, 4);
    const oldButton = splitButtons(app)[1], oldResult = taxiNode(app, 'taxi-split-result');
    const previous = oldResult.textContent;
    await app.advance(1000);
    assert.match(app.text('taxi-content'), /만료/); assert.doesNotMatch(app.text('taxi-content'), /10,001원/);
    assert.equal(taxiCalls(app).length, 1); assert.equal(app.elements['taxi-refresh'].disabled, false);
    assert.equal(splitButtons(app).length, 0);
    await oldButton.emit('click');
    assert.equal(oldResult.textContent, previous, 'An expired detached selection cannot run again');
    assert.doesNotMatch(app.text('taxi-content'), /2,500|2,501|3,333|3,334/);
    await app.click(); assert.equal(taxiCalls(app).length, 2);
    assert.deepEqual(splitButtons(app).map(button => button.getAttribute('aria-pressed')), ['true', 'false', 'false']);
});

test('context changes clear a selected allocation and old controls cannot select a new quote', async () => {
    const app = memoryUI((request, count, quote) => quote(request.payload, { amount: count === 1 ? 10001 : 20001 }));
    prepare(app); await app.click(); await selectCount(app, 3);
    const oldButton = splitButtons(app)[2], oldResult = taxiNode(app, 'taxi-split-result');
    const previous = oldResult.textContent;
    app.chooseDestination({ ...destination, name: '새 합성 목적지' });
    assert.equal(splitButtons(app).length, 0);
    assert.doesNotMatch(app.text('taxi-content'), /10,001|3,333|3,334/);
    await app.click(); await oldButton.emit('click');
    assert.equal(oldResult.textContent, previous);
    assert.match(app.text('taxi-content'), /20,001원/);
    assert.deepEqual(splitButtons(app).map(button => button.getAttribute('aria-pressed')), ['true', 'false', 'false']);
});

test('loading and failed refreshes remove the old selected fare and allocation', async () => {
    const pending = deferred();
    const app = memoryUI((request, count, quote) => count === 1 ? quote(request.payload) : pending.promise);
    prepare(app); await app.click(); await selectCount(app, 3); await app.click();
    assert.equal(splitButtons(app).length, 0);
    assert.doesNotMatch(app.text('taxi-content'), /10,001|3,333|3,334/);
    pending.resolve({ httpStatus: 503, body: { error: '검증용 연결 실패' } }); await flush();
    assert.equal(splitButtons(app).length, 0);
    assert.match(app.text('taxi-content'), /검증용 연결 실패/);
    assert.doesNotMatch(app.text('taxi-content'), /10,001|3,333|3,334/);
});

test('an old expiry timer cannot erase a new selection estimate', async () => {
    const app = memoryUI((request, count, quote) => quote(request.payload, { ttl: count === 1 ? 1 : 60, amount: count === 1 ? 10001 : 20001 }));
    prepare(app); await app.click(); await app.advance(500);
    app.chooseBuilding('도서관'); await app.click(); await app.advance(500);
    assert.match(app.text('taxi-content'), /20,001원/); assert.doesNotMatch(app.text('taxi-content'), /만료/);
    assert.equal(taxiCalls(app).length, 2);
});

test('route names and server warnings render as text rather than executable HTML', async () => {
    const injection = '<img src=x onerror="window.injected=true">';
    const app = memoryUI((request, count, quote) => ({ ...quote(request.payload), warnings: [injection] }));
    app.chooseBuilding(); app.chooseDestination({ ...destination, name: injection }); await app.click();
    assert.ok(app.text('taxi-route').includes(injection)); assert.ok(app.text('taxi-content').includes(injection));
    assert.equal(descendants(app.elements['taxi-content']).some(node => node.tagName === 'IMG'), false);
});

(async () => {
    let failed = 0;
    for (const item of tests) {
        try { await item.run(); console.log('PASS ' + item.name); }
        catch (error) { failed++; console.error('FAIL ' + item.name + '\n' + (error.stack || error)); }
    }
    console.log(`\n${tests.length - failed}/${tests.length} taxi controller behavior checks passed. No browser or real provider was used.`);
    if (failed) process.exitCode = 1;
})();
