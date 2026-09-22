# -*- coding: utf-8 -*-
"""스토리 패스 프롬프트 v3 — 2026-08-14 prompt-auditor 반려(치명 7·중대 13) 전면 반영판.
단일 출처: 문안 상수·금지어·결정론 검사·라이브 재료 렌더를 이 모듈 하나가 쥔다.
러너(story_bon_v3.py / sp_finalize_v3.py)는 여기서 import만 한다.

감사 반영 핵심:
 · 공통 [형식] 블록을 5개 변환 콜 전부에 동일 바이트로 재명시 (F1·F2·F4)
 · 결정론 검사 0개 → 8종 배선: 불릿 시작·따옴표 0·과거형 종결 0·줄 수 범위·
   병합 줄 수 대조·인용 substring 대조·자리 정수 범위·본문 자수 하한+말미 종결 (F18·F3)
 · 자기 이력(1화 해법)은 생성 콜에서 제거, 심문 콜 판정 자료로만 (F5)
 · 문안 전체 em dash 0·부정 지시 0·형태 메뉴 호명 0 (F6·F7·F11)
 · 재료는 라이브 캐논에서 렌더 (F19 — 하드코딩 '금 간 손거울'이 정본 '깨진 손거울'서
   이탈해 있던 실측이 이 원칙의 근거)
 · 병합 자동 채택 제거: 두 안 모두 작가 게이트로 (F13)
 · 리뷰는 JSON 변환 지시 쌍으로 고정, 산문 리뷰의 기획서 말투 유입 차단 (F16)
 · 심문의 부재 증명 항목을 최상급 질문으로 반전(항상 인용 가능) (F15)
"""
import re

# ── 금지어 단일 상수 (F17: 두 파일이 서로 다른 부분집합을 들고 있던 결함 통합)
BAN = ['독무', '오랏줄', '경면', '술사', '시술', '굶겨', '물듦', '표류자', '부른 그릇',
       '두루마기', '짚신', '갓끈', '도포', '한복', '저고리', '곰방대']

# 사후 감사 전용 — 프롬프트에 노출 금지(RB-6). 중간 산출물의 설계 용어 오염 검출.
# 재감사 M7: '훅'(부사)·'변수' 등은 정상 한국어라 순수 substring은 위양성 — 라벨꼴
# (줄머리 '단어:')만 잡고, substring은 구조 표지 3종으로 한정한다.
DESIGN_WORDS = ['###', '소제목', '간판 사건']
DESIGN_LABEL_RE = re.compile(r"(?m)^\s*[-*·]?\s*(변수|훅|복선|전환점|최저점|아이러니)\s*[:：]")

# ── 공통 형식 계약 (사양 story-pass-sp: 모든 변환 콜에 동일 바이트로 재명시)
FORMAT_CORE = (
    "· 각 줄은 '- '로 시작하는 한 개의 장면이다.\n"
    "· 각 줄은 지금 벌어지는 일을 현재형 한두 문장으로 적는다.\n"
    "· 인물이 주고받은 말은 그 인물이 무엇을 전하고 무엇을 얻어내는지로 옮겨 서술 문장 안에 녹인다.\n"
    # VH-1(2026-08-16 감사 F-2): 불릿의 규정 문면이 본문 대사로 직역되는 채널 차단(5화 '미끄러져요'·
    #   6화 '증빙 불가 개체…' 2회 재발 → 설계 승격). 5개 변환 콜 동일 바이트(상수 공유)로 재명시된다.
    "· 서류·서식·규정에 적힌 문면은 그 자리에서 인물이 무엇을 정했고 무엇이 걸렸는지로 옮겨 적는다.\n"
    "· 각 줄은 사건의 서술로만 이루어진다.\n"
    "· 읽으면 그대로 짧은 이야기로 흐르게 쓴다.")
FORMAT = "[형식]\n" + FORMAT_CORE + "\n출력은 이 줄들만."

# ── 1) 설계 (F4: 계약 완비 / F5: 자기 이력 제거 / F6·F7: 대시·부정 지시 제거)
# SP-2(2026-08-18 사용자 확정 + 감사 반려 반영): 고정 각도 3종 폐지 → 화 역할(role) 주도.
#   ⓐ '간판 사건' 어구는 DESIGN_WORDS 검출어와 교집합이라 프롬프트에서 '중심 사건'으로 전환(감사 M3 —
#     검출어를 생성 문형째 시연하면 산출 복제·재변환 콜 증가). ⓑ 구 ⑥(모든 사건은 대가 또는 실패)은
#     대가 래칫의 소스(PM 특정·패널 "뭘 뜯어가도 놀랍지 않다") — 역할 슬롯 참조로 축약(감사 C5),
#     role_line 없이도 자립하는 문장(하위호환 ⓐ).
BASE_CORE = (
    "너는 웹소설 스토리 설계자다. 아래 재료로 이번 화 스토리를 만들어라.\n\n"
    "설계 순서(먼저 속으로 정하고, 출력은 아래 형식의 줄 목록만):\n"
    "① 이 화의 중심 사건 하나. 나머지 사건은 중심을 받치는 배치다.\n"
    "② 이 화의 아이러니 한 줄. [작품 전제]에서 이 작품의 원 아이러니를 읽어내 이 화에 장전하라.\n"
    "③ 주인공이 무엇을 걸고 무엇을 고르는지 한 대목에 못박아라.\n"
    "④ 전환점과 최저점. 이 화에서 가장 나쁜 순간이 어디인지 먼저 정하고, 거기로 꺾이는 전환점을 심어라.\n"
    "⑤ 해소 지연. 중심 사건이 풀리기 직전, 다 됐다 싶은 순간에 한 번 더 어긋난다.\n"
    "⑥ 이 화의 사건들은 이 화가 하기로 한 일에 맞게 닫는다. 무엇이 닫히고 무엇이 열린 채 남는지가 줄에서 보이게 써라.\n"
    "⑦ 줄과 줄은 '그래서' 또는 '그러나'의 관계로 이어져야 한다. 앞 사건의 결과가 다음 사건의 원인이 되게.\n"
    "⑧ 위협과 유머는 물성 있게. 구체적 지목, 구체적 소재.\n\n"
    "시간은 접어도 된다. 이동·행정·확인은 한 줄로 접고 중심 사건에 분량을 몰아라.")
