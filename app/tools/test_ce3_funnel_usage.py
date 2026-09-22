# -*- coding: utf-8 -*-
"""CE-3 회귀 — 깔때기(run_story_pass) 비용 전량 계상: gen(sess.provider)+judge(cross-vendor) 델타가
① 레코드 checks.usage ② state.usage_total 양쪽에 동일하게 계상된다(자율 런 1.14M 미계상 실측의 잠금).
run_funnel·create_role_provider 스텁 — LLM 0.
"""
import sys, os, tempfile, threading
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _U:
    def __init__(self):
        self.t = 0
    def as_dict(self):
        return {"chat_calls": 0, "chat_tokens": self.t, "embed_calls": 0, "embed_items": 0}


class _Prov:
    def __init__(self):
        self.usage = _U()


def test_funnel_usage_in_record_and_total():
    from novelcopilot.config import get_settings
    from novelcopilot.repository import FilesystemProjectRepository
    from novelcopilot.services import CopilotService
    from novelcopilot.domain.project import ProjectState, ProjectSeed
    from novelcopilot.domain.world import WorldConfig
    import novelcopilot.engine.story_pass as SPmod
    import novelcopilot.services.copilot as C

    tmp = Path(tempfile.mkdtemp(prefix="ce3_"))
    settings = get_settings().model_copy(update={"data_dir": str(tmp)})
    svc = CopilotService(settings, FilesystemProjectRepository(tmp))
    state = ProjectState(id="p1", seed=ProjectSeed(), world=WorldConfig(title="t"))
    svc.repo.save(state)
    gen = _Prov()
    sess = SimpleNamespace(lock=threading.Lock(), provider=gen,
                           bus=SimpleNamespace(emit=lambda *a, **k: None))
    svc.sessions = SimpleNamespace(get_or_create=lambda st: sess, evict=lambda pid: None)

    def stub_funnel(deps, st, chapter=None, **kw):
        deps.gen.usage.t += 100      # 초안·수정 콜 시뮬(sess.provider)
        deps.judge.usage.t += 50     # 리뷰·심문·쌍대 콜 시뮬(cross-vendor 별도 프로바이더)
        return {"materials": {"chapter_no": 16, "digest": "d" * 12, "syn_prev": "", "syn1": "",
                              "hygiene": {}},
                "candidates": {}, "finals": {}, "reviews": {}, "audits": {}, "pairwise": {},
                "pillar": "E1", "grafts": [], "variants": [], "role": "회수", "events": {}}

    orig_rf, orig_crp = SPmod.run_funnel, C.create_role_provider
    SPmod.run_funnel = stub_funnel
    C.create_role_provider = lambda s, m: _Prov()
    try:
        total0 = (svc.repo.get("p1").usage_total or {}).get("chat_tokens", 0)
        res = svc.run_story_pass("p1")
        st = svc.repo.get("p1")
        rec = next(r for r in st.story_passes if r.chapter == 16)
        assert rec.checks["usage"]["chat_tokens"] == 150          # gen 100 + judge 50 전량
        assert res["usage"]["chat_tokens"] == 150
        assert (st.usage_total or {}).get("chat_tokens", 0) - total0 == 150   # 전역 총비용 동일 계상
    finally:
        SPmod.run_funnel = orig_rf
        C.create_role_provider = orig_crp
