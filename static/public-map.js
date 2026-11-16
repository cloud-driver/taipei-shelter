(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const esc = (v) =>
    String(v ?? '').replace(
      /[&<>"']/g,
      (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
    );
  const fmt = (v, d = 0) =>
    v == null ? '未知' : Number(v).toLocaleString('zh-TW', { maximumFractionDigits: d });
  const typeName = { shelter: '收容名冊', park: '防災公園', airraid: '防空設施' };
  const roleName = {
    primary: '震災適用',
    backup: '震災備用',
    not_applicable: '非震災適用',
    disaster_park: '防災公園',
  };
  let map,
    tiles,
    markers,
    routeLayer,
    startMarker,
    targetMarker,
    meta,
    selected = null,
    offset = 0,
    currentRows = [],
    currentRoute = null,
    requestVersion = 0;
  let initialized = false,
    filterTimer,
    routeVersion = 0,
    accessibilityLayer,
    accessibilityData;
  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: {
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...options.headers,
      },
    });
    const body = await response.json();
    if (!response.ok)
      throw new Error(body.detail?.message || body.detail || `HTTP ${response.status}`);
    return body;
  }
  function notice(id, text, error = false) {
    $(id).innerHTML = `<div class="notice${error ? ' error' : ''}">${esc(text)}</div>`;
  }
  function params() {
    return new URLSearchParams({
      kind: $('public-kind').value,
      district: $('public-district').value,
      q: $('public-search').value.trim(),
      wheelchair: $('public-wheelchair-filter').value,
    });
  }
  function color(item) {
    return item.status === 'closed'
      ? '#8b9390'
      : item.kind === 'airraid'
        ? '#8a6897'
        : item.kind === 'park'
          ? '#68954d'
          : item.earthquake_role === 'backup'
            ? '#bd8537'
            : '#247e6d';
  }
  function clearRoute() {
    routeVersion++;
    currentRoute = null;
    if (routeLayer) {
      routeLayer.remove();
      routeLayer = null;
    }
    $('public-route-result').textContent = '';
  }
  function setStart(lat, lon, keepRoute = false) {
    if (!keepRoute) {
      const hadRoute = !!currentRoute;
      clearRoute();
      if (hadRoute) notice('public-route-message', '起點已變更，請重新計算路線。', true);
    }
    $('public-lat').value = Number(lat).toFixed(6);
    $('public-lon').value = Number(lon).toFixed(6);
    if (startMarker) startMarker.remove();
    startMarker = L.circleMarker([lat, lon], {
      radius: 8,
      color: '#fff',
      weight: 3,
      fillColor: '#307d9f',
      fillOpacity: 1,
    })
      .addTo(map)
      .bindPopup('本次參考起點（可在地圖點選修正）');
  }
  const ratingName = {
    yes: '可使用輪椅（OSM標記）',
    limited: '部分可使用輪椅',
    no: '不可使用輪椅',
    unknown: '輪椅條件未知',
  };
  const ratingColor = { yes: '#257c4d', limited: '#ce8526', no: '#b8433e', unknown: '#7e8986' };
  function accessibilityHtml(s) {
    const a = s.wheelmap_osm;
    if (!a) return '';
    return `<div class="accessibility-evidence"><strong class="accessibility-rating-${esc(a.rating)}">${esc(ratingName[a.rating] || ratingName.unknown)}</strong><p class="hint">廁所評級：${esc(ratingName[a.toilet_rating] || '未知')}<br>${esc(a.match_method)}${a.conflicting ? '<br>來源標記衝突，列為未知。' : ''}</p>${a.evidence.map((p) => `<p class="hint"><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.name)} · ${esc(p.id)} ↗</a><br>OSM 快照 ${esc(p.timestamp)}${p.check_date ? ' · 查核日期標籤 ' + esc(p.check_date) : ' · 未提供查核日期'}${p.description ? '<br>' + esc(p.description) : ''}</p>`).join('')}<p class="hint">使用 Wheelmap 相同的 OSM 標籤；不是全程可通行或震後開設證明。</p></div>`;
  }
  async function showAccessibility() {
    if (!map) return;
    if (accessibilityLayer) {
      accessibilityLayer.remove();
      accessibilityLayer = null;
    }
    if (!$('public-accessibility-layer').checked) return;
    try {
      if (!accessibilityData) accessibilityData = await api('/api/public/accessibility');
      if (!$('public-accessibility-layer').checked) return;
      const rating = $('public-accessibility-rating').value,
        points = accessibilityData.items.filter((p) => rating === 'all' || p.rating === rating);
      accessibilityLayer = L.layerGroup().addTo(map);
      for (const p of points) {
        const marker = L.circleMarker([p.lat, p.lon], {
          radius: 7,
          color: ratingColor[p.rating],
          weight: 2,
          fillColor: ratingColor[p.rating],
          fillOpacity: 0.5,
        });
        marker.bindPopup(
          `<strong>${esc(p.name)}</strong><p>${esc(ratingName[p.rating])}<br>廁所：${esc(ratingName[p.toilet_rating])}</p>${p.description ? '<p>' + esc(p.description) + '</p>' : ''}<p class="hint">一般設施標記，不代表避難目的地。<br>OSM ${esc(p.timestamp)}<br>尚未確認沿途道路與現場狀態。</p><a href="${esc(p.url)}" target="_blank" rel="noopener">查看原始 OSM 紀錄 ↗</a>`,
        );
        marker.on('click', (e) => L.DomEvent.stopPropagation(e));
        accessibilityLayer.addLayer(marker);
      }
      $('public-accessibility-status').textContent =
        `顯示 ${fmt(points.length)} 筆臺北及鄰近 OSM 無障礙標記，並非全部都是避難場所。快照 ${accessibilityData.meta.timestamp}。${accessibilityData.meta.scope}。綠色不代表全程可通行。`;
    } catch (e) {
      notice('public-accessibility-status', e.message, true);
    }
  }
  function detailHtml(s) {
    const source = meta?.sources[s.source];
    const location = meta?.sources[s.coordinate_source];
    return `<span class="pill ${s.status === 'closed' ? 'red' : s.earthquake ? '' : 'gray'}">${esc(s.status === 'closed' ? '名冊暫停用' : roleName[s.earthquake_role])}</span><h3>${esc(s.name)}</h3><p class="hint">${esc(s.district)} · ${esc(typeName[s.kind])}<br>${esc(s.address || '公園資料未提供門牌，請核對已公布位置')}</p><div class="public-detail-grid"><div><small>名冊容納人數</small><strong>${fmt(s.capacity)} 人</strong></div><div><small>現場開設</small><strong>${s.status === 'closed' ? '暫停用' : '尚未確認'}</strong></div></div><p class="hint">座標來源：${esc(location?.agency || s.coordinate_source || '待核對')}<br>${esc(s.coordinate_method)}<br>無障礙設施：${s.accessible_facility === true ? '名冊記載有設施' : s.accessible_facility === false ? '名冊記載無設施' : '未知'}，入口與全程未查核。</p>${accessibilityHtml(s)}${s.notes ? `<p class="warning">${esc(s.notes)}</p>` : ''}<p class="hint">紀錄 ${esc(s.id)} · <a href="${esc(source?.page || '#')}" target="_blank" rel="noopener">${esc(source?.agency || '官方資料來源')} ↗</a></p>`;
  }
  async function choose(id) {
    clearRoute();
    if (targetMarker) {
      targetMarker.remove();
      targetMarker = null;
    }
    try {
      const s = await api('/api/public/catalog/' + encodeURIComponent(id));
      selected = s;
      $('public-selected').innerHTML = detailHtml(s);
      $('public-destination').textContent = s.name;
      $('public-destination').dataset.id = s.id;
      $('public-clear-target').hidden = false;
      if (s.lat != null) {
        map.setView([s.lat, s.lon], Math.max(map.getZoom(), 15));
        if (targetMarker) targetMarker.remove();
        targetMarker = L.circleMarker([s.lat, s.lon], {
          radius: 12,
          color: color(s),
          weight: 3,
          fillColor: '#fff',
          fillOpacity: 0.7,
        }).addTo(map);
        targetMarker
          .bindPopup(
            `<strong>${esc(s.name)}</strong><p>${esc(s.district)} · ${esc(typeName[s.kind])}<br>${esc(s.status === 'closed' ? '名冊暫停用' : '現場開設尚未確認')}</p><p class="hint">完整來源與無障礙證據見下方「場所資訊」。</p>`,
          )
          .openPopup();
      } else
        notice(
          'public-route-message',
          '此紀錄尚無可核對座標，保留在清單但不放到地圖或導航。',
          true,
        );
      if (!s.earthquake)
        notice(
          'public-route-message',
          '此場所不是震災適用目的地；防空設施只提供獨立圖層查詢。',
          true,
        );
      else if (s.status === 'closed')
        notice('public-route-message', '官方名冊標示暫停用，不能選為導航目的地。', true);
      else $('public-route-message').textContent = '';
    } catch (e) {
      notice('public-route-message', e.message, true);
    }
  }
  async function loadCatalog(reset = true) {
    if (!map) return;
    if (reset) offset = 0;
    const version = ++requestVersion;
    const query = params();
    query.set('offset', offset);
    query.set('limit', 40);
    try {
      const [page, points] = await Promise.all([
        api('/api/public/catalog?' + query),
        api('/api/public/points?' + params()),
      ]);
      if (version !== requestVersion) return;
      currentRows = page.items;
      $('public-result-count').textContent =
        `${fmt(page.total)} 筆紀錄 · ${fmt(page.located)} 筆有座標`;
      $('public-page-label').textContent = page.total
        ? `${offset + 1}–${Math.min(offset + 40, page.total)} / ${fmt(page.total)}`
        : '0 / 0';
      $('public-prev').disabled = offset === 0;
      $('public-next').disabled = offset + 40 >= page.total;
      $('public-list').innerHTML =
        page.items
          .map(
            (s) =>
              `<button class="public-list-item" data-place="${esc(s.id)}"><span class="public-dot ${s.kind}"></span><span><strong>${esc(s.name)}</strong><small>${esc(s.district)} · ${esc(s.status === 'closed' ? '暫停用' : roleName[s.earthquake_role])}${s.lat == null ? ' · 座標待核對' : ''}${s.wheelmap_osm?.rating && s.wheelmap_osm.rating !== 'unknown' ? ' · ' + ratingName[s.wheelmap_osm.rating] : ''}<br>${esc(s.address || '官方公園位置')}</small></span></button>`,
          )
          .join('') || '<p class="hint panelbody">沒有符合篩選條件的紀錄。</p>';
      $('public-list')
        .querySelectorAll('[data-place]')
        .forEach((button) => button.addEventListener('click', () => choose(button.dataset.place)));
      markers.clearLayers();
      const renderer = L.canvas({ padding: 0.3 });
      for (const p of points.items) {
        const marker = L.circleMarker([p.lat, p.lon], {
          radius: p.kind === 'airraid' ? 3 : 6,
          color: color(p),
          weight: 1,
          fillColor: color(p),
          fillOpacity: 0.78,
          renderer,
        });
        marker.bindTooltip(esc(p.name), { direction: 'top' });
        marker.on('click', (e) => {
          L.DomEvent.stopPropagation(e);
          choose(p.id);
        });
        markers.addLayer(marker);
      }
      $('public-map-count').textContent =
        `地圖 ${fmt(points.items.length)} 筆 · ${points.unlocated} 筆座標待核對`;
      $('public-updated').textContent =
        `公開資料快照 ${new Date(meta.snapshot_at).toLocaleString('zh-TW')} · OSM ${meta.osm.timestamp || '尚未建立'}`;
    } catch (e) {
      notice('public-data-message', e.message, true);
    }
  }
  function renderSources() {
    const total = meta.raw_counts;
    $('public-metrics').innerHTML = [
      ['官方收容名冊', total.education_rows, '筆', '包含震災適用、備用與其他用途'],
      ['防災公園', total.parks, '筆', '可切換來源圖層'],
      ['防空設施', total.airraid_rows, '筆', '獨立圖層・不作震災目的地'],
      [
        '有座標的震災紀錄',
        meta.located_earthquake_records + '/' + meta.earthquake_records,
        '',
        '含備用，非已開設／床位數',
      ],
    ]
      .map(
        ([label, value, unit, hint]) =>
          `<div class="metric"><label>${label}</label><div class="value">${typeof value === 'number' ? fmt(value) : esc(value)}<small>${unit}</small></div><div class="foot">${hint}</div></div>`,
      )
      .join('');
    $('public-sources').innerHTML = Object.values(meta.sources)
      .map(
        (s) =>
          `<p class="hint"><a href="${esc(s.page)}" target="_blank" rel="noopener">${esc(s.agency)}｜${esc(s.title)} ↗</a><br>${esc(s.published_version)} · ${esc(s.license)}</p>`,
      )
      .join('<div class="divider"></div>');
    $('public-quality').innerHTML =
      `<p class="hint">收容名冊 ${meta.raw_counts.education_rows} 筆、防災公園 ${meta.raw_counts.parks} 筆、防空設施 ${fmt(meta.raw_counts.airraid_rows)} 筆。來源完整筆數保留，未定位者仍可在清單找到。未定位共 ${meta.unlocated_count} 筆；名冊暫停用震災紀錄 ${meta.closed_earthquake_records} 筆。</p><p class="hint">OSM 節點 ${fmt(meta.osm.node_count)}、路段 ${fmt(meta.osm.edge_count)}；來源時間 ${esc(meta.osm.timestamp || '尚未建立')}。道路資料存於本機，規劃不傳起點到第三方路由服務。</p>${meta.accessibility ? '<p class="hint">無障礙標記 ' + meta.accessibility.observations + ' 筆；匹配避難名冊 ' + meta.accessibility.matched_catalog_records + ' 筆。' + esc(meta.accessibility.scope) + '。OSM ' + esc(meta.accessibility.timestamp) + '；未使用 Wheelmap 合作夥伴資料。</p>' : ''}${meta.notes.map((n) => `<p class="hint">• ${esc(n)}</p>`).join('')}`;
  }
  async function init() {
    if (initialized) return;
    initialized = true;
    try {
      meta = await api('/api/public/sources');
      $('public-district').innerHTML =
        '<option value="">臺北市全部行政區</option>' +
        meta.districts.map((d) => `<option value="${esc(d)}">${esc(d)}</option>`).join('');
      renderSources();
      map = L.map('public-map', { preferCanvas: true, zoomControl: true }).setView(
        [25.0478, 121.5319],
        13,
      );
      tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution:
          '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a> · ODbL',
      }).addTo(map);
      let tileFailed = false;
      tiles.on('tileerror', () => {
        if (!tileFailed) {
          tileFailed = true;
          notice('public-tile-message', '背景圖磚暫時無法載入，仍可查詢本機名冊與計算OSM路線。');
        }
      });
      markers = L.layerGroup().addTo(map);
      map.on('click', (e) => setStart(e.latlng.lat, e.latlng.lng));
      await loadCatalog();
      if ($('public-accessibility-layer').checked) await showAccessibility();
      requestAnimationFrame(() => map.invalidateSize());
    } catch (e) {
      initialized = false;
      notice('public-data-message', e.message, true);
    }
  }
  $('public-accessibility-layer').addEventListener('change', showAccessibility);
  $('public-accessibility-rating').addEventListener('change', showAccessibility);
  $('public-wheelchair-filter').addEventListener('change', () => loadCatalog());
  $('public-mode').addEventListener('change', () => {
    if ($('public-mode').value === 'wheelchair') {
      $('public-accessibility-layer').checked = true;
      showAccessibility();
    }
  });
  $('public-kind').addEventListener('change', () => loadCatalog());
  $('public-district').addEventListener('change', () => loadCatalog());
  $('public-search').addEventListener('input', () => {
    clearTimeout(filterTimer);
    filterTimer = setTimeout(() => loadCatalog(), 300);
  });
  $('public-prev').addEventListener('click', () => {
    offset = Math.max(0, offset - 40);
    loadCatalog(false);
  });
  $('public-next').addEventListener('click', () => {
    offset += 40;
    loadCatalog(false);
  });
  $('public-refresh').addEventListener('click', () => loadCatalog());
  $('public-fit').addEventListener('click', () => map?.setView([25.075, 121.55], 12));
  $('public-tiles').addEventListener('change', () => {
    if (!map) return;
    if ($('public-tiles').checked) tiles.addTo(map);
    else tiles.remove();
  });
  $('public-clear-target').addEventListener('click', () => {
    clearRoute();
    selected = null;
    $('public-destination').textContent = '自動選擇路網可達的最近震災場所';
    delete $('public-destination').dataset.id;
    $('public-clear-target').hidden = true;
    if (targetMarker) targetMarker.remove();
    $('public-selected').innerHTML =
      '<p class="hint">點選地圖或下方清單中的場所，可查看來源、名冊容量與位置品質。</p>';
    $('public-route-message').textContent = '';
  });
  for (const id of ['public-lat', 'public-lon'])
    $(id).addEventListener('change', () => {
      const lat = Number($('public-lat').value),
        lon = Number($('public-lon').value);
      if (lat >= 24.94 && lat <= 25.23 && lon >= 121.44 && lon <= 121.69) setStart(lat, lon);
    });
  $('public-locate').addEventListener('click', () => {
    if (!navigator.geolocation) {
      notice('public-route-message', '瀏覽器不支援定位，請在地圖點選或輸入座標。', true);
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (p) => {
        setStart(p.coords.latitude, p.coords.longitude);
        map.setView([p.coords.latitude, p.coords.longitude], 16);
        notice(
          'public-route-message',
          `定位精度約 ${fmt(p.coords.accuracy)} m；可在地圖修正，座標只送到本機後端。`,
        );
      },
      () => notice('public-route-message', '未取得定位，請在地圖點選實際戶外起點。', true),
      { enableHighAccuracy: true, timeout: 12000, maximumAge: 30000 },
    );
  });
  $('public-route-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    clearRoute();
    const version = routeVersion;
    $('public-route-button').disabled = true;
    try {
      const stepFree = $('public-mode').value !== 'walking' && $('public-mode').value !== 'slow';
      const body = {
        lat: Number($('public-lat').value),
        lon: Number($('public-lon').value),
        destination_id: selected?.id || null,
        needs: {
          mode: $('public-mode').value,
          can_stairs: !$('public-no-stairs').checked,
          step_free: stepFree || $('public-no-stairs').checked,
          accessible_toilet: $('public-toilet').checked,
          confirmed: $('public-confirmed').checked,
        },
        shaking_stopped: $('public-stopped').checked,
        evacuation_needed: $('public-evacuate').checked,
        acknowledge_unknown_status: $('public-reference-consent').checked,
        helper_present: $('public-helper').checked,
        max_minutes: Number($('public-max-minutes').value),
      };
      const result = await api('/api/public/route', { method: 'POST', body: JSON.stringify(body) });
      if (version !== routeVersion) return;
      currentRoute = result;
      renderRoute(currentRoute);
      notice('public-route-message', 'OSM 路線已計算；現場開設與入口尚未確認，沒有配置容量。');
    } catch (e) {
      if (version === routeVersion) {
        clearRoute();
        notice('public-route-message', e.message, true);
      }
    } finally {
      $('public-route-button').disabled = false;
    }
  });
  function renderRoute(route) {
    map.closePopup();
    if (routeLayer) routeLayer.remove();
    routeLayer = L.geoJSON(
      { type: 'Feature', properties: {}, geometry: route.geometry },
      { style: { color: '#0c7867', weight: 6, opacity: 0.9 } },
    ).addTo(map);
    setStart(Number($('public-lat').value), Number($('public-lon').value), true);
    if (route.geometry.coordinates.length > 1)
      map.fitBounds(routeLayer.getBounds(), { padding: [40, 40], maxZoom: 17 });
    selected = route.shelter;
    $('public-selected').innerHTML = detailHtml(selected);
    $('public-destination').textContent = selected.name;
    $('public-clear-target').hidden = false;
    $('public-route-result').innerHTML =
      `<div class="panel result"><div class="panelhead"><h2>前往 ${esc(selected.name)}</h2><span class="pill amber">路線參考・未配置容量</span></div><div class="panelbody"><div class="route-number">${fmt(route.minutes, 1)} <small>分鐘・路網估計</small></div><div class="route-grid"><div><label>OSM 路網距離</label><strong>${fmt(route.distance_m)} m</strong></div><div><label>起點至路網</label><strong>${fmt(route.origin_snap_m)} m・未查核</strong></div><div><label>路網至名冊點位</label><strong>${fmt(route.destination_snap_m)} m・未查核</strong></div></div><div class="warning result">${esc(route.notice)}<br>${route.warnings.map(esc).join('<br>')}</div><ol class="public-route-steps">${route.steps.map((step) => `<li>沿 ${esc(step.name)} 約 ${fmt(step.distance_m)} m${step.stairs ? '（含樓梯）' : ''}</li>`).join('')}</ol><p class="hint">速度 ${route.speed_mps} m/s${route.speed_assumed ? '（模式預設）' : ''}；OSM 快照 ${esc(route.osm_timestamp)}。</p>${route.alternatives.length ? `<h3>其他路網可達選項</h3><div class="actions">${route.alternatives.map((s) => `<button class="btn small" data-alternative="${esc(s.id)}">${esc(s.name)} · ${fmt(s.minutes, 1)} 分</button>`).join('')}</div>` : ''}</div></div>`;
    $('public-route-result')
      .querySelectorAll('[data-alternative]')
      .forEach((b) => b.addEventListener('click', () => choose(b.dataset.alternative)));
  }
  for (const input of $('public-route-form').querySelectorAll('input,select')) {
    if (!['public-lat', 'public-lon'].includes(input.id))
      input.addEventListener('change', () => {
        const hadRoute = !!currentRoute;
        clearRoute();
        if (hadRoute) {
          notice('public-route-message', '需求或確認條件已變更，請重新計算路線。', true);
        }
      });
  }
  window.AnxingPublic = {
    show() {
      if (!initialized) init();
      else requestAnimationFrame(() => map?.invalidateSize());
    },
  };
  init();
})();
