/** Network-free controller checks in a small memory DOM; not a layout/browser test. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const source = name => fs.readFileSync(path.join(root, 'static/js', name), 'utf8');
const markup = fs.readFileSync(path.join(root, 'templates/partials/commute_advice.html'), 'utf8');
const tests = [], test = (name, run) => tests.push({ name, run });
const flush = async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); };
function fixture(selected = 'hallym', overrides = {}) {
    return { status: 'ok', campus: { id: selected }, summary: '준비물을 확인해 주세요.',
        period: { label: '앞으로 6시간' },
        weather: { status: 'ok', temperature_c: 3, wind_speed_ms: 4.5, rain_probability: 70, issued_at: '2030-10-21T08:00:00+09:00', forecast_at: '2030-10-21T09:00:00+09:00', message: '다음 예보 시각의 날씨예요.' },
        air: { status: 'ok', station_name: '중앙로', observed_at: '2030-10-21T08:00:00+09:00', pm25: { value: 55, grade: '나쁨' }, pm10: { value: 82, grade: '나쁨' }, message: '주변 측정소 참고값이에요.' },
        alerts: [
            { id: 'rain', title: '우산을 챙기세요', message: '오후에 비 예보가 있어요.', at: '2030-10-21T13:00:00+09:00' },
            { id: 'dust', title: '마스크를 챙기세요', message: '초미세먼지가 나쁨 수준이에요.' },
            { id: 'cold_wind', title: '장갑을 챙기세요', message: '추위와 바람이 예상돼요.' },
        ], ...overrides };
}
function harness(handler = async selected => fixture(selected)) {
    let now = Date.parse('2030-10-21T08:30:00+09:00'), next = 0, document;
    const timers = new Map(), nodes = new Map(), requests = [];
    class Events {
        constructor() { this.listeners = new Map(); }
        addEventListener(name, callback) { this.listeners.set(name, [...(this.listeners.get(name) || []), callback]); }
        dispatchEvent(event) { for (const callback of this.listeners.get(event.type) || []) callback(event); return true; }
    }
    class Element extends Events {
        constructor(tag = 'div', id = '') {
            super(); this.tagName = tag; this.id = id; this.children = []; this.parentElement = null; this.attributes = {};
            this.dataset = {}; this.hidden = false; this.disabled = false; this._text = ''; this.className = '';
            this.classList = {
                contains: name => this.className.split(/\s+/).includes(name),
                toggle: (name, enabled) => { const names = new Set(this.className.split(/\s+/).filter(Boolean)); if (enabled) names.add(name); else names.delete(name); this.className = [...names].join(' '); },
                add: name => this.classList.toggle(name, true), remove: name => this.classList.toggle(name, false),
            };
        }
        set textContent(value) { this._text = String(value); this.children = []; }
        get textContent() { return this._text + this.children.map(node => node.textContent).join(''); }
        appendChild(node) { node.parentElement = this; this.children.push(node); return node; }
        append(...children) { children.forEach(child => this.appendChild(child)); }
        replaceChildren(...children) { this.children.forEach(child => { child.parentElement = null; }); this.children = []; this._text = ''; this.append(...children); }
        contains(node) { return node === this || this.children.some(child => child.contains(node)); }
        setAttribute(name, value) { this.attributes[name] = String(value); }
        getAttribute(name) { return this.attributes[name] ?? null; }
        focus() { document.activeElement = this; }
    }
    class Event { constructor(type, properties = {}) { this.type = type; Object.assign(this, properties); } preventDefault() {} }
    document = new Events(); document.readyState = 'loading'; document.hidden = false;
    document.activeElement = new Element('body');
    for (const match of markup.matchAll(/id="([^"]+)"/g)) nodes.set(match[1], new Element('div', match[1]));
    for (const id of ['commute-advice-toggle', 'commute-advice-count', 'commute-advice-toggle-status']) nodes.set(id, new Element('button', id));
    const panel = nodes.get('commute-advice-panel'); panel.hidden = true;
    for (const [id, node] of nodes) if (id !== panel.id && !['commute-advice-toggle', 'commute-advice-count', 'commute-advice-toggle-status'].includes(id)) panel.appendChild(node);
    document.getElementById = id => nodes.get(id) || null;
    document.createElement = tag => new Element(tag);
    document.createElementNS = (_, tag) => new Element(tag);
    const window = new Events(); window.CustomEvent = Event;
    window.setTimeout = (callback, delay) => { const id = ++next; timers.set(id, { at: now + delay, callback }); return id; };
    window.clearTimeout = id => timers.delete(id);
    window.fetch = async (url, options) => {
        const selected = new URL(url, 'http://test.invalid').searchParams.get('campus');
        requests.push({ selected, options });
        const result = await handler(selected, requests.length, options);
        return { ok: result !== false, json: async () => result };
    };
    class ClockDate extends Date { constructor(...args) { super(...(args.length ? args : [now])); } static now() { return now; } }
    const context = vm.createContext({ window, document, console, Date: ClockDate, Intl, Set, AbortController,
        setTimeout: window.setTimeout, clearTimeout: window.clearTimeout, encodeURIComponent });
    vm.runInContext(source('main.js'), context);
    vm.runInContext(source('commute-advice.js'), context);
    const api = window.AppModules.commuteAdvice;
    api.init();
    return { window, document, nodes, requests, api, el: id => nodes.get(id),
        async click(id) {
            const node = nodes.get(id);
            if (node.disabled) return;
            document.dispatchEvent(new Event('pointerdown', { target: node }));
            node.dispatchEvent(new Event('click', { target: node })); await flush();
        },
        async select(selected) { window.AppState.setSelectedCampus(selected); await flush(); },
        async visible(value) { document.hidden = !value; document.dispatchEvent(new Event('visibilitychange')); await flush(); },
        async advance(ms) {
            const target = now + ms;
            for (;;) {
                const due = [...timers.entries()].sort((a, b) => a[1].at - b[1].at).find(([, timer]) => timer.at <= target);
                if (!due) break;
                now = due[1].at; timers.delete(due[0]); due[1].callback(); await flush();
            }
            now = target; await flush();
        },
    };
}

test('all simultaneous advice and measured/forecast labels render without focus theft', async () => {
    const app = harness(); await flush();
    assert.equal(app.el('commute-advice-panel').hidden, false);
    assert.equal(app.document.activeElement.tagName, 'body');
    assert.equal(app.el('commute-advice-alerts').children.length, 3);
    assert.equal(app.el('commute-advice-count').textContent, '3');
    assert.equal(app.el('commute-weather-temperature').textContent, '3℃');
    assert.equal(app.el('commute-weather-wind').textContent, '4.5m/s');
    assert.match(app.el('commute-weather-time').textContent, /예보.*발표/);
    assert.match(app.el('commute-air-time').textContent, /측정소.*춘천 참고값.*측정/);
});
test('closing, reopening and reinitializing preserve input state and one request', async () => {
    const app = harness(); await flush(); app.api.init();
    await app.click('commute-advice-close');
    assert.equal(app.el('commute-advice-panel').hidden, true);
    assert.equal(app.document.activeElement.id, 'commute-advice-toggle');
    await app.click('commute-advice-toggle');
    assert.equal(app.document.activeElement.id, 'commute-advice-heading');
    app.document.dispatchEvent({ type: 'keydown', key: 'Escape', preventDefault() {} });
    assert.equal(app.el('commute-advice-panel').hidden, true);
    assert.equal(app.requests.length, 1);
    await app.click('commute-advice-toggle'); await app.click('commute-advice-refresh');
    assert.equal(app.requests.length, 2);
});
test('a late uncancellable campus response cannot replace the new campus or reopen a closed panel', async () => {
    let release;
    const app = harness((selected, count) => count === 1 ? new Promise(resolve => { release = resolve; }) : fixture(selected));
    await flush(); await app.click('commute-advice-close');
    await app.select('knu');
    assert.equal(app.el('commute-advice-campus').textContent, '강원대학교 기준');
    release(fixture('hallym', { summary: '오래된 응답' })); await flush();
    assert.equal(app.el('commute-advice-campus').textContent, '강원대학교 기준');
    assert.doesNotMatch(app.el('commute-advice-status').textContent, /오래된/);
    assert.equal(app.el('commute-advice-panel').hidden, true);
    await app.select('knu'); await app.select('invalid');
    assert.equal(app.requests.length, 2, 'Unchanged/invalid campus does not re-query');
});
test('partial and stale data keep only confirmed alerts without claiming healthy air', async () => {
    const response = fixture(); response.status = 'partial'; response.air = { status: 'unavailable', message: '대기질 조회 불가' };
    const app = harness(async () => response); await flush();
    assert.equal(app.el('commute-air-values').hidden, true);
    assert.deepEqual(app.el('commute-advice-alerts').children.map(row => row.dataset.advice), ['rain', 'cold_wind']);
    response.weather.status = 'stale'; await app.click('commute-advice-refresh');
    assert.equal(app.el('commute-weather-badge').textContent, '자료 지연');
    assert.equal(app.el('commute-advice-alerts').children.length, 0);
});
test('HTTP errors, invalid JSON shape and campus mismatch clear previous values', async () => {
    const wrong = fixture('knu');
    const results = [fixture(), false, { status: 'ok', weather: null, air: null }, wrong];
    const app = harness(async (_, count) => results[count - 1]); await flush();
    for (let n = 0; n < 3; n++) {
        await app.click('commute-advice-refresh');
        assert.equal(app.el('commute-advice-alerts').children.length, 0);
        assert.equal(app.el('commute-weather-values').hidden, true);
        assert.equal(app.el('commute-air-values').hidden, true);
        assert.match(app.el('commute-advice-status').textContent, /불러오지 못/);
        assert.equal(app.el('commute-advice-refresh').disabled, false);
    }
});
test('missing values stay unknown while zero readings are preserved', async () => {
    const data = fixture(); data.weather.temperature_c = 0; data.weather.wind_speed_ms = null; data.weather.rain_probability = 0;
    data.air.pm25 = { value: null, grade: null }; data.air.pm10 = { value: 0, grade: '좋음' };
    const app = harness(async () => data); await flush();
    assert.equal(app.el('commute-weather-temperature').textContent, '0℃');
    assert.equal(app.el('commute-weather-wind').textContent, '—');
    assert.equal(app.el('commute-weather-rain').textContent, '0%');
    assert.equal(app.el('commute-air-pm25-grade').textContent, '확인 불가');
    assert.equal(app.el('commute-air-pm10-value').textContent, '0㎍/㎥');
});
test('provider text is inserted literally and duplicate alert IDs are ignored', async () => {
    const data = fixture(); const injection = '<img src=x onerror="bad()">';
    data.alerts[0].title = injection; data.alerts[0].message = injection; data.alerts.push(data.alerts[0]); data.air.station_name = injection;
    const app = harness(async () => data); await flush();
    assert.equal(app.el('commute-advice-alerts').children.length, 3);
    assert.match(app.el('commute-advice-alerts').textContent, /<img/);
    assert.match(app.el('commute-air-time').textContent, /<img/);
    const descend = node => [node, ...node.children.flatMap(descend)];
    assert.equal(descend(app.el('commute-advice-alerts')).filter(node => node.tagName === 'img').length, 0);
});
test('hidden pages pause polling and due data refreshes once on return', async () => {
    const app = harness(); await flush();
    await app.advance(14 * 60 * 1000); assert.equal(app.requests.length, 1);
    await app.visible(false); await app.advance(20 * 60 * 1000); assert.equal(app.requests.length, 1);
    await app.visible(true); assert.equal(app.requests.length, 2);
    await app.advance(15 * 60 * 1000); assert.equal(app.requests.length, 3);
});
test('a timed-out request recovers controls and does not invent advice', async () => {
    const app = harness((_, __, options) => new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new Error('aborted')))));
    await flush(); await app.advance(22000);
    assert.equal(app.el('commute-advice-refresh').disabled, false);
    assert.equal(app.el('commute-advice-alerts').children.length, 0);
    assert.match(app.el('commute-advice-status').textContent, /불러오지 못/);
});

(async () => {
    let failures = 0;
    for (const item of tests) {
        try { await item.run(); console.log('PASS ' + item.name); }
        catch (error) { failures++; console.error('FAIL ' + item.name + '\n' + (error.stack || error)); }
    }
    console.log(`${tests.length - failures}/${tests.length} preparation-advice memory DOM checks passed; no browser, API or .env used.`);
    if (failures) process.exitCode = 1;
})();
