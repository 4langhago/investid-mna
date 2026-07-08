/*
 * Supabase DB 연동 모듈
 * 사용 전 아래 두 값을 본인 Supabase 프로젝트 값으로 교체하세요.
 * Supabase 대시보드 → Settings → API 에서 확인
 */

const SUPABASE_URL = 'YOUR_SUPABASE_URL';
const SUPABASE_ANON_KEY = 'YOUR_SUPABASE_ANON_KEY';

/*
-- ============================================================
-- Supabase SQL Editor에서 아래 스키마를 실행하여 테이블을 생성하세요
-- ============================================================

CREATE TABLE listings (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  title TEXT NOT NULL,
  category TEXT NOT NULL,
  transaction_type TEXT,        -- 'akuisisi', 'investor', 'jual', 'sewa'
  price BIGINT,                 -- IDR
  monthly_revenue BIGINT,       -- IDR (M&A 매물)
  profit_margin DECIMAL,        -- % (M&A 매물)
  area_m2 DECIMAL,              -- 면적 (부동산)
  floors INTEGER,
  location TEXT,                -- 지역명 (영문)
  location_ko TEXT,             -- 지역명 (한국어)
  address TEXT,
  lat DECIMAL,
  lng DECIMAL,
  business_type TEXT,           -- 업종
  description TEXT,
  contact_wa TEXT,              -- WhatsApp 번호
  is_direct BOOLEAN DEFAULT false,
  is_featured BOOLEAN DEFAULT false,
  images TEXT[],
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- RLS 정책: 읽기는 공개, 쓰기는 로그인한 관리자만
ALTER TABLE listings ENABLE ROW LEVEL SECURITY;
CREATE POLICY "공개 읽기" ON listings FOR SELECT USING (true);
CREATE POLICY "관리자 등록" ON listings FOR INSERT TO authenticated WITH CHECK (true);
CREATE POLICY "관리자 수정" ON listings FOR UPDATE TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "관리자 삭제" ON listings FOR DELETE TO authenticated USING (true);

-- 관리자 계정 준비 (Supabase 대시보드에서):
-- 1. Authentication → Users → "Add user"로 관리자 이메일/비밀번호 생성
-- 2. Authentication → Sign In / Up → "Allow new users to sign up" 비활성화
--    (공개 가입을 막지 않으면 아무나 가입해서 authenticated 권한을 얻게 됨)

-- 샘플 데이터 예시 (기존 data.js 기반)
INSERT INTO listings (title, category, transaction_type, price, monthly_revenue, profit_margin, location, location_ko, lat, lng, business_type, description, contact_wa, is_direct)
VALUES
  ('스페셜티 카페 - 가딩 세르퐁', 'bisnis', 'akuisisi', 500000000, 80000000, 22, 'Gading Serpong', '가딩 세르퐁', -6.2311, 106.6200, '카페 & 레스토랑', '스페셜티 커피 카페. 좌석 40석.', '+6281234567001', false),
  ('루코 3층 - BSD 시티', 'ruko', 'jual', 4500000000, null, null, 'BSD', 'BSD', -6.3016, 106.6538, '루코', 'BSD 시티 핵심 상권 루코.', '+6281234567002', false);
*/

let supabaseClient = null;

// ── 로컬 저장 (Supabase 미설정 시 localStorage로 영속화) ──
const LS_KEY = 'mna_listings_v1';

(function loadLocalListings() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return;
    const arr = JSON.parse(raw);
    if (Array.isArray(arr) && arr.length) {
      LISTINGS.length = 0;
      Array.prototype.push.apply(LISTINGS, arr);
    }
  } catch (e) {
    console.warn('로컬 매물 데이터 로드 실패, 기본 데이터 사용:', e);
  }
})();

function persistLocalListings() {
  try {
    // 실시간 수집 매물(live-*)은 스크래퍼가 관리하므로 저장 대상에서 제외
    const own = LISTINGS.filter(l => String(l.id).indexOf('live-') !== 0);
    localStorage.setItem(LS_KEY, JSON.stringify(own));
  } catch (e) {
    console.warn('로컬 매물 데이터 저장 실패:', e);
  }
}

// ── 실시간 수집 매물 병합 (js/live_data.js — scraper/scrape_99co.py가 생성) ──
(function mergeLiveListings() {
  if (typeof LIVE_LISTINGS === 'undefined' || !Array.isArray(LIVE_LISTINGS)) return;
  for (let i = LISTINGS.length - 1; i >= 0; i--) {
    if (String(LISTINGS[i].id).indexOf('live-') === 0) LISTINGS.splice(i, 1);
  }
  const existing = new Set(LISTINGS.map(l => l.id));
  LIVE_LISTINGS.forEach(l => {
    if (!existing.has(l.id)) LISTINGS.push(l);
  });
})();

