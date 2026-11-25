/** Two independent browsers against a real Flask server and a disposable SQLite file. */
'use strict';
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { randomUUID } = require('node:crypto');
const { once } = require('node:events');
const fs = require('node:fs/promises');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { setTimeout: delay } = require('node:timers/promises');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const python = process.env.CAPSTONE_PYTHON || path.join(root, '.venv',
    ...(process.platform === 'win32' ? ['Scripts', 'python.exe'] : ['bin', 'python']));
const meeting = { name: '검증용 공개 정문', lat: 37.8848, lon: 127.7381 };
const campusMeeting = { name: '강원대학교 공과대학6호관', lat: 37.86865, lon: 127.7411 };
const title = '자동 검증용 모집';
const hostMessage = '방장에서 보낸 자동 검증 메시지';
const guestMessage = '참가자에서 보낸 자동 검증 메시지';
const sessionCookie = 'capstone_ride_session';
const visible = (page, selector, text) => page.locator(selector).filter({ hasText: text })
    .waitFor({ state: 'visible', timeout: 15000 });

async function freePort() {
    const probe = net.createServer().listen(0, '127.0.0.1');
    await once(probe, 'listening');
    const port = probe.address().port;
    await new Promise((resolve, reject) => probe.close(error => error ? reject(error) : resolve()));
    return port;
}

async function stopFlask(server) {
    if (!server || server.exitCode !== null || server.signalCode !== null) return;
    const stopped = once(server, 'exit');
    server.kill();
    await stopped;
}

async function startFlask(port, directory, origin) {
    const server = spawn(python, ['-Bu', 'scripts/run_mobility_test_server.py',
        '--port', String(port), '--reuse-temp-dir', directory], { cwd: root });
    let logs = '';
    let spawnError;
    server.on('error', error => { spawnError = error; });
    for (const stream of [server.stdout, server.stderr]) stream.on('data', chunk => { logs = (logs + chunk).slice(-4000); });
    try {
        const deadline = Date.now() + 45000;
        while (Date.now() < deadline) {
            if (spawnError) throw spawnError;
            if (server.exitCode !== null) throw new Error('검증 서버 종료 ' + server.exitCode);
            let response;
            try { response = await fetch(origin + '/health', { signal: AbortSignal.timeout(500) }); }
            catch { await delay(150); continue; }
            assert.equal(response.headers.get('x-capstone-test-server'), 'synthetic-data');
            return server;
        }
        throw new Error('검증 서버가 준비되지 않았습니다.');
    } catch (error) {
        if (!spawnError) await stopFlask(server);
        throw new Error(error.message + '\n' + logs, { cause: error });
    }
}

async function roomHeaders(context, origin) {
    const response = await context.request.get(origin + '/api/ride-rooms/session');
    assert.ok(response.ok());
    const session = await response.json();
    return { Origin: origin, 'X-CSRF-Token': session.csrf_token };
}

async function roomAction(context, origin, roomId, action, data = {}) {
    return context.request.post(origin + '/api/ride-rooms/' + roomId + '/' + action, {
        headers: await roomHeaders(context, origin), data,
    });
}

async function assertConversation(host, guest) {
    for (const page of [host.page, guest.page]) {
        await visible(page, '#room-detail-title', title);
        await visible(page, '#room-detail-count', '현재 2명');
        await visible(page, '#room-messages', hostMessage);
        await visible(page, '#room-messages', guestMessage);
        assert.equal(await page.locator('#room-messages .room-message').count(), 2);
    }
}

