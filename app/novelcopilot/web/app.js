"use strict";
// ===== AI 웹소설 코파일럿 프론트엔드 (vanilla, 빌드 불필요) =====
const $ = (s) => document.querySelector(s);
// 인라인 role=button 링크의 키보드 조작(Enter/Space) 속성 — 템플릿에 ${ACT} 로 끼워 넣음
const ACT = 'role="button" tabindex="0" onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();this.click()}"';

// 진행 로그 — 내부 단계명(코드) → 화면 표현(사용자). 용어 사전: web/DESIGN.md §5
// 원칙: 화이트리스트. 매핑 안 된 코드는 일반 한글로 폴백 — 영어/내부용어(harness·SSOT·kind코드)가 절대 새지 않게.
const NODE_LABELS = {
  harness:"집필", plan_chapter:"회차 구상", plan_scenes:"흐름 설계", draft_chapter:"본문 집필",
  draft_scene:"본문 집필", assemble_memory:"맥락 정리", consistency_check:"일관성 검사",
  partial_rewrite:"부분 교정", scene_loop:"일관성 교정", quality_gate:"문장 다듬기", quality:"문장 다듬기",
  plan_lint:"구성 점검", cast_plan:"등장인물 준비", ontology_update:"설정 갱신", narrative:"전개 점검",
  drift:"전개 점검", summarize:"줄거리 정리", finalize:"마무리", worldgen:"세계 설계", connect:"" };
function nodeLabel(n){ return NODE_LABELS[n] !== undefined ? NODE_LABELS[n] : "진행"; }
// 일관성/구성 점검의 위반 코드 → 한글. 폴백은 일반어("설정 점검") — 코드값 노출 금지.
const KIND_LABELS = {
  state_timeline:"등장 시점", field_value:"설정값", edge_post_death:"사망 후 관계", ssot_ambiguous:"설정 충돌",
  relation_state:"관계 상태", numeric_monotonic:"수치 변화", vocab_violation:"설정 어휘", categorical_violation:"설정 어휘",
  plan_dead_cast:"퇴장 인물 배정", plan_unknown_entity:"미등록 인물", plan_beat_repeat:"전개 반복",
  wiki_dangling_edge:"노트 연결", wiki_orphan_thread:"미회수 복선", wiki_stale:"오래된 노트" };
function kindLabel(k){ return KIND_LABELS[k] || "설정 점검"; }
function kindList(arr){ return [...new Set((arr||[]).map(kindLabel))].slice(0,4).join(", "); }
const api = {
  async get(u){ const r = await fetch(u); if(!r.ok) throw new Error((await r.json().catch(()=>({}))).detail||r.status); return r.json(); },
  async post(u,b){ const r = await fetch(u,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b||{})});
    if(!r.ok) throw new Error((await r.json().catch(()=>({}))).detail||r.status); return r.json(); },
  async put(u,b){ const r = await fetch(u,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(b||{})});
    if(!r.ok) throw new Error((await r.json().catch(()=>({}))).detail||r.status); return r.json(); },
  async del(u){ const r = await fetch(u,{method:"DELETE"}); return r.json(); },
};
let STATE = { project:null, activeChapter:null, generating:false, chapterPage:null };
const CH_PAGE = 24;                         // 회차 네비 페이지당 칸 수(200화 대응)
function chPageOf(n){ return Math.max(0, Math.floor(((n||1) - 1) / CH_PAGE)); }

// ---------- 해시 라우팅 ----------
// 단일 진실: location.hash 가 현재 뷰/딥링크. 뒤로/앞으로·새로고침·공유 링크 지원.
//  #/  홈 · #/new 작품 만들기(대화) · #/p/{pid} 작업실 · #/p/{pid}/ch/{n} 회차 · #/read/{pid}/{n} 뷰어
let _routing = false;
function go(hash){ if(location.hash === hash) router(); else location.hash = hash; }       // 동일 해시면 강제 재라우팅
let _suppress = false;
function setHashSilent(hash){   // URL 만 갱신(재라우팅 없이). 발생할 hashchange 1회를 리스너가 소비.
  if(location.hash === hash) return;
  _suppress = true;
  location.replace(location.pathname + location.search + hash);
}
function _onlyView(id){ ["view-home","view-create","view-project","view-viewer"].forEach(v=>$("#"+v).classList.toggle("hidden", v!==id)); }
async function router(){
  if(_routing) return; _routing = true;
  try{
    const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
    if(!parts.length){ _onlyView("view-home"); loadProjects(); return; }
    if(parts[0] === "new"){
      if(STATE.draft && STATE.draft.id !== undefined){ _onlyView("view-create"); }
      else { location.replace("#/"); }
      return;
    }
    if(parts[0] === "p" && parts[1]){
      const pid = parts[1];
      if(!STATE.project || STATE.project.id !== pid){
        try{ await openProject(pid); }
        catch(e){ alert("작품을 열 수 없습니다: " + (e.message||"")); location.replace("#/"); return; }
      }
      _onlyView("view-project");
      if(parts[2] === "ch" && parts[3]){   // #/p/{id}/ch/{n} → 집필 작업대 + 해당 회차 활성
        const ch = parseInt(parts[3], 10);
        if((STATE.project.chapters||[]).some(c=>c.chapter===ch)){ STATE.activeChapter = ch; STATE.chapterPage = chPageOf(ch); }
        showSection("write");
        return;
      }
      if(PROJ_SECTIONS.includes(parts[2])) showSection(parts[2]);
      else location.replace(`#/p/${pid}/overview`);   // 섹션 미지정/미지 경로 → 개요로
      return;
    }
    if(parts[0] === "read" && parts[1]){
      const pid = parts[1], n = parseInt(parts[2], 10);
      if(!STATE.project || STATE.project.id !== pid){
        try{ await openProject(pid); }
        catch(e){ location.replace("#/"); return; }
      }
      renderViewerAt(n);   // 뷰어 표시 + 해당 화
      return;
    }
    location.replace("#/");   // 알 수 없는 경로 → 홈
  } finally { _routing = false; }
}
window.addEventListener("hashchange", ()=>{ if(_suppress){ _suppress = false; return; } router(); });   // 무음 갱신 1회 소비

// ---------- 네비게이션(해시로 위임) ----------
function goHome(){ go("#/"); }
// ---------- 작품 내부 섹션 네비게이션(좌측 사이드바 + 해시 라우트) ----------
const PROJ_SECTIONS = ["overview","write","bible","cast","world","serial"];
function goSection(sec){ if(STATE.project) go(`#/p/${STATE.project.id}/${sec}`); }   // 라우트로 위임 → 뒤로가기·딥링크·새로고침 복원
function showSection(sec){
  STATE.section = sec;
  document.querySelectorAll('.side-link').forEach(a=>{
    const on = a.dataset.sec===sec; a.classList.toggle('active', on);
    if(on) a.setAttribute('aria-current','page'); else a.removeAttribute('aria-current');
  });
  PROJ_SECTIONS.forEach(s=>{ const el=$("#sec-"+s); if(el) el.classList.toggle("hidden", s!==sec); });
  if(sec!=="cast" && CY){ try{CY.destroy();}catch(e){} CY=null; SELECTED=[]; SELECTED_EDGE=null; }   // 그래프 떠나면 정리(누수 방지)
  loadSection(sec);
}
function loadSection(sec){   // 섹션 진입 시 항상 최신 로드 — '보이는 패널만 갱신' 게이트 폐기(stale 차단)
  if(sec==="overview") renderOverview();
  else if(sec==="write"){ renderChapters(); renderReader(); renderGenInspect(); }
  else if(sec==="bible") bibleSub(STATE.bibleSub||"bible");
  else if(sec==="cast") loadGraph();
  else if(sec==="world") loadWorldgen();
  else if(sec==="serial"){ loadSpine(); serialSub(STATE.serialSub||"status"); }
}
function refreshSection(sec){ if(sec && sec!=="write" && sec!=="world") loadSection(sec); }   // 생성 완료 후 보던 섹션 최신화
function bibleSub(sub){     // 설정집 페이지 내부 2차 탭: 설정집 / 공식 설정 / 작품 노트
  STATE.bibleSub = sub;
  document.querySelectorAll('#sec-bible .tab').forEach(b=>b.classList.toggle('active',b.dataset.sub===sub));
  $("#tab-bible").classList.toggle("hidden",sub!=="bible");
  $("#inspect-onto").classList.toggle("hidden",sub!=="onto");
  $("#inspect-wiki").classList.toggle("hidden",sub!=="wiki");
  $("#inspect-style").classList.toggle("hidden",sub!=="style");
  if(sub==="bible") loadBible();
  else if(sub==="onto") loadOntology();
  else if(sub==="wiki") loadWiki();
  else if(sub==="style") loadStylePolicy();
}
function serialSub(sub){    // 연재 관리 내부 2차 탭: 연재 현황 / 이야기 구조 (데이터는 loadSpine 가 한 번에 채움)
  STATE.serialSub = sub;
  document.querySelectorAll('#sec-serial .tab').forEach(b=>b.classList.toggle('active',b.dataset.sub===sub));
  $("#serial-status").classList.toggle("hidden",sub!=="status");
  $("#serial-structure").classList.toggle("hidden",sub!=="structure");
}
// 개요(허브) — 돌아온 작가의 재개 동선: 이어 쓰기 + 진행/검토필요/미회수약속/사용량 요약
async function renderOverview(){
  const el=$("#sec-overview"); if(!el) return;
  const p=STATE.project, u=p.usage_total||{};
  const chs=(p.chapters||[]);
  const escCnt=chs.filter(c=>c.status==="ESCALATED").length;
  const last=p.current_chapter||0;
  el.innerHTML=`
    <div class="panel ov-hero">
      <div class="eyebrow">${esc(p.world.genre||"작품")}${p.world.tone?` · ${esc(p.world.tone)}`:""}</div>
      <h3 class="ov-logline">${esc(p.world.premise||p.world.title||"무제")}</h3>
      <div class="ov-actions">
        <button class="primary" onclick="goSection('write')">${last?`${last}화에 이어 쓰기 →`:"1화 쓰기 →"}</button>
        <button onclick="openViewer()">📖 읽기 모드</button>
      </div>
    </div>
    <div class="ov-cards">
      <div class="ov-card"><div class="ov-k">${last}<span class="ov-sub"> / ${p.total_beats}화</span></div><div class="ov-l">진행</div></div>
      <div class="ov-card ${escCnt?'warnish ov-clickable':''}" ${escCnt?ACT:''} ${escCnt?'onclick="gotoFirstEscalated()"':''}><div class="ov-k">${escCnt}</div><div class="ov-l">검토 필요 회차${escCnt?' →':''}</div></div>
      <div class="ov-card" id="ov-promise"><div class="ov-k">–</div><div class="ov-l">미회수 약속</div></div>
      <div class="ov-card"><div class="ov-k">${(u.chat_calls||0).toLocaleString()}</div><div class="ov-l">AI 사용(회)</div></div>
    </div>
    <div class="panel ov-links">바로가기 —
      <a ${ACT} onclick="goSection('bible')">설정집</a> ·
      <a ${ACT} onclick="goSection('cast')">인물 관계</a> ·
      <a ${ACT} onclick="goSection('world')">세계관 만들기</a> ·
      <a ${ACT} onclick="goSection('serial')">연재 관리</a></div>`;
  try{   // 미회수 약속 잔고(연재 관리로의 발견 경로)
    const sp=await api.get(`/api/projects/${p.id}/spine`);
    const open=(sp.promise_ledger||{}).open||0;
    const card=$("#ov-promise"); if(card){ card.querySelector(".ov-k").textContent=open; if(open>0) card.classList.add("warnish"); }
  }catch(e){}
}
function openRetro(){ STATE.pendingRetro=true; goSection('serial'); }   // 아크완결 nudge → 연재 관리 + 회고 자동 실행
function gotoFirstEscalated(){   // 개요 '검토 필요' 카드 → 가장 빠른 ESCALATED 회차로 점프
  const c=((STATE.project&&STATE.project.chapters)||[]).filter(x=>x.status==="ESCALATED").sort((a,b)=>a.chapter-b.chapter)[0];
  if(c) go(`#/p/${STATE.project.id}/ch/${c.chapter}`);
}

