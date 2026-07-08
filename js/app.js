'use strict';

// ===== STATE =====
const state = {
  filter: {
    keyword: '',
    location: '전체 지역',
    category: 'all',
    businessType: '전체 업종',
    maxPrice: 25000000000
  },
  sort: 'latest',
  listings: [],
  mapOpen: false
};

// ===== MAP STATE =====
let mainMap = null;
let mapMarkers = [];
let modalMapInstance = null;
let modalMapMarker = null;

// ===== MARKER COLORS =====
const MARKER_COLORS = {
  bisnis: '#CE1126',
  ruko:   '#2563eb',
  properti: '#16a34a'
};

function createMarkerIcon(color) {
  return L.divIcon({
    className: '',
    html: `<div style="
      width:20px;height:20px;border-radius:50%;
      background:${color};border:2px solid #fff;
      box-shadow:0 2px 6px rgba(0,0,0,.35);
    "></div>`,
    iconSize: [20, 20],
    iconAnchor: [10, 10]
  });
}

// ===== DOM REFS =====
const $ = id => document.getElementById(id);
const searchInput = $('searchInput');
const locationSelect = $('locationSelect');
const businessTypeSelect = $('businessTypeSelect');
const sortSelect = $('sortSelect');
const listingsGrid = $('listingsGrid');
const resultCount = $('resultCount');
const c2cWarning = $('c2cWarning');
const modalOverlay = $('modalOverlay');

// ===== INIT =====
async function init() {
  populateSelects();
  bindEvents();
  showLoading(true);
  const data = await fetchListings(state.filter);
  state.listings = data;
  showLoading(false);
  render();
  updateCatCounts();
  updateStatsBar();
  initMainMap();
}

function updateStatsBar() {
  const all = LISTINGS;
  const regions = new Set(all.map(l => l.location)).size;
  document.getElementById('statTotal').textContent = all.length;
  document.getElementById('statMa').textContent = all.filter(l => l.type === 'bisnis').length;
  document.getElementById('statProp').textContent = all.filter(l => l.type === 'properti' || l.type === 'ruko').length;
  document.getElementById('statC2c').textContent = all.filter(l => l.c2c).length;
  document.getElementById('statRegion').textContent = regions;
}

function showLoading(on) {
  const el = $('loadingSpinner');
  el.classList.toggle('visible', on);
  listingsGrid.style.display = on ? 'none' : '';
}

function populateSelects() {
  LOCATIONS.forEach(loc => {
    const label = LOCATION_KO[loc] || loc;
    [locationSelect, $('sidebarLocation')].forEach(sel => {
      const opt = document.createElement('option');
      opt.value = loc;
      opt.textContent = label;
      sel.appendChild(opt);
    });
  });

  BUSINESS_TYPES.forEach(bt => {
    const opt = document.createElement('option');
    opt.value = opt.textContent = bt;
    businessTypeSelect.appendChild(opt);
  });
}

// ===== MAP INIT =====
function initMainMap() {
  if (mainMap) return;
  mainMap = L.map('mainMap').setView([-6.2088, 106.8456], 10);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© OpenStreetMap contributors',
    maxZoom: 18
  }).addTo(mainMap);
  renderMapMarkers(state.listings);
}

function renderMapMarkers(listings) {
  mapMarkers.forEach(m => m.remove());
  mapMarkers = [];
  if (!mainMap) return;

  listings.forEach(l => {
    if (!l.lat || !l.lng) return;
    const color = MARKER_COLORS[l.type] || '#666';
    const marker = L.marker([l.lat, l.lng], { icon: createMarkerIcon(color) })
      .addTo(mainMap)
      .bindPopup(`
        <div style="min-width:180px;">
          <strong style="font-size:13px;">${escapeHtml(l.title)}</strong><br>
          <span style="color:#CE1126;font-weight:700;">${l.price}</span><br>
          <span style="font-size:12px;color:#666;">📍 ${l.locationKo || l.location}</span><br>
          <button onclick="openModal('${String(l.id).replace(/[^\w-]/g, '')}')" style="
            margin-top:8px;padding:5px 12px;background:#CE1126;
            color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:12px;
          ">상세보기</button>
        </div>
      `);
    marker.listingId = l.id;
    mapMarkers.push(marker);
  });
}

function flyToListing(listing) {
  if (!mainMap || !listing.lat) return;
  openMapPanel();
  mainMap.flyTo([listing.lat, listing.lng], 14, { duration: 1 });
  const marker = mapMarkers.find(m => m.listingId === listing.id);
  if (marker) marker.openPopup();
}

