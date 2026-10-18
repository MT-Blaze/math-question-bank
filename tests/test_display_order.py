# -*- coding: utf-8 -*-
"""阶段2回归：display-order 读写、学校简称改名、读侧 full_name/short_name。

所有写操作都 monkeypatch 到临时文件，绝不触碰 mathbank/resources/ 真实数据。
"""
import json
import os
from pathlib import Path

import pytest


@pytest.fixture()
def isolated_paths(tmp_path, monkeypatch):
    from mathbank import routes_library as rl

    schools = tmp_path / "schools.json"
    order = tmp_path / "display_order.json"
    data = [
        {"id": "sch_hsf_0001", "name": "哈师大附中", "full_name": "哈尔滨师范大学附属中学", "short_name": "哈师大附中",
         "province": "黑龙江省", "city": "哈尔滨市"},
        {"id": "sch_h3z_000a", "name": "哈三中", "full_name": "哈尔滨市第三中学校", "short_name": "哈三中",
         "province": "黑龙江省", "city": "哈尔滨市"},
        {"id": "sch_df_00002", "name": "东北师大附中", "full_name": "东北师范大学附属中学", "short_name": "东北师大附中",
         "province": "吉林省", "city": "长春市"},
        {"id": "sch_syl_03", "name": "省实验", "full_name": "辽宁省实验中学", "short_name": "省实验",
         "province": "辽宁省", "city": "沈阳市"},
        {"id": "sch_syl_04", "name": "省实验", "full_name": "河南省实验中学", "short_name": "省实验",
         "province": "河南省", "city": "郑州市"},
        {"id": "sch_daq_05", "name": "大连二十四中", "full_name": "大连市第二十四中学", "short_name": "大连二十四中",
         "province": "辽宁省", "city": "大连市"},
        {"id": "sch_dqsy_06", "name": "大庆实验", "full_name": "大庆实验中学", "short_name": "大庆实验",
         "province": "黑龙江省", "city": "大庆市"},
        {"id": "", "name": "无全称中学", "full_name": "", "short_name": "无全称中学",
         "province": "黑龙江省", "city": "大庆市"},
    ]
    schools.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(rl, "_SCHOOLS_JSON", schools)
    monkeypatch.setattr(rl, "_DISPLAY_ORDER_JSON", order)
    monkeypatch.setattr(rl, "_schools_cache", None)
    monkeypatch.setattr(rl, "_schools_raw", {})
    monkeypatch.setattr(rl, "_schools_fingerprint", None)
    monkeypatch.setattr(rl, "_display_order_cache", None)
    yield rl, schools, order
    # 先清缓存再让 monkeypatch 还原全局：顺序反了会把脏缓存写回模块、污染后续用例。
    # （旧 teardown 用 monkeypatch.setattr 置 None，实际是在还原之后才清，
    #  任何后续用例都会读到上一条留下的顺序。）
    rl._schools_cache = None
    rl._schools_raw = {}
    rl._schools_fingerprint = None
    rl._display_order_cache = None


def test_display_order_falls_back_to_default_without_file(isolated_paths):
    rl, _schools, order = isolated_paths
    assert not order.exists()
    payload = rl._load_display_order()
    # 默认序即置顶表：黑吉辽置顶 → II卷 → I卷 → 地方卷（设计决策 1）
    provs = payload["provinces"]["_"]
    assert provs[:3] == ["黑龙江省", "吉林省", "辽宁省"]
    assert provs[3] == "内蒙古自治区"
    assert provs.index("广东省") > provs.index("四川省")
    assert provs[-3:] == ["北京市", "天津市", "上海市"]
    assert payload["cities"] == {} and payload["schools"] == {}


def test_post_display_order_writes_file_and_refreshes_cache_without_restart(isolated_paths):
    rl, _schools, order = isolated_paths
    # 钉死「不重启即生效」：先经 GET 路径把旧序装进进程缓存，POST 后必须读到新序。
    # 若 save 端点漏掉 `_display_order_cache = normalized`，这里会拿回旧缓存。
    assert rl._load_display_order()["provinces"]["_"][0] == "黑龙江省"
    payload = {
        "provinces": ["吉林省", "黑龙江省", "辽宁省"],
        "cities": {"黑龙江省": ["大庆市", "哈尔滨市"]},
        "schools": {"黑龙江省|哈尔滨市": ["哈师大附中", "无全称中学"]},
    }
    result = rl.save_display_order(dict(payload))
    assert result["status"] == "success"
    # 文件真的落了盘，且省层已收敛为置顶表形态
    on_disk = json.loads(order.read_text(encoding="utf-8"))
    assert on_disk["provinces"] == {"_": payload["provinces"]}
    # 进程内缓存同步刷新——不重启也必须读到新序（用户约束）
    assert rl._load_display_order()["provinces"]["_"][0] == "吉林省"
    regions = rl.school_regions()["regions"]
    assert [r["province"] for r in regions][:3] == ["吉林省", "黑龙江省", "辽宁省"]
    heilongjiang = next(r for r in regions if r["province"] == "黑龙江省")
    assert heilongjiang["cities"] == ["大庆市", "哈尔滨市"]


