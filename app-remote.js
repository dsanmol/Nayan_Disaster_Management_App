// Nayan web client: authentication, API hydration, role-aware controls, and persistent mutations.
(() => {
  const BHOPAL = { lat: 23.2599, lon: 77.4126 };
  const apiRoot = location.protocol === 'file:' ? 'http://127.0.0.1:8000' : '';
  let token = localStorage.getItem('nayan-token');
  let profile = null;
  let prior = null;
  let writes = Promise.resolve();
  let registering = false;
  let leafletMap = null;
  let routeLayer = null;
  const $ = (id) => document.getElementById(id);
  const copy = (x) => JSON.parse(JSON.stringify(x));
  function updateAnalyticsView() {
    const data = db._analytics || {}, total = data.total ?? db.incidents.length, active = data.active ?? db.incidents.filter((i) => !['Resolved', 'Cancelled'].includes(i.status)).length;
    const resolved = data.resolved ?? db.incidents.filter((i) => i.status === 'Resolved').length;
    const deployed = db.resources.filter((r) => r.status !== 'Available').length;
    $('analyticsMetrics').innerHTML = metric('Reports this period', total, 'tracked incident reports', '⌖') + metric('Average severity', data.average_severity ?? 0, 'weighted incident score', '◉') + metric('Resolution rate', `${Math.round(resolved / (total || 1) * 100)}%`, 'reports currently resolved', '✓') + metric('Units deployed', deployed, 'assigned or en route', '◷');
    $('donutTotal').textContent = total;
    const categories = Object.entries(data.by_type || {}).sort((a, b) => b[1] - a[1]).slice(0, 4), colors = ['#d84e4c', '#e5a142', '#5698b2', '#49a080'];
    let cursor = 0; const stops = categories.map(([, count], index) => { const start = cursor; cursor += total ? count / total * 100 : 0; return `${colors[index]} ${start}% ${cursor}%`; });
    if (stops.length) document.querySelector('.donut').style.background = `conic-gradient(${stops.join(',')})`;
    $('donutLegend').innerHTML = categories.map(([name, count], index) => `<div class="dlegend"><i style="background:${colors[index]}"></i>${esc(name)} <b style="color:#364451;margin-left:4px">${count}</b></div>`).join('');
    const daily = data.daily || [], max = Math.max(1, ...daily.map((point) => point.count));
    $('volumeChart').innerHTML = daily.map((point, index) => `<div class="bar-col"><span class="bar-val">${point.count}</span><div class="bar ${index === daily.length - 1 ? 'darkbar' : ''}" style="height:${Math.max(3, point.count / max * 90)}%"></div><span class="bar-label">${new Date(`${point.day}T12:00:00`).toLocaleDateString(undefined, { weekday: 'short' })}</span></div>`).join('');
    const critical = db.incidents.filter((i) => i.severity === 'Critical'), criticalAssigned = critical.filter((i) => i.unit && i.unit !== 'Unassigned').length;
    const capacity = db.shelters.reduce((sum, shelter) => sum + Number(shelter.capacity || 0), 0), occupied = db.shelters.reduce((sum, shelter) => sum + Number(shelter.occupied || 0), 0);
    const metrics = [['Critical incidents assigned', critical.length ? Math.round(criticalAssigned / critical.length * 100) : 0], ['Reports resolved', Math.round(resolved / (total || 1) * 100)], ['Shelter capacity in use', capacity ? Math.round(occupied / capacity * 100) : 0]];
    $('performance').innerHTML = metrics.map(([label, value]) => `<div style="margin-bottom:18px"><div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:7px"><span>${esc(label)}</span><b>${value}%</b></div><div class="progress"><i style="width:${value}%"></i></div></div>`).join('');
    $('readiness').innerHTML = db.resources.map((r) => `<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #eff2f4;font-size:11px"><span>${esc(r.type)}</span><span style="color:${r.status === 'Available' ? '#318b66' : '#88939f'}">${esc(r.name)} · ${esc(r.status)}</span></div>`).join('');
  }
  window.renderAnalytics = updateAnalyticsView;
  const api = async (path, options = {}) => {
    const headers = new Headers(options.headers || {});
    if (token) headers.set('Authorization', `Bearer ${token}`);
    if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
    const response = await fetch(`${apiRoot}${path}`, { ...options, headers });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const error = new Error(body.detail || `Request failed (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return response.status === 204 ? null : response.json();
  };
  const mapIncident = (i) => ({
    id: i.id, type: i.type, place: i.place, desc: i.description,
    people: i.people_affected, medical: i.medical_required, score: i.severity_score,
    severity: i.severity_level, status: i.status, unit: i.unit,
    time: new Date(i.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    x: 25 + ((Number(i.longitude) * 1000) % 55), y: 24 + ((Number(i.latitude) * 1000) % 51),
    latitude: i.latitude, longitude: i.longitude, desc: i.description
  });
  async function hydrate() {
    const [incidents, resources, shelters, alerts] = await Promise.all([
      api('/api/incidents'), api('/api/resources'), api('/api/shelters'), api('/api/alerts')
    ]);
    const missions = await api('/api/missions');
    let analytics = null;
    if (profile.role !== 'CITIZEN') {
      analytics = await api('/api/analytics');
      db._analytics = analytics;
    }
    db.incidents = incidents.map(mapIncident);
    db.resources = resources.map((r) => ({ ...r, skill: r.skills, icon: r.type === 'Ambulance' ? '✚' : r.type === 'Fire team' ? '♨' : '⚑' }));
    db.shelters = shelters;
    db.alerts = alerts.map((a) => ({ title: `${a.title}${a.area && a.area !== 'District-wide' ? ` · ${a.area}` : ''}`, text: a.message, level: a.severity, time: new Date(a.created_at).toLocaleString() }));
    db.missions = missions.map((m) => {
      const incident = db.incidents.find((item) => item.id === m.incident_id);
      const place = m.place || incident?.place || '';
      return { id: m.id, incident_id: m.incident_id, incident: `${m.incident_id} · ${m.incident_type || incident?.type || 'Incident'}${place ? ` · ${place}` : ''}`, place, unit: m.unit, assigned: new Date(m.assigned_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }), status: m.status };
    });
    localStorage.setItem('nayan-state', JSON.stringify(db));
    prior = copy(db);
    render();
    if (leafletMap) drawMarkers();
  }
  function configureRole() {
    document.body.classList.toggle('citizen-mode', profile.role === 'CITIZEN');
    if (!document.getElementById('roleViewStyles')) {
      const style = document.createElement('style');
      style.id = 'roleViewStyles';
      style.textContent = '.citizen-mode #incidentsTable .table-action,.citizen-mode #missionsTable .btn{display:none!important}.citizen-mode #view-missions .panel-title .live,.citizen-mode #view-missions th:last-child,.citizen-mode #missionsTable td:last-child{display:none!important}';
      document.head.append(style);
    }
    document.querySelector('.profile b').textContent = profile.name;
    document.querySelector('.profile span').textContent = profile.role[0] + profile.role.slice(1).toLowerCase();
    document.querySelectorAll('.nav-item').forEach((item) => {
      const v = item.dataset.view;
      item.classList.toggle('hidden', profile.role === 'CITIZEN' && ['resources', 'analytics'].includes(v));
      item.classList.toggle('hidden', profile.role === 'RESPONDER' && ['resources'].includes(v));
    });
    if (profile.role !== 'ADMIN') {
      document.querySelectorAll('[data-modal="resource"],[data-modal="shelter"],[data-modal="alert"],#assignBtn,#simulateBtn,#exportBtn').forEach((el) => el.classList.add('hidden'));
    }
    if (profile.role === 'CITIZEN') {
      document.querySelectorAll('#incidentsTable .table-action').forEach((el) => el.classList.add('hidden'));
      document.querySelectorAll('[data-view="alerts"],[data-view="shelters"]').forEach((el) => el.classList.remove('hidden'));
    }
    if (!window.nayanCitizenActionGuard) {
      const originalCycleIncident = window.cycleIncident;
      window.cycleIncident = (id) => {
        if (profile?.role === 'CITIZEN') return toast('Citizens can report incidents and track response status.');
        return originalCycleIncident?.(id);
      };
      window.nayanCitizenActionGuard = true;
    }
    if (!document.getElementById('logoutBtn')) {
      const b = document.createElement('button'); b.id = 'logoutBtn'; b.className = 'iconbtn'; b.textContent = 'Sign out';
      b.onclick = () => { localStorage.removeItem('nayan-token'); localStorage.removeItem('nayan-user'); location.reload(); };
      document.querySelector('.top-right').prepend(b);
    }
  }
  function showAuthError(message) { $('authError').textContent = message; }
  $('authSwitch').onclick = () => {
    registering = !registering;
    $('authName').classList.toggle('hidden', !registering);
    $('authName').required = registering;
    $('authTitle').textContent = registering ? 'Create your account' : 'Welcome back';
    $('authSubtitle').textContent = registering ? 'Citizen accounts can submit and track emergency reports.' : 'Sign in to your Nayan response workspace.';
    $('authSubmit').textContent = registering ? 'Create citizen account' : 'Sign in';
    $('authSwitchLabel').textContent = registering ? 'Already registered?' : 'New to Nayan?';
    $('authSwitch').textContent = registering ? 'Sign in' : 'Create citizen account';
    $('authPassword').autocomplete = registering ? 'new-password' : 'current-password';
    showAuthError('');
  };
  $('authForm').onsubmit = async (event) => {
    event.preventDefault(); showAuthError(''); $('authSubmit').disabled = true;
    try {
      const result = await api(registering ? '/api/auth/register' : '/api/auth/login', {
        method: 'POST', body: JSON.stringify(registering
          ? { name: $('authName').value, email: $('authEmail').value, password: $('authPassword').value }
          : { email: $('authEmail').value, password: $('authPassword').value })
      });
      token = result.access_token; profile = result.user;
      localStorage.setItem('nayan-token', token); localStorage.setItem('nayan-user', JSON.stringify(profile));
      await enterApp();
    } catch (error) { showAuthError(error.message); }
    finally { $('authSubmit').disabled = false; }
  };
  async function enterApp() {
    window.nayanRemote = { persist, role: profile.role };
    configureRole(); await hydrate(); $('authScreen').classList.add('hidden');
    installMap(); addRouteActions(); addPhotoControl(); startSimulationLoop(); useRecommendedDispatch();
    startSocket();
  }
  async function persistChanges() {
    if (!profile || !prior) return;
    const before = prior;
    // Persist additions and status transitions through role-checked API routes.
    for (const i of db.incidents) {
      const old = before.incidents.find((x) => x.id === i.id);
      if (!old) {
        const duplicates = await api('/api/incidents/check-duplicates', { method: 'POST', body: JSON.stringify({ type: i.type, description: i.desc, place: i.place, latitude: i.latitude || BHOPAL.lat, longitude: i.longitude || BHOPAL.lon, people_affected: Number(i.people) || 0, medical_required: !!i.medical }) }).catch(() => ({ possible_duplicates: [] }));
        if (duplicates.possible_duplicates?.length) toast(`Possible duplicate nearby: ${duplicates.possible_duplicates[0].incident_id} · review recommended`);
        await api('/api/incidents', { method: 'POST', body: JSON.stringify({ type: i.type, description: i.desc, place: i.place, latitude: i.latitude || BHOPAL.lat, longitude: i.longitude || BHOPAL.lon, people_affected: Number(i.people) || 0, medical_required: !!i.medical }) });
      } else if (old.status !== i.status && profile.role !== 'CITIZEN') {
        await api(`/api/incidents/${encodeURIComponent(i.id)}/status`, { method: 'PATCH', body: JSON.stringify({ status: i.status, note: 'Status updated from command interface' }) });
      }
    }
    if (profile.role === 'ADMIN') {
      for (const r of db.resources) {
        const old = before.resources.find((x) => x.name === r.name);
        if (!old) await api('/api/resources', { method: 'POST', body: JSON.stringify({ name: r.name, type: r.type, base: r.base, capacity: r.capacity, skills: r.skill || r.skills || '', status: r.status }) });
        else if (old.status !== r.status && r.id && !db.missions.some((m) => !before.missions.some((x) => x.id === m.id) && m.unit === r.name)) await api(`/api/resources/${r.id}`, { method: 'PATCH', body: JSON.stringify({ status: r.status }) });
      }
      for (const s of db.shelters) if (!before.shelters.some((x) => x.name === s.name)) await api('/api/shelters', { method: 'POST', body: JSON.stringify({ name: s.name, place: s.place, capacity: Number(s.capacity), occupied: Number(s.occupied) || 0 }) });
      for (const a of db.alerts) if (!before.alerts.some((x) => x.title === a.title)) await api('/api/alerts', { method: 'POST', body: JSON.stringify({ title: a.title, message: a.text, severity: a.level, area: a.title.split(' · ').slice(1).join(' · ') || 'District-wide' }) });
      for (const m of db.missions) {
        const old = before.missions.find((x) => x.id === m.id);
        if (!old) {
          const resource = db.resources.find((r) => r.name === m.unit), incidentId = m.incident_id || m.incident.split(' · ')[0];
          if (resource?.id) await api('/api/missions', { method: 'POST', body: JSON.stringify({ incident_id: incidentId, resource_id: resource.id }) });
        } else if (old.status !== m.status) {
          const target = m.status === 'Completed' ? 'Completed' : m.status;
          await api(`/api/missions/${encodeURIComponent(m.id)}/status`, { method: 'PATCH', body: JSON.stringify({ status: target }) });
        }
      }
    }
    await hydrate();
  }
  function persist() {
    writes = writes.then(persistChanges).catch((error) => { console.error('Nayan sync failed:', error); toast(`Could not sync changes: ${error.message}`); });
    return writes;
  }
  function startSocket() {
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    const address = location.protocol === 'file:' ? 'ws://127.0.0.1:8000/api/ws' : `${scheme}://${location.host}/api/ws`;
    const socket = new WebSocket(address);
    socket.onopen = () => socket.send(`Bearer ${token}`);
    socket.onmessage = () => hydrate().catch(console.error);
    socket.onclose = () => { if (token) setTimeout(startSocket, 3500); };
  }
  function installMap() {
    if (document.querySelector('script[data-leaflet]')) return;
    const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'; document.head.append(css);
    const script = document.createElement('script'); script.dataset.leaflet = 'true'; script.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
    script.onload = () => {
      const wrap = document.querySelector('.map-wrap');
      const target = document.createElement('div'); target.id = 'liveMap'; target.style.cssText = 'position:absolute;inset:0;z-index:2'; wrap.append(target);
      leafletMap = L.map(target, { scrollWheelZoom: false }).setView([BHOPAL.lat, BHOPAL.lon], 12);
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' }).addTo(leafletMap);
      leafletMap.on('click', (event) => {
        const form = $('dynamicForm');
        if (form?.querySelector('[name="latitude"]')) {
          form.querySelector('[name="latitude"]').value = event.latlng.lat.toFixed(6);
          form.querySelector('[name="longitude"]').value = event.latlng.lng.toFixed(6);
          toast('Report location selected on the map');
        }
      });
      document.querySelectorAll('.map-bg,.river,.road,.district,#markers,.map-caption').forEach((el) => el.classList.add('hidden'));
      drawMarkers(); setTimeout(() => leafletMap.invalidateSize(), 100);
    };
    document.head.append(script);
  }
  function drawMarkers() {
    if (!leafletMap) return;
    if (window.nayanMarkerLayer) window.nayanMarkerLayer.remove();
    window.nayanMarkerLayer = L.layerGroup().addTo(leafletMap);
    db.incidents.forEach((incident) => {
      const lat = Number(incident.latitude) || BHOPAL.lat, lon = Number(incident.longitude) || BHOPAL.lon;
      const color = incident.severity === 'Critical' ? '#d94d4d' : incident.severity === 'High' ? '#d8912e' : '#318b66';
      const marker = L.circleMarker([lat, lon], { radius: incident.severity === 'Critical' ? 9 : 7, color: '#fff', weight: 2, fillColor: color, fillOpacity: .95 }).addTo(window.nayanMarkerLayer);
      const popup = document.createElement('div'), title = document.createElement('b');
      title.textContent = incident.type; popup.append(title, document.createElement('br'), document.createTextNode(incident.place), document.createElement('br'), document.createTextNode(`${incident.severity} · ${incident.score}/100`), document.createElement('br'), document.createTextNode(incident.status));
      marker.bindPopup(popup);
    });
  }
  async function drawRoute(incidentId, resourceId) {
    try {
      const route = await api(`/api/routes/${encodeURIComponent(incidentId)}?resource_id=${resourceId}`);
      if (!leafletMap) { showView('overview'); toast(`${route.distance_km} km · ${route.duration_minutes} min · ${route.provider}`); return; }
      showView('overview');
      const coords = route.coordinates.map(([lon, lat]) => [lat, lon]);
      if (routeLayer) routeLayer.remove();
      routeLayer = L.polyline(coords, { color: '#137f79', weight: 5, opacity: .88 }).addTo(leafletMap);
      leafletMap.fitBounds(routeLayer.getBounds(), { padding: [25, 25] });
      toast(`${route.distance_km} km · ${route.duration_minutes} min · ${route.provider}`);
    } catch (error) { toast(error.message); }
  }
  function addRouteActions() {
    const table = $('missionsTable'); if (!table) return;
    const observer = new MutationObserver(() => {
      table.querySelectorAll('tr').forEach((row, index) => {
        const cell = row.lastElementChild;
        if (!cell || cell.querySelector('.route-btn')) return;
        const mission = db.missions[index], resource = mission && db.resources.find((r) => r.name === mission.unit);
        if (!mission || !resource) return;
        const button = document.createElement('button'); button.className = 'btn route-btn'; button.textContent = 'Route';
        button.onclick = () => drawRoute(mission.incident_id || mission.incident.split(' · ')[0], resource.id);
        cell.append(button);
      });
    });
    observer.observe(table, { childList: true, subtree: true });
  }
  function addPhotoControl() {
    document.addEventListener('click', (event) => {
      if (event.target.closest('[data-modal="incident"]')) setTimeout(() => {
        const form = $('dynamicForm');
        if (form && !form.querySelector('[name="image"]')) {
          const field = document.createElement('div'); field.className = 'field full';
          field.innerHTML = '<label>Attach photo (JPEG, PNG or WebP · max 5 MB)</label><input name="image" type="file" accept="image/jpeg,image/png,image/webp">'; form.append(field);
        }
        if (form && !form.querySelector('[name="latitude"]')) {
          const field = document.createElement('div'); field.className = 'field full';
          field.innerHTML = `<label>Map coordinates (Bhopal default; adjust for the exact location)</label><div style="display:flex;gap:8px"><input name="latitude" type="number" step="any" min="-90" max="90" value="${BHOPAL.lat}" placeholder="Latitude"><input name="longitude" type="number" step="any" min="-180" max="180" value="${BHOPAL.lon}" placeholder="Longitude"></div>`;
          form.append(field);
        }
        const description = form?.querySelector('[name="desc"]'), kind = form?.querySelector('[name="type"]');
        if (description && !description.dataset.classifierReady) {
          const note = document.createElement('div'); note.className = 'field full'; note.style.cssText = 'font-size:11px;color:#71808d';
          description.closest('.field').after(note); let timer;
          description.addEventListener('input', () => {
            clearTimeout(timer);
            if (description.value.trim().length < 5) { note.textContent = ''; return; }
            timer = setTimeout(async () => {
              try {
                const suggestion = await api('/api/intelligence/classify', { method: 'POST', body: JSON.stringify({ text: description.value }) });
                note.replaceChildren();
                if (suggestion.matched_signals) {
                  note.append(document.createTextNode(`AI suggested category: ${suggestion.suggested_type} (${Math.round(suggestion.confidence * 100)}% model confidence). `));
                  const choose = document.createElement('button'); choose.type = 'button'; choose.className = 'btn'; choose.textContent = 'Use suggestion';
                  choose.onclick = () => { if ([...kind.options].some((option) => option.text === suggestion.suggested_type)) kind.value = suggestion.suggested_type; };
                  note.append(choose);
                }
              } catch { note.textContent = ''; }
            }, 650);
          });
          description.dataset.classifierReady = 'true';
        }
        const saveButton = $('saveModal');
        if (saveButton && !saveButton.dataset.coordinateHook) {
          const original = saveButton.onclick;
          saveButton.onclick = (submitEvent) => {
            const latitude = Number(form.querySelector('[name="latitude"]')?.value), longitude = Number(form.querySelector('[name="longitude"]')?.value);
            original?.call(saveButton, submitEvent);
            if (db.incidents[0] && Number.isFinite(latitude) && Number.isFinite(longitude)) {
              db.incidents[0].latitude = latitude; db.incidents[0].longitude = longitude;
              if (leafletMap) { drawMarkers(); leafletMap.panTo([latitude, longitude]); }
            }
          };
          saveButton.dataset.coordinateHook = 'true';
        }
      }, 0);
      if (event.target.id === 'saveModal' && $('modalTitle')?.textContent?.includes('incident')) {
        const photo = $('dynamicForm')?.querySelector('[name="image"]')?.files?.[0];
        if (!photo) return;
        if (photo.size > 5_000_000) { event.preventDefault(); event.stopImmediatePropagation(); toast('Image must be 5 MB or smaller'); return; }
        setTimeout(async () => {
          try {
            await writes;
            const description = $('dynamicForm')?.querySelector('[name="desc"]')?.value;
            const incident = db.incidents.find((i) => i.desc === description);
            if (!incident) return;
            const body = new FormData(); body.append('image', photo);
            await api(`/api/incidents/${encodeURIComponent(incident.id)}/image`, { method: 'POST', body });
            toast('Incident photo uploaded');
          } catch (error) { toast(`Image upload failed: ${error.message}`); }
        }, 10);
      }
    });
  }
  function startSimulationLoop() {
    const button = $('simulateBtn'); if (!button || button.dataset.loopReady) return;
    const createEvent = button.onclick; let timer = null;
    button.onclick = (event) => {
      if (timer) { clearInterval(timer); timer = null; button.textContent = '▶  Run simulation'; toast('Disaster simulation stopped'); return; }
      createEvent?.call(button, event);
      button.textContent = '■  Stop simulation';
      timer = setInterval(() => createEvent?.call(button, new Event('click')), 10000);
    };
    button.dataset.loopReady = 'true';
  }
  function useRecommendedDispatch() {
    const button = $('assignBtn'); if (!button || button.dataset.recommendationReady) return;
    const original = button.onclick;
    button.onclick = async (event) => {
      const incident = sorted().find((i) => i.status === 'Reported');
      if (!incident) { toast('No unassigned incident found'); return; }
      try {
        const result = await api(`/api/incidents/${encodeURIComponent(incident.id)}/recommendations`);
        const best = result.recommendations[0];
        if (!best) { toast('No available response unit matches this incident'); return; }
        db.resources.sort((a, b) => Number(b.id === best.resource_id) - Number(a.id === best.resource_id));
        toast(`Recommended ${best.name} · ${best.distance_km} km · ${best.reason}`);
        original?.call(button, event);
      } catch (error) { toast(`Recommendation unavailable: ${error.message}`); }
    };
    button.dataset.recommendationReady = 'true';
  }
  async function boot() {
    try {
      await api('/api/health');
      const config = await api('/api/config');
      if (!config.demo_mode) document.querySelector('.demo-accounts')?.classList.add('hidden');
      const clock = $('clock');
      if (clock) clock.innerHTML = '● &nbsp;BHOPAL, MP';
      const overviewEyebrow = document.querySelector('#view-overview .eyebrow');
      if (overviewEyebrow) overviewEyebrow.textContent = `${new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })} · Bhopal district`;
      const mapSubheading = document.querySelector('#view-overview .panel-title small');
      if (mapSubheading) mapSubheading.textContent = 'Live operational picture · Bhopal, Madhya Pradesh';
      if (token) {
        profile = JSON.parse(localStorage.getItem('nayan-user') || 'null') || await api('/api/auth/me');
        await enterApp();
      }
    } catch (error) {
      if (error.status === 401 && token) {
        token = null;
        profile = null;
        localStorage.removeItem('nayan-token');
        localStorage.removeItem('nayan-user');
        showAuthError('Your session expired. Please sign in again.');
      } else {
        showAuthError(`API unavailable. Start Nayan using the instructions in README.md. (${error.message})`);
      }
    }
  }
  boot();
})();
