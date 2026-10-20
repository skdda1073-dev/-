/** Campus/origin selection and accessible destination suggestions. */
(function() {
    let searchVersion = 0;
    let searchController = null;
    let suggestions = [];
    let activeIndex = -1;
    let initialized = false;
    let composing = false;

    function setSearchStatus(message, failed) {
        const status = document.getElementById('search-status');
        if (status) {
            status.textContent = message || '';
            status.classList.toggle('status-error', Boolean(failed));
        }
    }

    function syncUI() {
        if (window.AppModules.ui) window.AppModules.ui.sync();
    }

    function resetRoutes() {
        if (window.AppModules.api) window.AppModules.api.cancelPendingRequests();
    }

    function closeSuggestions() {
        const list = document.getElementById('autocomplete-list');
        const input = document.getElementById('dest-input');
        suggestions = [];
        activeIndex = -1;
        if (list) {
            list.hidden = true;
            list.style.display = 'none';
            list.replaceChildren();
        }
        if (input) {
            input.setAttribute('aria-expanded', 'false');
            input.removeAttribute('aria-activedescendant');
        }
    }

    function cancelSearch() {
        const input = document.getElementById('dest-input');
        const status = document.getElementById('search-status');
        if (input && input.getAttribute('aria-busy') === 'true' && status && !status.classList.contains('status-error')) {
            setSearchStatus('');
        }
        searchVersion += 1;
        clearTimeout(searchTimer);
        if (searchController) searchController.abort();
        searchController = null;
        closeSuggestions();
        if (input) input.setAttribute('aria-busy', 'false');
    }

    function updateBuildingSelect(campus) {
        const select = document.getElementById('building-select');
        if (!select) return;
        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.textContent = '출발 건물을 선택하세요';
        select.replaceChildren(placeholder);
        const names = buildingData && Array.isArray(buildingData[campus]) ? buildingData[campus] : [];
        names.forEach(function(name) {
            if (typeof name !== 'string' || !name.trim()) return;
            const option = document.createElement('option');
            option.value = name;
            option.textContent = name;
            select.appendChild(option);
        });
        select.removeAttribute('aria-invalid');
    }

    function selectOriginMode(mode, options) {
        if (mode !== 'gps' && mode !== 'building') return;
        options = options || {};
        const changed = window.AppState.getOriginMode() !== mode;
        window.AppState.setOriginMode(mode);
        if (changed && options.reset !== false) resetRoutes();
        ['gps', 'building'].forEach(function(value) {
            const button = document.getElementById('origin-' + value);
            if (!button) return;
            button.classList.toggle('is-active', value === mode);
            button.setAttribute('aria-pressed', String(value === mode));
        });
        const field = document.getElementById('building-field');
        const select = document.getElementById('building-select');
        const status = document.getElementById('origin-status');
        if (field) field.hidden = mode !== 'building';
        if (status) {
            status.classList.remove('status-error');
            status.textContent = mode === 'building'
                ? (select && select.value ? select.value + '에서 출발해요.' : '학교 안 출발 건물을 골라주세요.')
                : (window.AppState.getCurrentPosition() ? '현재 위치에서 출발해요.' : '검색할 때 현재 위치를 확인해요.');
        }
        if (changed && mode === 'building' && select && options.focus !== false) select.focus();
        syncUI();
    }

    function switchCampus(campus) {
        if (campus !== 'hallym' && campus !== 'knu') return;
        const changed = selectedCampus !== campus;
        window.AppState.setSelectedCampus(campus);
        ['hallym', 'knu'].forEach(function(value) {
            const button = document.getElementById('tab-' + value);
            if (!button) return;
            button.classList.toggle('active', value === campus);
            button.classList.toggle('is-active', value === campus);
            button.setAttribute('aria-pressed', String(value === campus));
        });
        if (!changed) return;
        cancelSearch();
        resetRoutes();
        updateBuildingSelect(campus);
        selectOriginMode(window.AppState.getOriginMode(), { reset: false, focus: false });
        if (window.panToCampus) window.panToCampus(campus);
        const input = document.getElementById('dest-input');
        if (input && input.value.trim() && !destName) onSearchDestination(input.value);
        else setSearchStatus('');
    }

    function setActiveSuggestion(index) {
        const list = document.getElementById('autocomplete-list');
        const input = document.getElementById('dest-input');
        if (!list || !input || !suggestions.length) return;
        activeIndex = (index + suggestions.length) % suggestions.length;
        Array.from(list.children).forEach(function(option, optionIndex) {
            const active = optionIndex === activeIndex;
            option.classList.toggle('is-highlighted', active);
            option.setAttribute('aria-selected', String(active));
            if (active) {
                input.setAttribute('aria-activedescendant', option.id);
                if (typeof option.scrollIntoView === 'function') option.scrollIntoView({ block: 'nearest' });
            }
        });
    }

    function displaySuggestions(items, version) {
        const list = document.getElementById('autocomplete-list');
        const input = document.getElementById('dest-input');
        if (!list || !input) return;
        suggestions = items.filter(function(item) {
            return item && typeof item.name === 'string' && item.name.trim() &&
                window.AppState.isValidPosition(item.lat, item.lon);
        }).slice(0, 12);
        if (!suggestions.length) {
            closeSuggestions();
            setSearchStatus('검색 결과가 없습니다. 다른 이름으로 검색해 보세요.');
            return;
        }
        const fragment = document.createDocumentFragment();
        suggestions.forEach(function(item, index) {
            const option = document.createElement('button');
            option.type = 'button';
            option.className = 'autocomplete-item';
            option.id = 'destination-option-' + version + '-' + index;
            option.setAttribute('role', 'option');
            option.setAttribute('aria-selected', 'false');
            option.tabIndex = -1;
            const name = document.createElement('span');
            name.className = 'autocomplete-name';
            name.textContent = item.name;
            const sub = document.createElement('span');
            sub.className = 'autocomplete-sub';
            sub.textContent = typeof item.sub === 'string' ? item.sub : '';
            option.append(name, sub);
            option.addEventListener('pointerdown', function(event) { event.preventDefault(); });
            option.addEventListener('click', function() { selectDestination(item.name, item.lat, item.lon); });
            fragment.appendChild(option);
        });
        list.replaceChildren(fragment);
        list.hidden = false;
        list.style.display = 'block';
        input.setAttribute('aria-expanded', 'true');
        activeIndex = -1;
        const partial = items.some(function(item) { return item && item.search_warning; });
        setSearchStatus(partial
            ? '교내 장소부터 보여드려요. 일부 외부 장소를 불러오지 못했어요.'
            : '장소 ' + suggestions.length + '곳을 찾았어요. 목록에서 선택해 주세요.');
    }

    function onSearchDestination(keyword) {
        if (composing) return;
        cancelSearch();
        resetRoutes();
        const input = document.getElementById('dest-input');
        const query = typeof keyword === 'string' ? keyword.trim() : '';
        if (destName) {
            window.AppState.clearDestination();
            const tag = document.getElementById('dest-selected-tag');
            if (tag) { tag.hidden = true; tag.style.display = 'none'; }
            if (input) { input.hidden = false; input.style.display = ''; }
        }
        syncUI();
        if (!query) {
            setSearchStatus('');
            return;
        }
        if (query.length > 100) {
            setSearchStatus('목적지는 100자 이내로 입력해 주세요.', true);
            return;
        }
        const version = searchVersion;
        const campus = selectedCampus;
        setSearchStatus('장소를 찾고 있어요…');
        if (input) input.setAttribute('aria-busy', 'true');
        searchTimer = setTimeout(async function() {
            const controller = typeof AbortController === 'function' ? new AbortController() : null;
            searchController = controller;
            let timeout;
            const isCurrent = function() { return version === searchVersion && selectedCampus === campus; };
            try {
                const response = await Promise.race([
                    fetch('/search_poi?q=' + encodeURIComponent(query) + '&campus=' + encodeURIComponent(campus),
                        controller ? { signal: controller.signal } : {}),
                    new Promise(function(resolve, reject) {
                        timeout = setTimeout(function() {
                            reject(new Error('검색 응답이 늦어지고 있어요. 잠시 후 다시 입력해 주세요.'));
                            if (controller) controller.abort();
                        }, 15000);
                    })
                ]);
                const items = await response.json();
                if (!isCurrent()) return;
                if (!response.ok || !Array.isArray(items)) {
                    throw new Error(items && typeof items.error === 'string'
                        ? items.error : '장소 검색을 연결하지 못했어요. 잠시 후 다시 입력해 주세요.');
                }
                displaySuggestions(items, version);
            } catch (error) {
                if (!isCurrent() || error.name === 'AbortError') return;
                closeSuggestions();
                setSearchStatus(error instanceof TypeError || error instanceof SyntaxError
                    ? '장소 검색을 연결하지 못했어요. 잠시 후 다시 입력해 주세요.'
                    : error.message, true);
            } finally {
                clearTimeout(timeout);
                if (isCurrent()) {
                    searchController = null;
                    if (input) input.setAttribute('aria-busy', 'false');
                }
            }
        }, 250);
    }

    function selectDestination(name, lat, lon) {
        if (!window.AppState.setDestination(name, lat, lon)) {
            setSearchStatus('장소의 위치를 확인하지 못했어요. 다른 검색 결과를 선택해 주세요.', true);
            return;
        }
        cancelSearch();
        resetRoutes();
        const input = document.getElementById('dest-input');
        const tag = document.getElementById('dest-selected-tag');
        const text = document.getElementById('dest-selected-text');
        if (input) { input.value = ''; input.hidden = true; input.style.display = 'none'; }
        if (tag) { tag.hidden = false; tag.style.display = 'flex'; }
        if (text) text.textContent = destName;
        setSearchStatus('목적지를 선택했어요. 출발지를 확인하고 경로를 찾아보세요.');
        syncUI();
        const submit = document.getElementById('route-submit');
        if (submit) submit.focus({ preventScroll: true });
    }

    function clearDestination(options) {
        options = options || {};
        cancelSearch();
        window.AppState.clearDestination();
        resetRoutes();
        const input = document.getElementById('dest-input');
        const tag = document.getElementById('dest-selected-tag');
        const text = document.getElementById('dest-selected-text');
        if (tag) { tag.hidden = true; tag.style.display = 'none'; }
        if (text) text.textContent = '';
        if (input) { input.value = ''; input.hidden = false; input.style.display = ''; }
        setSearchStatus('');
        syncUI();
        if (input && options.focus !== false) input.focus();
    }

    function onDestinationKeyDown(event) {
        if (event.isComposing || composing) return;
        if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && suggestions.length) {
            event.preventDefault();
            const next = activeIndex === -1 ? (event.key === 'ArrowDown' ? 0 : suggestions.length - 1)
                : activeIndex + (event.key === 'ArrowDown' ? 1 : -1);
            setActiveSuggestion(next);
        } else if (event.key === 'Enter') {
            event.preventDefault();
            const item = suggestions[activeIndex < 0 ? 0 : activeIndex];
            if (item) selectDestination(item.name, item.lat, item.lon);
            else if (window.searchJourney) window.searchJourney();
        } else if (event.key === 'Escape' || event.key === 'Tab') {
            if (event.key === 'Escape') event.preventDefault();
            cancelSearch();
            setSearchStatus('');
        }
    }

    function init() {
        if (initialized) return;
        initialized = true;
        updateBuildingSelect(selectedCampus);
        selectOriginMode(originMode, { reset: false, focus: false });
        const list = document.getElementById('autocomplete-list');
        const input = document.getElementById('dest-input');
        if (list) { list.setAttribute('role', 'listbox'); list.setAttribute('aria-label', '목적지 검색 결과'); }
        closeSuggestions();
        if (input) {
            input.setAttribute('role', 'combobox');
            input.setAttribute('aria-autocomplete', 'list');
            input.setAttribute('aria-controls', 'autocomplete-list');
            input.setAttribute('aria-haspopup', 'listbox');
            input.setAttribute('aria-expanded', 'false');
            input.setAttribute('maxlength', '100');
            input.addEventListener('keydown', onDestinationKeyDown);
            input.addEventListener('compositionstart', function() { composing = true; cancelSearch(); resetRoutes(); });
            input.addEventListener('compositionend', function() { composing = false; onSearchDestination(input.value); });
            if (!input.hasAttribute('oninput')) input.addEventListener('input', function() { onSearchDestination(input.value); });
            document.addEventListener('click', function(event) {
                if (input !== event.target && list && !list.contains(event.target)) cancelSearch();
            });
        }
        const building = document.getElementById('building-select');
        if (building) building.addEventListener('change', function() {
            resetRoutes();
            building.removeAttribute('aria-invalid');
            selectOriginMode('building', { reset: false, focus: false });
        });
        ['hallym', 'knu'].forEach(function(campus) {
            const button = document.getElementById('tab-' + campus);
            if (button) button.addEventListener('keydown', function(event) {
                if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
                event.preventDefault();
                const other = campus === 'hallym' ? 'knu' : 'hallym';
                switchCampus(other);
                const next = document.getElementById('tab-' + other);
                if (next) next.focus();
            });
        });
    }

    window.AppModules.search = { init, switchCampus, updateBuildingSelect, selectOriginMode,
        onSearchDestination, selectDestination, clearDestination, cancelSearch, setSearchStatus };
    window.switchCampus = switchCampus;
    window.updateBuildingSelect = updateBuildingSelect;
    window.selectOriginMode = selectOriginMode;
    window.onSearchDestination = onSearchDestination;
    window.selectDestination = selectDestination;
    window.clearDestination = clearDestination;
})();