def test_school_order_pins_then_reference_count_then_alphabetical(isolated_paths, monkeypatch):
    """排序口径：置顶（父级内）→ 引用次数降序 → 字母序；取消置顶要真的从文件移除。"""
    rl, _schools, order = isolated_paths
    monkeypatch.setattr(rl, "_reference_counts", lambda: {
        "schools": {"哈三中": 9, "大庆实验": 4, "哈师大附中": 0},
        "provinces": {}, "cities": {}})
    resp = rl.save_display_order({
        "provinces": [],
        "cities": {},
        "schools": {"黑龙江省|哈尔滨市": ["哈师大附中"]},   # 该市置顶：0 引用的也要排在最前
    })
    assert resp["status"] == "success"
    cfg = rl.get_display_config()
    names = cfg["schools"]["黑龙江省|哈尔滨市"]
    # 置顶的「哈师大附中」（0 引用）压过引用次数最高的「哈三中」，同父级其余按次数降序；
    # 未选市（大庆市桶）不得混进哈尔滨的列表——父级内生效是硬口径。
    assert names == ["哈师大附中", "哈三中"], names
    assert cfg["schools"]["黑龙江省|大庆市"][0] == "大庆实验", cfg["schools"]["黑龙江省|大庆市"]
    # 取消置顶：清空该父级后按引用次数重排（无 id 的「无全称中学」计 0 引用，排在末位）
    order.write_text(json.dumps({"provinces": {}, "cities": {}, "schools": {}}, ensure_ascii=False),
                     encoding="utf-8")
    rl._display_order_cache = None
    assert rl.get_display_config()["schools"]["黑龙江省|哈尔滨市"] == ["哈三中", "哈师大附中"]


def test_display_config_provinces_follow_pin_and_never_drop_library_entries(isolated_paths, monkeypatch):
    rl, _schools, order = isolated_paths
    monkeypatch.setattr(rl, "_reference_counts", lambda: {
        "schools": {}, "provinces": {"吉林省": 7}, "cities": {}})
    order.write_text(json.dumps({
        "provinces": {"_": ["辽宁省"]},
        "cities": {}, "schools": {}}, ensure_ascii=False), encoding="utf-8")
    rl._display_order_cache = None   # 本用例直接改文件，必须绕开上一条留下的进程缓存
    provs = rl.get_display_config()["provinces"]
    # 手工置顶第一 → 引用次数第二 → 其余字母序；词库里的省一个都不能少
    assert provs[0] == "辽宁省" and provs[1] == "吉林省"
    assert set(provs) == {"黑龙江省", "吉林省", "辽宁省", "河南省"}


def test_post_display_order_rejects_malformed_payload(isolated_paths):
    rl, _schools, order = isolated_paths
    bad_cases = [
        {},                                             # 缺键
        {"provinces": "not-a-list"},                    # 类型错
        {"provinces": ["黑龙江省"], "cities": [], "schools": {}},   # cities 非 dict
        {"provinces": ["黑龙江省", 3], "cities": {}, "schools": {}},  # 非字符串项
        {"provinces": ["黑龙江省"], "cities": {"黑龙江省": [""]}, "schools": {}},  # 空串项
        {"provinces": {"_": [None]}, "cities": {}, "schools": {}},   # 置顶表非字符串项
    ]
    for bad in bad_cases:
        response = rl.save_display_order(bad)
        assert getattr(response, "status_code", 200) == 400, bad
    assert not order.exists()


def test_legacy_array_payload_is_accepted_and_stored_as_pinned_map(isolated_paths):
    """旧客户端仍发裸数组 provinces：接受并收敛成置顶表，不得 400。"""
    rl, _schools, order = isolated_paths
    resp = rl.save_display_order({"provinces": ["吉林省", "黑龙江省"], "cities": {}, "schools": {}})
    assert resp["status"] == "success"
    assert json.loads(order.read_text(encoding="utf-8"))["provinces"] == {"_": ["吉林省", "黑龙江省"]}


def test_school_regions_unions_custom_entries_missing_from_library(isolated_paths):
    rl, _schools, order = isolated_paths
    order.write_text(json.dumps({
        "provinces": {"_": ["黑龙江省"]},
        "cities": {"黑龙江省": ["哈尔滨市", "伊春市"]},
        "schools": {},
    }, ensure_ascii=False), encoding="utf-8")
    rl._display_order_cache = None   # 绕开其他用例留下的进程缓存，读本次写入
    regions = rl.school_regions()["regions"]
    heilongjiang = next(r for r in regions if r["province"] == "黑龙江省")
    # 词库里没有伊春市学校，但用户置顶过 → 必须露出而非静默丢弃
    assert "伊春市" in heilongjiang["cities"]
    assert [r["province"] for r in regions][0] == "黑龙江省"