# RC-1 ⓓ: 줄 수 계약을 슬롯 예산으로 스케일할 수 있게 함수화. 기본(lo=12·hi=16)은 종전 상수와 바이트 동일 —
#   OUT_CONTRACT 는 그 기본값 상수로 유지(미분배·플래그 OFF 경로 바이트 불변). 슬롯 예산이 주어질 때만 스케일된 계약이 실린다.
def build_out_contract(lo: int = 12, hi: int = 16) -> str:
    return f"분량: 줄 {lo}~{hi}개.\n" + FORMAT

OUT_CONTRACT = build_out_contract()   # = "분량: 줄 12~16개.\n" + FORMAT (기존 상수 바이트 동일)

# 재감사 2차 M-L: 프로즈 층 hook(미결에서 끊기)과 커버리지 계약(마지막 줄까지 실현)의
# 교차 충돌 해소 — 마지막 줄의 성격을 작품 결말 정책(ending_hook, 작품 데이터 주도)과
# 연동해 스토리 층에서 긍정형으로 지정한다. 정책 미설정·none이면 무추가(바이트 동일).
_ENDING_LINES = {
    "cliffhanger": "마지막 줄은 이 화가 쌓아온 긴장에서 자라난, 다음 화로 넘어가는 미결의 순간이다.",
    "soft": "마지막 줄은 이 화의 감정과 상황이 자연스러운 여운으로 가라앉는 순간이다.",
}

# 3차 감사 M2: 두 빌더의 기본값을 ""(무추가)로 대칭 — 인자 누락 시 설계·수정 콜의 결말
# 계약이 어긋나는(한쪽만 실리는) 사슬 증발을 구조로 차단. 배선은 항상 style.ending_hook을 넘긴다.
# SP-2: role_line 슬롯 — 있으면 [이 화의 몫] 블록으로 얹는다(⑥이 이 라벨을 자립형으로 참조).
def build_base_sys(ending_hook="", role_line="", out_contract=None):
    # RC-1 ⓓ: out_contract 미전달(None)=OUT_CONTRACT 상수(바이트 동일). 슬롯 예산이 있으면 스케일된 계약을 넘긴다.
    oc = out_contract if out_contract is not None else OUT_CONTRACT
    line = _ENDING_LINES.get(ending_hook or "", "")
    role_block = (f"\n\n[이 화의 몫]\n{role_line}" if role_line else "")
    return BASE_CORE + (("\n" + line) if line else "") + role_block + "\n\n" + oc

# SP-2(감사 C1·M1·M3 반영): 화 역할 6종 — 렌즈 명제가 아니라 '이 화가 하는 일' 선언 + 중심 사건 지정.
#   genre-blind 중립 기본값(작품 고유명 0 — hygiene 스윕이 잠근다). 값·정산·지불 어휘 0(돈-가치 축 배제).
#   3안 다양성은 role_line 공유 + 서로 다른 {event}(사건 메뉴에서 결정론 배정)로 만든다.
ROLES = {
    "위기": "이 화는 위협이 조여 오는 화다. 이 화의 중심은 다음 사건 하나다: {event}. 이 사건이 인물들의 선택지를 좁히는 과정이 이 화의 구경거리다.",
    "꼼수": "이 화는 규칙을 제 방식으로 뒤집는 화다. 이 화의 중심은 다음 사건 하나다: {event}. 뒤집는 데 쓰는 것은 이미 등장한 규칙과 소품이다.",
    "회수": "이 화는 앞서 남겨둔 것이 돌아오는 화다. 이 화의 중심은 다음 사건 하나다: {event}. 돌아온 것이 앞의 어느 대목에서 비롯됐는지가 이 화의 구경거리다.",
    "조사": "이 화는 규칙의 정체를 캐는 화다. 이 화의 중심은 다음 사건 하나다: {event}. 알아낸 것 하나가 다음 선택을 바꾸는 데까지가 이 화의 몫이다.",
    "관계": "이 화는 두 사람의 합이 드러나는 화다. 이 화의 중심은 다음 사건 하나다: {event}. 사건은 두 사람이 서로를 읽는 무대다.",
    "휴지": "이 화는 숨을 고르는 화다. 이 화의 중심은 다음 사건 하나다: {event}. 일상의 결과 작은 되갚음이 이 화의 몫이다.",
}

