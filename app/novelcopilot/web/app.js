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
let STATE = { project:null, activeChapter:null, generating:false };

// ---------- 네비게이션 ----------
function goHome(){ $("#view-viewer").classList.add("hidden"); $("#view-project").classList.add("hidden"); $("#view-home").classList.remove("hidden"); loadProjects(); }
function showProject(){ $("#view-home").classList.add("hidden"); $("#view-project").classList.remove("hidden"); }
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
async function loadSpine(){
  const el=$("#tab-arc");
  try{
    const sp=await api.get(`/api/projects/${STATE.project.id}/spine`);
    if(!sp.has_spine){ el.innerHTML='<p class="muted">아직 이야기 구조가 없습니다. 새 작품은 자동으로 설계됩니다.</p>'; return; }
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
    el.innerHTML=ending+`<div class="bible-sec"><h4>단락과 에피소드 <span class="muted small">(현재 단락 ${sp.chapters_in_episode}화째)</span></h4>${arcs}</div>`;
  }catch(e){ el.innerHTML=`<span class="muted">로드 실패: ${esc(e.message)}</span>`; }
}
function switchInspect(t){
  document.querySelectorAll('.col-inspect .tab').forEach(b=>b.classList.toggle('active',b.dataset.itab===t));
  $("#inspect-onto").classList.toggle("hidden",t!=="onto");
  $("#inspect-wiki").classList.toggle("hidden",t!=="wiki");
  if(t==="wiki") loadWiki();
}

