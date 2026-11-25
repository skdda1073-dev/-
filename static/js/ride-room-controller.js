/** Shared ride rooms. Refresh the visible list or room, without polling taxi quotes. */
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
    let listStatus = 'active';
    let createContext = null;
    let meeting = null;
    let destination = null;
    const pickers = { meeting: {}, destination: {} };
    let listTimer = null;
    let listFingerprint = null;
    let listPageCount = 1;
    let listRooms = [];
    let createBusy = false;
    let createAttempt = null;
    let createContextInvalidated = false;
    const byId = function(id) { return document.getElementById(id); };
    const labels = { open: '모집 중', full: '정원 마감', closed: '방 종료', expired: '출발시각 지남' };
    const terminalReasons = {
        full: '정원이 모두 찼어요.',
        closed: '방장이 모집을 종료했어요.',
        expired: '출발 시각이 지나 모집이 끝났어요.'
    };
    const roomIdPattern = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
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

    function setText(id, value) {
        const target = byId(id);
        if (target && target.textContent !== (value || '')) target.textContent = value || '';
    }
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
        if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,3})?)?$/.test(value || '')) return null;
        const iso = value + (value.length === 16 ? ':00' : '') + '+09:00';
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
        if (method === 'GET') return fetchJSON('/api/ride-rooms' + path, { signal });
        await ensureSession();
        const submit = (token) => fetchJSON('/api/ride-rooms' + path, {
            method,
            signal,
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token },
            body: JSON.stringify(body || {})
        });
        const token = csrfToken;
        try { return await submit(token); }
        catch (error) {
            if (error.code !== 'CSRF_INVALID' && error.code !== 'SESSION_REQUIRED') throw error;
            if (csrfToken === token) csrfToken = null;
            await ensureSession();
            return submit(csrfToken);
        }
    }

    function isActive(room) {
        return active === room && view === 'detail' && !byId('transport-panel-rooms').hidden;
    }
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

    function cancelPicker(picker) {
        clearTimeout(picker.timer);
        picker.controller?.abort();
        picker.controller = null;
    }

    function setRoomURL(id) {
        const url = new URL(window.location.href);
        if (id) url.searchParams.set('room', id);
        else url.searchParams.delete('room');
        if (url.href !== window.location.href) window.history.replaceState(window.history.state, '', url);
    }

    function setView(next) {
        stopRoom();
        Object.values(pickers).forEach(cancelPicker);
        clearTimeout(listTimer);
        if (listController) listController.abort();
        listVersion += 1;
        listLoading = false;
        viewVersion += 1;
        view = next;
        if (next !== 'detail') setRoomURL(null);
        show('room-panel', Boolean(next));
        ['list', 'create', 'detail'].forEach(function(name) { show('room-' + name + '-view', name === next); });
        status('');
        window.AppModules.shell.refreshUnloadGuard();
    }

    async function openList(notice) {
        setView('list');
        focus('tool-heading');
        const generation = viewVersion;
        await loadList(false);
        if (notice && viewVersion === generation && !byId('room-status').classList.contains('room-status--error')) status(notice);
    }

    async function loadListPage(query, signal) {
        try {
            return await api('?' + query.toString(), 'GET', null, signal);
        } catch (error) {
            if (listStatus !== 'active' || error.code !== 'INVALID_STATUS') throw error;
            // A running Flask worker can still have the previous API while serving new static assets.
            listStatus = 'all';
            query.set('status', listStatus);
            return api('?' + query.toString(), 'GET', null, signal);
        }
    }

    async function loadList(more, background = false) {
        if (view !== 'list' || listLoading || byId('transport-panel-rooms').hidden) return;
        clearTimeout(listTimer);
        if (!more && !background) {
            listPageCount = 1;
            listFingerprint = null;
            listRooms = [];
            listCursor = null;
            show('room-load-more', false);
            byId('room-list').replaceChildren();
        }
        const pageSize = 20;
        const query = new URLSearchParams({ limit: String(pageSize), sort: 'newest', status: listStatus });
        const generation = viewVersion;
        const version = ++listVersion;
        const controller = new AbortController();
        listController = controller;
        listLoading = true;
        byId('room-load-more').disabled = true;
        if (!background) status('모집방을 확인하고 있어요…');
        try {
            await ensureSession();
            const rooms = new Map(more ? listRooms.map(room => [room.id, room]) : []);
            let cursor = more ? listCursor : null;
            const pages = more ? 1 : listPageCount;
            const expected = rooms.size + pages * pageSize;
            for (let page = 0; page < pages + 4; page += 1) {
                if (generation !== viewVersion || version !== listVersion) return;
                if (cursor) query.set('cursor', cursor);
                const data = await loadListPage(query, controller.signal);
                if (generation !== viewVersion || version !== listVersion) return;
                if (!Array.isArray(data.rooms)) throw new Error('모집방 목록을 확인하지 못했어요.');
                for (const item of data.rooms) {
                    const room = item.room || item;
                    if (room.status === 'open' || room.status === 'full') rooms.set(room.id, room);
                }
                cursor = data.next_cursor || null;
                if (!cursor || (page + 1 >= pages && (listStatus === 'active' || rooms.size >= expected))) break;
            }
            const updatedRooms = [...rooms.values()];
            const fingerprint = JSON.stringify([updatedRooms, cursor]);
            const notice = updatedRooms.length ? '' : '아직 모집방이 없어요.';
            if (background && listFingerprint === fingerprint) {
                if (byId('room-status').classList.contains('room-status--error')) status(notice);
                return;
            }
            const list = byId('room-list');
            const focusedId = list.contains(document.activeElement) ? document.activeElement.dataset.roomId : null;
            list.replaceChildren();
            updatedRooms.forEach(function(room) {
                const card = node('article', undefined, 'room-card');
                const header = node('header', undefined, 'room-card-header');
                const stateClass = room.status === 'open' ? 'room-state' : 'room-state room-state--inactive';
                header.append(node('h3', room.title), node('span', labels[room.status] || '상태 확인 중', stateClass));
                const meta = node('p', undefined, 'room-card-meta');
                const departure = node('time', kstDate(room.departure_at));
                departure.dateTime = room.departure_at;
                meta.append(departure, node('span', room.participant_count + '/' + room.capacity + '명'));
                card.append(header, node('p', room.meeting.name + ' → ' + room.destination.name), meta);
                const button = node('button', '방 보기', 'room-button room-button--secondary');
                button.type = 'button';
                button.setAttribute('aria-label', room.title + ' 방 보기');
                button.dataset.roomId = room.id;
                button.addEventListener('click', function() { openRoom(room.id); });
                card.appendChild(button);
                list.appendChild(card);
            });
            if (focusedId) Array.from(list.querySelectorAll('button')).find(button => button.dataset.roomId === focusedId)?.focus({ preventScroll: true });
            if (more) listPageCount += 1;
            listRooms = updatedRooms;
            listFingerprint = fingerprint;
            listCursor = cursor;
            show('room-load-more', Boolean(listCursor));
            status(notice);
        } catch (error) {
            if (generation === viewVersion && version === listVersion && error.name !== 'AbortError') status(error.message, true);
        } finally {
            if (generation === viewVersion && version === listVersion) {
                listLoading = false;
                byId('room-load-more').disabled = false;
                if (!document.hidden) listTimer = setTimeout(() => loadList(false, true), 5000);
            }
        }
    }

    function startCreate(place) {
        window.AppModules.shell.select('rooms', { roomAction: 'create', place });
    }

    function createRequirement() {
        if (createContext && (createContextInvalidated || !createContext.isCurrent())) {
            return '경로가 바뀌었어요. 새 목적지로 다시 열어 주세요.';
        }
        if (!destination) return '검색 결과에서 목적지를 선택해 주세요.';
        if (!meeting) return '모이는 곳을 선택해 주세요.';
        if (!byId('room-meeting-confirm').checked) return '모이는 곳 공개 안내에 체크해 주세요.';
        return '';
    }

    function updateCreateButton() {
        const requirement = createRequirement();
        byId('room-create-submit').disabled = createBusy || Boolean(requirement);
        setText('room-create-requirement', requirement);
        show('room-create-requirement', Boolean(requirement));
    }

    function setMeeting(point) {
        meeting = point;
        const confirm = byId('room-meeting-confirm');
        confirm.checked = false;
        confirm.disabled = !point;
        show('room-meeting-consent', Boolean(point));
        updateCreateButton();
    }

    function meetingCampusSelects() {
        return [byId('room-meeting-hallym'), byId('room-meeting-knu')];
    }

    function selectMeetingBuilding(event) {
        const select = event.currentTarget;
        const { placeName, lat, lon } = select.selectedOptions[0]?.dataset || {};
        const point = { name: placeName, lat: Number(lat), lon: Number(lon) };
        cancelPicker(pickers.meeting);
        byId('room-meeting-query').value = '';
        byId('room-meeting-options').replaceChildren();
        for (const other of meetingCampusSelects()) {
            if (other !== select) other.value = '';
        }
        const valid = validPlace(point);
        setMeeting(valid ? point : null);
        setText('room-meeting-status', select.value && !valid ? '이 건물의 위치를 확인하지 못했어요.' : '');
    }

    function invalidateSearchContext() {
        if (view !== 'create' || !createContext) return;
        createContextInvalidated = true;
        updateCreateButton();
    }

    async function openCreate(context, place) {
        if (context && !context.isCurrent()) return;
        setView('create');
        if (window.matchMedia('(max-width: 799px)').matches) window.AppModules.workspaceSheet?.reveal('full');
        createContext = context || null;
        const selected = context ? { name: context.payload.dest_name, lat: context.payload.dest_lat, lon: context.payload.dest_lon }
            : (place || window.AppState.getDestination());
        destination = validPlace(selected) ? { name: selected.name, lat: Number(selected.lat), lon: Number(selected.lon) } : null;
        createContextInvalidated = false;
        createBusy = false;
        createAttempt = null;
        byId('room-create-form').reset();
        byId('room-meeting-options').replaceChildren();
        setMeeting(null);
        byId('room-departure').value = localInput(new Date(Date.now() + 30 * 60000));
        setText('room-meeting-status', '');
        byId('room-destination-query').value = destination?.name || selected?.name || '';
        byId('room-destination-query').readOnly = Boolean(context);
        byId('room-destination-options').replaceChildren();
        setText('room-destination-status', destination ? '선택한 목적지 · ' + destination.name : '검색 결과에서 목적지를 선택해 주세요.');
        if (!destination && byId('room-destination-query').value) searchPlace('destination');
        updateCreateButton();
        focus('room-title');
        const generation = viewVersion;
        try { await ensureSession(); }
        catch (error) { if (generation === viewVersion) status(error.message, true); }
    }

    function searchPlace(kind) {
        const picker = pickers[kind];
        cancelPicker(picker);
        const prefix = 'room-' + kind + '-';
        const input = byId(prefix + 'query');
        const options = byId(prefix + 'options');
        options.replaceChildren();
        if (kind === 'meeting') {
            for (const select of meetingCampusSelects()) select.value = '';
            setMeeting(null);
        } else {
            destination = null;
            updateCreateButton();
        }
        const query = input.value.trim();
        setText(prefix + 'status', query ? '장소 검색 중…' : '');
        if (!query) return;
        const controller = new AbortController();
        picker.controller = controller;
        picker.timer = setTimeout(async function() {
            const timeout = setTimeout(() => controller.abort(), 15000);
            try {
                const params = new URLSearchParams({ q: query, campus: window.AppState.getSelectedCampus(), scope: 'national' });
                const response = await fetch('/search_poi?' + params, {
                    credentials: 'same-origin', signal: controller.signal, cache: 'no-store'
                });
                const items = await response.json();
                if (picker.controller !== controller) return;
                if (!response.ok || !Array.isArray(items)) throw new Error(items?.error || '장소 검색에 실패했어요.');
                const places = items.filter(validPlace).slice(0, 12);
                for (const place of places) {
                    const button = node('button', undefined, 'room-place-option');
                    button.type = 'button';
                    button.append(node('span', place.name), node('small', typeof place.sub === 'string' ? place.sub : ''));
                    button.addEventListener('click', function() {
                        if (picker.controller !== controller) return;
                        const point = { name: place.name.trim(), lat: Number(place.lat), lon: Number(place.lon) };
                        input.value = point.name;
                        options.replaceChildren();
                        if (kind === 'meeting') {
                            setMeeting(point);
                            focus('room-meeting-confirm');
                        } else {
                            destination = point;
                            updateCreateButton();
                            focus('room-meeting-query');
                        }
                        setText(prefix + 'status', kind === 'destination' ? '선택한 목적지 · ' + point.name : '');
                    });
                    options.appendChild(button);
                }
                setText(prefix + 'status', places.length ? '' : '검색 결과가 없어요.');
            } catch (error) {
                if (picker.controller === controller) setText(prefix + 'status', '장소 검색에 실패했어요. 다시 검색해 주세요.');
            } finally {
                clearTimeout(timeout);
            }
        }, 250);
    }

    async function submitCreate(event) {
        event.preventDefault();
        if (createBusy) return;
        const requirement = createRequirement();
        if (requirement) { status(requirement, true); return; }
        const departure = isoKST(byId('room-departure').value);
        if (!departure) { status('한국 시간으로 출발시각을 입력해 주세요.', true); return; }
        const payload = { title: byId('room-title').value.trim(), nickname: byId('room-create-nickname').value.trim(),
            meeting: { ...meeting }, destination: { ...destination },
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
            if (!roomIdPattern.test(data.room?.id) || !data.self?.joined) {
                throw new Error('저장한 방을 확인하지 못했어요. 다시 시도해 주세요.');
            }
            const room = await openRoom(data.room.id, data);
            if (room && isActive(room)) status('방을 만들었어요.');
        } catch (error) {
            if (generation === viewVersion) status(error.message, true);
        } finally {
            if (view === 'create' && generation === viewVersion) {
                createBusy = false;
                updateCreateButton();
            }
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

    function showRoomUnavailable(current) {
        if (!isActive(current)) return;
        stopRoom();
        setRoomURL(null);
        for (const field of ['route', 'time', 'state', 'count']) setText('room-detail-' + field, '');
        setText('room-detail-title', '모집방을 찾을 수 없어요.');
        setText('room-visitor-note', '삭제되거나 출발 시각이 지나 정리된 모집방일 수 있어요.');
        byId('room-members').replaceChildren();
        byId('room-messages').replaceChildren();
        byId('room-message-body').value = '';
        byId('room-share-url').value = '';
        for (const id of ['room-member-content', 'room-join-form', 'room-share-link', 'room-share-field']) show(id, false);
        status('');
        focus('room-detail-title');
        window.AppModules.shell.refreshUnloadGuard();
    }

    function renderRoom(current) {
        if (!isActive(current) || !current.room) return;
        const room = current.room;
        const joined = Boolean(current.self && current.self.joined);
        const ended = room.status === 'closed' || room.status === 'expired';
        show('room-share-link', true);
        setText('room-detail-title', room.title);
        setText('room-detail-route', room.meeting.name + ' → ' + room.destination.name);
        setText('room-detail-time', '출발 ' + kstDate(room.departure_at));
        setText('room-detail-state', labels[room.status] || '상태 확인 중');
        byId('room-detail-state').classList.toggle('room-state--inactive', ended);
        setText('room-detail-count', '현재 ' + room.participant_count + '명 / 정원 ' + room.capacity + '명 · 방장 포함');
        byId('room-members').replaceChildren(...(joined ? current.participants : []).map(function(member) {
            return node('li', member.nickname + (member.is_host ? ' · 방장' : '') + (current.self.member_id === member.member_id ? ' · 나' : ''));
        }));
        show('room-join-form', !joined && room.status === 'open');
        show('room-member-content', joined);
        const visitorNote = room.status === 'open' ? '대화와 방의 견적은 참가한 사람에게만 보여요.' : terminalReasons[room.status];
        setText('room-visitor-note', !joined || ended ? visitorNote : '');
        show('room-leave', joined && !current.self.is_host);
        show('room-close', joined && current.self.is_host && room.status !== 'closed');
        const canDelete = joined && current.self.can_delete === true;
        show('room-delete', canDelete);
        if (!canDelete) show('room-delete-confirm', false);
        show('room-message-form', joined && canChat(current));
        if (!canChat(current)) {
            setText('room-chat-status', joined ? '읽기 전용이에요. 새 참가와 메시지 전송이 중단됐어요.' : '참가 후 대화를 볼 수 있어요.');
            stopPolling(current);
            scheduleRoomRefresh(current);
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

    async function copyRoomLink() {
        const current = active;
        if (!current || !current.room || !isActive(current)) return;
        const link = new URL('/', window.location.origin);
        if (['tonghak.com', 'www.tonghak.com'].includes(link.hostname)) {
            link.protocol = 'https:';
            link.hostname = 'tonghak.com';
            link.port = '';
        }
        link.searchParams.set('room', current.id);
        try {
            await navigator.clipboard.writeText(link.href);
            if (isActive(current)) status('공유 링크를 복사했어요.');
        } catch (error) {
            if (!isActive(current)) return;
            show('room-share-field', true);
            const input = byId('room-share-url');
            input.value = link.href;
            input.focus();
            input.select();
            status('아래에 표시된 링크를 복사해 주세요.');
        }
    }

    async function openRoom(id, initial) {
        setView('detail');
        setRoomURL(id);
        if (window.matchMedia('(max-width: 799px)').matches) window.AppModules.workspaceSheet?.reveal('full');
        const current = { id: id, room: null, self: null, participants: [], seen: new Set(), lastId: null,
            pollGeneration: 0, pollInFlight: false, failures: 0, quote: null, quoteBusy: false, actionBusy: false,
            messageAttempt: null, detailController: new AbortController() };
        active = current;
        for (const field of ['title', 'route', 'time', 'state', 'count']) setText('room-detail-' + field, '');
        setText('room-visitor-note', '');
        byId('room-members').replaceChildren();
        byId('room-messages').replaceChildren();
        byId('room-join-nickname').value = '';
        byId('room-message-body').value = '';
        for (const button of ['join-submit', 'leave', 'estimate-refresh', 'close-yes', 'delete-yes', 'message-send']) {
            const control = byId('room-' + button);
            if (control) control.disabled = false;
        }
        byId('room-estimate-content').replaceChildren(node('p', '방의 현재 견적을 준비하고 있어요.'));
        show('room-share-link', false);
        show('room-share-field', false);
        show('room-member-content', false);
        show('room-join-form', false);
        show('room-close-confirm', false);
        show('room-delete-confirm', false);
        status('방을 확인하고 있어요…');
        try {
            await ensureSession();
            if (!isActive(current)) return;
            const data = initial || await api('/' + encodeURIComponent(id), 'GET', null, current.detailController.signal);
            if (!isActive(current)) return;
            acceptMetadata(current, data);
            status('');
            focus('room-detail-title');
            if (current.self && current.self.joined) {
                loadMessages(current);
                loadQuote(current);
            }
            return current;
        } catch (error) {
            if (isActive(current) && error.name !== 'AbortError') {
                if (error.status === 404) showRoomUnavailable(current);
                else status(error.message, true);
            }
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
                : current.quoteError || (quote ? '견적이 만료됐어요. 갱신해 주세요. 예상 분담금은 계산할 수 없어요.' : '견적이 없어요. 갱신해서 예상 분담금을 확인해 주세요.')));
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
        current.quoteError = null;
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
                if (error.status === 404) showRoomUnavailable(current);
                else {
                    current.quote = null;
                    current.quoteError = '방의 견적을 확인하지 못했어요. ' + error.message;
                }
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
            if (error.status === 404) { showRoomUnavailable(current); return; }
            current.failures += 1;
            nextDelay = Math.min(30000, 3000 * Math.pow(2, Math.min(current.failures, 4)));
            setText('room-chat-status', error.message + ' 잠시 후 다시 확인해요.');
            if (error.status === 403) {
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
        } catch (error) {
            if (isActive(current)) {
                if (error.status === 404) showRoomUnavailable(current);
                else {
                    status(error.message, true);
                    if (error.status === 409) refreshMembership(current);
                }
            }
        }
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
            if (isActive(current)) {
                if (error.status === 404) showRoomUnavailable(current);
                else {
                    status(error.message + ' 다시 보내도 같은 메시지는 한 번만 저장돼요.', true);
                    if (error.status === 409) refreshMembership(current);
                }
            }
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
            const data = await api('/' + encodeURIComponent(current.id) + '/leave', 'POST', {});
            if (!isActive(current)) return;
            current.quote = null;
            byId('room-messages').replaceChildren();
            const room = await openRoom(current.id, data);
            if (room && isActive(room)) status('방에서 나왔어요. 이후 대화는 볼 수 없어요.');
        } catch (error) {
            if (isActive(current)) {
                if (error.status === 404) showRoomUnavailable(current);
                else { status(error.message, true); schedulePoll(current, 3000); }
            }
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
        } catch (error) {
            if (isActive(current)) {
                if (error.status === 404) showRoomUnavailable(current);
                else status(error.message, true);
            }
        }
        finally { current.actionBusy = false; if (isActive(current)) byId('room-close-yes').disabled = false; }
    }

    async function deleteRoom() {
        const current = active;
        const confirm = byId('room-delete-yes');
        if (!current || current.actionBusy || current.self?.can_delete !== true || !confirm) return;
        current.actionBusy = true;
        confirm.disabled = true;
        stopPolling(current);
        if (current.detailController) current.detailController.abort();
        try {
            const data = await api('/' + encodeURIComponent(current.id), 'DELETE', {});
            if (!isActive(current)) return;
            if (data.deleted !== true) throw new Error('모집방 삭제를 확인하지 못했어요. 다시 시도해 주세요.');
            byId('room-message-body').value = '';
            await openList('모집방을 삭제했어요.');
        } catch (error) {
            if (isActive(current)) {
                if (error.status === 404) showRoomUnavailable(current);
                else {
                    status(error.message, true);
                    if (canChat(current)) schedulePoll(current, 3000);
                    else scheduleRoomRefresh(current);
                }
            }
        } finally {
            current.actionBusy = false;
            if (isActive(current)) confirm.disabled = false;
        }
    }

    function scheduleRoomRefresh(current) {
        clearTimeout(current.pollTimer);
        if (!isActive(current) || document.hidden || !current.room) return;
        const interval = ['closed', 'expired'].includes(current.room.status) ? 10000 : 5000;
        current.pollTimer = setTimeout(() => refreshMembership(current, true), interval);
    }

    async function refreshMembership(current, statusOnly = false) {
        if (!isActive(current) || document.hidden) return;
        clearTimeout(current.pollTimer);
        if (current.detailController) current.detailController.abort();
        const controller = new AbortController();
        current.detailController = controller;
        const generation = current.pollGeneration;
        try {
            const data = await api('/' + encodeURIComponent(current.id), 'GET', null, controller.signal);
            if (!isActive(current) || generation !== current.pollGeneration || document.hidden || controller.signal.aborted) return;
            acceptMetadata(current, data);
            if (current.self?.joined && (canChat(current) || !statusOnly)) loadMessages(current);
        } catch (error) {
            if (isActive(current) && error.name !== 'AbortError') {
                if (error.status === 404) showRoomUnavailable(current);
                else {
                    if (current.self?.joined) setText('room-chat-status', error.message + ' 잠시 후 참가 상태를 다시 확인해요.');
                    else status(error.message, true);
                    current.pollTimer = setTimeout(function() { refreshMembership(current, statusOnly); }, 6000);
                }
            }
        }
    }

    function init() {
        if (initialized) return;
        initialized = true;
        const shell = window.AppModules.shell;
        shell.onMenuSelect(function(name, options) {
            if (name !== 'rooms') {
                if (view) setView(null);
            } else if (options.roomAction === 'create') {
                openCreate(options.context, options.place);
            } else if (roomIdPattern.test(options.roomId)) {
                openRoom(options.roomId);
            } else {
                openList();
            }
        });
        shell.setDirtyProvider(() => (view === 'create' && Boolean(byId('room-title').value || byId('room-create-nickname').value
            || meeting || byId('room-meeting-query').value || (byId('room-destination-query').value && !destination)))
            || (view === 'detail' && Boolean(byId('room-message-body').value)));
        byId('taxi-new-room').addEventListener('click', () => startCreate());
        byId('taxi-browse-rooms').addEventListener('click', () => shell.select('rooms'));
        byId('room-create-current').addEventListener('click', () => openCreate());
        byId('room-load-more').addEventListener('click', function() { loadList(true); });
        byId('room-create-form').addEventListener('submit', submitCreate);
        byId('room-create-cancel').addEventListener('click', function() { openList(); });
        for (const kind of Object.keys(pickers)) byId('room-' + kind + '-query').addEventListener('input', () => searchPlace(kind));
        byId('room-meeting-confirm').addEventListener('change', updateCreateButton);
        for (const select of meetingCampusSelects()) select.addEventListener('change', selectMeetingBuilding);
        byId('room-detail-back').addEventListener('click', function() { openList(); });
        byId('room-share-link').addEventListener('click', copyRoomLink);
        byId('room-join-form').addEventListener('submit', joinRoom);
        byId('room-message-form').addEventListener('submit', sendMessage);
        byId('room-estimate-refresh').addEventListener('click', function() { if (active) loadQuote(active); });
        byId('room-leave').addEventListener('click', leaveRoom);
        for (const [action, onConfirm, other] of [['close', closeRoom, 'delete'], ['delete', deleteRoom, 'close']]) {
            const button = 'room-' + action;
            byId(button)?.addEventListener('click', function() {
                show('room-' + other + '-confirm', false);
                show(button + '-confirm', true);
                focus(button + '-yes');
            });
            byId(button + '-no')?.addEventListener('click', function() { show(button + '-confirm', false); focus(button); });
            byId(button + '-yes')?.addEventListener('click', onConfirm);
        }
        document.addEventListener('visibilitychange', function() {
            clearTimeout(listTimer);
            if (view === 'list' && !document.hidden) loadList(false, true);
            if (!active) return;
            if (document.hidden) {
                stopPolling(active);
                if (active.detailController) active.detailController.abort();
            } else refreshMembership(active);
        });
        document.addEventListener('keydown', function(event) {
            if (event.key !== 'Escape' || !view || !byId('ride-room-section').contains(document.activeElement)) return;
            event.preventDefault();
            if (byId('room-delete-confirm')?.hidden === false) { show('room-delete-confirm', false); focus('room-delete'); }
            else if (!byId('room-close-confirm').hidden) { show('room-close-confirm', false); focus('room-close'); }
            else if (view !== 'list') openList();
            else { setView(null); shell.closeTool(); }
        });
        const linkedRoom = new URL(window.location.href).searchParams.get('room');
        if (roomIdPattern.test(linkedRoom)) shell.select('rooms', { roomId: linkedRoom.toLowerCase() });
    }

    window.AppModules.rideRooms = { init: init, startCreate, invalidateSearchContext,
        openList: () => window.AppModules.shell.select('rooms'),
        openCreate: context => window.AppModules.shell.select('rooms', { roomAction: 'create', context }) };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
    else init();
})();
