const CHART_LABELS = ["6월","7월","8월","현재","2주후","3주후","4주후"];

const ITEMS = [
  {
    id:"flour", name:"밀가루", signal:"WAIT", prob:72, probKind:"하락",
    adviceShort:"구매 보류", adviceLong:"아직 담아두지 마세요 · 2~4주",
    forecast:"2,300~2,600원", reason:"국제 소매가 하락세로 인해 예상 (시카고 선물 −3.2%)",
    chart:{ actual:[2760,2690,2820,2690,null,null,null],
            center:[null,null,null,2690,2600,2520,2470],
            lower :[null,null,null,2690,2540,2440,2360],
            upper :[null,null,null,2690,2660,2600,2560] },
    retailers:[
      { name:"농협", price:2720 },
      { name:"이마트", price:2680 },
      { name:"롯데", price:2650, min:true,
        stores:[ {name:"롯데 사당점",price:2700}, {name:"롯데 동작점",price:2690},
                 {name:"롯데 신대방점",price:2650,min:true}, {name:"롯데 서울대입구점",price:2720} ] },
    ]
  },
  {
    id:"butter", name:"버터", signal:"BUY", prob:15, probKind:"상승",
    adviceShort:"대량 매수 추천", adviceLong:"지금이 살 때예요 · 저점 확보",
    forecast:"9,400~9,600원", reason:"추가 상승 가능성 낮음 — 지금 구매 권장",
    chart:{ actual:[9000,9200,9450,9600,null,null,null],
            center:[null,null,null,9600,9620,9640,9650],
            lower :[null,null,null,9600,9560,9560,9560],
            upper :[null,null,null,9600,9700,9740,9760] },
    retailers:[
      { name:"농협", price:9700 },
      { name:"이마트", price:9500, min:true,
        stores:[ {name:"이마트 사당점",price:9550}, {name:"이마트 용산점",price:9500,min:true},
                 {name:"이마트 영등포점",price:9620} ] },
      { name:"롯데", price:9600 },
    ]
  },
  {
    id:"sugar", name:"설탕", signal:"WAIT", prob:70, probKind:"하락",
    adviceShort:"안전재고만 유지", adviceLong:"당분간 관망 · 필요분만",
    forecast:"2,450~2,650원", reason:"성수기 종료로 하락 흐름 예상",
    chart:{ actual:[2620,2700,2680,2680,null,null,null],
            center:[null,null,null,2680,2620,2580,2560],
            lower :[null,null,null,2680,2560,2500,2470],
            upper :[null,null,null,2680,2690,2680,2680] },
    retailers:[
      { name:"농협", price:2560, min:true,
        stores:[ {name:"농협 사당점",price:2600}, {name:"농협 신대방점",price:2560,min:true},
                 {name:"농협 동작점",price:2620} ] },
      { name:"이마트", price:2680 },
      { name:"롯데", price:2650 },
    ]
  },
  {
    id:"egg", name:"계란", signal:"WAIT", prob:58, probKind:"하락",
    adviceShort:"구매 보류", adviceLong:"조금 기다려보세요 · 1~2주",
    forecast:"6,800~7,200원", reason:"공급 회복으로 소폭 하락 예상",
    chart:{ actual:[6800,7100,7050,7200,null,null,null],
            center:[null,null,null,7200,7050,6950,6900],
            lower :[null,null,null,7200,6900,6800,6750],
            upper :[null,null,null,7200,7200,7150,7120] },
    retailers:[
      { name:"농협", price:7150 },
      { name:"이마트", price:6900, min:true,
        stores:[ {name:"이마트 사당점",price:6900,min:true}, {name:"이마트 용산점",price:6950},
                 {name:"이마트 영등포점",price:7000} ] },
      { name:"롯데", price:7050 },
    ]
  },
  {
    id:"milk", name:"우유", signal:"BUY", prob:22, probKind:"상승",
    adviceShort:"지금 구매 적기", adviceLong:"지금이 살 때예요",
    forecast:"2,750~2,900원", reason:"원유가 인상 예정 — 인상 전 확보 권장",
    chart:{ actual:[2800,2850,2870,2890,null,null,null],
            center:[null,null,null,2890,2920,2950,2970],
            lower :[null,null,null,2890,2880,2900,2910],
            upper :[null,null,null,2890,2960,3000,3030] },
    retailers:[
      { name:"농협", price:2900 },
      { name:"이마트", price:2750, min:true,
        stores:[ {name:"이마트 사당점",price:2750,min:true}, {name:"이마트 용산점",price:2790},
                 {name:"이마트 영등포점",price:2830} ] },
      { name:"롯데", price:2880 },
    ]
  },
];
const byId = id => ITEMS.find(x => x.id === id);

