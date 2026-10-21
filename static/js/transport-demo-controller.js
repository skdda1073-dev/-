/** Memory-only transport preview. No geolocation, persistence or network requests. */
(function() {
    'use strict';
    const root = document.getElementById('transport-demo');
    if (!root) return;
    const byId = id => document.getElementById(id);
    const tabs = [...root.querySelectorAll('[role="tab"]')];
    const dialog = byId('demo-party-dialog');
    const form = byId('demo-party-form');
    const join = byId('demo-join');
    const rooms = new Map();
    const posts = new Map();
    const chatInput = byId('demo-chat-input');
    const chatList = byId('demo-chat-messages');
    const toast = byId('demo-toast');
    let room = null;
    let nextRoomId = 0;
    let creating = false;
    let lastSubmission = null;
    let toastTimer = null;
    let toastHideTimer = null;
    let opener = null;

    const timeRange = item => item.from.replace('T', ' ') + ' ~ ' + item.to.replace('T', ' ');
    const count = item => item.joined ? '2/4명' : '1/4명';
    function textNode(tag, value, className) {
        const element = document.createElement(tag);
        element.textContent = value;
        if (className) element.className = className;
        return element;
    }
    function showSuccess() {
        clearTimeout(toastTimer);
        clearTimeout(toastHideTimer);
        toast.classList.remove('is-fading');
        toast.textContent = '작성 완료!';
        toast.hidden = false;
        toastTimer = setTimeout(() => {
            toast.classList.add('is-fading');
            toastHideTimer = setTimeout(() => { toast.hidden = true; toast.textContent = ''; }, 400);
        }, 1000);
    }
    function renderList() {
        byId('demo-room-empty').hidden = rooms.size > 0;
        [...rooms.values()].reverse().forEach(item => {
            let post = posts.get(item.id);
            if (!post) {
                const li = document.createElement('li');
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'demo-room-post';
                button.dataset.roomId = item.id;
                const occupancy = textNode('span', count(item), 'demo-post-count');
                button.append(textNode('span', '내가 만든 방', 'demo-post-owner'),
                    textNode('strong', item.title), textNode('span', '목적지 · ' + item.destination),
                    textNode('span', timeRange(item)), textNode('span', '모집 중', 'party-state'), occupancy);
                button.addEventListener('click', () => open(item.id, button));
                li.append(button);
                post = { li, occupancy };
                posts.set(item.id, post);
            }
            post.occupancy.textContent = count(item);
        });
        // Reorder existing nodes without replacing buttons or losing their focus identity.
        byId('demo-room-list').replaceChildren(...[...rooms.keys()].reverse().map(id => posts.get(id).li));
    }

    function appendMessage(message) {
        const nearBottom = chatList.scrollHeight - chatList.scrollTop - chatList.clientHeight < 60;
        const previousScroll = chatList.scrollTop;
        const empty = chatList.querySelector('.demo-chat-empty');
        if (empty) empty.remove();
        const entry = document.createElement('article');
        entry.className = 'demo-message is-mine';
        entry.dataset.messageId = message.id;
        const at = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(message.at);
        entry.append(textNode('strong', message.nickname), textNode('p', message.body), textNode('small', at));
        chatList.append(entry);
        if (nearBottom) chatList.scrollTop = chatList.scrollHeight;
        else chatList.scrollTop = previousScroll;
    }
    function renderChat() {
        chatList.replaceChildren();
        if (!room.messages.length) chatList.append(textNode('p', '첫 인사를 남겨보세요!', 'demo-chat-empty'));
        room.messages.forEach(appendMessage);
        chatList.scrollTop = room.chatScroll === null ? chatList.scrollHeight : room.chatScroll;
        chatInput.value = room.draft;
        byId('demo-chat-status').textContent = room.chatStatus;
        byId('demo-chat-send').disabled = room.sending;
    }

    function selectTab(tab) {
        tabs.forEach(item => {
            const selected = item === tab;
            item.setAttribute('aria-selected', String(selected));
            item.tabIndex = selected ? 0 : -1;
            byId(item.getAttribute('aria-controls')).hidden = !selected;
        });
    }
    tabs.forEach((tab, index) => {
        tab.addEventListener('click', () => selectTab(tab));
        tab.addEventListener('keydown', event => {
            let next;
            if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % tabs.length;
            if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (index + tabs.length - 1) % tabs.length;
            if (event.key === 'Home') next = 0;
            if (event.key === 'End') next = tabs.length - 1;
            if (next === undefined) return;
            event.preventDefault();
            selectTab(tabs[next]);
            tabs[next].focus();
        });
    });

    function render() {
        byId('demo-room-title').textContent = room.title;
        byId('demo-room-destination').textContent = room.destination;
        byId('demo-room-time').textContent = timeRange(room);
        byId('demo-count').textContent = count(room);
        byId('demo-members').replaceChildren(...Array.from({ length: 4 }, (_, index) => {
            const slot = document.createElement('li');
            slot.textContent = index === 0 ? '방장(시연)' : index === 1 && room.joined ? '참가자(시연)' : '빈자리';
            slot.className = index === 0 || index === 1 && room.joined ? 'party-slot is-occupied' : 'party-slot';
            return slot;
        }));
        join.textContent = room.joined ? '참여 완료' : '같이 타요!';
        join.disabled = room.joined;
    }
    function open(id, button) {
        if (dialog.open || !rooms.has(id)) return;
        room = rooms.get(id);
        opener = button;
        render();
        renderChat();
        dialog.showModal();
        chatList.scrollTop = room.chatScroll === null ? chatList.scrollHeight : room.chatScroll;
        byId('demo-close').focus();
    }
    form.addEventListener('submit', event => {
        event.preventDefault();
        if (creating) return;
        const values = {};
        let firstError = null;
        const error = (name, message) => {
            byId('demo-' + name + '-error').textContent = message;
            byId('demo-' + name).setAttribute('aria-invalid', 'true');
            if (!firstError) firstError = byId('demo-' + name);
        };
        ['title', 'destination', 'from', 'to'].forEach(name => {
            const input = byId('demo-' + name);
            values[name] = input.value.trim();
            input.removeAttribute('aria-invalid');
            byId('demo-' + name + '-error').textContent = '';
            if (!values[name]) error(name, name === 'title' ? '제목을 입력해 주세요.' : name === 'destination' ? '목적지를 입력해 주세요.' : '날짜와 시간을 입력해 주세요.');
        });
        // Explicit offset avoids interpreting Korean wall-clock inputs in the browser's timezone.
        const start = Date.parse(values.from + '+09:00');
        const end = Date.parse(values.to + '+09:00');
        if (values.from && !Number.isFinite(start)) error('from', '올바른 날짜와 시간을 입력해 주세요.');
        if (values.to && !Number.isFinite(end)) error('to', '올바른 날짜와 시간을 입력해 주세요.');
        if (Number.isFinite(start) && Number.isFinite(end) && end < start) error('to', '종료 시간은 시작 시간보다 빠를 수 없어요.');
        if (firstError) { firstError.focus(); return; }
        const fingerprint = JSON.stringify(values);
        if (fingerprint === lastSubmission) {
            byId('demo-create-status').textContent = '이미 작성한 모집글입니다. 방 찾기에서 확인하거나 새 모집글 작성을 눌러 주세요.';
            return;
        }
        creating = true;
        const submit = form.querySelector('[type="submit"]');
        submit.disabled = true;
        try {
            const id = 'demo-room-' + (++nextRoomId);
            rooms.set(id, { ...values, id, joined: false, messages: [], draft: '', sending: false, chatStatus: '', chatScroll: null });
            renderList();
            lastSubmission = fingerprint;
            byId('demo-new-post').hidden = false;
            byId('demo-create-status').textContent = '방 찾기 탭에서 작성한 모집글을 확인해 주세요.';
            showSuccess();
        } finally {
            queueMicrotask(() => { creating = false; submit.disabled = false; });
        }
    });
    byId('demo-new-post').addEventListener('click', () => {
        lastSubmission = null;
        form.reset();
        ['title', 'destination', 'from', 'to'].forEach(name => {
            byId('demo-' + name).removeAttribute('aria-invalid');
            byId('demo-' + name + '-error').textContent = '';
        });
        byId('demo-create-status').textContent = '새 모집글을 작성해 주세요.';
        byId('demo-title').focus();
    });
    byId('demo-go-create').addEventListener('click', () => {
        selectTab(byId('transport-tab-taxi'));
        byId('demo-title').focus();
    });
    join.addEventListener('click', () => {
        if (!room || room.joined) return;
        room.joined = true;
        render();
        renderList();
        // Keep focus inside the dialog when the active button becomes disabled.
        byId('demo-close').focus();
    });
    byId('demo-close').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => {
        if (room) { room.draft = chatInput.value; room.chatScroll = chatList.scrollTop; }
        if (opener && opener.isConnected) opener.focus();
        room = null;
    });
    chatInput.addEventListener('input', () => { if (room) room.draft = chatInput.value; });
    chatInput.addEventListener('keydown', event => {
        if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return;
        event.preventDefault();
        byId('demo-chat-form').requestSubmit();
    });
    byId('demo-chat-form').addEventListener('submit', async event => {
        event.preventDefault();
        const current = room;
        if (!current || current.sending) return;
        const body = chatInput.value.trim();
        current.draft = chatInput.value;
        if (!body || body.length > 500) {
            byId('demo-chat-status').textContent = '메시지는 공백을 제외한 1~500자로 입력해 주세요.';
            return;
        }
        current.sending = true;
        byId('demo-chat-send').disabled = true;
        try {
            // Local preview only: this microtask is not a server acknowledgement.
            await Promise.resolve();
            const message = { id: current.id + '-message-' + (current.messages.length + 1), nickname: '나(시연)', body, at: Date.now() };
            if (room === current && dialog.open) appendMessage(message);
            current.messages.push(message);
            if (current.draft.trim() === body) current.draft = '';
            current.chatStatus = '이 페이지에만 표시한 시연 메시지입니다.';
            if (room === current && dialog.open) {
                chatInput.value = current.draft;
                byId('demo-chat-status').textContent = current.chatStatus;
            }
        } catch (error) {
            current.chatStatus = '시연 메시지를 표시하지 못했어요. 내용을 확인하고 다시 눌러 주세요.';
            if (room === current) byId('demo-chat-status').textContent = current.chatStatus;
        } finally {
            current.sending = false;
            if (room === current) byId('demo-chat-send').disabled = false;
        }
    });
    // Native modal dialog provides background inertness and Escape handling.
    dialog.addEventListener('keydown', event => {
        if (event.key !== 'Tab') return;
        const controls = [...dialog.querySelectorAll('button:not(:disabled), textarea, [tabindex="0"]')];
        const first = controls[0], last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
})();
