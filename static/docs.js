'use strict';
const $ = (id) => document.getElementById(id),
  esc = (v) =>
    String(v ?? '').replace(
      /[&<>"']/g,
      (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
    );
let endpoints = [];
fetch('/openapi.json')
  .then((r) => r.json())
  .then((spec) => {
    for (const [path, methods] of Object.entries(spec.paths))
      for (const [method, detail] of Object.entries(methods)) {
        endpoints.push({ path, method, detail });
      }
    $('endpoint').innerHTML = endpoints
      .map((e, i) => `<option value="${i}">${e.method.toUpperCase()} ${esc(e.path)}</option>`)
      .join('');
    selectEndpoint();
    $('api-reference').innerHTML = endpoints
      .map(
        (e) =>
          `<details><summary><strong>${e.method.toUpperCase()} ${esc(e.path)}</strong> · ${esc(e.detail.summary || '')}</summary><pre class="code">${esc(JSON.stringify(e.detail, null, 2))}</pre></details>`,
      )
      .join('');
    $('api-schemas').innerHTML = Object.entries(spec.components.schemas)
      .map(
        ([name, body]) =>
          `<details><summary>${esc(name)}</summary><pre class="code">${esc(JSON.stringify(body, null, 2))}</pre></details>`,
      )
      .join('');
  })
  .catch((e) => ($('api-response').textContent = e.message));
function selectEndpoint() {
  const e = endpoints[Number($('endpoint').value)];
  $('request-path').value = e.path;
  $('json-body').disabled = ['get', 'delete'].includes(e.method);
  $('json-body').value = '';
}
$('endpoint').addEventListener('change', selectEndpoint);
$('api-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const e = endpoints[Number($('endpoint').value)];
    const path = $('request-path').value;
    if (!/^\/api\/[A-Za-z0-9_./-]+$/.test(path))
      throw new Error('只允許此後端的 /api/ 路徑，請替換大括號識別。');
    const options = {
      method: e.method.toUpperCase(),
      headers: {
        ...($('bearer').value ? { Authorization: 'Bearer ' + $('bearer').value } : {}),
        ...($('admin-key').value ? { 'X-Admin-Token': $('admin-key').value } : {}),
      },
    };
    if (!$('json-body').disabled && $('json-body').value.trim()) {
      options.body = JSON.stringify(JSON.parse($('json-body').value));
      options.headers['Content-Type'] = 'application/json';
    }
    const r = await fetch(path, options);
    const text = await r.text();
    let body = text;
    try {
      body = JSON.stringify(JSON.parse(text), null, 2);
    } catch {}
    $('api-response').textContent = `HTTP ${r.status}\n${body}`;
  } catch (e) {
    $('api-response').textContent = e.message;
  }
});
$('docs-guest').addEventListener('click', async () => {
  try {
    const r = await fetch('/api/guest', { method: 'POST' });
    const body = await r.json();
    if (!r.ok) throw new Error(body.detail?.message || '建立失敗');
    $('bearer').value = body.token;
    $('api-response').textContent =
      '訪客 token 已填入欄位，關閉頁面即清除。不會使用主網頁的登入憑證。';
  } catch (e) {
    $('api-response').textContent = e.message;
  }
});
$('docs-clear').addEventListener('click', () => {
  $('bearer').value = '';
  $('admin-key').value = '';
  $('api-response').textContent = '本頁憑證已清除。';
});
