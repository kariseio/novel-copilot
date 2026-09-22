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
  drift:"전개 점검", summarize:"줄거리 정리", finalize:"마무리", worldgen:"세계 설계", connect:"",
  ending_contract:"엔딩 계약", derivatives:"퇴고 반영 점검" };
// XR-7: 퇴고 후 '옛 본문 기준'으로 남은 파생물 이름 → 작가 언어(용어 §5). 회차 배지·생성 패널·재계산 버튼이 공유.
const _staleLabels = {wiki:"인물 노트", promise_ledger:"복선·약속 정리", claim_audit:"연속성 점검",
  reader_feedback:"독자 반응 예측", dialogue_ledger:"대사 정리"};
function staleLabel(k){ return _staleLabels[k] || "파생 자료"; }
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
  async patch(u,b){ const r = await fetch(u,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify(b||{})});
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
function _onlyView(id){ ["view-home","view-create","view-project","view-viewer","view-skills","view-dashboard"].forEach(v=>$("#"+v).classList.toggle("hidden", v!==id)); }
async function router(){
  if(_routing) return; _routing = true;
  try{
    const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
    if(!parts.length){ _onlyView("view-home"); loadProjects(); return; }
    if(parts[0] === "skills"){ _onlyView("view-skills"); renderSkillsLibrary(); return; }   // 전역 스킬 라이브러리(작품 무관)
    if(parts[0] === "dashboard"){ _onlyView("view-dashboard"); renderDashboard(); return; }   // FI-4 전역 관측 대시보드(작품 무관)
    if(parts[0] === "new"){
      if(STATE.draft && STATE.draft.id !== undefined){ _onlyView("view-create"); renderWorldSkillPicker(); }
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
        let chChanged = false;   // 활성 회차가 실제로 바뀐 경우에만 스크롤 최상단(같은 회차 재클릭엔 영향 없게)
        if((STATE.project.chapters||[]).some(c=>c.chapter===ch)){
          chChanged = STATE.activeChapter !== ch;
          STATE.activeChapter = ch; STATE.chapterPage = chPageOf(ch);
        }
        showSection("write");
        if(chChanged){ const cb=$("#chapter-body"); if(cb) cb.scrollTop=0; window.scrollTo({top:0}); }   // 본문(자체 스크롤 70vh)·페이지 모두 최상단
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
const PROJ_SECTIONS = ["overview","write","bible","cast","world","serial","skills"];
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
  else if(sec==="skills") renderSkillsManager();
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
    <div class="panel ov-hero ov-hero-cover">
      <div class="ov-cover" id="ov-cover">${coverBlockHtml(p)}</div>
      <div class="ov-hero-main">
        <div class="eyebrow">${esc(p.world.genre||"작품")}${p.world.tone?` · ${esc(p.world.tone)}`:""}</div>
        <h3 class="ov-logline">${esc(p.world.premise||p.world.title||"무제")}</h3>
        <div class="ov-actions">
          <button class="primary" onclick="overviewWrite(${last?0:1})">${last?`${last}화에 이어 쓰기 →`:"1화 쓰기 →"}</button>
          <button onclick="openViewer()">📖 읽기 모드</button>
          <button class="btn-ghost" onclick="openMetaModal()">✏️ 정보 수정</button>
        </div>
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
// ---------- 작품 정보 수정(제목·한 줄 소개·소개 — 작가 직접, 설정집 편집 계보) ----------
// seed 는 안 바꾼다(최초 시드의 이력) — 표시·생성 컨텍스트가 읽는 world 만 수정. 저장 시 서버가 세션을
// 재구성해 다음 회차부터 새 메타가 반영된다(자동 재생성·검증 0 — 무강제).
function openMetaModal(){
  const p = STATE.project; if(!p) return;
  const w = p.world || {};
  const ov = openModal(`
    <h3>작품 정보 수정</h3>
    <label>제목
      <input id="meta-title" type="text" maxlength="200" value="${esc(w.title||"")}">
    </label>
    <label>한 줄 소개 <span class="muted">(개요에 크게 표시 — 비워도 돼요)</span>
      <textarea id="meta-premise" rows="2" maxlength="500">${esc(w.premise||"")}</textarea>
    </label>
    <label>소개 <span class="muted">(시놉시스 — 이후 회차 생성에 참고돼요)</span>
      <textarea id="meta-synopsis" rows="6" maxlength="4000">${esc(w.synopsis||"")}</textarea>
    </label>
    <div id="meta-err" class="modal-err hidden"></div>
    <div class="revise-actions">
      <button class="primary" id="meta-save" onclick="saveMeta()">저장</button>
      <button data-close>취소</button>
    </div>`);
  const t = ov.querySelector("#meta-title"); if(t) t.focus();
}
async function saveMeta(){
  const err = $("#meta-err");
  const show = (m)=>{ if(!err) return; if(m){ err.textContent=m; err.classList.remove("hidden"); } else { err.textContent=""; err.classList.add("hidden"); } };
  const title = ($("#meta-title")&&$("#meta-title").value||"").trim();
  if(!title){ show("제목을 입력해 주세요."); return; }
  show("");
  const btn = $("#meta-save"); if(btn){ btn.disabled=true; btn.textContent="저장 중…"; }
  try{
    await api.put(`/api/projects/${STATE.project.id}/meta`, {
      title,
      premise: ($("#meta-premise")&&$("#meta-premise").value||"").trim(),
      synopsis: ($("#meta-synopsis")&&$("#meta-synopsis").value||"").trim(),
    });
  }catch(e){
    show(String(e.message||e));   // 400=빈 제목 등(서버 사유 그대로)
    if(btn && btn.isConnected){ btn.disabled=false; btn.textContent="저장"; }
    return;
  }
  STATE.project = await fetchProject(STATE.project.id);   // 최신화
  const h = $("#p-title"); if(h) h.textContent = STATE.project.world.title || "무제";   // 작업실 헤더 즉시 반영
  renderOverview();
  const mo = document.querySelector(".modal-overlay"); if(mo) closeModal(mo);
  toast("작품 정보를 수정했습니다", "ok");
}
// ---------- CV-1: 표지 이미지(작가 발동형) ----------
// 표지 블록 — 이미지가 있으면 2:3 표시(캐시버스터), 없으면 플레이스홀더+생성 버튼. 진행 표시는 STATE.coverBusy.
function coverBlockHtml(p){
  const cov=p.cover;
  const busy=STATE.coverBusy;
  if(busy){
    return `<div class="cover-frame busy"><div class="cover-ph"><span class="spin"></span><div class="muted small">표지 그리는 중… (약 20~40초)</div></div></div>`;
  }
  if(cov && cov.filename){
    const src=`/api/projects/${p.id}/cover?v=${encodeURIComponent(cov.created_at||"")}`;
    return `<div class="cover-frame"><img class="cover-img" src="${src}" alt="표지" loading="lazy" title="표지 크게 보기" onclick="openCoverZoom(this.src)"></div>
      <div class="cover-actions">
        <button class="small" onclick="regenCover()">다시 생성</button>
        <button class="small" onclick="openCoverLibrary()">보관함</button>
        <button class="small" onclick="showCoverPrompt()">프롬프트 보기</button>
      </div>`;
  }
  return `<div class="cover-frame empty"><div class="cover-ph"><div class="cover-ph-icon">🖼️</div>
      <div class="muted small">표지가 아직 없어요</div></div></div>
    <div class="cover-actions"><button class="primary small" onclick="genCover()">표지 생성</button></div>`;
}
function _refreshCover(){ const el=$("#ov-cover"); if(el && STATE.project) el.innerHTML=coverBlockHtml(STATE.project); }
async function genCover(promptOverride){
  if(STATE.coverBusy) return;
  STATE.coverBusy=true; _refreshCover();
  try{
    const body = (promptOverride && promptOverride.trim()) ? {prompt:promptOverride.trim()} : {};
    await api.post(`/api/projects/${STATE.project.id}/cover`, body);
    STATE.project = await fetchProject(STATE.project.id);   // 전체 재조회(부분 동기화 stale 방지)
    toast("표지를 생성했어요");
  }catch(e){
    toast("표지 생성 실패: "+e.message, "bad");
  }finally{
    STATE.coverBusy=false; _refreshCover();
  }
}
function showCoverPrompt(){
  const cov=STATE.project&&STATE.project.cover; if(!cov) return;
  alert("이 표지에 쓰인 이미지 프롬프트 —\n\n"+(cov.prompt||"(없음)"));
}
function regenCover(){
  const cov=STATE.project&&STATE.project.cover;
  const cur=(cov&&cov.prompt)||"";
  // 재생성: 프롬프트 수정 입력(선택) — 비우고 확인하면 설정 기반 재합성, 내용을 두면 그 프롬프트로 생성.
  const edited=prompt("표지를 다시 생성합니다.\n프롬프트를 직접 수정하려면 아래를 고치고 확인을,\n비워서 확인하면 설정 기반으로 다시 합성합니다.", cur);
  if(edited===null) return;   // 취소
  genCover(edited.trim());
}
// 표지 확대(라이트박스) — 홈 썸네일·개요 표지·보관함 썸네일 공용. 클릭·Esc 로 닫기.
//   오버레이가 포커스를 가져가(tabindex=-1) 모달의 Esc 핸들러(오버레이 요소 keydown)와 간섭하지 않는다. z-index 90(모달 70 위).
function openCoverZoom(src){
  if(!src) return;
  const prev=document.activeElement;
  const ov=document.createElement("div");
  ov.className="cover-zoom-overlay"; ov.tabIndex=-1;
  ov.setAttribute("role","dialog"); ov.setAttribute("aria-label","표지 크게 보기");
  ov.innerHTML=`<img src="${src}" alt="표지 확대">`;
  const close=()=>{ ov.remove(); if(prev&&prev.focus){ try{ prev.focus(); }catch(e){} } };
  ov.addEventListener("click", close);
  ov.addEventListener("keydown", e=>{ if(e.key==="Escape"){ e.stopPropagation(); close(); } });
  document.body.appendChild(ov); ov.focus();
}
// 표지 보관함 — 생성 이력 갤러리(적용/삭제/새 생성). 모달 열림 여부는 #cover-lib-grid DOM 존재로 판정(스테일 참조 방지).
let _coverLibList = [];
function openCoverLibrary(){
  openModal(`
    <h3>표지 보관함</h3>
    <p class="muted small" style="margin:.2em 0 .9em">지금까지 만든 표지예요. 원하는 표지를 적용하거나 삭제할 수 있어요.</p>
    <div class="cover-lib-top"><button class="primary small" id="cl-gen" onclick="coverLibGenerate()" ${STATE.coverBusy?'disabled':''}>＋ 새 표지 생성</button></div>
    <div id="cover-lib-grid" class="cover-lib-grid"><span class="muted small">불러오는 중…</span></div>
    <div class="modal-actions"><button data-close>닫기</button></div>
  `);
  renderCoverLibGrid();
}
async function renderCoverLibGrid(){
  const grid=$("#cover-lib-grid"); if(!grid) return;   // 모달 닫힘 → no-op
  const pid=STATE.project&&STATE.project.id; if(!pid) return;
  try{
    const r=await api.get(`/api/projects/${pid}/covers`);
    const g=$("#cover-lib-grid"); if(!g) return;   // await 중 닫혔을 수 있음
    _coverLibList=r.covers||[]; const applied=r.applied||null;
    if(!_coverLibList.length){ g.innerHTML='<span class="muted small">아직 만든 표지가 없어요. 위 ‘새 표지 생성’으로 만들어 보세요.</span>'; return; }
    g.innerHTML=_coverLibList.map((c,i)=>{
      const on=c.filename===applied;
      const src=`/api/projects/${pid}/covers/${encodeURIComponent(c.filename)}/file`;
      const day=String(c.created_at||'').slice(0,10);
      return `<div class="cover-lib-item${on?' applied':''}">
        <div class="cli-frame"><img src="${src}" alt="" loading="lazy" title="${esc(c.prompt||'')}" onclick="openCoverZoom(this.src)"></div>
        <div class="cli-meta"><span class="muted tiny">${esc(day)}</span>
          ${on?'<span class="cli-badge">적용 중</span>':`<button class="small" onclick="applyCoverAt(${i})">이 표지 적용</button>`}
          <button class="small cli-del" onclick="deleteCoverAt(${i})">삭제</button></div>
      </div>`;
    }).join("");
  }catch(e){ const g=$("#cover-lib-grid"); if(g) g.innerHTML=`<span class="muted small">불러오지 못했어요: ${esc(e.message)}</span>`; }
}
async function applyCoverAt(i){
  const c=_coverLibList[i]; if(!c||!STATE.project) return;
  try{
    await api.post(`/api/projects/${STATE.project.id}/covers/${encodeURIComponent(c.filename)}/apply`,{});
    STATE.project=await fetchProject(STATE.project.id);   // 전체 재조회(부분 동기화 stale 방지)
    _refreshCover(); renderCoverLibGrid();
    toast("표지를 적용했어요");
  }catch(e){ toast("표지 적용 실패: "+e.message,"bad"); }
}
async function deleteCoverAt(i){
  const c=_coverLibList[i]; if(!c||!STATE.project) return;
  if(!confirm("이 표지를 삭제할까요?")) return;
  try{
    await api.del(`/api/projects/${STATE.project.id}/covers/${encodeURIComponent(c.filename)}`);
    STATE.project=await fetchProject(STATE.project.id);   // 적용본 삭제 시 서버가 최신본으로 폴백 → 재조회로 반영
    _refreshCover(); renderCoverLibGrid();
    toast("표지를 삭제했어요");
  }catch(e){ toast("표지 삭제 실패: "+e.message,"bad"); }
}
async function coverLibGenerate(){
  if(STATE.coverBusy){ toast("표지를 만드는 중이에요","bad"); return; }
  const btn=$("#cl-gen"); if(btn) btn.disabled=true;
  await genCover();   // 기존 생성 경로 재사용(보관함 추가+즉시 적용) — STATE.project·_refreshCover 갱신은 genCover 내부에서
  renderCoverLibGrid();   // 완료 후 그리드 갱신
  const b2=$("#cl-gen"); if(b2) b2.disabled=false;
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
    <p id="style-msg" class="muted small" role="status"></p></div>
    <div id="voice-editor" class="style-edit"><span class="muted small">목소리를 불러오는 중…</span></div>`;
  loadVoices();   // 서술자 목소리(작품 style + 인물 카드) — 별도 조회라 문체 저장과 독립
}

// ---------- 서술자 목소리(설정집 > 문체) ----------
// 회차를 쓸 때 집필에 실제로 들어가는 '목소리' 값은 셋인데 저장 위치가 서로 달라 지금껏 어느 화면에도
// 안 나왔다. 여기 한 곳에 모아 보여주고 고친다:
//   ① 화자 보이스   작품 문체(style.narrator_voice)  — 1인칭 서술 지시 안에 얹힘
//   ② 음성 카드     주인공 인물 카드(entity.voice)    — 지문 서술자 프레임
//   ③ 단계 카드     인물 카드의 상태별 판(voice_stages) — 지정한 추적 속성 값에 따라 ②를 갈아끼움
// 저장은 값마다 제 소유 API 로(문체=PUT style / 인물 카드=PATCH entities/{id}/voice).
let VOICES = null;
async function loadVoices(){
  const el = $("#voice-editor"); if(!el || !STATE.project) return;
  let v;
  try{ v = VOICES = await api.get(`/api/projects/${STATE.project.id}/voices`); }
  catch(e){ el.innerHTML = `<span class="muted">목소리를 불러오지 못했습니다: ${esc(e.message)}</span>`; return; }
  const first = v.pov === "first";
  const narr = (v.entities||[]).find(e=>e.is_narrator);
  const others = (v.entities||[]).filter(e=>e.is_actor && !e.is_narrator);
  const povTxt = first ? "1인칭 — 주인공이 자기 목소리로 들려줍니다"
    : (v.pov==="third_omniscient" ? "3인칭 전지" : "3인칭 밀착");
  // 1인칭이 아니면 ①②는 집필에 들어가지 않는다 — 안 쓰이는 값을 쓰이는 것처럼 보이지 않게 정직히 알린다.
  const povNote = first ? "" :
    `<p class="muted small">지금 시점은 <b>${esc(povTxt)}</b>이라 화자 보이스와 서술자 음성 카드는 집필에 들어가지 않습니다(인물 보이스는 시점과 무관하게 쓰입니다).</p>`;
  const stageOpts = (v.stage_attr_options||[]).map(o=>
    `<option value="${esc(o.key)}" ${o.key===v.narrator_voice_stage_attr?'selected':''}>${esc(o.label)} (${o.values.length}단계)</option>`).join("");
  const stageCards = (v.stage_values||[]).map(s=>`<label class="vc-stage"><span class="vc-stage-k">${esc(s)}</span>
    <textarea rows="3" data-stage="${esc(s)}" placeholder="이 단계의 화자가 무엇을 잃기 싫어하는지…">${esc((narr&&narr.voice_stages[s])||"")}</textarea></label>`).join("");
  const otherRows = others.map(e=>`<details class="vc-other"><summary>${esc(e.name)} <span class="muted tiny">${e.voice?`${e.voice.length}자`:'비어 있음'}</span></summary>
    <textarea id="vc-ent-${esc(e.id)}" rows="3" placeholder="이 인물 말투의 결(태도·어휘)…">${esc(e.voice||"")}</textarea>
    <div class="style-row"><span class="style-actions"><button class="primary" onclick="saveEntityVoice('${esc(e.id)}')">저장</button></span>
      <span class="muted tiny" id="vc-msg-${esc(e.id)}"></span></div></details>`).join("");
  el.innerHTML = `
    <h4>서술자 목소리 <span class="muted small">— 이 이야기를 누가, 어떤 태도로 들려주는가</span></h4>
    <p class="muted small">회차를 쓸 때 집필에 그대로 들어가는 값입니다. 작품을 만들 때 주인공 설계서에서 자동으로 뽑히고, 여기서 작가가 고쳐 쓸 수 있어요. 고친 값은 다음 회차부터 반영됩니다.</p>
    ${povNote}
    <label class="vc-lbl">화자 보이스 <span class="muted small">— 1인칭 서술 지시에 얹히는 화자의 태도(2~4문장)</span></label>
    <textarea id="vc-narrator" rows="4" maxlength="800" placeholder="이 화자가 무엇을 즐기고 무엇에 이죽거리는지…">${esc(v.narrator_voice||"")}</textarea>
    <div class="style-row"><span class="style-actions"><button class="primary" id="vc-nv-save" onclick="saveNarratorVoice()">저장</button></span>
      <span class="muted small" id="vc-nv-msg" role="status"></span></div>
    ${narr ? `
    <label class="vc-lbl" style="margin-top:1em">서술자 음성 카드 <span class="muted small">— ${esc(narr.name)}의 지문 서술 인격(세상을 보는 각도·속생각의 결·감정을 드러내는 방식)</span></label>
    <textarea id="vc-card" rows="8" placeholder="이 화자는 무엇에 먼저 주의가 끌리고 어떤 태도를 취하는가…">${esc(narr.voice||"")}</textarea>
    <div class="style-row"><span class="style-actions"><button class="primary" onclick="saveEntityVoice('${esc(narr.id)}')">저장</button></span>
      <span class="muted small" id="vc-msg-${esc(narr.id)}" role="status"></span></div>
    <label class="vc-lbl" style="margin-top:1em">상태 연동 보이스 <span class="muted small">— 주인공의 상태가 바뀌면 카드도 갈아끼웁니다(선택)</span></label>
    <div class="style-row"><label>따라갈 상태 <select id="vc-stage-attr" onchange="saveStageAttr()">
      <option value="">쓰지 않음(카드 하나로 고정)</option>${stageOpts}</select></label>
      <span class="muted small" id="vc-stage-msg" role="status"></span></div>
    ${v.narrator_voice_stage_attr ? `<div class="vc-stages">${stageCards}</div>
      <div class="style-row"><span class="style-actions"><button class="primary" onclick="saveStageCards('${esc(narr.id)}')">단계 카드 저장</button></span>
      <span class="muted tiny">비워 둔 단계는 기본 음성 카드를 그대로 씁니다.</span></div>` : ""}`
    : `<p class="muted small" style="margin-top:1em">서술자로 결속할 주인공이 없어 음성 카드가 비어 있습니다.</p>`}
    ${others.length ? `<label class="vc-lbl" style="margin-top:1.2em">인물 보이스 <span class="muted small">— 대사의 결(시점과 무관하게 쓰입니다)</span></label>${otherRows}` : ""}`;
}
async function saveNarratorVoice(){
  const ta=$("#vc-narrator"), msg=$("#vc-nv-msg"), btn=$("#vc-nv-save"); if(!ta||!btn) return;
  btn.disabled=true; if(msg) msg.textContent="저장 중…";
  try{
    await api.put(`/api/projects/${STATE.project.id}/style`, {narrator_voice: ta.value.trim()});
    STATE.project = await fetchProject(STATE.project.id);   // 문체 편집기가 읽는 world.style 최신화
    if(msg) msg.textContent="저장됨 — 다음 회차부터 반영됩니다.";
  }catch(e){ if(msg) msg.textContent="저장 실패: "+e.message; }
  finally{ btn.disabled=false; }
}
async function saveEntityVoice(eid){
  const ta=$("#vc-ent-"+eid) || $("#vc-card");   // 인물 목록 칸(없으면 서술자 음성 카드 칸)
  const msg=$("#vc-msg-"+eid); if(!ta) return;
  if(msg) msg.textContent="저장 중…";
  try{
    await api.patch(`/api/projects/${STATE.project.id}/entities/${encodeURIComponent(eid)}/voice`, {voice: ta.value.trim()});
    if(msg) msg.textContent="저장됨 — 다음 회차부터 반영됩니다.";
  }catch(e){ if(msg) msg.textContent="저장 실패: "+e.message; }
}
async function saveStageAttr(){
  const sel=$("#vc-stage-attr"), msg=$("#vc-stage-msg"); if(!sel) return;
  if(msg) msg.textContent="저장 중…";
  try{
    await api.put(`/api/projects/${STATE.project.id}/style`, {narrator_voice_stage_attr: sel.value});
    STATE.project = await fetchProject(STATE.project.id);
    loadVoices();   // 단계 목록이 바뀌므로 재렌더
  }catch(e){ if(msg) msg.textContent="저장 실패: "+e.message; }
}
async function saveStageCards(eid){
  const msg=$("#vc-stage-msg"), stages={};
  document.querySelectorAll("#voice-editor .vc-stages textarea").forEach(t=>{ stages[t.dataset.stage]=t.value.trim(); });
  if(msg) msg.textContent="저장 중…";
  try{
    await api.patch(`/api/projects/${STATE.project.id}/entities/${encodeURIComponent(eid)}/voice`, {voice_stages: stages});
    if(msg) msg.textContent="단계 카드를 저장했습니다 — 다음 회차부터 반영됩니다.";
  }catch(e){ if(msg) msg.textContent="저장 실패: "+e.message; }
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
    STATE.project = await fetchProject(STATE.project.id);   // 전체 재조회 — 부분 동기화 stale 혼합 방지(다른 저장 핸들러 패턴)
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
  return `<div class="bible-sec">${head}<div class="aitell-grid">${lines}</div>${kiwiEndingHtml(rows)}
    <p class="muted tiny">작품 자기 중앙값 대비 <b>상대 추세</b>일 뿐 'AI 판정'이 아닙니다 — 문체는 작가마다 다릅니다(균일 단문도 만연체도 사람 글). 한 회차의 한 숫자가 아니라 흐름으로 보세요.</p></div>`;
}
// SP-1 Stage C: Kiwi 문말 계측(ending_profile·daStreak)을 최신 측정 회차 기준으로 병기 — advisory 원자료(판정 라벨 0).
//   ai_tell.kiwi 는 부품(kiwipiepy) 가용 시에만 채워짐 → 결측 회차는 조용히 건너뛴다(정직 결측·백엔드 표기).
function kiwiEndingHtml(rows){
  const kr=(rows||[]).filter(c=>c.ai_tell&&c.ai_tell.kiwi&&c.ai_tell.kiwi.ending_profile);
  if(!kr.length) return "";
  const c=kr[kr.length-1], k=c.ai_tell.kiwi, ep=k.ending_profile||{}, da=k.da_streak||{};
  if(!ep.n_ending) return "";   // 종결 문장 없음(측정 불가) — 표시 생략
  const be=ep.backend==='regex'?' <span class="muted tiny">(정규식 근사 — Kiwi 미설치)</span>':'';
  const tt=ep.top_template?esc(String(ep.top_template)):'–';
  return `<div class="bible-sec" style="margin-top:.4em"><h4>문말 종결 계측 <span class="muted small">— ${c.chapter}화 기준·Kiwi 형태소(참고 원자료)${be}</span></h4>
    <div class="aitell-grid">
      <div class="aitell-row"><div class="aitell-lbl"><b>최빈 종결형</b> <span class="muted tiny">한 종결형의 지배도</span></div>
        <div class="aitell-val muted tiny">‘${tt}’ ${ep.top_ratio!=null?(ep.top_ratio*100).toFixed(0)+'%':'–'}</div></div>
      <div class="aitell-row"><div class="aitell-lbl"><b>종결형 종류</b> <span class="muted tiny">많을수록 다양</span></div>
        <div class="aitell-val muted tiny">${ep.unique!=null?ep.unique:'–'}종 <span class="muted">/ 최장 동일런 ${ep.max_run!=null?ep.max_run:'–'}</span></div></div>
      <div class="aitell-row"><div class="aitell-lbl"><b>‘~다(과거)’ 연속</b> <span class="muted tiny">인간 레퍼런스 대역 ~3</span></div>
        <div class="aitell-val muted tiny">최장 ${da.max_run!=null?da.max_run:'–'} <span class="muted">/ 비율 ${da.ratio!=null?(da.ratio*100).toFixed(0)+'%':'–'}</span></div></div>
    </div>
    <p class="muted tiny">문말 종결 어미의 단일성 원자료입니다 — 임계·판정이 아니라 흐름 참고용(어미가 이어져도 발화 층위가 섞이면 벽이 아닙니다).</p></div>`;
}
// EC-1 엔딩 계약 블록(T6 readiness 패널 동형 — advisory·비차단). gt/ni 병렬 표시(단일 판정 없음),
// 미표현 엔딩 요소(등록부 승격 유도)·완결 미정산 경고·stale(결말 개정 후 미갱신) 안내.
function ecBadge(r){
  const m={satisfied:["충족","ok"],open:["미결","open"],blocked:["봉쇄 신호","blocked"]};
  const b=m[r]||[r||"?",""]; return `<span class="ec-badge ${b[1]}">${esc(b[0])}</span>`;
}
function endingContractHtml(ec){
  if(!ec) return "";
  const head=`<h4>엔딩 계약 <span class="muted small">— 결말 귀결의 '설정 상태' 감시(참고 — 아무것도 막지 않아요)</span></h4>`;
  if(!ec.has_contract){
    return ec.error?`<div class="bible-sec">${head}<p class="muted small">감시 계약이 없어요(컴파일 건너뜀). 연재 회고에서 결말을 개정·승인하면 다시 만듭니다.</p></div>`:"";
  }
  const stale=ec.stale?`<div class="ec-warn"><b>결말이 개정된 뒤 계약이 갱신되지 않았어요.</b> <span class="muted small">아래 항목은 이전 결말 기준 — 결말을 다시 개정·승인하면 재컴파일됩니다.</span></div>`:"";
  const st=ec.settlement||{};
  const settle=st.trigger
    ?(st.unsettled_gt>0
      ?`<div class="ec-warn"><b>미정산 엔딩</b> — 완결 시점에 확정 기준 미정산 ${st.unsettled_gt}건(미결 ${st.open_gt}·봉쇄 신호 ${st.blocked_gt}). <span class="muted small">표시만 해요 — 완결을 막거나 에필로그를 자동으로 쓰지 않습니다.</span></div>`
      :`<div class="muted small" style="margin-bottom:.5em">✓ 완결 시점 정산 — 확정 기준 전 항목 충족(상태 기준)</div>`)
    :"";
  const rows=(ec.predicates||[]).map(p=>{
    const g=p.eval.ground_truth_only, n=p.eval.with_narrative_inferred;
    const hint=p.promotion_hint?`<div class="muted tiny">↑ 본문 추출로는 이미 충족 — 해당 설정을 작가 확정(등록부 승격)하면 '확정' 기준에도 반영돼요.</div>`:"";
    return `<div class="ec-row"><div class="ec-lbl">${esc(p.label||p.type)}${p.note?` <span class="muted tiny">— ${esc(p.note)}</span>`:""}</div>
      <div class="ec-tiers"><span class="muted tiny">확정</span> ${ecBadge(g.result)} <span class="muted tiny">추출포함</span> ${ecBadge(n.result)}</div>${hint}</div>`;
  }).join("");
  const ux=(ec.unexpressed||[]);
  const unexp=ux.length?`<div class="ec-warn"><b>미표현 엔딩 요소 ${ux.length}건</b> <span class="muted small">— 등록부(인물·속성·관계·약속)에 없어 감시할 수 없는 결말 개념이에요. 설정으로 등록하면 재컴파일 때 반영됩니다(자동 등록은 안 해요).</span>
    ${ux.slice(0,4).map(u=>`<div class="muted tiny">· ${esc(JSON.stringify(u.raw).slice(0,90))} <span class="muted">(${esc((u.reason||"").split(":")[0])})</span></div>`).join("")}</div>`:"";
  return `<div class="bible-sec">${head}${stale}${settle}${rows||'<p class="muted small">감시 중인 술어가 없습니다.</p>'}${unexp}
    <p class="muted tiny">'충족'은 설정(상태) 기준이에요 — 본문에 그 귀결이 극화됐다는 보장이 아닙니다. 확정(작가·시드 설정)과 추출포함(AI가 본문에서 읽은 것)을 나란히 보여줄 뿐, 단일 판정은 하지 않습니다.</p></div>`;
}
// PR-2: 회차 통합 검증 패널(SSOT) — 이 회차가 도입된 전 축에서 '어떤 상태인가'를 한 번에 표로 전재.
//   무강제: 판정 라벨·색상 경고·임계 0 — 축별 값 + 인간 대역(참조선) + 미실행 표시(결측 정직)만.
//   verification dict(engine.verification.build_verification)를 그대로 읽어 표시 — 웹이 수기 조립하지 않는다.
const V_MISSING = "미실행";
function _vval(x){   // 값/미실행/None을 정직 표기(0으로 위장 금지 — 결측은 '미실행', null은 '–')
  if(x===V_MISSING) return `<span class="muted tiny">미실행</span>`;
  if(x==null) return `<span class="muted tiny">–</span>`;
  if(typeof x==="number") return esc(String(Number.isInteger(x)?x:x.toFixed(3).replace(/\.?0+$/,"")));
  return esc(String(x));
}
function _vpct(x){   // 0~1 비율을 % 로(숫자일 때만) — 미실행/null 은 _vval 로 정직 표기(NaN 방지)
  return (typeof x==="number") ? esc((x*100).toFixed(0)+"%") : _vval(x);
}
function _vrow(label, valHtml, hint){
  return `<div class="v-row"><div class="v-lbl">${esc(label)}${hint?` <span class="muted tiny">${esc(hint)}</span>`:""}</div><div class="v-val">${valHtml}</div></div>`;
}
function verificationPanel(c){
  const v=c&&c.verification;
  if(!v||!Object.keys(v).length) return "";   // 구 회차(미집계) — 패널 생략(백필 스크립트로 채울 수 있음)
  const rows=[];
  // ① 캐넌(결정론 체커) — 항상 존재
  const cn=v.canon||{};
  rows.push(_vrow("캐넌 위반", `하드 ${_vval(cn.hard)} <span class="muted">/ 전체 ${_vval(cn.final_violations)}</span>${(cn.hard_kinds&&cn.hard_kinds.length)?` <span class="muted tiny">(${esc(cn.hard_kinds.join(", "))})</span>`:""}`, "하드 0이 발행 기준"));
  // ② ai_tell(정규식 + Kiwi 종결/층위 + 인간 대역)
  const at=v.ai_tell;
  if(at===V_MISSING){ rows.push(_vrow("문체 신호(ai_tell)", _vval(V_MISSING), "미계산")); }
  else if(at){
    rows.push(_vrow("쉼표 밀도 / 문장길이 변동", `${_vval(at.comma_per_100)} <span class="muted">/</span> ${_vval(at.sent_len_cv)}`, "분포 신호"));
    rows.push(_vrow("어휘 다양성 / 종결형 다양성", `${_vval(at.lexical_mattr)} <span class="muted">/</span> ${_vval(at.ending_diversity)}`, "낮을수록 반복·단조"));
    rows.push(_vrow("과거형 최장런 / 파편문 비율", `${_vval(at.past_run_max)} <span class="muted">/</span> ${_vval(at.frag_ratio)}`, "지문 리듬"));
    const kw=at.kiwi;
    if(kw===V_MISSING){ rows.push(_vrow("문말 종결(Kiwi)", _vval(V_MISSING), "형태소 부품 부재")); }
    else if(kw){
      const be=(kw.backend==='regex')?' (정규식 근사)':'';
      rows.push(_vrow("최빈 종결형 / 종류", `‘${_vval(kw.top_template)}’ ${_vpct(kw.top_ratio)} <span class="muted">/ ${_vval(kw.unique)}종·최장런 ${_vval(kw.max_run)}</span>${be?`<span class="muted tiny">${be}</span>`:""}`, "종결 단일성 원자료"));
      rows.push(_vrow("‘~다(과거)’ 연속", `최장 ${_vval(kw.da_max_run)} <span class="muted">/ 비율 ${_vpct(kw.da_ratio)}</span>`, "인간 대역 ~3(참조선)"));
      const ly=kw.layer;
      if(ly===V_MISSING){ rows.push(_vrow("발화 층위(벽 축)", _vval(V_MISSING), "부품 부재")); }
      else if(ly){ rows.push(_vrow("발화 층위(무대사 지문 최장 연속)", `${_vval(ly.max_narration_run)} <span class="muted">/ 대사 문단비 ${_vpct(ly.dialogue_para_ratio)}</span>`, "ST-9 벽 판정축 원자료")); }
      const hb=at.human_band;
      if(hb&&hb!==V_MISSING){ rows.push(_vrow("인간 레퍼런스 대역", `<span class="muted tiny">동결 실측 대역 병기(임계 아님·참조선)</span>`, "im-not-ai")); }
    }
  }
  // ③ 재탕계수(도입부 장면 재인스턴스) — 1화 등 선행 없으면 축 자체가 '미실행'(결측 정직)
  const rt=v.retread;
  if(rt===V_MISSING){ rows.push(_vrow("재탕계수(도입부)", _vval(V_MISSING), "선행 회차 없음")); }
  else if(rt){ rows.push(_vrow("재탕계수(도입부)", `${_vval(rt.opening_coef)} <span class="muted">/ Jaccard ${_vval(rt.opening_jac)}</span>`, "선행 회차 대비 최대 유사(정독 앵커)")); }
  // ④ 분량(출고 규범 대비)
  const ln=v.length;
  if(ln){ rows.push(_vrow("분량", `${_vval(ln.chars)}자 <span class="muted">/ 규범 ${_vval(ln.norm)} · 비율 ${_vval(ln.ratio_to_norm)}</span>`, "코드 판정 아님·원자료")); }
  // ④-b 조판(TG-1·VI-1) — 모바일 문단 줄수(고정 형식 규격·advisory). 초과 최장 문단 머리 인용.
  const ly=v.layout;
  if(ly&&ly.n_paras!==undefined){
    const worst=(ly.worst&&ly.worst.length)?` <span class="muted tiny">(최장 ${_vval(ly.worst[0].lines)}줄 「${esc(String(ly.worst[0].head||""))}…」)</span>`:"";
    rows.push(_vrow("조판(모바일)", `${_vval(ly.n_paras)}문단 <span class="muted">/ 3줄 초과 ${_vval(ly.over)}</span>${worst}`, "웹소설 조판 관행·원자료"));
  }
  // ⑤ craft 라벨(마무리 장치·훅·기능·장면 안무)
  const lb=v.labels;
  if(lb&&(lb.closing_device||lb.hook_type||lb.chapter_function||lb.scene_form)){
    rows.push(_vrow("마무리 장치 / 훅 / 기능", `${_vval(lb.closing_device||'–')} <span class="muted">/</span> ${_vval(lb.hook_type||'–')} <span class="muted">/</span> ${_vval(lb.chapter_function||'–')}`, "craft 순환 원자료"));
    // SX-1: 장면 안무(scene_form) — craft 순환 원자료(접촉→복원→은폐 3연속 같은 안무 반복 가시화)
    if(lb.scene_form){ rows.push(_vrow("장면 안무", `${_vval(lb.scene_form)}`, "회차 중심 장면 안무·순환 원자료")); }
  }
  // ⑤-b 세계 노출 슬롯(SX-3) — 이 회차에서 독자가 처음 알게 되는 세계 사실(존재 여부·advisory·판정 없음)
  const wr=v.world_reveal;
  if(wr&&typeof wr.count==="number"){
    const items=(wr.items&&wr.items.length)?` <span class="muted tiny">(${wr.items.map(x=>esc(String(x).slice(0,60))).join(" · ")})</span>`:"";
    rows.push(_vrow("세계 노출 슬롯", `${_vval(wr.count)}개${items}`, "장면 사건으로 드러나는 새 세계 사실·advisory"));
  }
  // ⑥ style_repairs(SP-1 Stage B)
  const sr=v.style_repairs;
  if(sr){ rows.push(_vrow("리듬 수리(스팬)", `${_vval(sr.spans)}개 <span class="muted">/ 변경 ${_vval(sr.changed)} · 폴백 ${_vval(sr.fallback)}</span>`, "국소 수리 내역")); }
  // ⑥-b 무중단 동일 종결 키 run(ST-14 FIX-5) — 형태 불문 지문 리듬 벽(풍선 감시·advisory). 부품 부재 시 '미실행'.
  const er=v.ending_runs;
  if(er===V_MISSING){ rows.push(_vrow("무중단 종결 run(벽)", _vval(V_MISSING), "형태소 부품 부재")); }
  else if(er){
    const topKey=(er.runs&&er.runs.length)?`‘${esc(String(er.runs[0].key))}’`:"–";
    rows.push(_vrow("무중단 동일 종결 run(벽)", `최장 ${_vval(er.max_run)} <span class="muted">/ ${_vval(er.count)}개(≥${_vval(er.threshold)}) · 최장 종결형 ${topKey}</span>`, "형태 불문·대사 리셋 원자료(판정 아님)"));
  }
  // ⑦ reader_feedback(블라인드 독자 시뮬)
  const rf=v.reader_feedback;
  if(rf===V_MISSING){ rows.push(_vrow("독자 시뮬(잔존·하차)", _vval(V_MISSING), "미실행")); }
  else if(rf){ rows.push(_vrow("독자 시뮬 — 잔존 추정 / 하차", `${typeof rf.retention_est==="number"?rf.retention_est+'%':_vval(rf.retention_est)} <span class="muted">/ ${rf.drop===true?'하차':(rf.drop===false?'잔존':_vval(rf.drop))}</span>`, "advisory·실독자 아님")); }
  // ⑦-b 콜드리드(SX-2) — 프로즈만 읽는 신규 독자 프로브(이해도·혼란·궁금·하차). 미실행 시 정직 표기.
  const cr=v.cold_read;
  if(cr===V_MISSING||!cr){ rows.push(_vrow("콜드리드(신규 독자·프로즈만)", _vval(V_MISSING), "미실행·advisory")); }
  else {
    const trunc=(cr.truncated===true)?` <span class="muted tiny">(앞부분만·절단)</span>`:"";
    rows.push(_vrow("콜드리드 — 이해도 / 하차위험", `${typeof cr.comprehension_0_100==="number"?cr.comprehension_0_100+'/100':_vval(cr.comprehension_0_100)} <span class="muted">/ ${cr.drop_risk===true?'하차위험':(cr.drop_risk===false?'잔존':_vval(cr.drop_risk))}</span>${trunc}`, "요약 없이 본문만 읽는 신규 독자·실독자 아님"));
    // 혼란 대목(원문 인용) — 콜드리드 사각(요약이 메꾸던 혼란)의 실제 증거
    if(cr.confusions&&cr.confusions.length){
      const cf=cr.confusions.slice(0,3).map(c=>`${esc(String(c.what||'').slice(0,60))}${c.quote?` — “${esc(String(c.quote).slice(0,80))}”`:""}`).join("<br>");
      rows.push(_vrow("콜드리드 혼란 대목", `<span class="muted tiny">${cf}</span>`, "처음 읽는 독자가 헷갈린 곳·원문 인용"));
    }
    if(cr.curiosities&&cr.curiosities.length){
      rows.push(_vrow("콜드리드 궁금증", `<span class="muted tiny">${cr.curiosities.slice(0,3).map(x=>esc(String(x).slice(0,70))).join(" · ")}</span>`, "더 알고 싶어진 것·좋은 훅 신호"));
    }
  }
  // ⑧ ending_contract(EC-1 gt/ni 병렬)
  const ec=v.ending_contract;
  if(ec===V_MISSING){ rows.push(_vrow("엔딩 계약(gt/ni)", _vval(V_MISSING), "미실행")); }
  else if(ec){ rows.push(_vrow("엔딩 계약 — 확정 충족 / 추출 충족", `${_vval(ec.gt&&ec.gt.satisfied)} <span class="muted">/</span> ${_vval(ec.ni&&ec.ni.satisfied)} <span class="muted tiny">of ${_vval(ec.total)}</span>`, "상태 기준·단일 판정 없음")); }
  // ⑨ claim_audit(과거 회차 사실 모순 수) — 항상 존재
  rows.push(_vrow("연속성 점검(사실 모순)", `${_vval(v.claim_audit)}건`, "과거 회차 대조·비차단"));
  // ⑩ gate(제품/러너 정독 게이트) — PR-1이 채움. 미실행 시 정직 표기. 게이트 결과 + 재시도 이력(gate_rounds).
  const gt=v.gate;
  if(gt===V_MISSING||!gt){ rows.push(_vrow("정독 게이트", _vval(V_MISSING), "옵션(기본 OFF)·PR-1")); }
  else {
    const ret=(gt.retention_est!=null)?` <span class="muted">/ 잔존 ${_vval(gt.retention_est)}%</span>`:"";
    const rtr=(gt.retries!=null)?` <span class="muted">/ 재시도 ${_vval(gt.retries)}</span>`:"";
    const exh=(gt.fail_exhausted===true)?` <span class="muted tiny">(R 소진 후 전진)</span>`:"";
    rows.push(_vrow("정독 게이트", `${_vval(gt.verdict||'–')}${ret}${rtr}${exh}`, "옵션·PR-1·판정/차단 아님"));
    // 게이트가 실제로 짚은 대목(원자료 인용) — 있으면 표기(작가가 재생성 조준에 쓸 수 있게).
    if(gt.drop_trigger && gt.drop_trigger!=='없음'){ rows.push(_vrow("게이트 이탈 대목", `<span class="muted tiny">“${esc(String(gt.drop_trigger).slice(0,160))}”</span>`, "정독 인용 원자료")); }
    if(gt.fix_note){ rows.push(_vrow("게이트 수정 메모", `<span class="muted tiny">${esc(String(gt.fix_note).slice(0,200))}</span>`, "국소 수리 재료")); }
    // 재시도 이력(라운드별 판정·수리 경로) — jsonl 무덤 대신 회차에 영속된 게이트 라운드를 그대로 전재.
    const gr=gt.gate_rounds;
    if(Array.isArray(gr) && gr.length){
      const hist=gr.map(r=>`R${_vval(r.round)}:${esc(String(r.verdict||'–'))}${(r.repair_path&&r.repair_path!=='none')?`·${esc(String(r.repair_path))}`:""}${r.reverted?"·복원":""}`).join(" → ");
      rows.push(_vrow("게이트 재시도 이력", `<span class="muted tiny">${hist}</span>`, "라운드별 판정·수리 경로"));
    }
  }
  return `<div class="verify-panel"><div class="verify-head">회차 검증 <span class="muted small">— 이 회차가 도입된 전 축의 상태(원자료·인간 대역·미실행 표시 · 판정/경고 없음)</span></div>
    <div class="verify-grid">${rows.join("")}</div>
    <p class="muted tiny">전 축을 한 곳에 모은 참고 원자료입니다 — 어떤 값도 회차를 막거나 자동 수정하지 않습니다. '미실행'은 그 축이 이 회차에서 돌지 않았다는 정직한 표시(0으로 위장하지 않음)예요.</p></div>`;
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
    // DP-10 시뮬 독자(참고) — 최근 회차 신호 집약(리더·생성결과에 흩어진 신호를 한곳에). 실독자 아님 — LLM 시뮬.
    const reacts=(STATE.project.chapters||[])
      .filter(c=>{const rf=c.reader_feedback;return rf&&(rf.why||rf.got||rf.kill_trigger||rf.hate_comment||rf.retention_est!=null);})
      .slice(-8).reverse().map(c=>readerReactHtml(c.reader_feedback, c.chapter)).join("");
    const reactBlock=`<div class="bible-sec"><h4>시뮬 독자(참고) <span class="muted small">— LLM 시뮬 · 실독자 아님</span></h4>${reacts||'<p class="muted small">아직 시뮬 독자 신호가 없습니다.</p>'}</div>`;
    // G3 연재 회고 — on-demand
    const retroBlock=`<div class="bible-sec"><h4>연재 회고 <span class="muted small">— 페이싱·방향 점검과 개정 제안</span></h4>
      <button id="retro-btn" onclick="loadRetrospective()">회고 받기</button>
      <div id="retro-body"></div></div>`;
    const ending=sp.ending?`<div class="bible-sec"><h4>결말 — 이 방향으로 수렴합니다</h4>
      <p><b>중심 질문:</b> ${esc(sp.ending.central_question)}<br><b>결말:</b> ${esc(sp.ending.ending)}
      ${sp.ending.thematic_payoff?`<br><b>주제:</b> ${esc(sp.ending.thematic_payoff)}`:""}</p></div>`:"";
    // EC-1 엔딩 계약(advisory — 실패해도 나머지 연재 관리 렌더는 그대로 진행)
    let ecBlock="";
    try{ ecBlock=endingContractHtml(await api.get(`/api/projects/${STATE.project.id}/ending-contract`)); }catch(e){}
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
    structEl.innerHTML=gcBlock+ending+ecBlock+`<div class="bible-sec"><h4>단락과 에피소드 <span class="muted small">(현재 단락 ${sp.chapters_in_episode}화째)</span></h4>${arcs}</div>`;   // 이야기 구조
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
    const pacing=`<div class="retro-pacing muted small">최근 ${pw.window||0}화 · 훅 단조 ${pw.hook_monotony??'–'}${pw.hook_max_run!=null?` (연속 ${pw.hook_max_run}화)`:''} · 장소 ${pw.places_distinct??'–'}종${pw.place_max_run!=null?` (연속 ${pw.place_max_run}화)`:''} · 새 고유명사 ${pw.new_names??'–'}개${(pw.prose_echo||[]).length?` · 직전 본문 겹침 최대 ${Math.max(...pw.prose_echo)}`:''}${pw.since_payoff!=null?` · 회수 후 ${pw.since_payoff}화`:''}</div>`;
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
    STATE.project=await fetchProject(STATE.project.id);
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
function bookCard(p){
  const pct=p.total_chapters?Math.min(100,Math.round((p.current_chapter||0)/p.total_chapters*100)):0;
  const init=esc(((p.title||'무').trim().charAt(0))||'무');
  const cont=(p.current_chapter>0)?`${p.current_chapter}화 이어 쓰기 →`:'첫 회차 쓰기 →';
  return `<div class="bookcard">
    <a class="bc-link" href="#/p/${p.id}/write">
      <div class="bc-row">
        ${(p.cover && p.cover.filename)
          ? `<img class="bc-thumb" src="/api/projects/${p.id}/cover?v=${encodeURIComponent(p.cover.created_at||'')}" alt="" loading="lazy"
               title="표지 크게 보기" onclick="event.preventDefault();event.stopPropagation();openCoverZoom(this.src)">`
          : `<div class="bc-spine" style="background:${genreTone(p.genre)}">${init}</div>`}
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
// 홈 '내 작품' 정렬 — 마지막 목록을 캐시해 정렬 변경 시 재fetch 없이 재렌더. 선택은 localStorage 영속.
let _projectsCache = [];
const LIB_SORT_KEY = 'novelcopilot:libSort';
function libSort(){ try{ return localStorage.getItem(LIB_SORT_KEY) || 'recent'; }catch(e){ return 'recent'; } }
function setLibSort(v){ try{ localStorage.setItem(LIB_SORT_KEY, v); }catch(e){} renderProjectList(); }   // 재fetch 불필요 — 캐시 재정렬
function _progRatio(p){ return p.total_chapters ? (p.current_chapter||0)/p.total_chapters : 0; }
async function loadProjects(){
  const el = $("#project-list"), cnt = $("#lib-count");
  try{
    const list = await api.get("/api/projects");
    _projectsCache = list;
    if(cnt) cnt.textContent = list.length ? `${list.length}편` : "";
    renderProjectList();
  }catch(e){ if(el) el.innerHTML = `<p class="muted">목록을 불러오지 못했습니다: ${esc(e.message)}</p>`; }
}
function renderProjectList(){
  const el = $("#project-list"); if(!el) return;
  const sort = libSort();
  const sel = $("#lib-sort"); if(sel && sel.value!==sort) sel.value=sort;   // 재방문 시 select 복원
  const list = _projectsCache.slice();
  if(!list.length){ el.innerHTML = '<p class="muted">아직 작품이 없습니다. 오른쪽 ‘새 작품 시작’에서 첫 작품을 빚어보세요. →</p>'; return; }
  if(sort==='title') list.sort((a,b)=>String(a.title||'무제').localeCompare(String(b.title||'무제'),'ko'));
  else if(sort==='progress') list.sort((a,b)=>_progRatio(b)-_progRatio(a));   // 진행률(current/total) 내림차순
  else if(sort==='chapters') list.sort((a,b)=>(b.current_chapter||0)-(a.current_chapter||0));   // 쓴 회차 많은 순
  else list.sort((a,b)=>String(b.created_at||'').localeCompare(String(a.created_at||'')));   // recent(기본) — 최근 생성 먼저
  // 마지막 작업 작품 승격(최상단 고정)은 폐기 — 정렬 선택과 어긋나 '정렬이 깨진 것처럼' 보임(사용자 판정 2026-07-15)
  el.innerHTML = list.map(p=>bookCard(p)).join("");
}
async function delProject(pid){ if(!confirm("이 작품을 삭제할까요? 되돌릴 수 없습니다.")) return; await api.del(`/api/projects/${pid}`); loadProjects(); }

// ---------- FI-4 전역 관측 대시보드 ----------
// 원칙(설계 §0): 원시 수치 전 제공·판정 형태 금지 — % 헤드라인 0·순위 배지 0·판정색 0. 파생은 분수 병기(표본 크기 동승).
//   record_counts(전체 이력)와 event_counts(관측 이후·actor 분리) 분리. actor 토글 기본=작가만(실험 오염 차단).
let DASH = { data:null, sortKey:"last_activity", sortDir:-1, actor:"author", series:"top_ratio" };   // 기본 추세 축='-었다'류 최빈 종결 비율(1순위 관심 계측 — 저장값 인용)
async function renderDashboard(){
  const el = $("#dashboard-body"); if(!el) return;
  el.innerHTML = '<p class="muted">불러오는 중…</p>';
  try{ DASH.data = await api.get("/api/dashboard"); }
  catch(e){ el.innerHTML = `<p class="muted">불러오지 못했습니다: ${esc(e.message)}</p>`; return; }
  paintDashboard();
}
function dashTile(label, val){
  return `<div class="dash-tile"><div class="dash-tile-val">${val}</div><div class="dash-tile-lbl">${esc(label)}</div></div>`;
}
// actor 토글에 따른 관측 이벤트 카운트(작가만 / 도구 포함=전 actor 합). 판정 아님 — 원시 건수.
function dashEv(w, cat){
  const ba = (w.event_counts && w.event_counts.by_actor) || {};
  if(DASH.actor === "author") return (ba.author && ba.author[cat]) || 0;
  let s = 0; for(const k in ba) s += (ba[k] && ba[k][cat]) || 0; return s;
}
function dashVal(w, key){
  switch(key){
    case "title": return String(w.title || "무제");
    case "chapters": return w.chapters || 0;
    case "last_activity": return String(w.last_activity || "");
    case "accepted": return (w.record_counts||{}).revise_accepted || 0;
    case "propose": return dashEv(w, "revise_propose");
    case "regens": return (w.record_counts||{}).regens || 0;
    case "rerender": return (w.rerender||{}).adopted || 0;
    case "tpc": return (w.usage||{}).tokens_per_chapter || 0;
    case "ending": { const m = w.metrics_latest||{}; return (m.ending_max_run==null) ? -1 : m.ending_max_run; }
    default: return "";
  }
}
function dashSort(key){
  if(DASH.sortKey === key) DASH.sortDir = -DASH.sortDir;
  else { DASH.sortKey = key; DASH.sortDir = (key==="title") ? 1 : -1; }   // 문자열=오름차순 기본, 수치=내림차순 기본
  paintDashboard();
}
function setDashActor(a){ DASH.actor = a; paintDashboard(); }
function setDashSeries(s){ DASH.series = s; paintDashboard(); }
function _dashSeriesVals(w){
  const vals = (w.metrics_series||[]).map(p => p[DASH.series]).filter(v => typeof v === "number");
  return vals.length >= 2 ? normMinMax(vals) : vals;   // 추세 '모양'만(저변동 비율축도 보이게) — 크기는 옆 최신 계측 숫자로
}
function _dashLatest(w){
  const m = w.metrics_latest || {};
  if(m.top_ratio==null && m.ending_max_run==null && m.chars==null) return '<span class="muted">미계측</span>';
  const parts = [];
  if(m.top_ratio!=null) parts.push(`최빈 종결 ${m.top_ratio}`);   // 저장 top_ratio 인용('-었다'류 — 판정 아님)
  if(m.ending_max_run!=null){
    const thr = (m.ending_run_threshold!=null) ? ` <span class="muted tiny">/ 기준선 ${m.ending_run_threshold}</span>` : "";
    parts.push(`run ${m.ending_max_run}${thr}`);
  }
  if(m.chars!=null) parts.push(`<span class="muted tiny">${Number(m.chars).toLocaleString()}자</span>`);
  return parts.join(" · ") || '<span class="muted">미계측</span>';
}
function paintDashboard(){
  const el = $("#dashboard-body"), d = DASH.data; if(!el || !d) return;
  const sub = $("#dash-sub");
  if(sub) sub.textContent = `생성 ${d.generated_at||""} · 관측 이벤트 ${d.observed_since||""}~`
    + ((d.trace_errors||0) ? ` · ⚠ 읽지 못한 트레이스 ${d.trace_errors}건` : "");
  const t = d.totals || {}, et = t.events_today || {};
  const tiles = `<div class="dash-tiles">
    ${dashTile("작품", (t.projects||0).toLocaleString())}
    ${dashTile("총 회차", (t.chapters||0).toLocaleString())}
    ${dashTile("누적 토큰", (t.tokens||0).toLocaleString())}
    ${dashTile("오늘 작가 이벤트", `${(et.author||0).toLocaleString()}<span class="dash-tile-sub muted"> · 도구 ${(et.tool||0).toLocaleString()}</span>`)}
  </div>`;
  const controls = `<div class="dash-controls">
    <div class="dash-toggle" role="group" aria-label="관측 이벤트 주체">
      <span class="muted small">관측 이벤트:</span>
      <button class="${DASH.actor==='author'?'on':''}" onclick="setDashActor('author')">작가만</button>
      <button class="${DASH.actor==='both'?'on':''}" onclick="setDashActor('both')">도구 포함</button>
    </div>
    <label class="dash-series muted small">추세 축:
      <select onchange="setDashSeries(this.value)">
        <option value="top_ratio"${DASH.series==='top_ratio'?' selected':''}>최빈 종결 비율(-었다류)</option>
        <option value="da_ratio"${DASH.series==='da_ratio'?' selected':''}>-다 연속 비율</option>
        <option value="ending_max_run"${DASH.series==='ending_max_run'?' selected':''}>무중단 종결 run</option>
        <option value="chars"${DASH.series==='chars'?' selected':''}>분량(자)</option>
      </select>
    </label>
  </div>`;
  const th = (label, key) => {
    const active = DASH.sortKey === key, arr = active ? (DASH.sortDir<0 ? " ▾" : " ▴") : "";
    return `<th class="dash-th${active?' sorted':''}" ${ACT} onclick="dashSort('${key}')" title="클릭해 정렬">${esc(label)}${arr}</th>`;
  };
  const obs = `관측 ${d.observed_since||""}~`;
  const rows = (d.works||[]).slice().sort((a,b)=>{
    const va = dashVal(a, DASH.sortKey), vb = dashVal(b, DASH.sortKey);
    let c = (typeof va === "string") ? va.localeCompare(vb, "ko") : (va - vb);
    return DASH.sortDir < 0 ? -c : c;
  }).map(w=>{
    const rc = w.record_counts||{}, rr = w.rerender||{}, u = w.usage||{};
    const prop = dashEv(w,"revise_propose"), fric = dashEv(w,"friction"), canon = dashEv(w,"canon_edits");
    const spk = sparkline(_dashSeriesVals(w));
    return `<tr>
      <td class="dash-title"><a ${ACT} onclick="go('#/p/${w.pid}/overview')">${esc(w.title||"무제")}</a></td>
      <td class="num">${w.chapters||0}</td>
      <td class="dash-when muted small">${esc(w.last_activity||"—")}</td>
      <td class="dash-rev">채택 ${rc.revise_accepted||0} · 편집 ${rc.direct_edits||0} · <span title="채택 대비 되돌림(표본 병기)">undo ${rc.undone||0}/${rc.revise_accepted||0}</span></td>
      <td class="dash-ev" title="${obs}">제안 ${prop} · 마찰 ${fric} · 정정 ${canon}</td>
      <td class="num">${rc.regens||0}</td>
      <td class="dash-rr">채택 ${rr.adopted||0} / 기각 ${rr.rejected||0}</td>
      <td class="dash-metric">${_dashLatest(w)}</td>
      <td class="num">${(u.tokens_per_chapter||0).toLocaleString()}</td>
      <td class="dash-spk">${spk||'<span class="muted tiny">—</span>'}</td>
    </tr>`;
  }).join("");
  const table = `<div class="dash-table-wrap"><table class="dash-table">
    <thead><tr>
      ${th("제목","title")}${th("회차","chapters")}${th("최근 활동","last_activity")}
      <th class="dash-th">퇴고 <span class="muted tiny">채택·편집·undo</span></th>
      <th class="dash-th" title="${obs}">관측 이벤트 <span class="muted tiny">제안·마찰·정정</span></th>
      ${th("재생성","regens")}${th("재실현","rerender")}
      <th class="dash-th">최신 계측</th>${th("토큰/회차","tpc")}
      <th class="dash-th">추세</th>
    </tr></thead>
    <tbody>${rows || '<tr><td colspan="10" class="muted">작품이 없습니다.</td></tr>'}</tbody>
  </table></div>`;
  const imprint = `<p class="dash-imprint">이 수치는 가설 생성용이다. 파이프라인 레버 결정(모델·쿼터·후보 풀)의 단독 근거가 될 수 없다 — 결정은 페어드 A/B+정독으로만.</p>`;
  el.innerHTML = tiles + controls + table + imprint;
}

// 작품 생성 단계(SSE) → 사람이 읽는 진행 문구. 가장 느린 설정집은 카테고리별로 노출.
function wgStage(ev){
  switch(ev.event){
    case "world_start": return "세계관을 구상하는 중…";
    case "world_done": return `세계관 완성 — 등장인물 ${(ev.entities||[]).length}명${(ev.entities||[]).length?` (${ev.entities.slice(0,4).map(esc).join(", ")}${ev.entities.length>4?" 외":""})`:""}`;
    case "spine_start": return "이야기 구조(결말·단락)를 설계하는 중…";
    case "spine_retry": return "이야기 구조 설계가 한 번 실패해 다시 시도하는 중…";   // B-26: transient 실패 1회 retry 가시화
    case "spine_done": return `이야기 구조 완성 — 단락 ${ev.arcs}개`;
    case "spine_gen_failed": return "⚠️ 이야기 구조 설계에 실패했습니다(재시도 포함)";   // B-26: 재실패 가시 경고
    case "spine_skip": return "⚠️ 이야기 구조 없이 진행합니다 — 결말·단락 계획 없이 회차가 생성됩니다";
    case "bible_start": return "설정집을 쓰기 시작합니다…";
    case "bible": return `설정집 작성 중 — ${esc(ev.label)} <span class="muted">(${ev.idx}/${ev.total})</span>`;
    case "bible_done": return `설정집 완성 — ${ev.entries}개 항목`;
    case "contract_start": return "결말 감시 계약을 준비하는 중…";                       // EC-1(advisory)
    case "contract_done": return `결말 감시 준비 완료 — 술어 ${ev.predicates}개${ev.unexpressed?` (미표현 ${ev.unexpressed}건)`:""}`;
    case "contract_skip": return "결말 감시 계약 없이 진행합니다(참고 — 생성엔 영향 없음)";
    case "voice_card_start": return "서술자의 목소리를 빚는 중…";                          // ST-12a(1인칭 음성 카드)
    case "voice_card_done": return `서술자 음성 카드 완성 — 이 목소리로 지문을 서술합니다 (${ev.chars}자)`;
    case "voice_card_skip": return "서술자 음성 카드 없이 진행합니다(참고 — 생성엔 영향 없음)";
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
  renderKeywordChips();
}
// 트로프·키워드 택소노미(장르 필터) — 공통 + 장르별. B-01b: 칩 multi-select → locks.keywords (백엔드 foundation 주입).
const TROPE_PRESETS = {
  "공통": ["회귀","빙의","환생","후회","복수","성장","먼치킨","사이다","피폐","힐링"],
  "현대 판타지": ["헌터","던전","게이트","각성","레이드","탑등반","아카데미","재벌"],
  "게임/헌터": ["헌터","던전","게이트","각성","레이드","랭커","시스템","탑등반"],
  "판타지": ["아카데미","기사","마법사","용병","영지경영","소드마스터","대공"],
  "무협": ["정파","사파","마교","기연","무공","문파","천마","하극상"],
  "로맨스 판타지": ["악역영애","계약결혼","정략결혼","역하렘","아카데미","신분상승","육아","약혼파기"],
  "로맨스": ["계약연애","재벌","삼각관계","첫사랑","사내연애"],
  "SF": ["디스토피아","포스트아포칼립스","AI","우주","사이버펑크","시간여행"],
  "미스터리": ["추리","반전","범죄","스릴러","밀실"],
};
function tropeChipsFor(genre){
  const g = (genre||"").trim();
  const keys = Object.keys(TROPE_PRESETS).filter(k=>k!=="공통" && g.includes(k)).sort((a,b)=>b.length-a.length);
  const spec = keys.length ? TROPE_PRESETS[keys[0]] : TROPE_PRESETS["현대 판타지"];   // S2: 가장 구체적인 장르 1개만(로판이면 로판 트로프 — 판타지/로맨스 중복 폭증·핵심 트로프 잘림 방지)
  const seen=new Set(), out=[];
  for(const t of spec.concat(TROPE_PRESETS["공통"])){ if(!seen.has(t)){ seen.add(t); out.push(t); } }   // 장르 특화 먼저(cap 에서 안 잘리게) → 공통
  return out.slice(0,18);
}
function renderKeywordChips(){
  const box = $("#ctl-keyword-chips"); if(!box) return;
  const genre = ($("#ctl-genre")&&$("#ctl-genre").value) || ((STATE.draft&&STATE.draft.locks&&STATE.draft.locks.genre)||"");
  const sel = (STATE.draft&&STATE.draft.locks&&STATE.draft.locks.keywords) || [];
  const presets = tropeChipsFor(genre);
  const chips = presets.map(t=>`<span class="ctl-chip ${sel.includes(t)?'on':''}" onclick="toggleKeyword(this.textContent)">${esc(t)}</span>`);
  sel.filter(t=>!presets.includes(t)).forEach(t=>chips.push(`<span class="ctl-chip on" onclick="toggleKeyword(this.textContent)">${esc(t)}</span>`));   // 커스텀 선택분
  box.innerHTML = chips.join("");
}
function toggleKeyword(kw){
  kw=(kw||"").trim(); if(!kw) return;
  if(!STATE.draft) STATE.draft={id:null,locks:{}}; if(!STATE.draft.locks) STATE.draft.locks={};
  let arr = STATE.draft.locks.keywords ? STATE.draft.locks.keywords.slice() : [];
  if(arr.includes(kw)) arr = arr.filter(k=>k!==kw);
  else { if(arr.length>=6){ toast("키워드는 최대 6개까지 — 초점을 좁혀요","bad"); return; } arr.push(kw); }
  STATE.draft.locks.keywords = arr;   // 빈 배열도 유지(백엔드가 비면 잠금 해제)
  renderKeywordChips();
}
function addCustomKeyword(input){
  const kw=(input.value||"").trim().slice(0,24); input.value="";   // S3: 길이 캡(트로프 태그는 짧은 명사 — 프롬프트 오염·토큰 낭비 방지)
  if(!kw) return;
  const arr = (STATE.draft&&STATE.draft.locks&&STATE.draft.locks.keywords) || [];
  if(!arr.includes(kw)) toggleKeyword(kw);   // 새것만 추가(중복 무시)
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
  if(key==="genre") renderKeywordChips();   // 장르 바뀌면 트로프 추천 재필터
}
function applyBriefToControls(b){     // AI가 제안한 장르·분위기·회차를 컨트롤에 반영(작가가 잠근 건 건드리지 않음)
  const locks = (STATE.draft && STATE.draft.locks) || {};
  if(b.genre && !locks.genre) $("#ctl-genre").value = b.genre;
  if(b.tone && !locks.tone) $("#ctl-tone").value = b.tone;
  if(b.target_chapters && !locks.target_chapters) $("#ctl-target").value = b.target_chapters;
  renderKeywordChips();   // AI 장르 반영 후 트로프 추천 재필터
}

function startCreate(ev){
  ev.preventDefault();
  const seed = (ev.target.seed.value||"").trim(); if(!seed) return false;
  STATE.draft = { id:null, locks:{} }; _prevBrief = {}; STATE.worldSkills = new Set();   // 세계관 스킬 선택은 create 흐름마다 초기화(직전 작품 선택 누수 방지)
  go("#/new");   // 라우팅(STATE.draft 설정 후 — 라우터가 create 뷰 표시, 뒤로가기=홈)
  $("#cc-log").innerHTML = ""; $("#cc-questions").innerHTML = ""; $("#cc-gaps").innerHTML = "";
  hideConfirm();
  renderControlChips();
  $("#ctl-genre").value = ""; $("#ctl-tone").value = ""; $("#ctl-target").value = "200"; $("#ctl-keyword-input").value = "";
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
  $("#brief-pct").textContent = rdy ? `✓ 생성 준비 완료 · ${pct}%` : `완성도 ${pct}%`;   // 준비됐는데 '88% 고정'으로 멈춘 듯 보이던 문제 — 준비 상태를 명시
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
  if((b.keywords||[]).length) f.push(_mark(b, ["keywords"], `<div class="bf-field"><div class="bf-label">키워드·트로프</div><div class="bf-tags">`
    + b.keywords.map(t=>`<span>${esc(t)}</span>`).join("") + `</div></div>`));
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
  qp.set("keywords", (locks.keywords||[]).join("|"));   // B1: 칩 선택을 finalize 채널로 전달(대화 없이 바로 생성해도 반영, 빈 값=잠금 해제)
  if(STATE.worldSkills && STATE.worldSkills.size) qp.set("world_skills", [...STATE.worldSkills].join("|"));   // 라이브러리에서 고른 세계관 스킬
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

// ---------- 프로젝트 조회 — 회차는 요약본으로, 본문·이력은 펼칠 때 단건으로 ----------
// 회차 레코드는 회차당 ~90KB(대부분 퇴고 이력·생성 컨텍스트·판정 원문)라 전 회차를 한 번에 받으면
// 목록 한 줄 그리자고 수 MB 를 기다린다. 목록·현황이 쓰는 메타만 받고(요약본), 실제로 여는 회차만 채운다.
function _chapterFresh(full, lite){   // 이미 받아 둔 전체 레코드가 최신 요약본과 같은 판본인가
  return !!full && full.gen_no===lite.gen_no && full.status===lite.status
    && (full.revisions||[]).length===(lite.n_revisions||0)
    && (full.text||"").length===(lite.chars||0);
}
async function fetchProject(pid){
  const p = await api.get(`/api/projects/${pid}?chapters=summary`);
  // 이미 펼쳐 둔 회차는 그대로 유지 — 요약본으로 덮어써 열려 있던 본문이 사라지지 않게(판본 같을 때만).
  const prev = (STATE.project && STATE.project.id===pid) ? (STATE.project.chapters||[]) : [];
  const kept = new Map(prev.filter(c=>!c.lite && !c._ephemeral && !c._stale).map(c=>[c.chapter,c]));
  p.chapters = (p.chapters||[]).map(c=>{ const f=kept.get(c.chapter); return _chapterFresh(f,c)?f:c; });
  return p;
}
// 회차를 고친 뒤 부르면 캐시된 전체 레코드를 버린다(다음 fetchProject 가 서버 값으로 다시 채움).
// 요약본에 안 실리는 축(빨간펜·퇴고 이력·검증)만 바뀌는 편집은 _chapterFresh 로 구분되지 않으므로 명시 폐기가 필요하다.
function _invalidateChapter(n){
  const c = ((STATE.project&&STATE.project.chapters)||[]).find(x=>x.chapter===n);
  if(c) c._stale = true;
}
// 회차 하나를 채운다. view='full'(작업실 — 이력·검증까지) | 'text'(뷰어 — 본문만).
// 이미 가진 레코드로 충분하면 요청하지 않는다. 실패해도 예외를 내지 않는다(화면은 요약본으로라도 그린다).
async function ensureChapter(n, view){
  view = view || "full";
  if(!STATE.project || n==null) return null;
  const pid = STATE.project.id, chs = STATE.project.chapters||[];
  const i = chs.findIndex(c=>c.chapter===n);
  if(i<0) return null;
  const cur = chs[i];
  if(cur._ephemeral) return cur;                                   // 서버에 없는 미저장 검토본
  if(view==="full" ? !cur.lite : cur.text!=null) return cur;       // 이미 충분
  if(cur._loadFailed) return cur;                                  // 직전 조회 실패 — 무한 재시도 금지
  try{
    const got = await api.get(`/api/projects/${pid}/chapters/${n}?view=${view}`);
    if(!(STATE.project && STATE.project.id===pid)) return null;    // await 사이 작품 전환(TOCTOU)
    const j = (STATE.project.chapters||[]).findIndex(c=>c.chapter===n);
    if(j>=0) STATE.project.chapters[j] = (view==="text") ? {...STATE.project.chapters[j], ...got} : got;
    return j>=0 ? STATE.project.chapters[j] : got;
  }catch(e){
    const j = (STATE.project&&STATE.project.id===pid) ? (STATE.project.chapters||[]).findIndex(c=>c.chapter===n) : -1;
    if(j>=0) STATE.project.chapters[j]._loadFailed = true;
    return j>=0 ? STATE.project.chapters[j] : null;
  }
}

// ---------- 프로젝트 열기(로드+렌더 — 뷰 표시는 라우터가) ----------
async function openProject(pid){   // 데이터 로드 전용 — 뷰 초기화·섹션 로드는 라우터(showSection)가 담당
  if(_genES){ try{_genES.close();}catch(e){} }   // 이전 작품 생성 SSE 정리(서버 잡은 계속 — 끊어도 안전)
  _genES=null; _genDone=true; STATE.generating=false; genStop();   // _genDone=true: 끊긴 이전 ES 의 onerror 재접속 억제
  STATE.project = null;                          // 이전 작품 회차 캐시 격리(fetchProject 병합 대상 오염 방지)
  STATE.project = await fetchProject(pid);
  STATE.readiness = await api.get(`/api/projects/${pid}/readiness`).catch(()=>null);   // T6: 집필 준비도 advisory(비차단)
  STATE.activeChapter = STATE.project.current_chapter || null;
  STATE.chapterPage = null;          // null → renderChapters 가 최신(마지막) 페이지로
  STATE.section = undefined; STATE.bibleSub = "bible"; STATE.serialSub = "status";
  renderHeader();
  $("#harness-log").innerHTML = ""; $("#gen-result").innerHTML = "";   // 이전 작품 잔여 로그 제거(요소는 항상 DOM 상주)
  { const d=$("#directive"); if(d) d.value=""; const k=$("#dir-keep"); if(k) k.checked=false; }   // 작품별 작가지시 격리 — 다음 작품 누수 차단
  resumeGenerationIfRunning(pid);   // 진행 중인 회차 생성이 있으면 자동 재연결(새로고침/재접속 복원). 非동기 — 화면은 즉시 렌더
  renderWriteSkillHint();           // 집필 화면 상단의 '적용 스킬' 힌트(관리는 사이드바 ✨ 스킬)
}

// ---------- 스킬 ----------
// 전역 라이브러리(작품 무관 카탈로그)에 정의가 살고, 작품은 '주입'(injected_skills membership)으로 쓴다.
// 라이브러리 편집은 주입한 모든 작품의 다음 회차부터 반영(참조형 live SSOT). 과거 회차는 생성 시 스냅샷되어 불변.
const SKILL_POINTS = [
  {key:"chapter",  label:"회차 집필",   note:"주입한 스킬이 다음 회차 집필에 반영(문체·전개)"},
  {key:"revise",   label:"퇴고",        note:"주입한 스킬이 퇴고 다듬기에 반영(사실 불변)"},
  {key:"worldgen", label:"세계관 생성", note:"작품을 만들 때 적용(이미 생성된 세계엔 재적용 안 됨)"},
];
const SKILL_CAP = 3;   // 지점당 합성 상한(서버 SKILL_COMPOSE_CAP 과 동일 — 시각화용)
let _skillsCache = [];

// ===== 작품별 주입 패널(체크박스 대신 '주입'): 라이브러리 선반 → 주입 슬롯 =====
async function renderSkillsManager(){
  const el = $("#skills-manager"); if(!el || !STATE.project) return;
  el.innerHTML = '<div class="muted">불러오는 중…</div>';
  let data;
  try{ data = await api.get(`/api/projects/${STATE.project.id}/skills`); }
  catch(e){ el.innerHTML = `<div class="muted">불러오지 못했습니다: ${esc(e.message)}</div>`; return; }
  _skillsCache = data.skills || [];
  const injectedIds = data.injected || [];
  const groups = SKILL_POINTS.map(p=>{
    const all = _skillsCache.filter(s=>s.point===p.key);
    const injected = injectedIds.map(id=>all.find(s=>s.id===id)).filter(Boolean);   // 주입 순서 보존
    const shelf = all.filter(s=>!s.injected);
    return p.key==='worldgen' ? worldgenGroupHtml(p, injected) : injectGroupHtml(p, injected, shelf);
  }).join("");
  el.innerHTML = `<div class="skills-head">
      <div><h3 style="margin:0">✨ 스킬</h3>
        <div class="muted small">라이브러리 스킬을 이 작품에 <b>주입</b>하면 해당 지점 생성에 반영됩니다 — 지점당 ${SKILL_CAP}개까지 적용</div></div>
      <a class="btn-ghost" ${ACT} onclick="go('#/skills')">＋ 라이브러리에서 만들기</a></div>${groups}`;
}
function injectGroupHtml(p, injected, shelf){
  let slots = "";
  const n = Math.max(SKILL_CAP, injected.length);
  for(let i=0;i<n;i++){
    const s = injected[i];
    if(s){
      const over = i>=SKILL_CAP;
      slots += `<div class="slot filled ${over?'over':''}"${over?' title="초과 — 미적용(앞 '+SKILL_CAP+'개만 적용)"':''}>
        <span class="slot-name">${esc(s.name)}</span>${s.builtin?'<span class="skill-badge">내장</span>':''}
        <a class="slot-eject" ${ACT} onclick="ejectSkill('${s.id}')" title="빼기">×</a></div>`;
    } else slots += `<div class="slot empty">주입 슬롯</div>`;
  }
  const shelfHtml = shelf.length ? shelf.map(s=>`<div class="shelf-card">
      <div class="shelf-main"><span class="skill-name">${esc(s.name)}</span>${s.builtin?'<span class="skill-badge">내장</span>':''}
        ${s.description?`<div class="muted small">${esc(s.description)}</div>`:''}</div>
      <button class="btn-inject" onclick="injectSkill('${s.id}')">주입 →</button></div>`).join("")
    : '<div class="muted small">라이브러리에 남은 스킬이 없습니다 · <a '+ACT+' onclick="go(\'#/skills\')">새로 만들기</a></div>';
  return `<div class="skill-group"><h4>${esc(p.label)} <span class="muted small">— ${esc(p.note)}</span></h4>
    <div class="inject-rack">${slots}</div>
    <div class="shelf-label muted small">라이브러리 선반</div>
    <div class="shelf">${shelfHtml}</div></div>`;
}
function worldgenGroupHtml(p, injected){
  const chips = injected.length
    ? `<div class="inject-rack">`+injected.map(s=>`<div class="slot filled frozen"><span class="slot-name">${esc(s.name)}</span><span class="skill-badge">생성 시 적용</span></div>`).join("")+`</div>`
    : '<div class="muted small">이 작품 생성에 적용된 세계관 스킬이 없습니다</div>';
  return `<div class="skill-group"><h4>${esc(p.label)} <span class="muted small">— ${esc(p.note)}</span></h4>
    ${chips}
    <div class="muted small" style="margin-top:.5em">세계관 스킬은 <b>새 작품 만들기</b> 화면에서 고릅니다(이미 생성된 세계엔 다시 주입되지 않음).</div></div>`;
}
async function injectSkill(sid){
  if(!STATE.project) return;
  try{ await api.post(`/api/projects/${STATE.project.id}/skills/${sid}/inject`, {});
       renderSkillsManager(); renderWriteSkillHint(); }
  catch(e){ alert(e.message || "주입 실패 — 회차 생성 중이면 잠시 후 다시"); }
}
async function ejectSkill(sid){
  if(!STATE.project) return;
  try{ await api.del(`/api/projects/${STATE.project.id}/skills/${sid}/inject`);
       renderSkillsManager(); renderWriteSkillHint(); }
  catch(e){ alert(e.message || "빼기 실패 — 회차 생성 중이면 잠시 후 다시"); }
}
async function renderWriteSkillHint(){
  const el = $("#write-skill-hint"); if(!el || !STATE.project) return;
  // 항상 현재 작품의 주입 상태를 재조회 — 캐시는 작품 전환/인젝트 직후 stale(직전 작품 스킬을 표시하는 결함 차단)
  try{ _skillsCache = (await api.get(`/api/projects/${STATE.project.id}/skills`)).skills || []; }catch(e){ return; }
  const on = _skillsCache.filter(s=>s.injected && s.point==='chapter');
  el.innerHTML = on.length
    ? `<span class="muted small">✨ 주입된 스킬: <b>${on.map(s=>esc(s.name)).join(' · ')}</b></span> <a ${ACT} onclick="goSection('skills')">관리</a>`
    : `<a ${ACT} onclick="goSection('skills')">✨ 집필 스킬 주입하기</a>`;
}

// ===== 전역 스킬 라이브러리(메인 화면 — 작품 무관) =====
let _libCache = [];
async function renderSkillsLibrary(){
  const el = $("#skills-library"); if(!el) return;
  el.innerHTML = '<div class="muted">불러오는 중…</div>';
  try{ _libCache = (await api.get('/api/skills')).skills || []; }
  catch(e){ el.innerHTML = `<div class="muted">불러오지 못했습니다: ${esc(e.message)}</div>`; return; }
  const groups = SKILL_POINTS.map(p=>{
    const list = _libCache.filter(s=>s.point===p.key);
    const rows = list.length ? list.map(libCardHtml).join("") : '<div class="muted small">스킬 없음</div>';
    return `<div class="skill-group"><h4>${esc(p.label)} <span class="muted small">— ${esc(p.note)}</span></h4>${rows}</div>`;
  }).join("");
  el.innerHTML = `<div class="lib-head">
      <div><h2 style="margin:0">✨ 스킬 라이브러리</h2>
        <div class="muted small">여기서 만든 스킬을 각 작품에 <b>주입</b>해 씁니다. 한 곳에서 고치면 주입한 모든 작품의 다음 회차부터 반영됩니다.</div></div>
      <div class="lib-head-act"><button class="primary" onclick="openLibrarySkillEditor()">+ 새 스킬</button>
        <button onclick="goHome()">← 홈</button></div></div>
    <div class="skills-manager">${groups}</div>`;
}
function libCardHtml(s){
  return `<div class="skill-card">
    <div class="skill-card-top"><span class="skill-name">${esc(s.name)}</span>${s.builtin?'<span class="skill-badge">내장</span>':''}</div>
    ${s.description?`<div class="skill-card-desc muted small">${esc(s.description)}</div>`:''}
    ${s.builtin?'<div class="skill-card-act muted small">내장 — 편집·삭제 불가</div>'
      :`<div class="skill-card-act"><a ${ACT} onclick="openLibrarySkillEditor('${s.id}')">편집</a> · <a ${ACT} onclick="deleteLibrarySkill('${s.id}')">삭제</a></div>`}
  </div>`;
}
function openLibrarySkillEditor(sid){
  const s = sid ? _libCache.find(x=>x.id===sid) : null;
  const opts = SKILL_POINTS.map(p=>`<option value="${p.key}" ${(s?s.point===p.key:p.key==='chapter')?'selected':''}>${esc(p.label)}</option>`).join("");
  openModal(`<h3>${s?'스킬 편집':'새 스킬'}</h3>
    <label class="fld">이름<input id="sk-name" value="${s?esc(s.name):''}" maxlength="40" placeholder="예: 느와르 톤"></label>
    <label class="fld">적용 지점<select id="sk-point">${opts}</select></label>
    <label class="fld">한 줄 설명<input id="sk-desc" value="${s?esc(s.description||''):''}" maxlength="120"></label>
    <label class="fld">지시(쓰는 법)<textarea id="sk-inst" rows="4" placeholder="이 스킬이 이 지점에서 어떻게 쓰게 할지">${s?esc(s.instructions||''):''}</textarea></label>
    <label class="fld">예시 — 결·리듬만(빈 줄로 구분, 내용은 안 베낍니다)<textarea id="sk-ex" rows="6" placeholder="예시 한 토막…&#10;&#10;다른 토막…">${s?esc((s.examples||[]).join('\n\n')):''}</textarea></label>
    <label class="fld muted">모델 라우팅 <span class="small">(예정 — 아직 미적용)</span><input id="sk-model" value="${s?esc(s.model||''):''}" placeholder="provider:model" disabled></label>
    <div class="modal-act"><button class="primary" onclick="saveLibrarySkill('${sid||''}')">저장</button> <button data-close>취소</button></div>`);
}
async function saveLibrarySkill(sid){
  const body = {
    name: $("#sk-name").value.trim(), point: $("#sk-point").value,
    description: $("#sk-desc").value.trim(), instructions: $("#sk-inst").value.trim(),
    examples: $("#sk-ex").value.split(/\n\s*\n/).map(x=>x.trim()).filter(Boolean),
  };
  if(!body.name){ alert("이름을 입력하세요"); return; }
  try{
    if(sid) await api.put(`/api/skills/${sid}`, body);   // 편집 = in-place(id 불변 → 주입 참조 유지)
    else await api.post('/api/skills', body);
    closeModal(document.querySelector('.modal-overlay'));
    renderSkillsLibrary();
  }catch(e){ alert(e.message||"저장 실패"); }
}
async function deleteLibrarySkill(sid){
  if(!confirm("이 스킬을 라이브러리에서 삭제할까요? 주입한 작품에서도 빠집니다.")) return;
  try{ await api.del(`/api/skills/${sid}`); renderSkillsLibrary(); }
  catch(e){ alert(e.message||"삭제 실패"); }
}

// ===== 새 작품 만들기 — 세계관 스킬 선택(라이브러리에 worldgen 스킬이 있을 때만 노출) =====
async function renderWorldSkillPicker(){
  const el = $("#world-skill-picker"); if(!el) return;
  let list = [];
  try{ list = ((await api.get('/api/skills')).skills || []).filter(s=>s.point==='worldgen'); }catch(e){ }
  if(!list.length){ el.classList.add('hidden'); el.innerHTML=''; return; }
  if(!STATE.worldSkills) STATE.worldSkills = new Set();
  el.classList.remove('hidden');
  el.innerHTML = `<div class="wsp-label">✨ 세계관 스킬 <span class="muted small">— 이 세계 생성에 적용(선택, 최대 ${SKILL_CAP}개)</span></div>` +
    list.map(s=>`<label class="wsp-item"><input type="checkbox" ${STATE.worldSkills.has(s.id)?'checked':''}
      onchange="toggleWorldSkill('${s.id}', this)"> <span>${esc(s.name)}</span></label>`).join("");
}
function toggleWorldSkill(sid, cb){
  if(!STATE.worldSkills) STATE.worldSkills = new Set();
  if(cb.checked){
    if(STATE.worldSkills.size >= SKILL_CAP){ cb.checked = false; alert(`세계관 스킬은 최대 ${SKILL_CAP}개까지 적용됩니다`); return; }   // 상한 초과 선택 차단(초과분이 조용히 버려지지 않게)
    STATE.worldSkills.add(sid);
  } else STATE.worldSkills.delete(sid);
}
function renderHeader(){
  const p = STATE.project, u = p.usage_total||{};
  $("#p-title").textContent = p.world.title || "무제";
  $("#p-meta").innerHTML = `${esc(p.world.genre)} · ${esc(p.world.tone)}<br><span class="muted">${esc(p.world.premise||"")}</span>`;
  $("#p-progress").textContent = `${p.current_chapter} / ${p.total_beats}화${p.completed?" · 완결":""}`;
  $("#p-cost").textContent = `AI 사용량 ${(u.chat_calls||0).toLocaleString()}회 · ${(u.chat_tokens||0).toLocaleString()}토큰`;
  const done=!!p.completed; const gb=$("#gen-btn");
  if(gb){ gb.disabled=done; gb.textContent=done?"완결되었습니다":"다음 회차 쓰기"; }
  renderStaleDerivatives();
}

// XR-7①: 다음 회차를 쓰기 전 '퇴고 반영 안 된 자료' 사전 점검(참고 — 생성을 막지 않아요).
//   자동 재계산은 하지 않는다: 목록과 버튼만 두고 누를지는 작가가 정한다(무강제).
function renderStaleDerivatives(){
  const el = $("#stale-derivatives"); if(!el) return;
  const items = (STATE.readiness && STATE.readiness.stale_derivatives) || [];
  // XR-18: 지문 불일치(퇴고 표식 없이 본문이 바뀐 경우의 이중 방어) — 있으면 위키 격리·재구축 안내 동일 노출.
  const fpMM = (STATE.readiness && STATE.readiness.wiki_fingerprint_mismatch) || [];
  if(!items.length && !fpMM.length){ el.innerHTML = ""; return; }
  // XR-10: 위키는 회차별 재계산이 아니라 전체 재구축이 정답(누적 페이지에 퇴고 전 서술이 남는 구조) —
  //   재구축 전까지는 생성에서 위키를 사용하지 않는다(격리 — 서버가 자동 제외 + 여기 안내).
  const hasWiki = items.some(it=>(it.names||[]).includes("wiki")) || fpMM.length > 0;
  const wikiNote = hasWiki
    ? `<div class="rd-flag stale-wiki-note">📔 인물 노트(위키)는 재구축 전까지 생성에 사용하지 않아요.
        <button class="revise-btn" onclick="rebuildWiki()">인물 노트 전체 재구축</button></div>` : "";
  el.innerHTML = `<div class="rd-head">↻ 퇴고 반영 안 된 자료 <span class="muted small">참고 — 생성을 막지 않아요</span></div>`
    + wikiNote
    + items.map(it=>`<div class="rd-flag"><b>${it.chapter}화</b> ${esc(it.names.map(staleLabel).join(", "))}
        <div class="stale-acts">${it.names.map(k=> k==="promise_ledger"
          ? `<span class="muted tiny">복선·약속 정리는 연재 관리의 약속 원장에서 직접 정리해 주세요</span>`
          : k==="wiki"
          ? `<span class="muted tiny">${esc(staleLabel(k))}는 위의 전체 재구축으로 한 번에 갱신돼요</span>`
          : `<button class="revise-btn" onclick="recomputeDerivative(${it.chapter},'${k}')">${esc(staleLabel(k))} 다시 계산</button>`).join(" ")}</div></div>`).join("");
}
// XR-10 정식: Wiki Projection 전체 재구축(작가 발동) — 확정 회차 replay(회차당 1콜, 몇 분 걸릴 수 있음).
async function rebuildWiki(){
  if(!STATE.project) return;
  const pid = STATE.project.id;
  if(!confirm("인물 노트(위키)를 확정 회차 전체로 다시 만듭니다. 회차 수만큼 AI 호출이 발생해요. 진행할까요?")) return;
  const btns = [...document.querySelectorAll(".stale-acts button, .stale-wiki-note button")];
  btns.forEach(b=>b.disabled=true);
  try{
    const r = await api.post(`/api/projects/${pid}/wiki/rebuild`, {});
    toast(`인물 노트를 ${r.rebuilt_chapters}개 확정 회차로 재구축했어요(페이지 ${r.pages}개).`, "ok");
    STATE.project = await fetchProject(pid);
    STATE.readiness = await api.get(`/api/projects/${pid}/readiness`).catch(()=>null);
    renderHeader(); renderReader();
  }catch(e){
    toast("재구축하지 못했습니다: "+e.message, "bad");
  }finally{ btns.forEach(b=>b.disabled=false); }
}
async function recomputeDerivative(ch, name){
  if(!STATE.project) return;
  const pid = STATE.project.id;
  const btns = [...document.querySelectorAll(".stale-acts button")]; btns.forEach(b=>b.disabled=true);
  try{
    await api.post(`/api/projects/${pid}/chapters/${ch}/derivatives/recompute`, {name});
    toast(`${ch}화 ${staleLabel(name)}을(를) 지금 본문으로 다시 계산했어요.`, "ok");
    STATE.project = await fetchProject(pid);
    STATE.readiness = await api.get(`/api/projects/${pid}/readiness`).catch(()=>null);
    renderHeader(); renderReader();
  }catch(e){
    toast("다시 계산하지 못했습니다: "+e.message, "bad");
  }finally{ btns.forEach(b=>b.disabled=false); }
}

// ---------- 설정집(스토리 바이블, R2) ----------
// 항목이 수백 개가 되면 한 번에 다 받느라 탭 진입이 느려진다 — 묶음으로 받아 이어 붙인다(분류 묶음 렌더는 유지).
const BIBLE_PAGE = 60;
let BIBLE = {entries:[], total:0, has_more:false};
async function loadBible(){
  BIBLE = {entries:[], total:0, has_more:false};
  const el = $("#tab-bible");
  if(el) el.innerHTML = '<p class="muted"><span class="spin"></span> 설정집을 불러오는 중…</p>';
  await loadMoreBible();
}
async function loadMoreBible(){
  const el = $("#tab-bible"); if(!el || !STATE.project) return;
  const pid = STATE.project.id;
  try{
    const b = await api.get(`/api/projects/${pid}/bible?offset=${BIBLE.entries.length}&limit=${BIBLE_PAGE}`);
    if(!(STATE.project && STATE.project.id===pid)) return;   // await 사이 작품 전환(TOCTOU)
    STATE.bibleMeta = {labels:b.category_labels||{}, template:b.template||[]};   // addBible 한국어 분류 셀렉트용
    BIBLE.entries = BIBLE.entries.concat(b.entries||[]);
    BIBLE.total = b.total!=null ? b.total : BIBLE.entries.length;
    BIBLE.has_more = !!b.has_more;
    renderBible(b);
  }catch(e){ el.innerHTML = `<span class="muted">불러오지 못했습니다: ${esc(e.message)}</span>`; }
}
function renderBible(b){
  const el = $("#tab-bible"); if(!el) return;
  const labels = b.category_labels||{}, template = b.template||[];
  const byCat = {};
  BIBLE.entries.forEach(e=>{ (byCat[e.category]=byCat[e.category]||[]).push(e); });
  const cats = [...template, ...Object.keys(byCat).filter(c=>!template.includes(c))];
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
    return `<div class="bible-sec"><h4>${esc(labels[c]||c)}</h4>${items}</div>`;
  }).join("");
  const more = BIBLE.has_more
    ? `<div class="load-more"><button onclick="loadMoreBible()">더 보기</button>
       <span class="muted small">${BIBLE.entries.length} / ${BIBLE.total}개</span></div>` : "";
  el.innerHTML = `<div class="bible-toolbar"><button onclick="addBible()">＋ 설정 항목</button>
    <span class="muted small">‘공식 설정으로 확정’하면 일관성 검사가 추적합니다. 내용을 클릭하면 바로 편집할 수 있어요(자동 저장).</span></div>`
    + (secs || '<p class="muted">설정집이 비어 있습니다. ＋로 항목을 추가하세요.</p>') + more;
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
    // EP-PUB 발행 점 — 발행됨(파랑)·발행 후 수정됨(주황). 미발행이면 없음. 목차에서 한눈에 어디까지 올렸는지.
    const ps = c.publish_state || ((c.published_at||"").trim() ? "published" : "unpublished");
    const pdot = ps==="published" ? '<i class="cn-pub pub" title="발행됨"></i>'
               : ps==="modified" ? '<i class="cn-pub mod" title="발행 후 수정됨"></i>' : "";
    return `<button class="cn ${a} ${e}" onclick="selectChapter(${c.chapter})">${c.chapter}화${pdot}</button>`;
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
  const beatBlk=`<div class="gd-row"><b>회차 기능</b> ${esc(b.chapter_function||'–')} · 훅 ${esc(b.hook_type||'–')} · 마무리 ${esc(b.closing_device||'–')} · 시간 ${esc(b.time_advance||'–')} · 장소 ${esc(b.place||'–')}</div>
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
    ${d.story_time?`<div class="gd-row"><b>이야기 시점</b> <span class="gd-tag canon">🕒 ${esc(d.story_time)}</span> <span class="muted tiny">(코드 결정론 누적 — 모델은 읽기만)</span></div>`:''}
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
  loadTracePanel();   // GA-2: 트레이스 패널도 활성 회차와 동기화(열려 있을 때만 재요청·닫혀 있으면 무동작)
  const el=$("#inspect-gen"); if(!el) return;
  const c=((STATE.project&&STATE.project.chapters)||[]).find(x=>x.chapter===STATE.activeChapter);
  if(!c){ el.innerHTML='<div class="inspect-head">회차를 고르면, 그 회차를 <b>어떤 정보로 만들었는지</b>(설계·집필 입력)가 여기 표시됩니다.</div>'; return; }
  // 아직 요약본이면 '기록 없음'으로 단정하지 않는다 — 본문과 함께 채워지는 중(renderReader 가 채우고 다시 그린다).
  if(c.lite && !c._loadFailed){ el.innerHTML='<div class="inspect-head"><span class="spin"></span> 생성 정보를 불러오는 중…</div>'; return; }
  el.innerHTML=`<div class="inspect-head">${c.chapter}화를 만든 정보 — 설계·집필에 들어간 입력 슬롯</div>`+genContextHtml(c);
}

// ═══ GA-2/FI-2: 생성 디버그 트레이스 뷰어 ═══
// GA-1 사이드카(첫 초안·판정 전문·수술 전후·후보·이벤트·실패)를 사람이 읽는 시간순 타임라인으로 렌더하고(GA-2),
// 그 위에 작가 의도 이벤트(FI-1)·퇴고 이력(revisions)·재생성 로그(regen_events)를 ts 기준 조인한다(FI-2).
// 접기 패턴·esc() 이스케이프는 기존 gen-debug 관례를 그대로 따른다. 원문 인용은 반드시 esc()(XSS).
function _trExcerpt(s,n){ n=n||160; s=(s==null?"":String(s)).replace(/\r\n/g,"\n").replace(/\n/g," ⏎ "); return s.length<=n?s:s.slice(0,n)+` …(+${s.length-n}자)`; }
function _trCoreDiff(before,after){   // 공통 접두/접미 벗긴 '변경 코어'만(전문 덤프 아님 — CLI _core_diff 미러)
  before=before==null?"":String(before); after=after==null?"":String(after);
  const bl=before.length, al=after.length, m=Math.min(bl,al);
  let i=0; while(i<m && before[i]===after[i]) i++;
  let j=0; while(j<(m-i) && before[bl-1-j]===after[al-1-j]) j++;
  return {before_len:bl, after_len:al, core_offset:i, core_before:before.slice(i,bl-j), core_after:after.slice(i,al-j)};
}
function _trDiffHtml(before,after){   // first_draft→final 수술 diff 요약(길이 델타 + 변경 코어 발췌)
  const d=_trCoreDiff(before,after);
  if(d.core_before===d.core_after) return `<div class="gd-line muted tiny">변경 없음 (길이 ${d.before_len}자)</div>`;
  return `<div class="gd-line tiny">수술: 길이 ${d.before_len}→${d.after_len}자 · 변경 코어 @${d.core_offset}</div>`
    + `<div class="tr-diff"><span class="tr-del">- ${esc(_trExcerpt(d.core_before,140))}</span>`
    + `<span class="tr-add">+ ${esc(_trExcerpt(d.core_after,140))}</span></div>`;
}
function _tsKey(ts){   // FI-2 조인 정렬 키: tz 오프셋(±HHMM/±HH:MM/Z)만 벗겨 로컬 문자열로(산술 변환 없음·결정론)
  if(!ts) return ""; let s=String(ts).trim();
  if(s.endsWith("Z")) return s.slice(0,-1);
  const m=s.match(/[+-]\d{2}:?\d{2}$/); return m?s.slice(0,m.index):s;
}
// ── kind 별 body HTML(원문 인용은 전부 esc) ──
function _trGenerate(run){   // GA-2: 생성 run — 수술 diff·라운드·판정·휴머나이즈·이벤트·실패
  let h="";
  const fails=run.failures||[];
  if(fails.length){   // 실패 하이라이트(승격 — 디버깅 빠른 진입점)
    h+=`<div class="tr-fail"><b>⚠ 실패 ${fails.length}건</b>`+fails.map(f=>`<div class="gd-line tiny">⚠ ${esc(f.node)}.${esc(f.event)}</div>`).join("")+`</div>`;
  }
  if(run.first_draft!=null||run.final_text!=null) h+=_trDiffHtml(run.first_draft,run.final_text);
  const rounds=run.rewrite_rounds||[];
  if(rounds.length) h+=`<div class="gd-line tiny"><b>재작성 라운드 ${rounds.length}회</b> `+rounds.map(r=>`round${r.round}(${esc((r.fixing||[]).join(","))||"—"})`).join(" · ")+`</div>`;
  const sj=run.style_judgment;
  if(sj&&typeof sj==="object"){
    h+=`<div class="gd-line tiny"><b>style_judge</b> needs_repair=${sj.needs_repair} · ${esc(_trExcerpt(sj.reason,120))}</div>`;
    (sj.spans||[]).forEach(s=>h+=`<div class="gd-line tiny tr-quote">“${esc(_trExcerpt(s.quote,80))}” — ${esc(_trExcerpt(s.why,80))}</div>`);
    (sj.motif_spans||[]).forEach(s=>h+=`<div class="gd-line tiny tr-quote">모티프 “${esc(_trExcerpt(s.quote,80))}” — ${esc(_trExcerpt(s.why,80))}</div>`);
  }
  const hs=run.humanize_spans||[];
  if(hs.length){
    h+=`<div class="gd-line tiny"><b>휴머나이즈 스팬 ${hs.length}건</b></div>`;
    hs.forEach(e=>{ const chg=e.changed?"변경":"유지"; const rv=e.author_review?" · ⚑작가 확인":"";
      h+=`<div class="gd-line tiny">[${esc((e.category||"?")+"/"+(e.severity||"?"))}] ${chg}${rv}`;
      if(e.before!=null) h+=`<div class="tr-diff"><span class="tr-del">- ${esc(_trExcerpt(e.before,100))}</span>`
        +(e.after!=null&&e.after!==e.before?`<span class="tr-add">+ ${esc(_trExcerpt(e.after,100))}</span>`:(e.note?`<span class="muted">· ${esc(_trExcerpt(e.note,100))}</span>`:""))+`</div>`;
      h+=`</div>`; });
  }
  const evs=run.events||[];
  if(evs.length) h+=`<details class="tr-sub"><summary>이벤트 타임라인 ${evs.length}건</summary>`
    +evs.map(ev=>{ const extra={}; Object.keys(ev).forEach(k=>{ if(!["seq","node","event","chapter","ts"].includes(k)) extra[k]=ev[k]; });
      const xs=Object.keys(extra).length?" · "+esc(_trExcerpt(JSON.stringify(extra),120)):"";
      return `<div class="gd-line tiny">${ev.seq} ${esc(ev.node)}.${esc(ev.event)}${xs}</div>`; }).join("")+`</details>`;
  return h;
}
function _trRerender(run){   // GA-2: 재실현 run — 후보·리랭크 eval·가드·정독
  let h=`<div class="gd-line tiny">mode=${esc(run.mode)} · 채택=${run.adopted} · ${esc(_trExcerpt(run.reason,120))}</div>`;
  const cands=run.candidates||[];
  h+=`<div class="gd-line tiny">후보 ${cands.length}개 · 승자 index=${run.winner_index}</div>`;
  (run.evaluations||[]).forEach(ev=>{ const dq=ev.disqualified?"실격":"통과";
    const metrics=["top_ratio","max_run","da_ratio","uninterrupted_run_max"].filter(k=>ev[k]!=null).map(k=>`${k}=${ev[k]}`).join(" · ");
    h+=`<div class="gd-line tiny">후보${ev.index}: ${dq} [${esc((ev.reasons||[]).join(", "))||"—"}]${metrics?" · "+esc(metrics):""}</div>`; });
  if(run.guardrail&&typeof run.guardrail==="object") h+=`<div class="gd-line tiny">가드 passed=${run.guardrail.passed} · ${esc(_trExcerpt(run.guardrail.reason,100))}</div>`;
  if(run.read_gate&&typeof run.read_gate==="object") h+=`<div class="gd-line tiny">정독 adopt=${run.read_gate.adopt} · ${esc(_trExcerpt(run.read_gate.reason,100))}</div>`;
  return h;
}
function _trRerenderPipe(run){   // GA-2: 재실현 훅 라이프사이클(RO-1)
  const ev=run.event||"?"; const fields={}; Object.keys(run).forEach(k=>{ if(!["kind","event","ts","chapter","traceback"].includes(k)) fields[k]=run[k]; });
  const cls=ev==="failure"?"tr-fail":"";
  let h=`<div class="gd-line tiny ${cls}">event=${esc(ev)}${Object.keys(fields).length?" · "+esc(_trExcerpt(JSON.stringify(fields),140)):""}</div>`;
  if(run.traceback) h+=`<div class="gd-line tiny tr-fail">traceback: ${esc(_trExcerpt(run.traceback,200))}</div>`;
  return h;
}
function _trAuthorIntent(run){   // FI-2: 작가 의도 이벤트 — surface별 원문 인용(measure-then-cite·비율 0)
  const p=run.payload||{}; let h="";
  if(p.directive) h+=`<div class="gd-line tiny tr-quote">지시: “${esc(_trExcerpt(p.directive,200))}”</div>`;
  if(p.span_text) h+=`<div class="gd-line tiny tr-quote">구간: “${esc(_trExcerpt(p.span_text,160))}”</div>`;
  if(p.note) h+=`<div class="gd-line tiny tr-quote">메모: “${esc(_trExcerpt(p.note,200))}”</div>`;
  if(p.reason) h+=`<div class="gd-line tiny">사유: ${esc(_trExcerpt(p.reason,160))}</div>`;
  const core=p.core;
  if(core&&typeof core==="object"){
    h+=`<div class="gd-line tiny">가드 passed=${p.guardrail_passed} · 변경 코어 @${core.core_offset} 길이 ${core.before_len}→${core.after_len}자</div>`;
    if(core.core_before||core.core_after) h+=`<div class="tr-diff"><span class="tr-del">- ${esc(_trExcerpt(core.core_before,120))}</span><span class="tr-add">+ ${esc(_trExcerpt(core.core_after,120))}</span></div>`;
  }
  const shown=["directive","span_text","note","reason","core","guardrail_passed","passes","latency_sec"];
  const rest={}; Object.keys(p).forEach(k=>{ if(!shown.includes(k)) rest[k]=p[k]; });
  if(Object.keys(rest).length) h+=`<div class="gd-line tiny muted">상세: ${esc(_trExcerpt(JSON.stringify(rest),200))}</div>`;
  return h;
}
function _trRevision(rev,event){   // FI-2: 퇴고 이력(before/after 전문 대신 지시+변경 코어 발췌)
  if(event==="undo") return `<div class="gd-line tiny">id=${esc(rev.revision_id)}</div>`;
  let h=`<div class="gd-line tiny">id=${esc(rev.revision_id)} · 가드 passed=${rev.guardrail_passed}</div>`;
  if(rev.directive) h+=`<div class="gd-line tiny tr-quote">지시: “${esc(_trExcerpt(rev.directive,200))}”</div>`;
  if(rev.span_text) h+=`<div class="gd-line tiny tr-quote">구간: “${esc(_trExcerpt(rev.span_text,160))}”</div>`;
  h+=_trDiffHtml(rev.before_text,rev.after_text);
  return h;
}
// 항목 라벨(kind → 표시명·아이콘). 원장-뷰 분리: revision/regen 은 SSOT 레코드, author_intent 는 이벤트 참조.
const TR_LABEL={generate:"■ 생성",rerender:"■ 재실현",rerender_pipeline:"■ 재실현 훅",author_intent:"● 작가 의도",revision:"◆ 퇴고",regen:"◇ 재생성"};
function _buildTraceItems(doc,c,ch){   // FI-2: 3원천 조인 — trace runs + revisions + regen_events
  const items=[];
  ((doc&&doc.runs)||[]).forEach(run=>{
    const kind=run.kind||"generate";   // 구 generate run 은 kind 없을 수 있음
    let body="", surface=null, actor=run.actor||null, sub="";
    if(kind==="generate"){ body=_trGenerate(run); sub=run.status?` · ${esc(run.status)}`:""; if(run.gen_no!=null) sub+=` · 세대 ${run.gen_no}`; }
    else if(kind==="rerender") body=_trRerender(run);
    else if(kind==="rerender_pipeline"){ body=_trRerenderPipe(run); sub=` · ${esc(run.event||"?")}`; }
    else if(kind==="author_intent"){ body=_trAuthorIntent(run); surface=run.surface||null; sub=` · ${esc(surface||"?")} · actor=${esc(actor||"?")}`; if(run.gen_no!=null) sub+=` · 세대 ${run.gen_no}`; }
    else body=`<div class="gd-line tiny">알 수 없는 kind=${esc(kind)}</div>`;
    items.push({sortKey:_tsKey(run.ts),ts:run.ts||"",kind,actor,surface,sub,body});
  });
  ((c&&c.revisions)||[]).forEach(rev=>{
    items.push({sortKey:_tsKey(rev.created_at),ts:rev.created_at||"",kind:"revision",actor:"author",surface:null,sub:" · 채택",body:_trRevision(rev,"accept")});
    if(rev.reverted_at) items.push({sortKey:_tsKey(rev.reverted_at),ts:rev.reverted_at||"",kind:"revision",actor:"author",surface:null,sub:" · 되돌림",body:_trRevision(rev,"undo")});
  });
  (((STATE.project&&STATE.project.regen_events)||[])).forEach(ev=>{ if(ev.chapter===ch)
    items.push({sortKey:_tsKey(ev.at),ts:ev.at||"",kind:"regen",actor:"author",surface:null,sub:` · seq=${ev.seq} · ${ev.fix_selected?"점검 반영":"단순 재생성"}`,body:""}); });
  items.sort((a,b)=> a.sortKey<b.sortKey?-1:(a.sortKey>b.sortKey?1:0));   // 안정 정렬(같은 키=삽입 순서 보존)
  return items;
}
function renderTraceItems(){   // 필터 적용 후 목록 렌더(필터 변경 시 네트워크 재요청 없이 재렌더)
  const list=$("#trace-list"); if(!list) return;
  const f=STATE.traceFilter||{};
  let items=STATE.traceItems||[];
  if(f.kind) items=items.filter(it=>it.kind===f.kind);
  if(f.actor) items=items.filter(it=>it.actor===f.actor);
  if(f.surface) items=items.filter(it=>it.surface===f.surface);
  const filt=[["kind",f.kind],["actor",f.actor],["surface",f.surface]].filter(x=>x[1]).map(x=>`${x[0]}=${x[1]}`);
  let h=`<div class="gd-line small muted">필터 결과 ${items.length}건${filt.length?` (필터: ${esc(filt.join(", "))})`:""}</div>`;
  if(!items.length) h+=`<p class="muted small" style="padding:.4em 0">표시할 항목이 없습니다.</p>`;
  h+=items.map(it=>`<div class="tr-item tr-${esc(it.kind)}"><div class="tr-head"><span class="tr-ts">${esc(it.ts||"시각미상")}</span> <b>${esc(TR_LABEL[it.kind]||it.kind)}</b><span class="muted tiny">${it.sub||""}</span></div>${it.body?`<div class="tr-body">${it.body}</div>`:""}</div>`).join("");
  list.innerHTML=h;
}
function _trSetFilter(field,val){ STATE.traceFilter=Object.assign({},STATE.traceFilter,{[field]:val}); renderTraceItems(); }
async function loadTracePanel(){   // details 열림 시(ontoggle)·회차 변경 시 호출 — 접혀 있으면 무동작
  const d=$("#trace-details"); if(!d||!d.open) return;
  const el=$("#inspect-trace"); if(!el) return;
  const ch=STATE.activeChapter;
  if(ch==null||!STATE.project){ el.innerHTML='<p class="muted small" style="padding:.6em 0">회차를 고르면 트레이스가 표시됩니다.</p>'; return; }
  const key=STATE.project.id+":"+ch;
  // 조인 원천 중 퇴고 이력(revisions)은 회차 전체 레코드에만 있다 — 요약본 상태로 조립하면 그 축이
  // 통째로 빠진 타임라인이 캐시된다(traceKey 로 1회만 조립). 먼저 채우고 조립한다.
  const c=(await ensureChapter(ch)) || ((STATE.project.chapters)||[]).find(x=>x.chapter===ch);
  if(!(STATE.project && STATE.project.id+":"+STATE.activeChapter===key)) return;   // await 사이 작품·회차 전환
  if(STATE.traceKey!==key){   // 회차 바뀜 → 사이드카 재요청(필터 변경만이면 이 분기 안 탐)
    el.innerHTML='<p class="muted small" style="padding:.6em 0">트레이스 불러오는 중…</p>';
    let doc=null;
    try{ doc=await api.get(`/api/projects/${STATE.project.id}/chapters/${ch}/trace`); }
    catch(e){ doc=null; }   // 404=사이드카 없음(정직)
    STATE.traceItems=_buildTraceItems(doc,c,ch); STATE.traceKey=key; STATE.traceHadShard=(doc!=null);
    STATE.traceFilter={kind:"",actor:"",surface:""};
    // 필터 옵션은 실제 존재하는 값만(distinct)
    const uniq=k=>Array.from(new Set(STATE.traceItems.map(it=>it[k]).filter(Boolean))).sort();
    const opt=(vals,cur)=>['<option value="">전체</option>'].concat(vals.map(v=>`<option value="${esc(v)}"${v===cur?" selected":""}>${esc(v)}</option>`)).join("");
    el.innerHTML=`<div class="inspect-head">${ch}화 생성 트레이스 — 판단·행위·실패·작가 의도 타임라인 <span class="muted small">(읽기 전용·조인)</span></div>`
      + (STATE.traceHadShard?"":`<p class="muted small" style="padding:.2em 0">이 회차 트레이스 사이드카가 없습니다(GA-1 도입 전 생성이거나 기록 미수집). 퇴고·재생성 이력만 표시됩니다.</p>`)
      + `<div class="tr-filters">`
      + `<label class="tiny">kind <select onchange="_trSetFilter('kind',this.value)">${opt(uniq('kind'),'')}</select></label>`
      + `<label class="tiny">actor <select onchange="_trSetFilter('actor',this.value)">${opt(uniq('actor'),'')}</select></label>`
      + `<label class="tiny">surface <select onchange="_trSetFilter('surface',this.value)">${opt(uniq('surface'),'')}</select></label>`
      + `</div><div id="trace-list"></div>`;
  }
  renderTraceItems();
}
// T6: 집필 준비도 advisory — 장르 중립 구조 신호(설정집·추적축·막 분해 등)가 얕으면 작가에게 가시화만(비차단·게이트 아님).
function readinessHtml(){
  const r = STATE.readiness;
  if(!r || r.level!=="thin" || !(r.flags||[]).length) return "";
  return `<div class="readiness"><div class="rd-head">⚠ 집필 준비도 점검 <span class="muted small">참고 — 생성을 막지 않아요</span></div>`
    + r.flags.map(f=>`<div class="rd-flag"><b>${esc(f.message)}</b><div class="muted small">${esc(f.hint||"")}</div></div>`).join("")
    + `</div>`;
}
function renderReader(){
  const p = STATE.project, body = $("#chapter-body");
  const c = (p.chapters||[]).find(x=>x.chapter===STATE.activeChapter);
  // 요약본이면 본문·퇴고 이력·검증을 이 회차만 채운 뒤 다시 그린다(작업실은 회차 하나씩 펼쳐 본다).
  if(c && c.lite && !c._loadFailed){
    body.classList.remove("muted");
    body.innerHTML = `<h3 class="ch-title">${c.chapter}화 · ${esc(c.title||"")}</h3>`
      + `<p class="muted"><span class="spin"></span> 회차를 불러오는 중…</p>`;
    ensureChapter(c.chapter).then(()=>{ if(STATE.activeChapter===c.chapter) renderReader(); });
    return;
  }
  if(!c){
    if(!(p.chapters||[]).length && !p.completed){   // 0회차: 첫 회차 쓰기로 화면 전체가 수렴(첫 여정 막힘 해소)
      body.classList.remove("muted");
      body.innerHTML = `<div class="reader-empty">
        <div class="re-mark">✍︎</div>
        <h3>세계가 준비됐어요. 이제 1화를 써볼까요?</h3>
        <p class="muted">AI가 초고를 쓰고, 설정이 어긋나면 자동으로 점검합니다. 한 화는 보통 1~3분 걸려요.</p>
        ${readinessHtml()}
        <button class="primary re-cta" onclick="generateNext()" ${STATE.generating?"disabled":""}>${STATE.generating?"집필 중…":"1화 쓰기 →"}</button>
      </div>`;
    } else {
      body.classList.remove("muted");
      body.innerHTML = readinessHtml() + `<div class="muted" style="padding:8px 0">회차를 고르거나, 다음 회차를 써보세요.</div>`;
    }
    return;
  }
  body.classList.remove("muted");
  const badge = c.status==="FINALIZED"?'<span class="badge fin">완성</span>':'<span class="badge esc">검토 필요</span>';
  // OV-4: contradiction 을 severity 로 재계층 — 'review'(통제어휘 밖·카디널리티 등, 작가 결정 여지)는 ⚠검토, 'conflict'(논리 불가)는 ✗모순. 필터 아닌 시각 구분(둘 다 표시)
  const oc = (c.ontology_changes||[]).map(o=>{
    const rev = o.op==='contradiction' && o.severity==='review';
    const cls = o.op==='new_entity'?'new' : o.op==='contradiction'?(rev?'rev':'con') : 'chg';
    const mark = o.applied ? "✓" : (rev ? "⚠" : "✗");
    return `<div class="onto-change ${cls}">${mark} ${esc(o.entity)}: ${esc(o.detail)}${o.reason?` <span class="muted">(${esc(o.reason)})</span>`:""}</div>`;
  }).join("");
  const chars = (c.text||"").replace(/\s/g,"").length;   // 공백 제외 글자수(공백·줄바꿈 미포함 — 사용자 지시 2026-08-18)
  // ESCALATED 회복 안내: 무엇이 충돌하고 어떻게 고칠지 자연어로(진행 차단에서 빠져나오는 길)
  const rec = (c.status!=="FINALIZED" && (c.recovery_hints||[]).length)
    ? `<div class="recovery"><div class="rec-head">이 회차는 설정과 충돌해 검토가 필요해요. 이렇게 풀 수 있어요:</div>`
      + c.recovery_hints.map(h=>`<div class="rec-item"><div class="rec-diag">${esc(h.diagnosis||"")}</div>`
        + `<ul class="rec-fix">${(h.fix||[]).map(f=>`<li>${esc(f)}</li>`).join("")}</ul></div>`).join("")
      + `<div class="rec-foot"><button onclick="generateNext()">이대로 다시 생성</button> <span class="muted small">설정을 고친 뒤 다시 생성하면 반영됩니다</span></div></div>`
    : "";
  const readerBlock=readerReactHtml(c.reader_feedback);   // DP-10 시뮬 독자(참고) — advisory·실독자 아님
  // C-5: FINALIZED 회차의 비구속 위반(세계규칙 등)도 작가에게 advisory 로 가시화 — 검출만 되고 사장되던 신호
  const fv=(c.status==="FINALIZED"&&(c.final_violations||[]).length)?c.final_violations:[];
  const violBlock=fv.length?`<div class="reader-react neg"><b>점검</b> — 비구속 위반 ${fv.length}건(차단 안 됨, 참고): ${esc([...new Set(fv.map(v=>v.kind))].join(", "))}</div>`:"";
  // CN-2: 자유형 사실 모순 advisory(과거 회차 프로즈와 대조 — 비차단·참고). 새 진술 ↔ 이전 회차 진술.
  const ca=(c.claim_audit||[]);
  const claimBlock=ca.length?`<div class="reader-react neg"><b>연속성 점검</b> — 과거 회차와 사실 충돌 의심 ${ca.length}건(차단 안 됨, 참고):
    ${ca.map(a=>`<div class="muted small">· “${esc(a.claim)}” ↔ ${a.ref?`${esc(a.ref)}화 `:""}“${esc(a.canon)}”${a.why?` (${esc(a.why)})`:""}</div>`).join("")}</div>`:"";
  // RV-2②: 퇴고로 본문이 바뀌었지만 비용(추가 LLM 콜) 때문에 자동 재계산하지 않은 파생물의 stale 배지.
  //   작가 언어만(용어 §5) — 인물 노트·복선 정리·연속성 점검·독자 반응 예측. 자동 재콜 없음: 다시 생성하면 최신화됨을 고지.
  const _staleMap = c.derivatives_revised_stale||{};
  const _staleKeys = Object.keys(_staleLabels).filter(k=>_staleMap[k]);
  const _staleNames = _staleKeys.map(staleLabel);
  // XR-7③: 항목별 '지금 본문으로 다시 계산' 버튼(작가 발동 — 자동 재계산은 하지 않아요).
  const _recompBtns = _staleKeys.map(k=> k==="promise_ledger"
    ? `<span class="muted tiny">${esc(staleLabel(k))}는 연재 관리의 약속 원장에서 직접 정리해 주세요</span>`
    : k==="wiki"   // XR-10: 위키는 누적 구조라 회차별 재계산 대신 전체 재구축(재구축 전까지 생성 미사용)
    ? `<button class="revise-btn" onclick="rebuildWiki()">인물 노트 전체 재구축</button>`
    : `<button class="revise-btn" onclick="recomputeDerivative(${c.chapter},'${k}')">${esc(staleLabel(k))} 다시 계산</button>`).join(" ");
  const staleBlock = _staleNames.length
    ? `<div class="reader-react neg revised-stale"><b>퇴고 반영 안내</b> — 이 회차를 퇴고해 본문이 바뀌었어요. ${esc(_staleNames.join(", "))}은(는) 퇴고 전 본문 기준이라 지금 본문과 어긋날 수 있어요(비용 때문에 자동으로 다시 계산하지 않습니다). 이 회차를 다시 생성하면 최신 본문으로 갱신됩니다.<div class="stale-acts">${_recompBtns}</div></div>`
    : "";
  // 퇴고 진입(F6) — FINALIZED·ESCALATED 둘 다. 본문 사후 다듬기(사실 불변).
  // B-25: _ephemeral(미저장 검토본)은 서버에 없는 회차 — 서버 대상 액션(퇴고·재생성)은 숨긴다(404/오대상 차단).
  const reviseBtn = (!c._ephemeral && (c.status==="FINALIZED"||c.status==="ESCALATED"))
    ? ` <button class="revise-btn" onclick="openReviseModal(${c.chapter})">퇴고</button>` : "";
  // DE-1 직접 편집 — AI 퇴고와 별개로 작가가 문장을 직접 고쳐 저장(같은 조건: 서버에 저장된 FINALIZED·ESCALATED).
  const editBtn = (!c._ephemeral && (c.status==="FINALIZED"||c.status==="ESCALATED"))
    ? ` <button class="revise-btn edit-btn" onclick="openEditModal(${c.chapter})">직접 편집</button>` : "";
  // 되돌리기 링크 — 채택된(미-reverted) 퇴고가 1건 이상 있을 때만(F4)
  const hasUndo = (c.revisions||[]).some(r=>!r.reverted);
  const undoLink = hasUndo
    ? `<span class="revise-undo-link" ${ACT} onclick="undoRevise(${c.chapter})">마지막 퇴고 되돌리기</span>` : "";
  // 마지막 회차 재생성(반영 전 백업 → 되돌리기) — '마지막 회차'에만(뒤 회차 없어 고아 0)
  const isLast = !((p.chapters||[]).some(x=>x.chapter>c.chapter));
  const regenBtn = (isLast && !c._ephemeral)
    ? ` <button class="regen-btn" onclick="regenerateLast()" ${STATE.generating?"disabled":""}>🔄 이 회차 다시 생성</button>` : "";
  // 점검(연속성·세계규칙·전개)이 있는 마지막 회차에만 — 항목 골라 반영해 재생성
  const _hasReviews = (c.claim_audit||[]).length || (c.status==="FINALIZED"&&(c.final_violations||[]).length) || (c.drift_signals||[]).length;
  const reviewRegenBtn = (isLast && _hasReviews && !c._ephemeral)
    ? ` <button class="regen-btn rr-cta" onclick="openReviewRegenModal()" ${STATE.generating?"disabled":""}>🔧 점검 반영해 다시 생성</button>` : "";
  const regenUndo = (isLast && p.has_regen_backup)
    ? `<span class="revise-undo-link" ${ACT} onclick="restoreLastRegen()">↩ 재생성 되돌리기</span>` : "";
  // EPT-2 연재 업로드 보조 — 제목·본문 클립보드 복사(플랫폼 등록 폼에 붙여넣기용). 서버 액션 아님(클라이언트 전용).
  const copyBtns = (c.text||"").length
    ? ` <button class="revise-btn" onclick="copyChapterTitle(${c.chapter})" title="회차 제목을 클립보드로">제목 복사</button>`
      + ` <button class="revise-btn" onclick="copyChapterText(${c.chapter})" title="본문 전체를 클립보드로">본문 복사</button>` : "";
  // EP-PUB 외부 발행 상태 — 툴은 어디에도 올리지 않는다(작가가 플랫폼에 올린 사실만 기록). 배지 + 표시/재발행/해제.
  const pubState = c.publish_state || ((c.published_at||"").trim() ? "published" : "unpublished");
  const pubBadge = pubState==="published" ? ' <span class="badge pub">발행됨</span>'
                 : pubState==="modified" ? ' <span class="badge pub-mod">발행 후 수정됨</span>' : "";
  const canPublish = (!c._ephemeral && (c.text||"").length && (c.status==="FINALIZED"||c.status==="ESCALATED"));
  const pubBtn = !canPublish ? ""
    : pubState==="unpublished"
      ? ` <button class="revise-btn pub-btn" onclick="markPublished(${c.chapter})" title="이 회차를 문피아·카카오 등에 올린 뒤 눌러 '발행함'으로 표시합니다(툴이 대신 올리지는 않아요)">발행함으로 표시</button>`
      : pubState==="modified"
        ? ` <button class="revise-btn pub-btn warn" onclick="markPublished(${c.chapter})" title="수정본을 플랫폼에 다시 올린 뒤 눌러 발행 지문을 갱신합니다">재발행함으로 표시</button>`
        : "";   // 발행됨(수정 없음) → 버튼 없이 해제 링크만
  const pubUnlink = (pubState!=="unpublished")
    ? `<span class="revise-undo-link" ${ACT} onclick="unmarkPublished(${c.chapter})">발행 해제</span>` : "";
  const pubMeta = (pubState!=="unpublished" && (c.published_at||"").trim())
    ? `${pubState==="modified"?"⚠ 발행 후 수정됨":"✓ 발행됨"} ${esc((c.published_at||"").replace('T',' '))}${c.published_note?` · ${esc(c.published_note)}`:""}`
    : "";
  body.innerHTML = `<h3 class="ch-title">${c.chapter}화 · ${esc(c.title)} ${badge}${pubBadge}${reviseBtn}${editBtn}${regenBtn}${reviewRegenBtn}${copyBtns}${pubBtn}</h3>`+
    `<div class="reader-meta">${chars.toLocaleString()}자${c._ephemeral?` · <span class="muted">미저장 검토본 — 다시 생성하면 새 본문으로 대체돼요</span>`:""}${c.wiki_pages_touched?` · 인물 노트 ${c.wiki_pages_touched}건 갱신`:""}${undoLink?` · ${undoLink}`:""}${regenUndo?` · ${regenUndo}`:""}${pubMeta?` · ${pubMeta}`:""}${pubUnlink?` · ${pubUnlink}`:""}${_rpChipHtml(c)}</div>`+
    rec+readerBlock+violBlock+claimBlock+staleBlock+verificationPanel(c)+
    (oc?`<div style="margin-bottom:1.4em">${oc}</div>`:"")+
    `<div id="rp-panel" class="rp-panel hidden"></div>`+
    (c._loadFailed?`<p class="muted">회차를 불러오지 못했습니다. <button onclick="reloadChapter(${c.chapter})">다시 시도</button></p>`:"")+
    `<div class="md-body">${mdToHtml(c.text||"")}</div>`;
  _rpSetup(c);          // RP-1 빨간펜 — 선택 핸들러 부착 + 유효 마크 밑줄 + 패널 채우기(추가만, 기존 블록 불변)
  renderGenInspect();   // 활성 회차 바뀌면 '생성 정보' 패널 동기화(요소 없으면 무시)
}

function reloadChapter(n){   // 조회 실패한 회차 재시도(_loadFailed 해제 후 다시 채움)
  const c=((STATE.project&&STATE.project.chapters)||[]).find(x=>x.chapter===n);
  if(!c) return;
  delete c._loadFailed;
  ensureChapter(n).then(()=>{ if(STATE.activeChapter===n) renderReader(); });
}

// ---------- 뷰어(몰입형 읽기) — 웹소설 플랫폼처럼 이전·다음 화 ----------
function _hasText(c){ return c.has_text!=null ? !!c.has_text : !!(c.text||"").trim(); }   // 요약본은 has_text, 전체 레코드는 본문으로 판정
function viewerChapters(){
  return (STATE.project.chapters||[]).filter(_hasText).sort((a,b)=>a.chapter-b.chapter);
}
function exportNovel(fmt){   // 회차당 문서 1개를 담은 zip 다운로드(Content-Disposition attachment)
  if(!STATE.project){ return; }
  if(!(STATE.project.chapters||[]).some(_hasText)){ alert("아직 내보낼 회차가 없습니다."); return; }
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
  // 본문 마크다운 렌더 — 제목·강조·구분선·&nbsp; 를 그대로(대사는 dlg 클래스 유지)
  // 읽는 회차의 본문만 그때 받아 온다(뷰어는 이력·검증이 필요 없어 본문 view 로).
  if(c.text==null && !c._loadFailed){
    $("#v-body").innerHTML = '<p class="muted"><span class="spin"></span> 본문을 불러오는 중…</p>';
    const at = c.chapter;
    ensureChapter(at, "text").then(()=>{ const now=viewerChapters()[STATE.viewerIdx];
      if(now && now.chapter===at) renderViewer(); });
    return;
  }
  $("#v-body").innerHTML = c._loadFailed && c.text==null
    ? '<p class="muted">본문을 불러오지 못했습니다.</p>' : mdToHtml(c.text||"");
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
let _genDone = false; // 현재 생성 잡이 종료 처리됐는지 — 연결 끊김(onerror) 시 재접속 vs 마무리 구분
function genStart(){ _genT0 = Date.now(); _genLabel = "집필 준비 중";
  const el = $("#p-genstatus"); if(el) el.classList.remove("hidden");
  if(_genTimer) clearInterval(_genTimer); _genTimer = setInterval(genTick, 1000); genTick(); }
function genTick(){ const el = $("#p-genstatus"); if(!el) return;
  const s = Math.floor((Date.now()-_genT0)/1000);
  el.innerHTML = `<span class="spin"></span> ${esc(_genLabel)} · ${Math.floor(s/60)}:${String(s%60).padStart(2,"0")}`; }
function genStop(){ if(_genTimer){ clearInterval(_genTimer); _genTimer = null; } const el = $("#p-genstatus"); if(el) el.classList.add("hidden"); }

function overviewWrite(autostart){   // 개요 CTA: 이동만 하던 이중클릭 해소 — 1화는 바로 집필 시작, 이어쓰기는 지시 입력 여지 위해 이동만
  goSection('write');
  if(autostart) generateNext();
}
function generateNext(){
  if(STATE.generating) return;
  const pid = STATE.project.id;
  const directive = $("#directive").value.trim();
  STATE.generating = true; _genDone = false;
  $("#gen-btn").disabled = true;
  $("#harness-log").innerHTML = "";
  $("#gen-result").innerHTML = '<span class="spin"></span> 집필 중… <span class="muted small">연결이 끊기거나 새로고침해도 계속 진행돼요. 다시 들어오면 이어서 보여드려요.</span>';
  renderReader();   // 0회차 빈 상태였다면 '집필 중'으로 갱신
  genStart();
  logEvent({node:"harness",event:"connect"},"");
  // 시작-또는-재접속(서버 멱등) — 이미 진행 중이면 같은 잡에 붙어 중복 회차가 안 생긴다.
  const url = `/api/projects/${pid}/generate?directive=${encodeURIComponent(directive)}`;
  _genES = new EventSource(url); _wireGenES(_genES, pid);
}

// 마지막 회차 재생성 — 반영 전 백업(되돌리기 가능) 후 그 회차를 다시 집필. 기존 생성 SSE 흐름 재사용.
function regenerateLast(){
  if(STATE.generating) return;
  if(!confirm("마지막 회차를 다시 생성할까요?\n지금 내용은 자동 백업되어 '↩ 재생성 되돌리기'로 복구할 수 있어요.\n(현재 설정·지시·스킬 그대로, 수 분 걸려요)")) return;
  const pid = STATE.project.id;
  STATE.generating = true; _genDone = false;
  const gb=$("#gen-btn"); if(gb) gb.disabled = true;
  const hl=$("#harness-log"); if(hl) hl.innerHTML = "";
  const gr=$("#gen-result"); if(gr) gr.innerHTML = '<span class="spin"></span> 마지막 회차 다시 집필 중… <span class="muted small">지금 내용은 백업해뒀어요.</span>';
  genStart();
  logEvent({node:"harness",event:"connect"},"");
  _genES = new EventSource(`/api/projects/${pid}/chapters/regenerate-last`); _wireGenES(_genES, pid);
}

// 마지막 재생성 되돌리기 — 반영 전 백업으로 복원.
async function restoreLastRegen(){
  if(STATE.generating) return;
  if(!confirm("마지막 재생성을 되돌리고 이전 회차로 복구할까요?")) return;
  try{
    const r = await api.post(`/api/projects/${STATE.project.id}/chapters/restore-last-regen`, {});
    STATE.project = await fetchProject(STATE.project.id);
    STATE.activeChapter = r.current_chapter || STATE.activeChapter;
    renderChapters(); renderReader();
  }catch(e){ alert("되돌리기 실패: "+(e.message||e)); }
}

// 점검(연속성·세계규칙·전개) 반영 재생성 — 항목 선택 → 1회성 교정 지시로 마지막 회차 재생성
function openReviewRegenModal(){
  if(STATE.generating) return;
  const p = STATE.project;
  const c = (p.chapters||[]).slice().sort((a,b)=>b.chapter-a.chapter)[0];
  if(!c) return;
  const ca = c.claim_audit||[];
  const fv = (c.status==="FINALIZED") ? (c.final_violations||[]) : [];
  const ds = c.drift_signals||[];
  const chk = (fix, main, sub) => `<label class="rr-item">
    <input type="checkbox" class="rr-chk" data-fix="${esc(fix)}" checked />
    <span><div class="rr-main">${main}</div>${sub?`<div class="muted tiny">${sub}</div>`:""}</span></label>`;
  const group = (title, items) => items.length ? `<div class="rr-group">
    <div class="rr-ghead">${title} <span class="muted small">(${items.length})</span>
    <button type="button" class="rr-all" onclick="rrToggleGroup(this)">모두</button></div>${items.join("")}</div>` : "";
  // XR-7①: 이 회차를 퇴고했다면 연속성 점검 결과는 '퇴고 전 본문' 기준이다 — 선택 전에 배지로 알린다(차단 없음).
  const _caStale = (c.derivatives_revised_stale||{}).claim_audit
    ? ` <span class="stale-badge">퇴고 전 본문 기준</span>` : "";
  const g1 = group("연속성 충돌 (과거 회차와)" + _caStale, ca.map(a=>chk(
    `확정: ${a.canon}${a.ref?` (${a.ref}화 기준)`:""} — "${a.claim}"처럼 어긋나지 않게 써라.`,
    `"${esc(a.claim||"")}" ↔ ${a.ref?esc(a.ref)+"화 ":""}"${esc(a.canon||"")}"`, a.why?`지킬 캐논: ${esc(a.canon||"")}`:"")));
  const g2 = group("세계규칙·설정 위반", fv.map(v=>chk(
    `${v.entity?v.entity+" ":""}확정 설정 준수: ${v.canon||v.kind}. 어기지 마라.`,
    `${esc(v.kind||"위반")}${v.entity?" · "+esc(v.entity):""}`, v.canon?`확정: ${esc(v.canon)}`:"")));
  const g3 = group("전개 점검", ds.map(d=>chk(
    `전개 점검: ${d}. 같은 정체·반복 피하고 한 걸음 전진.`, esc(d), "")));
  if(!g1 && !g2 && !g3){ alert("이 회차엔 반영할 점검 항목이 없어요."); return; }
  openModal(`<h3>🔧 점검 반영해 다시 생성 · ${c.chapter}화</h3>
    <p class="muted small">고칠 항목을 고르면 그 '사실'을 지켜 회차를 새로 씁니다. (수 분 · 백업→되돌리기 가능)</p>
    <div class="rr-body">${g1}${g2}${g3}</div>
    <div class="rr-extra"><label class="muted small">추가 지시(선택)</label>
      <input type="text" id="rr-extra" placeholder="예: 전투를 더 짧고 빠르게" /></div>
    <div class="rr-actions"><button data-close>취소</button>
      <button class="primary" onclick="submitReviewRegen(this)">선택 반영해 다시 생성 →</button></div>`);
}
function rrToggleGroup(btn){
  const g = btn.closest(".rr-group"); const boxes=[...g.querySelectorAll(".rr-chk")];
  const allOn = boxes.every(b=>b.checked); boxes.forEach(b=>b.checked=!allOn);
}
function submitReviewRegen(btn){
  const ov = btn.closest(".modal-overlay");
  const fixes = [...ov.querySelectorAll(".rr-chk:checked")].map(b=>b.dataset.fix).filter(Boolean);
  const extra = (ov.querySelector("#rr-extra")?.value||"").trim();
  if(extra) fixes.push(extra);
  if(!fixes.length){ alert("반영할 항목을 하나 이상 고르세요."); return; }
  const fix = "[이전 생성의 점검 사항을 반영해 이번 회차는 다음을 지켜라]\n" + fixes.map((f,i)=>`${i+1}) ${f}`).join("\n");
  closeModal(ov);
  regenerateLastWithFix(fix);
}
function regenerateLastWithFix(fix){
  if(STATE.generating) return;
  const pid = STATE.project.id;
  STATE.generating = true; _genDone = false;
  const gb=$("#gen-btn"); if(gb) gb.disabled = true;
  const hl=$("#harness-log"); if(hl) hl.innerHTML = "";
  const gr=$("#gen-result"); if(gr) gr.innerHTML = '<span class="spin"></span> 점검 반영해 다시 집필 중… <span class="muted small">선택한 사실을 지켜 새로 뽑는 중이에요.</span>';
  genStart();
  logEvent({node:"harness",event:"connect"},"");
  _genES = new EventSource(`/api/projects/${pid}/chapters/regenerate-last?fix=${encodeURIComponent(fix)}`); _wireGenES(_genES, pid);
}

// SSE 핸들러 배선 — 생성 시작(/generate)과 재접속(/generation/stream)이 공유. pid 를 캡처해 콜백에 전달(작품 전환 보호).
function _wireGenES(es, pid){
  es.addEventListener("start", e=>{ const d=JSON.parse(e.data); _genLabel = `${d.chapter}화 집필 중`; logEvent({node:"plan_chapter",event:`${d.chapter}화 시작`},"",true);
    if(d.note){ logEvent({node:"harness",event:"안내"}, d.note, true); const gr=$("#gen-result"); if(gr) gr.innerHTML += `<div class="muted small" style="margin-top:.4em">${esc(d.note)}</div>`; } });  // 다른 탭 진행 잡에 합류 안내
  es.addEventListener("event", e=> logEvent(JSON.parse(e.data)));
  es.addEventListener("complete", e=>{ _genDone=true; es.close(); if(es===_genES)_genES=null; onComplete(JSON.parse(e.data), pid); });
  es.addEventListener("failed", e=>{ _genDone=true; es.close(); if(es===_genES)_genES=null; onFail(JSON.parse(e.data), pid); });
  es.addEventListener("idle", e=>{ _genDone=true; es.close(); if(es===_genES)_genES=null; finalizeQuietly(pid); });  // 잡 없음/만료 → 조용히 마무리
  es.onerror = ()=>{ if(_genDone) return;     // 이미 종료 처리됐으면 무시(close 직후 onerror 노이즈)
    es.close(); if(es===_genES)_genES=null;
    // 연결 끊김 — 생성은 서버에서 계속될 수 있다. 잠시 후 상태 확인 후 재접속/마무리.
    if(STATE.project && STATE.project.id===pid && STATE.generating) setTimeout(()=>genReattach(pid, 0), 1500);
  };
}

// 끊긴 뒤 상태를 확인하고: 진행 중이면 다시 붙고, 끝났으면 마무리한다. attempt: 연속 상태조회 실패 횟수(상한·백오프).
const _GEN_REATTACH_MAX = 40;   // 누적 상한(오프라인 무한폴링 차단) — 초과 시 마무리로 수렴
function genReattach(pid, attempt){
  attempt = attempt || 0;
  if(!(STATE.project && STATE.project.id===pid && STATE.generating) || _genDone) return;
  api.get(`/api/projects/${pid}/generation`).then(st=>{
    if(!(STATE.project && STATE.project.id===pid && STATE.generating) || _genDone) return;   // 그 사이 전환/종료
    if(st.status==="running"){
      _genES = new EventSource(`/api/projects/${pid}/generation/stream`); _wireGenES(_genES, pid);   // 재연결 성공 → 카운터는 새 onerror 가 0으로
    } else if(st.status==="done" && st.result){ _genDone=true; onComplete(st.result, pid); }
    else if(st.status==="failed"){ _genDone=true; onFail(st.error||{message:"연결이 끊겼습니다"}, pid); }
    else { _genDone=true; finalizeQuietly(pid); }   // idle / 결과 만료 → 프로젝트 새로고침으로 마무리
  }).catch(()=>{   // 상태 확인 실패(오프라인·서버 일시 불가) — 상한까지 백오프 재시도, 초과 시 마무리로 수렴
    if(!(STATE.project && STATE.project.id===pid && STATE.generating) || _genDone) return;
    if(attempt+1 >= _GEN_REATTACH_MAX){ finalizeQuietly(pid); return; }   // 무한 폴링 방지 — 화면을 풀고 디스크 상태로 정리
    setTimeout(()=>genReattach(pid, attempt+1), Math.min(1500 + attempt*1500, 15000));   // 1.5s→…→최대 15s 백오프
  });
}

// 페이지 로드/작품 열기 시 — 진행 중인 생성이 있으면 자동으로 다시 연결한다.
async function resumeGenerationIfRunning(pid){
  let st;
  try{ st = await api.get(`/api/projects/${pid}/generation`); }catch(e){ return; }
  if(!(STATE.project && STATE.project.id===pid) || STATE.generating) return;   // 그 사이 전환됐거나 이미 진행 중 표시
  if(st.status!=="running"){
    // B-25: 비FINALIZED(검토 필요) 결과는 디스크에 저장되지 않는다(회차 append 와 커서 전진은 함께 커밋/함께 롤백).
    // 생성 중 탭을 닫았다가 완료 후 재접속한 경우에도 회복 안내·검토본을 가시화한다(조용한 정지 불가) —
    // genReattach 의 done 처리와 대칭. FINALIZED 완료분은 이미 본문에 반영돼 정상 렌더되므로 그대로 둔다.
    const rec = (st.status==="done" && st.result && !st.result.completed) ? st.result.record : null;
    if(rec && rec.status!=="FINALIZED" && !((STATE.project.chapters||[]).some(x=>x.chapter===rec.chapter))){
      _genDone = true; onComplete(st.result, pid);
    }
    return;   // idle/failed/FINALIZED-done — 라이브(running)만 아래서 재연결
  }
  STATE.generating = true; _genDone = false;
  $("#gen-btn").disabled = true;
  _genLabel = `${st.chapter}화 집필 중`;
  $("#gen-result").innerHTML = '<span class="spin"></span> 집필 중… <span class="muted small">진행 중이던 생성에 다시 연결했어요.</span>';
  renderReader(); genStart();
  _genES = new EventSource(`/api/projects/${pid}/generation/stream`); _wireGenES(_genES, pid);
}

// 생성이 (백그라운드에서) 끝났으나 표시할 즉석 결과가 없을 때 — 프로젝트를 다시 읽어 화면만 최신화.
async function finalizeQuietly(pid){
  STATE.generating = false; genStop(); _genES = null; _genDone = true;
  if(!(STATE.project && STATE.project.id===pid)) return;
  $("#gen-btn").disabled = false; $("#gen-result").innerHTML = "";
  const fresh = await fetchProject(pid);
  if(!(STATE.project && STATE.project.id===pid)) return;   // await 사이 작품 전환 → 남의 화면 덮어쓰기 금지(TOCTOU)
  STATE.project = fresh;
  STATE.readiness = await api.get(`/api/projects/${pid}/readiness`).catch(()=>null);   // T6: 설정집·막 성장 반영
  if(!(STATE.project && STATE.project.id===pid)) return;
  const last = STATE.project.current_chapter;
  if(last){ STATE.activeChapter = last; STATE.chapterPage = chPageOf(last); }
  renderHeader(); renderChapters(); renderReader(); renderGenInspect();
  refreshSection(STATE.section);
}
// 상태색: 실패/검토=red, 경고=amber, 완료성=green. (이벤트 코드 자체는 화면에 안 나오고 색 분류에만 사용)
const EV_BAD = new Set(["escalation","non_convergence","failed","parse_failure","wiki_failure","tense_fix_failed","spine_gen_failed"]);
const EV_WARN = new Set(["story_truncated","bible_truncated","violations","tics_residual","reformat_rejected",
  "signal","episode_stuck","plant_backlog","uncast_character","ssot_contradiction","under_norm","spine_incomplete",
  "contract_unsettled","compile_failed"]);
const EV_OK = new Set(["done","new_entity","relation","registered","debut","bible_done","spine_done","world_done",
  "payoff_detected","reconciled","retrospective_available","contract_settled","compiled"]);
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
    // XR-7: 퇴고 반영 점검(가시화만 — 생성을 막지 않아요)
    case "stale_consumed": return ["퇴고 반영 안 된 자료", `${(ev.items||[]).length}개 회차 — 생성은 그대로 진행돼요`];
    case "ledger_quote_stale": return ["옛 대사 정리 제외",
      `${ev.source_chapter}화 대사 정리가 지금 본문과 달라 이번 화 참고에서 뺐어요`];
    case "recomputed": return ["다시 계산 완료", staleLabel(ev.name)];
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
    case "window": return ["연재 페이싱", `훅 단조 ${ev.hook_monotony}${ev.hook_max_run!=null?` (연속 ${ev.hook_max_run}화)`:''}${ev.place_max_run!=null?` · 장소 연속 ${ev.place_max_run}화`:''} · 새 명사 ${ev.new_names}개`];
    case "prediction": {   // DP-10 시뮬 독자(참고) — 이진 결제 배지 제거, kill_trigger/잔존 전면
      if(ev.kill_trigger!=null||ev.hate_comment!=null||ev.retention_est!=null){
        const rt=ev.retention_est!=null?`잔존 추정 ${ev.retention_est}% · `:"";
        const kt=(ev.kill_trigger&&ev.kill_trigger!=="없음")?`손절 유발: ${ev.kill_trigger}`:"뚜렷한 손절 지점 없음";
        return ["시뮬 독자(참고)", `${rt}${kt}`];
      }
      return ["시뮬 독자(참고)", `${ev.got?`얻은 것: ${ev.got}`:""}${ev.why?` · ${ev.why}`:""}`];   // 구 이벤트 하위호환
    }
    // EC-1 엔딩 계약(advisory — 상태 기준 감시, 아무것도 막지 않음)
    case "contract_eval": return ["엔딩 계약", `확정 기준 ${ev.gt_satisfied}/${ev.total} 충족${ev.promotion_hints?` · 승격 후보 ${ev.promotion_hints}`:""}`];
    case "contract_unsettled": return ["미정산 엔딩", `확정 기준 미정산 ${ev.unsettled_gt}건 — 연재 관리에서 확인(완결은 막지 않아요)`];
    case "contract_settled": return ["엔딩 정산", "확정 기준 전 항목 충족"];
    case "compiled": return ["엔딩 계약 갱신", `감시 술어 ${ev.predicates}개${ev.unexpressed?` · 미표현 ${ev.unexpressed}건`:""}`];
    case "compile_failed": return ["엔딩 계약 갱신 실패", "기존 계약 유지(참고)"];
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
async function onComplete(data, pid){
  pid = pid || (STATE.project && STATE.project.id);
  if(!(STATE.project && STATE.project.id===pid)) return;   // 이미 다른 작품 화면 — 남의 화면 덮어쓰기 금지
  STATE.generating = false; genStop(); _genES = null;
  if(!($("#dir-keep") && $("#dir-keep").checked)) $("#directive").value="";   // '계속 적용' 체크 시 지시 유지(아크 표준 제약)
  if(data.completed){   // 엔딩 도달 → 완결
    $("#gen-btn").disabled = true; $("#gen-btn").textContent = "완결되었습니다";
    $("#gen-result").innerHTML = `작품이 완결되었습니다 · ${data.current_chapter}화`;
    const fresh = await fetchProject(pid);
    if(!(STATE.project && STATE.project.id===pid)) return;   // await 사이 작품 전환 → 중단(TOCTOU)
    STATE.project = fresh;
    STATE.activeChapter = data.current_chapter; STATE.chapterPage = chPageOf(data.current_chapter);
    renderHeader(); renderChapters(); renderReader(); refreshSection(STATE.section);
    return;
  }
  $("#gen-btn").disabled = false;
  const r = data.record;
  const badge = r.status==="FINALIZED"?'<span class="badge fin">완성</span>':'<span class="badge esc">검토 필요</span>';
  const fail = data.failures&&data.failures.length?` · 주의 ${data.failures.length}건`:"";
  const drift = r.drift_signals&&r.drift_signals.length?` · 전개 점검 ${r.drift_signals.length}건`:"";
  // DP-10 시뮬 독자(참고 — advisory·작가 가시화·실독자 아님)
  const readerBlock=readerReactHtml(r.reader_feedback);
  // G3: 아크 완결 시 회고 권유(nudge — 작가가 받을지 결정)
  const retroNudge=(data.events||[]).some(e=>e.event==="retrospective_available")
    ? `<div class="retro-nudge">📋 아크가 끝났어요 — <a ${ACT} onclick="openRetro()">연재 관리에서 회고 받기</a></div>`:"";
  $("#gen-result").innerHTML = `${r.chapter}화 ${badge} · AI 사용량 +${data.usage_delta.chat_calls}회${fail}${drift}${retroNudge}${readerBlock}`;
  // 상태 갱신
  const fresh = await fetchProject(pid);
  if(!(STATE.project && STATE.project.id===pid)) return;   // await 사이 작품 전환 → 중단(TOCTOU)
  STATE.project = fresh;
  // 방금 생성한 회차는 이 페이로드가 곧 전체 레코드다 — 요약본 자리에 그대로 얹어 재조회를 아낀다.
  const _ri = (fresh.chapters||[]).findIndex(x=>x.chapter===r.chapter);
  if(_ri>=0) fresh.chapters[_ri] = r;
  // B-25: 비FINALIZED(검토 필요) 회차는 서버에 저장되지 않는다(회차 append 와 커서 전진은 함께 커밋/함께 롤백).
  // 회복 안내·본문 검토 가시화는 유지해야 하므로, 이번 화면에만 페이로드 레코드를 겹쳐 보여준다(미저장 — 새로고침 시 소멸).
  if(r.status!=="FINALIZED" && !((fresh.chapters||[]).some(x=>x.chapter===r.chapter))){
    r._ephemeral = true;
    fresh.chapters = (fresh.chapters||[]).concat([r]).sort((a,b)=>a.chapter-b.chapter);
  }
  STATE.activeChapter = r.chapter;
  STATE.chapterPage = chPageOf(r.chapter);
  renderHeader(); renderChapters(); renderReader(); renderGenInspect();
  if(STATE.section==="write"||STATE.section===undefined) setHashSilent(`#/p/${pid}/ch/${r.chapter}`);   // 집필 중이면 새 회차를 URL에
  else refreshSection(STATE.section);   // 다른 섹션을 보고 있었다면 그 섹션도 최신화
}
function onFail(data, pid){
  pid = pid || (STATE.project && STATE.project.id);
  if(!(STATE.project && STATE.project.id===pid)) return;   // 다른 작품 화면이면 건드리지 않음
  STATE.generating = false; genStop(); _genES = null; $("#gen-btn").disabled = false;
  $("#gen-result").innerHTML = `회차를 쓰지 못했습니다: ${esc(data.message||"")} <button class="primary" onclick="generateNext()">다시 시도</button>`;
  logEvent({node:"harness",event:"실패"}, data.message||"", true);
  renderReader();
}

// ---------- 인스펙터 ----------
// XR-5 자동 확정 기준 — 회차 본문에서 감지된 값이 바로 '공식 설정'이 될지, 작가 확정을 기다릴지의 축별 선언.
//   참고 패널(무강제): 자동으로 바꾸는 것은 없고, 지금 무엇이 기계 판단으로 박혔는지 보여 주기만 한다.
const ATTR_TIER = {
  "": {label:"기본", hint:"지금까지 하던 대로 확정합니다."},
  "binding": {label:"바로 확정", hint:"장면에서 눈으로 확인되는 사실(소지·위치·생사·소속 같은 것)."},
  "non_binding": {label:"작가 확정 대기", hint:"본문만으로는 단정하기 어려운 축(마음·앎·자각 같은 것)."},
};
function attrTierHtml(tr){
  const rows = (tr && tr.attributes || []).filter(a=>a.declared);
  if(!rows.length) return "";
  const opt = (cur)=>Object.keys(ATTR_TIER).map(k=>
    `<option value="${k}"${k===cur?" selected":""}>${esc(ATTR_TIER[k].label)}</option>`).join("");
  const body = rows.map(a=>{
    const cur = ATTR_TIER[a.auto_commit] ? a.auto_commit : "";
    const samples = (a.samples||[]).map(s=>`${esc(s.entity)}: ${esc(s.value)} (${s.eff_from}화부터)`).join(" · ");
    const pending = a.non_binding ? `<span class="prov">확정 대기 ${a.non_binding}건</span>` : "";
    // XR-19: 기계 확정 전량 검토 큐 — 항목별 판정 기록(기록일 뿐 값은 안 바뀜·정정은 공식 설정에서).
    //   내면(작가 확정 대기 선언) 축이 1차 검토 대상(review_priority) — 배지로 표시.
    const prio = a.review_priority ? `<span class="prov">검토 우선</span>` : "";
    const revOpt = (cur)=>["","approve","dismiss","hold"].map(v=>{
      const lbl = {"":"미판정","approve":"승인(맞음)","dismiss":"기각(오추출)","hold":"보류"}[v];
      return `<option value="${v}"${v===cur?" selected":""}>${lbl}</option>`;}).join("");
    const entries = (a.entries||[]).length
      ? `<div class="kv small">${a.entries.map(e=>
          `<div class="rule-item">⏱ ${esc(e.entity)} · ${esc(String(e.value))} (${e.eff_from}화부터, ${esc(e.reason||"")})
             <select onchange="recordTierReview('${esc(e.entity_id)}','${esc(a.attr)}',${e.eff_from},this.value)">${revOpt(e.review||"")}</select></div>`).join("")}</div>` : "";
    const machine = a.machine_binding
      ? `<div class="muted small">본문 감지로 확정된 값 ${a.machine_binding}건${samples?` — ${samples}`:""}</div>${entries}` : "";
    // XR-11(007 §5): 티어(확정 기준)와 노출(누가 보는가)은 독립 축 — 한 행에서 함께 검토(노출은 표시만).
    const expo = a.exposure ? `<span class="muted small">노출: ${a.exposure==="internal"?"내부(집필에 비노출)":"공개"}</span>` : "";
    return `<div class="ent"><span class="en-name">${esc(a.label||a.attr)}</span>
      <span class="muted small">${esc(a.attr)}</span>${pending}${prio} ${expo}
      <div class="kv"><label class="muted small">감지된 값 처리:
        <select data-attr="${esc(a.attr)}" onchange="setAttrTier(this.dataset.attr, this.value)">${opt(cur)}</select></label>
        <span class="muted small">${esc(ATTR_TIER[cur].hint)}</span></div>
      ${machine}</div>`;
  }).join("");
  return `<h4 style="margin-top:1em">자동 확정 기준 <span class="muted small">— 참고. 지금 정해도 이미 확정된 값은 그대로고, 다음 회차부터 적용돼요.</span></h4>${body}`;
}
async function setAttrTier(key, value){
  if(!STATE.project) return;
  try{
    await api.patch(`/api/projects/${STATE.project.id}/attributes/${encodeURIComponent(key)}`, {auto_commit:value});
    toast("자동 확정 기준을 바꿨습니다(다음 회차부터 적용)", "ok");
    loadOntology();
  }catch(e){ toast("바꾸지 못했습니다: "+e.message, "bad"); }
}
// XR-19/25: 기계 확정 값 1건의 작가 판정 기록 — 기록일 뿐 값은 바뀌지 않아요(정정은 공식 설정의 상태 확정으로).
//   빈 선택=판정 취소(clear). 기각(dismiss)은 '정정 필요' 상태 — 실제 값 정정까지 별도 안내.
async function recordTierReview(entityId, attr, effFrom, decision){
  if(!STATE.project) return;
  try{
    await api.post(`/api/projects/${STATE.project.id}/tier-review`,
                   {entity_id: entityId, attr, eff_from: effFrom, decision: decision || "clear"});
    if(decision === "dismiss"){
      toast("기각을 기록했습니다 — 값 자체는 아직 그대로예요. 공식 설정 탭의 상태 확정으로 맞는 값을 박아야 정정이 끝납니다.", "ok");
    }else{
      toast(decision ? "판정을 기록했습니다(값은 바뀌지 않아요)" : "판정을 취소했습니다(미판정으로 되돌림)", "ok");
    }
  }catch(e){ toast("기록하지 못했습니다: "+e.message, "bad"); }
}
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
    // XR-5: 티어 선언 현황은 준비도 응답의 참고 키(tier_report) — 실패해도 공식 설정 화면은 그대로 뜬다(비차단)
    const rd = await api.get(`/api/projects/${STATE.project.id}/readiness`).catch(()=>null);
    el.innerHTML = `<div class="inspect-head">현재 ${o.as_of_chapter}화 기준 · 작가가 확정한 공식 설정입니다.</div>
      ${chars}
      <h4 style="margin-top:1em">세계 규칙</h4>${o.rules.map(r=>`<div class="rule-item">⚖️ ${esc(r)}</div>`).join("")||'<span class="muted">없음</span>'}
      <h4 style="margin-top:1em">예정된 변화</h4>${tl||'<span class="muted">없음</span>'}
      ${attrTierHtml(rd && rd.tier_report)}`;
  }catch(e){ el.innerHTML = `<span class="muted">불러오지 못했습니다: ${esc(e.message)}</span>`; }
}
// 작품 노트 — 회차가 쌓이면 페이지 수가 계속 늘어난다. 묶음으로 받아 이어 붙인다(자동 점검은 전량 — 작품 전체 진단).
const WIKI_PAGE = 40;
let WIKI = {pages:[], total:0, has_more:false, watermark:0, lint:[]};
async function loadWiki(){
  WIKI = {pages:[], total:0, has_more:false, watermark:0, lint:[]};
  const el = $("#inspect-wiki");
  if(el) el.innerHTML = '<p class="muted"><span class="spin"></span> 작품 노트를 불러오는 중…</p>';
  await loadMoreWiki();
}
async function loadMoreWiki(){
  const el = $("#inspect-wiki"); if(!el || !STATE.project) return;
  const pid = STATE.project.id;
  try{
    const w = await api.get(`/api/projects/${pid}/wiki?offset=${WIKI.pages.length}&limit=${WIKI_PAGE}`);
    if(!(STATE.project && STATE.project.id===pid)) return;   // await 사이 작품 전환(TOCTOU)
    WIKI.pages = WIKI.pages.concat(w.pages||[]);
    WIKI.total = w.total!=null ? w.total : WIKI.pages.length;
    WIKI.has_more = !!w.has_more;
    WIKI.watermark = w.watermark; WIKI.lint = w.lint||[];
    renderWiki();
  }catch(e){ el.innerHTML = `<span class="muted">불러오지 못했습니다: ${esc(e.message)}</span>`; }
}
function renderWiki(){
  const el = $("#inspect-wiki"); if(!el) return;
  const LIFE = {ACTIVE:"활성", DRAFT:"초안", ARCHIVED:"보관"};
  const shown = WIKI.pages.filter(p=>p.body);
  const pages = shown.map(p=>`<div class="ent"><span class="en-name">${esc(p.page_id)}</span>
    <span class="prov">${esc(LIFE[p.lifecycle]||p.lifecycle)}</span>
    <div class="small" style="margin-top:.4em">${esc(p.body)}</div></div>`).join("");
  const lint = WIKI.lint.map(l=>`<div class="lint-item">⚠️ ${esc(l.entity)} — ${esc(l.text)}</div>`).join("")||'<span class="muted small">점검 결과 이상 없음</span>';
  const more = WIKI.has_more
    ? `<div class="load-more"><button onclick="loadMoreWiki()">더 보기</button>
       <span class="muted small">${WIKI.pages.length} / ${WIKI.total}개</span></div>` : "";
  el.innerHTML = `<div class="inspect-head">현재 ${WIKI.watermark}화 기준 · 회차마다 자동으로 정리되는 인물·세계 노트입니다.</div>
    <h4>자동 점검</h4>${lint}
    <h4 style="margin-top:1em">노트 (${shown.length}${WIKI.has_more?` / ${WIKI.total}`:""})</h4>${pages||'<span class="muted">없음</span>'}${more}`;
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
// DP-10 시뮬 독자(참고) 렌더 — 정직화: 이진 결제 배지 제거·kill_trigger/악플 전면·"실독자 아님" 주석.
//   신규 스키마{drop,kill_trigger,hate_comment,retention_est,why} 우선, 구 데이터{got,pay_next,why}는 하위호환 분기.
//   ch 지정 시 'N화 —' 접두(연재 관리 신호 목록용). 렌더할 내용 없으면 "".
function readerReactHtml(rf, ch){
  if(!rf) return "";
  const chp = ch!=null?`<b>${ch}화</b> · `:"";
  const honest = `<span class="muted tiny">실독자 아님 — LLM 시뮬</span>`;
  const isNew = ('kill_trigger' in rf)||('hate_comment' in rf)||('retention_est' in rf)||('drop' in rf);
  if(isNew){
    const ret = (rf.retention_est!=null&&rf.retention_est!=="")?`<span class="pill">잔존 추정 ${esc(String(rf.retention_est))}%</span>`:"";
    const kt = (rf.kill_trigger&&rf.kill_trigger!=="없음")
      ? `<div class="kill-trigger"><b>손절 유발</b> — “${esc(rf.kill_trigger)}”</div>`
      : (rf.kill_trigger==="없음"?`<div class="muted small">뚜렷한 손절 지점: 없음</div>`:"");
    const hc = rf.hate_comment?`<div class="hate-comment">💬 ${esc(rf.hate_comment)}</div>`:"";
    const wy = rf.why?`<div class="muted small">${esc(rf.why)}</div>`:"";
    if(!(ret||kt||hc||wy)) return "";
    return `<div class="reader-react sim">${chp}<b>시뮬 독자(참고)</b> ${honest} ${ret}${kt}${hc}${wy}</div>`;
  }
  // 구 스키마(got/pay_next/why) — 하위호환: 이진 배지 없이 데이터만 보존, 정직 라벨로 감쌈.
  if(rf.why||rf.got){
    const legacy = rf.pay_next!=null?`<span class="muted tiny">· 구 신호: ${rf.pay_next?'결제 의향':'이탈 위험'}</span>`:"";
    return `<div class="reader-react sim">${chp}<b>시뮬 독자(참고)</b> ${honest} ${legacy}`
      +`${rf.got?`<div class="muted small">얻은 것: ${esc(rf.got)}</div>`:''}`
      +`${rf.why?`<div class="muted small">${esc(rf.why)}</div>`:''}</div>`;
  }
  return "";
}
// 본문 마크다운 렌더 — 모델이 내는 제목(#)·강조(**/*)·구분선(---)·&nbsp; 를 *그대로 렌더*(스트리핑 두더지잡기 금지).
// XSS 안전: < > 와 떠도는 & 만 이스케이프하고 유효 HTML 엔티티(&nbsp; 등)는 보존 → &nbsp; 가 비분리공백으로 렌더.
function _mdEsc(s){ return String(s==null?"":s)
  .replace(/&(?!(?:[a-zA-Z][a-zA-Z0-9]*|#\d+|#x[0-9a-fA-F]+);)/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
function _mdInline(s){ return s
  .replace(/—{2,}/g,"—")   // ——/——— → — : 줄표 런 문체 틱을 '한 개로 렌더'(&nbsp;→공백과 같은 계열, 삭제 아님)
  .replace(/\*\*([^*\n]+?)\*\*/g,"<strong>$1</strong>")
  .replace(/(^|[^*])\*([^*\n]+?)\*(?!\*)/g,"$1<em>$2</em>"); }
function mdToHtml(text){
  const lines = _mdEsc(text).split(/\n/); const out=[]; let para=[];
  const flush=()=>{ if(para.length){ const dlg=/^["“]/.test(para[0].trim())?' class="dlg"':''; out.push(`<p${dlg}>${para.join("<br>")}</p>`); para=[]; } };
  for(const raw of lines){ const s=raw.trim();
    if(!s){ flush(); continue; }
    let m;
    if((m=s.match(/^(#{1,6})\s+(.*)$/))){ flush(); const lv=Math.min(m[1].length,6); out.push(`<h${lv}>${_mdInline(m[2])}</h${lv}>`); continue; }
    if(/^([-*_]\s*){3,}$/.test(s)){ flush(); out.push("<hr>"); continue; }   // --- *** ___ → 장면 구분선
    para.push(_mdInline(s)); }
  flush(); return out.join("");
}
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

// EPT-2: 회차 제목/본문 클립보드 복사 — 연재 플랫폼 등록 폼 붙여넣기용. navigator.clipboard 우선,
//   비보안 컨텍스트(http) 폴백은 임시 textarea+execCommand(구식이지만 localhost 밖 http 접속 대비).
function _copyToClipboard(text, label){
  const done=()=>toast(`${label} 복사됨 (${text.length.toLocaleString()}자)`, "");
  const fail=()=>toast(`${label} 복사 실패 — 브라우저 권한을 확인하세요`, "bad");
  if(navigator.clipboard && window.isSecureContext){
    navigator.clipboard.writeText(text).then(done, ()=>{ _copyFallback(text)?done():fail(); });
  } else { _copyFallback(text)?done():fail(); }
}
function _copyFallback(text){
  const ta=document.createElement("textarea"); ta.value=text;
  ta.style.position="fixed"; ta.style.opacity="0"; document.body.appendChild(ta);
  ta.select(); let ok=false; try{ ok=document.execCommand("copy"); }catch(e){ ok=false; }
  document.body.removeChild(ta); return ok;
}
function copyChapterTitle(n){
  const c=(STATE.project.chapters||[]).find(x=>x.chapter===n); if(!c) return;
  _copyToClipboard(c.title||`${n}화`, `${n}화 제목`);
}
function copyChapterText(n){
  const c=(STATE.project.chapters||[]).find(x=>x.chapter===n); if(!c||!c.text) return;
  _copyToClipboard(c.text, `${n}화 본문`);
}

// ---------- EP-PUB: 외부 플랫폼 수동 발행 표시(툴은 업로드하지 않음 — 발행 사실+지문만 기록) ----------
// 작가가 문피아·카카오 등에 회차를 올린 뒤 '발행함으로 표시'를 누르면 그 순간 본문 지문·시각을 기록한다.
// 이후 퇴고·편집·재실현·재생성으로 본문이 바뀌면 서버가 '발행 후 수정됨'으로 파생 판정 → 재발행 버튼이 뜬다.
function _applyPublish(n, r){   // 응답으로 인메모리 회차의 발행 필드 갱신 후 목차·본문 다시 그림
  const c = ((STATE.project&&STATE.project.chapters)||[]).find(x=>x.chapter===n);
  if(c){
    if('publish_state' in r) c.publish_state = r.publish_state;
    if('published_at' in r) c.published_at = r.published_at||"";
    if('published_fingerprint' in r) c.published_fingerprint = r.published_fingerprint||"";
    if('published_note' in r) c.published_note = r.published_note||"";
  }
  renderChapters(); renderReader();
}
async function markPublished(n){
  const pid = STATE.project && STATE.project.id; if(!pid) return;
  try{
    const r = await api.post(`/api/projects/${pid}/chapters/${n}/publish`, {});
    _applyPublish(n, r);
    toast(r.first_publish===false ? "재발행으로 표시했어요" : "발행함으로 표시했어요", "ok");
  }catch(e){ toast("발행 표시 실패: "+(e.message||e), "bad"); }
}
async function unmarkPublished(n){
  const pid = STATE.project && STATE.project.id; if(!pid) return;
  if(!confirm("이 회차의 발행 표시를 해제할까요? (본문은 그대로예요)")) return;
  try{
    const r = await api.del(`/api/projects/${pid}/chapters/${n}/publish`);
    if(r && r.detail){ throw new Error(r.detail); }   // api.del 은 ok 체크를 안 함 — 에러 본문 방어
    _applyPublish(n, r||{publish_state:"unpublished", published_at:"", published_note:""});
    toast("발행 표시를 해제했어요", "ok");
  }catch(e){ toast("발행 해제 실패: "+(e.message||e), "bad"); }
}

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
// RV-1②: 무변경 원인 코드 → 정직 문안. 서버(harness 사이드채널) domain 과 1:1.
//   기존 "지시를 더 구체적으로"는 원인 불문 오귀속이었다 — 대부분은 기계적(출력 절단/과확장·살균 공백·콜 실패)이라
//   작가가 지시를 고쳐도 안 바뀐다. 원인별로 다음 행동을 다르게 안내한다.
function noChangeMessage(cause){
  switch(cause){
    case "length_guard":
      return "결과 분량이 원문과 너무 달라 반영하지 않았습니다 — 회차 전체보다 특정 구간을 지정해 다듬어 보세요";
    case "empty_after_sanitize":
      return "다듬은 결과에서 본문이 남지 않아 반영하지 않았습니다 — 지시를 다시 시도해 주세요";
    case "llm_failure":
      return "다듬기 처리 중 오류가 발생했습니다 — 잠시 후 다시 시도해 주세요";
    case "unchanged":
      return "바뀐 부분이 없습니다 — 지시를 더 구체적으로 적어 보세요";
    default:
      return "바뀐 부분이 없습니다 — 지시를 더 구체적으로 적어 보세요";
  }
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
  // 무변경(changed:false) — 후보 없음. 작가에게 '효과 없음'을 원인별로 정직하게 고지(RV-1②).
  //   기존엔 원인 불문 "지시를 더 구체적으로"로 오귀속했으나, 실제 원인은 대부분 기계적(길이가드 폴백=출력 절단/과확장,
  //   메타 살균 후 공백, LLM 콜 실패)이라 '더 구체적으로'가 무의미했다. no_change_cause 로 원인별 문안을 표시.
  if(r.changed === false || !r.revision_id){
    const idEl = $("#rv-revision-id"); if(idEl) idEl.textContent = "";
    renderDiff(r.before_text||"", r.after_text||"");
    const g = $("#rv-guardrail");
    if(g){
      g.classList.remove("hidden");
      g.innerHTML = `<span class="gr-pill warn">${esc(noChangeMessage(r.no_change_cause))}</span>`;
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
  _invalidateChapter(chapterNo);                          // 본문·퇴고 이력이 바뀜 — 캐시 폐기
  STATE.project = await fetchProject(STATE.project.id);   // 최신화(본문·이력 갱신)
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
  _invalidateChapter(chapterNo);
  STATE.project = await fetchProject(STATE.project.id);
  renderChapters(); renderReader();
  toast("퇴고를 되돌렸습니다", "ok");
}

// ---------- 직접 편집(2패널 에디터 → 바뀐 대목만 검토 → 일괄 적용 — DE-2) ----------
// 퇴고(AI revise)와 다르다: AI가 다듬는 게 아니라 작가가 우측 패널에서 본문을 직접 고친다.
//   좌=원본(읽기전용) / 우=수정본(자유 편집) → '변경 확인'으로 바뀐 대목(hunk)만 카드로 추려
//   선택분을 한 번의 요청(edits[])으로 적용. 저장 시 서버가 각 구간의 '본문에 정확히 1회 일치'를 재검증하고
//   사실 가드·요약 갱신을 수행(되돌리기 가능). 여러 구간을 문서 순으로 일괄 전송한다.
//
// 왜 '문단 단위 LCS'로 먼저 좁히나: 전문 단어 LCS 는 1,000토큰 상한(REVISE_DIFF_TOKEN_CAP)에 걸려
//   장문 회차에서 인라인 diff 가 끊긴다. 개행으로 나눈 문단 단위로 먼저 바뀐 블록만 추리면, 각 블록에서만
//   단어 diff 를 돌릴 수 있어 상한을 우회한다.
// 왜 '문맥 확장(anchor)'이 필요한가: 서버는 span_text 가 본문에 정확히 1회 일치해야 교체한다. 짧은 구간은
//   본문 여러 곳과 겹치거나(중복), 순수 삽입·삭제라 문단 경계 개행이 어긋난다. 인접한 '안 바뀐 문단'을
//   한 개씩 끌어와(문단 경계는 개행 포함 슬라이스라 바이트 복원 정합) 유일해질 때까지 확장하면 앵커가 생긴다.
function extractEditHunks(before, after){
  // 1) 빈 문자열 보존해 분할(join("\n") 시 원문 바이트 그대로 복원). 문단=개행 분할 단위.
  const A = before.split("\n"), B = after.split("\n");
  // 문단 시작 char 오프셋 프리픽스(개행 포함). off[k]=문단 k 시작 char, off[len]=끝+1(가상 경계).
  const _off = (arr)=>{ const o = new Array(arr.length+1); o[0]=0; for(let k=0;k<arr.length;k++) o[k+1]=o[k]+arr[k].length+1; return o; };
  const offA=_off(A), offB=_off(B);
  // 문단 범위 [s,e) → char 범위(개행 미포함). 빈 범위(s==e)는 [off[s],off[s]) — 삽입/삭제 지점.
  const charRange = (off, arr, s, e)=> e>s ? [off[s], off[e-1]+arr[e-1].length] : [off[s], off[s]];
  // 2) 문단 단위 LCS walk(diffWords 와 동일한 walk) → 연속 del/add run 을 hunk 로 그룹.
  const dp = _lcs(A, B);
  let raw=[], i=0, j=0, cur=null;
  const flush = ()=>{ if(cur){ raw.push(cur); cur=null; } };
  const open  = ()=>{ if(!cur) cur={aStart:i, aEnd:i, bStart:j, bEnd:j}; };   // run 시작 문단 인덱스 고정
  while(i<A.length && j<B.length){
    if(A[i]===B[j]){ flush(); i++; j++; }
    else if(dp[i+1][j] >= dp[i][j+1]){ open(); cur.aEnd=i+1; i++; }   // del A[i]
    else { open(); cur.bEnd=j+1; j++; }                              // add B[j]
  }
  while(i<A.length){ open(); cur.aEnd=i+1; i++; }
  while(j<B.length){ open(); cur.bEnd=j+1; j++; }
  flush();
  if(!raw.length) return [];
  // 작업용 hunk — la*=라벨용(실제 바뀐 A 문단 범위, 고정) / a*·b*=현재 span 문단 범위(문맥 확장 시 성장)
  let list = raw.map(h=>({ laStart:h.aStart, laEnd:h.aEnd,
                           aStart:h.aStart, aEnd:h.aEnd, bStart:h.bStart, bEnd:h.bEnd,
                           a0:0, a1:0, span:"", repl:"" }));
  const recompute = (h)=>{
    const [a0,a1]=charRange(offA,A,h.aStart,h.aEnd), [b0,b1]=charRange(offB,B,h.bStart,h.bEnd);
    h.a0=a0; h.a1=a1; h.span=before.slice(a0,a1); h.repl=after.slice(b0,b1);
  };
  // 등장 횟수 — 겹침 포함(+1 전진, 상한 2: 유일성 판정만 필요). 겹침 배제(+길이 전진)면 "X\nX\nX" 에서
  //   "X\nX" 가 1회로 보이지만 실제 위치는 2곳 — 서버가 첫 위치에 적용해 의도(둘째 위치)와 어긋난다(퍼즈 실측).
  //   서버(edit_chapter)도 동일 규칙으로 센다 — 규칙 불일치 시 클라 통과·서버 거절이 갈린다.
  const occ = (needle)=>{ if(needle==="") return 0;
    let idx=before.indexOf(needle), n=0; while(idx!==-1 && n<2){ n++; idx=before.indexOf(needle, idx+1); } return n; };
  const uniquify = (h)=>{
    recompute(h);
    // span 이 빈 문자열(순수 삽입)이거나 repl 이 빈 문자열(순수 삭제)이면 문단 경계 개행이 어긋난다 —
    //   삽입·삭제 모두 인접 eq 문단을 앵커로 끌어와야 바이트 복원이 맞는다(대칭). 그리고 span 이 본문에
    //   2회 이상 등장하면 유일해질 때까지 확장. a·b 경계를 함께 이동(사이 개행 포함 슬라이스).
    // eq 가드(필수): 확장은 그 문단이 실제로 안 바뀐 문단(A/B 동일)일 때만 허용한다. 가드 없이 이웃 hunk 의
    //   변경 문단을 밟으면 그 지점부터 A/B 정렬이 어긋나 span(원본측)과 repl(수정측)이 서로 다른 내용을 물고,
    //   적용 결과가 편집본과 바이트 불일치한다(문단 이동·중복 문단 퍼즈로 실측). 막혀서 여전히 모호하면
    //   stuck 표시 → 아래 병합 패스가 이웃 hunk 와 합쳐 더 넓은 유일 앵커를 만든다.
    let guard=0;   // 무한 방지: 최악=전체 문단 수(그 지점 span=전문 → 유일)
    while((h.span==="" || h.repl==="" || occ(h.span)>1) && guard++ <= A.length+B.length+2){
      if(h.aStart>0 && h.bStart>0 && A[h.aStart-1]===B[h.bStart-1]){ h.aStart--; h.bStart--; }            // 좌 확장(선호)
      else if(h.aEnd<A.length && h.bEnd<B.length && A[h.aEnd]===B[h.bEnd]){ h.aEnd++; h.bEnd++; }          // 우 확장
      else break;                                                        // 양방향 막힘(이웃 hunk·문서 경계) — stuck 판정으로
      recompute(h);
    }
    h.stuck = (h.span==="" || h.repl==="" || occ(h.span)>1);   // 여전히 모호 — 이웃 병합 필요 신호
  };
  // 3~6) 유일화 → a0 정렬 → 병합(겹침 or stuck) → 병합 있었으면 재유일화.
  //   겹침: 양쪽에서 같은 eq 문단을 끌어와 char 범위가 겹친 경우. stuck: eq 가드에 막혀 확장으로는 유일화
  //   불가한 경우 — 이웃과 합치면 사이 구간이 eq 라 양쪽 슬라이스가 정합하고, 합친 범위에서 확장을 재개한다.
  for(;;){
    for(const h of list) uniquify(h);
    list.sort((x,y)=>x.a0-y.a0);
    let merged=false; const out=[];
    for(const h of list){
      const prev = out[out.length-1];
      if(prev && (h.a0 < prev.a1 || prev.stuck || h.stuck)){   // char 겹침 or 유일화 막힘 → 병합
        prev.aStart=Math.min(prev.aStart,h.aStart); prev.aEnd=Math.max(prev.aEnd,h.aEnd);
        prev.bStart=Math.min(prev.bStart,h.bStart); prev.bEnd=Math.max(prev.bEnd,h.bEnd);
        prev.laStart=Math.min(prev.laStart,h.laStart); prev.laEnd=Math.max(prev.laEnd,h.laEnd);
        prev.stuck=false;   // 다음 외곽 패스의 uniquify 가 재판정 — 이번 패스 연쇄 과병합 방지
        recompute(prev); merged=true;
      } else out.push(h);
    }
    list = out;
    if(!merged) break;   // 병합은 hunk 수를 줄이므로 유한(≤ 초기 개수-1회) — 종료 보장
  }
  // 7) 문서 순(a0 오름차순 정렬 완료) 산출 — 라벨용 문단 범위 동반.
  return list.map(h=>({ span:h.span, repl:h.repl,
                        laStart:h.laStart, laEnd:h.laEnd, insertion:(h.laEnd===h.laStart) }));
}
function _edHunkLabel(hk, n){   // 카드 라벨 — 1-indexed 문단 범위(순수 삽입은 '뒤 삽입')
  if(hk.insertion){
    return hk.laStart===0 ? `구간 ${n} · 맨 앞에 삽입` : `구간 ${n} · 문단 ${hk.laStart} 뒤 삽입`;
  }
  const from=hk.laStart+1, to=hk.laEnd;   // 0-indexed [laStart,laEnd) → 1-indexed 포함 [from,to]
  return from===to ? `구간 ${n} · 문단 ${from}` : `구간 ${n} · 문단 ${from}–${to}`;
}
function _edHunkDiffHtml(before, after){   // 카드별 인라인 단어 diff(퇴고 부품 재사용) — 큰 hunk 는 나란히 폴백
  if(_rvTooLargeForInline(before, after)){   // LCS DP 블로킹 회피(REVISE_DIFF_TOKEN_CAP 초과)
    return `<div class="diff-side">
      <div class="diff-side-before">${esc(before).replace(/\n/g,"<br>")}</div>
      <div class="diff-side-after">${esc(after).replace(/\n/g,"<br>")}</div>
    </div>`;
  }
  const inline = diffWords(before, after).map(p=>{
    const html = esc(p.text).replace(/\n/g,"<br>");
    if(p.type==="del") return `<span class="diff-del">${html}</span>`;
    if(p.type==="add") return `<span class="diff-add">${html}</span>`;
    return html;
  }).join("");
  return `<div class="diff-inline ed-hunk-diff">${inline}</div>`;
}
function cleanupEditModal(){   // 직접 편집 모달 닫기 + STATE.editModal 초기화(스테일 참조 방지) — 모든 닫힘 경로 공통
  if(STATE.editModal){ closeModal(STATE.editModal); }
  STATE.editModal = null;
}
function openEditModal(chapterNo){
  const c = ((STATE.project&&STATE.project.chapters)||[]).find(x=>x.chapter===chapterNo);
  if(!c){ toast("회차를 찾을 수 없습니다", "bad"); return; }
  const original = c.text||"";
  // openModal 헬퍼를 쓰지 않는 이유: 그 헬퍼의 백드롭 mousedown·Esc·[data-close] 핸들러가 무조건 closeModal 해서
  //   dirty(미저장) 확인을 끼울 수 없다. a11y 요소(role/aria-modal/aria-labelledby/Tab 포커스 트랩)만 동일 복제한다.
  _modalPrevFocus = document.activeElement;
  const ov = document.createElement("div");
  ov.className = "modal-overlay";
  ov.innerHTML = `
    <div class="modal-card ed-card" role="dialog" aria-modal="true">
      <h3>직접 편집 — ${chapterNo}화</h3>
      <p class="muted small ed-guide">오른쪽에서 본문을 직접 고치세요. 바뀐 부분만 추려서 적용 전에 보여드려요.</p>
      <div class="ed-body">
        <div class="ed-edit">
          <div class="ed-panels">
            <div class="ed-orig" aria-label="원본(읽기 전용)"></div>
            <textarea class="ed-draft" spellcheck="false" aria-label="수정본(직접 편집)"></textarea>
          </div>
        </div>
        <div class="ed-review" style="display:none"></div>
      </div>
      <div class="modal-err ed-err hidden"></div>
      <div class="revise-actions ed-actions">
        <button type="button" class="primary ed-toreview">변경 확인 →</button>
        <button type="button" class="primary ed-apply" style="display:none">적용</button>
        <button type="button" class="ed-back" style="display:none">← 계속 편집</button>
        <button type="button" class="ed-cancel">취소</button>
      </div>
    </div>`;
  const card = ov.firstElementChild;
  const h = card.querySelector("h3");                                          // 제목 → aria-labelledby
  if(h){ if(!h.id) h.id="modal-title-"+Date.now().toString(36); card.setAttribute("aria-labelledby", h.id); }
  const origEl=card.querySelector(".ed-orig"), draftEl=card.querySelector(".ed-draft");
  _rpFillEdOrig(origEl, c, original); draftEl.value = original;               // 원본: 유효 빨간펜 마크 하이라이트(없으면 textContent) / 수정본: 원문 그대로
  const editWrap=card.querySelector(".ed-edit"), reviewWrap=card.querySelector(".ed-review");
  const errEl=card.querySelector(".ed-err");
  const btnReview=card.querySelector(".ed-toreview"), btnApply=card.querySelector(".ed-apply");
  const btnBack=card.querySelector(".ed-back"), btnCancel=card.querySelector(".ed-cancel");

  let hunks = [];   // 현재 검토 카드의 hunk 목록
  const showErr = (m)=>{ if(m){ errEl.textContent=m; errEl.classList.remove("hidden"); } else { errEl.textContent=""; errEl.classList.add("hidden"); } };
  const isDirty = ()=> draftEl.value !== original;
  // 닫기 — dirty 면 확인 후에만(openModal 과 달리 무조건 닫지 않는다)
  const attemptClose = ()=>{ if(isDirty() && !confirm("편집 내용이 저장되지 않았습니다. 닫을까요?")) return; cleanupEditModal(); };

  const checkedCount = ()=> reviewWrap.querySelectorAll(".ed-hunk-cb:checked").length;
  const updateApply = ()=>{ const k = hunks.length ? checkedCount() : 0;
    btnApply.textContent = k ? `적용 (${k}곳)` : "적용"; btnApply.disabled = k===0; };
  const renderCards = ()=>{
    if(!hunks.length){
      reviewWrap.innerHTML = `<div class="ed-nochange muted">변경 없음 — 바뀐 부분이 없어요. ‘← 계속 편집’으로 돌아가 본문을 고쳐 주세요.</div>`;
      updateApply(); return;
    }
    reviewWrap.innerHTML = hunks.map((hk,idx)=>`<div class="ed-card-hunk">
        <label class="ed-hunk-head">
          <input type="checkbox" class="ed-hunk-cb" data-i="${idx}" checked>
          <span class="ed-hunk-label">${esc(_edHunkLabel(hk, idx+1))}</span>
        </label>
        <div class="ed-hunk-body">${_edHunkDiffHtml(hk.span, hk.repl)}</div>
      </div>`).join("");
    reviewWrap.querySelectorAll(".ed-hunk-cb").forEach(cb=> cb.addEventListener("change", updateApply));
    updateApply();
  };
  // 1단계 ↔ 2단계 전환 — 패널은 display:none 으로 숨겨 상태 보존(← 계속 편집으로 복귀 가능)
  const goEdit = ()=>{
    reviewWrap.style.display="none"; editWrap.style.display="";
    btnReview.style.display=""; btnApply.style.display="none"; btnBack.style.display="none";
    showErr(""); draftEl.focus();
  };
  const goReview = ()=>{
    hunks = extractEditHunks(original, draftEl.value); renderCards();
    editWrap.style.display="none"; reviewWrap.style.display="";
    btnReview.style.display="none"; btnApply.style.display=""; btnBack.style.display="";
    showErr("");
  };
  const doSave = async ()=>{
    const picked = hunks.filter((_,i)=>{ const cb=reviewWrap.querySelector(`.ed-hunk-cb[data-i="${i}"]`); return cb && cb.checked; });
    if(!picked.length){ showErr("적용할 구간을 하나 이상 선택해 주세요."); return; }
    showErr("");
    const edits = picked.map(hk=>({ span_text:hk.span, replacement:hk.repl }));   // 문서 순(hunks 가 이미 정렬됨)
    const restore = btnApply.textContent;
    btnApply.disabled=true; btnApply.textContent="저장 중…"; btnBack.disabled=true; btnCancel.disabled=true;
    try{
      await api.post(`/api/projects/${STATE.project.id}/chapters/${chapterNo}/edit`, { edits });
    }catch(e){
      // 400=구간 불일치·무변경·사실 충돌(detail 사유 그대로) · 423=생성 중 잠금. 카드 화면 유지, 버튼 복원.
      showErr(String(e.message||e));
      if(btnApply.isConnected){ btnApply.disabled=false; btnApply.textContent=restore; btnBack.disabled=false; btnCancel.disabled=false; updateApply(); }
      return;
    }
    _invalidateChapter(chapterNo);
    STATE.project = await fetchProject(STATE.project.id);   // 최신화(본문·이력 갱신)
    renderChapters(); renderReader();
    cleanupEditModal();
    toast("직접 편집이 적용됐습니다. 되돌리려면 회차의 ‘마지막 퇴고 되돌리기’를 누르세요.", "ok");
  };

  // a11y — 백드롭·Esc·Tab 트랩(openModal 복제). display:none 로 숨긴 버튼/패널은 포커스 대상 제외.
  ov.addEventListener("mousedown", e=>{ if(e.target===ov) attemptClose(); });
  ov.addEventListener("keydown", e=>{
    if(e.key==="Escape"){ e.stopPropagation(); attemptClose(); return; }
    if(e.key==="Tab"){
      const all = card.querySelectorAll('input:not([disabled]),select:not([disabled]),textarea:not([disabled]),button:not([disabled]),[tabindex]:not([tabindex="-1"])');
      const vis = Array.from(all).filter(el=> el.offsetParent!==null || el===document.activeElement);
      if(!vis.length) return;
      const first=vis[0], last=vis[vis.length-1];
      if(e.shiftKey && document.activeElement===first){ e.preventDefault(); last.focus(); }
      else if(!e.shiftKey && document.activeElement===last){ e.preventDefault(); first.focus(); }
    }
  });
  btnReview.addEventListener("click", goReview);
  btnApply.addEventListener("click", doSave);
  btnBack.addEventListener("click", goEdit);
  btnCancel.addEventListener("click", attemptClose);

  document.body.appendChild(ov);
  STATE.editModal = ov;
  draftEl.focus();
}

// ================= RP-1 빨간펜(reader inline mark) =================
// 작가가 리더에서 "마음에 안 드는" 구간을 그어 두고(본문 불변·무강제), 옵트인으로 AI 방향 제안을 받아
//   기존 퇴고/직접 편집으로 넘어간다. 마크/메모/제안은 생성·퇴고 페이로드에 절대 자동 포함하지 않는다 —
//   허용된 유일한 전달은 사용자 눈에 보이는 입력값 프리필(퇴고 지시·구간, 직접편집 하이라이트)뿐.
// stale 은 파생: 현재 본문(c.text)에서 anchor_text 가 겹침 포함 계수로 정확히 1회가 아니면 stale(클라 판정).

// 겹침 포함 등장수(+1 전진). extractEditHunks 의 occ()·서버 규칙과 동일. cap 지정 시 상한에서 조기 종료.
function _rpOcc(hay, needle, cap){
  if(!needle) return 0;
  const lim = cap||Infinity; let idx = hay.indexOf(needle), n = 0;
  while(idx!==-1 && n<lim){ n++; idx = hay.indexOf(needle, idx+1); }
  return n;
}
// 겹침 포함 등장 시작 위치 전부(+1 전진).
function _rpStarts(hay, needle){
  const out = []; if(!needle) return out;
  let idx = hay.indexOf(needle);
  while(idx!==-1){ out.push(idx); idx = hay.indexOf(needle, idx+1); }
  return out;
}
// anchor_text 가 현재 본문에서 정확히 1회가 아니면 stale/모호 → 유효하지 않음.
function _rpValid(T, m){ return _rpOcc(T, m.anchor_text||"", 2)===1; }

// 앵커 좌우 확장 — 개행(문단) 경계 단위. 한 번에 한 문단씩.
function _rpExpandLeft(T, a0){
  if(a0<=0) return 0;
  let p = a0;
  if(T[p-1]==="\n") p = p-1;                 // 이미 문단 시작이면 경계 개행을 먼저 건너뛰어 '앞 문단'으로
  const nl = T.lastIndexOf("\n", p-1);
  return nl+1;                               // 그 문단 시작(또는 0)
}
function _rpExpandRight(T, a1){
  if(a1>=T.length) return T.length;
  let p = a1;
  if(T[p]==="\n") p = p+1;                    // 이미 문단 끝이면 경계 개행을 건너뛰어 '뒤 문단'으로
  const nl = T.indexOf("\n", p);
  return nl===-1 ? T.length : nl;            // 그 문단 끝(개행 미포함)
}
// 확정 위치 [s,e) → 겹침 포함 1회가 되는 최소 앵커. 전문까지 가면 전문(항상 유일).
function _rpExpandAnchor(T, s, e){
  let a0 = s, a1 = e, guard = 0;
  while(_rpOcc(T, T.slice(a0,a1), 2)!==1 && guard++ <= T.length+2){
    const na0 = _rpExpandLeft(T, a0), na1 = _rpExpandRight(T, a1);
    if(na0===a0 && na1===a1) break;          // 더 못 넓힘(=전문) — 유일
    a0 = na0; a1 = na1;
  }
  return { anchor_text:T.slice(a0,a1), span_start:s-a0, span_len:e-s };
}
// 선택 → 원문(T) 좌표. {s,e}|null. k번째 등장 정렬.
function _rpMapSelection(sel, mdBody, T){
  const S = sel.toString();
  if(!S) return null;
  const R = mdBody.textContent||"";
  const cntA = _rpOcc(T, S, 3);                                  // (a) 겹침 포함 계수(상한 3)
  if(cntA===1){ const idx = T.indexOf(S); return {s:idx, e:idx+S.length}; }
  if(cntA===0) return null;                                      // 0회(문단 경계 걸침 포함) → 실패(c)
  // (b) 2회+ : 선택이 R 기준 S 의 몇 번째 등장인지 → T 의 같은 순번
  let range; try{ range = sel.getRangeAt(0); }catch(e){ return null; }
  const pre = document.createRange();
  pre.selectNodeContents(mdBody);
  try{ pre.setEnd(range.startContainer, range.startOffset); }catch(e){ return null; }
  const preLen = pre.toString().length;
  const startsR = _rpStarts(R, S), startsT = _rpStarts(T, S);
  if(startsT.length !== startsR.length) return null;             // 총수 불일치 → 실패(c)
  const rank = startsR.indexOf(preLen);                          // 선택 시작이 S 등장 시작과 일치해야
  if(rank===-1 || startsT[rank]===undefined) return null;
  const sT = startsT[rank];
  return {s:sT, e:sT+S.length};
}

// ---- 리더 밑줄(유효 마크만) ----
function _rpDecorate(mdBody, c){
  const T = c.text||"";
  for(const m of (c.redpen||[])){
    if(!_rpValid(T, m)) continue;                                // stale/모호 → 본문 밑줄 생략
    const ai = T.indexOf(m.anchor_text);
    const absStart = ai + (m.span_start||0), absEnd = absStart + (m.span_len||0);
    if((m.span_len||0)<=0 || absStart<0 || absEnd>T.length) continue;
    const W = T.slice(absStart, absEnd); if(!W) continue;
    const startsT = _rpStarts(T, W);
    const k = startsT.indexOf(absStart)+1;                       // T 기준 우리 범위가 몇 번째 W 인지
    if(k<1) continue;
    _rpWrapKth(mdBody, W, k, startsT.length, m.mark_id, m.note||"");
  }
}
// 렌더 DOM 에서 W 의 k번째 등장을 <mark>로 감싼다. T 와 렌더의 W 총수 불일치면 조용히 강등(밑줄 생략).
function _rpWrapKth(mdBody, W, k, expectTotal, markId, note){
  const walker = document.createTreeWalker(mdBody, NodeFilter.SHOW_TEXT, null);
  const nodes = []; let text = "", n;
  while((n = walker.nextNode())){ nodes.push({node:n, start:text.length}); text += n.nodeValue; }
  const startsR = _rpStarts(text, W);
  if(startsR.length !== expectTotal) return;                    // 총수 다름 → 강등(정직·콘솔 경고 없음)
  const s = startsR[k-1]; if(s===undefined) return;
  const e = s + W.length;
  // 텍스트 노드 경계(<br>·<p>·<strong> 등)를 걸치면 노드별로 분할 래핑(각 조각은 단일 텍스트 노드 내 → surroundContents 안전)
  const segs = [];
  for(const it of nodes){
    const ns = it.start, ne = it.start + it.node.nodeValue.length;
    const from = Math.max(s, ns), to = Math.min(e, ne);
    if(from<to) segs.push({node:it.node, from:from-ns, to:to-ns});
  }
  if(!segs.length) return;
  for(const sg of segs){
    try{
      const r = document.createRange();
      r.setStart(sg.node, sg.from); r.setEnd(sg.node, sg.to);
      const mk = document.createElement("mark");
      mk.className = "rp-mark"; mk.setAttribute("data-mark-id", markId);
      if(note) mk.title = note;
      r.surroundContents(mk);
    }catch(err){ /* 강등 — 크래시 금지 */ }
  }
}

// ---- 선택 팝오버 ----
let _rpDocDown = null, _rpScrollClose = null;
function _rpClosePopover(){
  const p = document.getElementById("rp-popover"); if(p) p.remove();
  if(_rpDocDown){ document.removeEventListener("mousedown", _rpDocDown, true); _rpDocDown = null; }
  if(_rpScrollClose){ window.removeEventListener("scroll", _rpScrollClose, true); _rpScrollClose = null; }
}
function _rpOnMouseUp(mdBody, chapterNo){
  const sel = window.getSelection();
  if(!sel || !sel.rangeCount || sel.isCollapsed) return;
  const S = sel.toString();
  if(!S.trim()) return;                                          // 공백뿐 → 무시
  if(!(mdBody.contains(sel.anchorNode) && mdBody.contains(sel.focusNode))) return;   // .md-body 내부 선택만
  const c = ((STATE.project&&STATE.project.chapters)||[]).find(x=>x.chapter===chapterNo);
  if(!c) return;
  const pos = _rpMapSelection(sel, mdBody, c.text||"");          // {s,e}|null
  const map = pos ? _rpExpandAnchor(c.text||"", pos.s, pos.e) : null;
  let rect; try{ rect = sel.getRangeAt(0).getBoundingClientRect(); }catch(e){ return; }
  _rpShowPopover(rect, map, chapterNo);
}
function _rpShowPopover(rect, map, chapterNo){
  _rpClosePopover();
  const pop = document.createElement("div");
  pop.id = "rp-popover"; pop.className = "rp-popover";
  pop.innerHTML = `<button type="button" class="rp-pop-start">🖍 빨간펜</button>`;
  document.body.appendChild(pop);
  pop.style.top = (rect.bottom+6)+"px";
  pop.style.left = Math.max(8, rect.left)+"px";
  const w = pop.offsetWidth;                                     // 우측 오버플로 클램프
  if(rect.left + w > window.innerWidth-8) pop.style.left = Math.max(8, window.innerWidth - w - 8)+"px";
  pop.querySelector(".rp-pop-start").addEventListener("click", ()=>{
    if(!map){ toast("서식이 섞인 부분은 아직 짚을 수 없어요 — 조금 좁혀서 선택해 주세요", "bad"); _rpClosePopover(); return; }
    _rpPopoverToInput(pop, map, chapterNo);
  });
  _rpDocDown = (e)=>{ if(!e.target.closest("#rp-popover")) _rpClosePopover(); };
  document.addEventListener("mousedown", _rpDocDown, true);      // 바깥 클릭 닫기(캡처 — 버튼 클릭은 내부라 유지)
  _rpScrollClose = ()=> _rpClosePopover();
  window.addEventListener("scroll", _rpScrollClose, true);       // 스크롤 시 닫기(고정 위치 드리프트 방지)
}
function _rpPopoverToInput(pop, map, chapterNo){
  pop.innerHTML = `
    <input type="text" class="rp-pop-note" placeholder="왜 마음에 안 드나요? (선택)" maxlength="200">
    <div class="rp-pop-actions">
      <button type="button" class="rp-pop-do primary">긋기</button>
      <button type="button" class="rp-pop-cancel">취소</button>
    </div>`;
  const inp = pop.querySelector(".rp-pop-note");
  const doBtn = pop.querySelector(".rp-pop-do");
  const doMark = async ()=>{
    const note = (inp.value||"").trim();
    doBtn.disabled = true; doBtn.textContent = "긋는 중…";
    try{
      await api.post(`/api/projects/${STATE.project.id}/chapters/${chapterNo}/redpen`,
        { anchor_text:map.anchor_text, span_start:map.span_start, span_len:map.span_len, note });
    }catch(e){
      toast(String(e.message||e), "bad");                        // 400/423 사유 그대로
      if(doBtn.isConnected){ doBtn.disabled = false; doBtn.textContent = "긋기"; }
      return;
    }
    _rpClosePopover();
    _invalidateChapter(chapterNo);                          // 빨간펜은 요약본에 안 실리는 축 — 명시 폐기
    STATE.project = await fetchProject(STATE.project.id);   // 전체 재조회(부분 stale 방지)
    renderReader();
    toast("빨간펜을 그었어요", "ok");
  };
  inp.addEventListener("keydown", (e)=>{
    if(e.key==="Enter"){ e.preventDefault(); doMark(); }
    else if(e.key==="Escape"){ e.preventDefault(); _rpClosePopover(); }
  });
  doBtn.addEventListener("click", doMark);
  pop.querySelector(".rp-pop-cancel").addEventListener("click", ()=> _rpClosePopover());
  inp.focus();
}

// ---- 마크 패널 ----
function _rpChipHtml(c){
  const n = ((c&&c.redpen)||[]).length;
  if(!n) return "";
  return ` · <span class="rp-chip" ${ACT} onclick="_rpTogglePanel()">🖍 빨간펜 ${n}</span>`;
}
function _rpTogglePanel(){
  const p = $("#rp-panel"); if(!p) return;
  const willOpen = p.classList.contains("hidden");
  p.classList.toggle("hidden");
  STATE.rpPanelOpen = willOpen;                                  // 재렌더(제안·삭제) 후 열림 상태 복원용
}
function _rpRenderPanel(c){
  const panel = $("#rp-panel"); if(!panel) return;
  const marks = c.redpen||[], T = c.text||"";
  const valid = marks.filter(m=>_rpValid(T, m));
  const cross = (STATE.rpCross&&STATE.rpCross[c.chapter])||"";
  const top = `<div class="rp-panel-top">
      ${valid.length?`<button type="button" class="rp-suggest-all">🖍 전체 방향 제안</button>`:""}
      ${cross?`<div class="rp-cross">${esc(cross)}</div>`:""}
    </div>`;
  const rows = marks.map(m=>{
    const stale = !_rpValid(T, m);
    const disp = (m.anchor_text||"").slice(m.span_start||0, (m.span_start||0)+(m.span_len||0));
    const excerpt = esc(disp.slice(0,40)) + (disp.length>40?"…":"");
    const noteHtml = m.note?`<span class="rp-note">${esc(m.note)}</span>`:"";
    const staleHtml = stale?`<span class="rp-stale">본문이 바뀌어 위치를 잃었어요</span>`:"";
    const suggs = m.suggestions||[];
    const suggHtml = suggs.length
      ? `<div class="rp-suggs">`+suggs.map((s,si)=>`<div class="rp-sugg">
            <span class="rp-sugg-text">${esc(s)}</span>
            <button type="button" class="rp-sugg-revise" data-mid="${esc(m.mark_id)}" data-si="${si}">이 방향으로 퇴고</button>
            <button type="button" class="rp-sugg-edit" data-mid="${esc(m.mark_id)}">직접 고치기</button>
          </div>`).join("")+`</div>`
      : "";
    const suggestBtn = stale?"":`<button type="button" class="rp-suggest-one" data-mid="${esc(m.mark_id)}">방향 제안</button>`;
    return `<div class="rp-row${stale?" rp-row-stale":""}" data-mid="${esc(m.mark_id)}">
        <div class="rp-row-head">
          <span class="rp-excerpt">${excerpt||'<span class="muted">(빈 구간)</span>'}</span>
          ${staleHtml}${noteHtml}
          <span class="rp-row-actions">${suggestBtn}<button type="button" class="rp-del" data-mid="${esc(m.mark_id)}">삭제</button></span>
        </div>${suggHtml}
      </div>`;
  }).join("");
  panel.innerHTML = top + (rows || `<div class="muted small">아직 그은 곳이 없어요.</div>`);
  const sall = panel.querySelector(".rp-suggest-all");
  if(sall) sall.addEventListener("click", ()=> _rpSuggest(c.chapter, null, sall));
  panel.querySelectorAll(".rp-suggest-one").forEach(b=> b.addEventListener("click", ()=> _rpSuggest(c.chapter, [b.dataset.mid], b)));
  panel.querySelectorAll(".rp-del").forEach(b=> b.addEventListener("click", ()=> _rpDeleteMark(c.chapter, b.dataset.mid)));
  panel.querySelectorAll(".rp-sugg-revise").forEach(b=> b.addEventListener("click", ()=>{
    const m = (c.redpen||[]).find(x=>x.mark_id===b.dataset.mid); if(!m) return;
    const dir = (m.suggestions||[])[parseInt(b.dataset.si,10)]||"";
    _rpToRevise(c.chapter, m.anchor_text||"", dir);
  }));
  panel.querySelectorAll(".rp-sugg-edit").forEach(b=> b.addEventListener("click", ()=> _rpToEdit(c.chapter)));
}
async function _rpSuggest(chapterNo, markIds, btn){
  const restore = btn?btn.textContent:"";
  if(btn){ btn.disabled = true; btn.textContent = "제안 받는 중…"; }
  let r;
  try{
    r = await api.post(`/api/projects/${STATE.project.id}/chapters/${chapterNo}/redpen/suggest`,
                       markIds?{mark_ids:markIds}:{});
  }catch(e){
    toast(String(e.message||e), "bad");
    if(btn && btn.isConnected){ btn.disabled = false; btn.textContent = restore; }
    return;
  }
  STATE.rpCross = STATE.rpCross||{};
  if(r && typeof r.cross==="string" && r.cross) STATE.rpCross[chapterNo] = r.cross;
  STATE.rpPanelOpen = true;                                      // 제안 결과를 바로 보이게 패널 유지
  _invalidateChapter(chapterNo);
  STATE.project = await fetchProject(STATE.project.id);
  renderReader();
}
async function _rpDeleteMark(chapterNo, markId){
  // 마크는 가벼운 메모 — confirm 없이 즉시 삭제. api.del 은 오류를 삼키므로 로컬 fetch 로 실패를 감지(공유 헬퍼 불변).
  let resp;
  try{ resp = await fetch(`/api/projects/${STATE.project.id}/chapters/${chapterNo}/redpen/${encodeURIComponent(markId)}`, {method:"DELETE"}); }
  catch(e){ toast("삭제하지 못했어요 — 잠시 후 다시 시도해 주세요", "bad"); return; }
  if(!resp.ok){ toast("삭제하지 못했어요 — 이미 지워졌거나 위치를 찾지 못했습니다", "bad"); return; }
  STATE.rpPanelOpen = true;
  _invalidateChapter(chapterNo);
  STATE.project = await fetchProject(STATE.project.id);
  renderReader();
}
// 방향 → 기존 퇴고 모달 프리필(모달 코드 불변 — 열고 나서 사용자 눈에 보이는 입력값만 세팅).
function _rpToRevise(chapterNo, anchorText, direction){
  openReviseModal(chapterNo);
  const d = $("#rv-directive"); if(d) d.value = direction;
  const s = $("#rv-span"); if(s) s.value = anchorText;
  if(d) d.focus();
}
// 방향 → 직접 편집 모달(좌측 원본에 유효 마크 하이라이트는 openEditModal 이 _rpFillEdOrig 로 적용).
function _rpToEdit(chapterNo){ openEditModal(chapterNo); }
// 직접 편집 좌측 원본 프리필 — 유효 마크가 있으면 하이라이트 HTML, 없으면 기존 textContent 경로.
function _rpFillEdOrig(origEl, c, original){
  const marks = (c&&c.redpen)||[], T = original;
  const ranges = [];
  for(const m of marks){
    if(_rpOcc(T, m.anchor_text||"", 2)!==1) continue;            // 유효(정확 1회)만
    const ai = T.indexOf(m.anchor_text);
    const s = ai + (m.span_start||0), e = s + (m.span_len||0);
    if((m.span_len||0)>0 && s>=0 && e<=T.length) ranges.push({s, e, note:m.note||""});
  }
  if(!ranges.length){ origEl.textContent = original; return; }   // 마크 없음 → 기존 경로 그대로
  ranges.sort((a,b)=>a.s-b.s);
  let html = "", pos = 0, last = -1;
  for(const r of ranges){
    if(r.s < last) continue;                                     // 겹치는 마크는 앞선 것 우선(비겹침만)
    html += esc(original.slice(pos, r.s));
    const title = r.note?` title="${esc(r.note)}"`:"";
    html += `<mark class="rp-mark"${title}>` + esc(original.slice(r.s, r.e)) + `</mark>`;
    pos = r.e; last = r.e;
  }
  html += esc(original.slice(pos));
  origEl.innerHTML = html;
}
// 리더 렌더 후 배선 — 밑줄·패널·선택 핸들러(매 renderReader 마다 .md-body 는 새로 생성되므로 리스너 누수 없음).
function _rpSetup(c){
  const mdBody = $("#chapter-body .md-body");
  if(mdBody){
    _rpDecorate(mdBody, c);
    mdBody.addEventListener("mouseup", ()=> _rpOnMouseUp(mdBody, c.chapter));
  }
  _rpRenderPanel(c);
  const panel = $("#rp-panel");
  if(panel && STATE.rpPanelOpen) panel.classList.remove("hidden");   // 재렌더 시 열림 상태 복원
}

health(); renderStartExamples(); router();   // 라우터가 현재 해시(기본=홈) 렌더 — 새로고침/딥링크 복원
