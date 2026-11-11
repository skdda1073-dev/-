/** Run against an approved local UI server. All external and mobility requests are mocked. */
'use strict';
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { openApp, selectTool, noOverflow, INJECTION } = require('./frontend_smoke.cjs');
const tests = [];
const test = (name, run) => tests.push({ name, run });
const byId = (page, id) => page.locator('#' + id);

async function fill(page, title = '오늘 저녁 남춘천역 같이 가실 분!') {
    for (const [id, value] of Object.entries({ title, destination: '남춘천역', from: '2030-10-21T18:00' })) {
        await byId(page, 'demo-' + id).fill(value);
    }
}
async function submit(page) { await page.locator('#demo-party-form [type="submit"]').click(); }
function noDemoRequests(app) {
    assert.equal(app.requests.length, 0, 'Demo does not search transit');
    assert.equal(app.searches.length, 0, 'Demo does not search places');
    assert.equal(app.taxiRequests.length, 0, 'Demo does not request taxi estimates');
    assert.equal(app.roomRequests.length, 0, 'Demo does not access real rooms');
}

for (const width of [360, 390, 1024, 1440]) test(`${width}px menu, form, modal, participant and reopening`, async browser => {
    const app = await openApp(browser, { viewport: { width, height: 900 } });
    try {
        const page = app.page;
        assert.equal(await page.locator('.quick-actions button').count(), 0);
        const tabs = page.locator('.transport-tab');
        assert.deepEqual(await tabs.allTextContents(), ['길찾기', '학교 셔틀버스', '지하철·ITX 시간표', '시외버스 시간표', '택시', '방 찾기']);
        assert.equal(await tabs.first().getAttribute('aria-selected'), 'true');
        await page.locator('#sidebar-toggle').click();
        const bounds = await tabs.evaluateAll(items => items.map(item => {
            const r = item.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width };
        }));
        for (let i = 1; i < bounds.length; i++) {
            assert.ok(bounds[i].y > bounds[i - 1].y);
            assert.ok(Math.abs(bounds[i].x - bounds[0].x) < 1);
            assert.ok(Math.abs(bounds[i].width - bounds[0].width) < 1);
        }
        assert.equal(await tabs.evaluateAll(items => items.every(item => {
            const label = item.querySelector('span');
            return item.scrollWidth <= item.clientWidth && label.scrollWidth <= label.clientWidth;
        })), true, 'Menu labels fit without horizontal clipping');
        await noOverflow(page);
        assert.equal(await byId(page, 'transport-panel-journey').isVisible(), true);
        assert.equal(await tabs.first().getAttribute('tabindex'), '0');
        assert.deepEqual(await tabs.evaluateAll(items => items.map(item => item.tabIndex)), [0, -1, -1, -1, -1, -1]);
        assert.equal(await page.locator('#transport-tab-bus, #transport-tab-scooter, #transport-panel-bus, #transport-panel-scooter').count(), 0);
        assert.equal(await page.evaluate(() => {
            const ids = [...document.querySelectorAll('[id]')].map(item => item.id);
            return ids.length === new Set(ids).size;
        }), true, 'IDs remain unique');
        await selectTool(page, 'journey');
        const formOrder = await page.locator('.journey-form').evaluate(form => {
            const origin = form.querySelector('.origin-section'), destination = form.querySelector('.destination-field');
            const submit = form.querySelector('#route-submit');
            const before = (a, b) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
            const styles = [...form.querySelectorAll('.journey-field-title')].map(item => {
                const style = getComputedStyle(item);
                return [style.fontFamily, style.fontSize, style.fontWeight, style.lineHeight, style.color];
            });
            return { ordered: before(origin, destination) && before(destination, submit) && !origin.contains(submit),
                buttons: form.querySelectorAll('#route-submit').length, styles };
        });
        assert.equal(formOrder.ordered, true); assert.equal(formOrder.buttons, 1);
        assert.equal(formOrder.styles.length, 2); assert.deepEqual(formOrder.styles[0], formOrder.styles[1]);
        assert.equal(await page.locator('label[for="dest-input"]').count(), 1);
        await byId(page, 'origin-building').click();
        await byId(page, 'building-select').focus();
        await page.keyboard.press('Tab');
        assert.equal(await page.evaluate(() => document.activeElement.id), 'dest-input');
        await page.keyboard.press('Tab');
        assert.equal(await page.evaluate(() => document.activeElement.id), 'route-submit');
        assert.equal(app.itxRequests.filter(item => item.pathname === '/api/itx/stations').length, 0);
        await selectTool(page, 'subway');
        await page.waitForFunction(() => !document.getElementById('itx-load').disabled);
        assert.equal(app.itxRequests.filter(item => item.pathname === '/api/itx/stations').length, 1);
        assert.equal(app.itxRequests.filter(item => item.pathname === '/api/itx/route').length, 0);
        await byId(page, 'itx-load').click();
        await page.waitForFunction(() => !document.getElementById('itx-table-wrap').hidden);
        assert.equal(app.itxRequests.filter(item => item.pathname === '/api/itx/route').length, 1);
        assert.equal(await page.locator('#itx-table-body tr').count(), 1);
        assert.equal(new URLSearchParams(app.itxRequests.at(-1).query).get('origin'), '춘천');
        assert.equal(new URLSearchParams(app.itxRequests.at(-1).query).get('destination'), '마석');
        for (let i = 0; i < 6; i++) {
            await selectTool(page, (await tabs.nth(i).getAttribute('id')).replace('transport-tab-', ''));
            const panel = byId(page, await tabs.nth(i).getAttribute('aria-controls'));
            assert.equal(await panel.isVisible(), true);
            assert.equal(await tabs.nth(i).getAttribute('aria-selected'), 'true');
            assert.equal(await tabs.nth(i).getAttribute('tabindex'), '0');
            assert.equal(await page.locator('#planner-body .transport-panel:not([hidden])').count(), 1);
            if (i === 3) assert.match(await panel.innerText(), /오늘 출발 시간표/);
            if (i === 4) {
                assert.equal(await panel.locator('#taxi-refresh').isVisible(), true);
                assert.equal(await panel.locator('#taxi-room-actions').isVisible(), false);
            }
        }
        await selectTool(page, 'subway');
        assert.equal(app.itxRequests.filter(item => item.pathname === '/api/itx/stations').length, 1);
        assert.equal(app.itxRequests.filter(item => item.pathname === '/api/itx/route').length, 1);
        await selectTool(page, 'rooms');
        assert.match(await byId(page, 'demo-room-empty').innerText(), /아직 모집 중인 택시팟이 없어요/);
        assert.equal(await page.locator('.demo-room-post').count(), 0);
        await byId(page, 'demo-go-create').click();
        const title = '긴 한글 모집 제목 '.repeat(20) + INJECTION;
        await fill(page, title);
        await byId(page, 'demo-destination').fill(INJECTION);
        await submit(page);
        assert.equal(await byId(page, 'demo-party-dialog').evaluate(dialog => dialog.open), false);
        assert.equal(await byId(page, 'demo-toast').innerText(), '생성완료!');
        await selectTool(page, 'rooms');
        const post = page.locator('.demo-room-post').first();
        assert.match(await post.innerText(), /내가 만든 방/);
        await post.click();
        assert.equal(await byId(page, 'demo-count').innerText(), '1/4명');
        assert.equal(await byId(page, 'demo-room-title').innerText(), title);
        assert.equal(await byId(page, 'demo-room-destination').innerText(), INJECTION);
        assert.equal(await byId(page, 'demo-room-time').innerText(), '2030-10-21 18:00');
        assert.equal(await page.locator('#demo-party-dialog img').count(), 0);
        assert.equal(await page.locator('#demo-members li').count(), 4);
        assert.deepEqual(await page.locator('#demo-members li').allTextContents(), ['방장(시연)', '빈자리', '빈자리', '빈자리']);
        assert.equal(await page.evaluate(() => document.activeElement.id), 'demo-close');
        await byId(page, 'demo-close').press('Shift+Tab');
        assert.equal(await page.evaluate(() => document.activeElement.id), 'demo-join');
        await byId(page, 'demo-join').press('Tab');
        assert.equal(await page.evaluate(() => document.activeElement.id), 'demo-close');
        await byId(page, 'demo-join').evaluate(button => { for (let i = 0; i < 10; i++) button.click(); });
        assert.equal(await byId(page, 'demo-count').innerText(), '2/4명');
        assert.equal(await byId(page, 'demo-join').innerText(), '참여 완료');
        assert.equal(await byId(page, 'demo-join').isDisabled(), true);
        assert.deepEqual(await page.locator('#demo-members li').allTextContents(), ['방장(시연)', '참가자(시연)', '빈자리', '빈자리']);
        const geometry = await byId(page, 'demo-party-dialog').evaluate(dialog => ({
            width: dialog.scrollWidth, client: dialog.clientWidth, height: dialog.clientHeight,
            scrollHeight: dialog.scrollHeight, maxHeight: innerHeight - 32, overflow: getComputedStyle(dialog).overflowY
        }));
        assert.ok(geometry.width <= geometry.client + 1); assert.ok(geometry.height <= geometry.maxHeight);
        assert.equal(geometry.overflow, 'auto');
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => !document.getElementById('demo-party-dialog').open);
        assert.equal(await post.evaluate(button => document.activeElement === button), true);
        assert.match(await post.innerText(), /2\/4명/);
        await post.click();
        assert.equal(await byId(page, 'demo-count').innerText(), '2/4명');
        await byId(page, 'demo-close').click();
        await page.waitForFunction(() => document.activeElement.classList.contains('demo-room-post'));
        await selectTool(page, 'taxi');
        await submit(page);
        assert.equal(await page.locator('.demo-room-post').count(), 1, 'Unchanged submission does not create another post');
        assert.equal(await byId(page, 'demo-party-dialog').evaluate(dialog => dialog.open), false);
        await noOverflow(page);
        assert.deepEqual(await page.evaluate(() => window.__testGPSCalls), []);
        noDemoRequests(app);
        await app.expectDialog('beforeunload', true, () => page.reload({ waitUntil: 'networkidle' }));
        await selectTool(page, 'rooms');
        assert.equal(await page.locator('.demo-room-post').count(), 0);
        assert.equal(await byId(page, 'demo-room-empty').isVisible(), true);
    } finally { await app.close(); }
});

