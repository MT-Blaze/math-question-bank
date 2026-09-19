import re
from unittest.mock import patch

import pytest

from mathbank.content_locks import (
    ContentLockIntegrityError,
    lock_visible_math,
    restore_visible_math,
)


def test_visible_math_lock_keeps_formula_inline_and_restores_exact_source():
    source = r"已知 $f(x)=\dfrac{x+1}{x-1}$，价格为 \$5，且 $$A=\begin{matrix}1&2\\3&4\end{matrix}$$。"
    locked, locks = lock_visible_math(source, "task-1")
    assert len(locks) == 2
    assert r"$f(x)=\dfrac{x+1}{x-1}$" in locked
    assert r"$$A=\begin{matrix}1&2\\3&4\end{matrix}$$" in locked
    assert r"价格为 \$5" in locked

    questions = [{
        "content": f"已知 [[{locks[0].lock_id}]]，且 [[{locks[1].lock_id}]]。",
        "answer_markdown": "",
    }]
    report = restore_visible_math(questions, locks)
    assert report["math_locks_restored"] == 2
    assert questions[0]["content"] == (
        r"已知 $f(x)=\dfrac{x+1}{x-1}$，且 $$A=\begin{matrix}1&2\\3&4\end{matrix}$$。"
    )


def test_visible_math_lock_overwrites_model_modified_formula_inside_tag():
    locked, locks = lock_visible_math(r"求 $x^2+1$ 的最小值。", "task2")
    modified = locked.replace("x^2+1", "x^2-1")
    questions = [{"content": modified, "answer_markdown": ""}]
    report = restore_visible_math(questions, locks)
    assert questions[0]["content"] == r"求 $x^2+1$ 的最小值。"
    assert report["math_locks_overwritten"] == 1


@pytest.mark.parametrize("content", ["公式被删除", "[[{id}]] 和 [[{id}]]"])
def test_visible_math_lock_rejects_missing_or_duplicate_ids(content):
    _locked, locks = lock_visible_math(r"计算 $1+1$。", "task3")
    questions = [{"content": content.format(id=locks[0].lock_id), "answer_markdown": ""}]
    with pytest.raises(ContentLockIntegrityError):
        restore_visible_math(questions, locks)


def test_docx_task_restores_formula_before_returning_questions():
    from main import DOCUMENT_TASKS, run_docx_parsing_task

    task_id = "word-lock-integration"
    extracted = {
        "success": True,
        "markdown": r"1. 已知 $f(x)=x^2+1$，求最小值。",
        "image_count": 0,
        "image_paths": [],
        "diagnostics": {
            "omml_converted": 1,
            "mtef_converted": 0,
            "review_required": 0,
        },
    }

    def fake_parse(locked_text, _generate_answers):
        assert '<mathbank-math id="' in locked_text
        assert r"$f(x)=x^2+1$" in locked_text
        lock_id = re.search(r'id="(MBM_[^"]+)"', locked_text).group(1)
        return [{
            "content": f"1. 已知 [[{lock_id}]]，求最小值。",
            "answer_markdown": "",
            "question_type": "detailed_answer",
            "referenced_images": [],
        }]

    if DOCUMENT_TASKS.exists(task_id):
        DOCUMENT_TASKS.remove(task_id)
    DOCUMENT_TASKS.create(task_id, document_type="docx", temp_assets=[])
    with patch("main.extract_docx_markdown", return_value=extracted):
        with patch("main.parse_paper_text_internal", side_effect=fake_parse):
            run_docx_parsing_task(task_id, b"fake-docx", "公式锁定测试.docx")

    task = DOCUMENT_TASKS.snapshot(task_id)
    DOCUMENT_TASKS.remove(task_id)
    assert task["status"] == "completed"
    # 公式按锁定 id 逐字还原；开头的原卷题号会被剥到 paper_question_no，
    # 因为 A4 预览与 exam-zh 导出都自行编号，题干留题号会排成「1. 1.」。
    assert task["data"][0]["content"] == r"已知 $f(x)=x^2+1$，求最小值。"
    assert task["data"][0]["paper_question_no"] == "1"
    assert task["diagnostics"]["math_locks_restored"] == 1