def test_suggestions_return_full_and_short_names(isolated_paths):
    rl, _schools, _order = isolated_paths
    hits = rl.school_suggestions(prefix="哈尔滨师范")["suggestions"]
    # prefix 命中全称也能找到（旧实现只查 name，教师搜全称会一无所获）
    assert len(hits) == 1
    hit = hits[0]
    assert hit["full_name"] == "哈尔滨师范大学附属中学"
    assert hit["short_name"] == "哈师大附中"
    assert hit["name"] == "哈师大附中"
    empty = rl.school_suggestions(province="黑龙江省", city="哈尔滨市")["suggestions"]
    assert [e["name"] for e in empty] == ["哈师大附中", "哈三中"]


def test_update_school_short_name_by_full_name(isolated_paths):
    rl, schools, _order = isolated_paths
    result = rl.update_school_short_name({
        "full_name": "哈尔滨师范大学附属中学", "province": "黑龙江省",
        "city": "哈尔滨市", "short_name": "师大附",
    })
    assert result["status"] == "success"
    data = json.loads(schools.read_text(encoding="utf-8"))
    updated = next(e for e in data if e["full_name"] == "哈尔滨师范大学附属中学")
    assert updated["short_name"] == "师大附" and updated["name"] == "师大附"
    # 其他行不受波及
    untouched = next(e for e in data if e["full_name"] == "东北师范大学附属中学")
    assert untouched["short_name"] == "东北师大附中"
    # 缓存已刷新：联想立刻用新简称命中
    assert rl.school_suggestions(prefix="师大附")["suggestions"][0]["full_name"] == "哈尔滨师范大学附属中学"


def test_update_school_short_name_falls_back_to_name_for_legacy_rows(isolated_paths):
    rl, schools, _order = isolated_paths
    result = rl.update_school_short_name({
        "full_name": "无全称中学", "province": "黑龙江省", "city": "大庆市", "short_name": "大庆某中",
    })
    assert result["status"] == "success"
    data = json.loads(schools.read_text(encoding="utf-8"))
    assert any(e["name"] == "大庆某中" and e["full_name"] == "" for e in data)


def test_update_school_short_name_rejects_blank_or_unknown(isolated_paths):
    rl, _schools, _order = isolated_paths
    blank = rl.update_school_short_name({"full_name": "哈尔滨师范大学附属中学", "short_name": "  "})
    assert getattr(blank, "status_code", 200) == 400
    unknown = rl.update_school_short_name({"full_name": "不存在中学", "province": "黑龙江省", "short_name": "某中"})
    assert getattr(unknown, "status_code", 200) == 404


# ---------------------------------------------------------------------------
# 缺口回归（2026-10-03 用户批准）：投影保 id + PUT 按 id 定位
# ---------------------------------------------------------------------------

def test_load_schools_projection_keeps_stable_id(isolated_paths):
    """_load_schools 必须返回 id——PUT 靠它定位、批3 下拉靠 data-id 落 school_id。
    曾漏掉 id → _save_schools 写回把全库 id 抹掉，阶段 C 显示层永久回落快照旧文本。"""
    rl, _schools, _order = isolated_paths
    items = rl._load_schools()
    assert all("id" in e for e in items), "投影丢 id：任何一次成功 PUT 都会抹掉全库稳定 ID"
    by_name = {e["name"]: e["id"] for e in items}
    assert by_name["哈师大附中"] == "sch_hsf_0001"
    # 联想接口同样要带 id（前端拿不到 data-id 就断链）
    hit = rl.school_suggestions(prefix="哈尔滨师范")["suggestions"][0]
    assert hit.get("id") == "sch_hsf_0001"


def test_put_by_id_preserves_ids_and_follows_display_layer(isolated_paths, monkeypatch, tmp_path):
    """端到端：按 id PUT → 文件里每条仍有非空 id；QuestionSource.to_dict 立刻显示新简称。
    钉死「PUT 不丢 id」+「invalidate_school_index」两环（浏览器实测曾真炸过一次）。"""
    sl = _wire_school_library(monkeypatch, tmp_path)
    rl, schools, _order = isolated_paths
    (tmp_path / "schools.json").write_text(schools.read_text(encoding="utf-8"), encoding="utf-8")
    result = rl.update_school_short_name({"id": "sch_hsf_0001", "short_name": "师大附"})
    assert result["status"] == "success"
    data = json.loads(schools.read_text(encoding="utf-8"))
    assert all(str(e.get("id") or "").strip() for e in data if e.get("name") != "无全称中学"), \
        "PUT 写回后词库出现丢 id 条目"
    updated = next(e for e in data if e["id"] == "sch_hsf_0001")
    assert updated["short_name"] == "师大附" and updated["name"] == "师大附"
    from mathbank.database import QuestionSource
    row = QuestionSource(year="2023", region="师大附", exam_type="月考",
                         school_id="sch_hsf_0001").to_dict()
    assert row["region"] == "师大附" and "师大附" in row["text"]