test('validation retains values and keyboard tabs do not request data', async browser => {
    const app = await openApp(browser, { timezoneId: 'America/Los_Angeles' });
    try {
        const page = app.page;
        const pressMenuKey = async (from, key, expected) => {
            const tab = byId(page, 'transport-tab-' + from);
            if (!await tab.isVisible()) await byId(page, 'sidebar-toggle').click();
            await tab.focus(); await page.keyboard.press(key);
            assert.equal(await byId(page, 'transport-tab-' + expected).getAttribute('aria-selected'), 'true');
        };
        await pressMenuKey('journey', 'ArrowLeft', 'rooms');
        await pressMenuKey('rooms', 'Home', 'journey');
        await pressMenuKey('journey', 'ArrowDown', 'shuttle');
        await pressMenuKey('shuttle', 'ArrowUp', 'journey');
        await pressMenuKey('journey', 'ArrowRight', 'shuttle');
        await pressMenuKey('shuttle', 'End', 'rooms');
        await pressMenuKey('rooms', 'ArrowLeft', 'taxi');
        await submit(page);
        for (const name of ['title', 'destination', 'from']) assert.notEqual(await byId(page, 'demo-' + name + '-error').innerText(), '');
        assert.equal(await byId(page, 'demo-toast').isVisible(), false);
        await fill(page, '   ');
        await byId(page, 'demo-destination').fill('   ');
        await submit(page);
        assert.equal(await byId(page, 'demo-title').getAttribute('aria-invalid'), 'true');
        assert.equal(await byId(page, 'demo-destination').getAttribute('aria-invalid'), 'true');
        await fill(page);
        await byId(page, 'demo-from').fill('');
        await submit(page);
        assert.match(await byId(page, 'demo-from-error').innerText(), /날짜와 시간을 입력/);
        assert.equal(await byId(page, 'demo-title').inputValue(), '오늘 저녁 남춘천역 같이 가실 분!');
        assert.equal(await byId(page, 'demo-party-dialog').evaluate(dialog => dialog.open), false);
        await byId(page, 'demo-from').fill('2030-10-21T18:00');
        await submit(page);
        await selectTool(page, 'rooms');
        await page.locator('.demo-room-post').first().click();
        assert.equal(await byId(page, 'demo-room-time').innerText(), '2030-10-21 18:00');
        noDemoRequests(app);
    } finally { await app.close(); }
});

