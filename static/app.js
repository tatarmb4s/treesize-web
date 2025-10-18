(function(){
  const csrf = (function(){
    const m = document.cookie.match(/(?:^|; )ts_csrf=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  })();
  const baseEl = document.getElementById('base');
  const scanBtn = document.getElementById('scanBtn');
  const tbody = document.querySelector('#results tbody');
  const statusEl = document.getElementById('status');
  const errorBanner = document.getElementById('errorBanner');
  const debugPanel = document.getElementById('debugPanel');
  const breadcrumb = document.getElementById('breadcrumb');
  const suggestionsBox = document.getElementById('suggestions');
  const filterInput = document.getElementById('filterByName');
  const selectFilteredBtn = document.getElementById('selectFilteredBtn');
  const deselectAllBtn = document.getElementById('deselectAllBtn');
  const deleteSelectedBtn = document.getElementById('deleteSelectedBtn');
  let suggestionItems = [];
  let activeIndex = -1;

  let sortKey = 'size';
  let sortOrder = 'desc';
  let currentRows = [];
  const selectedPaths = new Set();

  function setError(message){
    if(!message){ errorBanner.style.display='none'; errorBanner.textContent=''; return; }
    errorBanner.textContent = message;
    errorBanner.style.display = 'block';
  }

  function formatBytes(n){
    const units = ['B','KB','MB','GB','TB','PB'];
    let i=0, v=n;
    while(v>=1024 && i<units.length-1){ v/=1024; i++; }
    return `${v.toFixed(1)} ${units[i]}`;
  }

  function setDebugPanelVisible(visible){
    debugPanel.style.display = visible ? 'block' : 'none';
  }

  async function loadDebug(){
    try{
      const res = await fetch('/api/csrf-debug', { headers: { 'X-CSRF-Token': csrf }});
      if(!res.ok) return;
      const data = await res.json();
      debugPanel.textContent = JSON.stringify({
        csrfCookie: data.cookieToken,
        headerRequired: data.headerRequired,
        authUser: data.user || '',
        cspMode: (document.currentScript && document.currentScript.nonce) ? 'nonce' : 'self/inline'
      }, null, 2);
      setDebugPanelVisible(true);
    }catch(_e){/* ignore */}
  }

  async function scan(){
    const basePath = baseEl.value.trim();
    if(!basePath){ setError('Enter a base path.'); return; }
    scanBtn.disabled = true;
    statusEl.textContent = 'Scanning…';
    tbody.innerHTML = '';
    setError('');
    try{
      const res = await fetch('/api/scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify({ basePath, sort: sortKey, order: sortOrder })
      });
      if(!res.ok){ const t = await res.text(); throw new Error(t); }
      const data = await res.json();
      currentRows = data;
      applyFilterAndRender();
      statusEl.textContent = `${data.length} items`;
    }catch(e){ statusEl.textContent = 'Error'; setError(String(e)); }
    finally{ scanBtn.disabled = false; }
  }

  function renderRows(rows){
    const max = Math.max(1, ...rows.map(r => r.sizeBytes));
    tbody.innerHTML = rows.map(r => (
      `<tr>
        <td><input type="checkbox" data-path="${r.path}" ${selectedPaths.has(r.path)?'checked':''}></td>
        <td class="name-cell"><span class="icon ${r.isDir?'folder':'file'}"></span><span class="name" data-path="${r.path}">${r.name}</span></td>
        <td>${r.isDir ? 'Folder' : 'File'}</td>
        <td>${formatBytes(r.sizeBytes)}</td>
        <td>${formatBytes(r.allocatedBytes)}</td>
        <td>${r.numFiles}</td>
        <td>${r.numDirs}</td>
        <td><div class="bar"><span style="width:${(r.sizeBytes/max*100).toFixed(2)}%"></span></div></td>
        <td>${r.modifiedIso}</td>
        <td><button data-action="delete" data-path="${r.path}">Delete</button></td>
      </tr>`
    )).join('');
    tbody.querySelectorAll('button[data-action=delete]').forEach(btn => {
      btn.addEventListener('click', () => del([btn.dataset.path]));
    });
    tbody.querySelectorAll('input[type=checkbox][data-path]').forEach(cb => {
      cb.addEventListener('change', () => {
        const p = cb.dataset.path;
        if(cb.checked){ selectedPaths.add(p); } else { selectedPaths.delete(p); }
      });
    });
    tbody.querySelectorAll('span.name').forEach(el => {
      el.addEventListener('dblclick', () => {
        const tr = el.closest('tr');
        const path = el.getAttribute('data-path');
        const row = rows.find(r => r.path === path);
        if(row && row.isDir){
          const next = path.endsWith('/') ? path : path + '/';
          setPath(next);
          scan();
        }
      });
    });
  }

  function getFilterText(){
    return (filterInput && filterInput.value || '').toLowerCase();
  }

  function applyFilterAndRender(){
    const f = getFilterText();
    const rendered = f ? currentRows.filter(r => r.name.toLowerCase().includes(f)) : currentRows.slice();
    renderRows(rendered);
  }

  async function del(paths){
    const basePath = baseEl.value.trim(); if(!basePath){ setError('Enter base path first'); return; }
    if(!confirm(`Delete ${paths.length} item(s)?`)) return;
    try{
      const res = await fetch('/api/delete', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify({ basePath, paths })
      });
      if(!res.ok){ const t = await res.text(); throw new Error(t); }
      await scan();
    }catch(e){ setError(String(e)); }
  }

  function buildCrumbs(path){
    breadcrumb.innerHTML = '';
    const parts = path.split('/');
    const accum = [];
    const rootBtn = document.createElement('span');
    rootBtn.className = 'crumb';
    rootBtn.innerHTML = `<span class="icon">📁</span><a href="#">/</a>`;
    rootBtn.addEventListener('click', (e)=>{ e.preventDefault(); setPath('/'); });
    breadcrumb.appendChild(rootBtn);
    for(const part of parts){
      if(!part) continue;
      accum.push(part);
      const full = '/' + accum.join('/');
      const span = document.createElement('span');
      span.className = 'crumb';
      span.innerHTML = `<span class="icon">📁</span><a href="#">${part}</a>`;
      span.addEventListener('click', (e)=>{ e.preventDefault(); setPath(full); });
      const sel = document.createElement('select');
      sel.className = 'crumb-select';
      sel.addEventListener('change', ()=>{ if(sel.value) setPath(sel.value); });
      populateSiblings(full, sel);
      span.appendChild(sel);
      breadcrumb.appendChild(span);
    }
  }

  async function populateSiblings(currentPath, selectEl){
    const parent = currentPath === '/' ? '/' : currentPath.split('/').slice(0,-1).join('/') || '/';
    try{
      const res = await fetch(`/api/list-dir?path=${encodeURIComponent(parent)}`);
      if(!res.ok) return;
      const data = await res.json();
      const dirs = data.entries.filter(e => e.type === 'dir');
      selectEl.innerHTML = '<option value="">…</option>' + dirs.map(d => `<option value="${parent === '/' ? '/' + d.name : parent + '/' + d.name}">${d.name}</option>`).join('');
    }catch(_e){/* ignore */}
  }

  async function validatePath(path){
    try{
      const res = await fetch(`/api/list-dir?path=${encodeURIComponent(path)}`);
      return res.ok;
    }catch(_e){ return false; }
  }

  function setPath(p){
    baseEl.value = p;
    buildCrumbs(p);
    refreshSuggestions(p);
    validatePath(p).then(ok => { baseEl.classList.toggle('invalid', !ok); });
    if(window.history && window.history.pushState){
      const url = new URL(window.location.href);
      url.searchParams.set('path', p);
      window.history.pushState({}, '', url.toString());
    }
  }

  function showSuggestions(items){
    if(!items || items.length === 0){
      suggestionsBox.style.display = 'none';
      suggestionsBox.innerHTML = '';
      suggestionItems = [];
      activeIndex = -1;
      return;
    }
    suggestionsBox.innerHTML = items.map(it => (
      `<div class="suggestion-item" data-path="${it.path}" data-type="${it.type}"><span class="icon ${it.type==='dir'?'folder':'file'}"></span><span>${it.path}</span></div>`
    )).join('');
    suggestionsBox.style.display = 'block';
    suggestionItems = Array.from(suggestionsBox.querySelectorAll('.suggestion-item'));
    suggestionItems.forEach((el, idx) => {
      el.addEventListener('mouseenter', ()=> setActiveIndex(idx));
      el.addEventListener('click', ()=> acceptActive(idx));
    });
  }

  function setActiveIndex(idx){
    if(activeIndex >= 0 && activeIndex < suggestionItems.length){
      suggestionItems[activeIndex].classList.remove('active');
    }
    activeIndex = idx;
    if(activeIndex >= 0 && activeIndex < suggestionItems.length){
      suggestionItems[activeIndex].classList.add('active');
      suggestionItems[activeIndex].scrollIntoView({ block: 'nearest' });
    }
  }

  function acceptActive(idx){
    if(idx < 0 || idx >= suggestionItems.length) return;
    const el = suggestionItems[idx];
    const p = el.getAttribute('data-path');
    const t = el.getAttribute('data-type');
    const isDir = t === 'dir';
    const pNext = isDir && p !== '/' ? (p.endsWith('/') ? p : p + '/') : p;
    setPath(pNext);
    baseEl.focus();
    if(isDir){
      refreshSuggestions(pNext);
    } else {
      showSuggestions([]);
    }
  }

  async function tryAcceptTyped(){
    const val = baseEl.value.trim();
    if(!val){ return false; }
    try{
      const res = await fetch(`/api/list-dir?path=${encodeURIComponent(val)}`);
      if(res.ok){
        const withSlash = val === '/' ? '/' : (val.endsWith('/') ? val : val + '/');
        setPath(withSlash);
        baseEl.focus();
        refreshSuggestions(withSlash);
        return true;
      }
    }catch(_e){ /* ignore */ }
    return false;
  }

  async function refreshSuggestions(path){
    const parent = path === '/' ? '/' : path.split('/').slice(0,-1).join('/') || '/';
    try{
      const res = await fetch(`/api/list-dir?path=${encodeURIComponent(parent)}`);
      if(!res.ok) return;
      const data = await res.json();
      const items = data.entries.map(e => ({
        path: parent === '/' ? '/' + e.name : parent + '/' + e.name,
        type: e.type
      }));
      // Filter by typed prefix
      const val = baseEl.value;
      const filtered = items.filter(it => it.path.startsWith(val));
      showSuggestions(filtered);
    }catch(_e){/* ignore */}
  }

  function onHeaderClick(e){
    const th = e.currentTarget;
    const key = th.dataset.key;
    if(sortKey === key){ sortOrder = (sortOrder === 'asc') ? 'desc' : 'asc'; } else { sortKey = key; sortOrder = 'desc'; }
    scan();
  }

  function init(){
    document.querySelectorAll('th.sortable').forEach(th => th.addEventListener('click', onHeaderClick));
    scanBtn.addEventListener('click', scan);
    if(filterInput){ filterInput.addEventListener('input', applyFilterAndRender); }
    if(selectFilteredBtn){
      selectFilteredBtn.addEventListener('click', () => {
        const f = getFilterText();
        const toSelect = f ? currentRows.filter(r => r.name.toLowerCase().includes(f)) : currentRows.slice();
        toSelect.forEach(r => selectedPaths.add(r.path));
        applyFilterAndRender();
      });
    }
    if(deselectAllBtn){
      deselectAllBtn.addEventListener('click', () => {
        selectedPaths.clear();
        applyFilterAndRender();
      });
    }
    if(deleteSelectedBtn){
      deleteSelectedBtn.addEventListener('click', () => {
        if(selectedPaths.size === 0){ setError('No items selected'); return; }
        del(Array.from(selectedPaths));
        selectedPaths.clear();
      });
    }
    baseEl.addEventListener('keydown', (e) => {
      if(e.key === 'ArrowDown'){
        e.preventDefault();
        setActiveIndex(Math.min(suggestionItems.length - 1, activeIndex + 1));
      } else if(e.key === 'ArrowUp'){
        e.preventDefault();
        setActiveIndex(Math.max(-1, activeIndex - 1));
      } else if(e.key === 'Enter'){
        if(e.ctrlKey){ e.preventDefault(); scan(); return; }
        e.preventDefault();
        if(activeIndex >= 0){
          acceptActive(activeIndex);
        } else {
          tryAcceptTyped().then(ok => { if(!ok) scan(); });
        }
      } else if(e.key === 'Escape'){
        showSuggestions([]);
      }
    });
    baseEl.addEventListener('input', () => { refreshSuggestions(baseEl.value); });
    baseEl.addEventListener('focus', () => { refreshSuggestions(baseEl.value); });
    document.addEventListener('click', (e) => {
      if(!suggestionsBox.contains(e.target) && e.target !== baseEl){
        showSuggestions([]);
      }
    });
    const url = new URL(window.location.href);
    const paramPath = url.searchParams.get('path');
    const initial = paramPath || baseEl.value || '/';
    setPath(initial);
    if(window.history && window.history.replaceState){
      const url = new URL(window.location.href);
      url.searchParams.set('path', initial);
      window.history.replaceState({}, '', url.toString());
    }
    const debugFlag = document.querySelector('meta[name="debug-ui"]');
    if (debugFlag) { loadDebug(); }
  }

  init();
})();


