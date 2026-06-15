"use strict";
// ===== AI 웹소설 코파일럿 프론트엔드 (vanilla, 빌드 불필요) =====
const $ = (s) => document.querySelector(s);

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
      const ch = (parts[2] === "ch" && parts[3]) ? parseInt(parts[3], 10) : null;
      _onlyView("view-project");
      if(ch && (STATE.project.chapters||[]).some(c=>c.chapter===ch)){
        STATE.activeChapter = ch; STATE.chapterPage = chPageOf(ch);
        switchTab("reader"); renderChapters(); renderReader();
      }
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
function switchTab(t){
  document.querySelectorAll('.col-reader .tab').forEach(b=>b.classList.toggle('active',b.dataset.tab===t));
  ["reader","bible","graph","arc","worldgen"].forEach(x=>$("#tab-"+x).classList.toggle("hidden",x!==t));
  if(t==="graph") loadGraph();
  else if(CY){ try{CY.destroy();}catch(e){} CY=null; SELECTED=[]; SELECTED_EDGE=null; }  // 떠날 때 정리(누수 방지)
  if(t==="arc") loadSpine();
  if(t==="bible") loadBible();
  if(t==="worldgen") loadWorldgen();
}

// ---------- 협업형 월드빌딩 대화 (R3) ----------
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
    loadOntology();   // 실시간 반영
    if(!$("#tab-graph").classList.contains("hidden")) loadGraph();
    if(!$("#tab-bible").classList.contains("hidden")) loadBible();
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
async function loadSpine(){
  const el=$("#tab-arc");
  try{
    const sp=await api.get(`/api/projects/${STATE.project.id}/spine`);
    if(!sp.has_spine){ el.innerHTML='<p class="muted">아직 이야기 구조가 없습니다. 새 작품은 자동으로 설계됩니다.</p>'; return; }
    // G5 장르 정체성 — 작품이 지키는 재미의 축(설계가 공유)
    const gc=sp.genre_contract;
    const gcBlock = (gc&&(gc.pleasure_engine||gc.premise_asset))?`<div class="bible-sec"><h4>장르 정체성 <span class="muted small">— 이 작품이 지키는 재미의 축</span></h4>
      ${gc.pleasure_engine?`<p class="small"><b>독자 쾌감:</b> ${esc(gc.pleasure_engine)}</p>`:""}
      ${gc.reader_expectations&&gc.reader_expectations.length?`<p class="small"><b>독자 기대:</b> ${gc.reader_expectations.map(esc).join(" · ")}</p>`:""}
      ${gc.premise_asset?`<p class="small"><b>핵심 전제:</b> ${esc(gc.premise_asset)}</p>`:""}</div>`:"";
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
    el.innerHTML=gcBlock+ledgerBlock+retroBlock+ending+`<div class="bible-sec"><h4>단락과 에피소드 <span class="muted small">(현재 단락 ${sp.chapters_in_episode}화째)</span></h4>${arcs}</div>`;
  }catch(e){ el.innerHTML=`<span class="muted">로드 실패: ${esc(e.message)}</span>`; }
}
async function loadRetrospective(){
  const body=$("#retro-body"), btn=$("#retro-btn");
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
function switchInspect(t){
  document.querySelectorAll('.col-inspect .tab').forEach(b=>b.classList.toggle('active',b.dataset.itab===t));
  $("#inspect-onto").classList.toggle("hidden",t!=="onto");
  $("#inspect-wiki").classList.toggle("hidden",t!=="wiki");
  $("#inspect-gen").classList.toggle("hidden",t!=="gen");
  if(t==="wiki") loadWiki();
  if(t==="gen") renderGenInspect();
}

// ---------- 홈 ----------
async function loadProjects(){
  const el = $("#project-list");
  try{
    const list = await api.get("/api/projects");
    if(!list.length){ el.innerHTML = '<p class="muted">아직 작품이 없습니다. 왼쪽에서 첫 작품을 시작해 보세요.</p>'; return; }
    el.innerHTML = list.map(p=>`
      <div class="pcard" onclick="go('#/p/${p.id}')">
        <div><div class="pc-title">${esc(p.title||"무제")}</div>
        <div class="pc-meta">${esc(p.genre||"")} · ${p.current_chapter}/${p.total_chapters}화 · ${esc(p.created_at||"")}</div></div>
        <button class="del" onclick="event.stopPropagation();delProject('${p.id}')">삭제</button>
      </div>`).join("");
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
async function openProject(pid){
  STATE.project = await api.get(`/api/projects/${pid}`);
  STATE.activeChapter = STATE.project.current_chapter || null;
  STATE.chapterPage = null;          // null → renderChapters 가 최신(마지막) 페이지로
  renderHeader(); renderChapters(); renderReader();
  loadOntology(); switchTab('reader'); switchInspect('onto');
  $("#harness-log").innerHTML = ""; $("#gen-result").innerHTML = "";
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
  }catch(e){ el.style.borderColor = "var(--bad)"; $("#gen-result").innerHTML = `설정을 저장하지 못했습니다: ${esc(e.message)}`; }
}
async function addBible(){
  const title = prompt("설정 항목 제목:"); if(!title) return;
  const category = (prompt("카테고리 키 (magic_system/ability_system/bestiary/race/geography/"
    + "faction_politics/chronology/artifact/character/culture_religion/power_system/taboo_worldrule/glossary):",
    "glossary")||"glossary").trim();
  const prose = prompt("내용(산문):","")||"";
  try{ await api.post(`/api/projects/${STATE.project.id}/bible`,{category,title,prose}); loadBible(); }
  catch(e){ alert("추가 실패: "+e.message); }
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
    <div class="gd-row"><b>참조 자료</b>${list(d.anchors,a=>`<div class="gd-line"><span class="gd-src">${esc(SRC_LABEL[a.source]||a.source)}</span> ${esc(a.text)}</div>`)}</div>
    ${(d.directives&&d.directives.length)?`<div class="gd-row"><b>작가 지시</b>${d.directives.map(x=>`<div class="gd-line">${esc(x)}</div>`).join("")}</div>`:''}
    ${d.story_so_far?`<div class="gd-row"><b>누적 줄거리</b><pre class="gd-pre">${esc(d.story_so_far)}</pre></div>`:''}
    ${d.voice_roster?`<div class="gd-row"><b>보이스·명부</b><pre class="gd-pre">${esc(d.voice_roster)}</pre></div>`:''}
    <div class="gd-row muted tiny">집필 화법: ${esc((d.persona||'').slice(0,120))} · 직전 회차 ${d.prev_chapter_chars||0}자 주입</div></div>`;
  return `<div class="gen-debug">${planBlk}${draftBlk}</div>`;
}
function renderGenInspect(){
  const el=$("#inspect-gen");
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
      body.textContent = "왼쪽에서 회차를 고르거나, 가운데에서 다음 회차를 써보세요.";
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
  body.innerHTML = `<h4>${c.chapter}화 · ${esc(c.title)} ${badge}</h4>`+
    `<div class="reader-meta">${chars.toLocaleString()}자${c.wiki_pages_touched?` · 인물 노트 ${c.wiki_pages_touched}건 갱신`:""}</div>`+
    rec+readerBlock+
    (oc?`<div style="margin-bottom:1.4em">${oc}</div>`:"")+
    `<div>${esc(c.text).replace(/\n/g,"<br>")}</div>`;
  if(!$("#inspect-gen").classList.contains("hidden")) renderGenInspect();   // 활성 회차 바뀌면 '생성 정보' 패널 동기화
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
  const target = n || STATE.activeChapter || list[list.length-1].chapter;
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
  const badge = c.status==="FINALIZED"?'<span class="badge fin">완성</span>':'<span class="badge esc">검토 필요</span>';
  $("#v-title").textContent = `${c.chapter}화 · ${c.title||""}`;
  $("#v-badge").innerHTML = badge;
  // 문단 단위 렌더 — 빈 줄/줄바꿈으로 분리, 대사("…")는 dlg 로 표시
  $("#v-body").innerHTML = (c.text||"").split(/\n+/).map(s=>s.trim()).filter(Boolean)
    .map(p=>`<p${/^["“]/.test(p)?' class="dlg"':''}>${esc(p)}</p>`).join("");
  $("#v-count").textContent = `${STATE.viewerIdx+1} / ${list.length}`;
  $("#v-prev").disabled = STATE.viewerIdx<=0;
  $("#v-next").disabled = STATE.viewerIdx>=list.length-1;
  $("#v-jump").value = String(STATE.viewerIdx);
  $("#view-viewer").scrollTop = 0;
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
  const es = new EventSource(url);
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
    case "retrospective_available": return ["아크 완결", `'${ev.arc||""}' — 이야기 구조 탭에서 회고를 받아보세요`];
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
  STATE.generating = false; genStop(); $("#directive").value="";
  if(data.completed){   // 엔딩 도달 → 완결
    $("#gen-btn").disabled = true; $("#gen-btn").textContent = "완결되었습니다";
    $("#gen-result").innerHTML = `작품이 완결되었습니다 · ${data.current_chapter}화`;
    STATE.project = await api.get(`/api/projects/${STATE.project.id}`);
    renderHeader(); renderChapters();
    if(!$("#tab-arc").classList.contains("hidden")) loadSpine();
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
    ? `<div class="retro-nudge">📋 아크가 끝났어요 — <a onclick="switchTab('arc');setTimeout(loadRetrospective,300)">이야기 구조 탭에서 회고 받기</a></div>`:"";
  $("#gen-result").innerHTML = `${r.chapter}화 ${badge} · AI 사용량 +${data.usage_delta.chat_calls}회${fail}${drift}${retroNudge}${readerBlock}`;
  // 상태 갱신
  STATE.project = await api.get(`/api/projects/${STATE.project.id}`);
  STATE.activeChapter = r.chapter;
  STATE.chapterPage = chPageOf(r.chapter);
  setHashSilent(`#/p/${STATE.project.id}/ch/${r.chapter}`);   // 새 회차를 URL에 반영(재라우팅 없이)
  renderHeader(); renderChapters(); renderReader(); loadOntology();
  if(!$("#inspect-wiki").classList.contains("hidden")) loadWiki();
  if(!$("#tab-graph").classList.contains("hidden")) loadGraph();
  if(!$("#tab-arc").classList.contains("hidden")) loadSpine();
}
function onFail(data){
  STATE.generating = false; genStop(); $("#gen-btn").disabled = false;
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
  CY=cytoscape({
    container:$("#cy"), elements:els, wheelSensitivity:0.2,
    style:[
      {selector:'node',style:{'background-color':'data(color)','shape':'data(shape)',
        'label':'data(label)','color':'#1C1B19','font-size':'11px','font-weight':600,'text-valign':'bottom',
        'text-halign':'center','text-margin-y':4,'text-outline-color':'#FAF8F3','text-outline-width':2.5,
        'width':38,'height':38,'border-width':2,'border-color':'#FFFFFF'}},
      {selector:'node[dead=1]',style:{'border-color':'#B23A38','border-width':3,'opacity':0.55}},
      {selector:'node[prov=1]',style:{'border-style':'dashed','border-color':'#2F5E8C'}},
      {selector:'node:selected',style:{'border-color':'#2F5E8C','border-width':5}},
      {selector:'edge',style:{'width':2,'line-color':'data(color)','target-arrow-color':'data(color)',
        'target-arrow-shape':'data(arrow)','line-style':'data(estyle)','curve-style':'bezier',
        'label':'data(label)','font-size':'9px','color':'#3D3A34','text-rotation':'autorotate',
        'text-background-color':'#FAF8F3','text-background-opacity':0.9,'text-background-padding':2}},
      {selector:'edge[trust="narrative_inferred"]',style:{'line-style':'dashed','opacity':0.55,
        'line-color':'#B0AB9E','target-arrow-color':'#B0AB9E','width':1.5}},
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
    + ` <span class="lg"><span class="dot" style="background:#B23A38"></span>사망</span>`
    + ` <span class="lg"><span class="dot" style="background:#2F5E8C"></span>AI·작가 추가</span>`
    + ` <span class="lg">┄ 점선 = 추정 관계(아직 미확정)</span>`;
}

// ---------- 유틸 ----------
function esc(s){ return String(s==null?"":s).replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m])); }
async function health(){ try{ const h=await api.get("/api/health"); const el=$("#health");
  el.textContent="연결됨"; el.title=`${h.provider} · ${h.model}`; }catch(e){ const el=$("#health"); if(el) el.textContent="연결 끊김"; } }

health(); renderStartExamples(); router();   // 라우터가 현재 해시(기본=홈) 렌더 — 새로고침/딥링크 복원
