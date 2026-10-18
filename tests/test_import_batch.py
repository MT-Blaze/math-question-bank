"""第四录入方案（望悠流水线移植）回归测试。

重点验证三道防陈旧/质控闸门真的拦得住：
1. previewHash 不符 -> 拒绝提交
2. 风险项 reason 留空 -> 拒绝接受
3. expectedRevision 冲突 -> 拒绝
4. 有未确认风险 -> 拒绝提交
"""

import pytest

from mathbank import import_batch, import_prompts


def _make_session(mode="exam", pages=1):
    state = import_batch.create_session(
        mode, [{"name": "a.pdf", "size": 1024, "pages": pages}]
    )
    return import_batch.build_plan(state["session_id"])


def _recognize(state, items):
    item_id = state["items"][0]["id"]
    return import_batch.record_recognized(
        state["session_id"], item_id, {"items": items, "source": "测试卷"}
    )


# ---------------------------------------------------------------------------
# 提示词：纪律层共用、契约层按我们 schema
# ---------------------------------------------------------------------------


def test_discipline_is_shared_by_both_modes():
    """纪律层原样移植，教材与整卷共用同一份约束。"""

    for system in (import_prompts.TEXTBOOK_SYSTEM, import_prompts.EXAM_SYSTEM):
        assert import_prompts.DISCIPLINE_RULES in system
        assert "看似多余的分组花括号" in system
        assert "反斜杠只写一个" in system
        assert "全角字符一律转半角" in system


def test_contract_uses_our_math_delimiters_not_wangyou_ones():
    """契约层必须是我们自己的 $..$ / $$..$$，不是望悠的 \\(..\\) / \\[..\\]。"""

    for system in (import_prompts.TEXTBOOK_SYSTEM, import_prompts.EXAM_SYSTEM):
        assert "$...$" in system
        assert "不是 \\( \\) 或 \\[ \\]" in system
    # 纪律层里对望悠分隔符的说明只应作为"反例"出现一次
    assert import_prompts.DISCIPLINE_RULES.count("\\( \\)") == 1


def test_contract_outputs_our_question_types_only():
    """输出契约里只允许本系统的四种题型。"""

    for system in (import_prompts.TEXTBOOK_SYSTEM, import_prompts.EXAM_SYSTEM):
        for qt in ("single_choice", "multi_choice", "fill_in_blank", "detailed_answer"):
            assert qt in system
        # 望悠的 kind 枚举不应出现在输出契约中（那是它的 schema）
        assert "example/theorem" not in system


def test_exam_prompt_strips_question_no_and_bracket():
    """整卷：题号只进 paper_question_no，题干开头不重复；括号不写进题干。"""

    assert "paper_question_no" in import_prompts.EXAM_SYSTEM
    assert "不要重复写题号" in import_prompts.EXAM_SYSTEM
    assert "（　　）" in import_prompts.EXAM_SYSTEM


def test_map_kind_to_question_type_translates_wangyou_schema():
    """望悠的 kind 必须翻译成我们的题型，不能直接沿用。"""

    assert import_prompts.map_kind_to_question_type("example", "textbook") == "detailed_answer"
    assert import_prompts.map_kind_to_question_type("theorem", "textbook") == "detailed_answer"
    assert import_prompts.map_kind_to_question_type("single", "exam") == "single_choice"
    assert import_prompts.map_kind_to_question_type("multiple", "exam") == "multi_choice"
    assert import_prompts.map_kind_to_question_type("fill", "exam") == "fill_in_blank"
    # 未知 kind 有选项时按选择题兜底
    assert import_prompts.map_kind_to_question_type("???", "exam", has_options=True) == "single_choice"
    assert import_prompts.map_kind_to_question_type("???", "exam") == "detailed_answer"


def test_render_choices_and_figure():
    """选项渲染成 choices 环境且不带前缀；选项行上的图限宽 0.42。"""

    out = import_prompts.render_choices(["106", "105"])
    assert out.startswith("\\begin{choices}")
    assert r"\item 106" in out and r"\item 105" in out
    assert "A." not in out

    assert "0.85\\linewidth" in import_prompts.render_figure("a.png")
    assert "0.42\\linewidth" in import_prompts.render_figure("a.png", on_option_line=True)


def test_build_recognize_messages_rejects_unknown_mode():
    with pytest.raises(ValueError):
        import_prompts.build_recognize_messages("unknown-mode")


# ---------------------------------------------------------------------------
# 闸门 1：previewHash
# ---------------------------------------------------------------------------


def test_commit_rejects_stale_preview_hash():
    state = _make_session(pages=1)
    _recognize(state, [{"stem": "求 $1+1$。", "question_type": "detailed_answer"}])

    with pytest.raises(ValueError, match="previewHash"):
        import_batch.commit_session(state["session_id"], "stale-hash", db=None)


# ---------------------------------------------------------------------------
# 闸门 2：风险项必须填理由
# ---------------------------------------------------------------------------


def test_accept_risks_requires_non_empty_reason():
    state = _make_session(pages=1)
    _recognize(state, [{"stem": "", "question_type": "detailed_answer"}])
    risks = import_batch.evaluate_risks(state["session_id"])
    assert risks, "空题干应被判为风险"

    with pytest.raises(ValueError, match="reason"):
        import_batch.accept_risks(
            state["session_id"], [{"riskId": risks[0]["id"], "reason": "  "}]
        )

    with pytest.raises(ValueError, match="riskId"):
        import_batch.accept_risks(state["session_id"], [{"reason": "只填理由"}])