// ── DB(snake_case) ↔ 프론트 모델(camelCase) 매핑 ──
// 주의: DB 스키마의 category는 대분류(bisnis/ruko/properti), business_type이 업종.
// 프론트 모델은 type이 대분류, category가 업종으로 반대이므로 반드시 이 매핑을 거쳐야 함.
const DB_BADGE_MAP = { akuisisi: '완전인수', investor: '지분투자', jual: '매매', sewa: '임대' };

function formatIdrShort(v) {
  if (v == null) return null;
  if (v >= 1000000000) return 'Rp ' + (v / 1000000000).toFixed(1).replace('.', ',') + ' M';
  return 'Rp ' + Math.round(v / 1000000) + ' jt';
}

function mapDbRow(r) {
  if (!r) return r;
  if (r.priceNum !== undefined) return r; // 이미 프론트 모델 (로컬 데이터)
  return {
    id: r.id,
    type: r.category,
    subtype: r.transaction_type,
    title: r.title,
    category: r.business_type,
    location: r.location,
    locationKo: r.location_ko,
    address: r.address,
    price: formatIdrShort(r.price),
    priceNum: r.price,
    monthlyRevenue: formatIdrShort(r.monthly_revenue),
    monthlyRevenueNum: r.monthly_revenue,
    profit: r.profit_margin != null ? r.profit_margin + '%' : null,
    area: r.area_m2,
    floors: r.floors,
    description: r.description,
    whatsapp: r.contact_wa,
    c2c: !!r.is_direct,
    featured: !!r.is_featured,
    images: (Array.isArray(r.images) && r.images[0]) || '🏷️',
    facilities: [],
    badge: r.is_direct ? '직거래' : (DB_BADGE_MAP[r.transaction_type] || ''),
    established: null,
    lat: r.lat,
    lng: r.lng
  };
}

function toDbRow(l) {
  return {
    title: l.title,
    category: l.type,
    transaction_type: l.subtype,
    price: l.priceNum != null ? l.priceNum : null,
    monthly_revenue: l.monthlyRevenueNum != null ? l.monthlyRevenueNum : null,
    profit_margin: l.profit ? parseFloat(l.profit) : null,
    area_m2: l.area != null ? l.area : null,
    floors: l.floors != null ? l.floors : null,
    location: l.location,
    location_ko: l.locationKo,
    address: l.address || null,
    lat: l.lat != null ? l.lat : null,
    lng: l.lng != null ? l.lng : null,
    business_type: l.category || null,
    description: l.description || null,
    contact_wa: l.whatsapp,
    is_direct: !!l.c2c,
    is_featured: !!l.featured,
    images: l.images ? [l.images] : null
  };
}

function initSupabase() {
  if (SUPABASE_URL === 'YOUR_SUPABASE_URL' || SUPABASE_ANON_KEY === 'YOUR_SUPABASE_ANON_KEY') {
    return null;
  }
  try {
    supabaseClient = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
    return supabaseClient;
  } catch (e) {
    console.warn('Supabase 초기화 실패, 로컬 데이터를 사용합니다.', e);
    return null;
  }
}

// ── 관리자 인증 (Supabase Auth) ──
function isSupabaseConfigured() {
  return !!(supabaseClient || initSupabase());
}

async function signInAdmin(email, password) {
  const client = supabaseClient || initSupabase();
  if (!client) throw new Error('Supabase 미설정');
  const { data, error } = await client.auth.signInWithPassword({ email, password });
  if (error) throw error;
  return data.session;
}

async function signOutAdmin() {
  const client = supabaseClient || initSupabase();
  if (!client) return;
  await client.auth.signOut();
}

async function getAdminSession() {
  const client = supabaseClient || initSupabase();
  if (!client) return null;
  try {
    const { data } = await client.auth.getSession();
    return data.session || null;
  } catch (e) {
    return null;
  }
}

/**
 * 매물 목록 조회 (필터 적용)
 * @param {Object} filters - { location, category, maxPrice, businessType, keyword }
 * @returns {Promise<Array>} 매물 배열 (실패 시 LISTINGS fallback)
 */