def test_put_same_name_schools_only_touches_target_id(isolated_paths):
    """同名校（辽宁省实验 vs 河南省实验都叫「省实验」）改一个不得误伤另一个。"""
    rl, schools, _order = isolated_paths
    result = rl.update_school_short_name({"id": "sch_syl_03", "short_name": "辽实验"})
    assert result["status"] == "success"
    data = json.loads(schools.read_text(encoding="utf-8"))
    ln = next(e for e in data if e["id"] == "sch_syl_03")
    hn = next(e for e in data if e["id"] == "sch_syl_04")
    assert ln["short_name"] == "辽实验" and ln["name"] == "辽实验"
    assert hn["short_name"] == "省实验" and hn["name"] == "省实验", "同名另一所被误伤"


def test_put_legacy_full_name_ambiguous_is_rejected_not_guessed(isolated_paths):
    """旧载荷兼容路径：仅凭 name 命中多所同名 → 409 拒绝，绝不猜一条改写。"""
    rl, schools, _order = isolated_paths
    resp = rl.update_school_short_name({"name": "省实验", "short_name": "随便改"})
    assert getattr(resp, "status_code", 200) == 409
    data = json.loads(schools.read_text(encoding="utf-8"))
    assert all(e["short_name"] == "省实验" for e in data if e["name"] == "省实验"), "被拒后仍发生了写入"


def test_http_endpoints_require_local_token(client, isolated_paths):
    from main import LOCAL_TOKEN
    _rl, _schools, order = isolated_paths
    get_resp = client.get("/api/display-order")
    assert get_resp.status_code == 200
    assert get_resp.json()["provinces"]["_"][:3] == ["黑龙江省", "吉林省", "辽宁省"]
    bad = client.post("/api/display-order", json={"provinces": ["黑龙江省"], "cities": {}, "schools": {}})
    assert bad.status_code == 403  # 全局中间件拦截无 token 写操作
    ok = client.post("/api/display-order",
                     headers={"X-Local-Token": LOCAL_TOKEN},
                     json={"provinces": ["辽宁省", "黑龙江省"], "cities": {}, "schools": {}})
    assert ok.status_code == 200
    assert json.loads(order.read_text(encoding="utf-8"))["provinces"]["_"][0] == "辽宁省"


# ---------------------------------------------------------------------------
# 阶段 C：显示层按 school_id 现查简称
# ---------------------------------------------------------------------------

def _wire_school_library(monkeypatch, tmp_path):
    """把 school_library 的索引指到临时词库，并保证测试前后缓存干净。"""
    from mathbank import school_library

    def build():
        data = json.loads((tmp_path / "schools.json").read_text(encoding="utf-8")) if (tmp_path / "schools.json").exists() else []
        return {
            str(e["id"]): {
                "name": str(e.get("name") or ""),
                "full_name": str(e.get("full_name") or ""),
                "short_name": str(e.get("short_name") or e.get("name") or ""),
                "province": str(e.get("province") or ""),
                "city": str(e.get("city") or ""),
            } for e in data if e.get("id")
        }
    monkeypatch.setattr(school_library, "_cache", None)
    monkeypatch.setattr(school_library, "_read_library", build)
    school_library.invalidate_school_index()
    return school_library


def test_question_source_region_follows_school_short_name(isolated_paths, monkeypatch, tmp_path):
    rl, schools, _order = isolated_paths
    sl = _wire_school_library(monkeypatch, tmp_path)
    (tmp_path / "schools.json").write_text(json.dumps([{
        "id": "sch_a76135c1d2", "name": "哈师大附中", "full_name": "哈尔滨师范大学附属中学",
        "short_name": "哈师大附中", "province": "黑龙江省", "city": "哈尔滨市"}], ensure_ascii=False), encoding="utf-8")
    sl.invalidate_school_index()
    from mathbank.database import QuestionSource
    row = QuestionSource(year="2023", region="哈师大附中", exam_type="期末", school_id="sch_a76135c1d2")
    d = row.to_dict()
    assert d["region"] == "哈师大附中" and "哈师大附中" in d["text"]
    # 教师改简称 → 历史记录显示名自动跟随（不落 DB 快照）
    (tmp_path / "schools.json").write_text(json.dumps([{
        "id": "sch_a76135c1d2", "name": "师大附", "full_name": "哈尔滨师范大学附属中学",
        "short_name": "师大附", "province": "黑龙江省", "city": "哈尔滨市"}], ensure_ascii=False), encoding="utf-8")
    sl.invalidate_school_index()
    d2 = row.to_dict()
    assert d2["region"] == "师大附" and "师大附" in d2["text"]
    assert d2["school_id"] == "sch_a76135c1d2"


