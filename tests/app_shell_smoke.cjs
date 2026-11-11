/** Node-only production shell/intro/demo checks. No Flask, .env, browser or network. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const project = path.resolve(__dirname, '..');
const markup = fs.readFileSync(path.join(project, 'templates/index.html'), 'utf8');
const source = name => fs.readFileSync(path.join(project, 'static/js', name + '.js'), 'utf8');
const tests = [], test = (name, run) => tests.push({ name, run });
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

function harness({ width = 1440, seen = false, storageFails = false, introFails = false } = {}) {
    let document, context, now = 0, nextTimer = 0, reloads = 0, confirmations = 0, accept = false, resizes = 0;
    const timers = new Map(), observers = [], resizeObservers = [], ids = new Map(), windowListeners = new Map();
    let mapSize = null;
    const storage = new Map(seen ? [['commute-master:intro-seen', '1']] : []);
    class Element {
        constructor(tag, attrs = {}) {
            this.tagName = tag; this.attributes = { ...attrs }; this.children = []; this.parentElement = null;
            this.listeners = new Map(); this.dataset = {}; this._text = ''; this._hidden = 'hidden' in attrs;
            this.value = attrs.value || ''; this.disabled = 'disabled' in attrs; this.inert = false; this.open = false;
            this.scrollTop = 0; this.scrollHeight = 100; this.clientHeight = 100;
            this.style = {}; this.id = attrs.id || ''; this.className = attrs.class || '';
            this.classList = {
                contains: name => this.className.split(/\s+/).includes(name),
                toggle: (name, state) => {
                    const values = new Set(this.className.split(/\s+/).filter(Boolean));
                    const enabled = state ?? !values.has(name);
                    if (enabled) values.add(name); else values.delete(name);
                    this.className = [...values].join(' '); return enabled;
                },
                add: name => { if (introFails && this.id === 'app-intro' && name === 'is-fading') throw new Error('Synthetic fade failure'); this.classList.toggle(name, true); },
                remove: name => this.classList.toggle(name, false),
            };
        }
        get textContent() { return this._text + this.children.map(item => item.textContent).join(''); }
        set textContent(value) { this._text = String(value); this.children = []; }
        get innerHTML() { return this._html || ''; }
        set innerHTML(value) { this.replaceChildren(); this._html = value; appendMarkup(this, value); }
        get hidden() { return this._hidden; }
        set hidden(value) {
            const changed = this._hidden !== value; this._hidden = value;
            if (changed) for (const observer of observers) if (observer.target === this) queueMicrotask(observer.callback);
        }
        get isConnected() { return document.contains(this); }
        getAttribute(key) { return this.attributes[key] ?? null; }
        hasAttribute(key) { return key in this.attributes; }
        setAttribute(key, value) { this.attributes[key] = String(value); }
        removeAttribute(key) { delete this.attributes[key]; }
        append(...items) { for (const item of items) { item.remove(); item.parentElement = this; this.children.push(item); } }
        appendChild(item) { this.append(item); return item; }
        replaceChildren(...items) { this.children.forEach(item => { item.parentElement = null; }); this.children = []; this._text = ''; this.append(...items); }
        remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(item => item !== this); this.parentElement = null; }
        contains(item) { return item === this || this.children.some(child => child.contains(item)); }
        querySelectorAll(selector) {
            const matches = item => selector.split(',').some(part => {
                part = part.trim();
                if (part.startsWith('#')) return item.id === part.slice(1);
                if (part.startsWith('.')) return item.classList.contains(part.slice(1));
                const attr = part.match(/^\[([^=\]]+)(?:="([^"]+)")?\]$/);
                return attr ? (attr[2] === undefined ? item.hasAttribute(attr[1]) : item.getAttribute(attr[1]) === attr[2]) : item.tagName === part;
            });
            return this.children.flatMap(item => [...(matches(item) ? [item] : []), ...item.querySelectorAll(selector)]);
        }
        querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
        closest(selector) { for (let item = this; item; item = item.parentElement) {
            if (selector === 'form' && item.tagName === 'form') return item;
            if (selector === '[data-journey-index]' && item.hasAttribute('data-journey-index')) return item;
        } return null; }
        focus() { for (let item = this; item; item = item.parentElement) if (item.hidden || item.inert) return; document.activeElement = this; }
        getBoundingClientRect() {
            const shell = ids.get('app-shell');
            const rail = shell?.classList.contains('menu-expanded') && width >= 1280 ? 208 : 72;
            const tool = shell?.classList.contains('tool-closed') || (shell?.classList.contains('search-closed') && !shell?.classList.contains('results-replace')) ? 0 : 340;
            const result = shell?.classList.contains('results-visible') && !shell?.classList.contains('results-replace') ? 360 : 0;
            const stage = { left: width < 800 ? 0 : rail + tool, top: 64, width: mapSize?.width ?? (width < 800 ? width : width - rail - tool - result), height: mapSize?.height ?? (width < 800 ? 288 : 700) };
            stage.right = stage.left + stage.width; stage.bottom = stage.top + stage.height;
            if (this.id === 'map-stage' || this.id === 'map') return stage;
            if (this.id === 'route-detail' && this.parentElement?.id === 'route-detail-map') {
                const panelWidth = Math.min(340, stage.width - 112), panelHeight = Math.min(420, stage.height - 128);
                return { left: stage.right - 80 - panelWidth, right: stage.right - 80, top: stage.bottom - 64 - panelHeight, bottom: stage.bottom - 64, width: panelWidth, height: panelHeight };
            }
            if (this.classList.contains('map-tools')) return { left: stage.right - 61, right: stage.right - 16, top: stage.bottom - 125, bottom: stage.bottom - 80, width: 45, height: 45 };
            return { x: 400, y: 64, top: 64, bottom: 764, left: 400, right: width, width: Math.max(200, width - 400), height: 700 };
        }
        getClientRects() { for (let item = this; item; item = item.parentElement) if (item.hidden || item.inert) return []; return this.isConnected ? [this.getBoundingClientRect()] : []; }
        addEventListener(type, callback) { this.listeners.set(type, [...(this.listeners.get(type) || []), callback]); }
        removeEventListener(type, callback) { this.listeners.set(type, (this.listeners.get(type) || []).filter(item => item !== callback)); }
        async emit(type, extra = {}) {
            let stopped = false;
            const event = { target: this, preventDefault() {}, stopPropagation() { stopped = true; }, ...extra };
            for (let item = this; item; item = item.parentElement) {
                for (const callback of item.listeners.get(type) || []) callback(event);
                if (stopped) break;
            }
            await flush(); return event;
        }
        reset() { this.querySelectorAll('input,textarea').forEach(item => { item.value = ''; }); }
        requestSubmit() { return this.emit('submit'); }
        scrollIntoView() { this.scrollCalls = (this.scrollCalls || 0) + 1; }
        showModal() { this.open = true; }
        close() { this.open = false; this.emit('close'); }
    }
    document = new Element('document'); document.readyState = 'loading';
    function appendMarkup(parent, html) {
    const stack = [parent]; let inTemplate = 0;
    for (const token of html.matchAll(/<!--[\s\S]*?-->|<[^>]+>|[^<]+/g)) {
        const value = token[0];
        if (value.startsWith('<!--') || value.startsWith('<!')) continue;
        if (!value.startsWith('<')) { if (!inTemplate) stack.at(-1)._text += value.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&'); continue; }
        const tag = value.match(/^<\/?([\w-]+)/)?.[1]; if (!tag) continue;
        if (tag === 'template') { inTemplate += value.startsWith('</') ? -1 : 1; continue; }
        if (inTemplate) continue;
        if (value.startsWith('</')) { while (stack.length > 1 && stack.pop().tagName !== tag) {} continue; }
        const attrs = Object.fromEntries([...value.matchAll(/([\w-]+)="([^"]*)"/g)].map(item => [item[1], item[2]]));
        for (const name of ['hidden', 'disabled']) if (new RegExp('(?:\\s)' + name + '(?:\\s|>)').test(value)) attrs[name] = '';
        const element = new Element(tag, attrs); stack.at(-1).append(element);
        if (element.id) ids.set(element.id, element);
        if (!['input', 'meta', 'link', 'br', 'hr'].includes(tag) && !value.endsWith('/>')) stack.push(element);
    }
    }
    appendMarkup(document, markup);
    document.getElementById = id => ids.get(id) || null;
    document.createElement = tag => new Element(tag);
    document.createElementNS = (_, tag) => new Element(tag);
    document.activeElement = document;
    class FixedDate extends Date {
        constructor(...args) { super(...(args.length ? args : [Date.parse('2030-10-21T00:00:00Z') + now])); }
        static now() { return Date.parse('2030-10-21T00:00:00Z') + now; }
    }
    context = { document, console, Intl, Date: FixedDate, Map, Set, queueMicrotask, URLSearchParams, AbortController,
        setTimeout(callback, delay) { const id = ++nextTimer; timers.set(id, { callback, at: now + delay }); return id; },
        clearTimeout(id) { timers.delete(id); },
        sessionStorage: { getItem(key) { if (storageFails) throw new Error('Storage unavailable'); return storage.get(key); },
            setItem(key, value) { if (storageFails) throw new Error('Storage unavailable'); storage.set(key, value); } },
        MutationObserver: class { constructor(callback) { this.callback = callback; } observe(target) { observers.push({ target, callback: this.callback }); } },
        ResizeObserver: class { constructor(callback) { this.callback = callback; } observe(target) { resizeObservers.push({ target, callback: this.callback }); } },
        location: { reload() { reloads++; } }, confirm() { confirmations++; return accept; },
        matchMedia: query => ({ matches: query.includes('min-width') ? width >= Number(query.match(/\d+/)[0]) : width <= Number(query.match(/\d+/)[0]) }),
        addEventListener(type, callback) { windowListeners.set(type, [...(windowListeners.get(type) || []), callback]); },
        removeEventListener(type, callback) { windowListeners.set(type, (windowListeners.get(type) || []).filter(item => item !== callback)); },
        AppModules: { map: { resizeMap() { resizes++; } } },
        AppState: { getSelectedCampus: () => 'knu', getOriginMode: () => 'building', getDestination: () => ({ name: '테스트 도착지' }) },
    };
    context.window = context; vm.createContext(context);
    vm.runInContext(source('app-shell-controller'), context);
    context.AppModules.shell.init();
    vm.runInContext(source('transport-demo-controller'), context);
    vm.runInContext(source('intro-controller'), context);
    return { context, document, ids, storage, windowListeners, timers,
        shell: context.AppModules.shell, el: id => ids.get(id),
        resize(nextWidth, size = null) {
            width = nextWidth; mapSize = size;
            for (const callback of windowListeners.get('resize') || []) callback();
            for (const observer of resizeObservers) observer.callback();
        },
        loadRouting(handler = () => ({ mode: 'journeys', journeys: [] })) {
            const requests = [], maps = [], gps = [];
            vm.runInContext(source('main'), context);
            context.AppState.setOriginMode('building');
            context.AppState.setDestination('테스트 도착지', 37.89, 127.74);
            ids.get('building-select').value = '테스트 건물';
            context.AppModules.search = { selectOriginMode: mode => context.AppState.setOriginMode(mode), cancelSearch() {}, setSearchStatus() {} };
            context.AppModules.map.clearStationMarkers = () => {};
            context.AppModules.map.drawStationMarkersOnMap = data => maps.push({ type: 'nearby', data });
            context.AppModules.map.recenterPosition = () => maps.push({ type: 'recenter' });
            context.showJourneyOnMap = (data, index) => { maps.push({ type: 'journey', data, index }); return true; };
            context.navigator = { geolocation: { getCurrentPosition(success, error) { gps.push({ success, error }); } } };
            context.isSecureContext = true;
            context.fetch = async (url, options) => {
                const payload = JSON.parse(options.body); requests.push(payload);
                const data = await handler(payload, requests.length);
                return { ok: true, json: async () => data };
            };
            for (const name of ['ui-renderer', 'api-service', 'gps-controller', 'ui-controller']) vm.runInContext(source(name), context);
            return { requests, maps, gps, render: (data, options) => context.renderResults(data, options) };
        },
        loadMap() {
            const state = { instances: 0, fits: [], markers: [], resizes: 0 };
            context.naver = { maps: {
                Map: class {
                    constructor() { state.instances++; }
                    autoResize() { state.resizes++; }
                    fitBounds(bounds, padding) { state.fits.push({ bounds, padding }); }
                    panTo() { state.fits.push('pan'); }
                },
                LatLng: class { constructor(lat, lon) { this.lat = lat; this.lon = lon; } },
                LatLngBounds: class { constructor() { this.points = []; } extend(point) { this.points.push(point); } },
                Point: class {},
                Marker: class { constructor(options) { this.options = options; this.active = true; state.markers.push(this); } setMap(value) { this.active = Boolean(value); } },
            } };
            vm.runInContext(source('map-controller'), context);
            context.initMap(); context.initMap();
            return state;
        },
        loadITX() {
            const requests = [];
            context.fetch = async url => {
                requests.push(url);
                return { ok: true, json: async () => ({ stations: ['춘천', '마석', '용산'].map(name => ({ name })), date: '2026-10-19' }) };
            };
            vm.runInContext(source('itx-timetable'), context);
            return requests;
        },
        setAccept(value) { accept = value; }, counters: () => ({ reloads, confirmations, resizes }),
        async advance(ms) {
            const target = now + ms;
            for (;;) {
                const next = [...timers].filter(([, item]) => item.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
                if (!next) break;
                timers.delete(next[0]); now = next[1].at; next[1].callback(); await flush();
            }
            now = target; await flush();
        },
    };
}

test('initial journey only; one result region outside the input tool, hidden until search', () => {
    const app = harness(), selected = app.el('app-menu').querySelectorAll('[role="tab"]').filter(item => item.getAttribute('aria-selected') === 'true');
    assert.deepEqual(selected.map(item => item.id), ['transport-tab-journey']);
    for (const key of ['shuttle', 'subway', 'intercity', 'taxi', 'rooms']) assert.equal(app.el('transport-panel-' + key).hidden, true);
    assert.equal(app.el('transport-panel-journey').contains(app.el('results-section')), false);
    assert.equal(app.el('app-shell').contains(app.el('results-section')), true);
    assert.equal(app.el('results-section').hidden, true);
    assert.equal(app.el('transport-panel-journey').contains(app.el('route-submit')), true);
    const ids = [...markup.matchAll(/\bid="([^"]+)"/g)].map(item => item[1]);
    assert.equal(new Set(ids).size, ids.length);
    assert.match(app.el('taxi-selection').textContent, /강원대학교/);
});
test('switching and closing preserve input, timetable, quote, existing result and node identity', () => {
    const app = harness();
    const values = ['dest-input', 'shuttle-trips', 'itx-status', 'intercity-trips', 'taxi-content', 'result-container'];
    const nodes = values.map(id => app.el(id));
    nodes.forEach((item, index) => { item.value = 'value-' + index; item.textContent = 'result-' + index; });
    for (const key of ['shuttle', 'subway', 'intercity', 'taxi', 'rooms', 'journey']) { app.shell.select(key); app.shell.closeTool(); app.shell.openTool(); }
    nodes.forEach((item, index) => { assert.equal(app.el(values[index]), item); assert.equal(item.value, 'value-' + index); assert.equal(item.textContent, 'result-' + index); });
    assert.equal(app.el('transport-panel-journey').hidden, false);
    assert.equal(app.counters().confirmations, 0);
});
test('reinitialization never duplicates listeners; keyboard and mobile drawer share selection', async () => {
    for (const width of [360, 390, 1024, 1440]) {
        const app = harness({ width }); app.shell.init(); app.shell.init();
        for (const tab of app.el('app-menu').querySelectorAll('[role="tab"]')) assert.equal(tab.listeners.get('click').length, 1);
        await app.el('sidebar-toggle').emit('click');
        assert.equal(app.el('sidebar-toggle').getAttribute('aria-expanded'), 'true');
        await app.el('transport-tab-journey').emit('keydown', { key: 'ArrowDown' });
        assert.equal(app.shell.getState().active, 'shuttle');
        assert.equal(app.el('transport-panel-shuttle').hidden, false);
        assert.equal(app.shell.getState().expanded, width >= 800);
        app.shell.closeTool();
        assert.equal(app.el('planner-body').hidden, true);
        assert.equal(app.el('transport-tab-shuttle').getAttribute('aria-selected'), 'false');
        app.shell.openTool(); assert.equal(app.el('transport-panel-shuttle').hidden, false);
    }
});
test('real ITX lazy observer starts once on selection and retains its controls on reentry', async () => {
    const app = harness(), requests = app.loadITX();
    await flush(); assert.equal(requests.length, 0);
    app.shell.select('subway'); await flush();
    assert.deepEqual(requests, ['/api/itx/stations']);
    assert.equal(app.el('itx-origin').value, '춘천');
    app.el('itx-date').value = '2026-10-23';
    app.shell.select('journey'); app.shell.select('subway'); app.shell.closeTool(); app.shell.openTool();
    await flush();
    assert.equal(requests.length, 1); assert.equal(app.el('itx-date').value, '2026-10-23');
});
function routeFixture(label = 'A', unknown = false) {
    return { mode: 'journeys', origin: { name: '응답 출발지', lat: 37.88, lon: 127.73 }, destination: { name: '응답 도착지', lat: 37.89, lon: 127.74 },
        source: { source: 'synthetic', time_basis: 'test_only' }, warnings: [], journeys: [null, { legs: [] }, ...[0, 1].map(index => ({
            access_walk: { minutes: 3, distance_m: 150 }, egress_walk: { minutes: 4, distance_m: 200 },
            walk_min: 7, ride_min: 11, wait_min: unknown ? null : 5, total_min: 23, has_live_departure: true,
            legs: [{ route_no: label + index, route_id: label + index, direction: '테스트 방면', direction_verified: !unknown,
                wait_min: unknown ? null : 5, ride_min: 11, board: { name: label + index + ' 승차', lat: 37.88, lon: 127.73 },
                alight: { name: label + index + ' 하차', lat: 37.89, lon: 127.74 } }], warnings: [],
        }))] };
}
test('loading, first valid selection and keyboard-native card buttons preserve original map indices', async () => {
    const app = harness(), routing = app.loadRouting(() => routeFixture());
    const pending = app.context.findRoutes();
    assert.equal(app.el('results-section').hidden, false);
    assert.equal(app.el('results-section').dataset.state, 'loading');
    assert.equal(app.el('result-container').children.length, 0);
    await pending;
    assert.equal(app.el('result-count').textContent, '2개');
    assert.equal(app.context.AppModules.renderer.getSelectedIndex(), 2);
    assert.equal(routing.maps.at(-1).index, 2);
    assert.match(app.el('results-summary').textContent, /테스트 건물 → 테스트 도착지/);
    assert.equal(app.el('result-container').querySelectorAll('.journey-timeline').length, 0);
    assert.equal(app.el('route-detail-content').querySelectorAll('.journey-timeline').length, 1);
    const cards = app.el('result-container').querySelectorAll('.route-card');
    assert.equal(cards[0].classList.contains('is-selected'), true);
    assert.equal(cards[1].querySelector('.route-select').tagName, 'button');
    await cards[1].querySelector('.route-select').emit('click');
    assert.equal(routing.maps.at(-1).index, 3);
    assert.equal(cards[1].querySelector('.route-select').getAttribute('aria-pressed'), 'true');
    assert.equal(cards[0].classList.contains('is-selected'), false);
    assert.match(app.el('route-detail-content').textContent, /A1 하차/);
    assert.equal(app.el('route-detail-content').querySelectorAll('.journey-timeline').length, 1);
    app.context.AppModules.renderer.selectJourney(0);
    assert.equal(routing.maps.at(-1).index, 3, 'Malformed filtered candidate is never selectable');
});
test('wide, narrow and mobile result transitions preserve the query and selection', async () => {
    for (const width of [360, 390, 1024, 1440]) {
        const app = harness({ width }); app.loadRouting(() => routeFixture()); await app.context.findRoutes();
        const replace = app.el('app-shell').classList.contains('results-replace');
        assert.equal(replace, width < 1280);
        assert.equal(app.el('planner-body').hidden, width < 1280);
        app.shell.editSearch();
        assert.equal(app.el('results-section').hidden, true); assert.equal(app.el('results-show').hidden, false);
        assert.equal(app.el('planner-body').hidden, false);
        assert.equal(app.el('building-select').value, '테스트 건물');
        app.shell.showResults(); assert.equal(app.el('results-section').hidden, false);
        const selected = app.context.AppModules.renderer.getSelectedIndex();
        app.shell.closeResults(); assert.equal(app.el('results-show').hidden, false);
        app.shell.showResults(); assert.equal(app.context.AppModules.renderer.getSelectedIndex(), selected);
        assert.equal(app.el('result-count').textContent, '2개');
    }
});
test('nearby, empty, errors, missing fields and map failure remain readable', async () => {
    const app = harness(), routing = app.loadRouting(() => []);
    app.context.AppState.clearDestination(); await app.context.findRoutes();
    assert.equal(app.el('results-section').dataset.state, 'empty');
    assert.match(app.el('results-summary').textContent, /주변 정류장/);
    routing.render([{ station_name: '테스트 정류장', buses: [], walk_min: null }]);
    assert.match(app.el('result-container').textContent, /테스트 정류장/);
    routing.render({ error: '모의 조회 오류', warnings: ['유지할 경고'], source: { source: 'test' } });
    assert.equal(app.el('results-section').dataset.state, 'error');
    assert.match(app.el('result-container').textContent, /유지할 경고/);
    app.context.showJourneyOnMap = () => { throw new Error('Synthetic map failure'); };
    routing.render(routeFixture('미확인', true));
    assert.equal(app.el('results-section').dataset.state, 'partial');
    assert.match(app.el('result-container').textContent, /이동 구간 추정/);
    assert.match(app.el('result-container').textContent, /총 소요 미확인/);
    assert.doesNotMatch(app.el('result-container').textContent, /총 소요 추정|23분|0원|NaN|undefined/);
    assert.match(app.el('route-detail-content').textContent, /미확인0 하차/);
    routing.render({ mode: 'journeys', journeys: [{ legs: [null] }, { legs: [{}], ride_min: null }] });
    assert.equal(app.el('result-count').textContent, '1개');
    assert.match(app.el('route-detail-content').textContent, /미확인/);
});
test('shuttle schedule notes and source survive without inventing live arrival or totals', async () => {
    const app = harness(), routing = app.loadRouting(() => routeFixture()); await app.context.findRoutes();
    const data = routeFixture('셔틀', true);
    data.journeys = [data.journeys[2]];
    data.journeys[0].transport_type = 'shuttle';
    data.journeys[0].ride_min = data.journeys[0].total_min = null;
    data.journeys[0].legs[0].ride_min = null;
    data.journeys[0].warnings = ['조회일 운행 조건 미확인'];
    routing.render(data);
    assert.match(app.el('result-container').textContent, /셔틀 ·/);
    assert.match(app.el('route-detail-content').textContent, /저장된 시간표 기준/);
    assert.match(app.el('route-detail-content').textContent, /운행 조건 미확인/);
    assert.doesNotMatch(app.el('route-detail-content').textContent, /후 도착/);
    assert.doesNotMatch(app.el('result-container').textContent, /총 소요 추정|0원/);
});
test('invalid root responses and network failures are errors rather than an empty timetable', async () => {
    for (const result of [null, {}, 'invalid']) {
        const app = harness(); app.loadRouting(() => result); await app.context.findRoutes();
        assert.equal(app.el('results-section').dataset.state, 'error');
        assert.match(app.el('result-container').textContent, /서버 응답 형식/);
    }
    const app = harness(); app.loadRouting(() => { throw new Error('Synthetic network error'); });
    await app.context.findRoutes();
    assert.equal(app.el('results-section').dataset.state, 'error');
});
test('old responses cannot replace a newer search or an invalidated selection', async () => {
    let release;
    const app = harness(), routing = app.loadRouting((_, count) => count === 1 ? new Promise(resolve => { release = resolve; }) : routeFixture('최신'));
    const old = app.context.findRoutes(); await flush();
    await app.context.findRoutes(); release(routeFixture('과거')); await old;
    assert.match(app.el('result-container').textContent, /최신/);
    assert.doesNotMatch(app.el('result-container').textContent, /과거/);
    assert.match(routing.maps.at(-1).data.journeys[2].legs[0].route_no, /최신/);
    let finish;
    app.context.fetch = async () => ({ ok: true, json: () => new Promise(resolve => { finish = resolve; }) });
    const pending = app.context.findRoutes(); await flush();
    app.context.AppState.setDestination('변경한 목적지', 37.90, 127.75);
    app.context.AppModules.api.cancelPendingRequests();
    finish(routeFixture('폐기')); await pending;
    assert.equal(app.el('results-section').hidden, true);
    assert.equal(app.shell.getState().resultAvailable, false);
    assert.doesNotMatch(app.el('result-container').textContent, /폐기/);
});
test('menu changes and explicit result close block late panel, focus and map movement', async () => {
    for (const action of ['menu', 'close']) {
        let release;
        const app = harness(), routing = app.loadRouting(() => new Promise(resolve => { release = resolve; }));
        const pending = app.context.findRoutes(); await flush();
        if (action === 'menu') app.shell.select('taxi', { focus: true }); else app.shell.closeResults();
        const focus = app.document.activeElement;
        release(routeFixture()); await pending;
        assert.equal(app.el('results-section').hidden, true);
        assert.equal(app.document.activeElement, focus);
        assert.equal(routing.maps.length, 0);
        assert.equal(app.shell.getState().resultAvailable, true);
        if (action === 'menu') app.shell.select('journey');
        app.shell.showResults();
        assert.equal(app.el('results-section').hidden, false);
        assert.equal(routing.maps.at(-1).index, 2, 'Explicitly reopening may show the saved selection');
    }
});
test('GPS waiting retains its original presentation token after navigating away', async () => {
    const app = harness(), routing = app.loadRouting(() => routeFixture());
    app.context.AppState.setOriginMode('gps');
    const pending = app.context.findRoutesByGPS(); await flush();
    assert.equal(routing.gps.length, 1);
    app.shell.select('intercity', { focus: true }); const focus = app.document.activeElement;
    routing.gps[0].success({ coords: { latitude: 37.88, longitude: 127.73 } });
    await pending;
    assert.equal(routing.requests.length, 1);
    assert.equal(app.el('results-section').hidden, true);
    assert.equal(app.document.activeElement, focus);
    assert.equal(routing.maps.length, 0);
    assert.equal(app.shell.getState().active, 'intercity');
    app.shell.select('journey'); app.shell.showResults(); assert.equal(app.el('result-count').textContent, '2개');
});
test('closing during GPS wait and invalidating its inputs never reopen or repaint results', async () => {
    for (const invalid of [false, true]) {
        const app = harness(), routing = app.loadRouting(() => routeFixture());
        app.context.AppState.setOriginMode('gps');
        const pending = app.context.findRoutesByGPS(); await flush();
        if (invalid) {
            app.context.AppState.setDestination('새 도착지', 37.91, 127.75);
            app.context.AppModules.api.cancelPendingRequests();
        } else app.shell.closeResults();
        const focus = app.document.activeElement;
        routing.gps[0].success({ coords: { latitude: 37.88, longitude: 127.73 } }); await pending;
        assert.equal(app.el('results-section').hidden, true); assert.equal(routing.maps.length, 0);
        assert.equal(app.document.activeElement, focus);
        assert.equal(routing.requests.length, invalid ? 0 : 1);
        assert.equal(app.shell.getState().resultAvailable, !invalid);
    }
});
test('production map controller keeps one instance, displays the selected route and never focuses on selection', async () => {
    const app = harness(); app.loadRouting(() => routeFixture('지도'));
    const map = app.loadMap(); assert.equal(map.instances, 1);
    await app.context.findRoutes();
    assert.ok(map.markers.filter(item => item.active).some(item => item.options.title.includes('지도0 승차')));
    const focus = app.document.activeElement;
    app.context.AppModules.renderer.selectJourney(3, { user: true });
    assert.ok(map.markers.filter(item => item.active).some(item => item.options.title.includes('지도1 승차')));
    assert.equal(app.document.activeElement, focus);
    const fits = map.fits.length;
    await app.el('sidebar-toggle').emit('click'); await app.el('sidebar-toggle').emit('click');
    assert.equal(map.instances, 1); assert.equal(map.fits.length, fits, 'Menu resizing never refits or recenters');
    assert.ok(map.fits.every(item => Object.values(item.padding).every(value => value >= 0)));
    assert.equal(app.el('results-section').hidden, false);
});

test('automatic selection stays closed; user selection docks a single detail beside the retained list', async () => {
    const app = harness(), routing = app.loadRouting(() => routeFixture()); await app.context.findRoutes();
    assert.equal(app.context.AppModules.renderer.getSelectedIndex(), 2);
    assert.equal(routing.maps.at(-1).index, 2);
    assert.equal(app.el('route-detail').hidden, true);
    const buttons = app.el('result-container').querySelectorAll('.route-select');
    const maps = routing.maps.length;
    await buttons[1].emit('click');
    assert.equal(app.el('route-detail').hidden, false);
    assert.equal(app.el('route-detail').parentElement, app.el('route-detail-map'));
    assert.equal(app.el('map-stage').contains(app.el('route-detail')), true);
    assert.equal(app.el('map').contains(app.el('route-detail')), false);
    assert.equal(app.el('app-shell').classList.contains('detail-docked'), true);
    assert.equal(app.el('result-container').hidden, false);
    assert.equal(app.el('results-section').hidden, false);
    assert.equal(app.el('route-detail-heading').textContent, '선택한 경로 상세 · 경로 02');
    assert.match(app.el('route-detail-content').textContent, /A1 하차/);
    assert.equal(routing.maps.at(-1).index, 3);
    assert.equal(routing.maps.length, maps + 1, 'Nested button click is handled once');
    assert.equal(buttons[1].getAttribute('aria-expanded'), 'true');
    assert.equal(routing.requests.length, 1);
});
test('close and same-route reopen preserve selection, map, inputs and detail scroll', async () => {
    const app = harness(), routing = app.loadRouting(() => routeFixture()); await app.context.findRoutes();
    const button = app.context.AppModules.renderer.getSelectedButton(); await button.emit('click');
    const detail = app.el('route-detail'), content = app.el('route-detail-content'); content.scrollTop = 93;
    const timeline = content.querySelector('.journey-timeline'), maps = routing.maps.length;
    await app.el('route-detail-close').emit('click');
    assert.equal(detail.hidden, true); assert.equal(app.el('results-section').hidden, false);
    assert.equal(app.document.activeElement, button);
    assert.equal(button.getAttribute('aria-pressed'), 'true'); assert.equal(button.getAttribute('aria-expanded'), 'false');
    assert.equal(routing.maps.length, maps); assert.equal(app.el('building-select').value, '테스트 건물');
    await button.emit('click');
    assert.equal(detail.hidden, false); assert.equal(content.scrollTop, 93);
    assert.equal(content.querySelector('.journey-timeline'), timeline);
    assert.equal(routing.requests.length, 1);
});
test('map-space limits and real overflow/collision checks use the inline fallback', async () => {
    const app = harness(), routing = app.loadRouting(() => routeFixture()); await app.context.findRoutes();
    await app.context.AppModules.renderer.getSelectedButton().emit('click');
    const detail = app.el('route-detail');
    for (const size of [{ width: 639, height: 700 }, { width: 900, height: 479 }]) {
        app.resize(1440, size);
        assert.equal(detail.parentElement, app.el('route-detail-inline'));
        assert.equal(app.el('app-shell').classList.contains('detail-docked'), false);
        assert.equal(detail.hidden, false);
    }
    app.resize(1440, { width: 640, height: 480 });
    assert.equal(detail.parentElement, app.el('route-detail-map'));
    detail.getBoundingClientRect = () => ({ left: 0, right: 2000, top: 0, bottom: 2000, width: 2000, height: 2000 });
    app.resize(1440, { width: 900, height: 700 });
    assert.equal(detail.parentElement, app.el('route-detail-inline'), 'Overflow overrides an otherwise large map');
    delete detail.getBoundingClientRect;
    const tools = app.el('map-stage').querySelector('.map-tools');
    tools.getBoundingClientRect = () => ({ left: 500, right: 1600, top: 64, bottom: 800, width: 1100, height: 736 });
    app.resize(1440, { width: 900, height: 700 });
    assert.equal(detail.parentElement, app.el('route-detail-inline'), 'Map controls win over the overlay');
    assert.equal(routing.requests.length, 1);
});
test('resize moves the same detail DOM without new listeners, requests, refits or lost scroll', async () => {
    const app = harness(), routing = app.loadRouting(() => routeFixture()); await app.context.findRoutes();
    await app.context.AppModules.renderer.getSelectedButton().emit('click');
    const detail = app.el('route-detail'), content = app.el('route-detail-content');
    const timeline = content.querySelector('.journey-timeline'), maps = routing.maps.length; content.scrollTop = 117;
    for (const width of [1024, 390, 360, 1440, 1024, 1440]) {
        app.resize(width);
        assert.equal(app.document.querySelectorAll('#route-detail').length, 1);
        assert.equal(content.querySelector('.journey-timeline'), timeline);
        assert.equal(content.scrollTop, 117); assert.equal(detail.hidden, false);
        assert.equal(detail.parentElement.id, width >= 1280 ? 'route-detail-map' : 'route-detail-inline');
        assert.equal(app.el('route-detail-close').listeners.get('click').length, 1);
        assert.equal(app.el('result-container').listeners.get('click').length, 1);
    }
    assert.equal(routing.requests.length, 1); assert.equal(routing.maps.length, maps);
    await app.el('route-detail-close').emit('click'); app.resize(390); app.resize(1440); await app.advance(260);
    assert.equal(detail.hidden, true, 'Resizing never reopens a closed detail');
});
test('keyboard selection enters the detail; Escape returns focus without trapping Tab or closing menus', async () => {
    const app = harness(); app.loadRouting(() => routeFixture()); await app.context.findRoutes();
    const button = app.context.AppModules.renderer.getSelectedButton();
    await app.el('sidebar-toggle').emit('click');
    await button.emit('click', { detail: 0 });
    assert.equal(app.document.activeElement, app.el('route-detail-heading'));
    let blocked = false;
    await app.el('route-detail-heading').emit('keydown', { key: 'Tab', preventDefault() { blocked = true; } });
    assert.equal(blocked, false, 'The detail is non-modal');
    await app.el('route-detail-heading').emit('keydown', { key: 'Escape' });
    assert.equal(app.el('route-detail').hidden, true); assert.equal(app.document.activeElement, button);
    assert.equal(app.shell.getState().expanded, true, 'Escape only closes the detail');
    await button.emit('click', { detail: 0 });
    button.getBoundingClientRect = () => ({ top: -200, bottom: -150, left: 1100, right: 1200, width: 100, height: 50 });
    await app.el('route-detail-close').emit('click');
    assert.equal(app.document.activeElement, app.el('results-heading'), 'Offscreen buttons do not receive invisible focus');
    delete button.getBoundingClientRect;
    await button.emit('click', { detail: 0 }); button.remove();
    await app.el('route-detail-close').emit('click');
    assert.equal(app.document.activeElement, app.el('results-heading'));
});
test('menu/close/new-query invalidation never resurrect an old detail via automatic or late selection', async () => {
    let release;
    const app = harness(), routing = app.loadRouting((_, count) => count === 1 ? routeFixture('원본') : new Promise(resolve => { release = resolve; }));
    await app.context.findRoutes(); await app.context.AppModules.renderer.getSelectedButton().emit('click');
    const pending = app.context.findRoutes(); await flush();
    assert.equal(app.el('route-detail').hidden, true);
    app.shell.select('shuttle', { focus: true }); const focus = app.document.activeElement, maps = routing.maps.length;
    release(routeFixture('지연')); await pending;
    assert.equal(app.el('route-detail').hidden, true); assert.equal(app.document.activeElement, focus);
    assert.equal(routing.maps.length, maps);
    app.shell.select('journey'); app.shell.showResults();
    assert.equal(app.el('route-detail').hidden, true);
    await app.context.AppModules.renderer.getSelectedButton().emit('click');
    assert.match(app.el('route-detail-content').textContent, /지연0 하차/);
    app.shell.closeResults(); app.shell.showResults(); assert.equal(app.el('route-detail').hidden, true);
    await app.context.AppModules.renderer.getSelectedButton().emit('click');
    app.context.AppModules.api.cancelPendingRequests();
    assert.equal(app.el('route-detail').hidden, true); assert.equal(app.shell.getState().resultAvailable, false);
});

test('intro holds one second, fades for 400ms and persists only the session flag', async () => {
    const app = harness(); assert.equal(app.el('app-intro').hidden, false);
    await app.advance(999); assert.equal(app.el('app-intro').classList.contains('is-fading'), false);
    await app.advance(1); assert.equal(app.el('app-intro').classList.contains('is-fading'), true);
    await app.advance(399); assert.equal(app.el('app-intro').hidden, false);
    await app.advance(1); assert.equal(app.el('app-intro').hidden, true);
    assert.deepEqual([...app.storage], [['commute-master:intro-seen', '1']]);
    assert.equal(harness({ seen: true }).el('app-intro').hidden, true);
    app.shell.select('taxi'); assert.equal(app.el('app-intro').hidden, true);
});
test('storage and fade failures cannot leave the intro visible; CSS fallback is nonblocking', async () => {
    for (const options of [{ storageFails: true }, { introFails: true }]) {
        const app = harness(options); await app.advance(1400); assert.equal(app.el('app-intro').hidden, true);
    }
    const css = fs.readFileSync(path.join(project, 'static/css/main.css'), 'utf8');
    assert.match(css, /pointer-events: none; opacity: 1; transition: opacity \.4s ease; animation: intro-safety 1\.6s/);
    assert.match(css, /\.intro-bus \{ animation: none; \}/);
    assert.doesNotMatch(source('intro-controller'), /fetch\(|geolocation|localStorage/);
});
test('refresh guard attaches only while dirty; cancel keeps data; confirmed title reload has no second prompt', async () => {
    const app = harness();
    assert.equal((app.windowListeners.get('beforeunload') || []).length, 0);
    await app.el('app-reload').emit('click'); assert.equal(app.counters().reloads, 1);
    app.el('demo-title').value = '보존할 초안'; await app.el('demo-title').emit('input');
    assert.equal(app.windowListeners.get('beforeunload').length, 1);
    let prevented = false;
    const unload = { preventDefault() { prevented = true; } };
    app.windowListeners.get('beforeunload')[0](unload);
    assert.equal(prevented, true); assert.equal(unload.returnValue, '');
    await app.el('app-reload').emit('click'); assert.equal(app.counters().reloads, 1);
    assert.equal(app.el('demo-title').value, '보존할 초안');
    assert.equal(app.windowListeners.get('beforeunload').length, 1);
    app.el('demo-title').value = ''; await app.el('demo-title').emit('input');
    assert.equal(app.windowListeners.get('beforeunload').length, 0);
    app.el('demo-title').value = '다시 작성'; await app.el('demo-title').emit('input');
    app.setAccept(true); await app.el('app-reload').emit('click');
    assert.equal(app.counters().reloads, 2); assert.equal(app.windowListeners.get('beforeunload').length, 0);
});
test('created rooms, membership, messages and draft survive tool closing and navigation', async () => {
    const app = harness(); app.shell.select('taxi');
    for (const [key, value] of Object.entries({ title: '메모리 시연', destination: '남춘천역', from: '2030-10-21T18:00' })) app.el('demo-' + key).value = value;
    await app.el('demo-party-form').emit('submit');
    assert.equal(app.el('demo-toast').textContent, '생성완료!');
    assert.equal(app.el('demo-room-list').children.length, 1);
    assert.equal(app.shell.getState().active, 'taxi');
    app.shell.select('rooms');
    const post = app.el('demo-room-list').children[0].children[0]; await post.emit('click');
    await app.el('demo-join').emit('click'); assert.equal(app.el('demo-count').textContent, '2/4명');
    app.el('demo-chat-input').value = '테스트 메시지'; await app.el('demo-chat-input').emit('input');
    await app.el('demo-chat-form').emit('submit');
    app.el('demo-chat-input').value = '남겨 둔 초안'; await app.el('demo-chat-input').emit('input');
    app.el('demo-party-dialog').close(); await flush();
    app.shell.select('intercity'); app.shell.closeTool(); app.shell.select('rooms');
    assert.equal(app.el('demo-room-list').children[0].children[0], post);
    await post.emit('click');
    assert.equal(app.el('demo-count').textContent, '2/4명');
    assert.match(app.el('demo-chat-messages').textContent, /테스트 메시지/);
    assert.equal(app.el('demo-chat-input').value, '남겨 둔 초안');
    assert.equal(app.windowListeners.get('beforeunload').length, 1);
    await app.advance(1400); assert.equal(app.shell.getState().active, 'rooms');
});

async function readyDemo() {
    const app = harness({ seen: true }); app.shell.select('taxi'); await app.advance(260); return app;
}
async function fillDemo(app, fields = {}) {
    for (const [name, value] of Object.entries({ title: '단일 시각 시연', destination: '남춘천역', from: '2030-10-21T18:00', ...fields })) {
        app.el('demo-' + name).value = value; await app.el('demo-' + name).emit('input');
    }
}
test('single departure validates real Korean calendar times without erasing other fields', async () => {
    const app = await readyDemo();
    assert.equal(app.el('demo-to'), undefined); assert.equal(app.el('demo-to-error'), undefined);
    assert.equal(app.el('demo-party-form').querySelectorAll('input').length, 3);
    assert.match(app.el('demo-from').parentElement.textContent, /출발 예정 시각 · 한국 시간/);
    for (const from of ['', 'invalid', '2030-02-29T18:00', '2030-02-30T18:00', '2030-13-01T18:00', '2030-10-21T24:00', '2030-10-21T18:60']) {
        await fillDemo(app, { from }); await app.el('demo-party-form').emit('submit');
        assert.equal(app.el('demo-room-list').children.length, 0);
        assert.equal(app.el('demo-from').getAttribute('aria-invalid'), 'true');
        assert.equal(app.el('demo-title').value, '단일 시각 시연');
        assert.equal(app.el('demo-destination').value, '남춘천역');
        assert.equal(app.el('demo-toast').hidden, true);
        assert.equal(app.timers.size, 0, 'Invalid input schedules neither feedback nor navigation');
    }
    await fillDemo(app, { from: '2032-02-29T18:00' }); await app.el('demo-party-form').emit('submit');
    const post = app.el('demo-room-list').children[0].children[0];
    assert.match(post.textContent, /출발 예정 · 2032-02-29 18:00/);
    app.shell.select('rooms'); await post.emit('click');
    assert.equal(app.el('demo-room-time').textContent, '2032-02-29 18:00');
    assert.doesNotMatch(post.textContent, / ~ /);
});
test('success stays in taxi for 999ms then opens rooms, focuses the new post and ends its feedback', async () => {
    const app = await readyDemo(); await fillDemo(app);
    const focus = app.document.activeElement;
    await app.el('demo-party-form').emit('submit');
    assert.equal(app.el('demo-toast').textContent, '생성완료!');
    assert.equal(app.document.activeElement, focus, 'The notification never takes focus');
    const post = app.el('demo-room-list').children[0].children[0];
    await app.advance(999);
    assert.equal(app.shell.getState().active, 'taxi'); assert.equal(app.el('demo-toast').classList.contains('is-fading'), false);
    await app.advance(1);
    assert.equal(app.shell.getState().active, 'rooms');
    assert.equal(app.el('demo-party-dialog').open, false);
    assert.equal(app.document.activeElement, post);
    assert.equal(post.classList.contains('is-new'), true);
    assert.equal(post.scrollCalls || 0, 0, 'Visible posts never trigger scrolling');
    assert.equal(app.el('demo-toast').classList.contains('is-fading'), true);
    await app.advance(399); assert.equal(app.el('demo-toast').hidden, false);
    await app.advance(1); assert.equal(app.el('demo-toast').hidden, true);
    await app.advance(1400); assert.equal(post.classList.contains('is-new'), false);
    assert.equal(app.timers.size, 0);
});
test('rapid and duplicate submissions create only one post and one scheduled transition', async () => {
    const app = await readyDemo(); await fillDemo(app);
    const submissions = Array.from({ length: 20 }, () => app.el('demo-party-form').emit('submit'));
    await Promise.all(submissions);
    assert.equal(app.el('demo-room-list').children.length, 1);
    assert.equal(app.timers.size, 2, 'Only fade and auto-move deadlines exist');
    await app.el('demo-party-form').emit('submit');
    assert.equal(app.timers.size, 2); assert.equal(app.el('demo-room-list').children.length, 1);
    await app.advance(1400); assert.equal(app.shell.getState().active, 'rooms');
    app.shell.select('taxi'); await app.advance(260);
    const timers = app.timers.size;
    await app.el('demo-party-form').emit('submit');
    assert.equal(app.el('demo-toast').hidden, true); assert.equal(app.timers.size, timers);
    assert.equal(app.el('demo-room-list').children.length, 1);
});
test('every manual menu choice cancels auto-move, including direct rooms and repeated taxi', async () => {
    for (const name of ['journey', 'shuttle', 'subway', 'intercity', 'taxi', 'rooms']) {
        const app = await readyDemo(); await fillDemo(app); await app.el('demo-party-form').emit('submit');
        await app.advance(500); app.shell.select(name, { focus: true });
        const navigation = app.shell.getNavigation(), focus = app.document.activeElement;
        await app.advance(2000);
        assert.equal(app.shell.getState().active, name);
        assert.equal(app.shell.getNavigation(), navigation, 'No hidden second selection occurs');
        assert.equal(app.document.activeElement, focus);
        assert.equal(app.el('demo-room-list').children.length, 1);
        assert.equal(app.el('demo-room-list').children[0].children[0].classList.contains('is-new'), false);
        assert.equal(app.timers.size, 0);
    }
});
test('new-post reset, form editing, closing tools and opening posts supersede a pending move', async () => {
    for (const action of ['reset', 'edit', 'close', 'open']) {
        const app = await readyDemo(); await fillDemo(app); await app.el('demo-party-form').emit('submit');
        const post = app.el('demo-room-list').children[0].children[0];
        if (action === 'reset') await app.el('demo-new-post').emit('click');
        if (action === 'edit') { app.el('demo-title').value = '다음 모집글'; await app.el('demo-title').emit('input'); }
        if (action === 'close') app.shell.closeTool();
        if (action === 'open') await post.emit('click');
        const focus = app.document.activeElement;
        await app.advance(2000);
        assert.equal(app.shell.getState().active, 'taxi'); assert.equal(app.document.activeElement, focus);
        assert.equal(app.el('demo-room-list').children.length, 1);
        if (action === 'reset') for (const field of ['title', 'destination', 'from']) assert.equal(app.el('demo-' + field).value, '');
    }
});
test('new generation clears previous feedback and highlights only the latest unique post', async () => {
    const early = await readyDemo(); await fillDemo(early, { title: '첫 예약' }); await early.el('demo-party-form').emit('submit');
    await early.advance(500); await fillDemo(early, { title: '교체 예약' }); await early.el('demo-party-form').emit('submit');
    await early.advance(500);
    assert.equal(early.shell.getState().active, 'taxi');
    assert.equal(early.el('demo-toast').classList.contains('is-fading'), false, 'Old fade deadline was cleared');
    await early.advance(500);
    assert.equal(early.shell.getState().active, 'rooms');
    assert.match(early.document.activeElement.textContent, /교체 예약/);
    const app = await readyDemo(); await fillDemo(app, { title: '첫 방' }); await app.el('demo-party-form').emit('submit');
    const first = app.el('demo-room-list').children[0].children[0];
    await app.advance(1000); assert.equal(first.classList.contains('is-new'), true);
    app.shell.select('taxi'); await app.el('demo-new-post').emit('click');
    await fillDemo(app, { title: '새 방', from: '2030-10-22T10:00' }); await app.el('demo-party-form').emit('submit');
    const latest = app.el('demo-room-list').children[0].children[0];
    assert.notEqual(latest.dataset.roomId, first.dataset.roomId);
    assert.equal(app.el('demo-room-list').children[1].children[0], first);
    assert.equal(first.classList.contains('is-new'), false);
    assert.equal(app.el('demo-toast').classList.contains('is-fading'), false);
    await app.advance(999); assert.equal(app.shell.getState().active, 'taxi');
    await app.advance(1); assert.equal(app.document.activeElement, latest); assert.equal(latest.classList.contains('is-new'), true);
    await app.advance(1800); assert.equal(latest.classList.contains('is-new'), false);
});
test('independent rooms retain membership, composition-safe chat, drafts and scroll', async () => {
    const app = await readyDemo(); await fillDemo(app, { title: '첫 대화방' }); await app.el('demo-party-form').emit('submit');
    await app.advance(1000); const first = app.el('demo-room-list').children[0].children[0]; await first.emit('click');
    await app.el('demo-join').emit('click'); await app.el('demo-join').emit('click');
    assert.equal(app.el('demo-count').textContent, '2/4명'); assert.match(first.textContent, /2\/4명/);
    app.el('demo-chat-input').value = '한글 조합';
    await app.el('demo-chat-input').emit('keydown', { key: 'Enter', isComposing: true });
    await app.el('demo-chat-input').emit('keydown', { key: 'Enter', shiftKey: true });
    assert.equal(app.el('demo-chat-messages').querySelectorAll('.demo-message').length, 0);
    await app.el('demo-chat-input').emit('keydown', { key: 'Enter' });
    assert.equal(app.el('demo-chat-messages').querySelectorAll('.demo-message').length, 1);
    app.el('demo-chat-input').value = '첫 방 초안'; await app.el('demo-chat-input').emit('input');
    app.el('demo-chat-messages').scrollHeight = 1000; app.el('demo-chat-messages').scrollTop = 0;
    app.el('demo-party-dialog').close(); await flush(); assert.equal(app.document.activeElement, first);
    app.shell.select('taxi'); await app.el('demo-new-post').emit('click'); await fillDemo(app, { title: '둘째 대화방' });
    await app.el('demo-party-form').emit('submit'); await app.advance(1000);
    const second = app.el('demo-room-list').children[0].children[0]; await second.emit('click');
    assert.equal(app.el('demo-count').textContent, '1/4명'); assert.equal(app.el('demo-chat-input').value, '');
    assert.equal(app.el('demo-chat-messages').querySelectorAll('.demo-message').length, 0);
    app.el('demo-chat-input').value = '둘째 초안'; await app.el('demo-chat-input').emit('input'); app.el('demo-party-dialog').close(); await flush();
    await first.emit('click');
    assert.equal(app.el('demo-count').textContent, '2/4명'); assert.equal(app.el('demo-chat-input').value, '첫 방 초안');
    assert.match(app.el('demo-chat-messages').textContent, /한글 조합/); assert.equal(app.el('demo-chat-messages').scrollTop, 0);
    app.el('demo-party-dialog').close(); await flush(); await second.emit('click');
    assert.equal(app.el('demo-chat-input').value, '둘째 초안');
});
test('only an out-of-view new post scrolls; single-field drafts retain refresh protection', async () => {
    const app = await readyDemo();
    app.el('demo-from').value = '2030-10-21T18:00'; await app.el('demo-from').emit('input');
    assert.equal(app.windowListeners.get('beforeunload').length, 1);
    await app.el('app-reload').emit('click'); assert.equal(app.el('demo-from').value, '2030-10-21T18:00');
    app.el('demo-from').value = ''; await app.el('demo-from').emit('input'); assert.equal(app.windowListeners.get('beforeunload').length, 0);
    await fillDemo(app); await app.el('demo-party-form').emit('submit');
    const post = app.el('demo-room-list').children[0].children[0]; post.getBoundingClientRect = () => ({ top: 900, bottom: 1000 });
    await app.advance(1000); assert.equal(post.scrollCalls, 1);
    app.shell.select('taxi'); await app.el('demo-new-post').emit('click');
    assert.equal(app.windowListeners.get('beforeunload').length, 1, 'Created rooms remain protected after clearing the form');
});

(async () => {
    let failures = 0;
    for (const item of tests) {
        try { await item.run(); console.log('PASS ' + item.name); }
        catch (error) { failures++; console.error('FAIL ' + item.name + '\n' + error.stack); }
    }
    console.log(`${tests.length - failures}/${tests.length} shell/intro/demo memory DOM checks passed; no browser or API used`);
    if (failures) process.exitCode = 1;
})();
