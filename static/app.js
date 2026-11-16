'use strict';
const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );
const fmt = (n, digits = 1) =>
  n == null ? '—' : Number(n).toLocaleString('zh-TW', { maximumFractionDigits: digits });
const stamp = (n) => new Date(n * 1000).toLocaleString('zh-TW', { hour12: false });
const statusNames = {
  proposed: '待接受',
  accepted: '已接受・在途',
  arrived: '已到達',
  cancelled: '已取消',
  expired: '已逾時',
  invalidated: '資料變更・需重規劃',
  left: '已離開',
  pending: '待確認',
  avoiding: '暫時避開',
  confirmed: '確認封閉',
  resolved: '已解除',
  open: '演練開設',
  closed: '關閉',
  unknown: '未知',
};
let data = null,
  currentPlan = null,
  online = true,
  me = { guest: true },
  authMode = 'login',
  page = 'public',
  guestPromise = null,
  toastTimer;
let token = sessionStorage.getItem('anxing.token');
let scope = sessionStorage.getItem('anxing.scope');
if (!scope) {
  scope = crypto.randomUUID();
  sessionStorage.setItem('anxing.scope', scope);
}
function toast(message) {
  $('toast').textContent = message;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($('toast').textContent = ''), 6000);
}
function message(id, text, error = false) {
  $(id).innerHTML = `<div class="notice${error ? ' error' : ''}">${esc(text)}</div>`;
}
async function api(path, options = {}) {
  let response;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30000);
  try {
    response = await fetch(path, {
      ...options,
      signal: controller.signal,
      headers: {
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...options.headers,
      },
    });
  } catch (error) {
    const e = new Error(
      error.name === 'AbortError' ? '連線逾時，請確認後端狀態。' : '無法連線後端。',
    );
    e.status = 0;
    throw e;
  } finally {
    clearTimeout(timeout);
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body.detail;
    const e = new Error(
      typeof detail === 'string' ? detail : detail?.message || `HTTP ${response.status}`,
    );
    e.status = response.status;
    e.fields = detail?.fields;
    e.reasons = detail?.reasons;
    throw e;
  }
  return body;
}
async function ensureSession() {
  if (token) return;
  if (!guestPromise)
    guestPromise = api('/api/guest', { method: 'POST' })
      .then((result) => {
        token = result.token;
        sessionStorage.setItem('anxing.token', token);
      })
      .finally(() => (guestPromise = null));
  return guestPromise;
}
function needs() {
  return {
    mode: $('mode').value,
    can_stairs: !$('no-stairs').checked,
    step_free: $('no-stairs').checked,
    accessible_toilet: $('toilet').checked,
    speed_mps: $('speed').value ? Number($('speed').value) : null,
    max_slope: Number($('slope').value),
    min_width: Number($('width').value),
    rest_minutes: Number($('rest').value),
    confirmed: $('confirmed').checked,
  };
}
function setNeeds(n) {
  if (!n) {
    $('confirmed').checked = false;
    return;
  }
  for (const [id, key] of [
    ['mode', 'mode'],
    ['speed', 'speed_mps'],
    ['slope', 'max_slope'],
    ['width', 'min_width'],
    ['rest', 'rest_minutes'],
  ])
    $(id).value = n[key] ?? '';
  $('no-stairs').checked = n.step_free || !n.can_stairs;
  $('toilet').checked = n.accessible_toilet;
  $('confirmed').checked = false;
  modeChanged(false);
}
function modeChanged(force = true) {
  const mode = $('mode').value;
  const special = ['aid', 'wheelchair', 'assistance'].includes(mode);
  if (special && force) {
    $('no-stairs').checked = true;
    if (Number($('accessible-count').value) === 0) $('accessible-count').value = 1;
  }
  const speeds = {
    walking: 1.2,
    slow: 0.7,
    aid: 0.6,
    wheelchair: 0.8,
    assistance: 0.5,
    unsure: 0.7,
  };
  $('speed-hint').textContent =
    `預設速度 ${speeds[mode]} m/s，僅為估算假設。${special ? '無障礙模式預設開啟，仍需確認全組限制。' : ''}`;
}
$('mode').addEventListener('change', () => modeChanged());
function showPage(name) {
  page = name;
  document.querySelectorAll('main>section').forEach((e) => (e.hidden = e.id !== `page-${name}`));
  document.querySelectorAll('[data-page]').forEach((e) => {
    e.classList.toggle('active', e.dataset.page === name);
    e.setAttribute('aria-current', e.dataset.page === name ? 'page' : 'false');
  });
  if (name === 'public') window.AnxingPublic?.show();
  if (name === 'analysis') loadAnalysis();
  if (name === 'account') renderAccount();
}
document
  .querySelectorAll('[data-page]')
  .forEach((e) => e.addEventListener('click', () => showPage(e.dataset.page)));