// ---------- 협업형 월드빌딩 대화 (R3) ----------
// 문체 설정 — Layer 2 작가 문체 오버레이 + 끝맺음 정책. PUT /style 로 저장(다음 회차부터 반영).
function loadStylePolicy(){
  const el=$("#inspect-style"); if(!el) return;
  const st=(STATE.project&&STATE.project.world&&STATE.project.world.style)||{};
  const hooks=[['cliffhanger','절단신공 — 다음 화 결제 유도'],['soft','잔잔한 여운'],['none','지시 없음(자유)']];
  el.innerHTML=`<div class="style-edit">
    <h4>작가 문체 <span class="muted small">— 이 작품을 어떤 결로 쓸지 (선택)</span></h4>
    <p class="muted small">문체는 작가마다 다릅니다 — 균일한 단문도, 만연체도 정답입니다. 여기 적은 결이 기본 문체의 <b>미학 축</b>(문장 리듬·감정 처리·직유 밀도·서술 거리·어휘 격)보다 우선합니다. 비우면 기본 문체로 씁니다. 분량·모바일 가독·시점/시제 같은 바닥은 항상 유지됩니다.</p>
    <textarea id="style-author" aria-label="작가 문체" rows="5" maxlength="2000" placeholder="예: 건조하고 짧은 단문 위주. 비유는 거의 쓰지 말고 사실만. 감정은 설명하지 말고 행동으로 드러내라. 군더더기 없이 빠르게.">${esc(st.author_style||"")}</textarea>
    <div class="style-row">
      <label>끝맺음 정책 <select id="style-hook">${hooks.map(([v,l])=>`<option value="${v}" ${st.ending_hook===v?'selected':''}>${esc(l)}</option>`).join("")}</select></label>
      <span class="style-actions"><button onclick="clearAuthorStyle()">비우기</button><button class="primary" id="style-save" onclick="saveStylePolicy()">저장</button></span>
    </div>
    <p id="style-msg" class="muted small" role="status"></p></div>`;
}
function clearAuthorStyle(){
  const ta=$("#style-author"), msg=$("#style-msg");
  if(ta) ta.value="";
  if(msg) msg.textContent="비웠습니다 — ‘저장’을 눌러야 기본 문체로 돌아갑니다.";
}
async function saveStylePolicy(){
  const ta=$("#style-author"), hook=$("#style-hook"), msg=$("#style-msg"), btn=$("#style-save");
  if(!ta||!btn) return;
  btn.disabled=true; if(msg) msg.textContent="저장 중…";
  try{
    await api.put(`/api/projects/${STATE.project.id}/style`,{author_style:ta.value.trim(), ending_hook:hook.value});
    STATE.project = await api.get(`/api/projects/${STATE.project.id}`);   // 전체 재조회 — 부분 동기화 stale 혼합 방지(다른 저장 핸들러 패턴)
    if(msg) msg.textContent="저장됨 — 다음 회차부터 반영됩니다.";
    toast("문체 설정을 저장했습니다");
  }catch(e){ if(msg) msg.textContent="저장 실패: "+e.message; toast("저장하지 못했습니다: "+e.message,"bad"); }
  finally{ btn.disabled=false; }
}
async function loadWorldgen(){
  try{ const r=await api.get(`/api/projects/${STATE.project.id}/worldgen`); renderWgLog(r.chat||[]); }
  catch(e){ $("#wg-log").innerHTML=`<span class="muted">로드 실패: ${esc(e.message)}</span>`; }
}
function renderWgLog(chat){
  const el=$("#wg-log");
  el.innerHTML = chat.length
    ? chat.map(t=>`<div class="wg-bubble ${t.role==='author'?'author':'ai'}">${esc(t.text)}</div>`).join("")
    : '<span class="muted small">세계관을 함께 만들어 봅시다. 무엇을 더하고 싶으세요?</span>';
  el.scrollTop=el.scrollHeight;
}
async function sendWorldgen(){
  const ta=$("#wg-msg"), msg=ta.value.trim(); if(!msg) return;
  const btn=$("#wg-send"), log=$("#wg-log");
  btn.disabled=true; ta.disabled=true;
  const ab=document.createElement("div"); ab.className="wg-bubble author"; ab.textContent=msg; log.appendChild(ab);
  const ai=document.createElement("div"); ai.className="wg-bubble ai"; ai.innerHTML='<span class="spin"></span> 구상 중…';
  log.appendChild(ai); log.scrollTop=log.scrollHeight;
  try{
    const r=await api.post(`/api/projects/${STATE.project.id}/worldgen`,{message:msg});
    const chips=(r.applied||[]).map(a=>{
      const t = a.kind==='entity' ? `＋${esc(a.name)}(${esc(a.etype)})`
        : a.kind==='relation' ? `＋${esc(a.src)} ${esc(a.label)} ${esc(a.dst)}`
        : `＋설정: ${esc(a.title)}`;
      return `<span class="wg-chip">${t}</span>`;
    }).join("");
    const blk=(r.blocked||[]).map(b=>`<span class="wg-chip blocked">보류: ${esc(b.reason)}</span>`).join("");
    const q=(r.questions||[]).map(x=>`<div class="wg-q">❓ ${esc(x)}</div>`).join("");
    ai.innerHTML = `${esc(r.reply||"")}${(chips||blk)?`<div class="wg-applied">${chips}${blk}</div>`:""}${q}`;
    ta.value="";
    // 추가된 인물·설정은 해당 섹션(인물 관계·설정집) 진입 시 자동 로드됨(라우트 진입 로드)
  }catch(e){ ai.innerHTML=`❌ ${esc(e.message)}`; }
  finally{ btn.disabled=false; ta.disabled=false; ta.focus(); log.scrollTop=log.scrollHeight; }
}
let RETRO=null;
function backlogSeries(promises, upto){   // 회차별 미회수 약속 잔고(개설<=c, 미지불 or 지불>c)
  const s=[]; for(let c=1;c<=upto;c++) s.push((promises||[]).filter(p=>p.o<=c&&(p.p==null||p.p>c)).length); return s;
}
function sparkline(series){
  if(!series||series.length<2) return "";
  const w=180,h=38,max=Math.max(1,...series);
  const pts=series.map((v,i)=>`${(i/(series.length-1)*w).toFixed(1)},${(h-v/max*(h-4)-2).toFixed(1)}`).join(" ");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="1.5"/></svg>`;
}
function median(a){ if(!a.length) return 0; const s=[...a].sort((x,y)=>x-y), m=s.length>>1; return s.length%2?s[m]:(s[m-1]+s[m])/2; }
function normMinMax(a){ const lo=Math.min(...a),hi=Math.max(...a); return hi===lo?a.map(()=>0.5):a.map(v=>(v-lo)/(hi-lo)); }  // 추세 '모양'만(저변동 지표도 보이게); 크기는 옆 숫자로
// 문체 지문 추세 — ai_tell 분포 신호를 '작품 자기 중앙값 대비 상대 추세'로만 노출(절대 판정·임계·경고색 금지, no-whack-a-mole).
// 출처는 chapters[].ai_tell(copilot 저장 경로). 미측정(도입 전·빈 dict) 회차는 제외하고 백필 안내.
function aiTellTrend(chapters){
  const rows=(chapters||[]).filter(c=>c.ai_tell&&c.ai_tell.n_sent).sort((a,b)=>a.chapter-b.chapter);
  const head=`<h4>문체 지문 추세 <span class="muted small">— 회차 간 분포 신호(상대 추세)</span></h4>`;
  if(rows.length<2) return `<div class="bible-sec">${head}<p class="muted small">${rows.length?'1개 회차만 측정됨 — 추세는 2화부터 보여요.':'아직 측정된 회차가 없어요. 새 회차를 쓰면 쌓입니다(도입 전 회차는 미측정).'}</p></div>`;
  const METRICS=[
    {k:'comma_per_100',   label:'쉼표 밀도',     hint:'높을수록 분절적'},
    {k:'sent_len_cv',     label:'문장길이 변동', hint:'낮을수록 균일'},
    {k:'lexical_mattr',   label:'어휘 다양성',   hint:'낮을수록 반복'},
    {k:'ending_diversity',label:'종결형 다양성', hint:'낮을수록 단조'},
    {k:'simile_per_1k',   label:'직유 밀도',     hint:'높을수록 비유 강박'},
  ];
  const lines=METRICS.map(m=>{
    const vals=rows.map(c=>c.ai_tell[m.k]).filter(v=>Number.isFinite(v));   // 결측·NaN을 0으로 위장 금지(메트릭별 실측만)
    if(vals.length<2) return `<div class="aitell-row"><div class="aitell-lbl"><b>${m.label}</b> <span class="muted tiny">${m.hint}</span></div>
      <div class="aitell-spk muted tiny">측정 부족</div><div class="aitell-val muted tiny">–</div></div>`;
    const latest=vals[vals.length-1], med=median(vals);
    const dir=latest>vals[0]?'완만한 상승':(latest<vals[0]?'완만한 하락':'안정');   // 스크린리더용 추세 방향
    // delta(%) 제거 — 중앙값을 암묵 임계로 세워 단일 회차 판정을 유도하던 누수. 추세는 스파크라인, 크기는 raw 숫자로만.
    return `<div class="aitell-row"><div class="aitell-lbl"><b>${m.label}</b> <span class="muted tiny">${m.hint}</span></div>
      <div class="aitell-spk" role="img" aria-label="${m.label} 추세 ${dir}">${sparkline(normMinMax(vals))}</div>
      <div class="aitell-val muted tiny">최근 ${latest.toFixed(2)}<br><span class="aitell-d">중앙값 ${med.toFixed(2)}</span></div></div>`;
  }).join("");
  return `<div class="bible-sec">${head}<div class="aitell-grid">${lines}</div>
    <p class="muted tiny">작품 자기 중앙값 대비 <b>상대 추세</b>일 뿐 'AI 판정'이 아닙니다 — 문체는 작가마다 다릅니다(균일 단문도 만연체도 사람 글). 한 회차의 한 숫자가 아니라 흐름으로 보세요.</p></div>`;
}
async function loadSpine(){
  const statusEl=$("#serial-status"), structEl=$("#serial-structure");
  if(!statusEl||!structEl) return;
  try{
    const sp=await api.get(`/api/projects/${STATE.project.id}/spine`);
    if(!sp.has_spine){ statusEl.innerHTML='<p class="muted">아직 이야기 구조가 없습니다. 새 작품은 자동으로 설계됩니다.</p>'; structEl.innerHTML=''; return; }
    // G5 장르 정체성 — 작품이 지키는 재미의 축(설계가 공유)
    const gc=sp.genre_contract;
    const gcBlock = (gc&&(gc.pleasure_engine||gc.premise_asset))?`<div class="bible-sec"><h4>장르 정체성 <span class="muted small">— 이 작품이 지키는 재미의 축</span></h4>
      ${gc.pleasure_engine?`<p class="small"><b>독자 쾌감:</b> ${esc(gc.pleasure_engine)}</p>`:""}
      ${gc.reader_expectations&&gc.reader_expectations.length?`<p class="small"><b>독자 기대:</b> ${gc.reader_expectations.map(esc).join(" · ")}</p>`:""}
      ${gc.premise_asset?`<p class="small"><b>핵심 전제:</b> ${esc(gc.premise_asset)}</p>`:""}</div>`
      :`<div class="bible-sec"><h4>장르 정체성 <span class="muted small">— 미설정</span></h4>
      <p class="muted small">이 작품은 장르 계약 도입 전 생성됐어요 — 쾌감 엔진·핵심 전제·독자 기대가 설계에 주입되지 않는 상태입니다.</p>
      <button id="gc-btn" onclick="backfillGenreContract()">장르 정체성 생성</button><span id="gc-msg" class="muted small"></span></div>`;
    // G1 약속 원장 — 독자에게 연 약속과 회수(잔고만 보여줌, 회수는 작가)
    const pl=sp.promise_ledger||{};
    const sinceTxt=(pl.since_payoff==null)?"아직 회수 없음":`마지막 회수 후 ${pl.since_payoff}화`;
    const opens=(sp.open_promises||[]).map(p=>`<li>${esc(p.text)} <span class="muted tiny">(${p.opened_chapter}화부터 · ${p.age}화째)</span></li>`).join("");
    const upto=((STATE.project&&STATE.project.current_chapter)||0)+1;
    const series=backlogSeries(sp.promises_all,upto);
    const trend=(series.length>=2)?`<div class="ledger-trend"><span class="muted tiny">미회수 잔고 추이 (1~${upto}화)</span>${sparkline(series)}</div>`:"";
    const ledgerBlock=`<div class="bible-sec"><h4>약속 원장 <span class="muted small">— 독자에게 연 약속과 회수</span></h4>
      <div class="ledger-stats"><span class="pill">미회수 ${pl.open||0}</span><span class="pill">회수 ${pl.paid||0}</span>
      <span class="pill ${(pl.since_payoff!=null&&pl.since_payoff>=5)?'amber':''}">${sinceTxt}</span></div>
      ${trend}
      ${opens?`<ul class="ledger-list small">${opens}</ul>`:'<p class="muted small">추적 중인 약속이 없습니다.</p>'}
      <p class="muted tiny">시스템은 잔고만 보여줍니다 — 회수 시점은 작가가 정합니다(슬로우번도 정당한 기법).</p></div>`;
    // G2 독자 반응 — 최근 회차 신호 집약(리더·생성결과에 흩어진 신호를 한곳에)
    const reacts=(STATE.project.chapters||[]).filter(c=>c.reader_feedback&&(c.reader_feedback.why||c.reader_feedback.got))
      .slice(-8).reverse().map(c=>{const rf=c.reader_feedback;return `<div class="reader-react ${rf.pay_next?'pos':'neg'}"><b>${c.chapter}화</b> — ${rf.pay_next?'결제 의향 있음':'이탈 위험'}${rf.got?` · 얻은 것: ${esc(rf.got)}`:''}${rf.why?`<div class="muted small">${esc(rf.why)}</div>`:''}</div>`;}).join("");
    const reactBlock=`<div class="bible-sec"><h4>독자 반응 <span class="muted small">— 최근 회차 신호</span></h4>${reacts||'<p class="muted small">아직 독자 반응 신호가 없습니다.</p>'}</div>`;
    // G3 연재 회고 — on-demand
    const retroBlock=`<div class="bible-sec"><h4>연재 회고 <span class="muted small">— 페이싱·방향 점검과 개정 제안</span></h4>
      <button id="retro-btn" onclick="loadRetrospective()">회고 받기</button>
      <div id="retro-body"></div></div>`;
    const ending=sp.ending?`<div class="bible-sec"><h4>결말 — 이 방향으로 수렴합니다</h4>
      <p><b>중심 질문:</b> ${esc(sp.ending.central_question)}<br><b>결말:</b> ${esc(sp.ending.ending)}
      ${sp.ending.thematic_payoff?`<br><b>주제:</b> ${esc(sp.ending.thematic_payoff)}`:""}</p></div>`:"";
    const arcs=sp.arcs.map(a=>{
      const eps=(a.episodes||[]).map(e=>{
        const cur=e.episode_id===sp.current_episode_id, st=e.done?"완료":(cur?"진행 중":"예정");
        return `<div class="ep ${cur?'cur':''} ${e.done?'done':''}"><b>${esc(e.title||e.episode_id)}</b>
          <span class="muted small">[${st} · 약 ${e.target_chapters}화]</span>
          <div class="small">절정: ${esc(e.climax)}</div>
          ${e.required_cast&&e.required_cast.length?`<div class="muted small">주요 인물: ${e.required_cast.map(esc).join(", ")}</div>`:""}
          ${e.summary?`<div class="muted small">요약: ${esc(e.summary)}</div>`:""}</div>`;
      }).join("")||'<span class="muted small">진행하면서 자동으로 설계됩니다</span>';
      return `<div class="arc ${a.done?'done':''}"><div class="arc-h">${esc(a.title||a.arc_id)}
        <span class="muted small">— ${esc(a.goal)}</span></div>${eps}</div>`;
    }).join("");
    statusEl.innerHTML=ledgerBlock+reactBlock+aiTellTrend(STATE.project.chapters)+retroBlock;   // 연재 현황
    structEl.innerHTML=gcBlock+ending+`<div class="bible-sec"><h4>단락과 에피소드 <span class="muted small">(현재 단락 ${sp.chapters_in_episode}화째)</span></h4>${arcs}</div>`;   // 이야기 구조
    if(STATE.pendingRetro){ STATE.pendingRetro=false; serialSub('status'); loadRetrospective(); }   // 아크완결 nudge 경유 진입
  }catch(e){ statusEl.innerHTML=`<span class="muted">로드 실패: ${esc(e.message)}</span>`; }
}
async function backfillGenreContract(){
  const btn=$("#gc-btn"), msg=$("#gc-msg"); if(!btn) return;
  btn.disabled=true; msg.textContent=" 추론 중…";
  try{
    await api.post(`/api/projects/${STATE.project.id}/genre-contract/backfill`,{});
    msg.textContent=" 완료";
    setTimeout(loadSpine,800);
  }catch(e){ msg.textContent=" 실패: "+e.message; btn.disabled=false; }
}
async function loadRetrospective(){
  const body=$("#retro-body"), btn=$("#retro-btn");
  if(!body||!btn) return;   // 연재 현황 미렌더 상태 방어
  btn.disabled=true; body.innerHTML='<span class="spin"></span> 전개를 돌아보는 중… <span class="muted small">잠시 걸려요</span>';
  try{
    const r=await api.get(`/api/projects/${STATE.project.id}/retrospective`); RETRO=r;
    const pw=r.pacing||{};
    const pacing=`<div class="retro-pacing muted small">최근 ${pw.window||0}화 · 훅 단조 ${pw.hook_monotony??'–'} · 장소 ${pw.places_distinct??'–'}종 · 새 고유명사 ${pw.new_names??'–'}개${pw.since_payoff!=null?` · 회수 후 ${pw.since_payoff}화`:''}</div>`;
    if(!r.diagnosis&&(!r.revisions||!r.revisions.length)){ body.innerHTML=pacing+'<p class="muted small">지금은 특별히 손볼 곳이 없다는 진단입니다.</p>'; btn.disabled=false; return; }
    const revs=(r.revisions||[]).map((rv,i)=>`<label class="retro-rev"><input type="checkbox" data-i="${i}" checked />
      <span><b>${esc(rv.target)}</b> · ${esc(rv.field)}<div class="small">${esc(rv.new_value)}</div>
      <div class="muted tiny">이유: ${esc(rv.reason||'')}</div></span></label>`).join("");
    body.innerHTML=pacing+`<div class="retro-diag">${esc(r.diagnosis||'')}</div>
      ${revs?`<div class="retro-revs"><div class="muted small">아래 개정안은 <b>아직 안 쓴 단락·결말</b>에만 적용됩니다(쓴 회차는 보호). 원하는 것만 고르세요.</div>${revs}
      <button class="primary" onclick="applyRevisions()">선택한 개정 적용</button></div>`:''}`;
  }catch(e){ body.innerHTML=`<span class="muted">실패: ${esc(e.message)}</span>`; }
  finally{ btn.disabled=false; }
}
async function applyRevisions(){
  if(!RETRO) return;
  const picks=[...document.querySelectorAll('.retro-rev input:checked')].map(c=>RETRO.revisions[+c.dataset.i]).filter(Boolean);
  if(!picks.length){ alert("적용할 개정을 선택하세요."); return; }
  try{
    const r=await api.post(`/api/projects/${STATE.project.id}/spine/revise`,{revisions:picks});
    $("#retro-body").innerHTML=`<p class="ok-msg">개정 ${r.applied.length}건 반영${r.rejected.length?` · ${r.rejected.length}건 제외`:''} — 다음 단락 설계부터 적용됩니다.</p>`;
    STATE.project=await api.get(`/api/projects/${STATE.project.id}`);
    setTimeout(loadSpine,1400);
  }catch(e){ alert("적용 실패: "+e.message); }
}
// (구 switchInspect 제거 — 공식설정/작품노트는 설정집 sub-tab(bibleSub), 생성정보는 집필 섹션 details 로 이동)

// ---------- 홈 ----------
function genreTone(g){   // 장르 문자열 → 안정적 색(라이브러리 책등 식별 신호)
  const palette=['#2F6F8F','#8A6D3B','#3F7050','#B05A7A','#5A6ABF','#9A5AB0','#566070','#3A6EA5'];
  let h=0; const s=g||'무제'; for(let i=0;i<s.length;i++) h=(h*31+s.charCodeAt(i))>>>0;
  return palette[h%palette.length];
}
function bookCard(p, promoted){
  const pct=p.total_chapters?Math.min(100,Math.round((p.current_chapter||0)/p.total_chapters*100)):0;
  const init=esc(((p.title||'무').trim().charAt(0))||'무');
  const cont=(p.current_chapter>0)?`${p.current_chapter}화 이어 쓰기 →`:'첫 회차 쓰기 →';
  return `<div class="bookcard${promoted?' promoted':''}">
    <a class="bc-link" href="#/p/${p.id}/write">
      ${promoted?'<div class="bc-eyebrow eyebrow">이어서 쓰기</div>':''}
      <div class="bc-row">
        <div class="bc-spine" style="background:${genreTone(p.genre)}">${init}</div>
        <div class="bc-main">
          <div class="bc-title">${esc(p.title||'무제')}</div>
          <div class="bc-meta">${esc(p.genre||'장르 미정')}${p.created_at?` · ${esc(String(p.created_at).slice(0,10))}`:''}</div>
          <div class="bc-progress"><div class="bp-bar"><div class="bp-fill" style="width:${pct}%"></div></div><span class="bp-label">${p.current_chapter||0}/${p.total_chapters}화</span></div>
          <div class="bc-cont">${cont}</div>
        </div>
      </div>
    </a>
    <button class="bc-del" title="작품 삭제" onclick="delProject('${p.id}')">삭제</button>
  </div>`;
}
function openWork(pid){ go(`#/p/${pid}/write`); }   // 라이브러리 클릭 → 바로 집필 작업대(재개)
async function loadProjects(){
  const el = $("#project-list"), cnt = $("#lib-count");
  try{
    const list = await api.get("/api/projects");
    if(cnt) cnt.textContent = list.length ? `${list.length}편` : "";
    if(!list.length){ el.innerHTML = '<p class="muted">아직 작품이 없습니다. 오른쪽 ‘새 작품 시작’에서 첫 작품을 빚어보세요. →</p>'; return; }
    list.sort((a,b)=>String(b.created_at||'').localeCompare(String(a.created_at||'')));   // 최근 생성 먼저
    let promoted=list[0];
    try{ const last=localStorage.getItem('novelcopilot:last');
      if(last){ const i=list.findIndex(p=>p.id===last); if(i>0){ promoted=list.splice(i,1)[0]; list.unshift(promoted); } else if(i===0) promoted=list[0]; } }catch(e){}
    el.innerHTML = list.map(p=>bookCard(p, p===promoted)).join("");
  }catch(e){ el.innerHTML = `<p class="muted">목록을 불러오지 못했습니다: ${esc(e.message)}</p>`; }
}
async function delProject(pid){ if(!confirm("이 작품을 삭제할까요? 되돌릴 수 없습니다.")) return; await api.del(`/api/projects/${pid}`); loadProjects(); }

// 작품 생성 단계(SSE) → 사람이 읽는 진행 문구. 가장 느린 설정집은 카테고리별로 노출.
function wgStage(ev){
  switch(ev.event){
    case "world_start": return "세계관을 구상하는 중…";
    case "world_done": return `세계관 완성 — 등장인물 ${(ev.entities||[]).length}명${(ev.entities||[]).length?` (${ev.entities.slice(0,4).map(esc).join(", ")}${ev.entities.length>4?" 외":""})`:""}`;
    case "spine_start": return "이야기 구조(결말·단락)를 설계하는 중…";
    case "spine_done": return `이야기 구조 완성 — 단락 ${ev.arcs}개`;
    case "spine_skip": return "이야기 구조 건너뜀";
    case "bible_start": return "설정집을 쓰기 시작합니다…";
    case "bible": return `설정집 작성 중 — ${esc(ev.label)} <span class="muted">(${ev.idx}/${ev.total})</span>`;
    case "bible_done": return `설정집 완성 — ${ev.entries}개 항목`;
    case "saving": return "마무리하는 중…";
    default: return "작품 세계를 짓는 중…";
  }
}
// ---------- 새 작품: 대화로 세계관 빚기 ----------
const START_EXAMPLES = [
  "기억을 사고파는 도시의 기억 거래상",
  "용을 길들이는 대가로 수명을 바치는 소녀",
  "회귀한 망나니 황자가 제국을 개혁한다",
  "죽은 자의 미련을 풀어주는 심부름센터",
];
function renderStartExamples(){
  const el = $("#start-examples"); if(!el) return;
  el.innerHTML = START_EXAMPLES.map((x,i)=>`<span class="ex" onclick="useExample(${i})">${esc(x)}</span>`).join("");
}
function useExample(i){ const ta = document.querySelector("#start-form [name=seed]"); if(ta){ ta.value = START_EXAMPLES[i]; ta.focus(); } }

// 작품 설정 컨트롤(작가 다이얼) — 대화와 별개로 직접 입력. 작가가 만지면 잠금(AI 갱신보다 우선).
const CTL_PRESETS = {
  genre: ["현대 판타지","무협","로맨스","로맨스 판타지","SF","미스터리","판타지","게임/헌터"],
  tone: ["어둡고 긴장감","밝고 유쾌","잔잔한","느와르","서사적","코믹"],
  target_chapters: [["단편","12"],["중편","30"],["장편","100"],["대장편","200"]],
};
function renderControlChips(){
  $("#ctl-genre-chips").innerHTML = CTL_PRESETS.genre.map(g=>`<span class="ctl-chip" onclick="pickCtl('genre','${g}')">${g}</span>`).join("");
  $("#ctl-tone-chips").innerHTML = CTL_PRESETS.tone.map(t=>`<span class="ctl-chip" onclick="pickCtl('tone','${t}')">${t}</span>`).join("");
  $("#ctl-target-chips").innerHTML = CTL_PRESETS.target_chapters.map(([lbl,v])=>`<span class="ctl-chip" onclick="pickCtl('target_chapters','${v}')">${lbl} ~${v}화</span>`).join("");
}
function pickCtl(key, val){
  const el = { genre:"#ctl-genre", tone:"#ctl-tone", target_chapters:"#ctl-target" }[key];
  if(el) $(el).value = val;
  lockParam(key, val);
}
function lockParam(key, value){     // 작가가 직접 정한 값 → 잠금(서버 turn/finalize 에 params 로 전달)
  if(!STATE.draft) STATE.draft = { id:null, locks:{} };
  if(!STATE.draft.locks) STATE.draft.locks = {};
  const v = (value||"").toString().trim();
  if(v) STATE.draft.locks[key] = (key==="target_chapters") ? parseInt(v,10)||200 : v;
  else delete STATE.draft.locks[key];
  // 칩 on 표시
  const chips = { genre:"#ctl-genre-chips", tone:"#ctl-tone-chips", target_chapters:"#ctl-target-chips" }[key];
  if(chips) document.querySelectorAll(`${chips} .ctl-chip`).forEach(c=>c.classList.toggle("on", c.textContent.includes(v) && v));
}
function applyBriefToControls(b){     // AI가 제안한 장르·분위기·회차를 컨트롤에 반영(작가가 잠근 건 건드리지 않음)
  const locks = (STATE.draft && STATE.draft.locks) || {};
  if(b.genre && !locks.genre) $("#ctl-genre").value = b.genre;
  if(b.tone && !locks.tone) $("#ctl-tone").value = b.tone;
  if(b.target_chapters && !locks.target_chapters) $("#ctl-target").value = b.target_chapters;
}

function startCreate(ev){
  ev.preventDefault();
  const seed = (ev.target.seed.value||"").trim(); if(!seed) return false;
  STATE.draft = { id:null, locks:{} }; _prevBrief = {};
  go("#/new");   // 라우팅(STATE.draft 설정 후 — 라우터가 create 뷰 표시, 뒤로가기=홈)
  $("#cc-log").innerHTML = ""; $("#cc-questions").innerHTML = ""; $("#cc-gaps").innerHTML = "";
  hideConfirm();
  renderControlChips();
  $("#ctl-genre").value = ""; $("#ctl-tone").value = ""; $("#ctl-target").value = "200";
  renderBrief({}); updateMeter(0, false);   // 빈칸 골격을 먼저 보여줌
  ccBubble("ai", "<b>같이 세계를 빚어볼게요.</b><br>주신 한 줄에서 시작해 제가 질문을 던지고, "
    + "오른쪽 <b>브리프</b>에 차곡차곡 정리할게요. 장르·분위기·목표 회차는 오른쪽에서 직접 정해도 돼요.");
  ccTurn(seed);
  return false;
}
function ccBubble(role, html){
  const b = document.createElement("div");
  b.className = `wg-bubble ${role==='author'?'author':'ai'}`;
  b.innerHTML = html;
  const log = $("#cc-log"); log.appendChild(b); log.scrollTop = log.scrollHeight;
  return b;
}
function ccSend(){ const ta = $("#cc-msg"); const m = (ta.value||"").trim(); if(!m) return; ta.value = ""; ccTurn(m); }
function ccAsk(btn){ ccTurn(btn.dataset.q || btn.textContent); }
async function ccTurn(message){
  ccBubble("author", esc(message));
  const thinking = ccBubble("ai", '<span class="spin"></span> 구상 중…'); thinking.classList.add("cc-thinking");
  $("#cc-send").disabled = true; $("#cc-msg").disabled = true;
  $("#cc-questions").innerHTML = ""; $("#cc-gaps").innerHTML = "";
  try{
    const r = await api.post("/api/drafts", { draft_id:STATE.draft.id, message, params:STATE.draft.locks });
    STATE.draft.id = r.draft_id;
    thinking.classList.remove("cc-thinking");
    const chips = (r.changes||[]).length
      ? `<div class="wg-applied">${r.changes.map(c=>`<span class="wg-chip">＋ ${esc(c)}</span>`).join("")}</div>` : "";
    thinking.innerHTML = `${esc(r.reply||"")}${chips}`;
    applyBriefToControls(r.brief || {});
    renderBrief(r.brief); updateMeter(r.completeness, r.ready);
    renderQuestions(r.questions||[]); renderGaps(r.gaps||[]);
    $("#cc-log").scrollTop = $("#cc-log").scrollHeight;
  }catch(e){ thinking.innerHTML = `응답을 받지 못했어요: ${esc(e.message)}`; }
  finally{ $("#cc-send").disabled = false; $("#cc-msg").disabled = false; $("#cc-msg").focus(); }
}
function renderQuestions(qs){
  const box = $("#cc-questions");
  if(!qs || !qs.length){ box.innerHTML = ""; return; }
  box.innerHTML = '<div class="cc-q-lbl">이어서 정해볼까요 — 눌러서 답하기</div>'
    + qs.map(q=>`<button class="cc-q" data-q="${esc(q)}" onclick="ccAsk(this)">${esc(q)}</button>`).join("");
}
function renderGaps(gs){
  $("#cc-gaps").innerHTML = (gs||[]).map(g=>`<div class="cc-gap">${esc(g)}</div>`).join("");
}
function updateMeter(pct, ready){     // 미터는 '안내'일 뿐 — 생성 버튼은 항상 활성(부족하면 확인 게이트가 받음)
  pct = Math.max(0, Math.min(100, pct||0));
  const rdy = !!ready && pct >= 35;
  const fill = $("#brief-fill"); fill.style.width = pct + "%"; fill.classList.toggle("is-ready", rdy);
  $("#brief-pct").textContent = `완성도 ${pct}%`;
  const meter = document.querySelector(".brief-meter"); if(meter) meter.setAttribute("aria-valuenow", pct);
  const gen = $("#brief-gen"), hint = $("#brief-gen-hint");
  gen.classList.toggle("ready", rdy);
  gen.textContent = rdy ? "이 세계로 시작하기 →" : "세계 생성 →";
  hint.textContent = rdy ? "충분히 무르익었어요 — 지금 시작해도 좋아요" : "원할 때 언제든 생성할 수 있어요";
}
let _prevBrief = {};
function _bf(label, val){ return `<div class="bf-field"><div class="bf-label">${label}</div><div class="bf-val">${val}</div></div>`; }
function _bfList(label, arr){ return `<div class="bf-field"><div class="bf-label">${label}</div><ul class="bf-list">`
  + arr.map(x=>`<li>${esc(x)}</li>`).join("") + `</ul></div>`; }
// 첫 턴(이전 브리프 없음)엔 하이라이트 안 함 — 모든 필드가 깜빡이는 노이즈 방지. 이후엔 바뀐 필드만.
function _mark(b, keys, html){
  if(!Object.keys(_prevBrief).length) return html;
  const changed = keys.some(k=>JSON.stringify(b[k]) !== JSON.stringify(_prevBrief[k]));
  return changed ? html.replace('class="bf-field"', 'class="bf-field bf-changed"') : html;
}
const _EMPTY = '<span class="bf-empty">아직 안 정해졌어요</span>';
// 빈칸 골격: 핵심 필드를 '항상' 보여줘 무엇을 채우면 되는지 한눈에. 채워지면 값, 비면 안내.
function renderBrief(b){
  b = b || {};
  const f = [];
  if(b.title) f.push(_mark(b, ["title"], _bf("제목", esc(b.title))));
  f.push(_mark(b, ["logline"], b.logline
    ? `<div class="bf-field"><div class="bf-label">로그라인</div><div class="bf-logline">${esc(b.logline)}</div></div>`
    : _bf("로그라인", _EMPTY)));
  f.push(_mark(b, ["premise"], _bf("전제", b.premise ? esc(b.premise) : _EMPTY)));
  f.push(_mark(b, ["setting"], _bf("배경", b.setting ? esc(b.setting) : _EMPTY)));
  f.push(_mark(b, ["characters"], (b.characters||[]).length
    ? `<div class="bf-field"><div class="bf-label">인물</div><div class="bf-chars">`
      + b.characters.map(c=>`<div class="bf-char"><span class="nm">${esc(c.name||"")}</span>${c.role?`<span class="rl">${esc(c.role)}</span>`:""}${c.want?`<span class="wt">${esc(c.want)}</span>`:""}</div>`).join("")
      + `</div></div>`
    : _bf("인물", _EMPTY)));
  f.push(_mark(b, ["world_rules"], (b.world_rules||[]).length ? _bfList("세계 규칙", b.world_rules) : _bf("세계 규칙", _EMPTY)));
  f.push(_mark(b, ["conflicts"], (b.conflicts||[]).length ? _bfList("핵심 갈등", b.conflicts) : _bf("핵심 갈등", _EMPTY)));
  if((b.themes||[]).length) f.push(_mark(b, ["themes"], `<div class="bf-field"><div class="bf-label">주제</div><div class="bf-tags">`
    + b.themes.map(t=>`<span>${esc(t)}</span>`).join("") + `</div></div>`));
  $("#brief-body").innerHTML = f.join("");
  _prevBrief = JSON.parse(JSON.stringify(b));
}
// 생성 직전 확인 게이트: 핵심(로그라인·인물)이 비면 "정말 진행?" → '그냥 진행'이면 default/AI가 채움.
function finalizeDraft(){
  if(!STATE.draft || !STATE.draft.id){ return; }
  const b = _prevBrief || {};
  const missing = [];
  if(!b.logline) missing.push("로그라인");
  if(!(b.characters||[]).length) missing.push("인물");
  if(missing.length){
    $("#bc-msg").innerHTML = `아직 <b>${missing.join("·")}</b>이(가) 비어 있어요. AI가 알아서 채워서 진행할까요?`;
    $("#brief-confirm").classList.remove("hidden");
    return;
  }
  finalizeGo();
}
function hideConfirm(){ const el = $("#brief-confirm"); if(el) el.classList.add("hidden"); }
function finalizeGo(){
  hideConfirm();
  if(!STATE.draft || !STATE.draft.id) return;
  const ov = $("#cc-overlay"), msg = $("#cc-overlay-msg");
  ov.classList.remove("hidden"); msg.innerHTML = "세계를 생성하는 중…";
  const locks = (STATE.draft.locks)||{};
  const qp = new URLSearchParams();
  if(locks.target_chapters) qp.set("target_chapters", String(locks.target_chapters));
  if(locks.genre) qp.set("genre", locks.genre);
  if(locks.tone) qp.set("tone", locks.tone);
  const es = new EventSource(`/api/drafts/${STATE.draft.id}/finalize?${qp.toString()}`);
  let done = false;
  es.addEventListener("event", e=>{ msg.innerHTML = wgStage(JSON.parse(e.data)); });
  es.addEventListener("complete", async e=>{ done = true; es.close(); ov.classList.add("hidden");
    const res = JSON.parse(e.data); STATE.draft = null; go(`#/p/${res.id}`); });
  es.addEventListener("failed", e=>{ done = true; es.close();
    msg.innerHTML = `생성하지 못했습니다: ${esc((JSON.parse(e.data)||{}).message||"")} <button class="primary" onclick="finalizeGo()">다시 시도</button>`; });
  es.onerror = ()=>{ if(!done){ es.close();
    msg.innerHTML = `연결이 끊겼습니다. <button class="primary" onclick="finalizeGo()">다시 시도</button> <button onclick="document.querySelector('#cc-overlay').classList.add('hidden')">닫기</button>`; } };
}

// ---------- 프로젝트 열기(로드+렌더 — 뷰 표시는 라우터가) ----------
async function openProject(pid){   // 데이터 로드 전용 — 뷰 초기화·섹션 로드는 라우터(showSection)가 담당
  if(_genES){ try{_genES.close();}catch(e){} _genES=null; STATE.generating=false; genStop(); }   // 진행 중이던 이전 작품 생성 정리
  STATE.project = await api.get(`/api/projects/${pid}`);
  try{ localStorage.setItem('novelcopilot:last', pid); }catch(e){}   // 홈 '이어서 쓰기' 복귀 동선
  STATE.activeChapter = STATE.project.current_chapter || null;
  STATE.chapterPage = null;          // null → renderChapters 가 최신(마지막) 페이지로
  STATE.section = undefined; STATE.bibleSub = "bible"; STATE.serialSub = "status";
  renderHeader();
  $("#harness-log").innerHTML = ""; $("#gen-result").innerHTML = "";   // 이전 작품 잔여 로그 제거(요소는 항상 DOM 상주)
  { const d=$("#directive"); if(d) d.value=""; const k=$("#dir-keep"); if(k) k.checked=false; }   // 작품별 작가지시 격리 — 다음 작품 누수 차단
}
function renderHeader(){
  const p = STATE.project, u = p.usage_total||{};
  $("#p-title").textContent = p.world.title || "무제";
  $("#p-meta").innerHTML = `${esc(p.world.genre)} · ${esc(p.world.tone)}<br><span class="muted">${esc(p.world.premise||"")}</span>`;
  $("#p-progress").textContent = `${p.current_chapter} / ${p.total_beats}화${p.completed?" · 완결":""}`;
  $("#p-cost").textContent = `AI 사용량 ${(u.chat_calls||0).toLocaleString()}회 · ${(u.chat_tokens||0).toLocaleString()}토큰`;
  const done=!!p.completed; const gb=$("#gen-btn");
  if(gb){ gb.disabled=done; gb.textContent=done?"완결되었습니다":"다음 회차 쓰기"; }
}

// ---------- 설정집(스토리 바이블, R2) ----------
async function loadBible(){
  const el = $("#tab-bible");
  try{
    const b = await api.get(`/api/projects/${STATE.project.id}/bible`);
    STATE.bibleMeta = {labels:b.category_labels||{}, template:b.template||[]};   // addBible 한국어 분류 셀렉트용
    const byCat = {};
    b.entries.forEach(e=>{ (byCat[e.category]=byCat[e.category]||[]).push(e); });
    const cats = [...b.template, ...Object.keys(byCat).filter(c=>!b.template.includes(c))];
    const secs = cats.filter(c=>byCat[c]&&byCat[c].length).map(c=>{
      const items = byCat[c].map(e=>`
        <div class="bentry ${e.promoted?'promoted':''}">
          <div class="be-h"><b>${esc(e.title)}</b>
            ${e.promoted ? '<span class="badge fin">공식</span>'
              : `<button class="be-promote" onclick="promoteBible('${e.entry_id}')">공식 설정으로 확정</button>`}
            <button class="be-del" onclick="delBible('${e.entry_id}')">삭제</button>
            <span class="muted small">${esc(e.provenance)}</span></div>
          <div class="be-prose" contenteditable="true"
               onfocus="this.dataset.orig=this.innerText"
               onblur="saveBible('${e.entry_id}', this)">${esc(e.prose)}</div>
        </div>`).join("");
      return `<div class="bible-sec"><h4>${esc(b.category_labels[c]||c)}</h4>${items}</div>`;
    }).join("");
    el.innerHTML = `<div class="bible-toolbar"><button onclick="addBible()">＋ 설정 항목</button>
      <span class="muted small">‘공식 설정으로 확정’하면 일관성 검사가 추적합니다. 내용을 클릭하면 바로 편집할 수 있어요(자동 저장).</span></div>`
      + (secs || '<p class="muted">설정집이 비어 있습니다. ＋로 항목을 추가하세요.</p>');
  }catch(e){ el.innerHTML = `<span class="muted">불러오지 못했습니다: ${esc(e.message)}</span>`; }
}
async function promoteBible(id){
  try{ const r=await api.post(`/api/projects/${STATE.project.id}/bible/${id}/promote`,{});
       if(r.already) {} loadBible(); loadOntology(); }
  catch(e){ alert("확정하지 못했습니다: "+e.message); }
}
async function delBible(id){
  if(!confirm("이 설정 항목을 삭제할까요?")) return;
  await api.del(`/api/projects/${STATE.project.id}/bible/${id}`); loadBible();
}
async function saveBible(id, el){
  const prose = el.innerText;
  if(prose === el.dataset.orig) return;   // dirty check — 변경 없으면 PUT 생략
  try{
    await api.put(`/api/projects/${STATE.project.id}/bible/${id}`, {prose});
    el.dataset.orig = prose;
    el.style.borderColor = "var(--ok)"; setTimeout(()=>{ el.style.borderColor=""; }, 800);   // 저장됨 피드백
  }catch(e){ el.style.borderColor = "var(--bad)"; toast("설정을 저장하지 못했습니다: "+e.message, "bad"); }   // gen-result 가 숨은 섹션이라 toast 로
}
// 서버 라벨이 없을 때만 쓰는 폴백(영문 카테고리 키를 화면에 노출하지 않기 위함 — DESIGN.md §5)
const BIBLE_CAT_FALLBACK = {
  character:"인물", faction_politics:"세력·정치", geography:"지리", power_system:"힘의 체계",
  magic_system:"마법 체계", ability_system:"능력 체계", chronology:"연표", artifact:"유물·도구",
  culture_religion:"문화·종교", taboo_worldrule:"금기·세계규칙", glossary:"용어", race:"종족", bestiary:"생물지" };
async function addBible(){
  const meta = STATE.bibleMeta || {labels:{}, template:[]};
  const keys = (meta.template && meta.template.length) ? meta.template : Object.keys(BIBLE_CAT_FALLBACK);
  const label = k => (meta.labels && meta.labels[k]) || BIBLE_CAT_FALLBACK[k] || "기타 설정";   // 영문 내부키 노출 방지(§5)
  const opts = keys.map(k=>`<option value="${esc(k)}"${k==='glossary'?' selected':''}>${esc(label(k))}</option>`).join("");
  const m = openModal(`<h3>설정 항목 추가</h3>
    <label>제목<input id="ab-title" placeholder="예: 백야 결사단" autocomplete="off"></label>
    <label>분류<select id="ab-cat">${opts}</select></label>
    <label>내용<textarea id="ab-prose" rows="4" placeholder="이 설정이 무엇인지 한두 문장으로…"></textarea></label>
    <div id="ab-err" class="modal-err" role="alert"></div>
    <div class="modal-actions">
      <button type="button" data-close>취소</button>
      <button type="button" class="primary" id="ab-submit">추가</button>
    </div>`);
  const submit = async () => {
    const title = $("#ab-title").value.trim();
    if(!title){ $("#ab-err").textContent="제목을 입력하세요."; $("#ab-title").focus(); return; }
    const category = $("#ab-cat").value, prose = $("#ab-prose").value;
    const btn = $("#ab-submit"); btn.disabled = true; btn.textContent = "추가 중…";
    try{ await api.post(`/api/projects/${STATE.project.id}/bible`,{category,title,prose}); loadBible(); closeModal(m); }
    catch(e){ btn.disabled=false; btn.textContent="추가"; $("#ab-err").textContent="추가하지 못했습니다: "+e.message; }
  };
  $("#ab-submit").onclick = submit;
  $("#ab-title").addEventListener("keydown", e=>{ if(e.key==="Enter"){ e.preventDefault(); $("#ab-prose").focus(); }});
  $("#ab-prose").addEventListener("keydown", e=>{ if((e.ctrlKey||e.metaKey)&&e.key==="Enter"){ e.preventDefault(); submit(); }});
  setTimeout(()=>$("#ab-title").focus(), 30);
}

// ---------- 회차/리더 ----------
function renderChapters(){
  const p = STATE.project, nav = $("#chapter-nav");
  const chs = (p.chapters||[]).slice().sort((a,b)=>a.chapter-b.chapter);
  if(!chs.length){ nav.innerHTML = '<span class="muted small">아직 생성된 회차가 없습니다.</span>'; return; }
  const pages = Math.ceil(chs.length / CH_PAGE);
  let pg = STATE.chapterPage;
  if(pg == null) pg = pages - 1;          // 기본=최신 페이지
  pg = Math.max(0, Math.min(pg, pages - 1));
  STATE.chapterPage = pg;
  const start = pg * CH_PAGE, slice = chs.slice(start, start + CH_PAGE);
  const pager = pages > 1
    ? `<div class="ch-pager">
         <button class="cn-nav" ${pg<=0?"disabled":""} onclick="chapterPage(${pg-1})" title="이전 묶음">◀</button>
         <span class="cn-range">${start+1}–${start+slice.length} <span class="muted">/ ${chs.length}화</span></span>
         <button class="cn-nav" ${pg>=pages-1?"disabled":""} onclick="chapterPage(${pg+1})" title="다음 묶음">▶</button>
       </div>` : "";
  const grid = '<div class="ch-grid">' + slice.map(c=>{
    const e = c.status==="ESCALATED" ? "escalated" : "";
    const a = c.chapter===STATE.activeChapter ? "active" : "";
    return `<button class="cn ${a} ${e}" onclick="selectChapter(${c.chapter})">${c.chapter}화</button>`;
  }).join("") + '</div>';
  nav.innerHTML = pager + grid;
}
function chapterPage(i){ STATE.chapterPage = i; renderChapters(); }   // 페이지 넘김(라우팅 아님 — 뷰 내 탐색)
function selectChapter(n){ go(`#/p/${STATE.project.id}/ch/${n}`); }   // 회차 선택은 라우팅(뒤로/딥링크)
// 생성 컨텍스트 디버그 — '어떤 정보로 이 회차를 만들었나'(계획 비트 + 집필 입력 슬롯)
const SRC_LABEL={rag_chunk:"이전 회차 검색",wiki_page:"인물 노트",arc_anchor:"서사 방향",bible:"설정집",cast_debut:"신규 인물 소개",roster:"고유명사 명부"};
function genContextHtml(c){
  const g=c.gen_context||{};
  if(!g.plan&&!g.draft) return '<p class="muted small" style="padding:.6em 0">이 회차는 생성 정보 기록 기능 도입 전에 만들어져, 들어간 컨텍스트 기록이 없습니다. 지금부터 새로 쓰는 회차에는 설계·집필에 사용한 정보가 모두 기록됩니다.</p>';
  const p=g.plan||{}, d=g.draft||{}, b=d.beat||{};
  const list=(arr,fn)=>(arr&&arr.length)?arr.map(fn).join(""):'<span class="muted tiny">없음</span>';
  const beatBlk=`<div class="gd-row"><b>회차 기능</b> ${esc(b.chapter_function||'–')} · 훅 ${esc(b.hook_type||'–')} · 시간 ${esc(b.time_advance||'–')} · 장소 ${esc(b.place||'–')}</div>
    <div class="gd-row"><b>핵심 사건</b> ${list(b.key_events,e=>`<span class="gd-tag">${esc(e)}</span>`)}</div>`;
  const planBlk=(p.arc||p.cast_context)?`<div class="gd-sec"><h5>설계 입력 ${p.arc?`— ${esc(p.arc)} / ${esc(p.episode||'')}${p.is_finale?' · 절정 회차':''}`:''}</h5>
    ${p.cast_context?`<div class="gd-row"><b>등장 인물 컨텍스트</b><pre class="gd-pre">${esc(p.cast_context)}</pre></div>`:''}
    ${(p.genre_contract&&p.genre_contract.pleasure_engine)?`<div class="gd-row"><b>장르 정체성</b> ${esc(p.genre_contract.pleasure_engine)}</div>`:''}
    ${p.plant_notes?`<div class="gd-row"><b>복선 리마인더</b> ${esc(p.plant_notes)}</div>`:''}
    ${(p.restraint&&p.restraint.length)?`<div class="gd-row"><b>표현 절제</b> ${p.restraint.map(esc).join(", ")}</div>`:''}
    ${(p.recent&&p.recent.length)?`<div class="gd-row"><b>최근 줄거리</b>${p.recent.map(r=>`<div class="gd-line">${esc(r)}</div>`).join("")}</div>`:''}</div>`:"";
  const draftBlk=`<div class="gd-sec"><h5>집필 입력</h5>${beatBlk}
    <div class="gd-row"><b>확정 설정(박기)</b> ${list(d.ground_truth,f=>`<span class="gd-tag canon">${esc(f)}</span>`)}</div>
    ${(d.world_rules&&d.world_rules.length)?`<div class="gd-row"><b>세계 규칙</b>${d.world_rules.map(r=>`<div class="gd-line">⚖️ ${esc(r)}</div>`).join("")}</div>`:''}
    <div class="gd-row"><b>참조 자료</b>${list(d.anchors,a=>`<div class="gd-line"><span class="gd-src">${esc(SRC_LABEL[a.source]||a.source)}</span> ${esc(a.text)}</div>`)}</div>
    ${(d.directives&&d.directives.length)?`<div class="gd-row"><b>작가 지시</b>${d.directives.map(x=>`<div class="gd-line">${esc(x)}</div>`).join("")}</div>`:''}
    ${d.story_so_far?`<div class="gd-row"><b>누적 줄거리</b> <span class="muted tiny">(실제 ${d.story_so_far_chars||'?'}자, 예산까지 사용)</span><pre class="gd-pre">${esc(d.story_so_far)}</pre></div>`:''}
    ${d.voice_roster?`<div class="gd-row"><b>보이스·명부</b><pre class="gd-pre">${esc(d.voice_roster)}</pre></div>`:''}
    ${(d.style_rules&&d.style_rules.length)?`<div class="gd-row"><b>문체 규칙</b> <span class="muted tiny">(매 집필 헤더 주입)</span>${d.style_rules.map(r=>`<div class="gd-line">${esc(r)}</div>`).join("")}</div>`:''}
    ${d.author_style?`<div class="gd-row"><b>작가 지정 문체</b> <span class="muted tiny">(기본 규칙보다 우선 — 미학 축만)</span><div class="gd-line">✍️ ${esc(d.author_style)}</div></div>`:''}
    ${d.prev_chapter_excerpt?`<div class="gd-row"><b>직전 회차 발췌</b> <span class="muted tiny">(${d.prev_chapter_chars||0}자 전문 주입)</span><div class="gd-line">${esc(d.prev_chapter_excerpt)}</div></div>`:''}
    <div class="gd-row muted tiny">끝맺음: ${esc(d.ending_hook_mode||'?')} · 출고규범 ${d.length_norm||'?'}자 · 이어쓰기 ${d.continuations||0}회 · 교정 ${(d.corrections&&d.corrections.length)?esc(d.corrections.join(", ")):'없음'}</div>
    <div class="gd-row muted tiny">집필 화법: ${esc((d.persona||'').slice(0,120))}</div></div>`;
  return `<div class="gen-debug">${planBlk}${draftBlk}</div>`;
}
function renderGenInspect(){
  const el=$("#inspect-gen"); if(!el) return;
  const c=((STATE.project&&STATE.project.chapters)||[]).find(x=>x.chapter===STATE.activeChapter);
  if(!c){ el.innerHTML='<div class="inspect-head">회차를 고르면, 그 회차를 <b>어떤 정보로 만들었는지</b>(설계·집필 입력)가 여기 표시됩니다.</div>'; return; }
  el.innerHTML=`<div class="inspect-head">${c.chapter}화를 만든 정보 — 설계·집필에 들어간 입력 슬롯</div>`+genContextHtml(c);
}
function renderReader(){
  const p = STATE.project, body = $("#chapter-body");
  const c = (p.chapters||[]).find(x=>x.chapter===STATE.activeChapter);
  if(!c){
    if(!(p.chapters||[]).length && !p.completed){   // 0회차: 첫 회차 쓰기로 화면 전체가 수렴(첫 여정 막힘 해소)
      body.classList.remove("muted");
      body.innerHTML = `<div class="reader-empty">
        <div class="re-mark">✍︎</div>
        <h3>세계가 준비됐어요. 이제 1화를 써볼까요?</h3>
        <p class="muted">AI가 초고를 쓰고, 설정이 어긋나면 자동으로 점검합니다. 한 화는 보통 1~3분 걸려요.</p>
        <button class="primary re-cta" onclick="generateNext()" ${STATE.generating?"disabled":""}>${STATE.generating?"집필 중…":"1화 쓰기 →"}</button>
      </div>`;
    } else {
      body.classList.add("muted");
      body.textContent = "회차를 고르거나, 다음 회차를 써보세요.";
    }
    return;
  }
  body.classList.remove("muted");
  const badge = c.status==="FINALIZED"?'<span class="badge fin">완성</span>':'<span class="badge esc">검토 필요</span>';
  const oc = (c.ontology_changes||[]).map(o=>`<div class="onto-change ${o.op==='new_entity'?'new':o.op==='contradiction'?'con':'chg'}">${o.applied?"✓":"✗"} ${esc(o.entity)}: ${esc(o.detail)}${o.reason?` <span class="muted">(${esc(o.reason)})</span>`:""}</div>`).join("");
  const chars = (c.text||"").length;
  // ESCALATED 회복 안내: 무엇이 충돌하고 어떻게 고칠지 자연어로(진행 차단에서 빠져나오는 길)
  const rec = (c.status!=="FINALIZED" && (c.recovery_hints||[]).length)
    ? `<div class="recovery"><div class="rec-head">이 회차는 설정과 충돌해 검토가 필요해요. 이렇게 풀 수 있어요:</div>`
      + c.recovery_hints.map(h=>`<div class="rec-item"><div class="rec-diag">${esc(h.diagnosis||"")}</div>`
        + `<ul class="rec-fix">${(h.fix||[]).map(f=>`<li>${esc(f)}</li>`).join("")}</ul></div>`).join("")
      + `<div class="rec-foot"><button onclick="generateNext()">이대로 다시 생성</button> <span class="muted small">설정을 고친 뒤 다시 생성하면 반영됩니다</span></div></div>`
    : "";
  const rf=c.reader_feedback;   // G2 독자 반응(advisory)
  const readerBlock=(rf&&(rf.why||rf.got))?`<div class="reader-react ${rf.pay_next?'pos':'neg'}">
    <b>독자 반응</b> — ${rf.pay_next?'다음 화 결제 의향 있음':'이탈 위험'}${rf.got?` · 얻은 것: ${esc(rf.got)}`:''}
    ${rf.why?`<div class="muted small">${esc(rf.why)}</div>`:''}</div>`:"";
  // C-5: FINALIZED 회차의 비구속 위반(세계규칙 등)도 작가에게 advisory 로 가시화 — 검출만 되고 사장되던 신호
  const fv=(c.status==="FINALIZED"&&(c.final_violations||[]).length)?c.final_violations:[];
  const violBlock=fv.length?`<div class="reader-react neg"><b>점검</b> — 비구속 위반 ${fv.length}건(차단 안 됨, 참고): ${esc([...new Set(fv.map(v=>v.kind))].join(", "))}</div>`:"";
  // 퇴고 진입(F6) — FINALIZED·ESCALATED 둘 다. 본문 사후 다듬기(사실 불변).
  const reviseBtn = (c.status==="FINALIZED"||c.status==="ESCALATED")
    ? ` <button class="revise-btn" onclick="openReviseModal(${c.chapter})">퇴고</button>` : "";
  // 되돌리기 링크 — 채택된(미-reverted) 퇴고가 1건 이상 있을 때만(F4)
  const hasUndo = (c.revisions||[]).some(r=>!r.reverted);
  const undoLink = hasUndo
    ? `<span class="revise-undo-link" ${ACT} onclick="undoRevise(${c.chapter})">마지막 퇴고 되돌리기</span>` : "";
  body.innerHTML = `<h3 class="ch-title">${c.chapter}화 · ${esc(c.title)} ${badge}${reviseBtn}</h3>`+
    `<div class="reader-meta">${chars.toLocaleString()}자${c.wiki_pages_touched?` · 인물 노트 ${c.wiki_pages_touched}건 갱신`:""}${undoLink?` · ${undoLink}`:""}</div>`+
    rec+readerBlock+violBlock+
    (oc?`<div style="margin-bottom:1.4em">${oc}</div>`:"")+
    `<div>${esc(c.text).replace(/\n/g,"<br>")}</div>`;
  renderGenInspect();   // 활성 회차 바뀌면 '생성 정보' 패널 동기화(요소 없으면 무시)
}

// ---------- 뷰어(몰입형 읽기) — 웹소설 플랫폼처럼 이전·다음 화 ----------
function viewerChapters(){
  return (STATE.project.chapters||[]).filter(c=>c.text).sort((a,b)=>a.chapter-b.chapter);
}
function exportNovel(fmt){   // 작품 전체를 파일로 다운로드(Content-Disposition attachment)
  if(!STATE.project){ return; }
  if(!(STATE.project.chapters||[]).some(c=>(c.text||"").trim())){ alert("아직 내보낼 회차가 없습니다."); return; }
  document.querySelectorAll(".export-wrap.open").forEach(el=>el.classList.remove("open"));
  const a = document.createElement("a");
  a.href = `/api/projects/${STATE.project.id}/export?fmt=${fmt}`;
  document.body.appendChild(a); a.click(); a.remove();
}
function openViewer(n){   // 진입(버튼) → 라우팅
  const list = viewerChapters();
  if(!list.length){ alert("아직 읽을 회차가 없습니다. 먼저 회차를 써보세요."); return; }
  let saved=0; try{ saved=parseInt(localStorage.getItem('novelcopilot:read:'+STATE.project.id))||0; }catch(e){}
  const target = n || saved || STATE.activeChapter || list[list.length-1].chapter;   // 읽던 회차 우선
  go(`#/read/${STATE.project.id}/${target}`);
}
function renderViewerAt(n){   // 라우터가 호출 — 뷰어 표시 + 해당 화
  const list = viewerChapters();
  if(!list.length){ location.replace(`#/p/${STATE.project.id}`); return; }
  const idx = list.findIndex(c=>c.chapter===n);
  STATE.viewerIdx = idx>=0 ? idx : list.length-1;
  _onlyView("view-viewer");
  $("#v-work").textContent = (STATE.project.world.title) || "무제";
  $("#v-jump").innerHTML = list.map((c,i)=>`<option value="${i}">${c.chapter}화 · ${esc(c.title||"")}</option>`).join("");
  renderViewer();
}
function renderViewer(){
  const list = viewerChapters(), c = list[STATE.viewerIdx];
  if(!c) return;
  applyReaderScale();
  $("#v-title").textContent = `${c.chapter}화 · ${c.title||""}`;
  $("#v-badge").innerHTML = "";   // 편집 상태(완성/검토필요)는 독자 화면에 숨김 — 몰입 보존(DESIGN.md §4)
  // 문단 단위 렌더 — 빈 줄/줄바꿈으로 분리, 대사("…")는 dlg 로 표시
  $("#v-body").innerHTML = (c.text||"").split(/\n+/).map(s=>s.trim()).filter(Boolean)
    .map(p=>`<p${/^["“]/.test(p)?' class="dlg"':''}>${esc(p)}</p>`).join("");
  $("#v-count").textContent = `${STATE.viewerIdx+1} / ${list.length}`;
  $("#v-prev").disabled = STATE.viewerIdx<=0;
  $("#v-next").disabled = STATE.viewerIdx>=list.length-1;
  $("#v-jump").value = String(STATE.viewerIdx);
  const nx=list[STATE.viewerIdx+1], pv=$("#v-next-preview");   // 다음 화 미리보기(읽는 손맛) / 최신 화 상태
  if(pv){ pv.innerHTML = nx
    ? `<button class="v-next-card" onclick="viewerNav(1)"><span class="vnc-lbl">다음 화</span><span class="vnc-title">${esc(nx.chapter)}화 · ${esc(nx.title||"")}</span><span class="vnc-go">이어 읽기 →</span></button>`
    : `<div class="v-end-card">여기까지가 최신 화예요. <button onclick="closeViewer()">작업실로 돌아가기</button></div>`; }
  try{ localStorage.setItem('novelcopilot:read:'+STATE.project.id, String(c.chapter)); }catch(e){}   // 읽던 회차 복원
  $("#view-viewer").scrollTop = 0;
}
function applyReaderScale(){
  let s=1; try{ s=parseFloat(localStorage.getItem('novelcopilot:fontscale'))||1; }catch(e){}
  $("#view-viewer").style.setProperty('--reader-scale', Math.max(.8,Math.min(1.5,s)));
}
function readerFont(d){   // 독자 글자 크기 A−/A+ (0.8~1.5배, localStorage 기억)
  let s=1; try{ s=parseFloat(localStorage.getItem('novelcopilot:fontscale'))||1; }catch(e){}
  s=Math.max(.8,Math.min(1.5, Math.round((s+d*0.1)*10)/10));
  try{ localStorage.setItem('novelcopilot:fontscale', String(s)); }catch(e){}
  applyReaderScale();
}
function _viewerSyncHash(){   // 현재 보는 화를 URL에 반영(재라우팅 없이 — 새로고침/공유 일관)
  const c = viewerChapters()[STATE.viewerIdx];
  if(c) setHashSilent(`#/read/${STATE.project.id}/${c.chapter}`);
}
function viewerNav(d){
  const list = viewerChapters(), ni = STATE.viewerIdx + d;
  if(ni<0 || ni>=list.length) return;
  STATE.viewerIdx = ni; renderViewer(); _viewerSyncHash();
}
function viewerJump(i){ STATE.viewerIdx = parseInt(i,10)||0; renderViewer(); _viewerSyncHash(); }
function closeViewer(){
  const c = viewerChapters()[STATE.viewerIdx];   // 작업실 리더를 본 회차로 동기화하며 복귀
  go(c ? `#/p/${STATE.project.id}/ch/${c.chapter}` : `#/p/${STATE.project.id}`);
}
document.addEventListener("keydown", e=>{
  if($("#view-viewer").classList.contains("hidden")) return;
  if(e.key==="ArrowLeft"){ viewerNav(-1); }
  else if(e.key==="ArrowRight"){ viewerNav(1); }
  else if(e.key==="Escape"){ closeViewer(); }
});

// ---------- 회차 생성 (SSE 라이브) ----------
// 전역 집필 진행 표시 — 어느 탭에 있든 헤더에 'N화 집필 중 · 경과시간'. 멈춤 오해·이탈 방지(UX).
let _genTimer = null, _genT0 = 0, _genLabel = "집필 준비 중";
let _genES = null;   // 진행 중 회차 생성 SSE — 작품 전환 시 정리(다른 작품으로의 완료 누수 방지)
function genStart(){ _genT0 = Date.now(); _genLabel = "집필 준비 중";
  const el = $("#p-genstatus"); if(el) el.classList.remove("hidden");
  if(_genTimer) clearInterval(_genTimer); _genTimer = setInterval(genTick, 1000); genTick(); }
function genTick(){ const el = $("#p-genstatus"); if(!el) return;
  const s = Math.floor((Date.now()-_genT0)/1000);
  el.innerHTML = `<span class="spin"></span> ${esc(_genLabel)} · ${Math.floor(s/60)}:${String(s%60).padStart(2,"0")}`; }
function genStop(){ if(_genTimer){ clearInterval(_genTimer); _genTimer = null; } const el = $("#p-genstatus"); if(el) el.classList.add("hidden"); }

function generateNext(){
  if(STATE.generating) return;
  const pid = STATE.project.id;
  const directive = $("#directive").value.trim();
  STATE.generating = true;
  $("#gen-btn").disabled = true;
  $("#harness-log").innerHTML = "";
  $("#gen-result").innerHTML = '<span class="spin"></span> 집필 중… <span class="muted small">보통 1~3분 걸려요. 다른 탭을 봐도 계속 진행됩니다.</span>';
  renderReader();   // 0회차 빈 상태였다면 '집필 중'으로 갱신
  genStart();
  logEvent({node:"harness",event:"connect"},"");

  const url = `/api/projects/${pid}/generate?directive=${encodeURIComponent(directive)}`;
  const es = new EventSource(url); _genES = es;
  let done = false;
  es.addEventListener("start", e=>{ const d=JSON.parse(e.data); _genLabel = `${d.chapter}화 집필 중`; logEvent({node:"plan_chapter",event:`${d.chapter}화 시작`},"",true); });
  es.addEventListener("event", e=> logEvent(JSON.parse(e.data)));
  es.addEventListener("complete", e=>{ done=true; es.close(); onComplete(JSON.parse(e.data)); });
  es.addEventListener("failed", e=>{ done=true; es.close(); onFail(JSON.parse(e.data)); });
  es.onerror = ()=>{ if(!done){ es.close(); onFail({message:"연결 끊김"}); } };
}
// 상태색: 실패/검토=red, 경고=amber, 완료성=green. (이벤트 코드 자체는 화면에 안 나오고 색 분류에만 사용)
const EV_BAD = new Set(["escalation","non_convergence","failed","parse_failure","wiki_failure","tense_fix_failed"]);
const EV_WARN = new Set(["story_truncated","bible_truncated","violations","tics_residual","reformat_rejected",
  "signal","episode_stuck","plant_backlog","uncast_character","ssot_contradiction","under_norm","spine_incomplete"]);
const EV_OK = new Set(["done","new_entity","relation","registered","debut","bible_done","spine_done","world_done",
  "payoff_detected","reconciled","retrospective_available"]);
// (node,event) → 사람이 읽는 한 줄. 미등재는 node 한글명만(코드/영어 노출 0). 두 번째 인자=상세(있을 때만).
function friendly(ev){
  const node = nodeLabel(ev.node), e = ev.event;
  const kinds = kindList(ev.kinds || ev.hard || ev.fixing);   // 위반/교정 코드는 kinds·hard·fixing 중 하나에 담겨 옴
  // node 기준 우선 처리(consistency_check·partial_rewrite 는 event="done"/"start" 라 일반 분기에 먼저 걸리지 않게)
  if(ev.node==="consistency_check") return ["일관성 검사", ev.hard>0?`설정 충돌 ${ev.hard}건${kinds?` · ${kinds}`:""}`:"이상 없음"];
  if(ev.node==="partial_rewrite") return ["부분 교정", kinds?`교정: ${kinds}`:""];
  switch(e){
    case "start": return [node, ""];
    case "done":  return [node, ""];
    case "extend": return ["분량 보강", "이어서 더 씁니다"];
    case "reformat": return ["문단 정리", ""];
    case "reformat_rejected": return ["문단 정리 보류", ""];
    case "story_truncated": case "bible_truncated": return ["오래된 맥락 정리", ev.dropped?`${ev.dropped}건 압축`:""];
    case "story_underfilled": return ["누적 줄거리 적음", `${ev.used}/${ev.budget}자만 사용(경계 직후)`];
    case "violations": return ["구성 점검", kinds];
    case "non_convergence": return ["교정 한계", kinds];
    case "ssot_contradiction": return ["설정 충돌 검토", kinds];
    case "tic_fixes": return ["반복 표현 정리", ""];
    case "tail_regen": return ["결말 다시 쓰기", ""];
    case "tics_residual": return ["반복 표현 남음", ""];
    case "tense_fixes": return ["시제 정리", ev.applied?`${ev.applied}건`:""];
    case "tense_fix_failed": return ["시제 정리 실패", ""];
    case "continuity_fixes": return ["출고 검수", ""];
    case "new_entity": return ["새 설정 추가", ev.entity||""];
    case "relation": return ["관계 추가", ""];
    case "registered": return ["등장인물 준비", ev.entity||""];
    case "debut": return ["새 인물 등장", ev.entity||""];
    case "uncast_character": return ["미설계 인물 감지", ev.entity||""];
    case "escalation": return ["검토 필요", kinds];
    case "wiki_failure": return ["노트 정리 일부 실패", ""];
    case "signal": case "episode_stuck": case "plant_backlog": return ["전개 점검", ""];
    // 작가 가시화 신호(측정·advisory — 강제 아님)
    case "under_norm": return ["분량 미달", `${ev.chars}자 (권장 ${ev.norm})`];
    case "new_commits": return ["새 고유명사", `${ev.count}개${ev.names&&ev.names.length?` · ${ev.names.slice(0,4).join(", ")}`:""}`];
    case "promise_state": return ["약속 원장", `미회수 ${ev.open}${ev.since_payoff!=null?` · 회수 후 ${ev.since_payoff}화`:""}`];
    case "payoff_detected": return ["약속 회수", `${ev.count}개`];
    case "reconciled": return ["약속 정산", `지불 ${ev.paid}개 · 새 약속 ${ev.opened}개`];
    case "retrospective_available": return ["아크 완결", `'${ev.arc||""}' — 연재 관리에서 회고를 받아보세요`];
    case "window": return ["연재 페이싱", `훅 단조 ${ev.hook_monotony} · 새 명사 ${ev.new_names}개`];
    case "prediction": return ["독자 반응", `${ev.pay_next?"다음 화 결제 의향":"이탈 위험"}${ev.got?` · 얻은 것: ${ev.got}`:""}`];
    case "spine_incomplete": return ["설계 보완 필요", ""];
    case "parse_failure": case "dup_skip": case "prop_skip": return [node, ""];
    // worldgen 단계(작품 생성용 — 회차 로그에는 안 옴, 안전상 포함)
    case "world_done": case "spine_done": case "bible_done": case "bible": return [node, ""];
  }
  // 일반 진행: 충돌/위반 카운트가 있으면 점검 한 줄로
  if(ev.hard!==undefined) return [node, ev.hard>0?`설정 충돌 ${ev.hard}건${kinds?` · ${kinds}`:""}`:"이상 없음"];
  return [node, ""];
}
function logEvent(ev, det, rawEvent){
  const cls = EV_BAD.has(ev.event)?"bad":EV_WARN.has(ev.event)?"warn":EV_OK.has(ev.event)?"ok":"";
  let label, extra;
  if(rawEvent){ label = `${nodeLabel(ev.node)} · ${ev.event}`; extra = det||""; }   // 이미 한글로 만든 문구(예: "3화 시작")
  else { const f = friendly(ev); label = f[0]; extra = det!==undefined&&det!==""?det:f[1]; }
  if(ev.round!==undefined && ev.round>0 && ev.event!=="extend") label += ` (${ev.round+1}차 교정)`;
  const line = document.createElement("div");
  line.className = `ev ${cls}`;
  line.innerHTML = `<span class="node">${esc(label)}</span><span class="det">${esc(extra)}</span>`;
  const log = $("#harness-log"); log.appendChild(line); log.scrollTop = log.scrollHeight;
}
async function onComplete(data){
  STATE.generating = false; genStop(); _genES = null;
  if(!($("#dir-keep") && $("#dir-keep").checked)) $("#directive").value="";   // '계속 적용' 체크 시 지시 유지(아크 표준 제약)
  if(data.completed){   // 엔딩 도달 → 완결
    $("#gen-btn").disabled = true; $("#gen-btn").textContent = "완결되었습니다";
    $("#gen-result").innerHTML = `작품이 완결되었습니다 · ${data.current_chapter}화`;
    STATE.project = await api.get(`/api/projects/${STATE.project.id}`);
    STATE.activeChapter = data.current_chapter; STATE.chapterPage = chPageOf(data.current_chapter);
    renderHeader(); renderChapters(); renderReader(); refreshSection(STATE.section);
    return;
  }
  $("#gen-btn").disabled = false;
  const r = data.record;
  const badge = r.status==="FINALIZED"?'<span class="badge fin">완성</span>':'<span class="badge esc">검토 필요</span>';
  const fail = data.failures&&data.failures.length?` · 주의 ${data.failures.length}건`:"";
  const drift = r.drift_signals&&r.drift_signals.length?` · 전개 점검 ${r.drift_signals.length}건`:"";
  // G2 독자 반응(advisory — 작가 가시화)
  const rf=r.reader_feedback;
  const readerBlock=(rf&&(rf.why||rf.got))?`<div class="reader-react ${rf.pay_next?'pos':'neg'}">
    <b>독자 반응</b> — ${rf.pay_next?'다음 화 결제 의향 있음':'이탈 위험'}${rf.got?` · 얻은 것: ${esc(rf.got)}`:''}
    ${rf.why?`<div class="muted small">${esc(rf.why)}</div>`:''}</div>`:"";
  // G3: 아크 완결 시 회고 권유(nudge — 작가가 받을지 결정)
  const retroNudge=(data.events||[]).some(e=>e.event==="retrospective_available")
    ? `<div class="retro-nudge">📋 아크가 끝났어요 — <a ${ACT} onclick="openRetro()">연재 관리에서 회고 받기</a></div>`:"";
  $("#gen-result").innerHTML = `${r.chapter}화 ${badge} · AI 사용량 +${data.usage_delta.chat_calls}회${fail}${drift}${retroNudge}${readerBlock}`;
  // 상태 갱신
  STATE.project = await api.get(`/api/projects/${STATE.project.id}`);
  STATE.activeChapter = r.chapter;
  STATE.chapterPage = chPageOf(r.chapter);
  renderHeader(); renderChapters(); renderReader(); renderGenInspect();
  if(STATE.section==="write"||STATE.section===undefined) setHashSilent(`#/p/${STATE.project.id}/ch/${r.chapter}`);   // 집필 중이면 새 회차를 URL에
  else refreshSection(STATE.section);   // 다른 섹션을 보고 있었다면 그 섹션도 최신화
}
function onFail(data){
  STATE.generating = false; genStop(); _genES = null; $("#gen-btn").disabled = false;
  $("#gen-result").innerHTML = `회차를 쓰지 못했습니다: ${esc(data.message||"")} <button class="primary" onclick="generateNext()">다시 시도</button>`;
  logEvent({node:"harness",event:"실패"}, data.message||"", true);
  renderReader();
}

// ---------- 인스펙터 ----------
async function loadOntology(){
  const el = $("#inspect-onto");
  try{
    const o = await api.get(`/api/projects/${STATE.project.id}/ontology`);
    const chars = o.characters.map(c=>{
      const kv = Object.entries(c.attrs).map(([k,v])=>`<span>${esc(k)}: ${esc(String(v))}</span>`).join("");
      const dead = c.status==="dead"?'<span class="dead">사망</span>':'<span>생존</span>';
      return `<div class="ent"><span class="en-name">${esc(c.name)}</span>${c.provisional?'<span class="prov">AI 추가 · 미확정</span>':""}
        <div class="kv">${dead}${kv}</div></div>`;
    }).join("");
    const tl = o.timeline.map(t=>`<div class="rule-item">⏱ ${esc(t.entity)} · ${esc(t.attr)}=${esc(String(t.value))} (${t.eff_from}화부터) ${esc(t.reason||"")}</div>`).join("");
    el.innerHTML = `<div class="inspect-head">현재 ${o.as_of_chapter}화 기준 · 작가가 확정한 공식 설정입니다.</div>
      ${chars}
      <h4 style="margin-top:1em">세계 규칙</h4>${o.rules.map(r=>`<div class="rule-item">⚖️ ${esc(r)}</div>`).join("")||'<span class="muted">없음</span>'}
      <h4 style="margin-top:1em">예정된 변화</h4>${tl||'<span class="muted">없음</span>'}`;
  }catch(e){ el.innerHTML = `<span class="muted">불러오지 못했습니다: ${esc(e.message)}</span>`; }
}
async function loadWiki(){
  const el = $("#inspect-wiki");
  try{
    const w = await api.get(`/api/projects/${STATE.project.id}/wiki`);
    const LIFE = {ACTIVE:"활성", DRAFT:"초안", ARCHIVED:"보관"};
    const pages = w.pages.filter(p=>p.body).map(p=>`<div class="ent"><span class="en-name">${esc(p.page_id)}</span>
      <span class="prov">${esc(LIFE[p.lifecycle]||p.lifecycle)}</span>
      <div class="small" style="margin-top:.4em">${esc(p.body)}</div></div>`).join("");
    const lint = w.lint.map(l=>`<div class="lint-item">⚠️ ${esc(l.entity)} — ${esc(l.text)}</div>`).join("")||'<span class="muted small">점검 결과 이상 없음</span>';
    el.innerHTML = `<div class="inspect-head">현재 ${w.watermark}화 기준 · 회차마다 자동으로 정리되는 인물·세계 노트입니다.</div>
      <h4>자동 점검</h4>${lint}
      <h4 style="margin-top:1em">노트 (${w.pages.filter(p=>p.body).length})</h4>${pages||'<span class="muted">없음</span>'}`;
  }catch(e){ el.innerHTML = `<span class="muted">불러오지 못했습니다: ${esc(e.message)}</span>`; }
}

// ---------- 관계도 (cytoscape 속성그래프) ----------
let CY=null, SELECTED=[], SELECTED_EDGE=null, GRAPH_MAX_CH=1;
async function loadGraph(){
  const st=$("#graph-status");
  if(!STATE.project){ return; }
  try{
    const o=await api.get(`/api/projects/${STATE.project.id}/ontology`);
    const g=o.graph||{nodes:[],edges:[],relations:[],types:[]};
    GRAPH_MAX_CH=g.max_chapter||1;
    renderGraph(g); populateRelSelect(g.relations); renderLegend(g.types);
    st.textContent=`인물·세력 ${g.nodes.length} · 관계 ${g.edges.length} · 현재 ${g.max_chapter}화 기준 (확정한 관계는 다음 회차 공식 설정에 반영됩니다)`;
  }catch(e){ st.textContent="관계도를 불러오지 못했습니다: "+esc(e.message); }
}
function renderGraph(g){
  if(!window.cytoscape){ $("#graph-status").textContent="cytoscape 로드 실패(네트워크 확인)"; return; }
  if(CY){ try{CY.destroy();}catch(e){} CY=null; }
  const els=[
    ...g.nodes.map(n=>({data:{id:n.id,label:n.name,color:n.color,shape:n.shape,
        dead:n.dead?1:0,prov:n.provisional?1:0}})),
    ...g.edges.map(e=>({data:{id:e.id,source:e.src,target:e.dst,label:e.label,color:e.color,
        estyle:e.line_style,arrow:e.directed?'triangle':'none',trust:e.trust_tier}})),
  ];
  // 라이트테마 색은 CSS 토큰에서 런타임으로 읽는다(하드코딩 금지 — DESIGN.md §2, 종이톤 회귀 차단)
  const C={ink:cssVar('--ink'),inkSoft:cssVar('--ink-soft'),paper:cssVar('--paper'),
    accent:cssVar('--accent'),bad:cssVar('--bad'),muted:cssVar('--muted'),lineStrong:cssVar('--line-strong')};
  CY=cytoscape({
    container:$("#cy"), elements:els, wheelSensitivity:0.2,
    style:[
      {selector:'node',style:{'background-color':'data(color)','shape':'data(shape)',
        'label':'data(label)','color':C.ink,'font-size':'11px','font-weight':600,'text-valign':'bottom',
        'text-halign':'center','text-margin-y':4,'text-outline-color':C.paper,'text-outline-width':2.5,
        'width':38,'height':38,'border-width':2,'border-color':C.lineStrong}},
      {selector:'node[dead=1]',style:{'border-color':C.bad,'border-width':3,'opacity':0.55}},
      {selector:'node[prov=1]',style:{'border-style':'dashed','border-color':C.accent}},
      {selector:'node:selected',style:{'border-color':C.accent,'border-width':5}},
      {selector:'edge',style:{'width':2,'line-color':'data(color)','target-arrow-color':'data(color)',
        'target-arrow-shape':'data(arrow)','line-style':'data(estyle)','curve-style':'bezier',
        'label':'data(label)','font-size':'9px','color':C.inkSoft,'text-rotation':'autorotate',
        'text-background-color':C.paper,'text-background-opacity':0.92,'text-background-padding':2}},
      {selector:'edge[trust="narrative_inferred"]',style:{'line-style':'dashed','opacity':0.6,
        'line-color':C.muted,'target-arrow-color':C.muted,'width':1.5}},
    ],
    layout:{name:'cose',animate:false,padding:30,nodeRepulsion:9000,idealEdgeLength:95},
  });
  SELECTED=[]; SELECTED_EDGE=null; updateSelBar();
  CY.on('tap','node',evt=>toggleSelect(evt.target.id()));
  CY.on('tap','edge',evt=>selectEdge(evt.target.data()));
  CY.on('tap',evt=>{ if(evt.target===CY){ SELECTED=[]; SELECTED_EDGE=null; CY.$(':selected').unselect();
                                          $("#rel-end-btn").disabled=true; updateSelBar(); }});
}
function selectEdge(d){
  SELECTED_EDGE={src:d.source,dst:d.target,label:d.label};
  // rel_id 는 edge id 의 접두에서 복원: '{rel_id}:{src}->{dst}...'
  SELECTED_EDGE.rel_id=(d.id||"").split(":")[0];
  $("#graph-sel").textContent=`관계 선택됨: ${esc(d.label||SELECTED_EDGE.rel_id)} — 종료할 수 있어요`;
  $("#rel-end-btn").disabled=false;
}
async function endRelation(){
  if(!SELECTED_EDGE) return;
  try{
    const r=await api.post(`/api/projects/${STATE.project.id}/relations/end`,
      {src_id:SELECTED_EDGE.src,dst_id:SELECTED_EDGE.dst,rel_id:SELECTED_EDGE.rel_id,eff_to:GRAPH_MAX_CH});
    $("#graph-status").textContent=`관계를 종료했습니다 (${GRAPH_MAX_CH}화부터): ${esc(SELECTED_EDGE.label||'')}`;
    SELECTED_EDGE=null; $("#rel-end-btn").disabled=true; await loadGraph();
  }catch(e){ $("#graph-status").textContent="처리하지 못했습니다: "+esc(e.message); }
}
function toggleSelect(id){
  SELECTED_EDGE=null; $("#rel-end-btn").disabled=true;   // 노드 선택 시 엣지 선택 해제
  const i=SELECTED.indexOf(id);
  if(i>=0){ SELECTED.splice(i,1); CY.$id(id).unselect(); }
  else{ SELECTED.push(id); CY.$id(id).select();
        if(SELECTED.length>2){ CY.$id(SELECTED.shift()).unselect(); } }
  updateSelBar();
}
function updateSelBar(){
  const bar=$("#graph-sel"), btn=$("#rel-add-btn");
  const names=SELECTED.map(id=>(CY&&CY.$id(id).data('label'))||id);
  bar.textContent = SELECTED.length===0 ? "두 인물을 클릭해 관계를 이어보세요"
    : SELECTED.length===1 ? `${names[0]} → (상대를 고르세요)` : `${names[0]} → ${names[1]}`;
  bar.classList.toggle('ready',SELECTED.length===2);
  btn.disabled=SELECTED.length!==2;
}
function populateRelSelect(relations){
  $("#rel-select").innerHTML=(relations||[]).map(r=>
    `<option value="${esc(r.rel_id)}">${esc(r.label)}${r.directed?' →':' ↔'}</option>`).join("");
}
async function submitRelation(){
  if(SELECTED.length!==2) return;
  const [src,dst]=SELECTED, rel=$("#rel-select").value, eff=parseInt($("#rel-efffrom").value||"1",10);
  try{
    const r=await api.post(`/api/projects/${STATE.project.id}/relations`,
      {src_id:src,dst_id:dst,rel_id:rel,eff_from:eff||1});
    $("#graph-status").textContent=r.created?`관계를 이었습니다: ${esc(r.label)}`:"이미 있는 관계입니다";
    SELECTED=[]; await loadGraph();
  }catch(e){ $("#graph-status").textContent="처리하지 못했습니다: "+esc(e.message); }
}
async function promptAddEntity(){
  const name=prompt("새 인물·세력·장소의 이름:"); if(!name) return;
  const etype=((prompt("종류 (character 인물 / faction 세력 / place 장소 / item 사물 / event 사건):","character")||"character").trim())||"character";
  try{
    const r=await api.post(`/api/projects/${STATE.project.id}/entities`,{name,etype});
    $("#graph-status").textContent=r.created?`추가했습니다: ${esc(name)}`+(r.unknown_type?" (기본 모양으로 표시)":""):"이미 있는 이름입니다";
    await loadGraph();
  }catch(e){ $("#graph-status").textContent="처리하지 못했습니다: "+esc(e.message); }
}
function renderLegend(types){
  $("#graph-legend").innerHTML=(types||[]).map(x=>
    `<span class="lg"><span class="dot" style="background:${esc(x.color)}"></span>${esc(x.label)}</span>`).join("")
    + ` <span class="lg"><span class="dot" style="background:${cssVar('--bad')}"></span>사망</span>`
    + ` <span class="lg"><span class="dot" style="background:${cssVar('--accent')}"></span>AI·작가 추가</span>`
    + ` <span class="lg">┄ 점선 = 추정 관계(아직 미확정)</span>`;
}

// ---------- 유틸 ----------
function esc(s){ return String(s==null?"":s).replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m])); }
function cssVar(n){ return getComputedStyle(document.documentElement).getPropertyValue(n).trim()||""; }   // 토큰값 런타임 조회(cytoscape 등 캔버스 색)

