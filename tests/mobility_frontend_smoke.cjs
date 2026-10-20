/** Mobility UI checks. Providers are mocked. Opt-in live room checks require a disposable local server/DB. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('playwright');
const { openApp: openBaseApp, activateRealMobility, chooseBuilding, selectDestination, clearDestination, noOverflow, resultContains,
    taxiFixture, journeyFixture, DESTINATION, INJECTION, BASE_URL } = require('./frontend_smoke.cjs');

// These checks deliberately activate preserved live markup, with provider requests mocked.
const openApp = (browser, options = {}) => openBaseApp(browser, { ...options, realMobility: true });

const tests = [];
const test = (name, run) => tests.push({ name, run });
const PUBLIC_MEETING = { name: '자동화 검증용 공개 정문', sub: '실제 모집 장소 예시가 아님', lat: 37.8848, lon: 127.7381 };
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const visibleText = (page, selector, text) => page.locator(selector).filter({ hasText: text }).waitFor({ state: 'visible' });
const polls = app => app.roomRequests.filter(request => request.method === 'GET' && request.pathname.endsWith('/messages'));
const quotes = app => app.roomRequests.filter(request => request.method === 'GET' && request.pathname.endsWith('/estimate'));

async function preview(page, filename) {
    if (!process.env.CAPSTONE_SCREENSHOT_DIR) return;
    await fs.mkdir(process.env.CAPSTONE_SCREENSHOT_DIR, { recursive: true });
    await page.screenshot({ path: path.join(process.env.CAPSTONE_SCREENSHOT_DIR, filename), fullPage: true, animations: 'disabled' });
}

async function search(page) {
    await chooseBuilding(page);
    await selectDestination(page);
    await page.locator('#route-submit').click();
}

function mockRoom(options = {}) {
    const room = { id: '11111111-1111-4111-8111-111111111111', title: '자동화 검증용 모집방',
        meeting: PUBLIC_MEETING, destination: DESTINATION, departure_at: new Date(Date.now() + 3600000).toISOString(),
        capacity: 4, participant_count: 2, status: 'open', room_version: 2,
        created_at: new Date().toISOString(), closed_at: null };
    const participants = [{ member_id: 'member-host', nickname: '검증 방장', is_host: true },
        { member_id: 'member-guest', nickname: '검증 참가자', is_host: false }];
    const self = { joined: true, is_host: true, member_id: 'member-host' };
    const state = { room, participants, self, messageCalls: 0, estimateCalls: 0, messages: options.messages || [], failPoll: false };
    const metadata = () => ({ room: { ...room }, self: { ...self }, participants: participants.map(member => ({ ...member })) });
    const estimate = () => ({ estimate: taxiFixture({}, { ttl: options.ttl || 60 }), room_version: room.room_version,
        participant_count: room.participant_count,
        current_split: participants.map((member, index) => ({ ...member, amount_krw: index ? 5000 : 5001 })),
        capacity_preview: { count: room.capacity, amounts_krw: [2501, 2500, 2500, 2500] } });
    state.handler = async (route, url, request) => {
        if (url.pathname.endsWith('/session')) return route.fulfill({ json: { ready: true, csrf_token: 'test-csrf' } });
        if (url.pathname === '/api/ride-rooms') return route.fulfill({ json: { rooms: [metadata()], next_cursor: null } });
        if (url.pathname.endsWith('/estimate')) { state.estimateCalls += 1; return route.fulfill({ json: estimate() }); }
        if (url.pathname.endsWith('/messages') && request.method === 'GET') {
            state.messageCalls += 1;
            if (options.messageHandler) return options.messageHandler(route, url, state, metadata);
            if (state.failPoll) return route.fulfill({ status: 503, json: { code: 'TEMPORARY', error: '검증용 일시 연결 실패' } });
            const after = Number(url.searchParams.get('after_id') || 0);
            const messages = state.messages.filter(message => message.id > after);
            return route.fulfill({ json: { ...metadata(), messages, next_after_id: messages.at(-1)?.id || after, has_more: false, cached_estimate: null } });
        }
        if (url.pathname.endsWith('/messages') && request.method === 'POST' && options.sendHandler) return options.sendHandler(route, request, state);
        if (url.pathname.endsWith('/close')) { room.status = 'closed'; room.room_version += 1; return route.fulfill({ json: metadata() }); }
        return route.fulfill({ json: metadata() });
    };
    return state;
}

async function openMockRoom(page) {
    await page.locator('#room-open-list').click();
    await page.locator('#room-list .room-card button').first().click();
    await visibleText(page, '#room-detail-title', '자동화 검증용 모집방');
    await page.locator('#room-member-content').waitFor({ state: 'visible' });
}

test('transit and taxi partial failures remain independent; building payload and null fare stay truthful', async browser => {
    const app = await openApp(browser, {
        routeHandler: (route, payload, count) => count === 1
            ? route.fulfill({ status: 503, json: { error: '버스 검증용 연결 실패' } })
            : route.fulfill({ json: journeyFixture(payload) }),
        taxiHandler: (route, payload, count) => count === 2
            ? route.fulfill({ status: 503, json: { code: 'PROVIDER', error: '택시 검증용 연결 실패' } })
            : route.fulfill({ json: taxiFixture(payload, { amount: count === 3 ? null : 10001 }) }),
    });
    try {
        await search(app.page);
        await resultContains(app.page, '버스 검증용 연결 실패');
        await visibleText(app.page, '#taxi-content', '예상요금 약 10,001원');
        assert.equal(app.taxiRequests[0].location, app.requests[0].location);
        assert.equal(app.taxiRequests[0].campus, app.requests[0].campus);
        assert.equal(app.taxiRequests[0].lat, undefined, 'Campus building coordinates are resolved by the server');
        await app.page.locator('#route-submit').click();
        await resultContains(app.page, '총 소요 추정');
        await visibleText(app.page, '#taxi-content', '택시 검증용 연결 실패');
        await app.page.locator('#route-submit').click();
        await visibleText(app.page, '#taxi-content', '요금 조회 불가');
        assert.match(await app.page.locator('#taxi-content').innerText(), /택시 이동 약 10분/);
        assert.doesNotMatch(await app.page.locator('#taxi-content').innerText(), /0원|NaN|undefined/);
        await noOverflow(app.page);
    } finally { await app.close(); }
});

for (const changed of ['destination', 'campus', 'building']) test(`uncancellable old taxi response is discarded after ${changed} changes`, async browser => {
    let release, began;
    const gate = new Promise(resolve => { release = resolve; });
    const started = new Promise(resolve => { began = resolve; });
    const app = await openApp(browser, { taxiHandler: async (route, payload, count) => {
        if (count === 1) { began(); await gate; }
        return route.fulfill({ json: taxiFixture(payload, { amount: count === 1 ? 12345 : 20001 }) }).catch(() => {});
    } });
    try {
        await app.page.evaluate(() => {
            const original = window.fetch.bind(window);
            window.fetch = (input, init) => String(input).includes('/api/taxi/estimate')
                ? original(input, { ...init, signal: undefined }) : original(input, init);
        });
        await search(app.page);
        await started;
        await resultContains(app.page, '총 소요 추정');
        if (changed === 'destination') { await clearDestination(app.page); await selectDestination(app.page); }
        else if (changed === 'campus') await chooseBuilding(app.page, 'knu');
        else {
            const second = await app.page.locator('#building-select option').evaluateAll(options => options.filter(option => option.value)[1]?.value);
            assert.ok(second);
            await app.page.locator('#building-select').selectOption(second);
        }
        await app.page.locator('#taxi-section').waitFor({ state: 'hidden' });
        const oldResponse = app.page.waitForResponse(response => new URL(response.url()).pathname === '/api/taxi/estimate');
        release(); await oldResponse;
        await app.page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        assert.equal(await app.page.locator('#taxi-section').isVisible(), false);
        await app.page.locator('#route-submit').click();
        await visibleText(app.page, '#taxi-content', '예상요금 약 20,001원');
        assert.doesNotMatch(await app.page.locator('#taxi-content').innerText(), /12,345/);
    } finally { release(); await app.close(); }
});

test('taxi estimate expiration clears values without automatic provider refresh', async browser => {
    const app = await openApp(browser, { taxiHandler: (route, payload) => route.fulfill({ json: taxiFixture(payload, { ttl: 1 }) }) });
    try {
        await search(app.page);
        await visibleText(app.page, '#taxi-content', '예상요금 약 10,001원');
        await visibleText(app.page, '#taxi-content', '견적이 만료됐어요');
        assert.equal(app.taxiRequests.length, 1);
        assert.doesNotMatch(await app.page.locator('#taxi-content').innerText(), /10,001원/);
    } finally { await app.close(); }
});

test('room configuration failure leaves transit and taxi usable', async browser => {
    const app = await openApp(browser);
    try {
        await app.page.locator('#room-open-list').click();
        await visibleText(app.page, '#room-status', '자동화 검증에서 채팅 기능을 사용하지 않아요');
        await app.page.locator('#room-hide').click();
        await search(app.page);
        await resultContains(app.page, '총 소요 추정');
        await visibleText(app.page, '#taxi-content', '택시 이동 약 10분');
    } finally { await app.close(); }
});

test('all-status and Seoul time filters are sent explicitly', async browser => {
    const mock = mockRoom();
    const app = await openApp(browser, { roomHandler: mock.handler, timezoneId: 'America/New_York' });
    try {
        await app.page.locator('#room-open-list').click();
        await app.page.locator('#room-list .room-card').waitFor({ state: 'visible' });
        assert.equal(new URLSearchParams(app.roomRequests.find(request => request.pathname === '/api/ride-rooms').query).get('status'), 'all');
        await app.page.locator('#room-filter-from').fill('2030-01-01T10:00');
        await app.page.locator('#room-filter-to').fill('2030-01-01T11:00');
        await app.page.locator('#room-filter-form button[type="submit"]').click();
        await app.page.locator('#room-list .room-card').waitFor({ state: 'visible' });
        const query = new URLSearchParams(app.roomRequests.filter(request => request.pathname === '/api/ride-rooms').at(-1).query);
        assert.equal(query.get('from'), '2030-01-01T10:00:00+09:00');
        assert.equal(query.get('to'), '2030-01-01T11:00:00+09:00');
    } finally { await app.close(); }
});

test('room creation retries retain their ID and a changed search invalidates the confirmed form', async browser => {
    const mock = mockRoom();
    let creates = 0;
    const app = await openApp(browser, { roomHandler: (route, url, request) => {
        if (url.pathname === '/api/ride-rooms' && request.method === 'POST') {
            creates += 1;
            if (creates === 1) return route.fulfill({ status: 503, json: { code: 'TEMPORARY', error: '검증용 생성 연결 실패' } });
            return route.fulfill({ status: 201, json: { room: mock.room, self: mock.self, participants: mock.participants } });
        }
        return mock.handler(route, url, request);
    } });
    try {
        await search(app.page);
        await app.page.locator('#taxi-create-room').click();
        await app.page.locator('#room-title').fill('검증용 생성');
        await app.page.locator('#room-create-nickname').fill('검증용 방장');
        await app.page.locator('#room-meeting-query').fill('공개 장소');
        await app.page.locator('#room-meeting-options button').first().click();
        await app.page.locator('#room-meeting-confirm').check();
        await app.page.locator('#room-create-submit').click();
        await visibleText(app.page, '#room-status', '검증용 생성 연결 실패');
        await app.page.locator('#room-create-submit').click();
        await visibleText(app.page, '#room-detail-title', mock.room.title);
        const attempts = app.roomRequests.filter(request => request.pathname === '/api/ride-rooms' && request.method === 'POST');
        assert.equal(attempts.length, 2);
        assert.equal(attempts[0].body.client_room_id, attempts[1].body.client_room_id);
        await app.page.locator('#room-create-current').click();
        await app.page.locator('#room-meeting-query').fill('공개 장소');
        await app.page.locator('#room-meeting-options button').first().click();
        await app.page.locator('#room-meeting-confirm').check();
        assert.equal(await app.page.locator('#room-create-submit').isEnabled(), true);
        await app.page.locator('#tab-knu').click();
        assert.equal(await app.page.locator('#room-create-submit').isDisabled(), true);
        await visibleText(app.page, '#room-create-context', '경로가 바뀌었어요');
        assert.equal(creates, 2);
    } finally { await app.close(); }
});

test('room polling drains pages, keeps draft and scroll, and renders user messages as text', async browser => {
    const messages = Array.from({ length: 110 }, (_, index) => ({ id: index + 1, member_id: 'member-guest', nickname: INJECTION,
        body: index === 0 ? INJECTION : '검증용 메시지 ' + (index + 1), created_at: new Date().toISOString() }));
    const mock = mockRoom({ messageHandler: (route, url, state, metadata) => {
        const after = Number(url.searchParams.get('after_id') || 0);
        const page = messages.filter(message => message.id > after).slice(0, after ? 100 : 50);
        return route.fulfill({ json: { ...metadata(), messages: page, next_after_id: page.at(-1)?.id || after,
            has_more: (page.at(-1)?.id || after) < 110, cached_estimate: null } });
    } });
    const app = await openApp(browser, { roomHandler: mock.handler });
    try {
        await openMockRoom(app.page);
        await app.page.waitForFunction(() => document.querySelectorAll('#room-messages .room-message').length === 110);
        assert.equal(polls(app).length, 2);
        assert.equal(await app.page.locator('#room-messages img').count(), 0);
        assert.equal(await app.page.evaluate(() => window.__injected), false);
        await app.page.locator('#room-message-body').fill('입력 중인 내용 보존');
        await app.page.locator('#room-messages').evaluate(element => { element.scrollTop = 0; });
        messages.push({ id: 111, member_id: 'member-guest', nickname: '검증', body: '새 메시지', created_at: new Date().toISOString() });
        await app.page.waitForFunction(() => document.querySelectorAll('#room-messages .room-message').length === 111);
        assert.equal(await app.page.locator('#room-message-body').inputValue(), '입력 중인 내용 보존');
        assert.equal(await app.page.locator('#room-messages').evaluate(element => element.scrollTop), 0);
        assert.equal(quotes(app).length, 1, 'Message polling does not refresh estimates');
        await noOverflow(app.page);
    } finally { await app.close(); }
});

test('desktop room metadata renders as text and controls fit the planner', async browser => {
    const mock = mockRoom();
    mock.room.title = INJECTION;
    mock.room.meeting = { ...PUBLIC_MEETING, name: INJECTION };
    mock.participants[0].nickname = INJECTION;
    const app = await openApp(browser, { roomHandler: mock.handler, viewport: { width: 1440, height: 1000 } });
    try {
        await app.page.locator('#room-open-list').click();
        await app.page.locator('#room-list .room-card button').first().click();
        await visibleText(app.page, '#room-detail-title', INJECTION);
        assert.equal(await app.page.locator('#ride-room-section img').count(), 0);
        assert.equal(await app.page.evaluate(() => window.__injected), false);
        await noOverflow(app.page);
        await preview(app.page, 'desktop-room-test-preview.png');
    } finally { await app.close(); }
});

test('send acknowledgements do not reorder or skip an earlier unpolled message', async browser => {
    const at = new Date().toISOString();
    const mock = mockRoom({ messages: [{ id: 1, member_id: 'member-guest', nickname: '검증', body: '첫 대화', created_at: at }],
        sendHandler: (route, request, state) => {
            const message = { id: 3, member_id: 'member-host', nickname: '검증 방장', body: request.body.body, created_at: at };
            state.messages.push({ id: 2, member_id: 'member-guest', nickname: '검증 참가자', body: '아직 확인하지 않은 이전 대화', created_at: at }, message);
            return route.fulfill({ status: 201, json: { message, room_version: state.room.room_version } });
        } });
    const app = await openApp(browser, { roomHandler: mock.handler });
    try {
        await openMockRoom(app.page);
        await visibleText(app.page, '#room-messages', '첫 대화');
        await app.page.locator('#room-message-body').fill('나의 새 대화');
        await app.page.locator('#room-message-send').click();
        await visibleText(app.page, '#room-messages', '나의 새 대화');
        await visibleText(app.page, '#room-messages', '아직 확인하지 않은 이전 대화');
        assert.deepEqual(await app.page.locator('#room-messages .room-message').evaluateAll(elements => elements.map(element => Number(element.dataset.messageId))), [1, 2, 3]);
        assert.match(polls(app).at(-1).query, /after_id=1/);
    } finally { await app.close(); }
});

test('hidden room aborts polling, resumes membership and cursor without a quote, and closes read-only', async browser => {
    const mock = mockRoom({ ttl: 1, messages: [{ id: 1, member_id: 'member-guest', nickname: '검증', body: '첫 메시지', created_at: new Date().toISOString() }] });
    const app = await openApp(browser, { roomHandler: mock.handler, viewport: { width: 360, height: 800 } });
    try {
        await openMockRoom(app.page);
        await visibleText(app.page, '#room-estimate-content', '현재 2명 기준 예상 분담금');
        assert.match(await app.page.locator('#room-estimate-content').innerText(), /정원 4명/);
        await visibleText(app.page, '#room-estimate-content', '견적이 만료됐어요');
        await app.page.evaluate(() => {
            Object.defineProperty(document, 'hidden', { configurable: true, value: true });
            document.dispatchEvent(new Event('visibilitychange'));
        });
        const before = polls(app).length;
        await delay(3300);
        assert.equal(polls(app).length, before);
        const beforeQuotes = quotes(app).length;
        await app.page.evaluate(() => {
            Object.defineProperty(document, 'hidden', { configurable: true, value: false });
            document.dispatchEvent(new Event('visibilitychange'));
        });
        await app.page.waitForFunction(() => document.querySelectorAll('#room-messages .room-message').length === 1);
        await delay(150);
        assert.equal(quotes(app).length, beforeQuotes);
        assert.match(polls(app).at(-1).query, /after_id=1/);
        mock.room.status = 'closed'; mock.room.room_version += 1;
        await visibleText(app.page, '#room-detail-state', '방 종료');
        await app.page.locator('#room-message-form').waitFor({ state: 'hidden' });
        const ended = polls(app).length;
        await delay(3300);
        assert.equal(polls(app).length, ended);
        assert.equal(await app.page.locator('#room-messages .room-message').count(), 1);
        await noOverflow(app.page);
        await preview(app.page, 'mobile-room-readonly-test-preview.png');
    } finally { await app.close(); }
});

test('switching rooms discards an old poll even when cancellation is ignored; failures back off', async browser => {
    let release, began;
    const gate = new Promise(resolve => { release = resolve; });
    const started = new Promise(resolve => { began = resolve; });
    const mock = mockRoom({ messageHandler: async (route, url, state, metadata) => {
        if (state.messageCalls === 1) { began(); await gate; return route.fulfill({ json: { ...metadata(),
            messages: [{ id: 1, member_id: 'member-guest', nickname: '검증', body: '닫힌 화면의 오래된 응답', created_at: new Date().toISOString() }],
            next_after_id: 1, has_more: false } }).catch(() => {}); }
        return route.fulfill({ status: 503, json: { code: 'TEMPORARY', error: '검증용 실패' } });
    } });
    const app = await openApp(browser, { roomHandler: mock.handler });
    try {
        await app.page.evaluate(() => {
            const original = window.fetch.bind(window);
            window.fetch = (input, init) => String(input).includes('/messages')
                ? original(input, { ...init, signal: undefined }) : original(input, init);
        });
        await openMockRoom(app.page); await started;
        await app.page.locator('#room-detail-back').click();
        const response = app.page.waitForResponse(reply => new URL(reply.url()).pathname.endsWith('/messages'));
        release(); await response;
        await app.page.locator('#room-list .room-card button').first().click();
        await visibleText(app.page, '#room-chat-status', '검증용 실패');
        assert.doesNotMatch(await app.page.locator('#room-messages').innerText(), /오래된 응답/);
        const count = polls(app).length;
        await delay(3300);
        assert.equal(polls(app).length, count, 'First failed poll waits six seconds before retry');
        await app.page.locator('#room-hide').click();
    } finally { release(); await app.close(); }
});

if (process.env.CAPSTONE_LIVE_ROOM_TESTS === '1') test('two isolated browsers use real anonymous rooms, retries, full chat, same-browser tabs, leave and close', async browser => {
    let loseFirstMessage = true;
    const first = await openApp(browser, { liveRooms: true, timezoneId: 'America/New_York',
        searchHandler: (route, query) => route.fulfill({ json: [query.includes('공개') ? PUBLIC_MEETING : DESTINATION] }) });
    const second = await openApp(browser, { liveRooms: true, roomHandler: async (route, url, request) => {
        if (request.method === 'POST' && url.pathname.endsWith('/messages') && loseFirstMessage) {
            loseFirstMessage = false;
            const response = await route.fetch(); assert.ok(response.ok());
            return route.abort('failed'); // Server committed, client lost the reply: retry must keep its UUID.
        }
        return route.continue();
    } });
    const extraContexts = [];
    let hostTab;
    try {
        const testServer = await first.context.request.get(BASE_URL + '/health');
        assert.equal(testServer.headers()['x-capstone-test-server'], 'synthetic-data',
            'Real room checks require scripts/run_mobility_test_server.py with its disposable database and blocked providers');
        const page = first.page;
        await selectDestination(page);
        await page.locator('#route-submit').click();
        await visibleText(page, '#taxi-content', '택시 이동 약 10분');
        await page.locator('#taxi-create-room').click();
        assert.equal(await page.locator('#room-meeting-confirm').isDisabled(), true);
        assert.equal(await page.locator('#room-create-submit').isDisabled(), true);
        assert.equal(await page.locator('#room-meeting-selected').isVisible(), false);
        assert.equal(first.roomRequests.filter(request => request.method === 'POST').length, 0);
        const title = '검증 모집 ' + Date.now();
        await page.locator('#room-title').fill(title);
        await page.locator('#room-create-nickname').fill('검증 방장');
        await page.locator('#room-capacity').selectOption('4');
        await page.locator('#room-meeting-query').fill('공개 정문');
        await page.locator('#room-meeting-options button').first().click();
        assert.equal(await page.locator('#room-create-submit').isDisabled(), true);
        await page.locator('#room-meeting-confirm').check();
        const createdReply = page.waitForResponse(response => new URL(response.url()).pathname === '/api/ride-rooms' && response.request().method() === 'POST');
        await page.locator('#room-create-submit').click();
        const created = await (await createdReply).json();
        const roomId = created.room.id;
        await visibleText(page, '#room-detail-title', title);
        await visibleText(page, '#room-detail-count', '현재 1명');
        const payload = first.roomRequests.find(request => request.method === 'POST' && request.pathname === '/api/ride-rooms');
        assert.deepEqual(payload.body.meeting, { name: PUBLIC_MEETING.name, lat: PUBLIC_MEETING.lat, lon: PUBLIC_MEETING.lon });
        assert.notEqual(payload.body.meeting.lat, 37.88695, 'GPS is not made public as the meeting place');
        assert.match(payload.body.departure_at, /\+09:00$/);
        assert.ok(new Date(payload.body.departure_at).getTime() > Date.now());
        assert.ok(payload.headers['x-csrf-token']);
        assert.equal(payload.body.actor_id, undefined);
        assert.equal(payload.body.user_id, undefined);
        await second.page.locator('#room-open-list').click();
        await second.page.locator('#room-filter-q').fill(DESTINATION.name);
        await second.page.locator('#room-filter-form button[type="submit"]').click();
        await second.page.locator('#room-list .room-card').filter({ hasText: title }).locator('button').click();
        await second.page.locator('#room-join-form').waitFor({ state: 'visible' });
        assert.equal(await second.page.locator('#room-member-content').isVisible(), false);
        assert.equal(second.roomRequests.filter(request => request.method === 'POST').length, 0, 'Viewing a room is not joining it');
        await second.page.locator('#room-join-nickname').fill('검증 동승자');
        await second.page.locator('#room-join-submit').click();
        await visibleText(second.page, '#room-detail-count', '현재 2명');
        await visibleText(page, '#room-detail-count', '현재 2명');
        await visibleText(page, '#room-estimate-content', '현재 2명 기준 예상 분담금');
        assert.match(await page.locator('#room-estimate-content').innerText(), /5,001원/);
        assert.match(await page.locator('#room-estimate-content').innerText(), /5,000원/);
        assert.match(await page.locator('#room-estimate-content').innerText(), /정원 4명/);
        const message = INJECTION + '\n네트워크 재전송 검증';
        await second.page.locator('#room-message-body').fill(message);
        await second.page.locator('#room-message-send').click();
        await visibleText(second.page, '#room-status', '다시 보내도 같은 메시지는 한 번만 저장');
        await second.page.locator('#room-message-send').click();
        await visibleText(second.page, '#room-status', '메시지를 보냈어요');
        await visibleText(page, '#room-messages', '네트워크 재전송 검증');
        const attempts = second.roomRequests.filter(request => request.method === 'POST' && request.pathname.endsWith('/messages'));
        assert.equal(attempts.length, 2);
        assert.equal(attempts[0].body.client_message_id, attempts[1].body.client_message_id);
        assert.equal(await page.locator('#room-messages .room-message').filter({ hasText: message }).count(), 1);
        assert.equal(await page.locator('#room-messages img').count(), 0);
        assert.equal(await page.evaluate(() => window.__injected), false);
        const beforeQuote = quotes(first).length;
        await delay(3300);
        assert.equal(quotes(first).length, beforeQuote);
        for (const nickname of ['검증 추가 1', '검증 추가 2']) {
            const context = await browser.newContext(); extraContexts.push(context);
            const session = await (await context.request.get(BASE_URL + '/api/ride-rooms/session')).json();
            const joined = await context.request.post(BASE_URL + '/api/ride-rooms/' + roomId + '/join', {
                headers: { 'X-CSRF-Token': session.csrf_token, Origin: new URL(BASE_URL).origin }, data: { nickname } });
            assert.equal(joined.status(), 200);
        }
        await visibleText(page, '#room-detail-state', '정원 마감');
        await page.locator('#room-message-body').fill('정원 마감 후에도 대화해요');
        await page.locator('#room-message-send').click();
        await visibleText(second.page, '#room-messages', '정원 마감 후에도 대화해요');
        await preview(page, 'mobile-shared-ride-test-preview.png');
        hostTab = await first.context.newPage();
        await hostTab.goto(BASE_URL);
        await activateRealMobility(hostTab);
        await hostTab.locator('#room-open-list').click();
        await hostTab.locator('#room-list .room-card').filter({ hasText: title }).locator('button').click();
        await visibleText(hostTab, '#room-detail-count', '현재 4명');
        assert.equal(await hostTab.locator('#room-join-form').isVisible(), false);
        const tabJoin = await hostTab.evaluate(async id => {
            const session = await (await fetch('/api/ride-rooms/session', { credentials: 'same-origin' })).json();
            const response = await fetch('/api/ride-rooms/' + id + '/join', { method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf_token }, body: JSON.stringify({ nickname: '같은 방장 탭' }) });
            return { status: response.status, body: await response.json() };
        }, roomId);
        assert.equal(tabJoin.status, 200);
        assert.equal(tabJoin.body.room.participant_count, 4);
        await hostTab.close(); hostTab = null;
        for (const context of extraContexts) {
            const session = await (await context.request.get(BASE_URL + '/api/ride-rooms/session')).json();
            const left = await context.request.post(BASE_URL + '/api/ride-rooms/' + roomId + '/leave', {
                headers: { 'X-CSRF-Token': session.csrf_token, Origin: new URL(BASE_URL).origin }, data: {} });
            assert.equal(left.status(), 200);
        }
        await visibleText(page, '#room-detail-state', '모집 중');
        await second.page.locator('#room-leave').click();
        await second.page.locator('#room-join-form').waitFor({ state: 'visible' });
        assert.equal(await second.page.locator('#room-member-content').isVisible(), false);
        await visibleText(page, '#room-detail-count', '현재 1명');
        await page.locator('#room-close').click();
        await visibleText(page, '#room-close-confirm', '새 참가와 메시지 전송이 중단돼요');
        await page.locator('#room-close-yes').click();
        await visibleText(page, '#room-detail-state', '방 종료');
        await page.locator('#room-message-form').waitFor({ state: 'hidden' });
        assert.ok(await page.locator('#room-messages .room-message').count() >= 2);
        await noOverflow(page); await noOverflow(second.page);
    } finally {
        if (hostTab) await hostTab.close();
        for (const context of extraContexts) await context.close();
        await first.close(); await second.close();
    }
});

(async () => {
    const browser = await chromium.launch({ headless: true,
        ...(process.env.CAPSTONE_BROWSER_EXECUTABLE ? { executablePath: process.env.CAPSTONE_BROWSER_EXECUTABLE } : {}) });
    let failed = 0;
    try {
        for (const item of tests) {
            try { await item.run(browser); console.log('PASS ' + item.name); }
            catch (error) { failed += 1; console.error('FAIL ' + item.name + '\n' + (error.stack || error)); }
        }
    } finally { await browser.close(); }
    console.log(`${tests.length - failed}/${tests.length} mobility browser checks passed. Provider values are test fixtures, not live fares.`);
    if (process.env.CAPSTONE_LIVE_ROOM_TESTS !== '1') console.log('Real room HTTP checks skipped; enable CAPSTONE_LIVE_ROOM_TESTS=1 only with a disposable local room database.');
    if (failed) process.exitCode = 1;
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