def test_question_source_falls_back_to_region_snapshot(isolated_paths, monkeypatch, tmp_path):
    sl = _wire_school_library(monkeypatch, tmp_path)
    (tmp_path / "schools.json").write_text("[]", encoding="utf-8")
    sl.invalidate_school_index()
    from mathbank.database import QuestionSource
    # 无 school_id（手输地区）→ 原文本
    legacy = QuestionSource(year="2022", region="全国甲卷", exam_type="高考").to_dict()
    assert legacy["region"] == "全国甲卷" and "全国甲卷" in legacy["text"]
    # 有 school_id 但词库已删校 → 回落该行 region 快照，不得空白
    orphan = QuestionSource(year="2023", region="哈师大附中", exam_type="月考",
                            school_id="sch_deleted").to_dict()
    assert orphan["region"] == "哈师大附中" and "哈师大附中" in orphan["text"]


def test_put_short_name_invalidates_display_layer_cache(isolated_paths, monkeypatch):
    """端到端：PUT 改简称后，QuestionSource.to_dict 必须立刻显示新简称——
    钉住 _save_schools 里 invalidate_school_index() 这一环（漏掉即历史题挂旧名）。"""
    from mathbank import school_library as sl
    rl, _schools, _order = isolated_paths

    def build_from_rl():
        return {
            f"fid_{i}": {"name": e["name"], "full_name": e["full_name"],
                         "short_name": e["short_name"], "province": e["province"], "city": e["city"]}
            for i, e in enumerate(rl._load_schools()) if e["full_name"]
        }
    monkeypatch.setattr(sl, "_cache", None)
    monkeypatch.setattr(sl, "_read_library", build_from_rl)
    fid = next(k for k, v in build_from_rl().items() if v["short_name"] == "哈师大附中")
    assert sl.resolve_school_label(fid, "") == "哈师大附中"
    rl.update_school_short_name({"full_name": "哈尔滨师范大学附属中学", "province": "黑龙江省",
                                 "city": "哈尔滨市", "short_name": "附中大"})
    assert sl.resolve_school_label(fid, "") == "附中大"


# ---------------------------------------------------------------------------
# 简称唯一性：撞车等于用户没法靠简称认校（内蒙古批次被 _strip_city 剥坏过）
# ---------------------------------------------------------------------------

def test_rename_cannot_produce_a_short_name_collision_in_the_same_province(isolated_paths):
    """同省两所学校改成同一个简称 → 拒绝写入，磁盘保持原样。

    事故原型：`_strip_city` 把「呼和浩特市第一中学」剥成「第一中学」，与包头/乌海/
    通辽/鄂尔多斯/巴彦淖尔的同名序号校在省级视图里全部撞车。
    """
    rl, schools, _order = isolated_paths
    data = json.loads(schools.read_text(encoding="utf-8"))
    data.append({"id": "sch_hh_0101", "name": "呼和浩特市第一中学",
                 "full_name": "呼和浩特市第一中学", "short_name": "呼和浩特市第一中学",
                 "province": "内蒙古自治区", "city": "呼和浩特市"})
    data.append({"id": "sch_bt_0102", "name": "包头市第一中学",
                 "full_name": "包头市第一中学", "short_name": "包头市第一中学",
                 "province": "内蒙古自治区", "city": "包头市"})
    schools.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    rl._schools_cache = None
    rl._schools_raw = {}
    rl._schools_fingerprint = None

    resp = rl.update_school_short_name({"id": "sch_bt_0102", "short_name": "呼和浩特市第一中学"})
    assert getattr(resp, "status_code", 200) == 409, "同省简称撞车却被放行"
    after = json.loads(schools.read_text(encoding="utf-8"))
    bt = next(e for e in after if e["id"] == "sch_bt_0102")
    assert bt["short_name"] == "包头市第一中学", "被拒后仍改写了目标行"
    hh = next(e for e in after if e["id"] == "sch_hh_0101")
    assert hh["short_name"] == "呼和浩特市第一中学", "冲突校验把别的行也带坏了"


def test_rename_to_your_own_current_short_name_is_not_a_self_collision(isolated_paths):
    """反向验证：把某校改成它**自己已有的**简称不得被判成撞车（判据不能把自己算进去）。"""
    rl, _schools, _order = isolated_paths
    result = rl.update_school_short_name({"id": "sch_h3z_000a", "short_name": "哈三中"})
    assert result["status"] == "success", "自我比对被误判为撞车"


def test_rename_across_different_provinces_is_allowed_even_when_names_match(isolated_paths):
    """反向验证：判据不得宽到「全库唯一」——跨省同名简称是合法存量（两所「省实验」）。"""
    rl, schools, _order = isolated_paths
    result = rl.update_school_short_name({"id": "sch_syl_03", "short_name": "省实验"})
    assert result["status"] == "success", "跨省同名被错误拒绝"
    data = json.loads(schools.read_text(encoding="utf-8"))
    ln = next(e for e in data if e["id"] == "sch_syl_03")
    hn = next(e for e in data if e["id"] == "sch_syl_04")
    assert ln["short_name"] == hn["short_name"] == "省实验"


