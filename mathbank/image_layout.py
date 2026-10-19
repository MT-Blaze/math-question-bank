"""题末可分离插图簇与逐图排版设置（持久化与导出共用）。

移植自上游 JudgePeach/math-question-bank V2.7 的同名模块，注释与错误文案本地化。
它解决的是「选项拼图被当成题末插图摘走」这一类重复排版：只有落在表格/选项环境**之外**
的题末连续图簇才允许被摘到 figure_align 栏，其余图片必须留在作者写下的锚点上。

这里只放纯函数：不认识数据库、不认识 Flask，导出的枚举值也绝不接受任意 CSS/TeX，
所以迁移、路由、导出三侧都能共用同一套判定。
"""

import json
import re

# Markdown 插图锚点：![](/static/uploads/xxx.png) 之类，捕获组是路径本身
IMAGE_RE = re.compile(r"!\[.*?\]\(([^)]+)\)")
# 需要「环境栈」跟踪的结构：表格类 + 选择题环境。只有 begin/end 配对才能判断
# 某张图是不是还嵌在这些环境里面（见 split_image_anchors）。
ENV_RE = re.compile(
    r"\\(begin|end)\{(tabular\*?|tabularx|longtable|tblr|longtblr|talltblr|choices)\}"
)
# 逐图尺寸的物理宽高上限（厘米）：PDF/Word 两条导出路径共用，auto 走各导出的默认值
SIZE_CM = {"small": (5.0, 4.2), "medium": (8.0, 6.0), "large": (10.5, 7.5)}


def image_key(path: str) -> str:
    """把各种写法的插图路径归一成同一个 key，供布局字典索引。"""

    return re.sub(r"^/?(?:static/)?uploads/", "", str(path or "").strip())


def split_image_anchors(content: str) -> tuple[str, str]:
    """只把「环境之外」的题末连续图簇摘下来，返回 (留在原地的正文, 可分离图簇)。

    从最后一张图往前扫：一旦某张图后面还有正文，或者它还在表格/choices 环境里面，
    题末图簇就到此为止。选项拼图因此不会被搬走，卷面也不会出现两份同一个图。
    """

    source = str(content or "")
    end = len(source)
    for match in reversed(list(IMAGE_RE.finditer(source))):
        if source[match.end():end].strip():
            break
        stack = []
        for env in ENV_RE.finditer(source[: match.start()]):
            if env[1] == "begin":
                stack.append(env[2])
            elif stack and stack[-1] == env[2]:
                stack.pop()
        if stack:
            break
        end = match.start()
    return source[:end].rstrip(), source[end:].strip()


def normalize_image_layouts(value, content: str) -> dict:
    """只保留正文里真实出现的图片引用与合法枚举值，其余一律丢弃。

    布局字典是用户可控输入，但导出侧会把它拼进 adjustbox/includegraphics，
    所以 align/size 必须是枚举，条目数有上限，绝不接受任意 CSS 或 TeX。
    """

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("插图独立排版格式无效") from exc
    if not isinstance(value, dict) or len(value) > 200:
        raise ValueError("插图独立排版必须为最多 200 项的对象")
    keys = {image_key(match[1]) for match in IMAGE_RE.finditer(content or "")}
    result = {}
    for path, layout in value.items():
        key = image_key(path)
        if not isinstance(layout, dict):
            raise ValueError("插图独立排版格式无效")
        align, size = layout.get("align", "center"), layout.get("size", "auto")
        if align not in ("left", "center", "right") or size not in ("auto", *SIZE_CM):
            raise ValueError("无效的插图位置或尺寸")
        if key in keys:
            result[key] = {"align": align, "size": size}
    return result