// 발주 리스트 상태 유지를 위한 localStorage 키
const CART_KEY = "costradar_cart_v1";
function loadCart(){
  try {
    const raw = localStorage.getItem(CART_KEY);
    if (raw) return JSON.parse(raw);
  } catch(e){ console.warn("장바구니 불러오기 실패:", e); }
  return []; 
}
function saveCart(){
  try { localStorage.setItem(CART_KEY, JSON.stringify(myOrder)); }
  catch(e){ console.warn("장바구니 저장 실패:", e); }
}
let myOrder = loadCart();

// 발주 리스트 수량 배지 갱신
function updateCartBadge(){
  const n = myOrder.reduce((sum,o) => sum + o.qty, 0);
  ["cartBadgeTop","cartBadgeSide"].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = n;
    el.hidden = n === 0;
  });
}

// 백엔드 미연결 시 목업 예측 가격 계산에 사용하는 보정 계수
ITEMS.forEach(it => it.fcFactor = it.probKind === "하락" ? +(1 - it.prob/1000).toFixed(3) : 1.01);
// 예측 기간: 우유·계란은 단기 품목이라 1-2주, 나머지는 장기 품목이라 2-4 
ITEMS.forEach(it => it.horizon = (it.id === "milk" || it.id === "egg") ? "1~2주" : "2~4주");
// 구매 모드에 따른 품목별 브랜드 가격 반환
function priceOf(item, brandName, mode){
  const r = item.retailers.find(x => x.name === brandName);
  if (!r) return null;
  return mode === "forecast" ? Math.round(r.price * item.fcFactor) : r.price;
}

// CSS 변수 기반 차트 색상 설정
const css = getComputedStyle(document.documentElement);
const C = {
  actual: css.getPropertyValue("--chart-actual").trim(),
  green:  css.getPropertyValue("--green").trim(),
  amber:  css.getPropertyValue("--amber").trim(),
  greenBand: css.getPropertyValue("--green-soft").trim(),
  amberBand: css.getPropertyValue("--amber-soft").trim(),
  grid:   css.getPropertyValue("--line").trim(),
  muted:  css.getPropertyValue("--muted").trim(),
};
const won = n => n.toLocaleString("ko-KR") + "원";

let currentItem = "flour";
function go(view){
  ["home","signal","detail","order"].forEach(v =>
    document.getElementById("view-"+v).hidden = (v !== view));
  window.scrollTo(0,0);
}
function toast(msg){
  const t = document.getElementById("toast");
  t.textContent = msg; t.classList.add("show");
  clearTimeout(t._t); t._t = setTimeout(()=>t.classList.remove("show"), 1800);
}

//홈화면 렌더링
function renderHome(){
  document.getElementById("signalGrid").innerHTML = ITEMS.map(it => {
    // 구매 신호별 스타일 클래스 설정
    const sigClass = it.signal === "BUY" ? "buy" : it.signal === "WAIT" ? "wait" : "hold";
    const colorClass = it.signal === "BUY" ? "down" : it.signal === "WAIT" ? "up" : "neu";
    return `
      <div class="signal-card" onclick="showDetail('${it.id}')">
        <div class="row1"><span class="nm">${it.name}</span>
          <span class="sig ${sigClass}">${it.signal}</span></div>
        <div class="advice">AI 의사결정 제안 · ${it.adviceShort} · ${it.horizon} 예측</div>
        <div class="prob ${colorClass}">가격 ${it.probKind} 확률 ${it.prob}%</div>
      </div>`;
  }).join("");
}

// 품목 상세 화면 렌더링
let detailChart = null;
function showDetail(id){
  currentItem = id;
  const it = byId(id);
  go("detail");
  document.getElementById("detailTabs").innerHTML = ITEMS.map(x =>
    `<button class="${x.id===id?"on":""}" onclick="showDetail('${x.id}')">${x.name}</button>`).join("");
  renderDetailScreen(it);          // 초기 데이터로 우선 렌더링
  if(!USE_MOCK) enhanceDetail(it);  // 백엔드 응답으로 상세 데이터 갱신
}