# SP-2(감사 M5): 역할 → 5번째 심문 축. 강등 루프·기둥 카운트가 이 상수 하나를 읽는다(장식 계약 차단).
COMMON_AUDIT_AXES = ("중심사건", "주인공선택", "아이러니", "해소지연")
ROLE_AUDIT_AXES = {
    "위기": "비용", "꼼수": "비용",
    "회수": "회수", "조사": "전진", "관계": "합", "휴지": "남긴것",
}

def audit_axes(role: str) -> tuple:
    return COMMON_AUDIT_AXES + (ROLE_AUDIT_AXES.get(role, "비용"),)

# ── 2) 블라인드 리뷰 (F16: JSON 변환 지시 쌍으로 고정 — 산문 리뷰의 설계 용어 말투가
#     수정 콜의 프로즈 앵커로 유입되던 경로 차단. 기획서 말투 오염 실측)
# SP-2(감사 C3): 리뷰 콜에는 화 역할이 안 실리므로 역할 무관 축으로 — '비용' 어휘 제거(래칫의 리뷰 축
#   이전 차단). 몫 판정은 심문(역할 축)이 전담한다.
REVIEW_SYS = (
    "너는 웹소설 동료 작가다. 익명 동료가 쓴 이번 화 스토리 초안을 블라인드 리뷰한다. 지적만 쓴다.\n"
    "반드시 다룰 것: 가장 재미없는 대목, 해소가 급하게 풀리는 곳, 풀리는 과정이 가장 쉽게 넘어가는 대목, "
    "앞뒤 인과가 가장 약한 이음, 사건 수준의 수정 제안.\n"
    'JSON: {"지적":[{"대상":"초안 원문 인용","방향":"무엇을 무엇으로 바꿀지 한 줄"}]} 지적은 5개.')

def render_review(review_json, draft):
    """리뷰 JSON을 편집 지시 쌍 텍스트로 렌더(헌법 1조 예외 ⓑ: 수술 지시 형태).
    재감사 M3: 인용이 초안 원문과 대조 실패하는 지적(환각 인용)은 렌더에서 제외 —
    검증 안 된 문장이 수정 콜(gen)의 앵커로 들어가는 채널 차단. 반환: (텍스트, 제외 수)."""
    rows = [r for r in ((review_json or {}).get("지적") or []) if isinstance(r, dict)]
    kept = [r for r in rows if cite_ok(r.get("대상", ""), draft)]
    text = "\n".join(f"- 고칠 곳: {r.get('대상', '')}\n  바꿀 방향: {r.get('방향', '')}" for r in kept)
    return text, len(rows) - len(kept)

# ── 3) 독립 수정 (계약 말미 재명시 — REV에서 FORMAT이 빠지면 그 콜에서 무너진다)
# SP-2(감사 M4): '설계 각도' dangling 제거 — 필터 기준은 [이 화의 몫]과 이 초안의 중심 사건.
def build_rev_sys(role_line="", ending_hook="", out_contract=None):
    # RC-1 ⓓ: out_contract 미전달(None)=OUT_CONTRACT 상수(바이트 동일). 슬롯 예산이 있으면 스케일된 계약을 넘긴다.
    oc = out_contract if out_contract is not None else OUT_CONTRACT
    line = _ENDING_LINES.get(ending_hook or "", "")
    role_block = (f"\n\n[이 화의 몫]\n{role_line}" if role_line else "")
    return (BASE_CORE + (("\n" + line) if line else "") + role_block + "\n\n"
            "아래 [수정 지시]에서 [이 화의 몫]과 이 초안의 중심 사건에 맞는 것만 골라 초안을 독립적으로 수정하라.\n\n"
            + oc)

# ── 4) 기준 심문 (F15: 부재 증명 항목을 최상급 질문으로 반전 — '가장 ~한 한 줄'은
#     항상 존재하므로 인용 의무가 성립한다. 1화 해법은 판정 자료로만 유저 블록에 동봉)
# SP-2: 심문표를 역할 조건부로 — 공통 4축 + 역할 축 1. '괴담'→'사건' 중립화(감사 C1 덤),
#   재탕근접은 3필드(감사 m1 — '다른 국면'은 판정자가 구조차이에 써서 증명, 부정문 0).
_AXIS_SPECS = {
    "비용": '"비용":{"이행":true,"인용":""}',
    "회수": '"회수":{"이행":true,"인용":"돌려받는 순간의 스토리 한 줄","출처인용":"[지난 이야기]에서 그것이 마련된 한 줄"}',
    "전진": '"전진":{"이행":true,"인용":"알아낸 것이 다음 선택을 바꾸는 한 줄"}',
    "합": '"합":{"이행":true,"인용":"한 사람이 다른 사람의 다음 수를 읽고 움직이는 한 줄"}',
    "남긴것": '"남긴것":{"이행":true,"인용":"이 화의 정비가 앞으로 쓰일 무엇을 남기는 한 줄"}',
}

