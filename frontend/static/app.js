/* =====================================================================
   [1] 가짜 데이터 (MOCK_DB)  ★ 나중에 여기만 백엔드/AI로 교체 ★
   brands = 농협 / 이마트 / 롯데 (회의 확정)
   숫자는 전부 예시예요.
===================================================================== */
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

/* 발주 리스트 초기 데이터 (사장님이 담아둔 상태 예시) — 이제 품목+수량만 (지점 안 쪼갬) */
let myOrder = [
  { itemId:"flour", qty:2 },
  { itemId:"egg",   qty:1 },
  { itemId:"milk",  qty:2 },
];

/* 오늘/예측 모드 → 예측가는 today 가격에 fcFactor를 곱해 계산 (실제 API는 값을 직접 줌).
   하락 신호 품목은 예측이 더 싸고(<1), 상승 신호 품목은 조금 오름(>1). */
ITEMS.forEach(it => it.fcFactor = it.probKind === "하락" ? +(1 - it.prob/1000).toFixed(3) : 1.01);
/* 예측 기간: 우유·계란은 유통기한이 짧아 1~2주, 나머지는 2~4주 (팀 결정) */
ITEMS.forEach(it => it.horizon = (it.id === "milk" || it.id === "egg") ? "1~2주" : "2~4주");
/* 특정 품목/브랜드의 가격을 모드에 맞게 돌려줌 */
function priceOf(item, brandName, mode){
  const r = item.retailers.find(x => x.name === brandName);
  if (!r) return null;
  return mode === "forecast" ? Math.round(r.price * item.fcFactor) : r.price;
}

/* =====================================================================
   [2] 테마 색을 Chart.js에 넘기기
===================================================================== */
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

/* =====================================================================
   [3] 화면 전환 함수 (버튼 누르면 이 함수로 화면 이동)
===================================================================== */
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