// 상세 화면의 구매 신호, 판매처 시세 및 차트 렌더링
function renderDetailScreen(it){
  const sig = it.signal;
  document.getElementById("bigsig").className = "bigsig " + sig.toLowerCase();
  document.getElementById("bigsig").innerHTML = `
    <div class="top"><span>${it.name} 구매 신호</span><span>${it.horizon} 내 하락 확률</span></div>
    <div class="mid">
      <div class="word">${sig}</div>
      <div class="prob">${it.prob}%<small>예측가 ${it.forecast}</small></div>
    </div>
    <div class="sub"><span>${it.adviceLong}</span></div>
    <div class="reason">${it.reason}</div>
    <button class="cta" id="ctaAdd">발주에 담기</button>
    ${sig==="WAIT" ? `<button class="cta2" id="ctaAlert">가격 내려가면 알림 받기</button>` : ""}`;
  document.getElementById("ctaAdd").onclick = () => addToOrder(it.id);
  const alertBtn = document.getElementById("ctaAlert");
  if (alertBtn) alertBtn.onclick = () => toast("알림을 설정했어요 🔔");

 // 실데이터가 있으면 liveQuotes를 사용하고, 없으면 목업 시세 사용
  const quotes = it.liveQuotes || it.retailers.map(r => ({ name:r.name, price:r.price, min:r.min }));
  document.getElementById("quoteDate").textContent =
    (it._predict && it._predict.survey_date) ? `${it._predict.survey_date} 기준` : "최근 조사일 기준";
  document.getElementById("quoteList").innerHTML = quotes.map(r => `
    <div class="quote">
      <span class="qn">${r.name}${r.min?'<span class="min">최저</span>':""}</span>
      <span class="qp">${won(r.price)}</span>
    </div>`).join("");

  renderDetailChart(it);
}

// 예측·시세 API 응답을 상세 화면에 반영
async function enhanceDetail(it){
  try{
    const [pred, hist] = await Promise.allSettled([ fetchPredict(it.name), fetchHistory(it.name) ]);
    if(pred.status === "fulfilled") adaptPredict(pred.value, it);
    if(hist.status === "fulfilled") applyHistoryToItem(it, hist.value);
    DATA_LIVE = true; updateBanner();
    if(currentItem === it.id) renderDetailScreen(it);
  }catch(e){ console.warn("상세 API 폴백:", e); }
}
// 발주 API 기준 브랜드명 매핑
// 발주 화면과의 일관성을 위해 대상 판매처를 농협·이마트·롯데로 제한
const BASKET_BRAND_MAP = {
  "(주)농협유통": "농협", "(주)농협하나로유통": "농협",
  "이마트": "이마트",
  "롯데슈퍼": "롯데",
};

// /prices/history 응답을 판매처 시세와 차트 데이터로 변환
function applyHistoryToItem(it, hist){
  const labels = hist.period_labels || hist.survey_dates || [];
  const n = labels.length;
  const brands = hist.brands || [];
  if(!n || !brands.length) return;
 // 시장 평균 추이를 위해 날짜별 전체 브랜드 평균 가격 계산
  const mean = [];
  for(let i=0;i<n;i++){
    const col = brands.map(b => b.prices[i]).filter(v => v != null);
    mean.push(col.length ? Math.round(col.reduce((a,c)=>a+c,0)/col.length) : null);
  }
 // 대상 브랜드별 최신 최저가 구성
  const byBrand = {};
  brands.forEach(b => {
    const disp = BASKET_BRAND_MAP[b.brand];
    if(!disp || b.latest_price == null) return;
    if(!(disp in byBrand) || b.latest_price < byBrand[disp]) byBrand[disp] = b.latest_price;
  });
  it.liveQuotes = Object.entries(byBrand).map(([name,price]) => ({ name, price }))
                        .sort((a,b)=>a.price-b.price);
  if(it.liveQuotes.length) it.liveQuotes[0].min = true;
  // 과거 평균 시세와 2주 예측값으로 차트 구성
  const p = it._predict;
  const lastIdx = mean.reduce((acc,v,i)=> v!=null?i:acc, -1);
  const L = labels.concat(["2주 후"]);
  const actual = mean.concat([null]);
  const center = new Array(L.length).fill(null);
  const lower  = new Array(L.length).fill(null);
  const upper  = new Array(L.length).fill(null);
  if(p && lastIdx >= 0){
    center[lastIdx] = mean[lastIdx]; center[L.length-1] = p.predicted_price_2weeks;
    lower[lastIdx]  = mean[lastIdx]; lower[L.length-1]  = p.pred_low;
    upper[lastIdx]  = mean[lastIdx]; upper[L.length-1]  = p.pred_high;
  }
  it.liveChart = { labels:L, actual, center, lower, upper };
}