// ---------- 경량 모달 (prompt/confirm 대체 — 포커스 이동·Esc·백드롭 닫힘·포커스 복원·Tab 트랩) ----------
let _modalPrevFocus = null;
function openModal(innerHTML){
  _modalPrevFocus = document.activeElement;
  const ov = document.createElement("div");
  ov.className = "modal-overlay";
  ov.innerHTML = `<div class="modal-card" role="dialog" aria-modal="true">${innerHTML}</div>`;
  const _h = ov.firstElementChild.querySelector("h1,h2,h3,h4");                                // 제목→aria-labelledby(스크린리더)
  if(_h){ if(!_h.id) _h.id = "modal-title-"+Date.now().toString(36); ov.firstElementChild.setAttribute("aria-labelledby", _h.id); }
  ov.addEventListener("mousedown", e=>{ if(e.target===ov) closeModal(ov); });                 // 백드롭 클릭
  ov.addEventListener("click", e=>{ if(e.target.closest("[data-close]")) closeModal(ov); });   // 취소 버튼
  ov.addEventListener("keydown", e=>{
    if(e.key==="Escape"){ e.stopPropagation(); closeModal(ov); return; }
    if(e.key==="Tab"){
      const f = ov.querySelectorAll('input:not([disabled]),select:not([disabled]),textarea:not([disabled]),button:not([disabled]),[tabindex]:not([tabindex="-1"])');
      if(!f.length) return;
      const first=f[0], last=f[f.length-1];
      if(e.shiftKey && document.activeElement===first){ e.preventDefault(); last.focus(); }
      else if(!e.shiftKey && document.activeElement===last){ e.preventDefault(); first.focus(); }
    }
  });
  document.body.appendChild(ov);
  return ov;
}
function closeModal(ov){
  if(!ov || !ov.parentNode) return;
  ov.remove();
  if(_modalPrevFocus && _modalPrevFocus.focus){ try{ _modalPrevFocus.focus(); }catch(e){} }   // 트리거로 포커스 복원
  _modalPrevFocus = null;
}

