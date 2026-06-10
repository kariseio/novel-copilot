"use strict";
// ===== AI 웹소설 코파일럿 프론트엔드 (vanilla, 빌드 불필요) =====
const $ = (s) => document.querySelector(s);
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
function goHome(){ $("#view-project").classList.add("hidden"); $("#view-home").classList.remove("hidden"); loadProjects(); }
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
    const blk=(r.blocked||[]).map(b=>`<span class="wg-chip blocked">차단: ${esc(b.reason)}</span>`).join("");
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
    if(!sp.has_spine){ el.innerHTML='<p class="muted">평면 모드(엔딩-주도 spine 없음). 새 작품은 자동 생성됩니다.</p>'; return; }
    const ending=sp.ending?`<div class="bible-sec"><h4>엔딩 (고정 — 여기로 수렴)</h4>
      <p><b>질문:</b> ${esc(sp.ending.central_question)}<br><b>결말:</b> ${esc(sp.ending.ending)}
      ${sp.ending.thematic_payoff?`<br><b>주제:</b> ${esc(sp.ending.thematic_payoff)}`:""}</p></div>`:"";
    const arcs=sp.arcs.map(a=>{
      const eps=(a.episodes||[]).map(e=>{
        const cur=e.episode_id===sp.current_episode_id, st=e.done?"완료":(cur?"진행중":"예정");
        return `<div class="ep ${cur?'cur':''} ${e.done?'done':''}"><b>${esc(e.title||e.episode_id)}</b>
          <span class="muted small">[${st} · ~${e.target_chapters}화]</span>
          <div class="small">절정: ${esc(e.climax)}</div>
          ${e.required_cast&&e.required_cast.length?`<div class="muted small">필수: ${e.required_cast.map(esc).join(", ")}</div>`:""}
          ${e.summary?`<div class="muted small">요약: ${esc(e.summary)}</div>`:""}</div>`;
      }).join("")||'<span class="muted small">에피소드 미생성(진행 시 lazy 생성)</span>';
      return `<div class="arc ${a.done?'done':''}"><div class="arc-h">${esc(a.title||a.arc_id)}
        <span class="muted small">— ${esc(a.goal)}</span></div>${eps}</div>`;
    }).join("");
    el.innerHTML=ending+`<div class="bible-sec"><h4>아크 · 에피소드 (현재 에피소드 ${sp.chapters_in_episode}화 진행)</h4>${arcs}</div>`;
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
    if(!list.length){ el.innerHTML = '<p class="muted">아직 작품이 없습니다. 왼쪽에서 시작하세요.</p>'; return; }
    el.innerHTML = list.map(p=>`
      <div class="pcard" onclick="openProject('${p.id}')">
        <div><div class="pc-title">${esc(p.title||"무제")}</div>
        <div class="pc-meta">${esc(p.genre||"")} · ${p.current_chapter}/${p.total_chapters}화 · ${esc(p.created_at||"")}</div></div>
        <button class="del" onclick="event.stopPropagation();delProject('${p.id}')">삭제</button>
      </div>`).join("");
  }catch(e){ el.innerHTML = `<p class="muted">목록 로드 실패: ${esc(e.message)}</p>`; }
}
async function delProject(pid){ if(!confirm("삭제할까요?")) return; await api.del(`/api/projects/${pid}`); loadProjects(); }