def test_strip_city_style_prefix_removal_never_collapses_to_bare_numberal(isolated_paths):
    """钉住事故根因：地名是校名本体时不能剥。

    「呼和浩特市第一中学」被剥成「第一中学」后，与包头/乌海/通辽/鄂尔多斯/巴彦淖尔的
    同名序号校在省级视图里全部撞车。这里不测已删除的 _strip_city，而是把**结果形态**
    钉死：以裸序号或「市第」开头的简称一律视为坏数据，真实词库必须零命中。
    """
    import re
    bad_shape = re.compile(r"^(第[一二三四五六七八九十百千]+|市第)|^\d")
    real = Path(__file__).resolve().parents[1] / "mathbank" / "resources" / "schools.json"
    rows = json.loads(real.read_text(encoding="utf-8"))
    offenders = [r["full_name"] for r in rows if bad_shape.match(str(r.get("short_name") or "").strip())]
    assert not offenders, f"简称被剥成裸序号，省级视图必然撞车: {offenders}"


def test_real_library_has_no_same_province_short_name_collision():
    """真实词库不变式：同省内简称不得重复（简称是用户识别学校的唯一入口）。"""
    from collections import defaultdict
    real = Path(__file__).resolve().parents[1] / "mathbank" / "resources" / "schools.json"
    rows = json.loads(real.read_text(encoding="utf-8"))
    buckets = defaultdict(list)
    for r in rows:
        sn = str(r.get("short_name") or "").strip()
        if sn:
            buckets[(r["province"], sn)].append(r["full_name"])
    dup = {k: v for k, v in buckets.items() if len(v) > 1}
    assert not dup, f"同省简称撞车 {len(dup)} 组: {dup}"


# ---------------------------------------------------------------------------
# 离线改词库 → 运行中服务不得拿旧快照把改动覆盖掉（实测吞过一次 21 条简称修复）
# ---------------------------------------------------------------------------

def test_offline_library_edit_survives_the_next_rename(isolated_paths):
    """服务进程缓存着旧投影时，磁盘被离线改写后的一次 PUT 改名必须**不**回滚那些改动。

    `_save_schools` 是整文件写回：merge 基线一旦取进程内旧快照，离线回填的行就静默消失。
    """
    rl, schools, _order = isolated_paths
    # 1) 服务先读一遍，进程内留下旧快照
    rl._load_schools()
    assert rl._schools_cache is not None

    # 2) 「回填脚本」离线新增一所学校 + 改掉一条简称
    data = json.loads(schools.read_text(encoding="utf-8"))
    offline_new = {"id": "sch_offl_9999", "name": "离线补录中学",
                   "full_name": "内蒙古自治区离线补录中学", "short_name": "离线补录中学",
                   "province": "内蒙古自治区", "city": "呼和浩特市"}
    data.append(offline_new)
    for row in data:
        if row["id"] == "sch_h3z_000a":
            row["short_name"] = "哈三中（离线改过）"
    schools.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    # 关键：**故意不失效**进程内缓存——这才是「服务跑着时有人改了文件」的真实形态
    assert rl._schools_cache is not None

    # 3) 用户对另一所学校做一次正常改名
    result = rl.update_school_short_name({"id": "sch_dqsy_06", "short_name": "大庆试验"})
    assert result["status"] == "success"

    after = {e["id"]: e for e in json.loads(schools.read_text(encoding="utf-8"))}
    assert "sch_offl_9999" in after, "离线新增的学校被这次改名整文件写回抹掉了"
    assert after["sch_offl_9999"]["short_name"] == "离线补录中学"
    assert after["sch_h3z_000a"]["short_name"] == "哈三中（离线改过）", "离线改的简称被旧快照覆盖回去"
    assert after["sch_dqsy_06"]["short_name"] == "大庆试验"


def test_fingerprint_guard_does_not_reload_on_every_read(isolated_paths, monkeypatch):
    """反向验证：指纹一致时必须走缓存——不得把守卫写成「每次都重读磁盘」的空守卫。"""
    rl, schools, _order = isolated_paths
    rl._load_schools()
    reads = []
    real_read = Path.read_text

    def counting_read(self, *a, **kw):
        reads.append(str(self))
        return real_read(self, *a, **kw)
    monkeypatch.setattr(Path, "read_text", counting_read)
    for _ in range(5):
        rl._load_schools()
    assert reads == [], f"缓存命中仍重复读盘 {len(reads)} 次：守卫退化成每次重读"
    schools.write_text(schools.read_text(encoding="utf-8"), encoding="utf-8")
    os.utime(schools, ns=(999999999999, 999999999999))
    rl._load_schools()
    assert reads, "磁盘变了却没重读——守卫根本没生效"


# ---------------------------------------------------------------------------
# POST /api/schools：排序面板「+ 新增学校」写入入口
# ---------------------------------------------------------------------------