// 예측구간의 극단값으로 차트가 과도하게 축소되지 않도록
// 실제·예측 중심값을 기준으로 y축 범위를 계산
function niceAxisRange(values){
  const v = values.filter(x => x != null);
  if(!v.length) return { min:undefined, max:undefined };
  let min = Math.min(...v), max = Math.max(...v);
  if(min === max){ min -= min*0.05; max += max*0.05; }
  const pad = Math.max((max - min) * 0.2, max * 0.01, 10);
  const step = max >= 10000 ? 100 : max >= 1000 ? 50 : 10;
  return {
    min: Math.max(0, Math.floor((min - pad) / step) * step),
    max: Math.ceil((max + pad) / step) * step
  };
}

function renderDetailChart(it){
  const errEl = document.getElementById("chartError");
  // Chart.js 로드 실패 시 안내 문구 표시
  if (typeof Chart === "undefined"){
    if (errEl) errEl.hidden = false;
    return;
  }
  if (errEl) errEl.hidden = true;
  // 실데이터 차트를 우선 사용하고 없으면 목업 차트 사용
  const ch = it.liveChart || { labels:CHART_LABELS, actual:it.chart.actual, center:it.chart.center, lower:it.chart.lower, upper:it.chart.upper };
  const fColor = it.signal==="BUY" ? C.green : it.signal==="WAIT" ? C.amber : C.muted;
  const fBand  = it.signal==="BUY" ? C.greenBand : it.signal==="WAIT" ? C.amberBand : "rgba(139,151,164,.15)";
  const data = {
    labels: ch.labels,
    datasets:[
      { label:"실제", data:ch.actual, borderColor:C.actual, backgroundColor:C.actual,
        borderWidth:2.5, tension:.25, pointRadius:3, pointHoverRadius:6, spanGaps:false },
      { label:"예측", data:ch.center, borderColor:fColor, borderDash:[5,4],
        borderWidth:2, tension:.25, pointRadius:0 },
      { label:"하한", data:ch.lower, borderColor:"transparent", pointRadius:0, fill:false },
      { label:"상한", data:ch.upper, borderColor:"transparent", pointRadius:0,
        backgroundColor:fBand, fill:"-1" },
    ]
  };
  // 실제·예측 중심값을 기준으로 y축 범위 설정
  const yRange = niceAxisRange([...ch.actual, ...ch.center]);
  const options = {
    responsive:true, maintainAspectRatio:false,
    interaction:{ mode:"index", intersect:false },
    plugins:{ legend:{display:false},
      tooltip:{ callbacks:{ label:c => c.parsed.y==null?null:`${c.dataset.label}: ${won(c.parsed.y)}` } } },
    scales:{
      x:{ grid:{color:C.grid}, ticks:{color:C.muted} },
      y:{ min:yRange.min, max:yRange.max, grid:{color:C.grid},
          ticks:{color:C.muted, callback:v=>v.toLocaleString("ko-KR")} }
    }
  };
  if (detailChart) detailChart.destroy();
  detailChart = new Chart(document.getElementById("detailChart"), {type:"line", data, options});
}

// 품목을 발주 리스트에 추가하고 발주 화면으로 이동
function addToOrder(itemId){
  if (!myOrder.find(o => o.itemId === itemId)) myOrder.push({ itemId, qty:1 });
  saveCart(); updateCartBadge();
  toast(`${byId(itemId).name} 발주에 담았어요`);
  showOrder();
}

// 발주 리스트: 브랜드별 합산 가격 TOP 3
const BRANDS = ["농협","이마트","롯데"];
let orderMode = "today";