function openMapPanel() {
  if (!state.mapOpen) {
    $('mapPanel').classList.add('open');
    state.mapOpen = true;
    $('mapToggleBtn').textContent = '🗺️ 지도 닫기';
    setTimeout(() => mainMap && mainMap.invalidateSize(), 100);
  }
}

// ===== EVENTS =====
function bindEvents() {
  searchInput.addEventListener('input', debounce(() => {
    state.filter.keyword = searchInput.value.trim().toLowerCase();
    render();
  }, 250));

  locationSelect.addEventListener('change', () => {
    state.filter.location = locationSelect.value;
    $('sidebarLocation').value = locationSelect.value;
    render();
  });

  $('sidebarLocation').addEventListener('change', () => {
    state.filter.location = $('sidebarLocation').value;
    locationSelect.value = $('sidebarLocation').value;
    render();
  });

  businessTypeSelect.addEventListener('change', () => {
    state.filter.businessType = businessTypeSelect.value;
    render();
  });

  sortSelect.addEventListener('change', () => {
    state.sort = sortSelect.value;
    render();
  });

  $('searchBtn').addEventListener('click', render);

  document.querySelectorAll('.cat-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.cat-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.filter.category = btn.dataset.cat;
      render();
    });
  });

  $('resetBtn').addEventListener('click', resetFilters);

  $('priceRange').addEventListener('input', e => {
    state.filter.maxPrice = parseInt(e.target.value);
    $('priceDisplay').textContent = formatPriceLabel(state.filter.maxPrice);
    render();
  });

  modalOverlay.addEventListener('click', e => {
    if (e.target === modalOverlay) closeModal();
  });

  $('modalClose').addEventListener('click', closeModal);

  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeModal();
  });

  $('mapToggleBtn').addEventListener('click', () => {
    const panel = $('mapPanel');
    state.mapOpen = !state.mapOpen;
    panel.classList.toggle('open', state.mapOpen);
    $('mapToggleBtn').textContent = state.mapOpen ? '🗺️ 지도 닫기' : '🗺️ 지도 보기';
    if (state.mapOpen && !mainMap) {
      initMainMap();
    } else if (state.mapOpen && mainMap) {
      setTimeout(() => mainMap.invalidateSize(), 100);
    }
  });

  $('mapPanelClose').addEventListener('click', () => {
    $('mapPanel').classList.remove('open');
    state.mapOpen = false;
    $('mapToggleBtn').textContent = '🗺️ 지도 보기';
  });

  // 모달 탭
  document.querySelectorAll('.modal-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.modal-tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
      tab.classList.add('active');
      $('tab-' + tab.dataset.tab).classList.add('active');
      if (tab.dataset.tab === 'location' && modalMapInstance) {
        setTimeout(() => modalMapInstance.invalidateSize(), 100);
      }
    });
  });
}

// ===== FILTER & SORT =====
function getFiltered() {
  let results = [...state.listings];
  const { keyword, location, category, businessType, maxPrice } = state.filter;

  if (keyword) {
    results = results.filter(l =>
      (l.title || '').toLowerCase().includes(keyword) ||
      (l.location || '').toLowerCase().includes(keyword) ||
      (l.locationKo || '').toLowerCase().includes(keyword) ||
      (l.category || '').toLowerCase().includes(keyword) ||
      (l.description || '').toLowerCase().includes(keyword)
    );
  }

  if (location !== '전체 지역') {
    results = results.filter(l => l.location === location);
  }

  if (category !== 'all') {
    if (category === 'c2c') {
      results = results.filter(l => l.c2c);
    } else {
      results = results.filter(l => l.type === category);
    }
  }

  if (businessType !== '전체 업종') {
    results = results.filter(l => l.category === businessType);
  }

  results = results.filter(l => l.priceNum <= maxPrice);

  if (state.sort === 'price-asc') results.sort((a, b) => a.priceNum - b.priceNum);
  else if (state.sort === 'price-desc') results.sort((a, b) => b.priceNum - a.priceNum);
  else if (state.sort === 'revenue') {
    results.sort((a, b) => (b.monthlyRevenueNum || 0) - (a.monthlyRevenueNum || 0));
  } else {
    // id가 문자열(live-*)인 실시간 매물도 안전하게 정렬
    results.sort((a, b) => (Number(b.id) || 0) - (Number(a.id) || 0));
  }

  return results;
}