def _create(rl, **fields):
    return rl.create_school(fields)


def test_create_school_appends_row_with_deterministic_id(isolated_paths):
    """新增成功：id = sch_+sha1(全称|省|市)[:10]，六字段按用户填的原样落盘。"""
    import hashlib
    rl, schools, _order = isolated_paths
    resp = _create(rl, full_name="呼和浩特市新桥中学", short_name="新桥中学",
                   province="内蒙古自治区", city="呼和浩特市", tier="市重", nature="公办")
    assert resp["ok"] is True
    expected = "sch_" + hashlib.sha1("呼和浩特市新桥中学|内蒙古自治区|呼和浩特市".encode("utf-8")).hexdigest()[:10]
    assert resp["id"] == expected
    data = {e["id"]: e for e in json.loads(schools.read_text(encoding="utf-8"))}
    row = data[expected]
    assert row["full_name"] == "呼和浩特市新桥中学"
    assert row["short_name"] == "新桥中学" and row["name"] == "新桥中学"
    assert row["province"] == "内蒙古自治区" and row["city"] == "呼和浩特市"
    # tier/nature 必存：这次抓取的核心需求就是区分档次与性质
    assert row["tier"] == "市重" and row["nature"] == "公办"


def test_create_school_leaves_every_existing_row_untouched(isolated_paths):
    """新增只追加：既有行的**已建模字段逐字不变**（值层面），行数 +1。

    写回会给旧行补齐 tier/nature/source 三个空键（投影口径），所以按值比较而不是比原始 JSON：
    真正要钉住的是「没有任何一行的内容被这次新增改动」。
    """
    rl, schools, _order = isolated_paths
    before = json.loads(schools.read_text(encoding="utf-8"))
    _create(rl, full_name="包头市测试中学", short_name="测试中学",
            province="内蒙古自治区", city="包头市", tier="", nature="民办")
    after = json.loads(schools.read_text(encoding="utf-8"))
    assert len(after) == len(before) + 1
    by_id = {e["id"]: e for e in after}
    for old in before:
        new = by_id[old["id"]]
        for key, value in old.items():
            assert str(new.get(key) or "") == str(value or ""), f"既有行被改写: {old['id']}.{key}"


def test_create_school_preserves_unmodeled_keys_on_existing_rows(isolated_paths):
    """merge 基线必须保住未建模字段：新增一所学校不得把旧行的自定义键抹掉。"""
    rl, schools, _order = isolated_paths
    data = json.loads(schools.read_text(encoding="utf-8"))
    data[0]["custom_extra"] = "必须存活"
    data[0]["tier"] = "省重"
    schools.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    rl._schools_cache = None
    rl._schools_raw = {}
    rl._schools_fingerprint = None
    _create(rl, full_name="通辽市测试中学", short_name="通辽测试",
            province="内蒙古自治区", city="通辽市", tier="", nature="公办")
    after = {e["id"]: e for e in json.loads(schools.read_text(encoding="utf-8"))}
    assert after[data[0]["id"]].get("custom_extra") == "必须存活"
    assert after[data[0]["id"]]["tier"] == "省重"


def test_create_school_requires_all_four_mandatory_fields(isolated_paths):
    rl, schools, _order = isolated_paths
    before = schools.read_text(encoding="utf-8")
    for missing in ("province", "city", "full_name", "short_name"):
        payload = {"full_name": "测试中学", "short_name": "测试", "province": "黑龙江省",
                   "city": "哈尔滨市", "tier": "", "nature": "公办"}
        payload[missing] = ""
        resp = _create(rl, **payload)
        assert getattr(resp, "status_code", 200) == 400, f"{missing} 为空却被接受"
        assert "请填写" in resp.body.decode("utf-8")
    assert schools.read_text(encoding="utf-8") == before, "校验失败仍写了盘"


def test_create_school_rejects_duplicate_full_name_in_same_city(isolated_paths):
    rl, schools, _order = isolated_paths
    resp = _create(rl, full_name="哈尔滨市第三中学校", short_name="哈三中新简称",
                   province="黑龙江省", city="哈尔滨市", tier="", nature="公办")
    assert getattr(resp, "status_code", 200) == 409, "同省同市同全称未被拒"
    assert len(json.loads(schools.read_text(encoding="utf-8"))) == 8


def test_create_school_rejects_short_name_collision_within_province(isolated_paths):
    """撞车判据与 PUT 改名同一口径：省级唯一（复用刚加的守卫语义）。"""
    rl, schools, _order = isolated_paths
    resp = _create(rl, full_name="大庆市测试中学", short_name="哈三中",
                   province="黑龙江省", city="大庆市", tier="", nature="公办")
    assert getattr(resp, "status_code", 200) == 409, "同省简称撞车却被放行"
    assert len(json.loads(schools.read_text(encoding="utf-8"))) == 8


