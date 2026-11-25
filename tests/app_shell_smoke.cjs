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
            if (selector === 'button' && item.tagName === 'button') return item;
            if (selector === '[data-journey-index]' && item.hasAttribute('data-journey-index')) return item;
        } return null; }
        focus() { for (let item = this; item; item = item.parentElement) if (item.hidden || item.inert) return; document.activeElement = this; }
        getBoundingClientRect() {
            const shell = ids.get('app-shell');
            const rail = shell?.classList.contains('menu-expanded') && width >= 800 ? 196 : 72;
            const panel = shell?.classList.contains('tool-closed') ? 0 : Math.min(408, Math.max(344, width * .28));
            const stage = { left: width < 800 ? 0 : rail + panel, top: 64, width: mapSize?.width ?? (width < 800 ? width : width - rail - panel), height: mapSize?.height ?? (width < 800 ? 288 : 700) };
            stage.right = stage.left + stage.width; stage.bottom = stage.top + stage.height;
            if (this.id === 'map-stage' || this.id === 'map') return stage;
            if (this.id === 'map-journey-widget') {
                const left = stage.left + 16, top = stage.top + (width < 1280 ? 106 : 16);
                const widgetWidth = Math.min(330, stage.width - 32);
                return { left, top, right: left + widgetWidth, bottom: top + 110, width: widgetWidth, height: 110 };
            }
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
        setPointerCapture(pointerId) { this.capturedPointers ??= new Set(); this.capturedPointers.add(pointerId); }
        releasePointerCapture(pointerId) { this.capturedPointers?.delete(pointerId); }
        hasPointerCapture(pointerId) { return this.capturedPointers?.has(pointerId) || false; }
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
        loadMap({ events = false } = {}) {
            const state = { instances: 0, fits: [], markers: [], polylines: [], listeners: [], resizes: 0 };
            context.naver = { maps: {
                Map: class {
                    constructor() { state.instances++; state.map = this; }
                    autoResize() { state.resizes++; }
                    fitBounds(bounds, padding) { state.fits.push({ bounds, padding }); }
                    panTo() { state.fits.push('pan'); }
                },
                LatLng: class { constructor(lat, lon) { this.lat = lat; this.lon = lon; } },
                LatLngBounds: class { constructor() { this.points = []; } extend(point) { this.points.push(point); } },
                Point: class {},
                Marker: class { constructor(options) { this.options = options; this.active = true; state.markers.push(this); } setMap(value) { this.active = Boolean(value); } },
                Polyline: class { constructor(options) { this.options = options; this.active = true; state.polylines.push(this); } setMap(value) { this.active = Boolean(value); } },
            } };
            if (events) context.naver.maps.Event = {
                addListener(target, type, callback) { const listener = { target, type, callback }; state.listeners.push(listener); return listener; },
                removeListener(listener) { state.listeners = state.listeners.filter(item => item !== listener); },
            };
            state.trigger = async (type, event = {}) => {
                state.listeners.filter(item => item.target === state.map && item.type === type).forEach(item => item.callback(event));
                await flush();
            };
            vm.runInContext(source('map-controller'), context);
            context.initMap(); context.initMap();
            return state;
        },
        loadMapJourney({ journey = () => mapJourneyFixture(), taxi = () => mapTaxiFixture(), ready = true, productionMap = false } = {}) {
            const requests = [], taxiRequests = [], mapEffects = [], gps = [];
            const presentation = { begin: 0, render: 0 };
            let mapReady = ready;
            vm.runInContext(source('main'), context);
            context.navigator = { geolocation: { getCurrentPosition(success, error) { gps.push({ success, error }); } } };
            context.isSecureContext = true;
            context.AppModules.search = { selectOriginMode: mode => context.AppState.setOriginMode(mode), cancelSearch() {}, setSearchStatus() {} };
            const originalBegin = context.AppModules.shell.beginResults;
            context.AppModules.shell.beginResults = (...args) => { presentation.begin++; return originalBegin(...args); };
            Object.assign(context.AppModules.map, {
                isReady: () => mapReady,
                clearStationMarkers() {},
                drawStationMarkersOnMap(data) { mapEffects.push({ type: 'nearby', data }); },
                setSelectionPoints: (origin, destination) => mapEffects.push({ type: 'selection', origin, destination }),
                showMapJourney: (data, index) => { mapEffects.push({ type: 'journey', data, index }); return mapReady; },
                clearMapJourney: () => mapEffects.push({ type: 'clear' }),
            });
            context.showJourneyOnMap = (data, index) => context.AppModules.map.showMapJourney(data, index);
            function request(handler, calls, payload, options = {}) {
                const call = { payload, signal: options.signal }; calls.push(call);
                return Promise.resolve().then(() => handler(payload, calls.length, options));
            }
            context.fetch = async (url, options = {}) => {
                const isTaxi = String(url).includes('/api/taxi/estimate');
                const data = await request(isTaxi ? taxi : journey, isTaxi ? taxiRequests : requests,
                    JSON.parse(options.body), options);
                return typeof data?.json === 'function' ? data : { ok: true, json: async () => data };
            };
            for (const name of ['ui-renderer', 'api-service', 'gps-controller', 'ui-controller']) vm.runInContext(source(name), context);
            const originalRender = context.renderResults;
            context.renderResults = (...args) => { presentation.render++; return originalRender(...args); };
            context.AppModules.renderer.renderResults = context.renderResults;
            vm.runInContext(source('map-journey-controller'), context);
            const controller = context.AppModules.mapJourney;
            controller.init(); controller.init();
            const mapState = productionMap ? this.loadMap({ events: true }) : null;
            return { requests, taxiRequests, mapEffects, gps, presentation, controller, mapState,
                async pickPair(origin = { lat: 37.88, lon: 127.73 }, destination = { lat: 37.89, lon: 127.74 }) {
                    controller.armSelection('origin'); controller.pickPoint(origin.lat, origin.lon);
                    controller.pickPoint(destination.lat, destination.lon); await flush();
                },
                setReady(value) { mapReady = value; controller.onMapReady(); },
            };
        },
        loadITX(handler) {
            const requests = [];
            context.fetch = async (url, options = {}) => {
                requests.push(url);
                const data = handler ? await handler(url, options) : itxStations();
                return typeof data?.json === 'function' ? data : { ok: true, json: async () => data };
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
function itxStations() {
    return { stations: ['춘천', '마석', '용산'].map(name => ({ name })), date: '2026-10-19' };
}
function itxTimetable(query = {}) {
    const { origin = '춘천', destination = '마석', date = '20261019' } = query;
    return { origin, destination, date, scope_warning: '계획 시간표입니다. 실제 운행은 코레일에서 확인해 주세요.', trains: [
        { origin, destination, train_no: '05001', departure_time: '08:05', arrival_time: '09:14', fare: 3100 },
        { origin, destination, train_no: '05003', departure_time: '10:00', arrival_time: '11:09', fare: null },
    ] };
}
test('ITX swap preserves the date and time, cancels stale results and queries the reversed pair', async () => {
    const app = harness(), pending = [];
    app.loadITX((url, options) => {
        if (url === '/api/itx/stations') return itxStations();
        return new Promise(resolve => pending.push({ resolve, signal: options.signal,
            query: Object.fromEntries(new URLSearchParams(url.split('?')[1])) }));
    });
    const swap = app.el('itx-swap'), submit = app.el('itx-load');
    assert.equal(swap.disabled, true);
    app.shell.select('subway'); await flush();
    assert.equal(swap.disabled, false);
    app.el('itx-destination').value = '용산'; await app.el('itx-destination').emit('change');
    app.el('itx-date').value = '2026-10-23';
    app.el('itx-time').value = '10:00';
    await submit.emit('click');
    assert.equal(swap.disabled, false);
    await swap.emit('click');
    assert.equal(pending[0].signal.aborted, true);
    assert.equal(pending.length, 1);
    assert.equal(app.el('itx-origin').value, '용산');
    assert.equal(app.el('itx-destination').value, '춘천');
    assert.ok(app.el('itx-destination').children.every(option => option.value !== '용산'));
    assert.equal(app.el('itx-date').value, '2026-10-23');
    assert.equal(app.el('itx-time').value, '10:00');
    await submit.emit('click');
    assert.deepEqual(pending[1].query, { origin: '용산', destination: '춘천', date: '20261023' });
    pending[1].resolve(itxTimetable(pending[1].query)); await flush();
    pending[0].resolve(itxTimetable(pending[0].query)); await flush();
    assert.equal(app.el('itx-results-heading').textContent, '용산역 → 춘천역');
    assert.match(app.el('itx-result-meta').textContent, /10:00 이후.*1편/);
    const list = app.el('itx-train-list');
    assert.equal(list.children.length, 1);
    assert.equal(list.querySelector('time').textContent, '10:00');
    app.el('itx-time').value = '20:00'; await app.el('itx-time').emit('input');
    assert.equal(app.el('itx-results').hidden, true);
    await submit.emit('click');
    pending[2].resolve(itxTimetable(pending[2].query)); await flush();
    assert.match(list.textContent, /20:00 이후 출발하는 열차가 없어요/);
});
test('ITX mobile results group the query once and compare labelled departure/arrival pairs', async () => {
    for (const width of [360, 390]) {
        const app = harness({ width });
        app.loadITX(url => url === '/api/itx/stations' ? itxStations() : itxTimetable());
        app.shell.select('subway'); await flush();
        const button = app.el('itx-load'); button.focus(); await button.emit('click');
        assert.equal(app.el('itx-results').hidden, false);
        assert.equal(app.el('itx-results-heading').textContent, '춘천역 → 마석역');
        assert.match(app.el('itx-result-meta').textContent, /2026-10-19.*2편.*계획 시간표/);
        const list = app.el('itx-train-list'); assert.equal(list.children.length, 2);
        assert.equal(list.children[0].querySelectorAll('time').map(item => item.textContent).join(' / '), '08:05 / 09:14');
        assert.match(list.children[0].textContent, /출발08:05도착09:14.*열차 05001.*성인 3,100원/);
        assert.match(list.children[1].textContent, /요금 미제공/);
        assert.equal(app.el('itx-status').getAttribute('data-state'), 'ok');
        assert.equal(app.document.activeElement, app.el('itx-results-heading'));
        assert.equal(app.el('itx-results-heading').scrollCalls, 1);
    }
});
test('each ITX input change clears its query summary, train results and old notices immediately', async () => {
    for (const id of ['itx-origin', 'itx-destination', 'itx-date']) {
        const app = harness();
        app.loadITX(url => url === '/api/itx/stations' ? itxStations() : itxTimetable());
        app.shell.select('subway'); await flush(); await app.el('itx-load').emit('click');
        assert.equal(app.el('itx-warning').hidden, false);
        app.el(id).value = id === 'itx-date' ? '2026-10-20' : '용산';
        app.el(id).focus(); await app.el(id).emit(id === 'itx-date' ? 'input' : 'change');
        assert.equal(app.el('itx-results').hidden, true);
        assert.equal(app.el('itx-results-heading').textContent, '');
        assert.equal(app.el('itx-result-meta').textContent, '');
        assert.equal(app.el('itx-train-list').children.length, 0);
        assert.equal(app.el('itx-warning').textContent, '');
        assert.equal(app.el('itx-warning').hidden, true);
        assert.equal(app.document.activeElement, app.el(id));
        assert.equal(app.el('itx-load').disabled, false);
    }
});
test('ITX input invalidation aborts pending work and late results cannot replace a newer query', async () => {
    for (const id of ['itx-origin', 'itx-destination', 'itx-date']) {
        const pending = [], app = harness();
        app.loadITX((url, options) => {
            if (url === '/api/itx/stations') return itxStations();
            return new Promise(resolve => pending.push({ resolve, signal: options.signal, query: Object.fromEntries(new URLSearchParams(url.split('?')[1])) }));
        });
        app.shell.select('subway'); await flush(); await app.el('itx-load').emit('click');
        assert.equal(pending.length, 1);
        app.el(id).value = id === 'itx-date' ? '2026-10-20' : '용산'; await app.el(id).emit(id === 'itx-date' ? 'input' : 'change');
        assert.equal(pending[0].signal.aborted, true);
        assert.equal(app.el('itx-timetable').getAttribute('aria-busy'), 'false');
        assert.equal(app.el('itx-load').disabled, false);
        await app.el('itx-load').emit('click'); assert.equal(pending.length, 2);
        pending[1].resolve(itxTimetable(pending[1].query)); await flush();
        const heading = app.el('itx-results-heading').textContent, meta = app.el('itx-result-meta').textContent;
        pending[0].resolve(itxTimetable(pending[0].query)); await flush();
        assert.equal(app.el('itx-results-heading').textContent, heading);
        assert.equal(app.el('itx-result-meta').textContent, meta);
        assert.match(meta, id === 'itx-date' ? /2026-10-20/ : /2026-10-19/);
        assert.equal(app.el('itx-train-list').children.length, 2);
    }
});
test('late ITX responses never move focus after leaving, closing or reentering the tab', async () => {
    for (const action of ['leave', 'close', 'return']) {
        let resolve; const app = harness({ width: 390 });
        app.loadITX(url => url === '/api/itx/stations' ? itxStations() : new Promise(done => { resolve = done; }));
        app.shell.select('subway'); await flush(); await app.el('itx-load').emit('click');
        if (action === 'close') app.shell.closeTool();
        else { app.shell.select('journey'); if (action === 'return') app.shell.select('subway'); }
        const focused = app.document.activeElement;
        resolve(itxTimetable()); await flush();
        assert.equal(app.document.activeElement, focused);
        assert.equal(app.el('itx-results-heading').scrollCalls, undefined);
    }
});
test('ITX empty, unavailable service and mismatched responses remain distinct', async () => {
    for (const kind of ['empty', 'error', 'mismatch']) {
        const app = harness();
        app.loadITX(url => {
            if (url === '/api/itx/stations') return itxStations();
            if (kind === 'error') return { ok: false, status: 502, json: async () => ({ error: 'internal configuration detail' }) };
            const data = itxTimetable();
            if (kind === 'empty') data.trains = [];
            if (kind === 'mismatch') data.date = '20261020';
            return data;
        });
        app.shell.select('subway'); await flush(); await app.el('itx-load').emit('click');
        const status = app.el('itx-status');
        assert.equal(status.getAttribute('data-state'), kind === 'empty' ? 'empty' : 'error');
        if (kind === 'empty') {
            assert.equal(app.el('itx-results').hidden, false);
            assert.match(status.textContent, /미운행·미정차 또는 데이터 미제공/);
            assert.equal(app.el('itx-train-list').querySelectorAll('time').length, 0);
        } else {
            assert.equal(app.el('itx-results').hidden, true);
            assert.equal(app.el('itx-result-meta').textContent, '');
            assert.doesNotMatch(status.textContent, /internal configuration|Blueprint|app\.py/);
        }
        assert.equal(app.el('itx-load').disabled, false);
    }
});
test('ITX station lookup can be retried without reloading the page or stealing initial focus', async () => {
    let count = 0; const app = harness();
    app.loadITX(() => ++count === 1 ? { ok: false, status: 502, json: async () => ({}) } : itxStations());
    app.shell.select('subway'); await flush();
    assert.equal(app.el('itx-status').getAttribute('data-state'), 'error');
    assert.equal(app.el('itx-load').disabled, false);
    assert.equal(app.el('itx-load').textContent, '역 목록 다시 불러오기');
    const focused = app.document.activeElement;
    await app.el('itx-load').emit('click');
    assert.equal(count, 2);
    assert.equal(app.el('itx-origin').disabled, false);
    assert.equal(app.el('itx-load').textContent, '시간표 조회');
    assert.equal(app.document.activeElement, focused);
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
function mapJourneyFixture(label = '300') {
    return { mode: 'journeys', origin: { name: '지도 출발지', lat: 37.88, lon: 127.73 },
        destination: { name: '지도 목적지', lat: 37.89, lon: 127.74 }, warnings: [],
        direct_walk: { status: 'ok', minutes: 30, distance_m: 2100, source: 'tmap' },
        providers: { city_bus: { status: 'ok' }, shuttle: { status: 'unmatched' } },
        journeys: [{ transport_type: 'city_bus', transfer_count: 0, total_min: 19, known_min: 19,
            walk_min: 7, ride_min: 5, wait_min: 7, has_live_departure: true,
            access_walk: { minutes: 4, distance_m: 260 }, egress_walk: { minutes: 3, distance_m: 190 },
            legs: [{ transport_type: 'city_bus', route_id: 'SYNTHETIC_ROUTE_' + label, route_no: label,
                route_number_verified: true, direction_verified: true, arrival_reference_only: false,
                direction: '다음 정류장 성심경로당 방면', next_stop: { name: '성심경로당', lat: 37.881, lon: 127.731 },
                board: { id: 'SYNTHETIC_BOARD', name: '한림대학교', lat: 37.88, lon: 127.73 },
                alight: { id: 'SYNTHETIC_ALIGHT', name: '포스코더샵A', lat: 37.89, lon: 127.74 },
                path: [{ lat: 37.88, lon: 127.73 }, { lat: 37.881, lon: 127.731 }, { lat: 37.89, lon: 127.74 }],
                ride_min: 5, wait_min: 7, stop_count: 5 }], warnings: [], is_estimate: true }],
    };
}
function mapTaxiFixture(overrides = {}) {
    return { status: 'ok', ride_duration_seconds: 420, distance_m: 2500,
        fare: { amount_krw: 6300 }, pickup_wait_seconds: null, door_to_door_seconds: null,
        warnings: [], ...overrides };
}
function mapMobilityRecommendationFixture() {
    const data = mapJourneyFixture(), journey = data.journeys[0];
    data.direct_walk = { status: 'ok', minutes: 7, distance_m: 490, source: 'tmap' };
    journey.access_walk = { minutes: 8, distance_m: 560 };
    journey.egress_walk = { minutes: 2, distance_m: 140 };
    journey.walk_min = 10; journey.wait_min = 4; journey.legs[0].wait_min = 4;
    data.recommendation = {
        status: 'ok', preferred_mode: 'walk', reason: 'stop_walk_overhead',
        walk: { minutes: 7, distance_m: 490 },
        transit: { candidate_index: 0, total_min: 19, access_walk_min: 8,
            egress_walk_min: 2, stop_walk_min: 10, wait_min: 4 },
        time_saved_min: 12, min_bus_access_min: 8, min_bus_walk_min: 10,
        scooter: { status: 'conditional', total_min: null, availability_confirmed: false, route_confirmed: false },
        scope: 'returned_candidates',
    };
    return data;
}
function mapWidgetGeometry(app, { width = 640, height = 420, widgetWidth = 250, widgetHeight = 140 } = {}) {
    const size = { width, height }, stage = app.el('map-stage'), widget = app.el('map-journey-widget');
    const rect = (left, top, width, height) => ({ x: left, y: top, left, top, width, height, right: left + width, bottom: top + height });
    stage.getBoundingClientRect = () => rect(100, 64, size.width, size.height);
    widget.getBoundingClientRect = () => {
        const left = parseFloat(widget.style.left), top = parseFloat(widget.style.top);
        return rect(100 + (Number.isFinite(left) ? left : 16), 64 + (Number.isFinite(top) ? top : 16), widgetWidth, widgetHeight);
    };
    return { size, stage, widget, handle: widget.querySelector('.map-journey-heading'),
        position: () => ({ left: widget.getBoundingClientRect().left - 100, top: widget.getBoundingClientRect().top - 64 }) };
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
    assert.equal(cards[0].querySelector('h3').textContent, 'A0 승차');
    assert.equal(cards[1].querySelector('.route-select').tagName, 'button');
    assert.match(cards[1].querySelector('.route-select').textContent, /경로 상세 보기/);
    assert.equal(cards[1].querySelector('.route-select').getAttribute('aria-controls'), 'route-detail');
    await cards[1].querySelector('.route-select').emit('click');
    assert.equal(routing.maps.at(-1).index, 3);
    assert.equal(cards[1].querySelector('.route-select').getAttribute('aria-pressed'), 'true');
    assert.equal(cards[0].classList.contains('is-selected'), false);
    assert.match(app.el('route-detail-content').textContent, /A1 하차/);
    assert.equal(app.el('route-detail-content').querySelectorAll('.journey-timeline').length, 1);
    app.context.AppModules.renderer.selectJourney(0);
    assert.equal(routing.maps.at(-1).index, 3, 'Malformed filtered candidate is never selectable');
});
test('route cards lead with boarding facts and keep reference sums separate from total estimates', async () => {
    for (const width of [360, 390]) {
        const app = harness({ width }), routing = app.loadRouting(() => routeFixture());
        await app.context.findRoutes();
        let card = app.el('result-container').querySelector('.route-card');
        assert.equal(card.querySelector('.route-confidence').textContent, '대기 포함 추정');
        assert.equal(card.querySelector('.boarding-guide-label').textContent, '타는 정류장');
        assert.equal(card.querySelector('.route-duration-label').textContent, '총 소요 추정');
        assert.equal(card.querySelector('.route-duration').querySelector('strong').textContent, '23');
        assert.ok(card.textContent.indexOf('A0 승차') < card.textContent.indexOf('총 소요 추정'));
        assert.match(card.querySelector('.route-card-direction').textContent, /운행 방향 정보: 테스트 방면/);
        routing.render(routeFixture('참고', true));
        card = app.el('result-container').querySelector('.route-card');
        assert.equal(card.querySelector('.route-confidence').textContent, '참고 경로 · 운행 방향 확인 필요');
        assert.equal(card.querySelector('.boarding-guide-label').textContent, '승차 후보 정류장');
        assert.equal(card.querySelector('.route-duration-label').textContent, '도보+탑승');
        assert.equal(card.querySelector('.route-duration').querySelector('strong').textContent, '18');
        assert.match(card.querySelector('.route-duration-note').textContent, /대기 제외.*환승 여유 제외.*총 소요 미확인/);
        assert.ok(card.textContent.indexOf('참고0 승차') < card.textContent.indexOf('도보+탑승'));
        assert.doesNotMatch(card.textContent, /총 소요 추정|추천|확정|23분/);
        assert.equal(app.context.AppModules.renderer.getSelectedIndex(), 2);
        assert.equal(routing.maps.at(-1).index, 2);
    }
});
test('route certainty labels preserve known waits and keep missing times or direction codes explicit', async () => {
    const app = harness(), routing = app.loadRouting(() => routeFixture()); await app.context.findRoutes();
    const data = routeFixture('보존'); data.journeys = [data.journeys[2]];
    const journey = data.journeys[0];
    journey.has_live_departure = false; journey.total_min = 999;
    journey.warnings = ['승차 전 정류장 안내를 확인해 주세요.'];
    routing.render(data);
    let card = app.el('result-container').querySelector('.route-card');
    assert.equal(card.querySelector('.route-confidence').textContent, '참고 경로 · 총 소요 미확인');
    assert.equal(card.querySelector('.route-duration').querySelector('strong').textContent, '18');
    assert.doesNotMatch(card.textContent, /대기 시간 미확인|999|총 소요 추정/);
    assert.match(card.querySelector('.route-warnings').textContent, /승차 전 정류장 안내/);
    journey.walk_min = journey.ride_min = null;
    journey.access_walk.minutes = journey.egress_walk.minutes = null;
    journey.legs[0].ride_min = null;
    journey.legs[0].direction = '1'; journey.legs[0].direction_verified = false;
    routing.render(data);
    card = app.el('result-container').querySelector('.route-card');
    assert.equal(card.querySelector('.route-duration').querySelector('strong').textContent, '시간 미확인');
    assert.equal(card.querySelector('.route-card-direction').textContent, '운행 방향 정보: 1');
    assert.match(card.querySelector('.route-confidence').textContent, /운행 방향 확인 필요/);
    assert.equal(card.querySelector('.boarding-guide-label').textContent, '승차 후보 정류장');
    assert.match(card.querySelector('.route-warnings').textContent, /상·하행.*승차 전에 확인/);
    assert.doesNotMatch(card.textContent, /도보\+탑승|총 소요 추정|0분|999/);
});
test('next-stop directions match cards and each transfer step without confirming unknown timing', async () => {
    const data = routeFixture('버스', true), journey = data.journeys[2];
    journey.total_unavailable_reason = 'direction_unverified';
    journey.legs[0].arrival_reference_only = true;
    journey.legs[0].next_stop = { id: 'NEXT_1', name: '  한림대병원  ', lat: 37.881, lon: 127.731 };
    journey.legs.push({ ...journey.legs[0], route_no: '환승 버스',
        board: journey.legs[0].alight, alight: { name: '환승 후 하차' },
        next_stop: { id: 'NEXT_2', name: '팔호광장', lat: 37.882, lon: 127.732 } });
    const app = harness(); app.loadRouting(() => data); await app.context.findRoutes();
    const card = app.el('result-container').querySelector('.route-card');
    const directions = app.el('route-detail-content').querySelectorAll('.journey-direction');
    assert.equal(card.querySelector('.route-card-direction').textContent, '다음 정류장 한림대병원 방면');
    assert.deepEqual(directions.map(item => item.textContent), ['다음 정류장 한림대병원 방면', '다음 정류장 팔호광장 방면']);
    assert.equal(card.querySelector('.route-confidence').textContent, '참고 경로 · 운행 방향 확인 필요');
    assert.equal(card.querySelector('.route-duration-label').textContent, '도보+탑승');
    assert.match(app.el('route-detail-content').textContent, /대기 시간 미확인/);
    assert.doesNotMatch(card.textContent, /총 소요 추정|대기 포함 추정/);
});

test('next-stop names are escaped and blank next stops preserve legacy direction responses', async () => {
    const data = routeFixture(), leg = data.journeys[2].legs[0];
    leg.next_stop = { name: '<img src=x onerror="alert(1)">' };
    const app = harness(), routing = app.loadRouting(() => data); await app.context.findRoutes();
    let card = app.el('result-container').querySelector('.route-card');
    assert.equal(card.querySelector('.route-card-direction').textContent, '다음 정류장 <img src=x onerror="alert(1)"> 방면');
    assert.equal(app.el('result-container').querySelectorAll('img').length, 0);
    assert.equal(app.el('route-detail-content').querySelectorAll('img').length, 0);
    assert.match(app.el('route-detail-content').innerHTML, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
    for (const nextStop of [undefined, null, { name: '   ' }]) {
        leg.next_stop = nextStop;
        routing.render(data);
        card = app.el('result-container').querySelector('.route-card');
        assert.equal(card.querySelector('.route-card-direction').textContent, '운행 방향 정보: 테스트 방면');
        assert.equal(app.el('route-detail-content').querySelector('.journey-direction').textContent, '테스트 방면');
    }
    leg.direction = '';
    routing.render(data);
    assert.equal(app.el('result-container').querySelector('.route-card-direction').textContent, '운행 방향 정보 미확인');
    assert.equal(app.el('route-detail-content').querySelector('.journey-direction').textContent, '운행 방향 정보 미확인');
});

test('wide, narrow and mobile result transitions preserve the query and selection', async () => {
    for (const width of [360, 390, 1024, 1440]) {
        const app = harness({ width }); app.loadRouting(() => routeFixture()); await app.context.findRoutes();
        assert.equal(app.el('planner-body').hidden, true);
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
    assert.match(app.el('result-container').textContent, /도보\+탑승/);
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
test('a comparable shuttle shows its planned times and representative stops in local directions', () => {
    const app = harness(), routing = app.loadRouting(() => routeFixture());
    const data = routeFixture();
    const shuttle = data.journeys[2];
    Object.assign(shuttle, { transport_type: 'shuttle', schedule_comparable: true, service_date_confirmed: true,
        has_live_departure: false, availability_confirmed: false, location_is_approximate: true, query_at: '2026-11-26T17:02:00+09:00' });
    Object.assign(shuttle.legs[0], { route_no: '제4노선', direction_verified: false,
        scheduled_departure_at: '2026-11-26T17:10:00+09:00', scheduled_arrival_at: '2026-11-26T17:21:00+09:00' });
    data.journeys = [shuttle];
    routing.render(data);
    const card = app.el('result-container').textContent;
    const detail = app.el('route-detail-content').textContent;
    assert.match(card, /셔틀 시간표 기준/);
    assert.match(card, /총 소요 추정.*23분/);
    assert.match(card, /승하차 위치는 대표 지점 기준/);
    assert.match(detail, /오후 5:10 출발 예정 · 오후 5:21 도착 예상/);
    assert.match(detail, /승차 대기 약 5분 · 학교 시간표 기준/);
    assert.doesNotMatch(card + detail, /대기 시간 미확인|실시간|후 도착/);
});
test('national directions name the shuttle and keep departure-only plans distinct', () => {
    const app = harness(), routing = app.loadRouting(() => routeFixture());
    const origin = { name: '한림대학교', lat: 37.88632, lon: 127.73812 };
    const destination = { name: '춘천역', lat: 37.88529, lon: 127.71803 };
    const start = '2026-11-26T17:02:00+09:00';
    const arrival = '2026-11-26T17:20:00+09:00';
    const shuttle = { mode: 'shuttle', route_name: '제4노선', from_place: origin, to_place: destination,
        departure_at: '2026-11-26T17:10:00+09:00', arrival_at: arrival, duration_min: 10, estimated: true };
    const journey = { segments: [shuttle], complete: true, departure_at: start, arrival_at: arrival, total_min: 18, estimated: true };
    const response = { mode: 'national_journeys', status: 'ok', origin, destination, departure_at: start, journeys: [journey] };
    routing.render(response);
    assert.match(app.el('result-container').textContent, /추천 경로.*셔틀 제4노선.*셔틀 시간표 기준/s);
    assert.match(app.el('route-detail-content').textContent, /학교 셔틀.*오후 5:10 출발 예정 · 오후 5:20 도착 예상/s);
    routing.render({ ...response, status: 'partial', journeys: [{ ...journey, complete: false, arrival_at: null, total_min: null,
        segments: [{ ...shuttle, route_name: '제1노선', arrival_at: null, duration_min: null }] }] });
    assert.match(app.el('result-container').textContent, /총 소요 미확인.*셔틀 제1노선/s);
    assert.match(app.el('route-detail-content').textContent, /오후 5:10 출발 예정/);
    assert.doesNotMatch(app.el('route-detail-content').textContent, /도착 예상|실시간/);
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
    assert.equal(detail.parentElement.id, 'route-detail-inline', 'The map point picker remains unobstructed on a short map');
    app.resize(1440, { width: 640, height: 700 });
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
    assert.match(app.el('demo-from').parentElement.textContent, /출발 시각 · 한국 시간/);
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

test('map selection opens the shared results pipeline without overwriting GPS or search inputs', async () => {
    const app = harness(), routing = app.loadMapJourney();
    app.context.AppState.setCurrentPosition(37.77, 127.66);
    app.context.AppState.setDestination('기존 검색 목적지', 37.76, 127.65);
    const position = JSON.stringify(app.context.AppState.getCurrentPosition());
    const destination = JSON.stringify(app.context.AppState.getDestination());
    const originMode = app.context.AppState.getOriginMode();
    const input = app.el('dest-input'); input.value = '보존할 검색어';
    for (const id of ['map-pick-origin', 'map-pick-destination', 'map-journey-reset']) {
        assert.equal(app.el(id).tagName, 'button'); assert.equal(app.el(id).getAttribute('type'), 'button');
        assert.equal(app.el(id).listeners.get('click').length, 1);
    }
    assert.equal(app.el('planner-body').contains(app.el('map-journey-widget')), false);
    await app.el('map-pick-origin').emit('click');
    assert.equal(routing.controller.pickPoint(37.88, 127.73), true);
    await flush();
    assert.equal(routing.requests.length, 0);
    assert.equal(routing.controller.getSelection().armed, 'destination');
    assert.equal(routing.controller.pickPoint(37.89, 127.74), true);
    await flush(); await flush();
    assert.equal(routing.requests.length, 1);
    assert.deepEqual([routing.requests[0].payload.lat, routing.requests[0].payload.lon,
        routing.requests[0].payload.dest_lat, routing.requests[0].payload.dest_lon], [37.88, 127.73, 37.89, 127.74]);
    assert.equal(routing.requests[0].payload.selection_source, 'map');
    assert.equal(routing.taxiRequests.length, 0); assert.equal(routing.gps.length, 0);
    assert.equal(JSON.stringify(app.context.AppState.getCurrentPosition()), position);
    assert.equal(JSON.stringify(app.context.AppState.getDestination()), destination);
    assert.equal(app.context.AppState.getOriginMode(), originMode);
    assert.equal(input.value, '보존할 검색어');
    assert.equal(app.el('results-section').hidden, false);
    assert.equal(app.el('results-section').dataset.state, 'ready');
    assert.equal(app.shell.getState().active, 'journey');
    assert.equal(routing.presentation.begin, 1); assert.ok(routing.presentation.render >= 2);
    assert.equal(app.document.activeElement, app.el('results-heading'));
    assert.match(app.el('results-summary').textContent, /지도.*출발지.*지도.*도착지/);
    assert.equal(app.el('map-recommendation'), undefined, 'Only the shared result panel displays route cards');
    const card = app.el('result-container').querySelector('.route-card');
    assert.match(card.querySelector('.route-chain').textContent, /300/);
    assert.equal(card.querySelector('.route-card-direction').textContent, '다음 정류장 성심경로당 방면');
    assert.match(app.el('route-detail-content').textContent, /포스코더샵A/);
    assert.equal(routing.mapEffects.filter(item => item.type === 'journey').length, 1);
    const regular = harness(); regular.loadRouting(() => mapJourneyFixture()); await regular.context.findRoutes();
    assert.equal(card.textContent, regular.el('result-container').querySelector('.route-card').textContent,
        'Map points and a search form use identical route-card rendering');
    assert.equal(app.el('route-detail-content').textContent, regular.el('route-detail-content').textContent);
});

test('map picking requires readiness and arming; invalid, single and equal points never fetch providers', async () => {
    const app = harness(), routing = app.loadMapJourney({ ready: false });
    assert.equal(app.el('map-pick-origin').disabled, true);
    routing.controller.armSelection('origin'); assert.equal(routing.controller.pickPoint(37.88, 127.73), false);
    routing.setReady(true); assert.equal(app.el('map-pick-origin').disabled, false);
    assert.equal(routing.controller.pickPoint(37.88, 127.73), false);
    routing.controller.armSelection('origin');
    for (const [lat, lon] of [[NaN, 127.73], [91, 127.73], [37.88, 181], [true, 127.73]]) {
        assert.equal(routing.controller.pickPoint(lat, lon), false);
    }
    assert.equal(routing.controller.pickPoint(37.88, 127.73), true);
    await flush(); assert.equal(routing.requests.length, 0);
    await app.document.emit('keydown', { key: 'Escape' });
    assert.equal(routing.controller.getSelection().armed, null);
    assert.equal(routing.controller.pickPoint(37.89, 127.74), false);
    routing.controller.armSelection('destination'); routing.controller.pickPoint(37.88, 127.73);
    await flush(); await flush();
    assert.equal(routing.requests.length, 0); assert.equal(routing.taxiRequests.length, 0);
    assert.equal(app.el('results-section').hidden, false);
    assert.match(app.el('result-container').textContent, /출발지와 도착지가 같아.*이동할 필요가 없/);
});

test('shared map result cards preserve next-stop directions while unknown waiting stays a reference', async () => {
    const data = mapJourneyFixture(), journey = data.journeys[0];
    journey.total_min = null; journey.wait_min = null; journey.has_live_departure = false;
    journey.total_unavailable_reason = 'wait_unknown'; journey.legs[0].wait_min = null;
    const app = harness(), routing = app.loadMapJourney({ journey: () => data });
    await routing.pickPair(); await flush();
    const card = app.el('result-container').querySelector('.route-card');
    assert.match(card.querySelector('.route-chain').textContent, /300/);
    assert.equal(card.querySelector('.route-card-direction').textContent, '다음 정류장 성심경로당 방면');
    assert.equal(card.querySelector('.route-confidence').textContent, '참고 경로 · 대기 시간 미확인');
    assert.match(card.querySelector('.route-duration-note').textContent, /총 소요 미확인/);
    assert.match(app.el('route-detail-content').textContent, /대기 시간 미확인/);
    assert.doesNotMatch(card.textContent, /총 소요 추정|걸어서 가는 편이.*빨라요/);
    assert.equal(routing.taxiRequests.length, 0);
});

test('shared map cards retain server indices and select the same candidate in the panel and map', async () => {
    const data = mapJourneyFixture(), first = data.journeys[0], second = mapJourneyFixture('400').journeys[0];
    first.total_min = null; first.known_min = 999; second.known_min = 1;
    data.journeys = [null, { legs: [] }, first, second];
    const app = harness(), routing = app.loadMapJourney({ journey: () => data });
    await routing.pickPair(); await flush();
    const cards = app.el('result-container').querySelectorAll('.route-card');
    assert.equal(cards.length, 2);
    assert.deepEqual(cards.map(card => card.getAttribute('data-journey-index')), ['2', '3']);
    assert.equal(app.context.AppModules.renderer.getSelectedIndex(), 2);
    assert.equal(routing.mapEffects.filter(item => item.type === 'journey').at(-1).index, 2);
    assert.doesNotMatch(cards[0].textContent, /999|걸어서 가는 편이.*빨라요/);
    await cards[1].querySelector('.route-select').emit('click');
    assert.equal(app.context.AppModules.renderer.getSelectedIndex(), 3);
    assert.equal(routing.mapEffects.filter(item => item.type === 'journey').at(-1).index, 3);
    assert.match(app.el('route-detail-content').textContent, /400/);
});

test('shared map cards display direct walking times and compare only a complete bus total', async () => {
    for (const walkMinutes of [10, 19, 30]) {
        const data = mapJourneyFixture(); data.direct_walk.minutes = walkMinutes;
        data.journeys[0].warnings = ['합성 검증 안내'];
        const app = harness(), routing = app.loadMapJourney({ journey: () => data });
        await routing.pickPair(); await flush();
        const card = app.el('result-container').querySelector('.route-card');
        assert.equal(card.querySelector('.route-direct-walk').textContent.trim(), '전체 도보 약 ' + walkMinutes + '분');
        assert.equal(card.querySelector('.route-duration').querySelector('strong').textContent, '19');
        if (walkMinutes < 19) {
            assert.match(card.textContent, /걸어서 가는 편이 약 9분 빨라요/);
            assert.match(card.textContent, /가까운 스쿠터도.*확인/);
        } else assert.doesNotMatch(card.textContent, /걸어서 가는 편이.*빨라요|가까운 스쿠터도/);
        assert.doesNotMatch(card.textContent, /스쿠터(?:로|를 타면).*?(?:\d+\s*분|더 빠)/);
        const warnings = card.querySelector('.route-warnings');
        assert.ok(warnings); assert.match(warnings.textContent, /합성 검증 안내/);
        assert.match(app.el('route-detail-content').textContent, /성심경로당 방면/);
        assert.equal(routing.taxiRequests.length, 0);
    }
});

test('known walking remains visible without faster claims when a transit total, wait or live departure is missing', async () => {
    for (const missing of ['total', 'wait', 'leg-wait', 'live', 'live-field', 'reason', 'transfer-wait']) {
        const data = mapJourneyFixture(), journey = data.journeys[0]; data.direct_walk.minutes = 8; journey.known_min = 1;
        if (missing === 'total') journey.total_min = null;
        if (missing === 'wait') delete journey.wait_min;
        if (missing === 'leg-wait') journey.legs[0].wait_min = null;
        if (missing === 'live') journey.has_live_departure = false;
        if (missing === 'live-field') delete journey.has_live_departure;
        if (missing === 'reason') journey.total_unavailable_reason = 'wait_unknown';
        if (missing === 'transfer-wait') {
            journey.transfer_count = 1; journey.legs.push({ ...mapJourneyFixture('301').journeys[0].legs[0], wait_min: null });
        }
        const app = harness(), routing = app.loadMapJourney({ journey: () => data });
        await routing.pickPair(); await flush();
        const card = app.el('result-container').querySelector('.route-card');
        assert.equal(card.querySelector('.route-direct-walk').textContent.trim(), '전체 도보 약 8분');
        assert.match(card.querySelector('.route-duration-note').textContent, /총 소요 미확인/, missing);
        assert.doesNotMatch(card.textContent, /총 소요 추정|걸어서 가는 편이.*빨라요|가까운 스쿠터도/);
        assert.equal(routing.taxiRequests.length, 0);
    }
    for (const missing of ['wait_min', 'has_live_departure']) {
        const data = mapJourneyFixture(); data.journeys[0].total_min = 5; delete data.journeys[0][missing];
        const app = harness(), routing = app.loadMapJourney({ journey: () => data });
        await routing.pickPair(); await flush();
        const card = app.el('result-container').querySelector('.route-card');
        assert.equal(card.querySelector('.route-duration').querySelector('strong').textContent, '12');
        assert.doesNotMatch(card.textContent, /총 소요 추정|걸어서 가는 편이.*빨라요/);
    }
});

test('unavailable or malformed direct walking leaves shared bus cards usable', async () => {
    for (const walk of [undefined, { status: 'unavailable', minutes: null, distance_m: null, source: 'tmap' },
        { status: 'ok', minutes: -1, distance_m: 100, source: 'tmap' },
        { status: 'ok', minutes: NaN, distance_m: 100, source: 'tmap' },
        { status: 'ok', minutes: 5, distance_m: null, source: 'tmap' }]) {
        const data = mapJourneyFixture(); data.direct_walk = walk;
        const app = harness(), routing = app.loadMapJourney({ journey: () => data });
        await routing.pickPair(); await flush();
        const card = app.el('result-container').querySelector('.route-card');
        assert.match(card.querySelector('.route-chain').textContent, /300/);
        assert.equal(card.querySelector('.route-duration').querySelector('strong').textContent, '19');
        assert.equal(card.querySelector('.route-direct-walk'), null);
        assert.doesNotMatch(card.textContent, /걸어서 가는 편이.*빨라요/);
        assert.equal(routing.taxiRequests.length, 0);
        assert.equal(routing.mapEffects.filter(item => item.type === 'journey').length, 1);
    }
});

test('empty and failed map transit queries suggest a taxi using the measured walk without requesting one', async () => {
    for (const kind of ['empty', 'error', 'http-502']) {
        const data = mapJourneyFixture(); data.journeys = [];
        data.direct_walk = { status: 'ok', minutes: 12, distance_m: 840, source: 'tmap' };
        if (kind !== 'empty') { data.error = '합성 버스 공급자 오류'; data.providers.city_bus.status = 'error'; }
        const app = harness(), routing = app.loadMapJourney({
            journey: () => kind === 'http-502' ? { ok: false, status: 502, json: async () => data } : data });
        await routing.pickPair(); await flush(); await flush();
        const card = app.el('result-container').querySelector('.route-walk-card');
        assert.ok(card); assert.match(card.textContent, /택시를 타는 것을 추천해요/);
        assert.match(card.textContent, /도보 약 12분/); assert.match(card.textContent, /840m/);
        assert.equal(card.querySelector('.taxi-room-banner').getAttribute('data-destination'), '지도 목적지');
        assert.doesNotMatch(card.textContent, /걸어서 가는 편이.*빨라요|가까운 스쿠터도/);
        if (kind !== 'empty') assert.match(app.el('result-container').textContent, /합성 버스 공급자 오류/);
        assert.equal(routing.requests.length, 1); assert.equal(routing.taxiRequests.length, 0);
        assert.equal(routing.mapEffects.filter(item => item.type === 'journey').length, 0);
        assert.equal(app.el('results-section').hidden, false);
    }
});

test('late direct walking alternatives never replace a newer map result or reopen a reset panel', async () => {
    for (const action of ['reselect', 'reset']) {
        const pending = [], app = harness(), routing = app.loadMapJourney({ journey: () => new Promise(resolve => pending.push(resolve)) });
        await routing.pickPair(); const oldSignal = routing.requests[0].signal;
        if (action === 'reselect') {
            routing.controller.armSelection('destination'); routing.controller.pickPoint(37.9, 127.75); await flush();
            pending[1](mapJourneyFixture('301')); await flush(); await flush();
        } else await app.el('map-journey-reset').emit('click');
        const content = app.el('result-container').textContent, effects = routing.mapEffects.length;
        pending[0]({ mode: 'journeys', journeys: [], direct_walk: { status: 'ok', minutes: 1, distance_m: 60, source: 'tmap' } });
        await flush(); await flush();
        assert.equal(oldSignal.aborted, true);
        assert.equal(app.el('result-container').textContent, content);
        assert.equal(routing.mapEffects.length, effects); assert.equal(routing.taxiRequests.length, 0);
        if (action === 'reselect') assert.match(content, /301/);
        else assert.equal(app.el('results-section').hidden, true);
    }
});

test('map reference candidates use the same number, direction and shuttle uncertainty labels as regular results', async () => {
    for (const kind of ['number', 'direction', 'arrival-reference', 'shuttle']) {
        const data = mapJourneyFixture(), journey = data.journeys[0], leg = journey.legs[0];
        if (kind === 'number') leg.route_number_verified = false;
        if (kind === 'direction') leg.direction_verified = false;
        if (kind === 'arrival-reference') leg.arrival_reference_only = true;
        if (kind === 'shuttle') {
            journey.transport_type = leg.transport_type = 'shuttle'; journey.availability_confirmed = false;
            journey.has_live_departure = false; journey.total_min = null;
        }
        const app = harness(), routing = app.loadMapJourney({ journey: () => data });
        await routing.pickPair(); await flush();
        const card = app.el('result-container').querySelector('.route-card');
        assert.ok(card); assert.equal(routing.taxiRequests.length, 0);
        if (kind === 'number') assert.match(card.textContent, /노선번호 확인 필요|번호 미확인/);
        else assert.match(card.textContent, /참고 경로/);
        assert.doesNotMatch(card.textContent, /걸어서 가는 편이.*빨라요/);
    }
});

test('map provider failures use the shared empty and error states without automatic taxi fallback', async () => {
    for (const kind of ['empty', 'error', 'network', 'invalid', 'bad-json']) {
        const app = harness(), routing = app.loadMapJourney({ journey: () => {
            if (kind === 'network') throw new Error('합성 네트워크 오류');
            if (kind === 'invalid') return {};
            if (kind === 'bad-json') return { ok: true, json: async () => { throw new Error('합성 JSON 오류'); } };
            return { mode: 'journeys', journeys: [], ...(kind === 'error' ? { error: '합성 공급자 오류' } : {}) };
        } });
        await routing.pickPair(); await flush(); await flush();
        assert.equal(routing.requests.length, 1); assert.equal(routing.taxiRequests.length, 0);
        assert.equal(app.el('results-section').hidden, false);
        assert.equal(app.el('results-section').dataset.state, kind === 'empty' ? 'empty' : 'error');
        assert.ok(app.el('result-container').querySelector('.result-message'));
        assert.equal(app.el('route-submit').disabled, false);
        if (kind === 'invalid') assert.match(app.el('result-container').textContent, /서버 응답 형식/);
        if (kind === 'bad-json') assert.match(app.el('result-container').textContent, /서버 응답을 읽지 못/);
    }
});

test('map reselect and reset abort stale requests while menu navigation suppresses late focus and map movement', async () => {
    for (const action of ['reselect', 'reset', 'menu']) {
        const pending = [], app = harness(), routing = app.loadMapJourney({ journey: () => new Promise(resolve => pending.push(resolve)) });
        await routing.pickPair();
        if (action === 'reselect') {
            routing.controller.armSelection('destination'); routing.controller.pickPoint(37.9, 127.75); await flush();
            pending[1](mapJourneyFixture('301')); await flush(); await flush();
        } else if (action === 'reset') await app.el('map-journey-reset').emit('click');
        else app.shell.select('shuttle', { focus: true });
        const focus = app.document.activeElement, effects = routing.mapEffects.length;
        pending[0](mapJourneyFixture('999')); await flush(); await flush();
        assert.equal(routing.mapEffects.length, effects);
        assert.equal(app.document.activeElement, focus);
        assert.equal(routing.taxiRequests.length, 0);
        if (action !== 'menu') assert.equal(routing.requests[0].signal.aborted, true);
        if (action === 'reselect') assert.match(app.el('result-container').textContent, /301/);
        else assert.equal(app.el('results-section').hidden, true);
        if (action === 'reset') assert.equal(routing.controller.getSelection().origin, null);
        if (action === 'menu') {
            assert.equal(app.shell.getState().active, 'shuttle');
            assert.equal(routing.controller.getSelection().origin.lat, 37.88);
            assert.equal(routing.controller.getSelection().armed, null);
        }
    }
});

test('shared result close and search-edit actions preserve map selections without unsolicited reopening', async () => {
    let release;
    const app = harness(), routing = app.loadMapJourney({ journey: () => new Promise(resolve => { release = resolve; }) });
    await routing.pickPair(); app.shell.closeResults();
    const focus = app.document.activeElement, effects = routing.mapEffects.length;
    release(mapJourneyFixture()); await flush(); await flush();
    assert.equal(app.el('results-section').hidden, true); assert.equal(app.document.activeElement, focus);
    assert.equal(routing.mapEffects.length, effects);
    app.shell.showResults(); assert.equal(app.el('results-section').hidden, false);
    assert.match(app.el('result-container').textContent, /300/);
    await app.el('results-edit').emit('click');
    assert.equal(app.el('results-section').hidden, true);
    assert.equal(routing.controller.getSelection().armed, 'origin');
    assert.equal(routing.controller.getSelection().origin.lat, 37.88);
    assert.equal(routing.controller.getSelection().destination.lat, 37.89);
    assert.equal(routing.requests.length, 1); assert.equal(routing.taxiRequests.length, 0);
});

test('real map clicks select only while armed and draw the shared result as a dashed stop-order path', async () => {
    const app = harness(), routing = app.loadMapJourney({ productionMap: true }), map = routing.mapState;
    assert.equal(map.instances, 1);
    const click = (lat, lon) => map.trigger('click', { coord: { lat: () => lat, lng: () => lon } });
    await click(37.88, 127.73); assert.equal(routing.controller.getSelection().origin, null);
    await map.trigger('tilesloaded'); await app.el('map-pick-origin').emit('click');
    await click(37.88, 127.73); assert.equal(routing.controller.getSelection().armed, 'destination');
    await click(37.89, 127.74); await flush(); await flush();
    assert.equal(routing.requests.length, 1); assert.equal(app.el('results-section').hidden, false);
    assert.equal(map.polylines.filter(item => item.active).length, 1);
    const line = map.polylines.find(item => item.active);
    assert.equal(line.options.strokeStyle, 'dash'); assert.equal(line.options.clickable, false); assert.equal(line.options.path.length, 3);
    assert.ok(map.markers.filter(item => item.active).some(item => /출발/.test(item.options.title)));
    assert.ok(map.markers.filter(item => item.active).some(item => /도착/.test(item.options.title)));
    await app.el('map-journey-reset').emit('click');
    assert.equal(map.polylines.filter(item => item.active).length, 0);
    assert.equal(map.markers.filter(item => item.active).length, 0);
    assert.equal(map.instances, 1);
});

test('shared map paths split at invalid coordinates without bridging a missing route section', async () => {
    const data = mapJourneyFixture();
    data.journeys[0].legs[0].path = [{ lat: 37.88, lon: 127.73 }, { lat: 37.881, lon: 127.731 },
        { lat: NaN, lon: 127.732 }, { lat: 37.883, lon: 127.733 }, { lat: 37.89, lon: 127.74 }];
    const app = harness(), routing = app.loadMapJourney({ productionMap: true, journey: () => data });
    await routing.mapState.trigger('tilesloaded'); await routing.pickPair(); await flush();
    const paths = routing.mapState.polylines.filter(item => item.active).map(item => item.options.path);
    assert.equal(paths.length, 2);
    assert.deepEqual(JSON.parse(JSON.stringify(paths.map(points => points.map(point => [point.lat, point.lon])))), [
        [[37.88, 127.73], [37.881, 127.731]], [[37.883, 127.733], [37.89, 127.74]],
    ]);
    app.context.AppModules.map.clearMapJourney();
    assert.equal(routing.mapState.polylines.filter(item => item.active).length, 0);
});

test('shared map API bounds fetch and body parsing at forty-five seconds without taxi fallback', async () => {
    for (const phase of ['fetch', 'body']) {
        const app = harness(), routing = app.loadMapJourney({ journey: () => phase === 'fetch'
            ? new Promise(() => {}) : { ok: true, json: () => new Promise(() => {}) } });
        await routing.pickPair(); await flush();
        assert.equal(app.el('results-section').dataset.state, 'loading');
        await app.advance(44999); assert.equal(app.el('results-section').dataset.state, 'loading');
        await app.advance(1); await flush();
        assert.equal(routing.requests[0].signal.aborted, true);
        assert.equal(app.el('results-section').dataset.state, 'error');
        assert.match(app.el('result-container').textContent, /조회가 오래 걸리고/);
        assert.equal(app.el('route-submit').disabled, false);
        assert.equal(routing.taxiRequests.length, 0);
        await app.advance(60000); assert.equal(routing.requests.length, 1);
        routing.controller.retry(); await flush(); assert.equal(routing.requests.length, 2);
        await app.el('map-journey-reset').emit('click');
    }
});

test('reset cancels a shared map API request without timeout errors or reopening the results', async () => {
    const app = harness(), routing = app.loadMapJourney({ journey: () => new Promise(() => {}) });
    await routing.pickPair(); await flush();
    await app.el('map-journey-reset').emit('click'); await flush();
    assert.equal(routing.requests[0].signal.aborted, true);
    await app.advance(90000);
    assert.equal(routing.requests.length, 1); assert.equal(routing.taxiRequests.length, 0);
    assert.equal(app.el('results-section').hidden, true);
    assert.doesNotMatch(app.el('result-container').textContent, /조회가 오래 걸리고/);
});

test('map point labels hide raw coordinates while keeping the selected coordinates in provider requests', async () => {
    const app = harness(), routing = app.loadMapJourney();
    assert.equal(app.el('map-origin-label').textContent, '지도에서 선택');
    assert.equal(app.el('map-destination-label').textContent, '지도에서 선택');
    routing.controller.armSelection('origin'); routing.controller.pickPoint(37.88001, 127.73002);
    assert.equal(app.el('map-origin-label').textContent, '출발지 선택 완료');
    assert.equal(app.el('map-destination-label').textContent, '지도에서 선택');
    assert.equal(routing.requests.length, 0);
    routing.controller.pickPoint(37.89003, 127.74004); await flush(); await flush();
    assert.equal(app.el('map-destination-label').textContent, '목적지 선택 완료');
    assert.doesNotMatch(app.el('map-journey-widget').textContent, /37\.88001|127\.73002|37\.89003|127\.74004/);
    assert.deepEqual([routing.requests[0].payload.lat, routing.requests[0].payload.lon,
        routing.requests[0].payload.dest_lat, routing.requests[0].payload.dest_lon], [37.88001, 127.73002, 37.89003, 127.74004]);
    routing.controller.reset();
    assert.equal(app.el('map-origin-label').textContent, '지도에서 선택');
    assert.equal(app.el('map-destination-label').textContent, '지도에서 선택');
});

test('widget mouse, touch and pen drags stay inside the map without selecting points or requesting routes', async () => {
    for (const pointerType of ['mouse', 'touch', 'pen']) {
        const app = harness(), geometry = mapWidgetGeometry(app), routing = app.loadMapJourney();
        const { handle, widget, stage, position } = geometry;
        let mapPointerStarts = 0;
        stage.addEventListener('pointerdown', () => { mapPointerStarts++; });
        routing.controller.armSelection('origin');
        const selection = JSON.stringify(routing.controller.getSelection());
        const initial = position();
        assert.equal(handle.getAttribute('tabindex'), '0');
        assert.equal(handle.getAttribute('role'), 'group');
        assert.match(handle.getAttribute('aria-label'), /위젯 이동/);
        assert.equal(handle.listeners.get('pointerdown').length, 1);
        await handle.emit('pointerdown', { pointerType, pointerId: 3, isPrimary: false, button: 0, clientX: 150, clientY: 100 });
        await handle.emit('pointerdown', { pointerType, pointerId: 4, isPrimary: true, button: 2, clientX: 150, clientY: 100 });
        await app.document.emit('pointermove', { pointerId: 4, clientX: 9999, clientY: 9999 });
        assert.deepEqual(position(), initial);
        assert.equal(handle.hasPointerCapture(4), false);
        mapPointerStarts = 0;
        await handle.emit('pointerdown', { pointerType, pointerId: 7, isPrimary: true, button: 0, clientX: 150, clientY: 100 });
        assert.equal(mapPointerStarts, 0, 'The drag handle stops the pointer gesture before the map receives it');
        assert.equal(handle.hasPointerCapture(7), true);
        await app.document.emit('pointermove', { pointerId: 99, clientX: 9999, clientY: 9999 });
        assert.deepEqual(position(), initial, 'Another pointer cannot move the widget');
        await app.document.emit('pointermove', { pointerId: 7, clientX: 9999, clientY: 9999 });
        assert.deepEqual(position(), { left: 382, top: 272 });
        await app.document.emit('pointermove', { pointerId: 7, clientX: -9999, clientY: -9999 });
        assert.deepEqual(position(), { left: 8, top: 8 });
        const ending = pointerType === 'mouse' ? 'pointerup' : pointerType === 'touch' ? 'pointercancel' : 'lostpointercapture';
        if (ending === 'lostpointercapture') {
            handle.releasePointerCapture(7); await handle.emit(ending, { pointerId: 7 });
        } else await app.document.emit(ending, { pointerId: 7 });
        assert.equal(handle.hasPointerCapture(7), false);
        await app.document.emit('pointermove', { pointerId: 7, clientX: 9999, clientY: 9999 });
        assert.deepEqual(position(), { left: 8, top: 8 }, 'The completed or cancelled drag no longer moves the widget');
        assert.match(widget.style.left, /^\d+px$/); assert.match(widget.style.top, /^\d+px$/);
        const afterDrag = position();
        await app.el('map-journey-reset').emit('pointerdown', { pointerType, pointerId: 8, isPrimary: true, button: 0, clientX: 150, clientY: 100 });
        await app.document.emit('pointermove', { pointerId: 8, clientX: 9999, clientY: 9999 });
        assert.deepEqual(position(), afterDrag, 'The reset button never starts a drag');
        assert.equal(JSON.stringify(routing.controller.getSelection()), selection);
        assert.equal(routing.requests.length, 0); assert.equal(routing.taxiRequests.length, 0); assert.equal(routing.gps.length, 0);
    }
});

test('widget position reclamps after resize and supports focused arrow, shifted-arrow and Home controls', async () => {
    const app = harness(), geometry = mapWidgetGeometry(app), routing = app.loadMapJourney();
    const { handle, size, position } = geometry;
    await handle.emit('pointerdown', { pointerType: 'mouse', pointerId: 10, isPrimary: true, button: 0, clientX: 150, clientY: 100 });
    await app.document.emit('pointermove', { pointerId: 10, clientX: 9999, clientY: 9999 });
    await app.document.emit('pointerup', { pointerId: 10 });
    assert.deepEqual(position(), { left: 382, top: 272 });
    size.width = 320; size.height = 230;
    app.resize(390); await flush();
    assert.deepEqual(position(), { left: 62, top: 82 }, 'Window and observed map resizes keep the whole widget in bounds');
    await handle.emit('keydown', { key: 'ArrowLeft' });
    assert.deepEqual(position(), { left: 52, top: 82 });
    await handle.emit('keydown', { key: 'ArrowUp', shiftKey: true });
    assert.deepEqual(position(), { left: 52, top: 52 });
    const retained = position();
    await app.el('map-journey-reset').emit('keydown', { key: 'ArrowRight' });
    assert.deepEqual(position(), retained, 'Arrow keys on a child button retain their normal behavior');
    await handle.emit('keydown', { key: 'Home' });
    assert.deepEqual(position(), { left: 16, top: 16 });
    await handle.emit('pointerdown', { pointerType: 'mouse', pointerId: 11, isPrimary: true, button: 0, clientX: 150, clientY: 100 });
    for (const callback of app.windowListeners.get('blur') || []) callback();
    await app.document.emit('pointermove', { pointerId: 11, clientX: 9999, clientY: 9999 });
    assert.deepEqual(position(), { left: 16, top: 16 }, 'Losing the window ends an active drag');
    assert.equal(routing.requests.length, 0); assert.equal(routing.taxiRequests.length, 0); assert.equal(routing.gps.length, 0);
});

test('a walking recommendation leads the shared panel with full-trip times and the stop access overhead', async () => {
    const data = mapMobilityRecommendationFixture(), app = harness();
    const routing = app.loadMapJourney({ journey: () => data });
    await routing.pickPair(); await flush();
    const container = app.el('result-container'), top = container.querySelector('.route-mobility-recommendation');
    const bus = container.querySelector('.route-card');
    assert.ok(top); assert.ok(bus);
    assert.ok(container.children.indexOf(top) < container.children.indexOf(bus));
    assert.match(top.querySelector('h3').textContent, /도보 우선/);
    assert.match(top.textContent, /도보.*7분/);
    assert.match(top.textContent, /버스.*19분/);
    assert.match(top.textContent, /정류장까지.*8분/);
    assert.match(top.textContent, /확인된 버스 경로보다 약 12분 짧아요/);
    assert.doesNotMatch(top.textContent, /모든 경로|가장 빠|최단 경로/);
    assert.equal(bus.querySelector('.route-rank').textContent, '버스 대안 01');
    assert.equal(bus.querySelector('.route-direct-walk').textContent.trim(), '전체 도보 약 7분');
    assert.doesNotMatch(bus.textContent, /걸어서 가는 편이.*빨라요|가까운 스쿠터도|킥보드가 가까이/);
    assert.equal(routing.requests.length, 1); assert.equal(routing.taxiRequests.length, 0);
});

test('the independent walking card does not change bus indices, selection or route details', async () => {
    const data = mapMobilityRecommendationFixture(), first = data.journeys[0];
    data.journeys = [null, { legs: [] }, first, mapJourneyFixture('301').journeys[0]];
    data.recommendation.transit.candidate_index = 2;
    const app = harness(), routing = app.loadMapJourney({ journey: () => data });
    await routing.pickPair(); await flush();
    const container = app.el('result-container'), top = container.querySelector('.route-mobility-recommendation');
    const cards = container.querySelectorAll('.route-card');
    assert.equal(container.querySelectorAll('.route-mobility-recommendation').length, 1);
    assert.equal(top.querySelectorAll('[data-journey-index]').length, 0);
    assert.deepEqual(cards.map(card => card.getAttribute('data-journey-index')), ['2', '3']);
    assert.deepEqual(cards.map(card => card.querySelector('.route-rank').textContent), ['버스 대안 01', '버스 대안 02']);
    assert.equal(app.context.AppModules.renderer.getSelectedIndex(), 2);
    assert.equal(routing.mapEffects.filter(effect => effect.type === 'journey').at(-1).index, 2);
    await cards[1].querySelector('.route-select').emit('click');
    assert.equal(app.context.AppModules.renderer.getSelectedIndex(), 3);
    assert.equal(routing.mapEffects.filter(effect => effect.type === 'journey').at(-1).index, 3);
    assert.match(app.el('route-detail-content').textContent, /301/);
    assert.match(app.el('route-detail-content').textContent, /성심경로당 방면/);
    assert.equal(container.querySelector('.route-mobility-recommendation'), top);
    assert.doesNotMatch(cards.map(card => card.textContent).join(''), /걸어서 가는 편이.*빨라요|가까운 스쿠터도/);
});

test('walking advice never confirms a faster bus comparison when waiting or live departure is missing', async () => {
    for (const missing of ['wait', 'leg-wait', 'live', 'live-field', 'candidate']) {
        const data = mapMobilityRecommendationFixture(), journey = data.journeys[0];
        data.recommendation.reason = 'short_trip';
        data.recommendation.transit.total_min = null; data.recommendation.time_saved_min = null;
        if (missing === 'wait') journey.wait_min = null;
        if (missing === 'leg-wait') journey.legs[0].wait_min = null;
        if (missing === 'live') journey.has_live_departure = false;
        if (missing === 'live-field') delete journey.has_live_departure;
        if (missing === 'candidate') data.recommendation.transit.candidate_index = 999;
        const app = harness(), routing = app.loadMapJourney({ journey: () => data });
        await routing.pickPair(); await flush();
        const container = app.el('result-container'), top = container.querySelector('.route-mobility-recommendation');
        assert.ok(top); assert.match(top.textContent, /도보.*7분/);
        assert.doesNotMatch(top.textContent, /버스.*19분|12분.*짧|빨라요|가장 빠|최단/);
        assert.match(top.textContent, /미확인|비교.*확인|확인.*비교/);
        assert.doesNotMatch(container.querySelector('.route-card').textContent, /걸어서 가는 편이.*빨라요/);
        assert.equal(routing.taxiRequests.length, 0);
    }
});

test('a 110-minute walk to Yongsan Reservoir leads with a taxi suggestion and preserves the bus candidates', async () => {
    const data = mapMobilityRecommendationFixture();
    data.destination = { name: '용산저수지', lat: 37.9434239, lon: 127.71852598 };
    data.direct_walk = { status: 'ok', minutes: 110, distance_m: 8637, source: 'tmap' };
    data.journeys[0].wait_min = null;
    data.journeys[0].total_min = null;
    Object.assign(data.recommendation, { preferred_mode: 'undetermined', reason: 'comparison_unknown', time_saved_min: null });
    data.recommendation.transit.total_min = null;
    const app = harness(), routing = app.loadMapJourney({ journey: () => data });
    await routing.pickPair(); await flush();
    const container = app.el('result-container');
    const taxi = container.querySelector('.route-taxi-recommendation');
    const bus = container.querySelector('.route-card');
    assert.ok(taxi && bus && container.children.indexOf(taxi) < container.children.indexOf(bus));
    assert.match(taxi.textContent, /택시를 타는 것을 추천해요/);
    assert.match(taxi.textContent, /도보 약 110분/);
    assert.match(taxi.textContent, /버스 도착 시각을 확인하지 못했어요/);
    assert.equal(taxi.querySelector('.taxi-room-banner').getAttribute('data-destination'), '용산저수지');
    assert.doesNotMatch(taxi.textContent, /도보 우선|확인된 도보 경로|빨라요|가장 빠|짧아요/);
});

test('scooter advice stays conditional without an ETA and absent or transit recommendations keep legacy cards', async () => {
    for (const mode of ['conditional', 'not-suggested', 'absent', 'transit', 'unavailable']) {
        const data = mapMobilityRecommendationFixture();
        if (mode === 'not-suggested') data.recommendation.scooter.status = 'not_suggested';
        if (mode === 'absent') delete data.recommendation;
        if (mode === 'transit') data.recommendation.preferred_mode = 'transit';
        if (mode === 'unavailable') data.recommendation.status = 'unavailable';
        const app = harness(), routing = app.loadMapJourney({ journey: () => data });
        await routing.pickPair(); await flush();
        const container = app.el('result-container'), top = container.querySelector('.route-mobility-recommendation');
        const bus = container.querySelector('.route-card');
        if (mode === 'conditional' || mode === 'not-suggested') {
            assert.ok(top);
            const scooterNotes = top.querySelectorAll('p').filter(note => /킥보드/.test(note.textContent));
            if (mode === 'conditional') {
                assert.equal(scooterNotes.length, 1);
                assert.match(scooterNotes[0].textContent, /가까이 있다면/);
                assert.match(scooterNotes[0].textContent, /기기 접근.*주행 가능한 경로.*반납.*확인/);
                assert.doesNotMatch(scooterNotes[0].textContent, /\d+\s*분|가용.*확인 완료|대여 가능 확정|더 빠/);
            } else {
                assert.equal(scooterNotes.length, 0);
                assert.doesNotMatch(top.querySelector('h3').textContent, /킥보드/);
            }
        } else {
            assert.equal(top, null);
            assert.equal(bus.querySelector('.route-rank').textContent, '경로 01');
            assert.match(bus.textContent, /걸어서 가는 편이 약 12분 빨라요/);
        }
        assert.equal(app.context.AppModules.renderer.getSelectedIndex(), 0);
        assert.equal(routing.taxiRequests.length, 0);
    }
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