// 내보내기 드롭다운: 바깥 클릭·Esc 로 닫기(열어둔 채 떠 있는 문제 해소)
document.addEventListener("click", e=>{
  document.querySelectorAll(".export-wrap.open").forEach(w=>{ if(!w.contains(e.target)) w.classList.remove("open"); });
});
document.addEventListener("keydown", e=>{
  if(e.key==="Escape") document.querySelectorAll(".export-wrap.open").forEach(w=>w.classList.remove("open"));
});

// 일시 알림(toast) — 숨은 섹션에 메시지가 묻히지 않게(role=status/alert). 자동 소멸.
let _toastT=null;
function toast(msg, kind){
  let t=$("#toast"); if(!t){ t=document.createElement("div"); t.id="toast"; t.className="toast"; document.body.appendChild(t); }
  t.className="toast "+(kind||"")+" show"; t.setAttribute("role", kind==="bad"?"alert":"status"); t.textContent=msg;
  if(_toastT) clearTimeout(_toastT); _toastT=setTimeout(()=>{ t.classList.remove("show"); }, 3400);
}
async function health(){ const el=$("#health"); if(!el) return;   // 정상이면 숨김, 끊겼을 때만 안심 문구
  try{ const h=await api.get("/api/health"); el.classList.add("hidden"); el.title=`${h.provider} · ${h.model}`; }
  catch(e){ el.classList.remove("hidden"); el.classList.add("bad"); el.textContent="연결 끊김 · 작업은 보관됩니다"; } }

