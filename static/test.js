'use strict';
const $ = (id) => document.getElementById(id);
const esc = (v) =>
  String(v ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );
$('run-tests').addEventListener('click', async () => {
  $('run-tests').disabled = true;
  $('test-results').textContent = '';
  let token = null,
    planId = null,
    passed = 0,
    failed = 0;
  const start = performance.now();
  async function request(path, method = 'GET', body) {
    const r = await fetch(path, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(body ? { 'Content-Type': 'application/json' } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    const value = await r.json();
    return { status: r.status, value };
  }
  const expect = (condition, message) => {
    if (!condition) throw new Error(message);
  };
  async function check(name, fn) {
    const row = document.createElement('div');
    row.className = 'testrow';
    row.innerHTML = `<span class="teststate pill gray">執行中</span><div><strong>${esc(name)}</strong><div class="hint"></div></div>`;
    $('test-results').append(row);
    try {
      const note = await fn();
      row.querySelector('.teststate').className = 'teststate pill';
      row.querySelector('.teststate').textContent = '通過';
      row.querySelector('.hint').textContent = note || '符合預期';
      passed++;
    } catch (e) {
      row.querySelector('.teststate').className = 'teststate pill red';
      row.querySelector('.teststate').textContent = '失敗';
      row.querySelector('.hint').textContent = e.message;
      failed++;
    }
  }
  let payload;
  try {
    await check('後端與資料庫健康檢查', async () => {
      const r = await request('/api/health');
      expect(r.status === 200 && r.value.status === 'ok', '健康狀態異常');
      return `${r.value.mode} / ${r.value.database}`;
    });
    await check('官方全市名冊與 OSM 快照', async () => {
      const r = await request('/api/public/sources');
      expect(r.status === 200 && r.value.districts.length === 12, '缺少全市官方資料');
      expect(
        r.value.raw_counts.education_rows > 400 && r.value.osm.edge_count > 100000,
        '名冊或真實路網未建立',
      );
      return `${r.value.catalog_count} 筆官方紀錄 / OSM ${r.value.osm.timestamp}`;
    });
    await check('實際 OSM 道路導航，沒有容量配置', async () => {
      const r = await request('/api/public/route', 'POST', {
        lat: 25.0422,
        lon: 121.5354,
        needs: { mode: 'walking', confirmed: true },
        shaking_stopped: true,
        evacuation_needed: true,
        acknowledge_unknown_status: true,
      });
      expect(r.status === 200, JSON.stringify(r.value));
      expect(
        r.value.allocation_created === false &&
          r.value.geometry.coordinates.length > 2 &&
          r.value.edge_ids.every((id) => id.startsWith('osm-')),
        '路線未沿真實OSM路網',
      );
      return `${r.value.shelter.name} / ${r.value.distance_m} m / ${r.value.edge_ids.length} 路段`;
    });
    await check('未知無障礙條件不放寬推薦', async () => {
      const r = await request('/api/public/route', 'POST', {
        lat: 25.0422,
        lon: 121.5354,
        needs: { mode: 'wheelchair', confirmed: true },
        shaking_stopped: true,
        evacuation_needed: true,
        acknowledge_unknown_status: true,
      });
      expect(r.status === 409, '未拒絕未知入口與全程通行條件');
    });
    await check('OSM 無障礙標記與場所評級', async () => {
      const a = await request('/api/public/accessibility?rating=yes');
      expect(
        a.status === 200 && a.value.total > 0 && a.value.route_verified === false,
        '無障礙圖層缺失或冒充全程查核',
      );
      const r = await request('/api/public/catalog?wheelchair=yes');
      expect(
        r.status === 200 &&
          r.value.total > 0 &&
          r.value.items.every((p) => p.wheelmap_osm.rating === 'yes' && !p.entrance_verified),
        '場所評級篩選異常',
      );
      return `${a.value.total} 筆綠色標記 / ${r.value.total} 筆震災名冊匹配；入口與全程仍待查核`;
    });
    await check('公開示意圖：資料來源與未知容量保留', async () => {
      const r = await request('/api/map');
      expect(r.status === 200 && r.value.drill === true, '未明確標示演練');
      expect(
        r.value.shelters.some((s) => s.capacity === null),
        '未知容量被錯誤填補',
      );
      expect(r.value.edges.length > 0, '缺少路網');
      return `${r.value.nodes.length} 節點 / ${r.value.edges.length} 路段 / ${r.value.shelters.length} 場所`;
    });
    await check('管理 API 權限阻擋', async () => {
      const r = await request('/api/admin/overview');
      expect(r.status === 403, `預期403，收到${r.status}`);
    });
    await check('建立獨立訪客工作階段', async () => {
      const r = await request('/api/guest', 'POST');
      expect(r.status === 200 && r.value.token, '訪客建立失敗');
      token = r.value.token;
    });
    payload = {
      request_id: crypto.randomUUID(),
      origin: 'n22',
      group_size: 1,
      accessible_count: 0,
      helper_present: false,
      needs: { mode: 'walking', confirmed: true },
      shaking_stopped: false,
      evacuation_needed: true,
      max_extra_minutes: 5,
      replace_id: null,
    };
    await check('搖晃未停止時，拒絕進入導航', async () => {
      const r = await request('/api/plans', 'POST', payload);
      expect(r.status === 409, `預期409，收到${r.status}`);
    });
    payload.shaking_stopped = true;
    await check('取得有容量限制的可達路線', async () => {
      const r = await request('/api/plans', 'POST', payload);
      expect(r.status === 201, JSON.stringify(r.value.detail || r.value));
      planId = r.value.id;
      expect(r.value.status === 'proposed' && r.value.route.node_ids.length > 0, '配置結果不完整');
      return `${r.value.route.shelter_name} / ${r.value.route.minutes} 分鐘`;
    });
    await check('重送相同請求，不重複配置容量', async () => {
      expect(planId, '前一步沒有可用配置');
      const r = await request('/api/plans', 'POST', payload);
      expect(r.status === 201 && r.value.id === planId, '重複請求未取得相同配置');
    });
    await check('相同識別改變內容，拒絕覆寫', async () => {
      expect(planId, '前一步沒有可用配置');
      const r = await request('/api/plans', 'POST', { ...payload, group_size: 2 });
      expect(r.status === 409, `預期409，收到${r.status}`);
    });
    await check('接受路線後取消，正確釋放配置', async () => {
      expect(planId, '沒有可用配置');
      let r = await request(`/api/plans/${planId}/transition`, 'POST', { action: 'accept' });
      expect(r.value.status === 'accepted', '接受失敗');
      r = await request(`/api/plans/${planId}/transition`, 'POST', { action: 'cancel' });
      expect(r.value.status === 'cancelled', '取消失敗');
    });
    await check('供需分析：安置與缺口守恆', async () => {
      const r = await request('/api/analysis');
      expect(
        r.status === 200 && r.value.areas.every((a) => a.assigned + a.unassigned === a.demand),
        '供需數量不守恆',
      );
      return `${r.value.areas.length} 個合成需求區`;
    });
    await check('三策略實驗與 CSV 匯出', async () => {
      const r = await request('/api/experiments', 'POST', {
        seed: 7,
        repeats: 1,
        population: 30,
        evacuation_rate: 0.3,
        app_rate: 0.6,
      });
      expect(r.status === 200 && r.value.rows.length === 3, '三策略結果不完整');
      const csv = await fetch(`/api/experiments/${r.value.id}/csv`);
      expect(csv.ok && (await csv.text()).includes('strategy'), 'CSV 匯出失敗');
      return '相同需求比較 A / B / C，逐人數量守恆';
    });
  } finally {
    await check('清理臨時演練工作階段', async () => {
      if (token) {
        const r = await request('/api/auth/logout', 'POST');
        expect(r.status === 200, '登出清理失敗');
        token = null;
      }
      return '臨時配置已釋放';
    });
    $('test-summary').textContent = `${passed} 通過 / ${failed} 失敗`;
    $('test-summary').className = `pill${failed ? ' red' : ''}`;
    $('test-duration').textContent = `耗時 ${((performance.now() - start) / 1000).toFixed(2)} 秒`;
    $('run-tests').disabled = false;
  }
});
