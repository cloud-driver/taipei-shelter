'use strict';
const $ = (id) => document.getElementById(id);
const esc = (v) =>
  String(v ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );
let adminToken = '',
  shelters = [],
  photoUrls = [];
function clearPhotos() {
  photoUrls.forEach((u) => URL.revokeObjectURL(u));
  photoUrls = [];
}
function message(id, text, error = false) {
  $(id).innerHTML = `<div class="notice${error ? ' error' : ''}">${esc(text)}</div>`;
}
async function api(url, options = {}) {
  const r = await fetch(url, {
    ...options,
    headers: { 'Content-Type': 'application/json', 'X-Admin-Token': adminToken },
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail?.message || d.detail || '操作失敗');
  return d;
}
async function load() {
  if (!adminToken) {
    message('admin-message', '請先輸入管理金鑰。', true);
    return;
  }
  try {
    const [overview, map] = await Promise.all([api('/api/admin/overview'), api('/api/map')]);
    clearPhotos();
    shelters = map.shelters;
    $('admin-content').hidden = false;
    const statuses = {
      proposed: '待接受',
      accepted: '在途',
      arrived: '已到達',
      invalidated: '需重規劃',
    };
    $('admin-metrics').innerHTML = Object.entries(statuses)
      .map(
        ([key, label]) =>
          `<div class="metric"><label>${label} 配置數</label><div class="value">${overview.allocations[key] || 0}</div></div>`,
      )
      .join('');
    const names = {
      pending: '待確認',
      avoiding: '暫時避開',
      confirmed: '確認封閉',
      resolved: '已解除',
    };
    $('admin-reports').innerHTML = overview.reports.length
      ? overview.reports
          .map(
            (r) =>
              `<article class="reportrow"><span class="pill">${esc(names[r.status])}</span> <strong>${esc(r.target_id)} · ${esc(r.kind)}</strong><p>${esc(r.description)}</p>${r.has_photo ? `<button class="btn small" type="button" data-photo="${esc(r.id)}">檢視回報照片</button><div class="photo-result"></div>` : ''}<p class="hint">發現 ${new Date(r.observed_at * 1000).toLocaleString('zh-TW')} · ${esc(r.note || '尚無審核紀錄')}</p><form data-report="${esc(r.id)}"><div class="formrow"><label class="field">審核狀態<select name="status">${Object.entries(
                names,
              )
                .map(
                  ([key, name]) =>
                    `<option value="${key}"${key === r.status ? ' selected' : ''}>${name}</option>`,
                )
                .join(
                  '',
                )}</select></label><label class="field">查核／解除理由<input name="note" required minlength="3" maxlength="300" placeholder="說明查核依據，不能僅因時間到期解除"></label></div><button type="submit" class="btn small">儲存審核結果</button><div class="review-result" role="status"></div></form></article>`,
          )
          .join('')
      : '<div class="panelbody hint">尚無回報。可先到導航頁提交演練災情。</div>';
    document.querySelectorAll('[data-report]').forEach((form) =>
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        try {
          await api(`/api/admin/reports/${form.dataset.report}`, {
            method: 'PATCH',
            body: JSON.stringify({
              status: form.elements.status.value,
              note: form.elements.note.value,
            }),
          });
          message('admin-message', '災情已更新；受影響在途路線會失效。');
          await load();
        } catch (error) {
          form.querySelector('.review-result').textContent = error.message;
        }
      }),
    );
    document.querySelectorAll('[data-photo]').forEach((button) =>
      button.addEventListener('click', async () => {
        try {
          const response = await fetch(`/api/reports/${button.dataset.photo}/photo`, {
            headers: { 'X-Admin-Token': adminToken },
          });
          if (!response.ok) throw new Error('照片讀取失敗');
          const url = URL.createObjectURL(await response.blob());
          photoUrls.push(url);
          const img = document.createElement('img');
          img.src = url;
          img.alt = '回報人提供的演練災情照片';
          img.className = 'report-photo';
          button.nextElementSibling.replaceChildren(img);
          button.disabled = true;
        } catch (e) {
          message('admin-message', e.message, true);
        }
      }),
    );
    const selected = $('admin-shelter').value;
    $('admin-shelter').innerHTML = shelters
      .map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`)
      .join('');
    if (selected) $('admin-shelter').value = selected;
    fillShelter();
    message('admin-message', '管理介面已連線。');
  } catch (e) {
    message('admin-message', e.message, true);
    $('admin-content').hidden = true;
  }
}
function fillShelter() {
  const s = shelters.find((s) => s.id === $('admin-shelter').value);
  if (!s) return;
  for (const [id, key] of [
    ['shelter-status', 'status'],
    ['shelter-capacity', 'capacity'],
    ['shelter-accessible', 'accessible_capacity'],
    ['shelter-background', 'background'],
    ['shelter-background-accessible', 'background_accessible'],
  ])
    $(id).value = s[key] ?? '';
  $('shelter-entrance').value = String(s.entrance_step_free);
  $('shelter-toilet').value = String(s.accessible_toilet);
  $('shelter-space').value = String(s.space_accessible);
}
$('admin-auth').addEventListener('submit', (e) => {
  e.preventDefault();
  adminToken = $('admin-token').value;
  $('admin-token').value = '';
  load();
});
$('admin-refresh').addEventListener('click', load);
$('admin-lock').addEventListener('click', () => {
  adminToken = '';
  clearPhotos();
  $('admin-token').value = '';
  $('admin-content').hidden = true;
  $('admin-reports').textContent = '';
  message('admin-message', '管理介面已鎖定。');
});
$('admin-shelter').addEventListener('change', fillShelter);
$('shelter-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const number = (id) => ($(id).value === '' ? null : Number($(id).value));
  const body = {
    status: $('shelter-status').value,
    capacity: number('shelter-capacity'),
    accessible_capacity: number('shelter-accessible'),
    background: number('shelter-background'),
    background_accessible: number('shelter-background-accessible'),
    entrance_step_free: JSON.parse($('shelter-entrance').value),
    space_accessible: JSON.parse($('shelter-space').value),
    accessible_toilet: JSON.parse($('shelter-toilet').value),
    note: $('shelter-note').value,
  };
  try {
    const result = await api(`/api/admin/shelters/${$('admin-shelter').value}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    });
    message('shelter-message', result.message);
    await load();
  } catch (error) {
    message('shelter-message', error.message, true);
  }
});
$('event-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const now = Date.now() / 1000;
  try {
    await api('/api/admin/events', {
      method: 'POST',
      body: JSON.stringify({
        event_id: $('event-id').value,
        version: Number($('event-version').value),
        occurred_at: now,
        published_at: now,
        title: $('event-title').value,
        magnitude: Number($('event-magnitude').value),
        source: '本機合成演練・非官方資訊',
        drill: true,
      }),
    });
    message('event-message', '演練事件已發布；導覽頁重新整理後可見。');
    $('event-version').value = Number($('event-version').value) + 1;
  } catch (error) {
    message('event-message', error.message, true);
  }
});