@pytest.mark.parametrize(
    "reference",
    [
        "[[MBM_abcd1234abcd1234_3]]",              # 前导零被模型吃掉
        "[[MBM_ABCD1234ABCD1234_0003]]",           # 十六进制段大小写漂移
        "[[MBM_abcd1234abcd1234_ 0003]]",          # 下划线后夹了空格
        '<mathbank-math id="MBM_abcd1234abcd1234_3">$x^2$</mathbank-math>',
    ],
)
def test_restore_accepts_harmless_id_drift_from_the_model(reference):
    r"""模型对锁定 ID 的无害改写不得判成「公式丢失」而让整卷拆题失败。

    实测 Word 路线 20 个锁里 2 个被写成不带前导零的形态，旧实现直接抛
    ContentLockIntegrityError，用户看到的是整卷解析失败。
    """
    from mathbank.content_locks import ContentLock

    lock = ContentLock(lock_id="MBM_abcd1234abcd1234_0003", original=r"$x^2$")
    questions = [{"content": f"已知 {reference}，求最小值。", "answer_markdown": ""}]
    report = restore_visible_math(questions, [lock])
    assert report["math_locks_restored"] == 1
    assert questions[0]["content"] == r"已知 $x^2$，求最小值。"


@pytest.mark.parametrize(
    "reference",
    [
        "[[MBM_abcd1234abcd1234_0004]]",           # 序号不同 = 另一个锁，不算命中
        "[[MBM_abcd1234abcd1234_00031]]",          # 边界必须收紧，不得截断匹配
        "[[MBM_ffffffffffffffff_0003]]",           # 作用域不同
        "公式不见了",                                # 真的丢了
    ],
)
def test_restore_still_fails_when_the_lock_is_really_gone(reference):
    from mathbank.content_locks import ContentLock

    lock = ContentLock(lock_id="MBM_abcd1234abcd1234_0003", original=r"$x^2$")
    questions = [{"content": reference, "answer_markdown": ""}]
    with pytest.raises(ContentLockIntegrityError):
        restore_visible_math(questions, [lock])


def test_formula_returned_verbatim_without_the_id_is_not_a_loss():
    """模型把公式一字不差抄回、只是省掉定位标记：内容没丢，不得整卷报错。

    实测 Word 路线 20 个锁里 2 个走的就是这种写法，系统还会先自动重试一次
    （白花一次付费调用），重试后仍不写 id 就把整卷判死。
    """
    from mathbank.content_locks import ContentLock

    lock = ContentLock(lock_id="MBM_abcd1234abcd1234_0003", original=r"$x={x}_{0}$")
    questions = [{"content": "取 $x={x}_{0}$ 时不成立。", "answer_markdown": ""}]
    report = restore_visible_math(questions, [lock])
    assert report["math_locks_implicit"] == 1
    assert report["math_locks_missing"] == 0
    assert questions[0]["content"] == "取 $x={x}_{0}$ 时不成立。"


def test_ambiguous_verbatim_formula_still_fails():
    """同一条公式出现两次、或两道题共用同一原文时无法判断归属，仍旧按丢失中断。"""
    from mathbank.content_locks import ContentLock

    lock = ContentLock(lock_id="MBM_abcd1234abcd1234_0003", original=r"$x$")
    same_text_other_id = ContentLock(
        lock_id="MBM_abcd1234abcd1234_0004", original=r"$x$"
    )
    # 两份锁共用同一原文
    questions = [{"content": "已知 $x$，求 $x$。", "answer_markdown": ""}]
    with pytest.raises(ContentLockIntegrityError):
        restore_visible_math(questions, [lock, same_text_other_id])
    # 原文在输出里出现两次
    with pytest.raises(ContentLockIntegrityError):
        restore_visible_math([{"content": "$x$ 与 $x$", "answer_markdown": ""}], [lock])


def test_mixed_restore_and_verbatim_return_counts_separately():
    from mathbank.content_locks import ContentLock

    by_id = ContentLock(lock_id="MBM_abcd1234abcd1234_0001", original=r"$a+1$")
    by_text = ContentLock(lock_id="MBM_abcd1234abcd1234_0002", original=r"$b+2$")
    questions = [{
        "content": "甲：[[MBM_abcd1234abcd1234_0001]]；乙：$b+2$",
        "answer_markdown": "",
    }]
    report = restore_visible_math(questions, [by_id, by_text])
    assert report["math_locks_restored"] == 1
    assert report["math_locks_implicit"] == 1
    assert r"甲：$a+1$；乙：$b+2$" == questions[0]["content"]
