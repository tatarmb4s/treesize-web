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
  const pathSuggestions = document.getElementById('pathSuggestions');

  let sortKey = 'size';
  let sortOrder = 'desc';

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
      renderRows(data);
      statusEl.textContent = `${data.length} items`;
    }catch(e){ statusEl.textContent = 'Error'; setError(String(e)); }
    finally{ scanBtn.disabled = false; }
  }

  function renderRows(rows){
    const max = Math.max(1, ...rows.map(r => r.sizeBytes));
    tbody.innerHTML = rows.map(r => (
      `<tr>
        <td><input type="checkbox" data-path="${r.path}"></td>
        <td>${r.name}</td>
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
  }

  async function refreshSuggestions(path){
    const parent = path === '/' ? '/' : path.split('/').slice(0,-1).join('/') || '/';
    try{
      const res = await fetch(`/api/list-dir?path=${encodeURIComponent(parent)}`);
      if(!res.ok) return;
      const data = await res.json();
      pathSuggestions.innerHTML = data.entries.map(e => `<option value="${parent === '/' ? '/' + e.name : parent + '/' + e.name}">${e.name}</option>`).join('');
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
    baseEl.addEventListener('keydown', (e) => { if(e.key === 'Enter'){ scan(); } });
    baseEl.addEventListener('input', () => {
      refreshSuggestions(baseEl.value);
    });
    const initial = baseEl.value || '/';
    setPath(initial);
    const debugFlag = document.querySelector('meta[name="debug-ui"]');
    if (debugFlag) { loadDebug(); }
  }

  init();
})();