async function main() {
    const port = await freePort();
    const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'capstone-browser-'));
    const origin = 'http://127.0.0.1:' + port;
    process.env.CAPSTONE_BASE_URL = origin;
    const { openApp, selectTool, DESTINATION } = require('./frontend_smoke.cjs');
    const launchOptions = { headless: true, executablePath: process.env.CAPSTONE_BROWSER_EXECUTABLE };
    let server, browserA, browserB, host, guest;
    try {
        server = await startFlask(port, directory, origin);
        browserA = await chromium.launch(launchOptions);
        browserB = await chromium.launch(launchOptions);
        guest = await openApp(browserB, { liveRooms: true, mapSDKGate: Promise.resolve(), hasTouch: true });
        await selectTool(guest.page, 'rooms');
        await visible(guest.page, '#room-status', '모집방이 없어요');
        assert.equal(await guest.page.locator('#room-list-view :is(input, select, form)').count(), 0);
        assert.equal(await guest.page.evaluate(() => document.hidden), false);
        const guestCookie = (await guest.context.cookies(origin)).find(cookie => cookie.name === sessionCookie);
        assert.ok(guestCookie);
        let failFirstCreate = true;
        host = await openApp(browserA, {
            liveRooms: true, mapSDKGate: Promise.resolve(), hasTouch: true,
            searchHandler: (route, query) => route.fulfill({ json: [query.includes('정문') ? meeting : DESTINATION] }),
            roomHandler: (route, url, request) => {
                if (failFirstCreate && url.pathname === '/api/ride-rooms' && request.method === 'POST') {
                    failFirstCreate = false;
                    return route.fulfill({ status: 503, json: { error: '검증용 서버 저장 실패' } });
                }
                return route.continue();
            },
        });
        await selectTool(host.page, 'taxi');
        await host.page.locator('#taxi-new-room').click();
        const name = host.page.locator('#room-title');
        const nickname = host.page.locator('#room-create-nickname');
        const departure = host.page.locator('#room-departure');
        const departureInput = new Date(Date.now() + 39 * 60000 + 9 * 3600000).toISOString().slice(0, 16);
        await name.fill(title);
        await nickname.fill('검증 방장');
        await departure.fill(departureInput);
        await host.page.locator('#room-capacity').selectOption('3');
        await host.page.locator('#room-destination-query').fill(DESTINATION.name);
        await host.page.locator('#room-destination-options button').first().click();
        await visible(host.page, 'fieldset#room-meeting-picker', '모이는 곳');
        const picker = host.page.locator('#room-meeting-picker');
        const hallym = picker.locator('#room-meeting-hallym');
        const knu = picker.locator('#room-meeting-knu');
        const meetingInput = picker.locator('#room-meeting-query');
        const meetingOptions = picker.locator('#room-meeting-options button');
        const meetingConsent = host.page.locator('#room-meeting-confirm');
        for (const select of [hallym, knu]) {
            assert.equal(await select.inputValue(), '');
            assert.ok(await select.evaluate(element => !element.multiple && element.size <= 1));
        }
        assert.equal(await meetingInput.inputValue(), '');
        assert.equal(await meetingOptions.count(), 0);
        assert.equal(await picker.locator('input[type="search"], select').count(), 3);
        await meetingInput.fill('공개 정문');
        await meetingOptions.filter({ hasText: meeting.name }).click();
        assert.equal(await meetingInput.inputValue(), meeting.name);
        await meetingConsent.check();
        const searchesBeforeCampus = host.searches.length;
        await hallym.selectOption({ label: '공학관' });
        assert.equal(await meetingInput.inputValue(), '');
        assert.equal(await meetingConsent.isChecked(), false);
        await meetingConsent.check();
        const [knuChoice] = await knu.selectOption({ label: '공과대학6호관' });
        assert.equal(await hallym.inputValue(), '');
        assert.equal(await meetingInput.inputValue(), '');
        assert.equal(await meetingOptions.count(), 0);
        assert.equal(host.searches.length, searchesBeforeCampus);
        assert.equal(await meetingConsent.isChecked(), false);
        assert.equal(await host.page.locator('#room-create-submit').isDisabled(), true);
        await meetingInput.fill('공개 정문');
        assert.equal(await knu.inputValue(), '');
        await meetingOptions.filter({ hasText: meeting.name }).click();
        await knu.selectOption(knuChoice);
        await meetingConsent.check();
        await host.page.locator('#room-create-submit').click();
        await visible(host.page, '#room-status', '검증용 서버 저장 실패');
        assert.equal(await guest.page.locator('#room-list .room-card').count(), 0);
        assert.equal(await name.inputValue(), title);
        assert.equal(await nickname.inputValue(), '검증 방장');
        assert.equal(await departure.inputValue(), departureInput);
        assert.equal(await knu.inputValue(), knuChoice);
        const hostCookie = (await host.context.cookies(origin)).find(cookie => cookie.name === sessionCookie);
        assert.ok(hostCookie && hostCookie.value !== guestCookie.value, 'The two browsers have different sessions');
        const created = host.page.waitForResponse(response => new URL(response.url()).pathname === '/api/ride-rooms'
            && response.request().method() === 'POST');
        await host.page.locator('#room-create-submit').click();
        const reply = await created;
        assert.equal(reply.status(), 201);
        assert.deepEqual(reply.request().postDataJSON().meeting, campusMeeting);
        const { id: roomId, meeting: savedMeeting } = (await reply.json()).room;
        assert.deepEqual(savedMeeting, campusMeeting);
        await visible(host.page, '#room-status', '방을 만들었어요');
        await visible(host.page, '#room-detail-count', '현재 1명');
        await visible(guest.page, '#room-list .room-card', title);
        console.log('PASS one meeting section supports both campuses and other places; the guest list updates without interaction');

        // Observe the clipboard call without overwriting the desktop clipboard.
        await host.page.evaluate(() => {
            Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
                async writeText(value) { window.__capstoneCopiedRoom = value; },
            } });
        });
        await host.page.locator('#room-share-link').click();
        const share = new URL(await host.page.evaluate(() => window.__capstoneCopiedRoom));
        assert.equal(share.origin, origin);
        assert.equal(share.searchParams.get('room'), roomId);
        await guest.page.goto(share.href, { waitUntil: 'domcontentloaded' });
        await visible(guest.page, '#room-detail-title', title);
        await visible(guest.page, '#room-detail-count', '현재 1명');
        await guest.page.locator('#room-join-form').waitFor();
        assert.equal(guest.roomRequests.filter(request => request.method === 'POST').length, 0);
        assert.equal(await guest.page.locator('#room-member-content').isVisible(), false);
        await guest.page.locator('#room-join-nickname').fill('검증 동승자');
        await guest.page.locator('#room-join-submit').click();
        await visible(guest.page, '#room-status', '참가했어요');
        await visible(guest.page, '#room-detail-count', '현재 2명');
        await visible(host.page, '#room-detail-count', '현재 2명');
        console.log('PASS copied link opens the same room without joining; both show two after join');

        await host.page.locator('#room-message-body').fill(hostMessage);
        await host.page.locator('#room-message-send').click();
        await visible(guest.page, '#room-messages', hostMessage);
        await guest.page.locator('#room-message-body').fill(guestMessage);
        await guest.page.locator('#room-message-send').click();
        await visible(host.page, '#room-messages', guestMessage);
        await Promise.all([host.page.reload(), guest.page.reload()]);
        await assertConversation(host, guest);
        await stopFlask(server);
        server = await startFlask(port, directory, origin);
        await Promise.all([host.page.reload(), guest.page.reload()]);
        await assertConversation(host, guest);
        console.log('PASS bidirectional chat and counts survive reload and Flask restart');

        const duplicate = await Promise.all([1, 2].map(() =>
            roomAction(guest.context, origin, roomId, 'join', { nickname: '검증 동승자' })));
        for (const response of duplicate) {
            assert.equal(response.status(), 200);
            assert.equal((await response.json()).room.participant_count, 2);
        }
        const contenders = await Promise.all([browserA.newContext(), browserB.newContext()]);
        try {
            const race = await Promise.all(contenders.map((context, index) =>
                roomAction(context, origin, roomId, 'join', { nickname: '검증 추가 ' + index })));
            assert.deepEqual(race.map(response => response.status()).sort(), [200, 409]);
            const winner = race.findIndex(response => response.ok());
            assert.equal((await race[winner].json()).room.participant_count, 3);
            assert.equal((await race[1 - winner].json()).code, 'ROOM_FULL');
            await visible(host.page, '#room-detail-state', '정원 마감');
            await visible(guest.page, '#room-detail-count', '현재 3명');
            assert.ok((await roomAction(contenders[winner], origin, roomId, 'leave')).ok());
        } finally { await Promise.all(contenders.map(context => context.close())); }
        await guest.page.locator('#room-leave').click();
        await visible(host.page, '#room-detail-count', '현재 1명');
        await visible(guest.page, '#room-detail-count', '현재 1명');
        await host.page.locator('#room-close').click();
        await host.page.locator('#room-close-yes').click();
        await visible(guest.page, '#room-detail-state', '방 종료');
        assert.equal(await guest.page.locator('#room-join-form').isVisible(), false);
        console.log('PASS repeat join is idempotent; final-seat race, leave, and closure propagate');

        assert.equal(await guest.page.locator('#room-delete').isVisible(), false);
        const deniedDelete = await guest.context.request.delete(origin + '/api/ride-rooms/' + roomId, {
            headers: await roomHeaders(guest.context, origin), data: {},
        });
        assert.equal(deniedDelete.status(), 403);
        await host.page.locator('#room-delete').click();
        await visible(host.page, '#room-delete-confirm', '모집방과 대화를 삭제할까요');
        await host.page.locator('#room-delete-no').click();
        assert.equal(await host.page.locator('#room-delete-confirm').isVisible(), false);
        await visible(host.page, '#room-detail-title', title);
        await host.page.locator('#room-delete').click();
        await host.page.locator('#room-delete-yes').click();
        await visible(host.page, '#room-status', '모집방을 삭제했어요');
        await visible(guest.page, '#room-detail-title', '모집방을 찾을 수 없어요');
        assert.equal(await guest.page.locator('#room-share-link').isVisible(), false);
        assert.equal(await guest.page.locator('#room-member-content').isVisible(), false);
        console.log('PASS only the author can confirm deletion, and an open detail is cleared');

        const headers = await roomHeaders(host.context, origin);
        const seedRoom = async (name, minutes) => {
            const { name: destinationName, lat, lon } = DESTINATION;
            const response = await host.context.request.post(origin + '/api/ride-rooms', {
                headers, data: {
                    title: name, client_room_id: randomUUID(), nickname: '목록 검증 방장', meeting,
                    destination: { name: destinationName, lat, lon }, capacity: 2,
                    departure_at: new Date(Date.now() + minutes * 60000).toISOString(),
                },
            });
            assert.equal(response.status(), 201);
        };
        for (let index = 1; index <= 21; index += 1) await seedRoom('기존 검증방 ' + index, 60);
        await guest.page.locator('#room-detail-back').click();
        await visible(guest.page, '#room-list .room-card', '기존 검증방 20');
        const cards = guest.page.locator('#room-list .room-card');
        assert.equal(await cards.count(), 20);
        await guest.page.locator('#room-load-more').click();
        await guest.page.getByRole('heading', { name: '기존 검증방 1', exact: true }).waitFor();
        assert.equal(await cards.count(), 21);
        await seedRoom('새로 등록한 검증방', 180);
        await visible(guest.page, '#room-list .room-card', '새로 등록한 검증방');
        assert.match(await cards.first().innerText(), /새로 등록한 검증방/);
        assert.equal(await cards.count(), 22);
        console.log('PASS a new room appears first while the guest has two pages loaded');
    } catch (error) {
        console.error(error.stack || error);
        process.exitCode = 1;
    } finally {
        const cleanup = await Promise.allSettled([
            (async () => { try { await host?.close(); } finally { await browserA?.close(); } })(),
            (async () => { try { await guest?.close(); } finally { await browserB?.close(); } })(),
            stopFlask(server),
        ]);
        await fs.rm(directory, { recursive: true, force: true });
        for (const result of cleanup) if (result.status === 'rejected') throw result.reason;
    }
}

main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