// 백엔드 API 연결 설정
const API_BASE = "http://127.0.0.1:8000";
let USE_MOCK = false;   
let DATA_LIVE = false;   // 실제 API 응답 여부

async function apiGet(path){
  const res = await fetch(API_BASE + path);
  if(!res.ok) throw new Error("GET " + path + " → " + res.status);
  return res.json();
}
async function apiPost(path, body){
  const res = await fetch(API_BASE + path, {
    method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)
  });
  if(!res.ok) throw new Error("POST " + path + " → " + res.status);
  return res.json();
}

// API 요청 함수
// 홈 신호: GET /signals
async function fetchSignals(){ return apiGet("/signals"); }
// 가격 추이: GET /prices/history
async function fetchHistory(itemName, brand){
  const q = new URLSearchParams({ item_name: itemName });
  if (brand) q.set("brand", brand);
  return apiGet("/prices/history?" + q.toString());
}
// 발주 브랜드 TOP 3: POST /optimize/basket
async function fetchQuote(items, mode){
  return apiPost("/optimize/basket", {
    items: items.map(o => ({ item_name: byId(o.itemId).name, quantity: o.qty })),
    mode: mode   // "today"(오늘) 또는 "forecast"(2주 예측)
  });
}
// 상세 예측: POST /predict  { item_name, brand? }
async function fetchPredict(itemName){ return apiPost("/predict", { item_name: itemName }); }