async function createProject(ev){
  ev.preventDefault();
  const f = ev.target, st = $("#create-status");
  const body = { title:f.title.value, genre:f.genre.value, tone:f.tone.value, premise:f.premise.value,
    protagonist_hint:f.protagonist_hint.value, target_chapters:parseInt(f.target_chapters.value||"12",10) };
  st.innerHTML = '<span class="spin"></span> 세계 생성 중… (LLM worldgen, 10~30초)';
  f.querySelector("button").disabled = true;
  try{
    const res = await api.post("/api/projects", body);
    st.innerHTML = `✅ '${esc(res.world.title)}' 생성 완료 (worldgen ${res.usage.chat_calls}콜).`;
    await openProject(res.id);
  }catch(e){ st.innerHTML = `❌ 실패: ${esc(e.message)}`; }
  finally{ f.querySelector("button").disabled = false; }
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
  $("#p-cost").textContent = `누적 ${u.chat_calls||0}콜 · ${(u.chat_tokens||0).toLocaleString()}토큰`;
  const done=!!p.completed; const gb=$("#gen-btn");
  if(gb){ gb.disabled=done; gb.textContent=done?"완결 ✓":"다음 회차 생성 ▶"; }
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
            ${e.promoted ? '<span class="badge fin">캐논</span>'
              : `<button class="be-promote" onclick="promoteBible('${e.entry_id}')">캐논으로 박기</button>`}
            <button class="be-del" onclick="delBible('${e.entry_id}')">삭제</button>
            <span class="muted small">${esc(e.provenance)}</span></div>
          <div class="be-prose" contenteditable="true"
               onfocus="this.dataset.orig=this.innerText"
               onblur="saveBible('${e.entry_id}', this)">${esc(e.prose)}</div>
        </div>`).join("");
      return `<div class="bible-sec"><h4>${esc(b.category_labels[c]||c)}</h4>${items}</div>`;
    }).join("");
    el.innerHTML = `<div class="bible-toolbar"><button onclick="addBible()">＋ 설정 항목</button>
      <span class="muted small">‘캐논으로 박기’=세계규칙으로 승격(일관성 엔진이 추적·프롬프트 주입). 산문 클릭→편집(포커스 해제 시 저장).</span></div>`
      + (secs || '<p class="muted">설정집이 비어 있습니다. ＋로 추가하세요.</p>');
  }catch(e){ el.innerHTML = `<span class="muted">로드 실패: ${esc(e.message)}</span>`; }
}
async function promoteBible(id){
  try{ const r=await api.post(`/api/projects/${STATE.project.id}/bible/${id}/promote`,{});
       if(r.already) {} loadBible(); loadOntology(); }
  catch(e){ alert("승격 실패: "+e.message); }
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
  }catch(e){ el.style.borderColor = "var(--bad)"; $("#gen-result").innerHTML = `❌ 설정 저장 실패: ${esc(e.message)}`; }
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
  if(!c){ body.classList.add("muted"); body.textContent = "오른쪽에서 다음 회차를 생성하세요."; return; }
  body.classList.remove("muted");
  const badge = c.status==="FINALIZED"?'<span class="badge fin">FINALIZED</span>':'<span class="badge esc">ESCALATED</span>';
  const oc = (c.ontology_changes||[]).map(o=>`<div class="onto-change ${o.op==='new_entity'?'new':o.op==='contradiction'?'con':'chg'}">${o.applied?"✓":"✗"} ${esc(o.entity)}: ${esc(o.detail)}${o.reason?` <span class="muted">(${esc(o.reason)})</span>`:""}</div>`).join("");
  body.innerHTML = `<h4>${c.chapter}화 — ${esc(c.title)} ${badge}</h4>`+
    `<div class="muted small" style="margin-bottom:.8em">장면 ${c.scenes} · 검색 ${c.n_retrieved} · 색인 ${c.indexed_chunks} · wiki+${c.wiki_pages_touched}</div>`+
    (oc?`<div style="margin-bottom:1em">${oc}</div>`:"")+
    `<div>${esc(c.text).replace(/\n/g,"<br>")}</div>`;
}

// ---------- 회차 생성 (SSE 라이브) ----------
function generateNext(){
  if(STATE.generating) return;
  const pid = STATE.project.id;
  const directive = $("#directive").value.trim();
  STATE.generating = true;
  $("#gen-btn").disabled = true;
  $("#harness-log").innerHTML = "";
  $("#gen-result").innerHTML = '<span class="spin"></span> 하네스 실행 중…';
  logEvent({node:"harness",event:"connect"},"");

  const url = `/api/projects/${pid}/generate?directive=${encodeURIComponent(directive)}`;
  const es = new EventSource(url);
  let done = false;
  es.addEventListener("start", e=>{ const d=JSON.parse(e.data); logEvent({node:"plan_chapter",event:`${d.chapter}화 시작`},""); });
  es.addEventListener("event", e=> logEvent(JSON.parse(e.data)));
  es.addEventListener("complete", e=>{ done=true; es.close(); onComplete(JSON.parse(e.data)); });
  es.addEventListener("failed", e=>{ done=true; es.close(); onFail(JSON.parse(e.data)); });
  es.onerror = ()=>{ if(!done){ es.close(); onFail({message:"연결 끊김"}); } };
}
function logEvent(ev, det){
  const cls = ev.event==="escalation"||ev.event==="non_convergence"?"bad"
    : (ev.event==="parse_failure"||ev.event==="signal"||ev.event==="story_truncated"||ev.event==="bible_truncated")?"warn"
    : (ev.event==="done"||ev.event==="new_entity"||ev.event==="relation")?"ok":"";
  let extra = det!==undefined?det:"";
  if(extra===""){
    if(ev.hard!==undefined) extra=`hard=${ev.hard} viol=${ev.violations??""} ${(ev.kinds||[]).join(",")}`;
    else if(ev.scenes!==undefined) extra=`장면 ${ev.scenes}개`;
    else if(ev.indexed!==undefined) extra=`색인 ${ev.indexed} · wiki ${ev.wiki_pages}`;
    else if(ev.goal!==undefined) extra=ev.goal;
    else if(ev.rel!==undefined) extra=`${ev.src}→${ev.dst} (${ev.rel})`;
    else if(ev.entity!==undefined) extra=`${ev.entity} ${ev.attr||ev.etype||""}`;
    else if(ev.fixing!==undefined) extra=`수정: ${(ev.fixing||[]).join(",")}`;
    else if(ev.ground_truth!==undefined) extra=`확정설정 ${ev.ground_truth} · 검색 ${ev.retrieved}`;
    else if(ev.detail!==undefined) extra=ev.detail;
    else if(ev.dropped!==undefined) extra=`오래된 맥락 ${ev.dropped}개 압축`;
  }
  const line = document.createElement("div");
  line.className = `ev ${cls}`;
  const label = `${ev.node}${ev.event&&ev.event!=="done"?"·"+ev.event:""}${ev.scene!==undefined?` s${ev.scene}`:""}${ev.round!==undefined?` r${ev.round}`:""}`;
  line.innerHTML = `<span class="node">${esc(label)}</span><span class="det">${esc(extra)}</span>`;
  const log = $("#harness-log"); log.appendChild(line); log.scrollTop = log.scrollHeight;
}
async function onComplete(data){
  STATE.generating = false; $("#directive").value="";
  if(data.completed){   // R4: 엔딩 도달/하드캡 → 완결
    $("#gen-btn").disabled = true; $("#gen-btn").textContent = "완결 ✓";
    $("#gen-result").innerHTML = `🏁 완결 (${esc(data.reason||"")}) · ${data.current_chapter}화 도달`;
    STATE.project = await api.get(`/api/projects/${STATE.project.id}`);
    renderHeader(); renderChapters();
    if(!$("#tab-arc").classList.contains("hidden")) loadSpine();
    return;
  }
  $("#gen-btn").disabled = false;
  const r = data.record;
  const badge = r.status==="FINALIZED"?'<span class="badge fin">FINALIZED</span>':'<span class="badge esc">ESCALATED 작가 확인 필요</span>';
  const fail = data.failures&&data.failures.length?` · ⚠️ ${data.failures.length} 경보`:"";
  const drift = r.drift_signals&&r.drift_signals.length?` · 🧭 드리프트 ${r.drift_signals.length}`:"";
  const epi = r.episode_id?` · <span class="muted">${esc(r.episode_id)}</span>`:"";
  $("#gen-result").innerHTML = `${r.chapter}화 ${badge}${epi} · +${data.usage_delta.chat_calls}콜${fail}${drift}`;
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
  $("#gen-result").innerHTML = `❌ 생성 실패: ${esc(data.message||"")}`;
  logEvent({node:"harness",event:"failed"}, data.message||"");
}

// ---------- 인스펙터 ----------
async function loadOntology(){
  const el = $("#inspect-onto");
  try{
    const o = await api.get(`/api/projects/${STATE.project.id}/ontology`);
    const chars = o.characters.map(c=>{
      const kv = Object.entries(c.attrs).map(([k,v])=>`<span>${esc(k)}: ${esc(String(v))}</span>`).join("");
      const dead = c.status==="dead"?'<span class="dead">사망</span>':'<span>생존</span>';
      return `<div class="ent"><span class="en-name">${esc(c.name)}</span>${c.provisional?'<span class="prov">동적추가</span>':""}
        <div class="kv">${dead}${kv}</div></div>`;
    }).join("");
    const tl = o.timeline.map(t=>`<div class="rule-item">⏱ ${esc(t.entity)} · ${esc(t.attr)}=${esc(String(t.value))} (${t.eff_from}화~) ${esc(t.reason||"")}</div>`).join("");
    el.innerHTML = `<div class="muted small">시점: ${o.as_of_chapter}화 기준 (결정론 SSOT · '박기')</div>
      ${chars}
      <h4 style="margin-top:.8em">세계 규칙</h4>${o.rules.map(r=>`<div class="rule-item">⚖️ ${esc(r)}</div>`).join("")||'<span class="muted">없음</span>'}
      <h4 style="margin-top:.8em">타임라인</h4>${tl||'<span class="muted">없음</span>'}`;
  }catch(e){ el.innerHTML = `<span class="muted">로드 실패: ${esc(e.message)}</span>`; }
}
async function loadWiki(){
  const el = $("#inspect-wiki");
  try{
    const w = await api.get(`/api/projects/${STATE.project.id}/wiki`);
    const pages = w.pages.filter(p=>p.body).map(p=>`<div class="ent"><span class="en-name">${esc(p.page_id)}</span>
      <span class="prov">${esc(p.page_type)} · ${esc(p.lifecycle)}</span>
      <div class="small" style="margin-top:.3em">${esc(p.body)}</div></div>`).join("");
    const lint = w.lint.map(l=>`<div class="lint-item">⚠️ ${esc(l.kind)}: ${esc(l.entity)} — ${esc(l.text)}</div>`).join("")||'<span class="muted small">lint 경고 없음</span>';
    el.innerHTML = `<div class="muted small">watermark: ${w.watermark}화 · 컴파운딩 위키('찾기', 결정론 lint)</div>
      <h4 style="margin-top:.6em">lint</h4>${lint}
      <h4 style="margin-top:.8em">페이지 (${w.pages.filter(p=>p.body).length})</h4>${pages||'<span class="muted">없음</span>'}`;
  }catch(e){ el.innerHTML = `<span class="muted">로드 실패: ${esc(e.message)}</span>`; }
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
    st.textContent=`노드 ${g.nodes.length} · 관계 ${g.edges.length} (현재 ${g.max_chapter}화 시점, 확정 관계는 다음 회차 ground_truth에 반영)`;
  }catch(e){ st.textContent="그래프 로드 실패: "+esc(e.message); }
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
        'label':'data(label)','color':'#e6e8ee','font-size':'11px','text-valign':'center',
        'text-halign':'center','text-outline-color':'#0b0d12','text-outline-width':2,
        'width':40,'height':40,'border-width':2,'border-color':'#0b0d12'}},
      {selector:'node[dead=1]',style:{'border-color':'#ff6b6b','border-width':3,'opacity':0.6}},
      {selector:'node[prov=1]',style:{'border-style':'dashed','border-color':'#b07cff'}},
      {selector:'node:selected',style:{'border-color':'#7c9cff','border-width':5}},
      {selector:'edge',style:{'width':2,'line-color':'data(color)','target-arrow-color':'data(color)',
        'target-arrow-shape':'data(arrow)','line-style':'data(estyle)','curve-style':'bezier',
        'label':'data(label)','font-size':'9px','color':'#b8c0d0','text-rotation':'autorotate',
        'text-background-color':'#0b0d12','text-background-opacity':0.75,'text-background-padding':2}},
      {selector:'edge[trust="narrative_inferred"]',style:{'line-style':'dashed','opacity':0.5,
        'line-color':'#7d8597','target-arrow-color':'#7d8597','width':1.5}},
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
  $("#graph-sel").textContent=`관계 선택: ${esc(d.label||SELECTED_EDGE.rel_id)} — 종료 가능`;
  $("#rel-end-btn").disabled=false;
}
async function endRelation(){
  if(!SELECTED_EDGE) return;
  try{
    const r=await api.post(`/api/projects/${STATE.project.id}/relations/end`,
      {src_id:SELECTED_EDGE.src,dst_id:SELECTED_EDGE.dst,rel_id:SELECTED_EDGE.rel_id,eff_to:GRAPH_MAX_CH});
    $("#graph-status").textContent=`✅ 관계 종료(${GRAPH_MAX_CH}화부터): ${esc(SELECTED_EDGE.label||'')}`;
    SELECTED_EDGE=null; $("#rel-end-btn").disabled=true; await loadGraph();
  }catch(e){ $("#graph-status").textContent="❌ "+esc(e.message); }
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
  bar.textContent = SELECTED.length===0 ? "노드 2개를 클릭해 선택 →"
    : SELECTED.length===1 ? `출발: ${names[0]} → (대상 선택)` : `${names[0]} → ${names[1]}`;
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
    $("#graph-status").textContent=r.created?`✅ 관계 추가: ${esc(r.label)}`:"이미 있는 관계입니다";
    SELECTED=[]; await loadGraph();
  }catch(e){ $("#graph-status").textContent="❌ "+esc(e.message); }
}
async function promptAddEntity(){
  const name=prompt("새 노드 이름 (인물/세력/장소 등):"); if(!name) return;
  const etype=((prompt("타입 키 (character / faction / place / item / event ...):","character")||"character").trim())||"character";
  try{
    const r=await api.post(`/api/projects/${STATE.project.id}/entities`,{name,etype});
    $("#graph-status").textContent=r.created?`✅ 노드 추가: ${esc(name)}`+(r.unknown_type?" (미등록 타입→기본 모양)":""):"이미 있는 이름";
    await loadGraph();
  }catch(e){ $("#graph-status").textContent="❌ "+esc(e.message); }
}
function renderLegend(types){
  $("#graph-legend").innerHTML=(types||[]).map(x=>
    `<span class="lg"><span class="dot" style="background:${esc(x.color)}"></span>${esc(x.label)}</span>`).join("")
    + ` <span class="lg"><span class="dot" style="background:#ff6b6b"></span>사망</span>`
    + ` <span class="lg"><span class="dot" style="background:#b07cff"></span>동적/작가추가</span>`
    + ` <span class="lg">┄ 점선=추정 관계(작가 확정 전)</span>`;
}

// ---------- 유틸 ----------
function esc(s){ return String(s==null?"":s).replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m])); }
async function health(){ try{ const h=await api.get("/api/health"); $("#health").textContent=`${h.provider} · ${h.model}`; }catch(e){} }

health(); loadProjects();