def test_unaccepted_risk_blocks_commit():
    state = _make_session(pages=1)
    _recognize(state, [{"stem": "", "question_type": "detailed_answer"}])
    import_batch.evaluate_risks(state["session_id"])
    fresh = import_batch._load(state["session_id"])

    with pytest.raises(ValueError, match="未确认"):
        import_batch.commit_session(
            state["session_id"], import_batch.compute_preview_hash(fresh), db=None
        )


def test_accepting_risk_records_reason_and_unblocks():
    state = _make_session(pages=1)
    _recognize(state, [{"stem": "", "question_type": "detailed_answer"}])
    risks = import_batch.evaluate_risks(state["session_id"])

    updated = import_batch.accept_risks(
        state["session_id"],
        [{"riskId": risks[0]["id"], "reason": "扫描缺页，人工补录"}],
    )
    assert updated["accepted_count"] == 1
    assert import_batch.blocking_risks(updated) == []


# ---------------------------------------------------------------------------
# 闸门 3：expectedRevision 乐观并发
# ---------------------------------------------------------------------------


def test_accept_risks_rejects_stale_revision():
    state = _make_session(pages=1)
    _recognize(state, [{"stem": "", "question_type": "detailed_answer"}])
    risks = import_batch.evaluate_risks(state["session_id"])

    with pytest.raises(ValueError, match="revision"):
        import_batch.accept_risks(
            state["session_id"],
            [{"riskId": risks[0]["id"], "reason": "理由"}],
            expected_revision=999,
        )


def test_recognize_bumps_revision():
    state = _make_session(pages=1)
    before = state["revision"]
    after = _recognize(state, [{"stem": "求 $x$。", "question_type": "detailed_answer"}])
    assert after["revision"] > before


# ---------------------------------------------------------------------------
# 回滚闸门
# ---------------------------------------------------------------------------


def test_rollback_rejects_bad_hash_and_uncommitted():
    state = _make_session(pages=1)
    with pytest.raises(ValueError, match="尚未提交"):
        import_batch.rollback_session(state["session_id"], "any", db=None)


def test_session_rejects_oversized_file_and_bad_mode():
    from mathbank.import_batch import MAX_FILE_BYTES

    with pytest.raises(ValueError, match="300MB"):
        import_batch.create_session("exam", [{"name": "big.pdf", "size": MAX_FILE_BYTES + 1, "pages": 1}])
    with pytest.raises(ValueError, match="未知录入模式"):
        import_batch.create_session("nope", [{"name": "a.pdf", "size": 1, "pages": 1}])


# ---------------------------------------------------------------------------
# 入库（走真实归一化链）
# ---------------------------------------------------------------------------


def test_commit_writes_questions_through_normalize_chain(db_session):
    from mathbank.database import Question

    state = _make_session(mode="exam", pages=1)
    _recognize(
        state,
        [
            {
                "stem": "1. 已知 $f(x)=x^2$，求 $f(1)$。",
                "question_type": "single_choice",
                "options": ["1", "2"],
                "analysis": "代入得 $1$。",
                "paper_question_no": "1",
                "figures": [],
            }
        ],
    )
    fresh = import_batch._load(state["session_id"])
    result = import_batch.commit_session(
        state["session_id"], import_batch.compute_preview_hash(fresh), db_session
    )
    assert result["count"] == 1

    row = db_session.query(Question).filter(Question.id == result["question_ids"][0]).first()
    assert row is not None
    # 归一化链剥离了题干开头的原卷题号，并回填到 paper_question_no
    assert row.paper_question_no == "1"
    assert not row.content.lstrip().startswith("1.")
    assert "$f(x)=x^2$" in row.content

    # rollbackHash 正确时可整批回滚
    fresh2 = import_batch._load(state["session_id"])
    out = import_batch.rollback_session(
        state["session_id"], import_batch.compute_preview_hash(fresh2), db_session
    )
    assert out["removed"] == 1
    assert db_session.query(Question).filter(Question.id == result["question_ids"][0]).first() is None


def test_recognize_worker_records_items(monkeypatch):
    """后台 recognize worker 逐页写入识别结果（视觉/渲染调用被 mock）。"""

    import os

    import mathbank.routes_import_batch as rb

    class _StubTasks:
        def create(self, *a, **k):
            pass

        def submit(self, *a, **k):
            pass

        def update(self, *a, **k):
            pass

        def complete(self, *a, **k):
            pass

        def fail(self, *a, **k):
            pass

        def remove(self, *a, **k):
            pass

        def check_cancelled(self, *a, **k):
            pass

    monkeypatch.setattr(rb._main, "DOCUMENT_TASKS", _StubTasks())
    monkeypatch.setattr(rb, "_render_page", lambda *a, **k: b"fake-png")

    calls = {"n": 0}

    def fake_vision(mode, png, page_no, total_pages):
        calls["n"] += 1
        return {
            "items": [
                {"stem": f"第{page_no}题", "question_type": "detailed_answer"}
            ]
        }

    monkeypatch.setattr(rb, "_vision_recognize", fake_vision)

    state = _make_session(pages=2)
    # 落一个占位文件，让"源文件存在"判定通过（render 已被 mock）
    sdir = rb._session_dir(state["session_id"])
    os.makedirs(sdir, exist_ok=True)
    with open(os.path.join(sdir, "a.pdf"), "wb") as fh:
        fh.write(b"%PDF-1.4 fake")

    rb._run_recognize(state["session_id"], "task-x", "")
    after = import_batch._load(state["session_id"])
    assert calls["n"] == 2
    assert all(it["status"] == "recognized" for it in after["items"])
    assert after.get("recognize_failures") == []