test('toast timing, duplicate submission and independent rooms with local chat', async browser => {
    const app = await openApp(browser);
    try {
        const page = app.page;
        await selectTool(page, 'taxi');
        await fill(page, '첫 번째 방');
        await page.evaluate(() => {
            const toast = document.getElementById('demo-toast');
            window.__toastEvents = [];
            new MutationObserver(() => window.__toastEvents.push({ at: performance.now(), hidden: toast.hidden, fading: toast.classList.contains('is-fading') }))
                .observe(toast, { attributes: true, attributeFilter: ['hidden', 'class'] });
            const form = document.getElementById('demo-party-form');
            for (let i = 0; i < 10; i++) form.requestSubmit();
        });
        assert.equal(await page.locator('.demo-room-post').count(), 1);
        assert.equal(await byId(page, 'demo-toast').innerText(), '생성완료!');
        assert.equal(await byId(page, 'demo-party-dialog').evaluate(dialog => dialog.open), false);
        await page.waitForFunction(() => document.getElementById('demo-toast').hidden);
        assert.equal(await byId(page, 'transport-tab-rooms').getAttribute('aria-selected'), 'true');
        assert.equal(await page.locator('.demo-room-post').first().evaluate(button => document.activeElement === button), true);
        assert.equal(await byId(page, 'demo-party-dialog').evaluate(dialog => dialog.open), false);
        const events = await page.evaluate(() => window.__toastEvents);
        const shown = events.find(event => !event.hidden && !event.fading);
        const fading = events.find(event => event.fading && !event.hidden);
        const hidden = events.find(event => event.hidden);
        assert.ok(fading.at - shown.at >= 950, 'Toast stays opaque for approximately one second');
        assert.ok(hidden.at - fading.at >= 350, 'Fade lasts approximately 0.4 seconds');
        await selectTool(page, 'taxi');
        await submit(page);
        assert.equal(await byId(page, 'demo-toast').isVisible(), false, 'Duplicate submission does not show success');
        await selectTool(page, 'rooms');
        const firstId = await page.locator('.demo-room-post').first().getAttribute('data-room-id');
        const first = page.locator(`[data-room-id="${firstId}"]`);
        await first.click();
        await byId(page, 'demo-join').click();
        const input = byId(page, 'demo-chat-input');
        assert.match(await byId(page, 'demo-chat-messages').innerText(), /첫 인사를 남겨보세요/);
        await input.fill('   ');
        await input.press('Enter');
        assert.equal(await page.locator('.demo-message').count(), 0);
        await input.fill(INJECTION);
        await input.dispatchEvent('keydown', { key: 'Enter', isComposing: true });
        assert.equal(await page.locator('.demo-message').count(), 0, 'IME confirmation does not submit');
        await input.press('Shift+Enter');
        assert.equal(await page.locator('.demo-message').count(), 0);
        await input.fill(INJECTION);
        await page.evaluate(() => {
            const form = document.getElementById('demo-chat-form');
            for (let i = 0; i < 10; i++) form.requestSubmit();
        });
        assert.equal(await page.locator('.demo-message').count(), 1);
        assert.equal(await page.locator('.demo-message p').innerText(), INJECTION);
        assert.equal(await page.locator('.demo-message img').count(), 0);
        assert.match(await page.locator('.demo-message strong').innerText(), /나\(시연\)/);
        assert.match(await page.locator('.demo-message small').innerText(), /\d/);
        // A failed local rendering attempt must preserve the draft for retry.
        await input.fill('표시 실패 후 재시도');
        await page.evaluate(() => {
            const list = document.getElementById('demo-chat-messages');
            const original = list.append;
            list.append = function() { list.append = original; throw new Error('Synthetic rendering failure'); };
        });
        await input.press('Enter');
        assert.equal(await input.inputValue(), '표시 실패 후 재시도');
        assert.match(await byId(page, 'demo-chat-status').innerText(), /다시 눌러/);
        await input.press('Enter');
        assert.equal(await page.locator('.demo-message').count(), 2);
        for (let i = 0; i < 12; i++) { await input.fill('이전 대화 확인용 메시지 ' + i); await input.press('Enter'); }
        await byId(page, 'demo-chat-messages').evaluate(list => { list.scrollTop = 0; });
        await input.fill('스크롤 유지 확인');
        await input.press('Enter');
        assert.equal(await byId(page, 'demo-chat-messages').evaluate(list => list.scrollTop), 0);
        await input.fill('첫 번째 방의 작성 중 초안');
        await byId(page, 'demo-close').click();
        await selectTool(page, 'taxi');
        await byId(page, 'demo-new-post').click();
        await fill(page, '두 번째 방');
        await byId(page, 'demo-destination').fill('춘천역');
        await byId(page, 'demo-from').fill('2030-10-22T10:00');
        await submit(page);
        await selectTool(page, 'rooms');
        const second = page.locator('.demo-room-post').first();
        assert.notEqual(await second.getAttribute('data-room-id'), firstId);
        assert.equal(await page.locator('.demo-room-post').count(), 2);
        assert.match(await second.innerText(), /두 번째 방/);
        await second.click();
        assert.equal(await byId(page, 'demo-count').innerText(), '1/4명');
        assert.equal(await byId(page, 'demo-room-destination').innerText(), '춘천역');
        assert.equal(await byId(page, 'demo-room-time').innerText(), '2030-10-22 10:00');
        assert.equal(await page.locator('.demo-message').count(), 0);
        assert.equal(await input.inputValue(), '');
        await input.fill('두 번째 방의 초안');
        await byId(page, 'demo-close').click();
        await first.click();
        assert.equal(await byId(page, 'demo-count').innerText(), '2/4명');
        assert.equal(await input.inputValue(), '첫 번째 방의 작성 중 초안');
        assert.equal(await page.locator('.demo-message').count(), 15);
        await byId(page, 'demo-close').click();
        await second.click();
        assert.equal(await input.inputValue(), '두 번째 방의 초안');
        assert.equal(await page.locator('.demo-message').count(), 0);
        assert.deepEqual(await page.evaluate(() => window.__testGPSCalls), []);
        noDemoRequests(app);
    } finally { await app.close(); }
});

(async () => {
    const browser = await chromium.launch({ headless: true,
        ...(process.env.CAPSTONE_BROWSER_EXECUTABLE ? { executablePath: process.env.CAPSTONE_BROWSER_EXECUTABLE } : {}) });
    let failures = 0;
    try {
        for (const item of tests) {
            try { await item.run(browser); console.log('PASS ' + item.name); }
            catch (error) { failures++; console.error('FAIL ' + item.name + '\n' + error.stack); }
        }
    } finally { await browser.close(); }
    if (failures) process.exitCode = 1;
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