/* =====================================================================
   [4] 홈 화면 그리기
===================================================================== */
function renderHome(){
  // 신호 카드는 전체 품목 (각 품목마다 구매 타이밍 신호가 있음)
  document.getElementById("signalGrid").innerHTML = ITEMS.map(it => {
    // 신호 3종: BUY=초록 / WAIT=노랑 / HOLD=회색 (배지·글씨색 통일)
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
  // 품목 바로가기는 전체 5개
  document.getElementById("itemGrid").innerHTML = ITEMS.map(it => `
      <div class="item-card" onclick="showDetail('${it.id}')">
        <div class="ic"></div><div class="nm">${it.name}</div>
      </div>`).join("");
}

/* =====================================================================
   [5] 품목 상세 화면 그리기
===================================================================== */
let detailChart = null;
function showDetail(id){
  currentItem = id;
  const it = byId(id);
  go("detail");
  // 상단 품목 탭
  document.getElementById("detailTabs").innerHTML = ITEMS.map(x =>
    `<button class="${x.id===id?"on":""}" onclick="showDetail('${x.id}')">${x.name}</button>`).join("");
  renderDetailScreen(it);          // 먼저 (가짜 or 기존) 데이터로 그림
  if(!USE_MOCK) enhanceDetail(it);  // 백엔드 연결 시 실제 예측/시세로 덮어씀
}

// 상세 화면 본문 그리기 (신호 카드 + 판매처 시세 + 차트) — 신호 3종 BUY/WAIT/HOLD 지원
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

  // 판매처별 시세 (실시간이면 liveQuotes, 아니면 가짜 retailers)
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

// 상세 실시간 연결: /predict(예측) + /prices/history(시세) → it 갱신 후 다시 그림
async function enhanceDetail(it){
  try{
    const [pred, hist] = await Promise.allSettled([ fetchPredict(it.name), fetchHistory(it.name) ]);
    if(pred.status === "fulfilled") adaptPredict(pred.value, it);
    if(hist.status === "fulfilled") applyHistoryToItem(it, hist.value);
    DATA_LIVE = true; updateBanner();
    if(currentItem === it.id) renderDetailScreen(it);
  }catch(e){ console.warn("상세 API 폴백:", e); }
}

// /prices/history 응답 → 판매처 시세 + 차트(과거 실제 평균 + 예측 지점)로 변환
function applyHistoryToItem(it, hist){
  const labels = hist.period_labels || hist.survey_dates || [];
  const n = labels.length;
  const brands = hist.brands || [];
  if(!n || !brands.length) return;
  // 날짜별 브랜드 평균 = 실제 가격 선
  const mean = [];
  for(let i=0;i<n;i++){
    const col = brands.map(b => b.prices[i]).filter(v => v != null);
    mean.push(col.length ? Math.round(col.reduce((a,c)=>a+c,0)/col.length) : null);
  }
  // 판매처별 시세 = 브랜드별 최근가 (싼 순)
  it.liveQuotes = brands.map(b => ({ name:b.brand, price:b.latest_price }))
                        .filter(q => q.price != null).sort((a,b)=>a.price-b.price);
  if(it.liveQuotes.length) it.liveQuotes[0].min = true;
  // 차트 = 과거 실제(평균) + 예측 지점(/predict)
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

function renderDetailChart(it){
  // 실시간 차트(liveChart)가 있으면 그걸, 없으면 가짜(chart) 사용
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
  const options = {
    responsive:true, maintainAspectRatio:false,
    interaction:{ mode:"index", intersect:false },
    plugins:{ legend:{display:false},
      tooltip:{ callbacks:{ label:c => c.parsed.y==null?null:`${c.dataset.label}: ${won(c.parsed.y)}` } } },
    scales:{
      x:{ grid:{color:C.grid}, ticks:{color:C.muted} },
      y:{ grid:{color:C.grid}, ticks:{color:C.muted, callback:v=>v.toLocaleString("ko-KR")} }
    }
  };
  if (detailChart) detailChart.destroy();
  detailChart = new Chart(document.getElementById("detailChart"), {type:"line", data, options});
}

/* 판매처/품목 상세에서 '발주에 담기' → 품목을 발주에 추가하고 발주 리스트로 이동
   (지점 안 쪼갬 → 품목+수량만 담아요) */
function addToOrder(itemId){
  if (!myOrder.find(o => o.itemId === itemId)) myOrder.push({ itemId, qty:1 });
  toast(`${byId(itemId).name} 발주에 담았어요`);
  showOrder();
}

/* =====================================================================
   [7] 발주 리스트 화면 — 브랜드별 합산 TOP 3 (백엔드 API 변경 반영)
      · today 모드 = 오늘 구매 / forecast 모드 = 2주 예측
      · 농협·이마트·롯데 각각 '전부 이 브랜드에서' 살 때 합산 총액을 구해 싼 순서로.
   실제 API 예: POST /order/recommend { items:[{id,qty}], mode:"today"|"forecast" }
              → [ {brand, total, lines:[{name,qty,unitPrice}]}, ... ] (싼 순 정렬)
===================================================================== */
const BRANDS = ["농협","이마트","롯데"];
let orderMode = "today";

/* =====================================================================
   [API] 백엔드 연결 층  ★ 실제 서버와 연결되는 부분 ★
   ---------------------------------------------------------------------
   · USE_MOCK = true  → 가짜 데이터로만 동작 (백엔드 없이 시연/발표용)
   · USE_MOCK = false → 실제 API 시도, 실패하면 자동으로 가짜 데이터로 폴백
   백엔드를 로컬에서 켜고( 예: uvicorn ... ) 아래를 false 로 바꾸면 연결돼요.
   기본 주소는 http://127.0.0.1:8000 (Swagger 화면 기준)
===================================================================== */
const API_BASE = "http://127.0.0.1:8000";
let USE_MOCK = true;    // ← 백엔드 연결할 때 false 로 변경
let DATA_LIVE = false;  // 실제 API 응답을 받았는지 (배너 표시용)

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

/* --- 요청 함수 (요청 형식은 Swagger 기준으로 정확히 맞춤) --- */
// 홈 신호: GET /signals
async function fetchSignals(){ return apiGet("/signals"); }
// 가격 추이: GET /prices/history?item_name=밀가루&brand=이마트  (brand 없으면 브랜드 평균)
async function fetchHistory(itemName, brand){
  const q = new URLSearchParams({ item_name: itemName });
  if (brand) q.set("brand", brand);
  return apiGet("/prices/history?" + q.toString());
}
// 발주 브랜드 TOP 3: POST /optimize/basket  { items:[{item_name,quantity}], mode }
async function fetchQuote(items, mode){
  return apiPost("/optimize/basket", {
    items: items.map(o => ({ item_name: byId(o.itemId).name, quantity: o.qty })),
    mode: mode   // "today"(오늘) 또는 "forecast"(2주 예측)
  });
}
// 상세 예측: POST /predict  { item_name, brand? }
async function fetchPredict(itemName){ return apiPost("/predict", { item_name: itemName }); }

/* --- 어댑터: 서버 응답 → 화면이 쓰는 모양으로 변환 (실제 코드 기준으로 확정) ---
   응답 필드가 안 맞으면 자동으로 가짜 데이터로 폴백되니 화면은 안 깨져요. */

// /optimize/basket 응답: { brands:[{brand,total,items:[{item_name,quantity,unit_price,amount}],rank}], savings, ... }
function adaptQuote(raw){
  const arr = (raw && raw.brands) || [];
  if(!arr.length) throw new Error("발주 응답 형식 불명");
  return arr.map(r => ({
    brand: r.brand,
    total: r.total,
    lines: (r.items || []).map(l => ({ name: l.item_name, qty: l.quantity, unit: l.unit_price }))
  }));   // 백엔드가 이미 싼 순으로 정렬해서 줌
}

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

/* 데이터 출처 배너 갱신 */
function updateBanner(){
  const el = document.getElementById("dsBanner");
  if(!el) return;
  if(DATA_LIVE){ el.className = "ds live"; el.textContent = "🟢 실시간 데이터 (백엔드 연결됨)"; }
  else { el.className = "ds mock"; el.textContent = USE_MOCK
      ? "🟡 예시 데이터 (시연 모드)" : "🟡 예시 데이터 (백엔드 미연결 — 자동 폴백)"; }
}

/* 처음 로드 시 실제 신호를 시도해서 홈을 최신화 */
async function hydrate(){
  if(!USE_MOCK){
    try { applySignals(await fetchSignals()); DATA_LIVE = true; renderHome(); }
    catch(e){ console.warn("신호 API 폴백:", e); }
  }
  updateBanner();
}

function showOrder(){ go("order"); renderOrder(); }

// 가짜 데이터로 브랜드별 합산 TOP 3 계산 (백엔드 없을 때 폴백용)
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

  // 왼쪽: 내가 담은 발주 (품목 + 수량)
  document.getElementById("myOrderList").innerHTML = myOrder.map((o,i) => `
      <div class="oline">
        <div class="l1"><span class="oi">${byId(o.itemId).name}</span>
          <span class="stepper">
            <button onclick="changeQty(${i},-1)">−</button>
            <span class="num">${o.qty}개</span>
            <button onclick="changeQty(${i},1)">+</button>
          </span></div>
      </div>`).join("") || `<p class="muted" style="font-size:.85rem;">담은 품목이 없어요. 아래에서 추가해보세요.</p>`;

  document.getElementById("recSub").textContent =
    `${orderMode==="today"?"오늘 기준":"2주 예측 기준"} · 한 브랜드에서 전부 구매할 때 합산 총액`;

  // 오른쪽: 실제 API(POST /orders/auto) 시도 → 실패하면 가짜 계산으로 폴백
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

  document.getElementById("orderBuyBtn").textContent = `${ranked[0].brand}에서 구매 (${won(ranked[0].total)})`;
}

function changeQty(i, d){
  myOrder[i].qty = Math.max(1, myOrder[i].qty + d);
  renderOrder();
}

/* 오늘/예측 토글 */
document.getElementById("orderMode").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  orderMode = b.dataset.mode;
  document.querySelectorAll("#orderMode button").forEach(x => x.classList.toggle("on", x === b));
  renderOrder();
});

/* =====================================================================
   [8] 처음 시작: 홈 화면
===================================================================== */
renderHome();
go("home");
hydrate();   // USE_MOCK=false 이고 백엔드가 켜져 있으면 실제 신호로 최신화