def build_audit_sys(role: str = "") -> str:
    role_axis = ROLE_AUDIT_AXES.get(role, "비용")
    return (
        "너는 스토리 감사관이다. 스토리가 기준을 이행했는지 스토리 원문 인용으로만 판정하라. 인용을 못 대면 미이행이다.\n"
        'JSON: {"중심사건":{"이행":true,"인용":""},"주인공선택":{"이행":true,"인용":""},'
        + _AXIS_SPECS[role_axis] + ','
        '"아이러니":{"이행":true,"인용":""},"해소지연":{"이행":true,"인용":""},'
        '"가장약한이음":{"인용":"앞뒤가 인과 없이 이어지는 곳 중 가장 약한 한 줄","이유":"한 줄"},'
        '"재탕근접":{"인용":"[1화 상세]의 해법과 푸는 구조(무엇으로 무엇을 풀었는가)가 가장 비슷한 이 스토리의 한 줄",'
        '"구조차이":"그 줄이 1화 해법과 달라진 점 한 줄",'
        '"고유출처":"그 해법이 이 화 사건의 어떤 고유 규칙에서 나왔는지 한 줄"}}')

AUDIT_SYS = build_audit_sys("")   # 구 호출 호환(기본 비용 축)

def build_audit_user(story, syn1, syn_prev=""):
    # SP-2(감사 C4): 회수 축 '출처인용'은 [지난 이야기]와 대조 — 스토리 단일 소스로는 구조적 대조 실패.
    prev_block = (f"\n\n[지난 이야기(판정 자료 전용)]\n{syn_prev}" if syn_prev else "")
    return f"[스토리]\n{story}\n\n[1화 상세(판정 자료 전용)]\n{syn1}" + prev_block

# ── 5) 쌍대 판정 (F14: 인용 의무 + 정독 체감 기준 명시. '웹소설 헤비 독자' 프라이어
#     소환 제거 — webnovel-style-priors-are-wrong)
PAIR_SYS = (
    "두 화 스토리를 처음부터 끝까지 읽고, 다음 화를 더 읽고 싶어지는 쪽을 골라라. "
    "판정 기준은 정독 체감이다. 비등 허용.\n"
    'JSON: {"pick":"A|B|비등","A_인용":"판단을 가른 A의 한 대목","B_인용":"판단을 가른 B의 한 대목","why":"한 줄"}')

# ── 6) 이식 스캔 (컷=원문 한 줄 그대로 → substring 검사 가능. 자리=정수 지정 의무)
SCAN_SYS = (
    "너는 스토리 편집자다. [기둥 스토리]와 [후보 스토리]를 비교해, 후보에만 있는 차별 자산"
    "(독창적 사건·컷)을 최대 3개 뽑고 각각 이식 가능성을 심사하라.\n"
    "관문 ⓐ: 이식해도 기둥의 중심 사건과 감정선이 그대로 유지되는가.\n"
    "관문 ⓑ: 기둥의 몇 번째 줄 뒤에 '그래서' 또는 '그러나' 관계로 접붙는지 자리를 지정할 수 있는가.\n"
    "컷은 [후보 스토리]의 한 줄을 그대로 옮겨 적는다.\n"
    'JSON: {"assets":[{"컷":"후보 원문 한 줄","관문a":true,"관문b":true,"자리":"기둥 줄 N 뒤","근거":"한 줄"}]}')

# ── 7) 병합 시공 (F1: 형식 계약 재명시 — 이 콜의 계약 부재가 확정 스토리 전면 붕괴의
#     원인 콜로 특정됨. 검사: 병합 줄 수 = 기둥 줄 수 + 이식 건수)
MERGE_SYS = (
    "너는 스토리 시공자다. [기둥]의 줄과 순서를 그대로 두고, [이식 명세]의 컷을 지정된 자리에 "
    "한 줄씩 새로 끼워 넣어라. 끼워 넣은 줄과 앞뒤 줄의 이음은 '그래서' 또는 '그러나'가 "
    "드러나게 다듬어라.\n\n" + FORMAT)

# ── 8) 확정 검사·접합 (F2: 직전 화 발췌에 참조 전용 라벨 — 엔진 AN-1a와 동형.
#     재감사 M6: 판정과 생성을 콜 분리 — 판정자가 '미소화' 선언으로 제 쓸거리를 만드는
#     인센티브 오염 + 교차 벤더 문체 줄이 확정 스토리에 섞이는 채널을 함께 차단)
CHECK_SYS = (
    "너는 연속성 검사관이다. [직전 화 말미]는 직전 상태를 확인하는 참조다. [확정 스토리] 초반이 "
    "그 미해결 순간(끝나지 않은 행동·대사·이상 징후)을 소화하는지 검사하라. "
    "소화되면 해당 스토리 줄을 그대로 인용하라.\n"
    'JSON: {"소화됨":true,"인용":"..."}')

JOINT_SYS = (
    "너는 스토리 시공자다. [직전 화 말미]는 직전 상태를 확인하는 참조다. "
    "이번 화의 첫 사건이 그 직전 상태에서 출발하는 지점을 새 줄 1~2개로 적어라. "
    "새 줄은 이번 화의 새 사건을 새 문장으로 적는다.\n"
    "[새 줄 형식]\n" + FORMAT_CORE + "\n출력은 이 줄들만.")

REF_LABEL = "이 발췌는 직전 상태를 확인하는 참조다. 확인한 내용은 이번 화의 새 사건과 새 문장으로 쓴다."