function project(n) {
  return [((n.lon - 121.5308) / 0.012) * 720 + 35, 415 - ((n.lat - 25.0193) / 0.01) * 350];
}
function drawMap() {
  if (!data) return;
  const nodes = Object.fromEntries(data.nodes.map((n) => [n.id, n]));
  let svg =
    '<svg viewBox="0 0 800 470" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><defs><pattern id="blockpattern" width="20" height="20" patternUnits="userSpaceOnUse"><path d="M0 20 20 0" stroke="#e0e6d7" stroke-width=".6"/></pattern></defs><rect width="800" height="470" fill="#edf1e6"/><rect x="500" y="80" width="200" height="310" rx="60" fill="#dce7cf"/><rect x="83" y="78" width="140" height="75" rx="12" fill="url(#blockpattern)"/><rect x="250" y="270" width="175" height="100" rx="12" fill="#e2e9d7"/><text x="560" y="110" fill="#809974">演練綠地</text>';
  for (const e of data.edges) {
    const a = project(nodes[e.a]),
      b = project(nodes[e.b]);
    const coords = `x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}"`;
    svg += `<line class="road-outline" ${coords}/><line class="road" ${coords}/>`;
    if (data.blocked_edges.includes(e.id)) svg += `<line class="blocked" ${coords}/>`;
    else if (e.stairs) svg += `<line class="stairs" ${coords}/>`;
  }
  const active =
    currentPlan && ['proposed', 'accepted', 'arrived', 'offline'].includes(currentPlan.status);
  if (active) {
    const points = currentPlan.route.node_ids.map((id) => project(nodes[id]).join(',')).join(' ');
    svg += `<polyline class="route" points="${points}"/>`;
  }
  for (const n of data.nodes) {
    const [x, y] = project(n);
    svg += `<circle class="node" cx="${x}" cy="${y}" r="3"/>`;
  }
  for (const s of data.shelters) {
    const [x, y] = project(nodes[s.node_id]);
    const opened = s.status === 'open' && s.disaster === 'earthquake' && !s.temporarily_avoided;
    svg += `<g><circle class="shelter${opened ? '' : ' closed'}" cx="${x}" cy="${y}" r="14"/><text x="${x}" y="${y + 4}" text-anchor="middle">${esc(s.id.replace('s', ''))}</text><rect x="${x - 36}" y="${y + 21}" width="72" height="19" rx="4" fill="#ffffffdf"/><text x="${x}" y="${y + 34}" text-anchor="middle">${esc(s.id === 'sF' ? '防空・排除' : `演練場所 ${s.id[1]}`)}</text></g>`;
  }
  const origin = nodes[$('origin').value];
  if (origin) {
    const [x, y] = project(origin);
    svg += `<circle cx="${x}" cy="${y}" r="16" fill="#327d9a" opacity=".15"/><circle cx="${x}" cy="${y}" r="7" fill="#327d9a" stroke="white" stroke-width="3"/><text x="${x + 12}" y="${y - 12}">起點</text>`;
  }
  svg += '</svg>';
  $('map').innerHTML =
    svg +
    '<div class="mapbadge">DA’AN · SIMULATED NETWORK</div><div class="north">↑<br>N</div><div class="map-note">地理座標僅供路網演算法演示；路線不是現地道路，場所不是已查核的避難處所。</div>';
}
function renderData() {
  const open = data.shelters.filter(
    (s) => s.status === 'open' && s.disaster === 'earthquake' && !s.temporarily_avoided,
  );
  const capacity = open.reduce((n, s) => n + (s.capacity || 0), 0);
  const load = open.reduce((n, s) => n + s.estimated_load, 0);
  $('metrics').innerHTML = [
    ['演練開設場所', open.length, '處', '僅列震災適用'],
    ['標示總容量', capacity, '人', '合成資料・非即時空位'],
    ['演練配置與背景負載', load, '人', '含在途及已到達'],
    ['暫時避開路段', data.blocked_edges.length, '段', '含未核實高嚴重度回報'],
  ]
    .map(
      ([label, value, unit, foot]) =>
        `<div class="metric"><label>${label}</label><div class="value">${fmt(value, 0)}<small>${unit}</small></div><div class="foot">${foot}</div></div>`,
    )
    .join('');
  $('map-version').textContent = `資料 v${data.revision}`;
  if (!$('origin').options.length) {
    $('origin').innerHTML = data.nodes
      .map((n) => `<option value="${esc(n.id)}">${esc(n.name)}</option>`)
      .join('');
    $('origin').value = 'n22';
  }
  const target = $('report-target').value;
  $('report-target').innerHTML =
    '<optgroup label="受影響路段">' +
    data.edges
      .map(
        (e) =>
          `<option value="edge:${esc(e.id)}">${esc(e.id)}${e.stairs ? '（樓梯）' : ''}</option>`,
      )
      .join('') +
    '</optgroup><optgroup label="受影響場所">' +
    data.shelters
      .map((s) => `<option value="shelter:${esc(s.id)}">${esc(s.name)}</option>`)
      .join('') +
    '</optgroup>';
  if (target) $('report-target').value = target;
  $('shelter-list').innerHTML = data.shelters
    .map(
      (s) =>
        `<article class="panel shelter-card"><span class="pill ${s.status === 'open' ? '' : 'gray'}">${esc(statusNames[s.status])}${s.temporarily_avoided ? '・災情避開' : ''}</span><h3>${esc(s.name)}</h3><p class="hint">${s.disaster === 'earthquake' ? '震災演練場所' : '防空設施・不列入震災導航'} · 入口 ${esc(s.node_id)}</p><div class="capacity">${fmt(s.estimated_load, 0)} <small>/ ${fmt(s.capacity, 0)} 人（演練）</small></div><p class="hint">無障礙適用容量 ${fmt(s.accessible_capacity, 0)} 人 · 入口無階梯：${s.entrance_step_free === true ? '是' : s.entrance_step_free === false ? '否' : '未知'}<br>無障礙廁所：${s.accessible_toilet === true ? '是' : s.accessible_toilet === false ? '否' : '未知'}<br>資訊時間：${stamp(s.updated_at)}</p></article>`,
    )
    .join('');
  if (page !== 'public')
    $('updated-at').textContent = `示意資料 ${data.version} · 取得時間 ${stamp(data.generated_at)}`;
  drawMap();
}
function setConnection(ok) {
  online = ok;
  $('connection').textContent = ok ? '後端已連線' : '離線參考模式';
  $('connection-dot').style.background = ok ? '#b2d393' : '#ddac62';
  $('network-notice').hidden = ok;
  if (!ok)
    $('network-notice').textContent =
      `後端連線中斷。${data ? '顯示最近預存的示意資料，時間：' + stamp(data.generated_at) + '。' : ''}離線路徑僅供參考，暫停雲端容量協調，不能確認最新道路或場所狀態。`;
  $('plan-button').textContent = ok ? '尋找可達場所 →' : '計算離線參考路徑 →';
}
async function refresh() {
  try {
    data = await api('/api/map');
    setConnection(true);
    renderData();
    const savedOnlineId = currentPlan?.id || sessionStorage.getItem('anxing.plan');
    if (savedOnlineId) {
      try {
        currentPlan = await api(`/api/plans/${savedOnlineId}`);
        renderPlan();
      } catch (e) {
        if (e.status === 401 || e.status === 404) {
          currentPlan = null;
          sessionStorage.removeItem('anxing.plan');
          $('route-result').innerHTML = '';
        } else throw e;
      }
    }
  } catch (e) {
    if (e.status === 0) {
      const cached = localStorage.getItem('anxing.map');
      if (!data && cached) {
        try {
          data = JSON.parse(cached);
        } catch {
          localStorage.removeItem('anxing.map');
        }
      }
      setConnection(false);
      if (data) renderData();
      else message('plan-message', '無法連線且尚未預存資料。請啟動後端後重新整理。', true);
    } else message('plan-message', e.message, true);
  }
}
$('origin').addEventListener('change', drawMap);
function renderPlan() {
  const p = currentPlan;
  if (!p) return;
  const route = p.route;
  const canReplan = ['proposed', 'accepted', 'invalidated', 'expired'].includes(p.status);
  const inFlight = ['proposed', 'accepted'].includes(p.status);
  const alert =
    p.status === 'invalidated'
      ? '資料已更新，原配置已釋放。請重新規劃，勿沿用此路線。'
      : p.status === 'expired'
        ? '配置逾時已釋放，請重新規劃。'
        : p.status === 'offline'
          ? '離線參考路徑，未配置容量；不確認現場安全與開設狀態。'
          : !p.route_current && inFlight
            ? '資料版本已更新；請重新規劃，確認最新替代選項。'
            : p.message;
  $('route-result').innerHTML =
    `<div class="panel"><div class="panelhead"><h2>${esc(route.shelter_name)}</h2><span class="pill ${inFlight ? '' : 'amber'}">${esc(statusNames[p.status] || '離線參考')}</span></div><div class="panelbody"><div class="route-header"><div class="route-number">${fmt(route.minutes)} <small>分鐘・估計</small></div><span class="pill gray">${fmt(p.group_size, 0)} 人整組同行</span></div><div class="route-grid"><div><label>沿路網距離</label><strong>${fmt(route.distance_m, 0)} m</strong></div><div><label>相對可行最短時間</label><strong>+ ${fmt(route.extra_minutes)} 分</strong></div><div><label>採用速度</label><strong>${fmt(route.speed_mps)} m/s</strong></div></div><div class="route-steps">${route.node_ids.map((n) => esc(data.nodes.find((x) => x.id === n)?.name || n)).join(' → ')}</div><p class="hint">${route.speed_assumed ? '使用模式預設速度，非個人實測。' : '使用本次指定速度。'}${p.expires_at ? ' 配置期限 ' + stamp(p.expires_at) : ''}</p><div class="warning">${esc(alert)}</div><div class="actions">${p.status === 'proposed' ? '<button class="btn primary" data-action="accept">接受路線</button>' : ''}${p.status === 'accepted' ? '<button class="btn primary" data-action="arrive">已到達場所</button>' : ''}${p.status === 'arrived' ? '<button class="btn primary" data-action="leave">已離開場所</button>' : ''}${inFlight ? '<button class="btn" data-action="cancel">取消配置</button>' : ''}${canReplan ? '<button class="btn" data-replan>重新規劃</button>' : ''}</div></div></div>`;
  document
    .querySelectorAll('[data-action]')
    .forEach((e) => e.addEventListener('click', () => transition(e.dataset.action)));
  document.querySelector('[data-replan]')?.addEventListener('click', () => {
    $('plan-form').requestSubmit();
  });
  if (p.id) sessionStorage.setItem('anxing.plan', p.id);
  drawMap();
}
async function transition(action) {
  try {
    currentPlan = await api(`/api/plans/${currentPlan.id}/transition`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    });
    renderPlan();
    await refresh();
    toast(statusNames[currentPlan.status]);
  } catch (e) {
    message('plan-message', e.message, true);
  }
}
function offlinePlan(body) {
  if (!data) throw new Error('沒有已預存的路網。');
  if (!body.shaking_stopped || !body.evacuation_needed || !body.needs.confirmed)
    throw new Error('請完成出發前確認。');
  const n = body.needs;
  if (n.mode === 'unsure') throw new Error('請選擇明確行進模式。');
  if (n.mode === 'assistance' && !body.helper_present) throw new Error('需確認協助者同行。');
  if (body.accessible_count > body.group_size) throw new Error('無障礙人數不可超過同行人數。');
  if (['aid', 'wheelchair', 'assistance'].includes(n.mode) || body.accessible_count > 0) {
    n.step_free = true;
    n.can_stairs = false;
  }
  const speed =
    n.speed_mps || { walking: 1.2, slow: 0.7, aid: 0.6, wheelchair: 0.8, assistance: 0.5 }[n.mode];
  const dist = { [body.origin]: 0 },
    prev = {},
    pending = new Set(data.nodes.map((x) => x.id));
  while (pending.size) {
    let u = null;
    for (const id of pending)
      if (u === null || (dist[id] ?? Infinity) < (dist[u] ?? Infinity)) u = id;
    if (!Number.isFinite(dist[u])) break;
    pending.delete(u);
    for (const e of data.edges) {
      if (data.blocked_edges.includes(e.id) || (!n.can_stairs && e.stairs)) continue;
      if (
        n.step_free &&
        (e.stairs ||
          e.slope === null ||
          e.width === null ||
          e.surface_ok !== true ||
          e.slope > n.max_slope ||
          e.width < n.min_width)
      )
        continue;
      const v = e.a === u ? e.b : e.b === u ? e.a : null;
      if (!v || !pending.has(v)) continue;
      const d = dist[u] + e.length;
      if (d < (dist[v] ?? Infinity)) {
        dist[v] = d;
        prev[v] = [u, e.id];
      }
    }
  }
  const choices = data.shelters
    .filter(
      (s) =>
        s.disaster === 'earthquake' &&
        s.status === 'open' &&
        !s.temporarily_avoided &&
        Number.isFinite(dist[s.node_id]) &&
        (!n.step_free || (s.entrance_step_free === true && s.space_accessible === true)) &&
        (!n.accessible_toilet || s.accessible_toilet === true),
    )
    .sort((a, b) => dist[a.node_id] - dist[b.node_id] || a.id.localeCompare(b.id));
  if (!choices.length)
    throw new Error('預存資料中無法確認可達場所，不放寬必要條件。急難請聯絡119。');
  const s = choices[0];
  let u = s.node_id,
    ids = [u],
    edges = [];
  while (u !== body.origin) {
    const [p, e] = prev[u];
    edges.unshift(e);
    ids.unshift(p);
    u = p;
  }
  return {
    status: 'offline',
    group_size: body.group_size,
    route: {
      shelter_name: s.name,
      shelter_id: s.id,
      node_ids: ids,
      edge_ids: edges,
      distance_m: dist[s.node_id],
      minutes: dist[s.node_id] / speed / 60 + n.rest_minutes,
      speed_mps: speed,
      speed_assumed: !n.speed_mps,
      extra_minutes: 0,
    },
  };
}
$('plan-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = $('plan-button');
  button.disabled = true;
  $('plan-message').innerHTML = '';
  const body = {
    origin: $('origin').value,
    group_size: Number($('group-size').value),
    accessible_count: Number($('accessible-count').value),
    helper_present: $('helper').checked,
    needs: needs(),
    shaking_stopped: $('stopped').checked,
    evacuation_needed: $('evacuate').checked,
    max_extra_minutes: Number($('extra').value),
    replace_id:
      currentPlan && ['proposed', 'accepted', 'invalidated', 'expired'].includes(currentPlan.status)
        ? currentPlan.id
        : null,
  };
  try {
    if (!online) {
      currentPlan = offlinePlan(body);
      renderPlan();
      return;
    }
    await ensureSession();
    const signature = JSON.stringify(body);
    const saved = JSON.parse(sessionStorage.getItem('anxing.pending-plan') || 'null');
    body.request_id = saved?.signature === signature ? saved.id : crypto.randomUUID();
    sessionStorage.setItem(
      'anxing.pending-plan',
      JSON.stringify({ signature, id: body.request_id }),
    );
    currentPlan = await api('/api/plans', { method: 'POST', body: JSON.stringify(body) });
    sessionStorage.removeItem('anxing.pending-plan');
    renderPlan();
    await refresh();
    toast('已建立整組短期演練配置');
  } catch (e) {
    message(
      'plan-message',
      e.message + (e.fields ? '：' + e.fields.map((x) => x.message).join('；') : ''),
      true,
    );
    if (e.reasons) {
      const detail = document.createElement('div');
      detail.className = 'hint';
      detail.textContent = e.reasons.map((x) => x.shelter_id + '：' + x.reason).join('；');
      $('plan-message').append(detail);
    }
    if (e.status === 0) setConnection(false);
  } finally {
    button.disabled = false;
  }
});
function localTime() {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}
$('report-time').value = localTime();
function queue() {
  try {
    return JSON.parse(localStorage.getItem('anxing.queue') || '[]').filter(
      (x) => x.scope === scope,
    );
  } catch {
    return [];
  }
}
function saveQueue(items) {
  const all = JSON.parse(localStorage.getItem('anxing.queue') || '[]').filter(
    (x) => x.scope !== scope,
  );
  localStorage.setItem('anxing.queue', JSON.stringify([...all, ...items]));
  updateQueue();
}
function updateQueue() {
  $('queue-count').textContent = `${queue().length} 筆待上傳`;
}
$('report-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const [type, id] = $('report-target').value.split(':');
  const body = {
    request_id: crypto.randomUUID(),
    edge_id: type === 'edge' ? id : null,
    shelter_id: type === 'shelter' ? id : null,
    kind: $('report-kind').value,
    observed_at: new Date($('report-time').value).getTime() / 1000,
    description: $('report-description').value,
  };
  try {
    const photo = $('report-photo').files[0];
    if (photo && photo.size > 2 * 1024 * 1024) throw new Error('照片不可超過2MB。');
    if (!online) {
      if (photo) throw new Error('離線暫存不包含照片，請先移除照片後暫存文字回報。');
      if (!$('queue-consent').checked) throw new Error('離線暫存需勾選同意。');
      if (queue().length >= 20) throw new Error('暫存上限20筆，請先上傳或清除。');
      saveQueue([...queue(), { scope, body }]);
      message('report-message', '已暫存本機，尚未上傳。復網後請按上傳。');
      return;
    }
    await ensureSession();
    const result = await api('/api/reports', { method: 'POST', body: JSON.stringify(body) });
    if (photo) {
      await api(`/api/reports/${result.id}/photo`, {
        method: 'PUT',
        body: photo,
        headers: { 'Content-Type': photo.type },
      });
      $('report-photo').value = '';
    }
    message('report-message', result.message + (photo ? ' 照片已儲存並移除中繼資料。' : ''));
    $('report-description').value = '';
    await refresh();
  } catch (e) {
    message('report-message', e.message, true);
  }
});
$('sync-queue').addEventListener('click', async () => {
  const items = queue();
  if (!items.length) {
    toast('沒有待上傳回報');
    return;
  }
  let done = 0;
  try {
    await ensureSession();
    for (const item of items) {
      await api('/api/reports', { method: 'POST', body: JSON.stringify(item.body) });
      done++;
      saveQueue(items.slice(done));
    }
    message('report-message', `已上傳 ${done} 筆回報。`);
    await refresh();
  } catch (e) {
    message('report-message', `已上傳 ${done} 筆；其餘保留本機。${e.message}`, true);
  }
});
$('clear-queue').addEventListener('click', () => {
  saveQueue([]);
  toast('已清除此分頁的離線暫存回報');
});
$('save-offline').addEventListener('click', async () => {
  try {
    if (!data) throw new Error('尚未取得可預存資料。');
    localStorage.setItem('anxing.map', JSON.stringify(data));
    if ('serviceWorker' in navigator) {
      await navigator.serviceWorker.ready;
      await fetch('/');
    }
    toast('已預存示意圖。離線時不會配置容量或確認最新狀態。');
  } catch (e) {
    toast(e.message);
  }
});
$('clear-offline').addEventListener('click', async () => {
  localStorage.removeItem('anxing.map');
  localStorage.removeItem('anxing.profile');
  for (const key of await caches.keys()) if (key.startsWith('anxing-')) await caches.delete(key);
  toast('已清除離線圖與需求');
});
document.querySelectorAll('[data-refresh]').forEach((e) => e.addEventListener('click', refresh));
async function loadEvents() {
  try {
    const result = await api('/api/events');
    $('event-list').innerHTML = result.items.length
      ? result.items
          .map(
            (e) =>
              `<div class="event-title">演練：${esc(e.title)}</div><p class="hint">M ${fmt(e.magnitude)} · 發震 ${stamp(e.occurred_at)} · ${esc(e.source)} · v${e.version}</p>`,
          )
          .join('')
      : '尚無演練事件。官方預警接收及 iPhone 推播未接入。';
  } catch {
    /* Offline copy already visible. */
  }
}
async function loadAnalysis() {
  try {
    const [result, reach] = await Promise.all([
      api('/api/analysis'),
      api(
        '/api/accessibility?origin=' +
          encodeURIComponent($('origin').value || 'n22') +
          '&mode=' +
          encodeURIComponent($('mode').value) +
          '&step_free=' +
          $('no-stairs').checked,
      ),
    ]);
    const rows = result.areas;
    const stat = [
      ['示意總人口', rows.reduce((a, r) => a + r.population, 0)],
      ['情境需求', rows.reduce((a, r) => a + r.demand, 0)],
      ['成功安置', rows.reduce((a, r) => a + r.assigned, 0)],
      ['未安置需求', rows.reduce((a, r) => a + r.unassigned, 0)],
    ];
    $('analysis-content').innerHTML =
      `<div class="metrics result">${stat.map(([label, value]) => `<div class="metric"><label>${label}</label><div class="value">${fmt(value, 0)}<small>人</small></div></div>`).join('')}</div><div class="panel result"><div class="panelhead"><h2>5 / 10 / 15 分鐘可達路網</h2><small>依導航頁目前起點與行進模式</small></div><div class="map" id="reach-map"></div><div class="panelbody hint">${esc(reach.notice)} 採用速度 ${reach.speed_mps} m/s。</div></div><div class="analysis-layout result"><div class="panel"><div class="panelhead"><h2>人口密度示意</h2><small>人 / km²</small></div><div class="map" id="density-map"></div></div><div class="panel"><div class="panelhead"><h2>未安置需求示意</h2><small>人</small></div><div class="map" id="gap-map"></div></div></div><div class="panel result"><div class="panelhead"><h2>需求區明細與步行可達性</h2></div><div class="tablewrap"><table><thead><tr><th>示意需求區</th><th>人口</th><th>密度 人/km²</th><th>高齡比例</th><th>最近步行分鐘</th><th>5 / 10 / 15 分鐘</th><th>需求 / 安置 / 缺口</th></tr></thead><tbody>${rows.map((r) => `<tr><td>${esc(r.name)}</td><td>${fmt(r.population, 0)}</td><td>${fmt(r.density, 0)}</td><td>${fmt(r.elderly_ratio * 100)}%</td><td>${fmt(r.nearest_minutes)}</td><td>${[5, 10, 15].map((t) => (r.nearest_minutes != null && r.nearest_minutes <= t ? '✓' : '—')).join(' / ')}</td><td>${r.demand} / ${r.assigned} / ${r.unassigned}</td></tr>`).join('')}</tbody></table></div></div><p class="hint result">資料版本 ${esc(result.version)}。${esc(result.allocation_order)}。分析不扣除線上測試配置，只使用場所情境容量與背景負載。這些區塊不是臺北市真實里界，不能解讀為實際供給缺口。</p>`;
    drawReach(reach);
    for (const [id, key, unit] of [
      ['density-map', 'density', '人/km²'],
      ['gap-map', 'unassigned', '人'],
    ]) {
      const max = Math.max(...rows.map((r) => r[key]), 1);
      let svg = '<svg viewBox="0 0 480 420" xmlns="http://www.w3.org/2000/svg">';
      rows.forEach((r, i) => {
        const x = 35 + (i % 3) * 140,
          y = 30 + Math.floor(i / 3) * 122;
        const c = Math.round(235 - (r[key] / max) * 115);
        svg += `<rect x="${x}" y="${y}" width="132" height="112" rx="6" fill="rgb(${Math.round(c * 0.85)},${c},${Math.round(c * 0.87)})"/><text x="${x + 66}" y="${y + 40}" text-anchor="middle">${esc(r.name)}</text><text x="${x + 66}" y="${y + 70}" text-anchor="middle">${fmt(r[key], 0)} ${unit}</text>`;
      });
      svg += '<text x="240" y="410" text-anchor="middle">合成區塊示意・非真實里界</text></svg>';
      $(id).innerHTML = svg;
    }
  } catch (e) {
    $('analysis-content').innerHTML = `<div class="notice error">${esc(e.message)}</div>`;
  }
}
$('refresh-analysis').addEventListener('click', loadAnalysis);
$('experiment-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('run-experiment').disabled = true;
  $('experiment-message').textContent = '計算中…';
  const params = {
    seed: Number($('sim-seed').value),
    repeats: Number($('sim-repeats').value),
    population: Number($('sim-population').value),
    daytime_multiplier: Number($('sim-daytime').value),
    evacuation_rate: Number($('sim-evacuation').value) / 100,
    app_rate: Number($('sim-app').value) / 100,
    acceptance_rate: Number($('sim-acceptance').value) / 100,
    accessibility_rate: Number($('sim-accessibility').value) / 100,
    blocked_rate: Number($('sim-blocked').value) / 100,
    false_report_rate: Number($('sim-false').value) / 100,
    max_extra_minutes: Number($('sim-extra').value),
  };
  try {
    await ensureSession();
    const result = await api('/api/experiments', { method: 'POST', body: JSON.stringify(params) });
    renderExperiment(result);
    message('experiment-message', `${params.repeats} 個種子 × 3 策略已完成，結果與參數已儲存。`);
  } catch (e) {
    message('experiment-message', e.message, true);
  } finally {
    $('run-experiment').disabled = false;
  }
});
function renderExperiment(result) {
  const labels = { A: '最近場所', B: '容量分流', C: '容量＋災情' };
  const range = (s, key, percent = false) =>
    `${fmt(s[key].mean == null ? null : s[key].mean * (percent ? 100 : 1))}${percent ? '%' : ''}<br><small>${fmt(s[key].min == null ? null : s[key].min * (percent ? 100 : 1))}–${fmt(s[key].max == null ? null : s[key].max * (percent ? 100 : 1))}</small>`;
  $('experiment-result').innerHTML =
    `<div class="panel"><div class="panelhead"><div><h2>分流策略比較</h2><small>顯示平均；下方為多種子最小–最大範圍</small></div><a class="btn small" href="/api/experiments/${esc(result.id)}/csv" download>↓ 匯出 CSV</a></div><div class="tablewrap"><table><thead><tr><th>策略</th><th>未安置率</th><th>超容量人數</th><th>危險暴露人次</th><th>共同安置平均分鐘</th><th>共同安置 P95 分鐘</th><th>額外步行分鐘</th><th>無障礙群組成功率</th></tr></thead><tbody>${result.summary.map((s) => `<tr><td>${s.strategy} · ${labels[s.strategy]}</td><td>${range(s, 'unassigned_rate', true)}</td><td>${range(s, 'over_capacity')}</td><td>${range(s, 'danger_exposure_people')}</td><td>${range(s, 'paired_mean_minutes')}</td><td>${range(s, 'paired_p95_minutes')}</td><td>${range(s, 'paired_extra_minutes')}</td><td>${range(s, 'accessible_success_rate', true)}</td></tr>`).join('')}</tbody></table></div><div class="panelbody"><h3>未安置率 %（零值仍顯示數字）</h3><div class="chart" id="sim-chart"></div><div class="mini-grid result"><div><h3>共同安置者平均到達時間（分鐘）</h3><div id="time-chart"></div></div><div><h3>各場所平均負載（人）</h3><div id="load-chart"></div></div></div><div class="divider"></div><h3>各種子明細</h3><div class="tablewrap"><table><thead><tr><th>種子</th><th>策略</th><th>需求 / 安置 / 缺口</th><th>場所負載 A / B / C</th><th>共同安置人數</th><th>C 推薦封路違反</th></tr></thead><tbody>${result.rows.map((r) => `<tr><td>${r.seed}</td><td>${r.strategy}</td><td>${r.demand} / ${r.assigned} / ${r.unassigned}</td><td>${r.loads.sA || 0} / ${r.loads.sB || 0} / ${r.loads.sC || 0}</td><td>${r.common_assigned_people}</td><td>${r.strategy === 'C' ? r.constraint_violations : '—'}</td></tr>`).join('')}</tbody></table></div><div class="divider"></div>${result.assumptions.map((a) => `<p class="hint">• ${esc(a)}</p>`).join('')}<p class="hint">資料 ${esc(result.data_version)} / 修訂 ${result.data_revision} · 演算法 ${esc(result.algorithm_version)} · 實驗 ${esc(result.id)}</p></div></div>`;
  drawBarChart(
    'time-chart',
    result.summary.map((s) => ({
      label: s.strategy + ' ' + labels[s.strategy],
      value: s.paired_mean_minutes.mean,
    })),
  );
  const shelterIds = Object.keys(result.rows[0].loads);
  drawBarChart(
    'load-chart',
    result.summary.flatMap((s) =>
      shelterIds
        .filter((id) => ['sA', 'sB', 'sC'].includes(id))
        .map((id) => ({
          label: s.strategy + ' / 場所 ' + id.replace('s', ''),
          value:
            result.rows
              .filter((r) => r.strategy === s.strategy)
              .reduce((n, r) => n + (r.loads[id] || 0), 0) / result.params.repeats,
        })),
    ),
  );
  for (const s of result.summary) {
    const wrapper = document.createElement('div');
    wrapper.className = 'barwrap';
    const val = document.createElement('span');
    val.textContent = fmt(s.unassigned_rate.mean * 100) + '%';
    const bar = document.createElement('div');
    bar.className = 'bar';
    bar.style.height = Math.max(1, s.unassigned_rate.mean * 120) + 'px';
    const label = document.createElement('small');
    label.textContent = s.strategy + ' ' + labels[s.strategy];
    wrapper.append(val, bar, label);
    $('sim-chart').append(wrapper);
  }
}
function renderAccount() {
  const registered = !me.guest;
  $('account-status').textContent = registered ? `已登入 ${me.username}` : '訪客模式';
  $('auth-form').hidden = registered;
  $('auth-tabs').hidden = registered;
  $('logged-in').hidden = !registered;
  $('auth-heading').textContent = registered ? '個人需求設定' : '登入或建立帳號';
  if (registered) {
    $('profile-age').value = me.profile.age ?? '';
  }
  if (registered)
    $('profile-summary').textContent =
      `帳號 ${me.username} · 需求 v${me.version} · 年齡 ${me.profile.age ?? me.profile.age_band ?? '未填'} · 行動模式 ${me.profile.needs?.mode ?? '尚未確認'}。資料僅本人可讀寫。`;
}
document.querySelectorAll('[data-auth]').forEach((e) =>
  e.addEventListener('click', () => {
    authMode = e.dataset.auth;
    document.querySelectorAll('[data-auth]').forEach((x) => x.classList.toggle('active', x === e));
    $('register-fields').hidden = authMode !== 'register';
    $('auth-submit').textContent = authMode === 'register' ? '建立帳號並儲存需求' : '登入';
    $('password').autocomplete = authMode === 'register' ? 'new-password' : 'current-password';
  }),
);
async function discardSession() {
  if (token) {
    await api('/api/auth/logout', { method: 'POST' });
    token = null;
    sessionStorage.removeItem('anxing.token');
  }
  currentPlan = null;
  sessionStorage.removeItem('anxing.plan');
  $('route-result').textContent = '';
  saveQueue([]);
  scope = crypto.randomUUID();
  sessionStorage.setItem('anxing.scope', scope);
}
$('auth-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const body = { username: $('username').value, password: $('password').value };
  if (authMode === 'register')
    body.profile = {
      age: $('age').value ? Number($('age').value) : null,
      age_band: null,
      needs: $('use-needs').checked ? needs() : null,
      consent: $('consent').checked,
    };
  $('auth-submit').disabled = true;
  try {
    const result = await api(`/api/auth/${authMode}`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
    const previousToken = token;
    if (previousToken) {
      try {
        await api('/api/auth/logout', { method: 'POST' });
      } catch (e) {
        if (e.status !== 401) toast('舊工作階段未能登出，配置將依期限釋放。');
      }
    }
    token = result.token;
    sessionStorage.setItem('anxing.token', token);
    currentPlan = null;
    sessionStorage.removeItem('anxing.plan');
    $('route-result').textContent = '';
    saveQueue([]);
    scope = crypto.randomUUID();
    sessionStorage.setItem('anxing.scope', scope);
    me = await api('/api/auth/me');
    $('password').value = '';
    renderAccount();
    message('auth-message', '已登入，出發前請載入並確認本次需求。');
    await refresh();
  } catch (e) {
    message(
      'auth-message',
      e.message + (e.fields ? '：' + e.fields.map((x) => x.message).join('；') : ''),
      true,
    );
  } finally {
    $('auth-submit').disabled = false;
  }
});
$('load-profile').addEventListener('click', () => {
  setNeeds(me.profile.needs);
  showPage('navigation');
  toast('已載入帳號需求，請再次確認全組需求');
});
$('update-profile').addEventListener('click', async () => {
  try {
    const result = await api('/api/auth/profile', {
      method: 'PUT',
      body: JSON.stringify({
        ...me.profile,
        age: $('profile-age').value === '' ? null : Number($('profile-age').value),
        needs: needs(),
        consent: true,
      }),
    });
    me = await api('/api/auth/me');
    renderAccount();
    message('auth-message', result.message);
    await refresh();
  } catch (e) {
    message('auth-message', e.message, true);
  }
});
$('logout').addEventListener('click', async () => {
  try {
    await discardSession();
    me = { guest: true };
    localStorage.removeItem('anxing.profile');
    renderAccount();
    message('auth-message', '已登出，已清除本機預存個人需求。');
    await refresh();
  } catch (e) {
    message('auth-message', e.message, true);
  }
});
$('delete-account').addEventListener('click', async () => {
  if (!$('delete-consent').checked) {
    message('auth-message', '請先勾選刪除確認。', true);
    return;
  }
  try {
    await api('/api/auth/me', { method: 'DELETE' });
    token = null;
    sessionStorage.removeItem('anxing.token');
    sessionStorage.removeItem('anxing.plan');
    localStorage.removeItem('anxing.profile');
    currentPlan = null;
    $('route-result').textContent = '';
    saveQueue([]);
    me = { guest: true };
    renderAccount();
    message('auth-message', '帳號與需求已刪除。');
    await refresh();
  } catch (e) {
    message('auth-message', e.message, true);
  }
});
$('save-profile-offline').addEventListener('click', () => {
  localStorage.setItem(
    'anxing.profile',
    JSON.stringify({ needs: me.profile.needs, saved_at: Date.now() }),
  );
  toast('已依同意預存需求至本機，未預存帳號與年齡。');
});
$('load-offline-profile').addEventListener('click', () => {
  const saved = localStorage.getItem('anxing.profile');
  if (!saved) {
    toast('尚無本機預存需求');
    return;
  }
  const value = JSON.parse(saved);
  setNeeds(value.needs);
  showPage('navigation');
  toast('已載入本機需求，請再次確認。');
});
window.addEventListener('online', () => {
  refresh();
  if (queue().length) toast('連線已恢復，離線回報可手動上傳。');
});
window.addEventListener('offline', () => setConnection(false));
async function init() {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker
      .register('/sw.js')
      .catch((e) => console.warn('離線快取未啟用：' + e.message));
  }
  await refresh();
  await loadEvents();
  updateQueue();
  if (token) {
    try {
      me = await api('/api/auth/me');
      const saved = sessionStorage.getItem('anxing.plan');
      if (saved) {
        currentPlan = await api(`/api/plans/${saved}`);
        const req = currentPlan.requirements;
        if (req) {
          setNeeds(req.needs);
          $('origin').value = req.origin;
          $('group-size').value = req.group_size;
          $('accessible-count').value = req.accessible_count;
          $('helper').checked = req.helper_present;
          $('extra').value = req.max_extra_minutes;
        }
        renderPlan();
      }
    } catch (e) {
      if (e.status === 401) {
        token = null;
        sessionStorage.removeItem('anxing.token');
        sessionStorage.removeItem('anxing.plan');
      } else if (e.status !== 0) toast(e.message);
    }
  }
  renderAccount();
  setInterval(() => {
    if (!document.hidden) refresh();
  }, 12000);
}
init();