// ===== RENDER =====
function render() {
  const filtered = getFiltered();
  const hasc2c = filtered.some(l => l.c2c) && (state.filter.category === 'all' || state.filter.category === 'c2c');

  c2cWarning.classList.toggle('hidden', !hasc2c);
  resultCount.innerHTML = `총 <strong>${filtered.length}</strong>개 매물`;

  if (filtered.length === 0) {
    listingsGrid.innerHTML = `
      <div class="empty-state">
        <div class="icon">🔍</div>
        <p>검색 조건에 맞는 매물이 없습니다.<br>필터를 조정해 보세요.</p>
      </div>`;
    renderMapMarkers([]);
    return;
  }

  listingsGrid.innerHTML = filtered.map(renderCard).join('');

  listingsGrid.querySelectorAll('.btn-detail').forEach(btn => {
    if (btn.dataset.id) btn.addEventListener('click', () => openModal(btn.dataset.id));
  });

  listingsGrid.querySelectorAll('.btn-map').forEach(btn => {
    btn.addEventListener('click', () => {
      const listing = state.listings.find(l => String(l.id) === btn.dataset.id);
      if (listing) flyToListing(listing);
    });
  });

  renderMapMarkers(filtered);
}

function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function renderCard(l) {
  const badgeClass = l.c2c ? 'badge-c2c' : {
    akuisisi: 'badge-akuisisi',
    investor: 'badge-investor',
    jual: 'badge-jual',
    sewa: 'badge-sewa'
  }[l.subtype] || '';

  const stats = [];
  if (l.monthlyRevenue) stats.push(`<span class="stat-pill pill-revenue">월매출 ${l.monthlyRevenue}</span>`);
  if (l.profit) stats.push(`<span class="stat-pill pill-profit">이익률 ${l.profit}</span>`);
  if (l.area) stats.push(`<span class="stat-pill pill-area">${l.area}m²</span>`);
  if (l.floors) stats.push(`<span class="stat-pill pill-floor">${l.floors}층</span>`);

  const locationLabel = l.locationKo || l.location;

  return `
    <div class="card">
      <div class="card-emoji">
        ${l.images || '🏷️'}
        <span class="card-badge ${badgeClass}">${escapeHtml(l.badge)}</span>
        ${l.c2c ? '<span class="card-c2c-tag">직거래</span>' : ''}
      </div>
      <div class="card-body">
        <div class="card-category">${escapeHtml(l.category)}</div>
        <div class="card-title">${escapeHtml(l.title)}</div>
        <div class="card-location">📍 ${escapeHtml(locationLabel)}</div>
        <div class="card-stats">${stats.join('') || '<span class="stat-pill pill-area">상세정보 보기</span>'}</div>
        <div class="card-price">${escapeHtml(l.price)}</div>
        <div class="card-footer">
          <button class="btn-detail" data-id="${l.id}">상세보기</button>
          ${l.lat ? `<button class="btn-map" data-id="${l.id}" title="지도에서 보기" style="padding:9px 10px;border:1.5px solid var(--blue);border-radius:8px;background:#fff;color:var(--blue);cursor:pointer;font-size:14px;">🗺️</button>` : ''}
          ${l.sourceUrl ? `<a class="btn-detail" style="text-decoration:none;text-align:center;" href="${escapeHtml(l.sourceUrl)}" target="_blank" rel="noopener">원문 ↗</a>` : ''}
          ${!l.whatsapp ? '' : `<a class="btn-wa" href="https://wa.me/${l.whatsapp.replace('+', '')}" target="_blank" rel="noopener">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/></svg>
            문의
          </a>`}
        </div>
      </div>
    </div>`;
}