# ── 9) 본문 생성 (F8: 종결 순환 수치 3으로 통일 / F9: 자수는 라이브 규칙 8에 일임 /
#     F10+재감사 M5: 완결문·종결 순환 축은 라이브 규칙 5를 '동일 바이트'로 말미 재명시 —
#     같은 축을 다른 어휘로 두 번 말하면 지시 희석(B4: 말미 집중 재명시 자체는 run 완화
#     실측이므로 재명시는 유지) / F11: 형태명을 기능 서술로 / F12: 산술 불능 조항을
#     커버리지 계약으로 / F7: 부정 지시를 긍정형으로)
def build_enforce(rules):
    """rules: render_style과 같은 소스(list(style.rules) or DEFAULT_STYLE_RULES)를 넘긴다.
    규칙 5(index 4)를 그대로 재인용해 사본 문안 드리프트를 차단한다."""
    r5 = rules[4] if len(rules) > 4 else ""
    return ("[문체 지정. 이 지정이 장르 관습과 기본 습관보다 우선한다. 본문 전체에 적용하라]\n"
            + (f"· {r5}\n" if r5 else "")
            + f"· {COVERAGE_CONTRACT}\n"
            "· 자연스러운 현대 한국어 산문으로 쓴다.")

def build_sys_gen(persona, style_block, rules):
    return ("아래 재료로 연재 다음 화(한 회차 전체)를 써라.\n\n"
            + persona + "\n\n" + style_block + "\n\n" + build_enforce(rules) + "\n\n"
            "첫 줄부터 마지막 줄까지 장면 안의 사건·대사·이미지로 이어 쓴다. 출력은 본문뿐이다.")

def build_user_gen(syn1, syn2, prev_tail, story):
    # F20+재감사 M1: 확정 스토리에 참조 층위 라벨 — 스토리의 3인칭 현재형이 본문의
    # 인칭·시제로 새는 경로 차단(라이브 pov=first와 스토리 3인칭의 충돌 실측 지적)
    return (f"[지난 이야기(1~2화 상세)]\n{syn1}\n\n{syn2}\n\n"
            f"[직전 화 말미(참조 전용)]\n{REF_LABEL}\n{prev_tail}\n\n"
            f"[이번 화 확정 스토리]\n{STORY_LAYER_LABEL}\n{story}")

# ── 10) 문체 수리 (F3: 절대 자수는 호출 시점에 코드가 계산해 주입 + 완결 계약.
#     실전 배선은 revise 관문(G-A/G-B/length_ok) 경유가 정본 — 이 콜은 시연 전용)
def build_repair_sys(input_len):
    lo, hi = int(input_len * 0.9), int(input_len * 1.1)
    return ("아래 회차 본문을 퇴고하라. 사실·사건·대사·수치·문단 구성은 유지한다. "
            "묘사·관찰·정보 전달은 완결된 한 문장으로 이어 쓰고, 시간·수량·정도·결과는 그 문장 안에 안겨 넣어라. "
            "소리 한 마디와 방금 세어 본 결과 한 마디는 그대로 둔다. 같은 종결이 세 문장 이어지기 전에 다른 "
            "종결(연결형·현재형 판단·입말 생각)로 옮겨 가며 순환하라. 화자의 결론 명제는 바로 앞에서 "
            "보고 세고 겪은 것의 출력일 때만 남겨라. "
            f"분량은 공백 포함 {lo:,}자 이상 {hi:,}자 이하를 유지하고, 마지막 문장까지 끝맺어 출력하라. "
            "출력은 퇴고된 본문만.")

# ── 형식 재변환 폴백 (검사 불합격 시 1회 시도 → 재검사 → 그래도 불합격이면 작가 플래그)
FIX_FMT = "아래 스토리를 사건·순서·정보는 그대로 두고 형식만 규격으로 바꿔라.\n\n" + FORMAT

# ═══════════════ 결정론 검사 (F18: 계약 6항목 전부 기계 검사 — 검사 없는 계약은 장식) ═══════════════

QUOTE_CHARS = '"“”「」'      # 재감사 m2: 「」 추가(홑따옴표는 강조 용법과 구분 불가라 제외)
_TERMINALS = '.!?…"”」』'

def story_lines(text):
    # 재감사 m3: 선행 공백은 정규화하고 마커 검사는 엄격 유지
    return [l.strip() for l in (text or "").splitlines() if l.strip()]

# 과거형 선어말 '-았/었-'은 축약형(갔다·했다·왔다)이 다수라 글자 매칭이 아니라
# 받침 ㅆ 자모 분해로 검출한다(교착어 substring 결함 패턴의 동족).
# 받침 ㅆ이되 과거가 아닌 어간(있다·겠다·재밌다)은 제외.
_PAST_EXCL = set("있겠밌")

def past_ending_count(text):
    """줄 종결 위치의 과거형 종결 계수 — 현재형 계약 위반 검출."""
    n = 0
    for m in re.finditer(r"([가-힣])다(?=[.!?…\s]|$)", text or ""):
        ch = m.group(1)
        if ch in _PAST_EXCL:
            continue
        code = ord(ch) - 0xAC00
        if code >= 0 and code % 28 == 20:   # 종성 인덱스 20 = ㅆ
            n += 1
    return n