function drawReach(reach) {
  if (!data) return;
  const times = Object.fromEntries(reach.nodes.map((n) => [n.id, n]));
  const lookup = Object.fromEntries(data.nodes.map((n) => [n.id, n]));
  const colors = { 5: '#28735b', 10: '#7eac73', 15: '#c2d8ab' };
  let svg =
    '<svg viewBox="0 0 800 470" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="步行5、10、15分鐘可達節點示意圖">';
  for (const e of data.edges) {
    const a = project(lookup[e.a]),
      b = project(lookup[e.b]);
    svg += `<line x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" stroke="#cfd8c6" stroke-width="5"/>`;
  }
  for (const n of data.nodes) {
    const [x, y] = project(n),
      time = times[n.id];
    svg += `<circle cx="${x}" cy="${y}" r="18" fill="${colors[time.within] || '#e2e5dc'}"/><text x="${x}" y="${y + 4}" text-anchor="middle" fill="white">${time.minutes == null ? '×' : fmt(time.minutes)}</text>`;
  }
  svg +=
    '<text x="30" y="445">深綠 ≤5分・中綠 ≤10分・淺綠 ≤15分・灰色 超時／不可達（節點值單位：分）</text></svg>';
  $('reach-map').innerHTML = svg;
}
function drawBarChart(id, values) {
  const maximum = Math.max(1, ...values.map((x) => x.value || 0)),
    height = values.length * 30 + 40;
  let svg = `<svg viewBox="0 0 440 ${height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="${esc(id === 'time-chart' ? '平均到達分鐘比較' : '平均場所負載人數比較')}">`;
  for (const [t, i] of [
    [0, 0],
    [maximum / 2, 1],
    [maximum, 2],
  ]) {
    const x = 135 + i * 125;
    svg += `<line x1="${x}" y1="10" x2="${x}" y2="${height - 25}" stroke="#dce4df"/><text x="${x}" y="${height - 5}" text-anchor="middle" fill="#637572" font-size="10">${fmt(t)}</text>`;
  }
  values.forEach((r, i) => {
    const y = 15 + i * 30;
    svg += `<text x="125" y="${y + 13}" text-anchor="end" font-size="11" fill="#16312f">${esc(r.label)}</text><rect x="135" y="${y}" width="${Math.max(0, ((r.value || 0) / maximum) * 250)}" height="18" rx="3" fill="${['#2b7660', '#7ba07b', '#a1b997'][i % 3]}"/><text x="${145 + ((r.value || 0) / maximum) * 250}" y="${y + 13}" font-size="10" fill="#16312f">${fmt(r.value)}</text>`;
  });
  svg += '</svg>';
  $(id).innerHTML = svg;
}