def test_create_school_allows_same_short_name_across_provinces(isolated_paths):
    """反向验证：判据不得宽到全库唯一——跨省同名简称合法（两所「省实验」）。"""
    rl, schools, _order = isolated_paths
    resp = _create(rl, full_name="河南省测试中学", short_name="哈师大附中",
                   province="河南省", city="郑州市", tier="", nature="公办")
    assert resp["ok"] is True
    assert len(json.loads(schools.read_text(encoding="utf-8"))) == 9


def test_create_school_rejects_unknown_tier_or_nature(isolated_paths):
    """下拉之外的值一律拒绝：不瞎填也意味着不许乱填。"""
    rl, _schools, _order = isolated_paths
    bad_tier = _create(rl, full_name="某中", short_name="某中甲", province="黑龙江省",
                       city="哈尔滨市", tier="超级重", nature="公办")
    assert getattr(bad_tier, "status_code", 200) == 400
    bad_nature = _create(rl, full_name="某中乙", short_name="某中乙", province="黑龙江省",
                         city="哈尔滨市", tier="", nature="公立")
    assert getattr(bad_nature, "status_code", 200) == 400


def test_create_school_blank_tier_is_allowed_and_stays_blank(isolated_paths):
    """档次判断不了留空是硬规矩：空串必须原样入库，不得被默认值顶替。"""
    rl, schools, _order = isolated_paths
    resp = _create(rl, full_name="呼伦贝尔测试中学", short_name="呼伦贝尔测试",
                   province="内蒙古自治区", city="呼伦贝尔市", tier="", nature="")
    assert resp["ok"] is True
    row = next(e for e in json.loads(schools.read_text(encoding="utf-8")) if e["id"] == resp["id"])
    assert row["tier"] == "" and row["nature"] == ""


def test_create_school_id_collision_does_not_touch_existing_id(isolated_paths):
    """确定性 id 撞上历史冻结 id 时另开一行，绝不覆盖已有行（被引 10 个 id 恒冻结）。"""
    rl, schools, _order = isolated_paths
    first = _create(rl, full_name="伊金霍洛旗第一中学", short_name="伊旗一中",
                    province="内蒙古自治区", city="鄂尔多斯市", tier="", nature="公办")
    assert first["ok"] is True
    # 手工把另一所学校的 id 换成 first 的 id，模拟历史冻结 ID 冲突
    data = json.loads(schools.read_text(encoding="utf-8"))
    for row in data:
        if row["id"] == first["id"]:
            row["short_name"] = "伊旗一中"
            row["full_name"] = "已被历史 ID 占用的学校"
            row["province"] = "吉林省"
            row["city"] = "长春市"
            row["source"] = "frozen-id-marker"
    schools.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    rl._schools_cache = None
    rl._schools_raw = {}
    rl._schools_fingerprint = None

    second = _create(rl, full_name="已存在学校第二校区", short_name="二校区",
                     province="内蒙古自治区", city="呼和浩特市", tier="", nature="公办")
    assert second["ok"] is True
    assert second["id"] != first["id"], "撞车后复用了已有 id"
    after = {e["id"]: e for e in json.loads(schools.read_text(encoding="utf-8"))}
    assert after[first["id"]]["source"] == "frozen-id-marker", "历史冻结行被覆盖"
    assert first["id"] in after and second["id"] in after


def test_create_school_visible_through_regions_and_suggestions(isolated_paths):
    """缓存刷新闭环：新增后 GET 侧必须立刻看得见（否则面板刷了也是旧列表）。"""
    rl, _schools, _order = isolated_paths
    resp = _create(rl, full_name="齐齐哈尔市测试新增中学", short_name="齐市测试新增",
                   province="黑龙江省", city="齐齐哈尔市", tier="省重", nature="公办")
    hit = [s for s in rl.school_suggestions(province="黑龙江省", city="齐齐哈尔市")["suggestions"]
           if s["id"] == resp["id"]]
    assert hit, "新增学校在联想接口里查不到"
    assert hit[0]["tier"] == "省重" and hit[0]["nature"] == "公办"
    regions = rl.school_regions()["regions"]
    node = next(r for r in regions if r["province"] == "黑龙江省")
    assert "齐齐哈尔市" in node["cities"]


def test_http_post_schools_requires_local_token(client, isolated_paths):
    from main import LOCAL_TOKEN
    _rl, schools, _order = isolated_paths
    denied = client.post("/api/schools", json={"full_name": "越权中学", "short_name": "越权",
                                               "province": "黑龙江省", "city": "哈尔滨市"})
    assert denied.status_code == 403
    rows_before = len(json.loads(schools.read_text(encoding="utf-8")))
    ok = client.post("/api/schools", headers={"X-Local-Token": LOCAL_TOKEN},
                     json={"full_name": "双城区测试高中", "short_name": "双城测试",
                           "province": "黑龙江省", "city": "哈尔滨市",
                           "tier": "普高", "nature": "公办"})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert len(json.loads(schools.read_text(encoding="utf-8"))) == rows_before + 1