// ---------- 홈 ----------
async function loadProjects(){
  const el = $("#project-list");
  try{
    const list = await api.get("/api/projects");
    if(!list.length){ el.innerHTML = '<p class="muted">아직 작품이 없습니다. 왼쪽에서 첫 작품을 시작해 보세요.</p>'; return; }
    el.innerHTML = list.map(p=>`
      <div class="pcard" onclick="openProject('${p.id}')">
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
function createProject(ev){
  ev.preventDefault();
  const f = ev.target, st = $("#create-status");
  const p = new URLSearchParams({ title:f.title.value, genre:f.genre.value, tone:f.tone.value,
    premise:f.premise.value, protagonist_hint:f.protagonist_hint.value,
    target_chapters:String(parseInt(f.target_chapters.value||"12",10)) });
  const btn = f.querySelector("button"); btn.disabled = true;
  st.innerHTML = '<span class="spin"></span> 시작하는 중…';
  const es = new EventSource(`/api/projects/create_stream?${p.toString()}`);
  let done = false;
  es.addEventListener("event", e=>{ st.innerHTML = `<span class="spin"></span> ${wgStage(JSON.parse(e.data))}`; });
  es.addEventListener("complete", async e=>{
    done = true; es.close(); btn.disabled = false;
    const res = JSON.parse(e.data);
    st.innerHTML = `‘${esc(res.world.title)}’ 세계가 만들어졌어요.`;
    await openProject(res.id);
  });
  es.addEventListener("failed", e=>{
    done = true; es.close(); btn.disabled = false;
    st.innerHTML = `생성하지 못했습니다: ${esc((JSON.parse(e.data)||{}).message||"")}`;
  });
  es.onerror = ()=>{ if(!done){ es.close(); btn.disabled = false;
    st.innerHTML = "연결이 끊겼습니다. 다시 시도해 주세요."; } };
  return false;
}

// ---------- 프로젝트 열기 ----------
async function openProject(pid){
  STATE.project = await api.get(`/api/projects/${pid}`);
  STATE.activeChapter = STATE.project.current_chapter || null;
  showProject();
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
  const p = STATE.project;
  const nav = $("#chapter-nav");
  if(!p.chapters.length){ nav.innerHTML = '<span class="muted small">아직 생성된 회차가 없습니다.</span>'; return; }
  nav.innerHTML = p.chapters.map(c=>{
    const esc2 = c.status==="ESCALATED"?"escalated":"";
    const act = c.chapter===STATE.activeChapter?"active":"";
    return `<button class="cn ${act} ${esc2}" onclick="selectChapter(${c.chapter})">${c.chapter}화</button>`;
  }).join("");
}
function selectChapter(n){ STATE.activeChapter = n; renderChapters(); renderReader(); }
function renderReader(){
  const p = STATE.project, body = $("#chapter-body");
  const c = (p.chapters||[]).find(x=>x.chapter===STATE.activeChapter);
  if(!c){ body.classList.add("muted"); body.textContent = "가운데에서 다음 회차를 써보세요."; return; }
  body.classList.remove("muted");
  const badge = c.status==="FINALIZED"?'<span class="badge fin">완성</span>':'<span class="badge esc">검토 필요</span>';
  const oc = (c.ontology_changes||[]).map(o=>`<div class="onto-change ${o.op==='new_entity'?'new':o.op==='contradiction'?'con':'chg'}">${o.applied?"✓":"✗"} ${esc(o.entity)}: ${esc(o.detail)}${o.reason?` <span class="muted">(${esc(o.reason)})</span>`:""}</div>`).join("");
  const chars = (c.text||"").length;
  body.innerHTML = `<h4>${c.chapter}화 · ${esc(c.title)} ${badge}</h4>`+
    `<div class="reader-meta">${chars.toLocaleString()}자${c.wiki_pages_touched?` · 인물 노트 ${c.wiki_pages_touched}건 갱신`:""}</div>`+
    (oc?`<div style="margin-bottom:1.4em">${oc}</div>`:"")+
    `<div>${esc(c.text).replace(/\n/g,"<br>")}</div>`;
}

// ---------- 뷰어(몰입형 읽기) — 웹소설 플랫폼처럼 이전·다음 화 ----------
function viewerChapters(){
  return (STATE.project.chapters||[]).filter(c=>c.text).sort((a,b)=>a.chapter-b.chapter);
}
function openViewer(n){
  const list = viewerChapters();
  if(!list.length){ alert("아직 읽을 회차가 없습니다. 먼저 회차를 써보세요."); return; }
  const target = n || STATE.activeChapter || list[list.length-1].chapter;
  const idx = list.findIndex(c=>c.chapter===target);
  STATE.viewerIdx = idx>=0 ? idx : 0;
  $("#view-home").classList.add("hidden");
  $("#view-project").classList.add("hidden");
  $("#view-viewer").classList.remove("hidden");
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
function viewerNav(d){
  const list = viewerChapters(), ni = STATE.viewerIdx + d;
  if(ni<0 || ni>=list.length) return;
  STATE.viewerIdx = ni; renderViewer();
}
function viewerJump(i){ STATE.viewerIdx = parseInt(i,10)||0; renderViewer(); }
function closeViewer(){
  $("#view-viewer").classList.add("hidden");
  $("#view-project").classList.remove("hidden");
  const list = viewerChapters(), c = list[STATE.viewerIdx];   // 작업실 리더를 본 회차로 동기화
  if(c){ STATE.activeChapter = c.chapter; renderChapters(); renderReader(); }
}
document.addEventListener("keydown", e=>{
  if($("#view-viewer").classList.contains("hidden")) return;
  if(e.key==="ArrowLeft"){ viewerNav(-1); }
  else if(e.key==="ArrowRight"){ viewerNav(1); }
  else if(e.key==="Escape"){ closeViewer(); }
});

// ---------- 회차 생성 (SSE 라이브) ----------
function generateNext(){
  if(STATE.generating) return;
  const pid = STATE.project.id;
  const directive = $("#directive").value.trim();
  STATE.generating = true;
  $("#gen-btn").disabled = true;
  $("#harness-log").innerHTML = "";
  $("#gen-result").innerHTML = '<span class="spin"></span> 집필 중…';
  logEvent({node:"harness",event:"connect"},"");

  const url = `/api/projects/${pid}/generate?directive=${encodeURIComponent(directive)}`;
  const es = new EventSource(url);
  let done = false;
  es.addEventListener("start", e=>{ const d=JSON.parse(e.data); logEvent({node:"plan_chapter",event:`${d.chapter}화 시작`},"",true); });
  es.addEventListener("event", e=> logEvent(JSON.parse(e.data)));
  es.addEventListener("complete", e=>{ done=true; es.close(); onComplete(JSON.parse(e.data)); });
  es.addEventListener("failed", e=>{ done=true; es.close(); onFail(JSON.parse(e.data)); });
  es.onerror = ()=>{ if(!done){ es.close(); onFail({message:"연결 끊김"}); } };
}
// 상태색: 실패/검토=red, 경고=amber, 완료성=green. (이벤트 코드 자체는 화면에 안 나오고 색 분류에만 사용)
const EV_BAD = new Set(["escalation","non_convergence","failed","parse_failure","wiki_failure","tense_fix_failed"]);
const EV_WARN = new Set(["story_truncated","bible_truncated","violations","tics_residual","reformat_rejected",
  "signal","episode_stuck","plant_backlog","uncast_character","ssot_contradiction"]);
const EV_OK = new Set(["done","new_entity","relation","registered","debut","bible_done","spine_done","world_done"]);
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
  STATE.generating = false; $("#directive").value="";
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
  $("#gen-result").innerHTML = `${r.chapter}화 ${badge} · AI 사용량 +${data.usage_delta.chat_calls}회${fail}${drift}`;
  // 상태 갱신
  STATE.project = await api.get(`/api/projects/${STATE.project.id}`);
  STATE.activeChapter = r.chapter;
  renderHeader(); renderChapters(); renderReader(); loadOntology();
  if(!$("#inspect-wiki").classList.contains("hidden")) loadWiki();
  if(!$("#tab-graph").classList.contains("hidden")) loadGraph();
  if(!$("#tab-arc").classList.contains("hidden")) loadSpine();
}
function onFail(data){
  STATE.generating = false; $("#gen-btn").disabled = false;
  $("#gen-result").innerHTML = `회차를 쓰지 못했습니다: ${esc(data.message||"")}`;
  logEvent({node:"harness",event:"실패"}, data.message||"", true);
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

health(); loadProjects();