def fmt_check(text, lo=12, hi=16):   # 재감사 m1: 기본 상한을 문안(12~16)과 일치
    """확정 스토리 형식 계약의 결정론 검사. 반환: 위반 문자열 리스트(빈 리스트=통과)."""
    errs = []
    lines = story_lines(text)
    bad_start = [i + 1 for i, l in enumerate(lines) if not l.startswith("- ")]
    if bad_start:
        errs.append(f"불릿 시작 위반 {len(bad_start)}줄(예: {bad_start[:3]})")
    q = sum((text or "").count(c) for c in QUOTE_CHARS)
    if q:
        errs.append(f"따옴표 {q}개(계약 0)")
    p = past_ending_count(text)
    if p:
        errs.append(f"과거형 종결 {p}건(계약 0)")
    if not (lo <= len(lines) <= hi):
        errs.append(f"줄 수 {len(lines)}(계약 {lo}~{hi})")
    dw = [w for w in DESIGN_WORDS if w in (text or "")]
    dw += [f"{m}:" for m in DESIGN_LABEL_RE.findall(text or "")]
    if dw:
        errs.append(f"설계 용어 잔존 {dw}")
    bans = [b for b in BAN if b in (text or "")]
    if bans:
        errs.append(f"금지어 {bans}")
    return errs

def _norm(s):
    s = re.sub(r"\s+", "", s or "")
    return s.translate(str.maketrans("", "", QUOTE_CHARS + "'‘’…·-"))

def cite_ok(quote, source, min_len=8):
    """심문·스캔 인용의 원문 대조 — 정규화 후 substring. 불일치=미이행 처리."""
    nq = _norm(quote)
    return len(nq) >= min_len and nq in _norm(source)

def pos_ok(slot, n_lines):
    """이식 자리 '기둥 줄 N 뒤'의 N이 실제 줄 범위 안 정수인지."""
    m = re.search(r"(\d+)", slot or "")
    return bool(m) and 1 <= int(m.group(1)) <= n_lines

def merge_ok(merged, n_pillar, n_grafts):
    return len(story_lines(merged)) == n_pillar + n_grafts

def prose_ok(text, lo):
    """본문·수리 산출의 자수 하한 + 말미 종결 + 금지어 검사."""
    t = (text or "").rstrip()
    errs = []
    if len(t) < lo:
        errs.append(f"자수 {len(t)} < 하한 {lo}")
    if not t or t[-1] not in _TERMINALS:
        errs.append(f"말미 미종결(끝 문자 {t[-3:]!r})")
    bans = [b for b in BAN if b in t]
    if bans:
        errs.append(f"금지어 {bans}")
    return errs

# ═══════════════ 라이브 사슬 주입 블록 (SY-1 배선 전용 — 별건 재감사 대상) ═══════════════
# 라이브 회차 생성은 '_draft 1콜 + 비트 미소진 시 _continue ≤4콜' 사슬이다(harness.py:965~
# 재설계 주석 — 장면 수는 설계 입력이 아니라 결과). 따라서 분배는 고정 3분할이 아니라:
#   · _draft: 확정 스토리 '전체'를 사건·순서 계약으로 받는다.
#   · _continue: 코드가 커버리지 자산으로 계산한 '아직 실현되지 않은 줄'만 받아 이어 실현한다.
# 커버리지 계약은 두 콜에 동일 바이트로 재명시한다(계약 누락 콜에서 형식 붕괴 실측 — 1차 감사 치명 ⓐ).

# 재감사 반려(2026-08-14) 반영: ⑴잔여 줄은 '후보 풀'이다 — 코드 계측(어간 substring)은
# 시제 층위를 못 가로질러(현재형 스토리 vs 과거형 본문) 사실 주장이 못 되므로, 실현 여부
# 판단은 자기 본문을 보는 모델에 넘기고 리포트 라벨은 '미실현 의심'으로만 쓴다(F1).
# ⑵'최소 한 장면'은 장면 수 바닥(12~16)을 심는 산술이라 사건 실현 계약으로 교체(M3),
# 끊는 방법은 hook 소관·끊는 자리는 목록 소관으로 관할 분리(M4). ⑶라벨의 '위'는 라이브
# 메시지 구조에서 지시 대상이 빗나가 위치 무관 표현으로(M2). 시제 누수는 라벨이 아니라
# 기존 관문(tense_leak_ratio·_fix_tense) 소관 — 배선 첫 런에서 주입 유무 대조 계측 의무.

# 재감사 2차 M-A: 계약 상수 2분할 — 이어쓰기 블록에서 '첫 줄부터'(절대)와 '지면에 오를
# 차례인 첫 줄부터'(모델 판단)가 인접 충돌하던 것을 해소. 공유부(REALIZE)는 두 콜 동일
# 바이트 유지, 시작점 지정(START)은 초안 콜 전용.
COVERAGE_REALIZE = ("확정 스토리의 각 줄은 본문에서 실제로 벌어지는 사건으로 실현하고, "
                    "줄의 순서가 곧 사건의 순서다.")
COVERAGE_START = "목록의 첫 줄부터 순서대로 실현하며 나아간다."
COVERAGE_CONTRACT = COVERAGE_REALIZE + " " + COVERAGE_START   # 초안 경로 바이트 불변

