/** Node-only checks of the production intercity controller with an in-memory DOM.
 * No browser, Flask import, credentials, external requests or live timetable data.
 * Run the companion intercity_frontend_smoke.cjs for browser/layout coverage.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const project = path.resolve(__dirname, '..');
const markup = fs.readFileSync(path.join(project, 'templates/index.html'), 'utf8');
const controller = fs.readFileSync(path.join(project, 'static/js/intercity-timetable.js'), 'utf8');
const helper = fs.readFileSync(path.join(__dirname, 'frontend_smoke.cjs'), 'utf8');
const fixtureStart = helper.indexOf('function intercityFixture(');
const fixtureEnd = helper.indexOf('\nasync function noOverflow(', fixtureStart);
assert.ok(fixtureStart >= 0 && fixtureEnd > fixtureStart);
const fixtureCode = helper.slice(fixtureStart, fixtureEnd);
const tests = [];
const test = (name, run) => tests.push({ name, run });

function fixture(request) {
    return JSON.parse(JSON.stringify(vm.runInNewContext(fixtureCode + '\nintercityFixture(pathname, params)', {
        pathname: request.pathname, params: request.params, URLSearchParams,
    })));
}

async function flush() { for (let i = 0; i < 40; i++) await Promise.resolve(); }

function memoryUI(handler = request => fixture(request)) {
    class Element {
        constructor(tag = 'div') {
            this.tagName = tag; this.children = []; this.listeners = new Map(); this.dataset = {};
            this.attributes = {}; this._value = ''; this._text = ''; this.disabled = false; this.hidden = false;
        }
        get value() { return this._value; }
        set value(value) {
            this._value = this.tagName === 'select' && !this.children.some(item => item.value === value) ? '' : String(value);
        }
        get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
        set textContent(value) { this._text = String(value); this.children = []; }
        set innerHTML(value) { throw new Error('Unexpected HTML insertion: ' + value); }
        replaceChildren(...children) {
            this._text = ''; this.children = [...children];
            if (this.tagName === 'select') this.value = children[0]?.value || '';
        }
        append(...children) { this.children.push(...children); }
        setAttribute(name, value) { this.attributes[name] = String(value); }
        addEventListener(type, callback) { this.listeners.set(type, [...(this.listeners.get(type) || []), callback]); }
        async emit(type) {
            for (const callback of this.listeners.get(type) || []) await callback({ preventDefault() {} });
            await flush();
        }
    }
    const elements = {};
    for (const match of markup.matchAll(/<(\w+)\b([^>]*\bid="intercity-([^"]+)"[^>]*)>/g)) {
        const element = new Element(match[1]);
        element.disabled = /\bdisabled\b/.test(match[2]);
        element.hidden = /\bhidden\b/.test(match[2]);
        element.readOnly = /\breadonly\b/.test(match[2]);
        elements[match[3]] = element;
    }
    const root = elements.timetable, panel = new Element('section'); panel.hidden = true;
    assert.ok(root, 'The controller root is present in production markup');
    root.querySelector = selector => elements[selector.replace('#intercity-', '')] || null;
    const requests = []; let observe;
    const context = {
        document: {
            getElementById: id => id === 'intercity-timetable' ? root : id === 'transport-panel-intercity' ? panel : null,
            createElement: tag => new Element(tag),
        },
        MutationObserver: class { constructor(callback) { observe = callback; } observe() {} },
        Intl, Date, URLSearchParams, AbortController, Error,
        fetch: async (url, options) => {
            const parsed = new URL(url, 'http://memory.invalid');
            const request = { pathname: parsed.pathname, params: parsed.searchParams, signal: options.signal };
            requests.push(request);
            const response = await handler(request, requests.length);
            return { ok: !response?.httpStatus || response.httpStatus < 400,
                json: async () => response?.body === undefined ? response : response.body };
        },
    };
    vm.runInNewContext(controller, context);
    return {
        elements, requests, root, text: id => elements[id].textContent,
        async open() { panel.hidden = false; observe(); await flush(); },
        async reopen() { panel.hidden = true; observe(); panel.hidden = false; observe(); await flush(); },
        async change(id, value) { elements[id].value = value; await elements[id].emit('change'); },
        async filter(id, value) { elements[id + '-filter'].value = value; await elements[id + '-filter'].emit('input'); },
        async includeStops(value) { elements['include-stops'].checked = value; await elements['include-stops'].emit('change'); },
        async click(id) { await elements[id].emit('click'); },
        async submit() { await elements.form.emit('submit'); },
    };
}

const requestsFor = (app, path) => app.requests.filter(item => item.pathname.endsWith('/' + path));
const latestRequest = (app, path) => requestsFor(app, path).at(-1);

async function chooseDestination(app, destination = 'TEST_DSE') {
    await app.change('destination', destination);
}

async function chooseRoute(app, destination = 'TEST_DSE') {
    await app.open(); await chooseDestination(app, destination);
}

test('lazy initialization auto-loads Chuncheon destinations once and keeps server date', async () => {
    const app = memoryUI();
    assert.equal(app.requests.length, 0);
    assert.equal(app.elements.load.disabled, true);
    await app.open();
    assert.deepEqual(app.requests.map(item => item.pathname), ['/api/intercity/config', '/api/intercity/terminals', '/api/intercity/destinations']);
    assert.equal(latestRequest(app, 'destinations').params.get('departure_id'), 'TEST_CTN');
    assert.equal(app.elements.date.value, '2030-10-21');
    assert.equal(app.elements.date.readOnly, true);
    assert.equal(app.elements.origin.value, 'TEST_CTN');
    assert.equal(app.elements.origin.children.length, 2);
    assert.equal(app.elements.destination.value, '');
    assert.equal(app.elements.destination.children.length, 3);
    assert.deepEqual(app.elements.destination.children.slice(1).map(item => item.value), ['TEST_DSE', 'TEST_OTHER']);
    assert.equal(app.text('destination-heading'), '춘천 출발 도착지');
    assert.match(app.text('destination-hint'), /도착지 2곳/);
    assert.deepEqual(app.elements.destination.children.slice(1).map(item => item.textContent), ['동서울', '테스트 터미널']);
    assert.equal(app.elements['include-stops'].checked, false);
    assert.equal(app.elements['additional-option'].hidden, true);
    assert.equal(app.elements.destination.disabled, false);
    assert.equal(app.elements.load.disabled, true);
    await app.reopen(); await app.reopen();
    assert.equal(app.requests.length, 3);
    for (const element of Object.values(app.elements)) {
        for (const callbacks of element.listeners.values()) assert.equal(callbacks.length, 1);
    }
    assert.match(markup, /id="intercity-status"[^>]*role="status"[^>]*aria-live="polite"/);
    assert.match(markup, /오늘 출발 시간표를 조회합니다/);
    assert.doesNotMatch(markup, /공공데이터는 오늘 배차정보만 제공합니다/);
    assert.match(markup, /filename='js\/intercity-timetable\.js'/);
    assert.match(markup, /filename='css\/intercity-timetable\.css'/);
});

test('direct destination selection, sorted cards and a verified reverse connection work', async () => {
    const app = memoryUI(); await chooseRoute(app);
    assert.equal(app.elements.load.disabled, false);
    await app.submit();
    assert.deepEqual(Object.fromEntries(latestRequest(app, 'timetable').params), { departure_id: 'TEST_CTN', arrival_id: 'TEST_DSE', date: '2030-10-21' });
    assert.equal(app.elements.results.hidden, false);
    assert.equal(app.elements.trips.children.length, 2);
    assert.match(app.elements.trips.children[0].textContent, /09:00/);
    assert.equal((app.elements.trips.children[0].textContent.match(/미제공/g) || []).length, 3);
    assert.match(app.elements.trips.children[1].textContent, /10:00.*11:30/);
    assert.match(app.elements.trips.children[1].textContent, /도착 예상/);
    assert.match(app.elements.trips.children[1].textContent, /12,300원/);
    assert.equal(app.elements.status.dataset.state, 'ok');
    await app.click('swap');
    assert.equal(app.elements.origin.value, 'TEST_DSE');
    assert.equal(app.elements.destination.value, 'TEST_CTN');
    assert.equal(app.elements.origin.children.length, 2);
    assert.equal(app.elements.destination.children.length, 3);
    assert.equal(latestRequest(app, 'destinations').params.get('departure_id'), 'TEST_DSE');
    assert.equal(app.elements.results.hidden, true);
    assert.equal(app.elements.trips.children.length, 0);
    assert.equal(app.elements['origin-filter'].value, '');
    assert.equal(app.elements['destination-filter'].value, '');
    assert.equal(app.text('destination-heading'), '동서울 출발 도착지');
    await app.change('destination', 'TEST_DSE');
    assert.equal(app.elements.destination.value, '', 'The origin is not an eligible arrival in this fixture');
    assert.equal(app.elements.load.disabled, true);
    const count = app.requests.length; await app.submit();
    assert.equal(app.requests.length, count);
});

test('all returned destinations exceed 30 freely, while nationwide origin search remains bounded', async () => {
    const generated = Array.from({ length: 2246 }, (_, index) => ({
        id: 'TEST_WIDE_' + index, name: '합성 터미널 ' + String(index).padStart(4, '0'), city: '합성도',
    }));
    const app = memoryUI(request => {
        const result = fixture(request);
        if (request.pathname.endsWith('/terminals')) result.terminals.push(...generated);
        if (request.pathname.endsWith('/destinations')) result.destinations = generated.slice(0, 65);
        return result;
    });
    await app.open();
    assert.equal(app.elements.origin.children.length, 2);
    assert.equal(app.elements.destination.children.length, 66, 'All 65 registered destinations plus one placeholder must appear immediately');
    assert.equal(app.elements.destination.value, '');
    assert.match(app.text('destination-hint'), /도착지 65곳/);
    await app.change('destination', 'TEST_WIDE_64');
    assert.equal(app.elements.load.disabled, false, 'A destination beyond the first 30 needs no search');
    await app.filter('destination', '합성');
    assert.equal(app.elements.destination.children.length, 66, 'Optional destination filtering has no candidate cap');
    await app.filter('destination', '');
    assert.equal(app.elements.destination.children.length, 66);
    assert.equal(app.elements.destination.value, 'TEST_WIDE_64');
    assert.equal(requestsFor(app, 'destinations').length, 1);
    await app.filter('origin', '합성');
    assert.equal(app.elements.origin.children.length, 31, 'Only nationwide origin search keeps the 30-candidate cap');
    assert.match(app.text('origin-hint'), /검색 결과 2,?246곳/);
    assert.equal(app.elements.destination.children.length, 1, 'Clearing the selected origin clears its old destinations');
    assert.equal(app.elements.destination.disabled, true);
    assert.equal(requestsFor(app, 'destinations').length, 1, 'Typing without choosing an origin must not request destinations');
});

test('destination names stay clean and duplicate names use their region without technical flags', async () => {
    const app = memoryUI(request => {
        const result = fixture(request);
        if (request.pathname.endsWith('/destinations')) {
            result.destinations.push(
                { id: 'TEST_SAME_A', name: '동명 터미널', city: '합성남도', booking_mode: 'one_way', match_status: 'matched' },
                { id: 'TEST_SAME_B', name: '동명 터미널', city: '합성북도', booking_mode: 'on_site', match_status: 'matched' },
            );
        }
        return result;
    });
    await app.open();
    assert.equal(app.elements.destination.children.length, 5);
    const choices = app.elements.destination.children.slice(1);
    assert.ok(choices.every(item => item.value && !item.disabled));
    assert.deepEqual(choices.filter(item => item.textContent.startsWith('동명')).map(item => item.textContent), ['동명 터미널 · 합성남도', '동명 터미널 · 합성북도']);
    assert.equal(choices.find(item => item.value === 'TEST_DSE').textContent, '동서울');
    assert.doesNotMatch(app.text('destination'), /인터넷 예매|현장 구매|연결 확인|matched|SOURCE_/);
    await app.change('destination', 'TEST_SAME_B'); await app.submit();
    assert.equal(latestRequest(app, 'timetable').params.get('arrival_id'), 'TEST_SAME_B');
    assert.equal(app.elements.results.hidden, false);
});

test('other stops require explicit opt-in and server classification wins over name heuristics', async () => {
    const app = memoryUI(request => {
        const result = fixture(request);
        if (request.pathname.endsWith('/destinations')) {
            result.destinations.push({ id: 'TEST_MAIN', name: '합성 목적지', city: '합성시' });
            result.additional_stops = [{ id: 'TEST_STOP', name: '합성 터미널 정류소', city: '합성시' }];
        }
        return result;
    });
    await app.open();
    assert.equal(app.elements.destination.children.length, 4);
    assert.ok(app.elements.destination.children.some(item => item.value === 'TEST_MAIN'), 'A verified destination need not end with 터미널');
    assert.ok(!app.elements.destination.children.some(item => item.value === 'TEST_STOP'), 'The extra stop stays hidden even though its name contains 터미널');
    assert.equal(app.elements['additional-option'].hidden, false);
    assert.equal(app.elements['include-stops'].checked, false);
    await app.filter('destination', '정류소');
    assert.equal(app.elements.destination.children.length, 1, 'Search does not implicitly opt into other stops');
    await app.includeStops(true);
    assert.equal(app.elements.destination.children.length, 2);
    await app.change('destination', 'TEST_STOP');
    assert.equal(app.elements.load.disabled, false);
    await app.submit();
    assert.equal(latestRequest(app, 'timetable').params.get('arrival_id'), 'TEST_STOP');
    await app.filter('destination', '');
    assert.equal(app.elements.destination.children.length, 5);
    await app.includeStops(false);
    assert.equal(app.elements.destination.children.length, 4);
    assert.equal(app.elements.destination.value, '');
    assert.equal(app.elements.load.disabled, true);
    assert.equal(requestsFor(app, 'destinations').length, 1, 'Opt-in is local and does not re-request routes');
});

test('opting out of other stops aborts and invalidates an in-flight stop timetable', async () => {
    let release;
    const app = memoryUI(request => {
        const result = fixture(request);
        if (request.pathname.endsWith('/destinations')) result.additional_stops = [{ id: 'TEST_STOP', name: '합성 하차지', city: '합성시' }];
        if (request.pathname.endsWith('/timetable')) return new Promise(resolve => { release = () => resolve(result); });
        return result;
    });
    await app.open(); await app.includeStops(true); await app.change('destination', 'TEST_STOP');
    const pending = app.submit(); await flush();
    const oldRequest = latestRequest(app, 'timetable');
    await app.includeStops(false);
    assert.equal(oldRequest.signal.aborted, true);
    assert.equal(app.elements.destination.value, '');
    release(); await pending;
    assert.equal(app.elements.results.hidden, true);
    assert.equal(app.elements.trips.children.length, 0);
    assert.equal(app.elements.load.disabled, true);
});

test('an additional-only response remains opt-in and can be queried after checking the control', async () => {
    const app = memoryUI(request => {
        const result = fixture(request);
        if (request.pathname.endsWith('/destinations')) {
            result.additional_stops = result.destinations; result.destinations = [];
        }
        return result;
    });
    await app.open();
    assert.equal(app.elements.destination.children.length, 1);
    assert.equal(app.elements.destination.disabled, true);
    assert.equal(app.elements['include-stops'].disabled, false);
    assert.equal(app.elements['additional-option'].hidden, false);
    assert.match(app.text('status'), /다른 하차지도 보기/);
    await app.includeStops(true); await chooseDestination(app); await app.submit();
    assert.equal(app.elements.results.hidden, false);
});

test('optional destination filtering only searches the current list and clearing restores all entries', async () => {
    const app = memoryUI(); await chooseRoute(app); await app.submit();
    await app.filter('destination', '동서울');
    assert.equal(app.elements.destination.children.length, 2);
    assert.equal(app.elements.destination.value, 'TEST_DSE');
    assert.equal(app.elements.results.hidden, true);
    await app.filter('destination', '');
    assert.equal(app.elements.destination.children.length, 3);
    assert.equal(app.elements.destination.value, 'TEST_DSE');
    await app.filter('destination', '춘천');
    assert.equal(app.elements.destination.children.length, 1, 'The nationwide catalogue must not leak into destination search');
    assert.equal(app.elements.destination.value, '');
    assert.match(app.text('destination'), /검색에 맞는 도착지가 없어요/);
    assert.equal(app.elements.load.disabled, true);
    assert.equal(app.elements.swap.disabled, true);
    assert.equal(requestsFor(app, 'destinations').length, 1);
});

test('origin changes reload the bound destination list, but retaining the same origin does not', async () => {
    const app = memoryUI(); await chooseRoute(app);
    await app.filter('origin', '춘천'); await app.change('origin', 'TEST_CTN'); await app.filter('origin', '');
    assert.equal(requestsFor(app, 'destinations').length, 1);
    assert.equal(app.elements.destination.value, 'TEST_DSE');
    await app.filter('origin', '동서울');
    assert.equal(app.elements.origin.value, '');
    assert.equal(app.elements.destination.value, '');
    assert.equal(app.elements.destination.children.length, 1);
    assert.equal(app.elements.load.disabled, true);
    await app.change('origin', 'TEST_DSE');
    assert.equal(requestsFor(app, 'destinations').length, 2);
    assert.equal(latestRequest(app, 'destinations').params.get('departure_id'), 'TEST_DSE');
    assert.equal(app.text('destination-heading'), '동서울 출발 도착지');
    assert.equal(app.elements.destination.value, '');
    assert.ok(app.elements.destination.children.some(item => item.value === 'TEST_CTN'));
    assert.ok(!app.elements.destination.children.some(item => item.value === 'TEST_DSE'));
});

test('a late destination response cannot overwrite a newer origin even when the mock ignores abort', async () => {
    let release;
    const app = memoryUI(request => {
        if (request.pathname.endsWith('/destinations') && request.params.get('departure_id') === 'TEST_CTN') {
            return new Promise(resolve => { release = () => resolve(fixture(request)); });
        }
        return fixture(request);
    });
    await app.open();
    assert.equal(app.elements.destination.disabled, true);
    assert.equal(app.root.attributes['aria-busy'], 'true');
    const oldRequest = latestRequest(app, 'destinations');
    await app.filter('origin', '동서울'); await app.change('origin', 'TEST_DSE');
    assert.equal(oldRequest.signal.aborted, true);
    assert.equal(app.elements.destination.disabled, false);
    const current = app.text('destination');
    release(); await flush();
    assert.equal(app.text('destination'), current);
    assert.equal(app.text('destination-heading'), '동서울 출발 도착지');
    assert.equal(app.root.attributes['aria-busy'], 'false');
    await app.change('destination', 'TEST_CTN'); await app.submit();
    assert.equal(latestRequest(app, 'timetable').params.get('departure_id'), 'TEST_DSE');
});

test('destination failures offer a scoped retry with no nationwide fallback', async () => {
    for (const failure of [
        () => ({ httpStatus: 502, body: { error: '등록 도착지 제공 서비스에 연결하지 못했습니다.' } }),
        () => { throw new Error('Synthetic destination network failure'); },
        () => ({ status: 'ok', departure: { id: 'TEST_CTN' }, destinations: null }),
        () => ({ status: 'ok', departure: { id: 'WRONG_ORIGIN' }, destinations: [] }),
        () => ({ status: 'ok', departure: { id: 'TEST_CTN' }, destinations: [{ id: null, name: '연결되지 않은 합성 항목' }], additional_stops: [] }),
        () => ({ status: 'ok', departure: { id: 'TEST_CTN' }, destinations: [], additional_stops: [{ id: null, name: '연결되지 않은 합성 하차지' }] }),
        () => ({ status: 'ok', departure: { id: 'TEST_CTN' }, destinations: [{ id: 'TEST_DSE', name: '합성 도착지' }], additional_stops: [{ id: 'TEST_DSE', name: '중복 ID' }] }),
    ]) {
        let fail = true;
        const app = memoryUI(request => request.pathname.endsWith('/destinations') && fail ? failure() : fixture(request));
        await app.open();
        assert.equal(app.elements.status.dataset.state, 'error');
        assert.equal(app.elements.destination.children.length, 1);
        assert.equal(app.elements.destination.disabled, true);
        assert.equal(app.elements.origin.disabled, false);
        assert.equal(app.elements['destinations-retry'].hidden, false);
        assert.equal(app.elements.retry.hidden, true);
        assert.equal(app.elements.load.disabled, true);
        fail = false; await app.click('destinations-retry');
        assert.equal(requestsFor(app, 'config').length, 1);
        assert.equal(requestsFor(app, 'terminals').length, 1);
        assert.equal(requestsFor(app, 'destinations').length, 2);
        assert.equal(app.elements.destination.children.length, 3);
        assert.equal(app.elements['destinations-retry'].hidden, true);
    }
});

test('empty registered destinations remain separate from absence of actual bus service', async () => {
    const app = memoryUI(request => {
        const result = fixture(request);
        if (request.pathname.endsWith('/destinations')) { result.status = 'empty'; result.destinations = []; }
        return result;
    });
    await app.open();
    assert.equal(app.elements.status.dataset.state, 'empty');
    assert.match(app.text('status'), /도착지를 확인하지 못했어요/);
    assert.match(app.text('status'), /실제 운행 여부/);
    assert.equal(app.elements.destination.children.length, 1);
    assert.equal(app.elements.destination.disabled, true);
    assert.equal(app.elements.load.disabled, true);
    assert.equal(requestsFor(app, 'timetable').length, 0);
});

test('swap never fabricates a reverse connection absent from the new origin list', async () => {
    const app = memoryUI(request => {
        const result = fixture(request);
        if (request.pathname.endsWith('/destinations') && request.params.get('departure_id') === 'TEST_DSE') {
            result.destinations = result.destinations.filter(item => item.id !== 'TEST_CTN');
        }
        return result;
    });
    await chooseRoute(app); await app.click('swap');
    assert.equal(app.elements.origin.value, 'TEST_DSE');
    assert.equal(app.elements.destination.value, '');
    assert.equal(app.elements.destination.children.length, 2);
    assert.ok(!app.elements.destination.children.some(item => item.value === 'TEST_CTN'));
    assert.match(app.text('status'), /이전 출발지가 없어 자동 선택하지 않았어요/);
    assert.equal(app.elements.load.disabled, true);
    assert.equal(requestsFor(app, 'destinations').length, 2);
});

test('empty timetable does not claim no bus service and keeps official booking options', async () => {
    const app = memoryUI(request => {
        const result = fixture(request);
        if (request.pathname.endsWith('/timetable')) { result.status = 'empty'; result.trips = []; }
        return result;
    });
    await chooseRoute(app); await app.submit();
    assert.equal(app.elements.status.dataset.state, 'empty');
    assert.equal(app.elements.results.hidden, true);
    assert.match(app.text('status'), /조회 결과가 없어요/);
    assert.match(app.text('status'), /데이터 미제공이나 배차 갱신 지연/);
    assert.doesNotMatch(app.text('status'), /운행하지|운행 없음/);
    assert.match(markup, /href="https:\/\/www\.bustago\.or\.kr\/newweb\/kr\/index\.do"/);
    assert.match(markup, /href="https:\/\/txbus\.t-money\.co\.kr\/"/);
});

test('missing configuration avoids all provider queries and explicit retry can recover', async () => {
    let configured = false;
    const app = memoryUI(request => {
        const result = fixture(request);
        if (request.pathname.endsWith('/config')) result.configured = configured;
        return result;
    });
    await app.open();
    assert.equal(app.requests.length, 1);
    assert.equal(app.elements.status.dataset.state, 'error');
    assert.match(app.text('status'), /연결 준비 중/);
    assert.equal(app.elements.retry.hidden, false);
    assert.equal(app.elements.origin.disabled, true);
    configured = true; await app.click('retry');
    assert.equal(app.requests.length, 4);
    assert.equal(app.elements.retry.hidden, true);
    assert.equal(app.elements.origin.disabled, false);
    await chooseDestination(app); await app.submit();
    assert.equal(app.elements.results.hidden, false);
});

test('catalogue and timetable provider authentication or malformed responses stay errors', async () => {
    for (const failure of [
        () => ({ httpStatus: 503, body: { status: 'error', code: 'INTERCITY_AUTH', error: '시외버스 API 인증을 확인해 주세요.' } }),
        () => { throw new Error('Synthetic catalogue network failure'); },
        () => ({ status: 'ok', terminals: null }),
    ]) {
        let fail = true;
        const app = memoryUI(request => request.pathname.endsWith('/terminals') && fail ? failure() : fixture(request));
        await app.open();
        assert.equal(app.elements.status.dataset.state, 'error');
        assert.equal(app.elements.load.disabled, true);
        assert.equal(app.elements.retry.hidden, false);
        fail = false; await app.click('retry');
        assert.equal(app.elements.origin.disabled, false);
        assert.equal(app.elements.destination.disabled, false);
    }
    const app = memoryUI(request => request.pathname.endsWith('/timetable')
        ? { httpStatus: 503, body: { error: '시외버스 API 인증을 확인해 주세요.' } } : fixture(request));
    await chooseRoute(app); await app.submit();
    assert.equal(app.elements.status.dataset.state, 'error');
    assert.match(app.text('status'), /인증/);
    assert.equal(app.elements.results.hidden, true);
    assert.equal(app.elements.load.disabled, false);
});

test('destination and timetable provider strings render as text without HTML insertion', async () => {
    const injection = '<img src=x onerror="window.__injected=true">';
    const app = memoryUI(request => {
        const result = fixture(request);
        if (request.pathname.endsWith('/destinations')) {
            result.destinations.find(item => item.id === 'TEST_DSE').name = injection;
            result.warnings = [injection];
        }
        if (request.pathname.endsWith('/timetable')) {
            result.status = 'partial'; result.warnings = [injection]; result.trips[0].grade = injection;
        }
        return result;
    });
    await chooseRoute(app); await app.submit();
    assert.equal(app.elements.status.dataset.state, 'partial');
    assert.equal(app.elements.warnings.hidden, false);
    assert.match(app.text('warnings'), /일부 운행정보/);
    assert.ok(app.text('destinations-warnings').includes(injection));
    assert.ok(app.text('warnings').includes(injection));
    assert.ok(app.text('trips').includes(injection));
    assert.ok(app.text('results-heading').includes(injection), 'Destination display names come from the current registered list');
});

test('destination changes abort and invalidate a late timetable even when the mock ignores abort', async () => {
    let release, queryCount = 0;
    const app = memoryUI(request => {
        if (request.pathname.endsWith('/timetable') && ++queryCount === 1) {
            return new Promise(resolve => { release = () => resolve(fixture(request)); });
        }
        return fixture(request);
    });
    await chooseRoute(app);
    const pending = app.submit(); await flush();
    assert.equal(app.elements.load.disabled, true);
    assert.equal(app.root.attributes['aria-busy'], 'true');
    const count = app.requests.length; await app.submit();
    assert.equal(app.requests.length, count, 'Repeated submit while loading must not duplicate the query');
    const oldRequest = latestRequest(app, 'timetable');
    await chooseDestination(app, 'TEST_OTHER');
    assert.equal(oldRequest.signal.aborted, true);
    assert.equal(app.elements.results.hidden, true);
    await app.submit();
    const current = app.text('results-heading');
    assert.match(current, /테스트 터미널/);
    release(); await pending;
    assert.equal(app.text('results-heading'), current);
    assert.equal(app.elements.status.dataset.state, 'ok');
    assert.equal(app.root.attributes['aria-busy'], 'false');
    assert.equal(requestsFor(app, 'destinations').length, 1);
});

test('an origin change aborts its previous timetable before the new destination list is ready', async () => {
    let release;
    const app = memoryUI(request => request.pathname.endsWith('/timetable')
        ? new Promise(resolve => { release = () => resolve(fixture(request)); }) : fixture(request));
    await chooseRoute(app);
    const pending = app.submit(); await flush();
    const oldRequest = latestRequest(app, 'timetable');
    await app.filter('origin', '동서울'); await app.change('origin', 'TEST_DSE');
    assert.equal(oldRequest.signal.aborted, true);
    assert.equal(app.elements.destination.value, '');
    release(); await pending;
    assert.equal(app.elements.results.hidden, true);
    assert.equal(app.elements.trips.children.length, 0);
    assert.equal(app.text('destination-heading'), '동서울 출발 도착지');
});

test('midnight rollover takes server today and asks for an explicit new query', async () => {
    const app = memoryUI(request => request.pathname.endsWith('/timetable')
        ? { httpStatus: 400, body: { error: '오늘만 조회할 수 있습니다.', today: '2030-10-22' } } : fixture(request));
    await chooseRoute(app); await app.submit();
    assert.equal(app.elements.date.value, '2030-10-22');
    assert.match(app.text('status'), /날짜가 바뀌었어요/);
    assert.equal(app.elements.results.hidden, true);
    assert.equal(app.elements.load.disabled, false);
    assert.equal(requestsFor(app, 'timetable').length, 1);
});

test('invalid timetable formats do not show stale or misleading cards', async () => {
    for (const modify of [
        result => { result.trips = null; },
        result => { result.date = '2030-10-20'; },
        result => { result.trips[0].departure_at = 'invalid date'; },
        result => { result.status = 'unexpected'; },
    ]) {
        let bad = false;
        const app = memoryUI(request => {
            const result = fixture(request);
            if (request.pathname.endsWith('/timetable') && bad) modify(result);
            return result;
        });
        await chooseRoute(app); await app.submit();
        assert.equal(app.elements.results.hidden, false);
        bad = true; await app.submit();
        assert.equal(app.elements.status.dataset.state, 'error');
        assert.equal(app.elements.results.hidden, true);
        assert.equal(app.elements.trips.children.length, 0);
        assert.match(app.text('status'), /응답 형식/);
    }
});

test('no default or destination request is invented for names merely containing Chuncheon', async () => {
    const app = memoryUI(request => {
        const result = fixture(request);
        if (request.pathname.endsWith('/terminals')) {
            result.terminals = result.terminals.filter(item => item.id !== 'TEST_CTN');
            result.terminals.push(
                { id: 'TEST_NEARBY_A', name: '남춘천', city: '춘천시' },
                { id: 'TEST_NEARBY_B', name: '춘천 합성 정류소', city: '춘천시' },
            );
        }
        return result;
    });
    await app.open();
    assert.equal(app.elements.origin.value, '');
    assert.equal(app.elements.origin.children.length, 1);
    assert.equal(app.elements.destination.children.length, 1);
    assert.equal(app.elements.destination.disabled, true);
    assert.equal(requestsFor(app, 'destinations').length, 0);
    assert.equal(app.elements.load.disabled, true);
    await app.filter('origin', '남춘천'); await app.change('origin', 'TEST_NEARBY_A');
    assert.equal(app.elements.origin.value, 'TEST_NEARBY_A');
    assert.equal(latestRequest(app, 'destinations').params.get('departure_id'), 'TEST_NEARBY_A');
});

(async () => {
    let passed = 0;
    for (const { name, run } of tests) {
        try { await run(); passed += 1; console.log('PASS ' + name); }
        catch (error) { console.error('FAIL ' + name); throw error; }
    }
    console.log(`Passed ${passed} intercity controller checks (in-memory DOM, synthetic fixtures).`);
})().catch(error => { console.error(error); process.exitCode = 1; });
