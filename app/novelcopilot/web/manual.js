/* ===== NovelCopilot 문서 포털 — 클라이언트 로직 =====
   - 좌측 사이드바 항목 클릭 → /api/docs/{name} 로드 → 본문 렌더
   - URL 해시(#getting-started 등)로 딥링크·새로고침 복원
   - 마크다운 렌더: 벤더링한 marked.min.js(MIT, /vendor/) 사용. 전역 부재 시 경량 폴백 렌더러.
   - 문서 내 상대 링크(guide/faq.md·../CHANGELOG.md 등)는 해시 네비로 재작성, 외부 http 링크는 새 탭.
   외부 CDN·네트워크 콜 0(문서 로드만 자체 /api). */
(function () {
  "use strict";

  // 사이드바 = 화이트리스트(routes.py _DOCS_WHITELIST 와 동일 키·순서). divider = 섹션 제목(링크 아님).
  // IA 계약(2026-07-14 개편 — 5섹션·17문서). 키·라벨·순서는 라우트와 1:1.
  var DOCS = [
    { divider: "시작하기" },
    { id: "overview", label: "소개" },
    { id: "getting-started", label: "빠른 시작" },
    { id: "setup", label: "설치와 실행" },
    { divider: "사용 가이드" },
    { id: "create-work", label: "작품 만들기" },
    { id: "write-chapters", label: "회차 쓰기" },
    { id: "read-verification", label: "검증 리포트 읽기" },
    { id: "refine-chapters", label: "회차 다듬기" },
    { id: "manage-assets", label: "작품 설정 관리" },
    { id: "serial-operations", label: "연재 운영" },
    { id: "export-backup", label: "내보내기와 백업" },
    { divider: "개념" },
    { id: "how-it-works", label: "작동 방식" },
    { divider: "레퍼런스" },
    { id: "settings", label: "설정 레퍼런스" },
    { id: "faq", label: "FAQ" },
    { id: "troubleshooting", label: "문제 해결" },
    { id: "release-notes", label: "릴리스 노트" },
    { divider: "기술 문서 (심화)" },
    { id: "pipeline", label: "파이프라인 명세" },
    { id: "architecture", label: "아키텍처" }
  ];
  var IDS = DOCS.filter(function (d) { return d.id; }).map(function (d) { return d.id; });

  // 문서 내 상대 링크(경로 조각) → 사이드바 id 매핑. 표준화한 경로 꼬리로 판정.
  // 신 파일명 전부 + 구→신 리다이렉트(파일 소멸/경로 이관으로 깨질 링크 보정).
  var LINK_MAP = {
    // 시작하기
    "introduction.md": "overview",
    "guide/introduction.md": "overview",
    "getting-started.md": "getting-started",
    "guide/getting-started.md": "getting-started",
    "setup.md": "setup",
    "guide/setup.md": "setup",
    // 사용 가이드
    "create-work.md": "create-work",
    "guide/create-work.md": "create-work",
    "write-chapters.md": "write-chapters",
    "guide/write-chapters.md": "write-chapters",
    "read-verification.md": "read-verification",
    "guide/read-verification.md": "read-verification",
    "refine-chapters.md": "refine-chapters",
    "guide/refine-chapters.md": "refine-chapters",
    "manage-assets.md": "manage-assets",
    "guide/manage-assets.md": "manage-assets",
    "serial-operations.md": "serial-operations",
    "guide/serial-operations.md": "serial-operations",
    "export-backup.md": "export-backup",
    "guide/export-backup.md": "export-backup",
    // 개념
    "how-it-works.md": "how-it-works",
    "guide/how-it-works.md": "how-it-works",
    // 레퍼런스
    "settings-reference.md": "settings",
    "guide/settings-reference.md": "settings",
    "faq.md": "faq",
    "guide/faq.md": "faq",
    "troubleshooting.md": "troubleshooting",
    "guide/troubleshooting.md": "troubleshooting",
    "release-notes.md": "release-notes",
    "guide/release-notes.md": "release-notes",
    // 기술 문서
    "pipeline.md": "pipeline",
    "architecture.md": "architecture",
    // 구→신 리다이렉트(파일 소멸/이관 — 링크 깨짐 방지)
    "service-overview.md": "overview",          // SERVICE-OVERVIEW.md → guide/introduction.md 이관
    "user-guide.md": "create-work",             // 분리 소멸: 사용 가이드 첫 문서로 안내
    "guide/user-guide.md": "create-work",
    "changelog.md": "release-notes"             // CHANGELOG.md 포털 밖 강등 → 릴리스 노트로
  };

  var navEl = document.getElementById("side-nav");
  var docEl = document.getElementById("doc");
  var statusEl = document.getElementById("doc-status");

  // ---- 사이드바 렌더 ----
  DOCS.forEach(function (d) {
    if (d.divider) {   // 섹션 제목(링크 아님)
      var h = document.createElement("div");
      h.className = "side-divider";
      h.textContent = d.divider;
      navEl.appendChild(h);
      return;
    }
    var a = document.createElement("a");
    a.className = "side-link";
    a.href = "#" + d.id;
    a.textContent = d.label;
    a.dataset.id = d.id;
    navEl.appendChild(a);
  });

  function setActive(id) {
    var links = navEl.querySelectorAll(".side-link");
    for (var i = 0; i < links.length; i++) {
      links[i].classList.toggle("active", links[i].dataset.id === id);
    }
  }

  // ---- 마크다운 렌더 (marked 우선, 폴백 자체 렌더러) ----
  function renderMarkdown(md) {
    if (window.marked && typeof window.marked.parse === "function") {
      // marked v12: GFM(표 지원 포함). 산출 HTML 은 우리 문서(신뢰 SSOT)만 대상.
      return window.marked.parse(md, { gfm: true, breaks: false });
    }
    return fallbackRender(md);
  }

  // 렌더 후처리: 표를 스크롤 래퍼로 감싸고, 링크를 해시 네비/새 탭으로 재작성.
  function postProcess(container) {
    // 표 가로 스크롤 래퍼
    var tables = container.querySelectorAll("table");
    for (var i = 0; i < tables.length; i++) {
      var t = tables[i];
      if (t.parentElement && t.parentElement.classList.contains("table-wrap")) continue;
      var wrap = document.createElement("div");
      wrap.className = "table-wrap";
      t.parentNode.insertBefore(wrap, t);
      wrap.appendChild(t);
    }
    // 링크 재작성
    var links = container.querySelectorAll("a[href]");
    for (var j = 0; j < links.length; j++) {
      rewriteLink(links[j]);
    }
  }

  function rewriteLink(a) {
    var href = a.getAttribute("href") || "";
    if (!href) return;
    // 순수 앵커(#...) — 문서 내 헤딩. 사이드바 id 면 문서 네비로.
    if (href.charAt(0) === "#") {
      var frag = href.slice(1).toLowerCase();
      if (IDS.indexOf(frag) >= 0) { a.setAttribute("href", "#" + frag); }
      return;
    }
    // 외부 링크 → 새 탭
    if (/^https?:\/\//i.test(href) || /^mailto:/i.test(href)) {
      a.setAttribute("target", "_blank");
      a.setAttribute("rel", "noopener noreferrer");
      return;
    }
    // 상대 링크 → 사이드바 문서면 해시 네비, 아니면 비활성(포털 밖 문서)
    var key = normalizeRelPath(href);
    var mapped = LINK_MAP[key];
    if (mapped) {
      a.setAttribute("href", "#" + mapped);
    } else {
      // 포털에 없는 내부 문서(ARCHITECTURE.md 등) — 죽은 링크로 두지 않고 안내 처리
      a.setAttribute("href", "#" + getCurrent());
      a.setAttribute("title", "이 문서는 포털에 포함되지 않습니다");
      a.style.color = "var(--muted)";
      a.style.textDecoration = "underline dotted";
    }
  }

  // "../CHANGELOG.md#v0.9.0" → "changelog.md" 형태의 매핑 키로 정규화
  function normalizeRelPath(href) {
    var p = href.split("#")[0].split("?")[0];       // 프래그먼트·쿼리 제거
    p = p.replace(/^\.\//, "").replace(/^(\.\.\/)+/, ""); // ./ 와 앞쪽 ../ 제거
    p = p.toLowerCase();
    if (LINK_MAP[p]) return p;
    // 경로 꼬리 1~2조각으로 재시도(guide/faq.md, faq.md)
    var parts = p.split("/");
    var tail1 = parts[parts.length - 1];
    var tail2 = parts.length >= 2 ? parts[parts.length - 2] + "/" + tail1 : tail1;
    if (LINK_MAP[tail2]) return tail2;
    if (LINK_MAP[tail1]) return tail1;
    return p;
  }

  // ---- 로드 ----
  function getCurrent() {
    var h = (location.hash || "").replace(/^#/, "").toLowerCase();
    return IDS.indexOf(h) >= 0 ? h : "overview";
  }

  function showStatus(msg, isErr, retryId) {
    docEl.innerHTML = "";
    statusEl.className = "doc-status" + (isErr ? " err" : "");
    statusEl.textContent = msg;
    statusEl.style.display = "";
    if (retryId) {
      var box = document.createElement("div");
      box.className = "retry";
      var btn = document.createElement("button");
      btn.textContent = "다시 시도";
      btn.onclick = function () { load(retryId); };
      box.appendChild(btn);
      statusEl.appendChild(box);
    }
  }

  var loadToken = 0;
  function load(id) {
    var token = ++loadToken;
    setActive(id);
    showStatus("문서를 불러오는 중…", false, null);
    fetch("/api/docs/" + encodeURIComponent(id), { headers: { "Accept": "application/json" } })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (data) {
        if (token !== loadToken) return;   // 경쟁 로드 방지(최신만 반영)
        var md = (data && typeof data.markdown === "string") ? data.markdown : "";
        statusEl.style.display = "none";
        docEl.innerHTML = renderMarkdown(md);
        postProcess(docEl);
        // 문서 전환·해시 진입 시 상단부터 읽도록
        window.scrollTo(0, 0);
        document.title = titleFor(id) + " — NovelCopilot";
      })
      .catch(function (err) {
        if (token !== loadToken) return;
        showStatus("문서를 불러오지 못했습니다 (" + (err && err.message ? err.message : "오류") + "). 서버가 실행 중인지 확인해 주세요.", true, id);
      });
  }

  function titleFor(id) {
    for (var i = 0; i < DOCS.length; i++) if (DOCS[i].id === id) return DOCS[i].label;
    return "문서";
  }

  window.addEventListener("hashchange", function () { load(getCurrent()); });

  // 초기 진입: 유효 해시 없으면 overview 를 명시 해시로 기입(딥링크 일관성)
  var initHash = (location.hash || "").replace(/^#/, "").toLowerCase();
  if (IDS.indexOf(initHash) < 0) {
    history.replaceState(null, "", "#" + getCurrent());
  }
  load(getCurrent());

  // ---- 경량 폴백 렌더러 (marked 부재 시) ----
  // 지원: 제목/굵게/기울임/링크/인라인 코드/코드블록/목록/표/인용/구분선. 표 지원 필수(우리 문서 다수 사용).
  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  // PUA 센티널(U+E000/E001) — 본문에 등장하지 않는 코드포인트. fromCharCode 로 생성해 소스에 비ASCII 리터럴 0.
  var CODE_OPEN = String.fromCharCode(0xE000);
  var CODE_CLOSE = String.fromCharCode(0xE001);
  function inline(s) {
    // 인라인 코드는 먼저 PUA 센티널로 빼서 내부를 원문 보존(마크업 오인 방지)
    var codes = [];
    s = s.replace(/`([^`]+)`/g, function (_, c) {
      codes.push(c); return CODE_OPEN + (codes.length - 1) + CODE_CLOSE;
    });
    s = esc(s);
    // 링크 [t](u)
    s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function (_, t, u) {
      return '<a href="' + u.trim().replace(/"/g, "%22") + '">' + t + "</a>";
    });
    // 굵게 **..** / __..__
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/__([^_]+)__/g, "<strong>$1</strong>");
    // 기울임 *..* (굵게 처리 후)
    s = s.replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
    // 코드 센티널 복원 → <code>
    s = s.replace(new RegExp(CODE_OPEN + "(\\d+)" + CODE_CLOSE, "g"), function (_, i) {
      return "<code>" + esc(codes[+i]) + "</code>";
    });
    return s;
  }
  function splitRow(line) {
    var s = line.trim().replace(/^\|/, "").replace(/\|$/, "");
    return s.split("|").map(function (c) { return c.trim(); });
  }
  function fallbackRender(md) {
    var lines = md.replace(/\r\n?/g, "\n").split("\n");
    var out = [], i = 0;
    function flushList(tag, items) {
      if (!items.length) return;
      out.push("<" + tag + ">" + items.map(function (x) { return "<li>" + inline(x) + "</li>"; }).join("") + "</" + tag + ">");
    }
    while (i < lines.length) {
      var line = lines[i];
      // 코드블록 ```
      if (/^```/.test(line)) {
        var buf = [];
        i++;
        while (i < lines.length && !/^```/.test(lines[i])) { buf.push(lines[i]); i++; }
        i++; // 닫는 ```
        out.push("<pre><code>" + esc(buf.join("\n")) + "</code></pre>");
        continue;
      }
      // 구분선
      if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { out.push("<hr>"); i++; continue; }
      // 헤딩
      var h = /^(#{1,6})\s+(.*)$/.exec(line);
      if (h) { var lv = h[1].length; out.push("<h" + lv + ">" + inline(h[2]) + "</h" + lv + ">"); i++; continue; }
      // 인용
      if (/^>\s?/.test(line)) {
        var q = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) { q.push(lines[i].replace(/^>\s?/, "")); i++; }
        out.push("<blockquote>" + q.map(function (x) { return "<p>" + inline(x) + "</p>"; }).join("") + "</blockquote>");
        continue;
      }
      // 표: 헤더행 + 구분행(|---|)
      if (/\|/.test(line) && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1]) && /-/.test(lines[i + 1])) {
        var header = splitRow(line);
        i += 2; // 헤더 + 구분
        var rows = [];
        while (i < lines.length && /\|/.test(lines[i]) && lines[i].trim() !== "") {
          rows.push(splitRow(lines[i])); i++;
        }
        var th = "<tr>" + header.map(function (c) { return "<th>" + inline(c) + "</th>"; }).join("") + "</tr>";
        var tb = rows.map(function (r) {
          return "<tr>" + r.map(function (c) { return "<td>" + inline(c) + "</td>"; }).join("") + "</tr>";
        }).join("");
        out.push("<table><thead>" + th + "</thead><tbody>" + tb + "</tbody></table>");
        continue;
      }
      // 목록(순서 없음 -,*,+ / 순서 1.)
      if (/^\s*[-*+]\s+/.test(line)) {
        var ul = [];
        while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) { ul.push(lines[i].replace(/^\s*[-*+]\s+/, "")); i++; }
        flushList("ul", ul);
        continue;
      }
      if (/^\s*\d+\.\s+/.test(line)) {
        var ol = [];
        while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) { ol.push(lines[i].replace(/^\s*\d+\.\s+/, "")); i++; }
        flushList("ol", ol);
        continue;
      }
      // 빈 줄
      if (line.trim() === "") { i++; continue; }
      // 문단(연속 비빈 줄 합침)
      var para = [line]; i++;
      while (i < lines.length && lines[i].trim() !== "" && !/^(#{1,6}\s|>\s?|```|\s*[-*+]\s|\s*\d+\.\s)/.test(lines[i]) && !/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(lines[i])) {
        para.push(lines[i]); i++;
      }
      out.push("<p>" + inline(para.join(" ")) + "</p>");
    }
    return out.join("\n");
  }
})();