// ---------- 퇴고(회차 본문 사후 다듬기 — 사실 불변) ----------
// 작가 지시형 · 구간 단위(선택) · before→after diff · 채택/취소 · 되돌리기.
// 용어 §5: 'violation/checker/ontology/G-A/G-B' 노출 금지 — 작가 언어만(퇴고·공식 설정·작가 지시).
const REVISE_CHIPS = ["더 간결하게", "대사 톤 차갑게", "묘사 줄이기", "문장 이어 읽기 좋게"];
function openReviseModal(chapterNo){
  const c = ((STATE.project&&STATE.project.chapters)||[]).find(x=>x.chapter===chapterNo);
  if(!c){ toast("회차를 찾을 수 없습니다", "bad"); return; }
  const chips = REVISE_CHIPS.map(t=>`<button type="button" class="revise-chip" onclick="reviseChip(this)">${esc(t)}</button>`).join("");
  const ov = openModal(`
    <h3>퇴고 — ${chapterNo}화</h3>
    <p class="muted small" style="margin:.2em 0 .8em">이미 쓴 회차의 본문을 작가 지시로 다듬습니다. 설정·사건·이름·수치는 바뀌지 않아요.</p>
    <label>작가 지시
      <textarea id="rv-directive" rows="3" placeholder="예: 이 대목 더 간결하게 / 대사 톤 차갑게"></textarea>
    </label>
    <div class="revise-chips">${chips}</div>
    <label>다듬을 구간 <span class="muted">(선택 — 그 구절을 본문에서 그대로 붙여넣기)</span>
      <textarea id="rv-span" rows="2" placeholder="원문에서 다듬고 싶은 부분만 붙여넣기 (없으면 전체 다듬기)"></textarea>
    </label>
    <div class="revise-toggles">
      <label><input type="checkbox" id="rv-reformat"> 행 정렬 다듬기</label>
      <label><input type="checkbox" id="rv-tense"> 시제 교정</label>
    </div>
    <div id="rv-err" class="modal-err hidden"></div>
    <div id="rv-revision-id" class="hidden"></div>
    <div id="rv-guardrail" class="hidden"></div>
    <div id="rv-diff-area" class="hidden"></div>
    <div class="revise-actions">
      <button class="primary" id="rv-submit" onclick="submitRevise(${chapterNo})">다듬기 →</button>
      <button id="rv-accept" class="primary hidden" onclick="acceptRevise(${chapterNo})">채택</button>
      <button data-close>취소</button>
    </div>
  `);
  ov.firstElementChild.classList.add("revise-card");   // 퇴고 모달만 넓게(diff 표시)
  STATE.reviseModal = ov; STATE.reviseChapter = chapterNo;
  // openModal 의 Esc·백드롭·data-close 핸들러는 closeModal(ov)만 호출하고 STATE.reviseModal을 비우지 않는다.
  // 모든 닫힘 경로에서 STATE.reviseModal=null 이 보장되도록 퇴고 전용 cleanup 핸들러를 ov 에 덧등록.
  // (closeModal 은 멱등 — 이미 제거된 ov 재호출은 무해. cleanup 도 STATE 만 비워 중복 호출 안전.)
  ov.addEventListener("mousedown", e=>{ if(e.target===ov) cleanupReviseModal(); });            // 백드롭 클릭
  ov.addEventListener("click", e=>{ if(e.target.closest("[data-close]")) cleanupReviseModal(); }); // 취소 버튼
  ov.addEventListener("keydown", e=>{ if(e.key==="Escape") cleanupReviseModal(); });             // Esc
  const ta = ov.querySelector("#rv-directive"); if(ta) ta.focus();
}
function cleanupReviseModal(){   // 퇴고 모달 닫기 + STATE.reviseModal 초기화(스테일 참조 방지) — 모든 닫힘 경로 공통
  if(STATE.reviseModal){ closeModal(STATE.reviseModal); }
  STATE.reviseModal = null;
}
function reviseChip(btn){   // 예시 칩 → 지시 textarea 채움
  const t = $("#rv-directive"); if(!t) return;
  t.value = btn.textContent; t.focus();
}
function _rvErr(msg){   // 모달 내 오류 표시(없으면 숨김)
  const el = $("#rv-err"); if(!el) return;
  if(msg){ el.textContent = msg; el.classList.remove("hidden"); }
  else { el.textContent = ""; el.classList.add("hidden"); }
}
async function submitRevise(chapterNo){
  const dEl = $("#rv-directive"); const directive = (dEl&&dEl.value||"").trim();
  if(!directive){ _rvErr("작가 지시를 입력해 주세요."); if(dEl) dEl.focus(); return; }
  _rvErr("");
  const passes = [];
  if($("#rv-reformat")&&$("#rv-reformat").checked) passes.push("reformat");
  if($("#rv-tense")&&$("#rv-tense").checked) passes.push("fix_tense");
  const span_text = ($("#rv-span")&&$("#rv-span").value||"").trim();
  const btn = $("#rv-submit"); const restore = btn?btn.textContent:"다듬기 →";
  if(btn){ btn.disabled = true; btn.textContent = "다듬는 중…"; }
  let r;
  try{
    r = await api.post(`/api/projects/${STATE.project.id}/chapters/${chapterNo}/revise`,
                       { directive, span_text, passes });
  }catch(e){
    _rvErr(String(e.message||e));
    // await 중 모달이 닫혔으면(Esc·백드롭) btn 이 DOM 에서 분리됨 — isConnected 가드.
    if(btn && btn.isConnected){ btn.disabled = false; btn.textContent = restore; }
    return;
  }
  // await 중 사용자가 모달을 닫았으면 #rv-* 요소가 전부 사라짐 — 결과 렌더 스킵(TypeError 방지).
  if(!btn || !btn.isConnected){ return; }
  btn.disabled = false; btn.textContent = "다시 다듬기";
  // 무변경(changed:false) — 후보 없음. 작가에게 '효과 없음' 고지(채택 무의미).
  if(r.changed === false || !r.revision_id){
    const idEl = $("#rv-revision-id"); if(idEl) idEl.textContent = "";
    renderDiff(r.before_text||"", r.after_text||"");
    const g = $("#rv-guardrail");
    if(g){
      g.classList.remove("hidden");
      g.innerHTML = `<span class="gr-pill warn">바뀐 부분이 없습니다 — 지시를 더 구체적으로 적어 보세요</span>`;
    }
    const ab = $("#rv-accept"); if(ab) ab.classList.add("hidden");
    return;
  }
  const idEl = $("#rv-revision-id"); if(idEl) idEl.textContent = r.revision_id;
  renderDiff(r.before_text, r.after_text);
  renderGuardrail(r.guardrail);
}
// 단어 단위 LCS diff — 의존성 0 순수 JS.
function _rvTokenize(s){   // 단어/공백/구두점을 토큰으로(공백 보존해 재조립 시 자연스럽게)
  return String(s==null?"":s).match(/\s+|[^\s]+/g) || [];
}
function _lcs(a, b){   // LCS 길이 DP 테이블
  const n=a.length, m=b.length;
  const dp = Array.from({length:n+1}, ()=>new Int32Array(m+1));
  for(let i=n-1;i>=0;i--){
    for(let j=m-1;j>=0;j--){
      dp[i][j] = a[i]===b[j] ? dp[i+1][j+1]+1 : Math.max(dp[i+1][j], dp[i][j+1]);
    }
  }
  return dp;
}
function diffWords(before, after){   // → [{type:'eq'|'del'|'add', text}]
  const a=_rvTokenize(before), b=_rvTokenize(after), dp=_lcs(a,b), out=[];
  let i=0, j=0;
  while(i<a.length && j<b.length){
    if(a[i]===b[j]){ out.push({type:"eq", text:a[i]}); i++; j++; }
    else if(dp[i+1][j] >= dp[i][j+1]){ out.push({type:"del", text:a[i]}); i++; }
    else { out.push({type:"add", text:b[j]}); j++; }
  }
  while(i<a.length){ out.push({type:"del", text:a[i++]}); }
  while(j<b.length){ out.push({type:"add", text:b[j++]}); }
  return out;
}
// 단어 단위 LCS(diffWords)는 O(n×m) 시간·공간 — 장문 회차(수천 토큰)에서 DP 테이블이 수 MB,
// 동기 실행이 메인 스레드를 수십 ms 블로킹한다(나란히 보기 토글마다 재실행). 토큰 상한을 둬 초과 시
// 인라인 diff 를 생략하고 나란히 보기 전용으로 자동 전환(렌더 블로킹 회피).
const REVISE_DIFF_TOKEN_CAP = 1000;
function _rvTooLargeForInline(before, after){
  return _rvTokenize(before).length > REVISE_DIFF_TOKEN_CAP || _rvTokenize(after).length > REVISE_DIFF_TOKEN_CAP;
}
function renderDiff(before, after){
  const el = $("#rv-diff-area"); if(!el) return;
  STATE.reviseBefore = before; STATE.reviseAfter = after; STATE.reviseSideBySide = false;
  // 토큰 상한 초과 — LCS DP 블로킹 회피. 인라인 생략하고 나란히 보기로 자동 전환 + 안내.
  STATE.reviseTooLarge = _rvTooLargeForInline(before, after);
  if(STATE.reviseTooLarge){ STATE.reviseSideBySide = true; renderSideBySide(true); return; }
  const parts = diffWords(before, after);
  const inline = parts.map(p=>{
    const html = esc(p.text).replace(/\n/g, "<br>");
    if(p.type==="del") return `<span class="diff-del">${html}</span>`;
    if(p.type==="add") return `<span class="diff-add">${html}</span>`;
    return html;
  }).join("");
  el.classList.remove("hidden");
  el.innerHTML = `<div class="diff-bar">
      <span class="muted small">바뀐 부분: <span class="diff-del">삭제</span> · <span class="diff-add">추가</span></span>
      <button type="button" class="diff-toggle" onclick="toggleSideBySide()">나란히 보기</button>
    </div>
    <div class="diff-inline">${inline}</div>`;
}
function renderSideBySide(tooLarge){   // 나란히 보기(원문/다듬은 글) — 인라인 LCS 없이 안전. tooLarge 면 인라인 토글 숨김+안내.
  const el = $("#rv-diff-area"); if(!el) return;
  const bf = esc(STATE.reviseBefore||"").replace(/\n/g,"<br>");
  const af = esc(STATE.reviseAfter||"").replace(/\n/g,"<br>");
  el.classList.remove("hidden");
  const bar = tooLarge
    ? `<span class="muted small">왼쪽: 원문 · 오른쪽: 다듬은 글 <span class="muted">— 분량이 많아 변경 표시 없이 나란히 보여드려요</span></span>`
    : `<span class="muted small">왼쪽: 원문 · 오른쪽: 다듬은 글</span>
       <button type="button" class="diff-toggle" onclick="toggleSideBySide()">인라인 보기</button>`;
  el.innerHTML = `<div class="diff-bar">${bar}</div>
    <div class="diff-side">
      <div class="diff-side-before">${bf}</div>
      <div class="diff-side-after">${af}</div>
    </div>`;
}
function toggleSideBySide(){
  const el = $("#rv-diff-area"); if(!el) return;
  if(STATE.reviseTooLarge) return;   // 장문 — 인라인 LCS 블로킹 회피. 나란히 보기 고정(토글 비활성).
  STATE.reviseSideBySide = !STATE.reviseSideBySide;
  if(!STATE.reviseSideBySide){ renderDiff(STATE.reviseBefore, STATE.reviseAfter); return; }
  renderSideBySide(false);
}
function renderGuardrail(g){
  const el = $("#rv-guardrail"); if(!el) return;
  el.classList.remove("hidden");
  const ab = $("#rv-accept");
  g = g || {};
  let html = "";
  if(g.passed){
    // '보증'이 아니라 '검사 결과' — 추출기가 못 잡는 케이스가 남을 수 있으므로 디프 검토를 유도(정직한 카피)
    html = `<span class="gr-pill ok">✓ 설정 충돌은 발견되지 않았어요</span><div class="gr-hint">자동 점검 결과예요 — 바뀐 부분은 한 번 확인해 주세요</div>`;
  } else {
    if(g.G_A_passed === false){
      const kinds = (g.new_hard||[]).map(h=>kindLabel(h.kind)).filter(Boolean);
      html += `<div class="gr-row"><span class="gr-pill bad">기존 설정과 충돌하는 표현이 생겼습니다</span>`;
      if(kinds.length) html += `<div class="gr-list">${esc([...new Set(kinds)].join(", "))}</div>`;
      html += `</div>`;
    }
    if(g.G_B_passed === false){
      html += `<div class="gr-row"><span class="gr-pill bad">이름·수치가 바뀌었습니다</span>`;
      const cc = (g.claim_changes||[]).slice(0,8).map(c=>
        `<div class="gr-change"><b>${esc(c.entity)}</b> · ${esc(c.key)}: <span class="diff-del">${esc(c.before)}</span> → <span class="diff-add">${esc(c.after)}</span></div>`).join("");
      if(cc) html += `<div class="gr-list">${cc}</div>`;
      html += `</div>`;
    }
    if(g.length_ok === false){
      html += `<div class="gr-row"><span class="gr-pill bad">분량이 너무 많이 바뀌었습니다</span></div>`;
    }
    if(!html) html = `<div class="gr-row"><span class="gr-pill bad">${esc(g.reason||"다듬은 결과를 채택할 수 없습니다")}</span></div>`;
  }
  // advisory(신규 표현) — 비차단. 노랑 pill.
  if((g.new_keys_advisory||[]).length){
    html += `<div class="gr-row"><span class="gr-pill warn">새 표현 추가됨 (설정 변경 아님)</span></div>`;
  }
  el.innerHTML = html;
  // 채택 버튼: 통과 시에만 활성/표시
  if(ab){
    if(g.passed){ ab.classList.remove("hidden"); ab.disabled = false; }
    else { ab.classList.add("hidden"); ab.disabled = true; }
  }
}
async function acceptRevise(chapterNo){
  const idEl = $("#rv-revision-id"); const revId = idEl?idEl.textContent.trim():"";
  if(!revId){ _rvErr("채택할 후보가 없습니다. 먼저 다듬어 주세요."); return; }
  _rvErr("");
  const btn = $("#rv-accept"); const restore = btn?btn.textContent:"채택";
  if(btn){ btn.disabled = true; btn.textContent = "채택 중…"; }
  try{
    await api.post(`/api/projects/${STATE.project.id}/chapters/${chapterNo}/revise/accept`,
                   { revision_id: revId });
  }catch(e){
    _rvErr(String(e.message||e));   // 409=가드레일 재검증 실패 등
    if(btn){ btn.disabled = false; btn.textContent = restore; }
    return;
  }
  STATE.project = await api.get(`/api/projects/${STATE.project.id}`);   // 최신화(본문·이력 갱신)
  renderChapters(); renderReader();
  cleanupReviseModal();   // 닫기 + STATE.reviseModal 초기화(스테일 참조 방지)
  toast("퇴고가 적용됐습니다", "ok");
}
async function undoRevise(chapterNo){
  if(!confirm("마지막 퇴고를 되돌릴까요? 본문이 다듬기 전으로 돌아갑니다.")) return;
  try{
    await api.post(`/api/projects/${STATE.project.id}/chapters/${chapterNo}/revise/undo`, {});
  }catch(e){
    toast(String(e.message||e), "bad"); return;
  }
  STATE.project = await api.get(`/api/projects/${STATE.project.id}`);
  renderChapters(); renderReader();
  toast("퇴고를 되돌렸습니다", "ok");
}

health(); renderStartExamples(); router();   // 라우터가 현재 해시(기본=홈) 렌더 — 새로고침/딥링크 복원