STORY_LAYER_LABEL = "이 목록은 사건과 순서를 지정한다. 인칭·시제·문장은 이 회차 본문의 문체 규칙을 따른다."
# VX-1(structured_prompt 경로 전용): STORY_LAYER_LABEL 에 대사 귀속 라우팅 1줄 추가. 확정 스토리 불릿이
#   recency 특권 자리(프롬프트 말미)에서 대사 레지스터를 정하던 채널을, 형태 처방이 아니라 앵커(<인물보이스>·
#   <문체규칙>)로 리다이렉트한다 — 소스 지정 긍정형(REF_LABEL/AN-1a 동류·헌법 1·3 무저촉). OFF 는 이 상수를
#   안 쓴다(위 STORY_LAYER_LABEL 바이트 불변 — SY-1 잠금 테스트 정합).
STORY_LAYER_LABEL_XML = (STORY_LAYER_LABEL
    + " 인물이 하는 말의 어체와 표현은 <인물보이스> 카드와 <문체규칙>에서 가져온다.")

def build_story_block_draft(story, xml: bool = False):
    """_draft(회차 첫 콜) 주입 블록 — assembler.assemble() 산출 뒤에 덧붙인다.
    빈 값 가드로 OFF 경로 바이트 동일을 구조로 보증한다(M6 — 호출부 관습 의존 제거).
    xml(VX-1): True 면 <확정스토리> 태그 + 대사 라우팅 라벨. 기본 False=종전 대괄호 바이트 동일."""
    if not (story or "").strip():
        return ""
    if xml:
        return (f'\n\n<확정스토리 용도="사건순서">\n'
                f"{STORY_LAYER_LABEL_XML} {COVERAGE_CONTRACT}\n{story}\n</확정스토리>")
    return f"\n\n[이번 화 확정 스토리]\n{STORY_LAYER_LABEL} {COVERAGE_CONTRACT}\n{story}"

def remaining_story_lines(story, prose, is_uncovered):
    """확정 스토리 줄 중 코드 계측이 '미실현 의심'으로 보는 줄(후보 풀 — 사실 주장 아님).
    판정은 엔진 커버리지 자산(어간 과반·보수적 — harness._uncovered)을 is_uncovered
    콜러블로 재사용한다. 현재형 스토리 줄의 동사 키워드는 과거형 본문과 원리적으로 어긋나
    과소 소거가 상례이므로, 이 목록의 지위는 이어쓰기 문안이 '지면에 오를 차례 판단은
    모델'로 명시해 재서술 채널을 닫는다. 리포트·이벤트 라벨도 '미실현 의심'으로 쓴다."""
    out = []
    for l in story_lines(story):
        body = l[2:] if l.startswith("- ") else l
        if is_uncovered([body], prose):
            out.append(l)
    return out

def build_story_block_continue(remaining, xml: bool = False):
    """_continue(이어쓰기 콜) 주입 블록 — 후보 풀을 넘기되 차례 판단은 모델이 한다.
    빈 리스트면 ""(주입 0 — 기존 이어쓰기와 바이트 동일).
    xml(VX-1·감사관 M4): 이어쓰기 콜에도 <확정스토리> 태그 + 대사 라우팅을 동일 축으로 실어 계약이 draft·
    continue 전 콜에 유지되게 한다(형식 계약 모든 변환 콜 재명시·태그명 일관). 기본 False=종전 바이트 동일."""
    if not remaining:
        return ""
    if xml:
        return ('\n\n<확정스토리 용도="사건순서" 범위="이어실현">\n'
                f"{STORY_LAYER_LABEL_XML} {COVERAGE_REALIZE} "
                "<직전장면들>의 마지막 문장에 바로 이어서, "
                "이 목록에서 지면에 오를 차례인 첫 줄부터 순서대로 실현하라.\n"
                + "\n".join(remaining) + "\n</확정스토리>")
    return ("\n\n[이번 화 확정 스토리(이 회차에서 이어 실현할 줄)]\n"
            f"{STORY_LAYER_LABEL} {COVERAGE_REALIZE} "
            "[이번 회차 직전 장면들]의 마지막 문장에 바로 이어서, "
            "이 목록에서 지면에 오를 차례인 첫 줄부터 순서대로 실현하라.\n"
            + "\n".join(remaining))