// API 응답을 화면에서 사용하는 데이터 구조로 변환
function adaptQuote(raw){
  const arr = (raw && raw.brands) || [];
  if(!arr.length) throw new Error("발주 응답 형식 불명");
  return arr.map(r => ({
    brand: r.brand,
    total: r.total,
    lines: (r.items || []).map(l => ({ name: l.item_name, qty: l.quantity, unit: l.unit_price }))
  }));   

// /signals 응답: { as_of, items:[{item_name,current_price,signal:"BUY"|"WAIT"|"HOLD",message,drop_probability}] }
const ADVICE_BY_SIGNAL = { BUY:"지금 구매 추천", WAIT:"구매 보류", HOLD:"관망" };
function applySignals(raw){
  const arr = (raw && raw.items) || [];
  let hit = 0;
  arr.forEach(s => {
    const it = ITEMS.find(x => x.name === s.item_name);
    if(!it) return;
    it.signal = String(s.signal || "").toUpperCase();
    it.prob = Math.round((s.drop_probability ?? 0) * 100);
    it.probKind = "하락";                       // /signals는 drop_probability(하락 확률)만 줌
    it.adviceShort = ADVICE_BY_SIGNAL[it.signal] || it.adviceShort;
    it.adviceLong = s.message || it.adviceLong;
    it.reason = s.message || it.reason;
    if(s.current_price != null) it.current = s.current_price;
    hit++;
  });
  if(!hit) throw new Error("신호 응답 형식 불명");
}

// /predict 응답 → 상세 큰 신호 카드에 반영
function adaptPredict(raw, it){
  if(!raw || raw.signal == null) throw new Error("예측 응답 형식 불명");
  it.signal = String(raw.signal).toUpperCase();
  it.prob = Math.round((raw.drop_probability ?? 0) * 100);
  it.reason = raw.message || it.reason;
  it.adviceLong = raw.message || it.adviceLong;
  if(raw.pred_low != null && raw.pred_high != null)
    it.forecast = `${raw.pred_low.toLocaleString("ko-KR")}~${raw.pred_high.toLocaleString("ko-KR")}원`;
  it._predict = raw;   // 차트에서 예측 지점으로 사용
}

// 데이터 출처 배너 갱신 
function updateBanner(){
  const el = document.getElementById("dsBanner");
  if(!el) return;
  if(DATA_LIVE){ el.className = "ds live"; el.textContent = "🟢 실시간 데이터 (백엔드 연결됨)"; }
  else { el.className = "ds mock"; el.textContent = USE_MOCK
      ? "🟡 예시 데이터 (시연 모드)" : "🟡 예시 데이터 (백엔드 미연결 — 자동 폴백)"; }
}

// 초기 로드 시 구매 신호 API 반영
async function hydrate(){
  if(!USE_MOCK){
    try { applySignals(await fetchSignals()); DATA_LIVE = true; renderHome(); }
    catch(e){ console.warn("신호 API 폴백:", e); }
  }
  updateBanner();
}

function showOrder(){ go("order"); renderOrder(); }

// 백엔드 미연결 시 목업 데이터로 브랜드별 합산 순위 계산
function mockRanked(){
  return BRANDS.map(brand => {
    let total = 0;
    const lines = myOrder.map(o => {
      const it = byId(o.itemId);
      const unit = priceOf(it, brand, orderMode);
      total += unit * o.qty;
      return { name: it.name, qty: o.qty, unit };
    });
    return { brand, total, lines };
  }).sort((a,b) => a.total - b.total);
}

async function renderOrder(){
  document.getElementById("orderCount").textContent = `${myOrder.length}개 품목 · 수량 조절 가능`;

  // 선택한 발주 품목 렌더링
  document.getElementById("myOrderList").innerHTML = myOrder.map((o,i) => `
      <div class="oline">
        <div class="l1"><span class="oi">${byId(o.itemId).name}</span>
          <span class="stepper">
            <button onclick="changeQty(${i},-1)">−</button>
            <span class="num">${o.qty}개</span>
            <button onclick="changeQty(${i},1)">+</button>
          </span>
          <button class="remove-btn" onclick="removeFromOrder(${i})" title="삭제">✕</button>
        </div>
      </div>`).join("") || `<p class="muted" style="font-size:.85rem;">담은 품목이 없어요. 아래에서 추가해보세요.</p>`;

  document.getElementById("recSub").textContent =
    `${orderMode==="today"?"최근 조사가 기준":"2주 예측 기준"} · 한 브랜드에서 전부 구매할 때 합산 총액`;

  // 발주 최적화 API 호출, 실패 시 목업 데이터로 폴백
  let ranked;
  if (!USE_MOCK && myOrder.length){
    document.getElementById("recList").innerHTML = `<p class="muted" style="font-size:.85rem;">불러오는 중…</p>`;
    try { ranked = adaptQuote(await fetchQuote(myOrder, orderMode)); DATA_LIVE = true; }
    catch(e){ console.warn("발주 API 폴백:", e); ranked = mockRanked(); }
  } else {
    ranked = mockRanked();
  }
  updateBanner();

  document.getElementById("recList").innerHTML = ranked.map((r,i) => {
    const lineText = r.lines.map(l => `${l.name}×${l.qty}(${won(l.unit)})`).join(" · ");
    return `
      <div class="brank ${i===0?"best":""}">
        <div class="r1">
          <span class="rk">${i+1}</span>
          <span class="bn">${r.brand}${i===0?'<span class="min">최저</span>':""}</span>
          <span class="tt">${won(r.total)}</span>
        </div>
        <div class="ll">${lineText}</div>
      </div>`;
  }).join("");

  // 최고가 대비 절감액 표시
  const savingsBadge = document.getElementById("savingsBadge");
  if (savingsBadge){
    const worst = ranked.length ? ranked[ranked.length-1].total : 0;
    const best = ranked.length ? ranked[0].total : 0;
    const savings = worst - best;
    if (ranked.length >= 2 && savings > 0){
      savingsBadge.textContent = `최고가 대비 -${won(savings)} 절감`;
      savingsBadge.hidden = false;
    } else {
      savingsBadge.hidden = true;
    }
  }

  document.getElementById("orderBuyBtn").textContent = `${ranked[0].brand}에서 구매 (${won(ranked[0].total)})`;
}

function changeQty(i, d){
  myOrder[i].qty = Math.max(1, myOrder[i].qty + d);
  saveCart(); updateCartBadge();
  renderOrder();
}

// 발주 품목 삭제
function removeFromOrder(i){
  const removed = myOrder[i];
  if (!removed) return;
  myOrder.splice(i, 1);
  saveCart(); updateCartBadge();
  toast(`${byId(removed.itemId).name} 삭제했어요`);
  renderOrder();
}

// 발주 가격 기준 모드 전환
document.getElementById("orderMode").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  orderMode = b.dataset.mode;
  document.querySelectorAll("#orderMode button").forEach(x => x.classList.toggle("on", x === b));
  renderOrder();
});

renderHome();
go("home");
updateCartBadge();  
hydrate();// 초기 API 데이터 반영
