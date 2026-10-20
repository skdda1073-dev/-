/** Anonymous room UI. Only the currently visible room polls messages; quotes never poll TMAP. */
(function() {
    let initialized = false;
    let csrfToken = null;
    let sessionPromise = null;
    let view = null;
    let viewVersion = 0;
    let active = null;
    let listController = null;
    let listCursor = null;
    let listVersion = 0;
    let listLoading = false;
    let createContext = null;
    let meeting = null;
    let meetingController = null;
    let meetingTimer = null;
    let meetingVersion = 0;
    let createBusy = false;
    let createAttempt = null;
    let createContextInvalidated = false;
    const byId = function(id) { return document.getElementById(id); };
    const labels = { open: '모집 중', full: '정원 마감', closed: '방 종료', expired: '출발시각 지남' };
    const money = function(value) { return Number.isSafeInteger(value) && value >= 0 ? value.toLocaleString('ko-KR') + '원' : '계산 불가'; };

    function node(tag, value, className) {
        const element = document.createElement(tag);
        if (value !== undefined) element.textContent = String(value);
        if (className) element.className = className;
        return element;
    }

    function uuid() {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
        const bytes = new Uint8Array(16);
        window.crypto.getRandomValues(bytes);
        bytes[6] = (bytes[6] & 15) | 64;
        bytes[8] = (bytes[8] & 63) | 128;
        const hex = Array.from(bytes, function(value) { return value.toString(16).padStart(2, '0'); }).join('');
        return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-');
    }

    function status(message, failed) {
        const target = byId('room-status');
        if (!target) return;
        target.textContent = message || '';
        target.classList.toggle('room-status--error', Boolean(failed));
    }

    function setText(id, value) { const target = byId(id); if (target) target.textContent = value || ''; }
    function show(id, visible) { const target = byId(id); if (target) target.hidden = !visible; }
    function focus(id) { const target = byId(id); if (target) target.focus({ preventScroll: true }); }
    function validPlace(place) {
        return place && typeof place.name === 'string' && place.name.trim() && place.name.length <= 100 &&
            window.AppState.isValidPosition(place.lat, place.lon);
    }

    function kstDate(value) {
        const at = new Date(value);
        return Number.isFinite(at.getTime()) ? new Intl.DateTimeFormat('ko-KR', {
            timeZone: 'Asia/Seoul', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false
        }).format(at) + ' (한국 시간)' : '시각 확인 불가';
    }

    function localInput(at) {
        return new Date(at.getTime() + 9 * 3600000).toISOString().slice(0, 16);
    }

    function isoKST(value) {
        if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value || '')) return null;
        const iso = value + ':00+09:00';
        return Number.isFinite(new Date(iso).getTime()) ? iso : null;
    }

    async function fetchJSON(url, options) {
        options = options || {};
        const controller = new AbortController();
        const parent = options.signal;
        const onAbort = function() { controller.abort(); };
        if (parent) {
            if (parent.aborted) controller.abort();
            else parent.addEventListener('abort', onAbort, { once: true });
        }
        let timedOut = false;
        const timeout = setTimeout(function() { timedOut = true; controller.abort(); }, 15000);
        try {
            const response = await fetch(url, { ...options, credentials: 'same-origin', cache: 'no-store', signal: controller.signal });
            let data;
            try { data = await response.json(); }
            catch (error) { throw new Error('서버 응답을 읽지 못했어요. 잠시 후 다시 시도해 주세요.'); }
            if (!response.ok) {
                const error = new Error(data && typeof data.error === 'string' ? data.error : '요청을 처리하지 못했어요.');
                error.status = response.status;
                error.code = data && data.code;
                throw error;
            }
            if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('서버 응답 형식을 확인할 수 없어요.');
            return data;
        } catch (error) {
            if (timedOut) throw new Error('응답이 늦어지고 있어요. 잠시 후 다시 시도해 주세요.');
            if (error instanceof TypeError) throw new Error('연결 상태를 확인하고 다시 시도해 주세요.');
            throw error;
        } finally {
            clearTimeout(timeout);
            if (parent) parent.removeEventListener('abort', onAbort);
        }
    }

    async function ensureSession() {
        if (csrfToken) return;
        const prepare = function() { return fetchJSON('/api/ride-rooms/session'); };
        if (!sessionPromise) sessionPromise = (navigator.locks && typeof navigator.locks.request === 'function'
            ? navigator.locks.request('capstone-ride-session', prepare) : prepare()).then(function(data) {
            if (!data.ready || typeof data.csrf_token !== 'string' || !data.csrf_token) {
                throw new Error('동승 채팅 설정을 준비하지 못했어요. 서버 설정을 확인해 주세요.');
            }
            csrfToken = data.csrf_token;
        }).finally(function() { sessionPromise = null; });
        return sessionPromise;
    }

    async function api(path, method, body, signal) {
        method = method || 'GET';
        if (method !== 'GET') await ensureSession();
        return fetchJSON('/api/ride-rooms' + path, {
            method: method,
            ...(signal ? { signal: signal } : {}),
            ...(method !== 'GET' ? {
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
                body: JSON.stringify(body || {})
            } : {})
        });
    }

    function isActive(room) { return active === room && view === 'detail' && !byId('room-panel').hidden; }
    function stopPolling(room) {
        if (!room) return;
        clearTimeout(room.pollTimer);
        room.pollTimer = null;
        room.pollGeneration += 1;
        if (room.pollController) room.pollController.abort();
        room.pollController = null;
        room.pollInFlight = false;
    }

    function stopRoom() {
        if (!active) return;
        stopPolling(active);
        clearTimeout(active.quoteTimer);
        clearTimeout(active.departureTimer);
        if (active.detailController) active.detailController.abort();
        if (active.quoteController) active.quoteController.abort();
        active = null;
    }

    function cancelMeeting() {
        meetingVersion += 1;
        clearTimeout(meetingTimer);
        if (meetingController) meetingController.abort();
        meetingController = null;
    }

    function setView(next) {
        stopRoom();
        cancelMeeting();
        if (listController) listController.abort();
        listVersion += 1;
        listLoading = false;
        viewVersion += 1;
        view = next;
        show('room-panel', Boolean(next));
        show('room-open-list', !next);
        ['list', 'create', 'detail'].forEach(function(name) { show('room-' + name + '-view', name === next); });
        show('room-create-current', next !== 'create');
        status('');
        if (window.AppModules.ui) window.AppModules.ui.setPlannerExpanded(true);
    }

    async function openList(query) {
        setView('list');
        if (typeof query === 'string') byId('room-filter-q').value = query;
        focus('room-list-heading');
        await loadList(false);
    }

    async function loadList(more) {
        if (view !== 'list' || listLoading) return;
        const version = ++listVersion;
        const generation = viewVersion;
        if (listController) listController.abort();
        const controller = new AbortController();
        listController = controller;
        listLoading = true;
        byId('room-load-more').disabled = true;
        if (!more) { listCursor = null; byId('room-list').replaceChildren(); }
        const query = new URLSearchParams({ limit: '20' });
        const keyword = byId('room-filter-q').value.trim();
        if (keyword) query.set('q', keyword);
        const filterStatus = byId('room-filter-status').value;
        query.set('status', filterStatus || 'all');
        ['from', 'to'].forEach(function(name) {
            const value = isoKST(byId('room-filter-' + name).value);
            if (value) query.set(name, value);
        });
        if (more && listCursor) query.set('cursor', listCursor);
        status('모집방을 확인하고 있어요…');
        try {
            await ensureSession();
            if (generation !== viewVersion || version !== listVersion) return;
            const data = await api('?' + query.toString(), 'GET', null, controller.signal);
            if (generation !== viewVersion || version !== listVersion) return;
            const rooms = Array.isArray(data.rooms) ? data.rooms : [];
            rooms.forEach(function(item) {
                const room = item.room || item;
                const card = node('article', undefined, 'room-card');
                card.append(node('h4', room.title), node('p', room.meeting.name + ' → ' + room.destination.name),
                    node('p', kstDate(room.departure_at)), node('p', room.participant_count + '/' + room.capacity + '명 · ' + (labels[room.status] || '상태 확인 중')));
                const button = node('button', '방 자세히 보기', 'room-button room-button--secondary');
                button.type = 'button';
                button.addEventListener('click', function() { openRoom(room.id); });
                card.appendChild(button);
                byId('room-list').appendChild(card);
            });
            listCursor = data.next_cursor || null;
            show('room-load-more', Boolean(listCursor));
            status(!more && !rooms.length ? '조건에 맞는 모집방이 없어요. 현재 목적지로 새 방을 만들 수 있어요.' : '모집 목록을 확인했어요. 방을 열어도 자동으로 참가하지 않아요.');
        } catch (error) {
            if (generation === viewVersion && version === listVersion && error.name !== 'AbortError') status(error.message, true);
        } finally {
            if (generation === viewVersion && version === listVersion) {
                listLoading = false;
                byId('room-load-more').disabled = false;
            }
        }
    }

    function snapshot() {
        const position = window.AppState.getCurrentPosition();
        return JSON.stringify({
            campus: window.AppState.getSelectedCampus(), mode: window.AppState.getOriginMode(),
            building: byId('building-select').value, destination: window.AppState.getDestination(),
            position: position ? [position.lat, position.lon] : null
        });
    }

    function createFromCurrent() {
        const destination = window.AppState.getDestination();
        if (!validPlace(destination)) {
            status('먼저 경로 검색에서 목적지를 검색하고 선택해 주세요.', true);
            focus('dest-input');
            return;
        }
        const initial = snapshot();
        openCreate({ payload: { dest_name: destination.name, dest_lat: destination.lat, dest_lon: destination.lon,
            campus: window.AppState.getSelectedCampus(), location: byId('building-select').value || '내 실시간 위치' },
            mode: window.AppState.getOriginMode(), isCurrent: function() { return initial === snapshot(); } });
    }

    function updateCreateButton() {
        const current = createContext && !createContextInvalidated && createContext.isCurrent();
        byId('room-create-submit').disabled = createBusy || !current || !meeting || !byId('room-meeting-confirm').checked;
    }

    function invalidateSearchContext() {
        if (view !== 'create' || !createContext) return;
        createContextInvalidated = true;
        setText('room-create-context', '경로가 바뀌었어요. 취소한 뒤 새 목적지로 방을 다시 준비해 주세요.');
        updateCreateButton();
    }

    async function openCreate(context) {
        if (!context || !context.isCurrent() || !validPlace({ name: context.payload.dest_name,
            lat: context.payload.dest_lat, lon: context.payload.dest_lon })) return;
        setView('create');
        createContext = context;
        createContextInvalidated = false;
        meeting = null;
        createBusy = false;
        createAttempt = null;
        byId('room-create-form').reset();
        byId('room-meeting-options').replaceChildren();
        show('room-meeting-selected', false);
        byId('room-meeting-confirm').disabled = true;
        byId('room-departure').value = localInput(new Date(Date.now() + 30 * 60000));
        setText('room-meeting-status', '공개할 장소를 검색하고 목록에서 선택해 주세요.');
        setText('room-create-context', context.mode === 'gps'
            ? '현재 GPS를 집결지로 공개하지 않아요. 공개할 만날 장소를 직접 선택해 주세요.'
            : '교내 건물이나 정문 등 공개할 만날 장소를 검색해 선택해 주세요.');
        setText('room-create-destination', '공개 목적지: ' + context.payload.dest_name);
        updateCreateButton();
        focus('room-title');
        const generation = viewVersion;
        try { await ensureSession(); }
        catch (error) { if (generation === viewVersion) status(error.message, true); }
    }

    function searchMeeting() {
        cancelMeeting();
        meeting = null;
        byId('room-meeting-confirm').checked = false;
        byId('room-meeting-confirm').disabled = true;
        show('room-meeting-selected', false);
        byId('room-meeting-options').replaceChildren();
        updateCreateButton();
        const query = byId('room-meeting-query').value.trim();
        if (!query) { setText('room-meeting-status', '공개할 장소를 검색해 주세요.'); return; }
        const version = meetingVersion;
        const generation = viewVersion;
        setText('room-meeting-status', '공개 집결 장소를 찾고 있어요…');
        meetingTimer = setTimeout(async function() {
            const controller = new AbortController();
            meetingController = controller;
            const timeout = setTimeout(function() { controller.abort(); }, 15000);
            try {
                // This picker reuses the API, never the destination controller or AppState setters.
                const response = await fetch('/search_poi?q=' + encodeURIComponent(query) + '&campus=' + encodeURIComponent(createContext.payload.campus), {
                    credentials: 'same-origin', signal: controller.signal, cache: 'no-store'
                });
                const items = await response.json();
                if (generation !== viewVersion || version !== meetingVersion) return;
                if (!response.ok || !Array.isArray(items)) throw new Error(items && typeof items.error === 'string' ? items.error : '장소 검색을 연결하지 못했어요.');
                const places = items.filter(validPlace).slice(0, 12);
                places.forEach(function(place) {
                    const button = node('button', undefined, 'room-place-option');
                    button.type = 'button';
                    button.append(node('span', place.name), node('small', typeof place.sub === 'string' ? place.sub : ''));
                    button.addEventListener('click', function() {
                        if (version !== meetingVersion || generation !== viewVersion) return;
                        meeting = { name: place.name.trim(), lat: Number(place.lat), lon: Number(place.lon) };
                        byId('room-meeting-options').replaceChildren();
                        setText('room-meeting-selected', '공개 집결 장소: ' + meeting.name);
                        show('room-meeting-selected', true);
                        byId('room-meeting-confirm').disabled = false;
                        setText('room-meeting-status', '장소를 확인하고 공개 동의를 선택해 주세요.');
                        updateCreateButton();
                        focus('room-meeting-confirm');
                    });
                    byId('room-meeting-options').appendChild(button);
                });
                setText('room-meeting-status', places.length ? '목록에서 공개 집결 장소를 선택해 주세요.' : '검색 결과가 없어요. 다른 공개 장소를 검색해 주세요.');
            } catch (error) {
                if (generation === viewVersion && version === meetingVersion && error.name !== 'AbortError') {
                    setText('room-meeting-status', error instanceof TypeError || error instanceof SyntaxError ? '장소 검색을 연결하지 못했어요.' : error.message);
                }
            } finally {
                clearTimeout(timeout);
            }
        }, 250);
    }

    async function submitCreate(event) {
        event.preventDefault();
        if (createBusy) return;
        if (!createContext || createContextInvalidated || !createContext.isCurrent()) { invalidateSearchContext(); return; }
        if (!meeting || !byId('room-meeting-confirm').checked) { status('공개 집결 장소를 선택하고 확인해 주세요.', true); return; }
        const departure = isoKST(byId('room-departure').value);
        if (!departure) { status('한국 시간으로 출발시각을 입력해 주세요.', true); return; }
        const payload = { title: byId('room-title').value.trim(), nickname: byId('room-create-nickname').value.trim(),
            meeting: { ...meeting }, destination: { name: createContext.payload.dest_name, lat: createContext.payload.dest_lat, lon: createContext.payload.dest_lon },
            departure_at: departure, capacity: Number(byId('room-capacity').value) };
        const fingerprint = JSON.stringify(payload);
        if (!createAttempt || createAttempt.fingerprint !== fingerprint) createAttempt = { fingerprint: fingerprint, id: uuid() };
        payload.client_room_id = createAttempt.id;
        const generation = viewVersion;
        createBusy = true;
        updateCreateButton();
        status('모집방을 만들고 있어요…');
        try {
            const data = await api('', 'POST', payload);
            if (generation !== viewVersion || view !== 'create') return;
            await openRoom(data.room.id);
            status('방을 만들었어요. 방장도 참가 인원에 포함돼요.');
        } catch (error) {
            if (generation === viewVersion) status(error.message + ' 같은 내용으로 다시 누르면 중복 생성되지 않아요.', true);
        } finally {
            createBusy = false;
            if (view === 'create' && generation === viewVersion) updateCreateButton();
        }
    }

    function acceptMetadata(current, data) {
        if (!data.room || typeof data.room !== 'object') return;
        if (current.room && Number(data.room.room_version) < Number(current.room.room_version)) return;
        current.room = data.room;
        if (data.self) current.self = data.self;
        if (Array.isArray(data.participants)) current.participants = data.participants;
        renderRoom(current);
    }

    function canChat(current) {
        return current.self && current.self.joined && current.room && ['open', 'full'].includes(current.room.status);
    }

    function renderRoom(current) {
        if (!isActive(current) || !current.room) return;
        const room = current.room;
        const joined = Boolean(current.self && current.self.joined);
        setText('room-detail-title', room.title);
        setText('room-detail-route', room.meeting.name + ' → ' + room.destination.name);
        setText('room-detail-time', '출발 ' + kstDate(room.departure_at));
        setText('room-detail-state', labels[room.status] || '상태 확인 중');
        byId('room-detail-state').classList.toggle('room-state--inactive', room.status === 'closed' || room.status === 'expired');
        setText('room-detail-count', '현재 ' + room.participant_count + '명 / 정원 ' + room.capacity + '명 · 방장 포함');
        byId('room-members').replaceChildren(...(joined ? current.participants : []).map(function(member) {
            return node('li', member.nickname + (member.is_host ? ' · 방장' : '') + (current.self.member_id === member.member_id ? ' · 나' : ''));
        }));
        show('room-join-form', !joined && room.status === 'open');
        show('room-member-content', joined);
        setText('room-visitor-note', joined ? '' : (room.status === 'open'
            ? '대화와 방의 견적은 참가한 사람에게만 보여요.' : '지금은 새로 참가할 수 없어요.'));
        show('room-leave', joined && !current.self.is_host);
        show('room-close', joined && current.self.is_host && room.status !== 'closed');
        show('room-message-form', joined && canChat(current));
        if (!canChat(current)) {
            setText('room-chat-status', joined ? '읽기 전용이에요. 새 참가와 메시지 전송이 중단됐어요.' : '참가 후 대화를 볼 수 있어요.');
            stopPolling(current);
        }
        clearTimeout(current.departureTimer);
        const remaining = new Date(room.departure_at).getTime() - Date.now();
        if (['open', 'full'].includes(room.status) && Number.isFinite(remaining)) {
            current.departureTimer = setTimeout(function() {
                if (!isActive(current) || current.room.status === 'closed') return;
                current.room = { ...current.room, status: 'expired' };
                renderRoom(current);
            }, Math.max(0, remaining));
        }
        renderQuote(current);
    }

    async function openRoom(id) {
        setView('detail');
        const current = { id: id, room: null, self: null, participants: [], seen: new Set(), lastId: null,
            pollGeneration: 0, pollInFlight: false, failures: 0, quote: null, quoteBusy: false, actionBusy: false,
            messageAttempt: null, detailController: new AbortController() };
        active = current;
        byId('room-messages').replaceChildren();
        byId('room-message-body').value = '';
        byId('room-estimate-content').replaceChildren(node('p', '방의 현재 견적을 준비하고 있어요.'));
        show('room-member-content', false);
        show('room-join-form', false);
        show('room-close-confirm', false);
        status('방을 확인하고 있어요…');
        try {
            await ensureSession();
            const data = await api('/' + encodeURIComponent(id), 'GET', null, current.detailController.signal);
            if (!isActive(current)) return;
            acceptMetadata(current, data);
            status('');
            focus('room-detail-title');
            if (current.self && current.self.joined) {
                loadMessages(current);
                loadQuote(current);
            }
        } catch (error) {
            if (isActive(current) && error.name !== 'AbortError') status(error.message, true);
        }
    }

    function quoteExpires(quote) {
        if (!quote || !quote.estimate) return 0;
        const estimate = quote.estimate;
        const explicit = new Date(estimate.expires_at).getTime();
        if (Number.isFinite(explicit)) return explicit;
        const fetched = new Date(estimate.fetched_at).getTime();
        const ttl = typeof estimate.cache_ttl_seconds === 'number' ? estimate.cache_ttl_seconds : 60;
        return Number.isFinite(fetched) ? fetched + ttl * 1000 : 0;
    }

    function renderQuote(current) {
        if (!isActive(current)) return;
        clearTimeout(current.quoteTimer);
        const content = byId('room-estimate-content');
        const quote = current.quote;
        const expires = quoteExpires(quote);
        if (!quote || expires <= Date.now()) {
            content.replaceChildren(node('p', current.quoteBusy ? '택시 견적을 확인하고 있어요…'
                : quote ? '견적이 만료됐어요. 갱신해 주세요. 예상 분담금은 계산할 수 없어요.' : '견적이 없어요. 갱신해서 예상 분담금을 확인해 주세요.'));
            return;
        }
        const estimate = quote.estimate;
        const amount = estimate.fare && estimate.fare.amount_krw;
        const duration = estimate.ride_duration_seconds;
        const nodes = [node('p', typeof duration === 'number' && Number.isFinite(duration)
            ? '택시 이동 약 ' + Math.ceil(duration / 60) + '분' : '주행시간 조회 불가'),
            node('p', Number.isSafeInteger(amount) && amount >= 0 ? '예상요금 약 ' + money(amount) : '요금 조회 불가'),
            node('p', '조회 ' + kstDate(estimate.fetched_at), 'room-caption')];
        const sameVersion = Number(quote.room_version) === Number(current.room.room_version);
        const authoritative = sameVersion && quote.participant_count === current.room.participant_count && Array.isArray(quote.current_split);
        nodes.push(node('p', '현재 ' + current.room.participant_count + '명 기준 예상 분담금' + (authoritative ? '' : ' · 인원 변경 반영 중')));
        let shares = authoritative ? quote.current_split : [];
        if (!authoritative && Number.isSafeInteger(amount) && amount >= 0 && current.participants.length === current.room.participant_count && current.participants.length > 0) {
            const count = current.participants.length;
            const quotient = Math.floor(amount / count), remainder = amount % count;
            shares = current.participants.map(function(member, index) { return { ...member, amount_krw: quotient + (index < remainder ? 1 : 0) }; });
        }
        const split = node('ul');
        if (shares.length) shares.forEach(function(member) { split.appendChild(node('li', member.nickname + ': ' + money(member.amount_krw))); });
        else split.appendChild(node('li', '현재 분담금 계산 불가'));
        nodes.push(split);
        const preview = quote.capacity_preview;
        const capacityAmounts = preview && Array.isArray(preview.amounts_krw) ? preview.amounts_krw : null;
        nodes.push(node('p', '정원 ' + current.room.capacity + '명이 모두 찼을 때의 가정: ' + (capacityAmounts && capacityAmounts.length === current.room.capacity
            ? capacityAmounts.map(money).join(' / ') : '계산 불가')));
        content.replaceChildren(...nodes);
        current.quoteTimer = setTimeout(function() { if (isActive(current)) renderQuote(current); }, Math.max(1, expires - Date.now()));
    }

    async function loadQuote(current) {
        if (!isActive(current) || !current.self || !current.self.joined || current.quoteBusy) return;
        current.quoteBusy = true;
        const controller = new AbortController();
        current.quoteController = controller;
        byId('room-estimate-refresh').disabled = true;
        renderQuote(current);
        try {
            const data = await api('/' + encodeURIComponent(current.id) + '/estimate', 'GET', null, controller.signal);
            if (!isActive(current)) return;
            current.quote = data;
        } catch (error) {
            if (isActive(current) && error.name !== 'AbortError') {
                current.quote = null;
                status('방의 견적을 확인하지 못했어요. ' + error.message + ' 대화는 계속 사용할 수 있어요.', true);
            }
        } finally {
            current.quoteBusy = false;
            if (isActive(current)) { byId('room-estimate-refresh').disabled = false; renderQuote(current); }
        }
    }

    function appendMessages(current, messages) {
        const container = byId('room-messages');
        const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 60;
        const containerTop = container.getBoundingClientRect().top;
        const anchor = Array.from(container.children).find(function(child) { return child.getBoundingClientRect().bottom >= containerTop; });
        const anchorTop = anchor ? anchor.getBoundingClientRect().top : null;
        (Array.isArray(messages) ? messages : []).forEach(function(message) {
            if (current.seen.has(message.id)) return;
            current.seen.add(message.id);
            const entry = node('article', undefined, 'room-message');
            entry.dataset.messageId = String(message.id);
            entry.append(node('strong', message.nickname), node('p', message.body), node('small', kstDate(message.created_at)));
            // A send acknowledgement can arrive before an older message from another participant.
            const following = Array.from(container.children).find(function(child) { return Number(child.dataset.messageId) > message.id; });
            container.insertBefore(entry, following || null);
        });
        if (nearBottom) container.scrollTop = container.scrollHeight;
        else if (anchor && anchorTop !== null) container.scrollTop += anchor.getBoundingClientRect().top - anchorTop;
    }

    function schedulePoll(current, delay, allowReadonly) {
        clearTimeout(current.pollTimer);
        if (!isActive(current) || document.hidden || !current.self || !current.self.joined) return;
        if (!allowReadonly && !canChat(current)) return;
        current.pollTimer = setTimeout(function() { loadMessages(current); }, delay);
    }

    async function loadMessages(current) {
        if (!isActive(current) || document.hidden || current.pollInFlight || !current.self || !current.self.joined) return;
        current.pollInFlight = true;
        const generation = current.pollGeneration;
        const controller = new AbortController();
        current.pollController = controller;
        const query = new URLSearchParams({ limit: current.lastId === null ? '50' : '100' });
        if (current.lastId !== null) query.set('after_id', String(current.lastId));
        let nextDelay = 3000, hasMore = false;
        try {
            const data = await api('/' + encodeURIComponent(current.id) + '/messages?' + query.toString(), 'GET', null, controller.signal);
            if (!isActive(current) || generation !== current.pollGeneration || document.hidden) return;
            appendMessages(current, data.messages);
            const previous = current.lastId;
            if (Number.isSafeInteger(data.next_after_id) && data.next_after_id >= 0) current.lastId = data.next_after_id;
            hasMore = Boolean(data.has_more && current.lastId !== previous);
            if (data.cached_estimate) current.quote = data.cached_estimate;
            acceptMetadata(current, data);
            if (canChat(current)) setText('room-chat-status', '새 대화와 참가 인원을 약 3초마다 확인해요.');
            current.failures = 0;
            nextDelay = hasMore ? 0 : 3000;
        } catch (error) {
            if (!isActive(current) || generation !== current.pollGeneration || error.name === 'AbortError') return;
            current.failures += 1;
            nextDelay = Math.min(30000, 3000 * Math.pow(2, Math.min(current.failures, 4)));
            setText('room-chat-status', error.message + ' 잠시 후 다시 확인해요.');
            if (error.status === 403 || error.status === 404) {
                current.self = { joined: false, is_host: false, member_id: null };
                current.quote = null;
                byId('room-messages').replaceChildren();
                renderRoom(current);
            }
        } finally {
            if (isActive(current) && generation === current.pollGeneration) {
                current.pollInFlight = false;
                current.pollController = null;
                schedulePoll(current, nextDelay, hasMore);
            } else if (isActive(current) && hasMore) {
                // A terminal status changes the poll generation; still drain its remaining read-only page.
                current.pollInFlight = false;
                schedulePoll(current, 0, true);
            }
        }
    }

    async function joinRoom(event) {
        event.preventDefault();
        const current = active;
        if (!current || current.actionBusy) return;
        current.actionBusy = true;
        byId('room-join-submit').disabled = true;
        try {
            const data = await api('/' + encodeURIComponent(current.id) + '/join', 'POST', { nickname: byId('room-join-nickname').value.trim() });
            if (!isActive(current)) return;
            acceptMetadata(current, data);
            if (!data.room || !data.self) { await openRoom(current.id); return; }
            status('참가했어요. 현재 인원에 포함돼요.');
            loadMessages(current);
            loadQuote(current);
        } catch (error) { if (isActive(current)) status(error.message, true); }
        finally { current.actionBusy = false; if (isActive(current)) byId('room-join-submit').disabled = false; }
    }

    async function sendMessage(event) {
        event.preventDefault();
        const current = active;
        if (!current || !canChat(current) || current.sending) return;
        const input = byId('room-message-body');
        const body = input.value.trim();
        if (!body || body.length > 500) { status('메시지는 1~500자로 입력해 주세요.', true); return; }
        if (!current.messageAttempt || current.messageAttempt.body !== body) current.messageAttempt = { body: body, id: uuid() };
        const attempt = current.messageAttempt;
        current.sending = true;
        byId('room-message-send').disabled = true;
        try {
            const data = await api('/' + encodeURIComponent(current.id) + '/messages', 'POST', { body: body, client_message_id: attempt.id });
            if (!isActive(current)) return;
            if (data.message) appendMessages(current, [data.message]);
            else if (Number.isSafeInteger(data.id)) appendMessages(current, [data]);
            if (input.value.trim() === body) input.value = '';
            current.messageAttempt = null;
            status('메시지를 보냈어요.');
            // Do not advance the polling cursor here: another participant's earlier message may still be pending.
            focus('room-message-body');
        } catch (error) {
            if (isActive(current)) status(error.message + ' 다시 보내도 같은 메시지는 한 번만 저장돼요.', true);
            if (isActive(current) && error.status === 409) refreshMembership(current);
        } finally {
            current.sending = false;
            if (isActive(current)) byId('room-message-send').disabled = false;
        }
    }

    async function leaveRoom() {
        const current = active;
        if (!current || current.actionBusy || !current.self || current.self.is_host) return;
        current.actionBusy = true;
        byId('room-leave').disabled = true;
        stopPolling(current);
        try {
            await api('/' + encodeURIComponent(current.id) + '/leave', 'POST', {});
            if (!isActive(current)) return;
            current.quote = null;
            byId('room-messages').replaceChildren();
            await openRoom(current.id);
            status('방에서 나왔어요. 이후 대화는 볼 수 없어요.');
        } catch (error) {
            if (isActive(current)) { status(error.message, true); schedulePoll(current, 3000); }
        } finally { current.actionBusy = false; if (isActive(current)) byId('room-leave').disabled = false; }
    }

    async function closeRoom() {
        const current = active;
        if (!current || current.actionBusy || !current.self || !current.self.is_host) return;
        current.actionBusy = true;
        byId('room-close-yes').disabled = true;
        try {
            const data = await api('/' + encodeURIComponent(current.id) + '/close', 'POST', {});
            if (!isActive(current)) return;
            show('room-close-confirm', false);
            if (data.room) acceptMetadata(current, data);
            else await refreshMembership(current);
            status('방을 종료했어요. 기존 참가자는 대화를 읽을 수 있어요.');
            loadMessages(current);
        } catch (error) { if (isActive(current)) status(error.message, true); }
        finally { current.actionBusy = false; if (isActive(current)) byId('room-close-yes').disabled = false; }
    }

    async function refreshMembership(current) {
        if (!isActive(current) || document.hidden) return;
        if (current.detailController) current.detailController.abort();
        const controller = new AbortController();
        current.detailController = controller;
        const generation = current.pollGeneration;
        try {
            const data = await api('/' + encodeURIComponent(current.id), 'GET', null, controller.signal);
            if (!isActive(current) || generation !== current.pollGeneration || document.hidden) return;
            acceptMetadata(current, data);
            if (current.self && current.self.joined) loadMessages(current);
        } catch (error) {
            if (isActive(current) && error.name !== 'AbortError') {
                setText('room-chat-status', error.message + ' 잠시 후 참가 상태를 다시 확인해요.');
                current.pollTimer = setTimeout(function() { refreshMembership(current); }, 6000);
            }
        }
    }

    function hide() {
        setView(null);
        focus('room-open-list');
    }

    function init() {
        if (initialized) return;
        initialized = true;
        byId('room-open-list').addEventListener('click', function() { openList(); });
        byId('room-hide').addEventListener('click', hide);
        byId('room-create-current').addEventListener('click', createFromCurrent);
        byId('room-filter-form').addEventListener('submit', function(event) {
            event.preventDefault();
            if (listController) listController.abort();
            listLoading = false;
            loadList(false);
        });
        byId('room-load-more').addEventListener('click', function() { loadList(true); });
        byId('room-create-form').addEventListener('submit', submitCreate);
        byId('room-create-cancel').addEventListener('click', function() { openList(); });
        byId('room-meeting-query').addEventListener('input', searchMeeting);
        byId('room-meeting-confirm').addEventListener('change', updateCreateButton);
        byId('room-detail-back').addEventListener('click', function() { openList(); });
        byId('room-join-form').addEventListener('submit', joinRoom);
        byId('room-message-form').addEventListener('submit', sendMessage);
        byId('room-estimate-refresh').addEventListener('click', function() { if (active) loadQuote(active); });
        byId('room-leave').addEventListener('click', leaveRoom);
        byId('room-close').addEventListener('click', function() { show('room-close-confirm', true); focus('room-close-yes'); });
        byId('room-close-no').addEventListener('click', function() { show('room-close-confirm', false); focus('room-close'); });
        byId('room-close-yes').addEventListener('click', closeRoom);
        document.addEventListener('visibilitychange', function() {
            if (!active) return;
            if (document.hidden) {
                stopPolling(active);
                if (active.detailController) active.detailController.abort();
            } else refreshMembership(active);
        });
        document.addEventListener('keydown', function(event) {
            if (event.key !== 'Escape' || !view || !byId('ride-room-section').contains(document.activeElement)) return;
            event.preventDefault();
            if (!byId('room-close-confirm').hidden) { show('room-close-confirm', false); focus('room-close'); }
            else hide();
        });
    }

    window.AppModules.rideRooms = { init: init, openList: openList, openCreate: openCreate, invalidateSearchContext: invalidateSearchContext };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
    else init();
})();