# ── 라벨 도출 콜 (SY-1 배선 전용 · story 모드 — PM 설계 §1-D: 비트 '설계' 콜을 '기술' 콜로
#    치환, 콜 ±0. 사건 계획 소스를 확정 스토리 하나로 단일화하기 위해 이 콜은 설계를 하지
#    않는다 — 스토리가 이미 정한 것을 관리 라벨로 자기 기술만 한다.)
# 재감사 2차 반영: ⑴M-B 분류 콜에 축소 화이트리스트를 주면 정답 값이 빠져 거짓 라벨이
# 강제되고 순환 원장이 오염된다 — scene_form은 '전체' 목록을 받는다(다양성 레버는 스토리
# 층이 이미 가짐). ⑵M-C hook_type·closing_device는 프로즈 속성이라 스토리 시점 도출
# 불가 — 산출하지 않는다(결측 = 하위 화이트리스트가 무시, 하위호환). ⑶M-I 부정 지시 2건
# 긍정 전환. ⑷M-J time_source 인용 의무(cite_ok 대조 가능) + 무대시 매핑 예시 +
# user 블록에 직전 화 시간 기준 동봉. ⑸M-K world_reveal 기술형 유지(검증 축 침묵 방지).
def build_label_sys(scene_form_choices):
    return (
        "너는 연재 관리 기록자다. [이번 화 확정 스토리]를 읽고, 확정된 사건들을 그대로 둔 채 "
        "이 회차가 어떤 회차인지 기술하는 라벨만 산출하라. 산출물은 아래 JSON 필드뿐이다.\n"
        "JSON 필드: "
        "title(이 회차 한 편에 붙는 제목 한 줄), "
        "summary(스토리 전체를 한 문장의 순수 서사로), "
        "chapter_function(독자에게 주는 것: payoff/setup/escalation/relation/respite 중 하나), "
        f"scene_form(중심 장면의 안무: {'/'.join(scene_form_choices)} 중 가장 가까운 것), "
        "time_advance(직전 화 대비 시간 경과 짧게: 예 '없음'/'몇 분'/'다음날'/'사흘 후'), "
        "time_delta(같은 경과의 구조화: amount는 숫자, unit은 minute/hour/day/week/month/year 중 하나, "
        "mode는 보통 advance이고 과거 회상이면 flashback, 같은 시각 다른 장소면 parallel. "
        "경과가 '사흘 후'면 amount 3, unit day, mode advance이고, 경과가 없으면 amount 0, unit minute, mode advance다), "
        "time_source(그 경과가 드러난 확정 스토리의 한 줄을 그대로 인용. 경과를 알 수 없으면 빈 문자열로 두고 amount는 0으로), "
        "world_reveal(확정 스토리에서 독자가 처음 알게 되는 세계 사실이 드러나는 줄이 있으면 그 사실들을 배열로, 없으면 빈 배열), "
        "place(주요 장소 짧게). JSON만.")

def build_label_user(prev_time_fact, story):
    """라벨 콜 user 블록(M-J) — 직전 화 절대 시점 사실(story_clock 산출) 1줄 + 확정 스토리.
    시간 기준이 없으면 블록 생략(경과 발명 대신 time_source 빈 문자열 계약이 받는다)."""
    tb = f"[직전 화 시간 기준]\n{prev_time_fact}\n\n" if (prev_time_fact or "").strip() else ""
    return f"{tb}[이번 화 확정 스토리]\n{story}"

# ═══════════════ 라이브 재료 렌더 (F19: gen_context 사본·상수 하드코딩 금지) ═══════════════

def render_materials(st, chapter_no):
    """라이브 캐논에서 설계 재료 조립. 도구 목록은 장르 계약의 정본 열거에서 파싱하고,
    없으면 시끄럽게 실패한다(하드코딩 폴백 없음 — 폴백이 곧 구판 유출 채널)."""
    from novelcopilot.engine.chapter_gate import planned_event_for
    m = re.search(r"도구 어휘\(([^)]+)\)", st.world.genre_contract.vocabulary_tone or "")
    if not m:
        raise RuntimeError("장르 계약에서 도구 정본 열거를 찾지 못함 — 캐논 확인 필요")
    tools = m.group(1)
    syn1 = st.chapter(1).detail_synopsis or ""
    syn2 = st.chapter(2).detail_synopsis or ""
    pe = planned_event_for(st, chapter_no) or ""
    # 재감사 M8: 에피소드는 정본 매핑(chapter.episode_id)으로만 — 인명 substring
    # 휴리스틱은 다른 에피소드 재료를 조용히 주입한다. 못 찾으면 시끄럽게 실패.
    ch = next((c for c in st.chapters if getattr(c, "chapter", None) == chapter_no), None)
    ep_id = getattr(ch, "episode_id", "") if ch else ""
    ep_menu = None
    for arc in st.world.spine.arcs:
        for ep in arc.episodes:
            if getattr(ep, "episode_id", "") == ep_id:
                ep_menu = list(ep.event_menu or [])
    if ep_menu is None:
        raise RuntimeError(f"{chapter_no}화의 episode_id({ep_id!r})에 해당하는 에피소드를 못 찾음 — 정본 매핑 확인 필요")
    mat = (f"[작품 전제]\n{st.world.synopsis}\n\n"
           f"[지난 이야기(1~2화 상세)]\n{syn1}\n\n{syn2}\n\n"
           f"[캐논 메모]\n주인공의 도구 여섯 점: {tools}.\n\n"
           f"[이번 화 설계 사건]\n{pe}\n\n"
           f"[사건 재료(취사선택)]\n" + "\n".join(f"- {x}" for x in ep_menu))
    # 재감사 치명 1: 산출물만 훑고 입력 재료는 안 훑던 공백 — 조립된 재료에 BAN 스윕.
    # 구판 어휘가 시놉시스 경유로 되돌아오는 채널을 소스에서 차단(걸리면 데이터 수리가 정도).
    bad = [b for b in BAN if b in mat]
    if bad:
        raise RuntimeError(f"조립 재료에 금지어 잔존 {bad} — 프로젝트 데이터 수리 필요(프롬프트 덧대기 금지)")
    return {"mat": mat, "syn1": syn1, "syn2": syn2, "tools": tools}