async function fetchListings(filters = {}) {
  const client = supabaseClient || initSupabase();
  if (!client) return applyLocalFilters(filters);

  try {
    let query = client.from('listings').select('*');

    if (filters.location && filters.location !== '전체 지역') {
      query = query.eq('location', filters.location);
    }
    if (filters.category && filters.category !== 'all') {
      if (filters.category === 'c2c') {
        query = query.eq('is_direct', true);
      } else {
        query = query.eq('category', filters.category);
      }
    }
    if (filters.maxPrice) {
      query = query.lte('price', filters.maxPrice);
    }
    if (filters.businessType && filters.businessType !== '전체 업종') {
      query = query.eq('business_type', filters.businessType);
    }
    if (filters.keyword) {
      query = query.or(`title.ilike.%${filters.keyword}%,description.ilike.%${filters.keyword}%`);
    }

    const { data, error } = await query.order('created_at', { ascending: false });
    if (error) throw error;
    return (data || []).map(mapDbRow);
  } catch (e) {
    console.warn('DB 조회 실패, 로컬 데이터 사용:', e);
    return applyLocalFilters(filters);
  }
}

/**
 * 단건 매물 조회
 * @param {string|number} id
 */
async function fetchListingById(id) {
  const client = supabaseClient || initSupabase();
  if (!client) return LISTINGS.find(l => l.id == id) || null;

  try {
    const { data, error } = await client.from('listings').select('*').eq('id', id).single();
    if (error) throw error;
    return mapDbRow(data);
  } catch (e) {
    return LISTINGS.find(l => l.id == id) || null;
  }
}

/**
 * 매물 등록 (관리자용)
 * @param {Object} listingData
 */
async function insertListing(listingData) {
  const client = supabaseClient || initSupabase();
  if (!client) {
    listingData.id = listingData.id || Date.now();
    LISTINGS.push(listingData);
    persistLocalListings();
    return listingData;
  }

  const { data, error } = await client.from('listings').insert([toDbRow(listingData)]).select();
  if (error) throw error;
  const saved = mapDbRow(data[0]);
  LISTINGS.push(saved); // 화면 목록 동기화
  return saved;
}

/**
 * 매물 수정 (관리자용)
 * @param {string|number} id
 * @param {Object} listingData
 */
async function updateListing(id, listingData) {
  const client = supabaseClient || initSupabase();
  if (!client) {
    const idx = LISTINGS.findIndex(l => l.id == id);
    if (idx === -1) throw new Error('매물을 찾을 수 없습니다.');
    LISTINGS[idx] = { ...LISTINGS[idx], ...listingData };
    persistLocalListings();
    return LISTINGS[idx];
  }

  const { data, error } = await client.from('listings').update(toDbRow(listingData)).eq('id', id).select();
  if (error) throw error;
  const saved = mapDbRow(data[0]);
  const localIdx = LISTINGS.findIndex(l => l.id == id);
  if (localIdx !== -1) LISTINGS[localIdx] = { ...LISTINGS[localIdx], ...saved }; // 화면 목록 동기화
  return saved;
}

/**
 * 매물 삭제 (관리자용)
 * @param {string|number} id
 */
async function deleteListing(id) {
  const client = supabaseClient || initSupabase();
  if (!client) {
    const idx = LISTINGS.findIndex(l => l.id == id);
    if (idx === -1) throw new Error('매물을 찾을 수 없습니다.');
    LISTINGS.splice(idx, 1);
    persistLocalListings();
    return true;
  }

  const { error } = await client.from('listings').delete().eq('id', id);
  if (error) throw error;
  const localIdx = LISTINGS.findIndex(l => l.id == id);
  if (localIdx !== -1) LISTINGS.splice(localIdx, 1); // 화면 목록 동기화
  return true;
}

function applyLocalFilters(filters) {
  let results = [...LISTINGS];
  const { keyword, location, category, businessType, maxPrice } = filters;

  if (keyword) {
    const kw = keyword.toLowerCase();
    results = results.filter(l =>
      (l.title || '').toLowerCase().includes(kw) ||
      (l.location || '').toLowerCase().includes(kw) ||
      (l.category || '').toLowerCase().includes(kw) ||
      (l.description || '').toLowerCase().includes(kw)
    );
  }
  if (location && location !== '전체 지역') {
    results = results.filter(l => l.location === location);
  }
  if (category && category !== 'all') {
    if (category === 'c2c') {
      results = results.filter(l => l.c2c);
    } else {
      results = results.filter(l => l.type === category);
    }
  }
  if (businessType && businessType !== '전체 업종') {
    results = results.filter(l => l.category === businessType);
  }
  if (maxPrice) {
    results = results.filter(l => l.priceNum <= maxPrice);
  }
  return results;
}

initSupabase();
