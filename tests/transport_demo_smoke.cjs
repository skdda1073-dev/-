/** Run against an approved local UI server. All external and mobility requests are mocked. */
'use strict';
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { openApp, noOverflow, INJECTION } = require('./frontend_smoke.cjs');
const tests = [];
const test = (name, run) => tests.push({ name, run });
const byId = (page, id) => page.locator('#' + id);

async function fill(page, title = '오늘 저녁 남춘천역 같이 가실 분!') {
    for (const [id, value] of Object.entries({ title, destination: '남춘천역', from: '2030-10-21T18:00', to: '2030-10-21T19:00' })) {
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

for (const width of [360, 390, 1440]) test(`${width}px menu, form, modal, participant and reopening`, async browser => {
    const app = await openApp(browser, { viewport: { width, height: 900 } });
    try {
        const page = app.page;
        assert.equal(await page.locator('.quick-actions button').count(), 0);
        const tabs = page.locator('.transport-tab');
        assert.deepEqual(await tabs.allTextContents(), ['버스 시간표', '주변 킥보드 찾기', '지하철 시간표', '시외버스 시간표', '택시', '방 찾기']);
        assert.equal(await tabs.first().getAttribute('aria-selected'), 'true');
        const bounds = await tabs.evaluateAll(items => items.map(item => {
            const r = item.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width };
        }));
        assert.equal(bounds[0].y, bounds[1].y); assert.equal(bounds[1].y, bounds[2].y);
        assert.equal(bounds[3].y, bounds[4].y); assert.equal(bounds[4].y, bounds[5].y); assert.ok(bounds[3].y > bounds[0].y);
        assert.ok(Math.abs(bounds[0].width - bounds[2].width) < 1);
        assert.ok(Math.abs(bounds[3].width - bounds[4].width) < 1);
        assert.ok(Math.abs(bounds[3].x - bounds[0].x) < 1);
        assert.ok(Math.abs(bounds[5].width - bounds[0].width) < 1);
        assert.ok(Math.abs(bounds[5].x + bounds[5].width - bounds[2].x - bounds[2].width) < 1);
        for (let i = 0; i < 5; i++) {
            await tabs.nth(i).click();
            const panel = byId(page, await tabs.nth(i).getAttribute('aria-controls'));
            assert.equal(await panel.isVisible(), true);
            assert.match(await panel.innerText(), /준비 중입니다/);
        }
        await byId(page, 'transport-tab-rooms').click();
        assert.match(await byId(page, 'demo-room-empty').innerText(), /아직 모집 중인 택시팟이 없어요/);
        assert.equal(await page.locator('.demo-room-post').count(), 0);
        await byId(page, 'demo-go-create').click();
        const title = '긴 한글 모집 제목 '.repeat(20) + INJECTION;
        await fill(page, title);
        await byId(page, 'demo-destination').fill(INJECTION);
        await submit(page);
        assert.equal(await byId(page, 'demo-party-dialog').evaluate(dialog => dialog.open), false);
        assert.equal(await byId(page, 'demo-toast').innerText(), '작성 완료!');
        await byId(page, 'transport-tab-rooms').click();
        const post = page.locator('.demo-room-post').first();
        assert.match(await post.innerText(), /내가 만든 방/);
        await post.click();
        assert.equal(await byId(page, 'demo-count').innerText(), '1/4명');
        assert.equal(await byId(page, 'demo-room-title').innerText(), title);
        assert.equal(await byId(page, 'demo-room-destination').innerText(), INJECTION);
        assert.equal(await byId(page, 'demo-room-time').innerText(), '2030-10-21 18:00 ~ 2030-10-21 19:00');
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
        await byId(page, 'transport-tab-taxi').click();
        await submit(page);
        assert.equal(await page.locator('.demo-room-post').count(), 1, 'Unchanged submission does not create another post');
        assert.equal(await byId(page, 'demo-party-dialog').evaluate(dialog => dialog.open), false);
        await noOverflow(page);
        assert.deepEqual(await page.evaluate(() => window.__testGPSCalls), []);
        noDemoRequests(app);
        await page.reload({ waitUntil: 'networkidle' });
        await byId(page, 'transport-tab-rooms').click();
        assert.equal(await page.locator('.demo-room-post').count(), 0);
        assert.equal(await byId(page, 'demo-room-empty').isVisible(), true);
    } finally { await app.close(); }
});

test('validation retains values and keyboard tabs do not request data', async browser => {
    const app = await openApp(browser, { timezoneId: 'America/Los_Angeles' });
    try {
        const page = app.page;
        await byId(page, 'transport-tab-bus').focus();
        await page.keyboard.press('End');
        assert.equal(await byId(page, 'transport-tab-rooms').getAttribute('aria-selected'), 'true');
        await page.keyboard.press('ArrowLeft');
        assert.equal(await byId(page, 'transport-tab-taxi').getAttribute('aria-selected'), 'true');
        await submit(page);
        for (const name of ['title', 'destination', 'from', 'to']) assert.notEqual(await byId(page, 'demo-' + name + '-error').innerText(), '');
        assert.equal(await byId(page, 'demo-toast').isVisible(), false);
        await fill(page, '   ');
        await byId(page, 'demo-destination').fill('   ');
        await submit(page);
        assert.equal(await byId(page, 'demo-title').getAttribute('aria-invalid'), 'true');
        assert.equal(await byId(page, 'demo-destination').getAttribute('aria-invalid'), 'true');
        await fill(page);
        await byId(page, 'demo-to').fill('2030-10-21T17:00');
        await submit(page);
        assert.match(await byId(page, 'demo-to-error').innerText(), /빠를 수 없어요/);
        assert.equal(await byId(page, 'demo-title').inputValue(), '오늘 저녁 남춘천역 같이 가실 분!');
        assert.equal(await byId(page, 'demo-party-dialog').evaluate(dialog => dialog.open), false);
        await byId(page, 'demo-to').fill('2030-10-21T19:00');
        await submit(page);
        await byId(page, 'transport-tab-rooms').click();
        await page.locator('.demo-room-post').first().click();
        assert.equal(await byId(page, 'demo-room-time').innerText(), '2030-10-21 18:00 ~ 2030-10-21 19:00');
        noDemoRequests(app);
    } finally { await app.close(); }
});

test('toast timing, duplicate submission and independent rooms with local chat', async browser => {
    const app = await openApp(browser);
    try {
        const page = app.page;
        await byId(page, 'transport-tab-taxi').click();
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
        assert.equal(await byId(page, 'demo-toast').innerText(), '작성 완료!');
        assert.equal(await byId(page, 'demo-party-dialog').evaluate(dialog => dialog.open), false);
        await page.waitForFunction(() => document.getElementById('demo-toast').hidden);
        const events = await page.evaluate(() => window.__toastEvents);
        const shown = events.find(event => !event.hidden && !event.fading);
        const fading = events.find(event => event.fading && !event.hidden);
        const hidden = events.find(event => event.hidden);
        assert.ok(fading.at - shown.at >= 950, 'Toast stays opaque for approximately one second');
        assert.ok(hidden.at - fading.at >= 350, 'Fade lasts approximately 0.4 seconds');
        await submit(page);
        assert.equal(await byId(page, 'demo-toast').isVisible(), false, 'Duplicate submission does not show success');
        await byId(page, 'transport-tab-rooms').click();
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
        await byId(page, 'transport-tab-taxi').click();
        await byId(page, 'demo-new-post').click();
        await fill(page, '두 번째 방');
        await byId(page, 'demo-destination').fill('춘천역');
        await byId(page, 'demo-from').fill('2030-10-22T10:00');
        await byId(page, 'demo-to').fill('2030-10-22T11:00');
        await submit(page);
        await byId(page, 'transport-tab-rooms').click();
        const second = page.locator('.demo-room-post').first();
        assert.notEqual(await second.getAttribute('data-room-id'), firstId);
        assert.equal(await page.locator('.demo-room-post').count(), 2);
        assert.match(await second.innerText(), /두 번째 방/);
        await second.click();
        assert.equal(await byId(page, 'demo-count').innerText(), '1/4명');
        assert.equal(await byId(page, 'demo-room-destination').innerText(), '춘천역');
        assert.equal(await byId(page, 'demo-room-time').innerText(), '2030-10-22 10:00 ~ 2030-10-22 11:00');
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