// ===== MODAL =====
function openModal(id) {
  const l = state.listings.find(x => String(x.id) === String(id));
  if (!l) return;

  // 기본정보 탭 초기화
  document.querySelectorAll('.modal-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  document.querySelector('.modal-tab[data-tab="info"]').classList.add('active');
  $('tab-info').classList.add('active');

  $('modalTitle').textContent = l.title;
  $('modalLocation').textContent = '📍 ' + (l.locationKo || l.location) + ' · ' + l.category;
  $('modalEmoji').textContent = l.images;
  $('modalPrice').textContent = l.price;
  $('modalDesc').textContent = l.description;

  const infoStats = [
    l.area ? { label: '면적', value: l.area + 'm²' } : null,
    l.floors ? { label: '층수', value: l.floors + '층' } : null,
    l.established ? { label: '설립연도', value: l.established + '년' } : null,
    { label: '위치', value: l.locationKo || l.location },
    { label: '거래유형', value: l.badge }
  ].filter(Boolean);

  $('modalStats').innerHTML = infoStats.map(s => `
    <div class="modal-stat">
      <div class="label">${s.label}</div>
      <div class="value">${escapeHtml(s.value)}</div>
    </div>`).join('');

  const financeStats = [
    l.monthlyRevenue ? { label: '월 매출', value: l.monthlyRevenue } : null,
    l.profit ? { label: '이익률', value: l.profit } : null,
    l.price ? { label: '인수가', value: l.price } : null
  ].filter(Boolean);

  $('modalFinanceStats').innerHTML = financeStats.length
    ? financeStats.map(s => `
      <div class="modal-stat">
        <div class="label">${s.label}</div>
        <div class="value">${s.value}</div>
      </div>`).join('')
    : '<p style="color:var(--gray-400);padding:20px;text-align:center;">재무 정보 없음 (부동산 매물)</p>';

  $('modalFacilities').innerHTML = (l.facilities || []).map(f =>
    `<span class="facility-tag">✓ ${escapeHtml(f)}</span>`).join('');

  const waBtn = $('modalWaBtn');
  if (l.whatsapp) {
    waBtn.style.display = '';
    waBtn.href = `https://wa.me/${l.whatsapp.replace('+', '')}`;
  } else if (l.sourceUrl) {
    waBtn.style.display = '';
    waBtn.href = l.sourceUrl;
    waBtn.target = '_blank';
  } else {
    waBtn.style.display = 'none';
  }

  const foreignWarning = $('foreignWarning');
  if (l.foreignNote) {
    foreignWarning.textContent = '🌏 외국인 안내: ' + l.foreignNote;
    foreignWarning.style.display = 'block';
  } else if (l.type === 'properti') {
    foreignWarning.textContent = '🌏 외국인 주의: 인도네시아 부동산 직접 소유 제한. PT PMA 법인 또는 Leasehold 방식 필요. 공증인(Notaris) 공증 필수.';
    foreignWarning.style.display = 'block';
  } else {
    foreignWarning.style.display = 'none';
  }

  $('modalLocationText').textContent = `📍 ${l.locationKo || l.location}${l.lat ? ` (${l.lat}, ${l.lng})` : ''}`;

  // 모달 미니맵
  if (l.lat && l.lng) {
    setTimeout(() => initModalMap(l), 150);
  } else {
    $('modalMap').innerHTML = '<p style="text-align:center;padding:40px;color:#9ca3af;">좌표 정보 없음</p>';
  }

  modalOverlay.classList.add('open');
  document.body.style.overflow = 'hidden';
}

function initModalMap(listing) {
  if (modalMapInstance) {
    modalMapInstance.remove();
    modalMapInstance = null;
  }
  modalMapInstance = L.map('modalMap').setView([listing.lat, listing.lng], 15);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© OpenStreetMap contributors',
    maxZoom: 18
  }).addTo(modalMapInstance);
  const color = MARKER_COLORS[listing.type] || '#666';
  L.marker([listing.lat, listing.lng], { icon: createMarkerIcon(color) })
    .addTo(modalMapInstance)
    .bindPopup(listing.locationKo || listing.location)
    .openPopup();
}

function closeModal() {
  modalOverlay.classList.remove('open');
  document.body.style.overflow = '';
  if (modalMapInstance) {
    modalMapInstance.remove();
    modalMapInstance = null;
  }
}

// ===== UTILITIES =====
function resetFilters() {
  state.filter = {
    keyword: '',
    location: '전체 지역',
    category: 'all',
    businessType: '전체 업종',
    maxPrice: 25000000000
  };
  searchInput.value = '';
  locationSelect.value = '전체 지역';
  $('sidebarLocation').value = '전체 지역';
  businessTypeSelect.value = '전체 업종';
  $('priceRange').value = 25000000000;
  $('priceDisplay').textContent = '전체';
  document.querySelectorAll('.cat-btn').forEach(b => b.classList.remove('active'));
  document.querySelector('.cat-btn[data-cat="all"]').classList.add('active');
  render();
}

function formatPriceLabel(val) {
  if (val >= 1000000000) return `Rp ${(val / 1000000000).toFixed(1)}M 이하`;
  if (val >= 1000000) return `Rp ${(val / 1000000).toFixed(0)}jt 이하`;
  return '전체';
}

function debounce(fn, delay) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

function updateCatCounts() {
  const counts = { all: state.listings.length, bisnis: 0, ruko: 0, properti: 0, c2c: 0 };
  state.listings.forEach(l => {
    if (l.type === 'bisnis') counts.bisnis++;
    else if (l.type === 'ruko') counts.ruko++;
    else if (l.type === 'properti') counts.properti++;
    if (l.c2c) counts.c2c++;
  });
  document.querySelectorAll('.cat-btn').forEach(btn => {
    const c = counts[btn.dataset.cat] || 0;
    const span = btn.querySelector('.count');
    if (span) span.textContent = c;
  });
}

document.addEventListener('DOMContentLoaded', init);
