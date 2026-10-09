"""P2-C4 干预建档/关闭路由测试（契约 docs/diagnosis-roadmap/p2-contracts.md §5.1/§5.4）。

覆盖：
- 普通档案完全向后兼容：响应含旧字段 + 新扩展列全 null；
- 干预建档：任一扩展列显式传入 → status=open、target_metric 校验（唯一
  口径 metric_meta）、基线自动捕获（复用 app.diagnosis.review，成绩口径
  同源，不另算第二口径）；
- 防重复录入（§5.1）：同人同科已有未关闭干预 → 409 duplicate_follow_up +
  existing 明细；force=true 可仍建；不同科不受限；关闭后可再建；
- follow_up_done 关闭路径回归（§5.4）：旧路径 follow_up_done=1 关闭干预 →
  status 镜像 done，未关闭计数归零；status=dismissed 关闭 → follow_up_done
  镜像 1；旧档案（status=NULL）行为一律不变（不碰旧列语义）；
- 参数校验：非法 status / review_date 早于 start_date → 422；
- N01 域隔离：teaching 干预经 homeroom 路径不可见。

样本全部合成（秦档/秦钥/秦门/秦理·T 等，见 tests/v2/conftest.py）。
"""

from datetime import date, timedelta

import pytest

API = "/api/v1"

METRIC_MAIN3 = "total:主三门"


def _iso(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def _create(client, person_id, **overrides):
    payload = {
        "date": _iso(0),
        "category": "谈话",
        "content": "常规谈话记录",
        "follow_up": "一周后再谈",
    }
    payload.update(overrides)
    return client.post(
        f"{API}/homeroom/students/{person_id}/notes", json=payload
    )


@pytest.mark.usefixtures("p2c4_seed")
class TestInterventionCreate:
    def test_plain_note_backward_compatible(self, client, p2c4_seed):
        """普通档案：不传扩展列 → 旧行为不变，新列全 null。"""
        resp = _create(client, p2c4_seed.dang_id)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        for key in ("id", "person_id", "date", "category", "content",
                    "follow_up", "follow_up_done", "created_at"):
            assert key in body
        for key in ("problem", "subject_scope", "measures", "target_metric",
                    "baseline_value", "start_date", "review_date", "status"):
            assert body[key] is None, f"{key} 应为 null"

    def test_intervention_defaults_to_open_with_auto_baseline(self, client, p2c4_seed):
        """干预建档：status=open，基线按 total:主三门 自动捕获（共享口径）。"""
        resp = _create(
            client, p2c4_seed.dang_id,
            problem="主三门名次持续下滑",
            subject_scope=None,
            measures="每周一次错题面批",
            target_metric=METRIC_MAIN3,
            start_date=_iso(30),
            review_date=_iso(-10),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "open"
        assert body["target_metric"] == METRIC_MAIN3
        assert body["start_date"] == _iso(30)
        assert body["review_date"] == _iso(-10)
        baseline = body["baseline_value"]
        assert baseline["metric"] == METRIC_MAIN3
        assert baseline["value"] == 210.0  # 秦档 C4基线考 xueji_rank
        assert baseline["unit"] == "rank"
        assert baseline["exam_name"] == p2c4_seed.exam_base
        assert baseline["source"] == "auto"

    def test_duplicate_same_person_same_scope_409_then_force(self, client, p2c4_seed):
        """同人同科（subject_scope 逐字相等）未关闭干预 → 409 + existing。"""
        first = _create(
            client, p2c4_seed.men_id,
            problem="数学作业连缺",
            subject_scope="数学",
            measures="每日作业面检",
            target_metric="subject:数学",
            start_date=_iso(20),
        )
        assert first.status_code == 200, first.text
        dup = _create(
            client, p2c4_seed.men_id,
            problem="再次登记数学问题",
            subject_scope="数学",
            measures="再谈一次",
            target_metric="subject:数学",
        )
        assert dup.status_code == 409, dup.text
        body = dup.json()
        assert body["error"] == "duplicate_follow_up"
        existing = body["existing"]
        assert len(existing) == 1
        assert existing[0]["status"] == "open"
        assert existing[0]["subject_scope"] == "数学"

        # 不同科不受限（同人不同科）
        other = _create(
            client, p2c4_seed.men_id,
            problem="语文课堂走神",
            subject_scope="语文",
            measures="调整座位",
        )
        assert other.status_code == 200, other.text

        # 教师确认知情：force=true 仍建成功
        forced = _create(
            client, p2c4_seed.men_id,
            problem="数学问题（确认重复）",
            subject_scope="数学",
            measures="第二条干预",
            force=True,
        )
        assert forced.status_code == 200, forced.text
        assert forced.json()["status"] == "open"

    def test_duplicate_requires_same_scope(self, client, p2c4_seed):
        """subject_scope 为 null 与 "数学" 互不视为同科重复。"""
        a = _create(
            client, p2c4_seed.yu_id,
            problem="不区分学科的干预", measures="观察一周",
        )
        assert a.status_code == 200, a.text
        b = _create(
            client, p2c4_seed.yu_id,
            problem="英语干预", subject_scope="英语", measures="听力加练",
        )
        assert b.status_code == 200, b.text

    def test_invalid_target_metric_422(self, client, p2c4_seed):
        """非法 target_metric（高二无 total:五门）→ 422 invalid_scope_param。"""
        resp = _create(
            client, p2c4_seed.zhi_id,
            problem="指标笔误", target_metric="total:五门", measures="更正指标",
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"] == "invalid_scope_param"

    def test_no_facts_baseline_null_but_created(self, client, p2c4_seed):
        """零成绩学生也可建档；基线捕获不到 → null（复查对照如实 pending）。"""
        resp = _create(
            client, p2c4_seed.wu2_id,
            problem="新转入，暂无成绩参照",
            measures="先观察",
            target_metric=METRIC_MAIN3,
            start_date=_iso(10),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["baseline_value"] is None
        assert resp.json()["status"] == "open"

    def test_review_date_before_start_422(self, client, p2c4_seed):
        resp = _create(
            client, p2c4_seed.shi_id,
            problem="日期倒置", start_date=_iso(5), review_date=_iso(10),
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"] == "invalid_scope_param"


@pytest.mark.usefixtures("p2c4_seed")
class TestInterventionClose:
    def _open_intervention(self, client, person_id, subject_scope=None):
        resp = _create(
            client, person_id,
            problem="待关闭的干预",
            subject_scope=subject_scope,
            measures="谈一次",
            target_metric=METRIC_MAIN3,
            start_date=_iso(30),
            review_date=_iso(-30),
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def test_close_via_follow_up_done_legacy_path(self, client, p2c4_seed):
        """§5.4 回归：旧关闭路径 follow_up_done=1 仍然有效，且镜像 status=done。"""
        note = self._open_intervention(client, p2c4_seed.men_id, subject_scope=None)
        resp = client.patch(
            f"{API}/homeroom/notes/{note['id']}", json={"follow_up_done": 1}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["follow_up_done"] == 1
        assert body["status"] == "done"

        # 重开（旧路径回拨）→ status 镜像回 open
        reopen = client.patch(
            f"{API}/homeroom/notes/{note['id']}", json={"follow_up_done": 0}
        )
        assert reopen.status_code == 200, reopen.text
        assert reopen.json()["status"] == "open"

    def test_close_via_status_dismissed(self, client, p2c4_seed):
        """status=dismissed 关闭 → follow_up_done 镜像 1（B1 未关闭计数不虚增）。"""
        note = self._open_intervention(client, p2c4_seed.yue_id, subject_scope=None)
        resp = client.patch(
            f"{API}/homeroom/notes/{note['id']}", json={"status": "dismissed"}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "dismissed"
        assert body["follow_up_done"] == 1

    def test_close_via_status_done_allows_new_intervention(self, client, p2c4_seed):
        """关闭后同科可再建（防重复只对未关闭干预生效）。"""
        note = self._open_intervention(client, p2c4_seed.suo_id, subject_scope="数学")
        done = client.patch(
            f"{API}/homeroom/notes/{note['id']}", json={"status": "done"}
        )
        assert done.status_code == 200, done.text
        again = _create(
            client, p2c4_seed.suo_id,
            problem="数学第二轮干预",
            subject_scope="数学",
            measures="换一种方式",
        )
        assert again.status_code == 200, again.text

    def test_legacy_note_no_status_sync(self, client, p2c4_seed):
        """旧档案（status=NULL）follow_up_done 切换语义完全不变。"""
        resp = _create(client, p2c4_seed.dang_id)
        assert resp.status_code == 200
        note = resp.json()
        assert note["status"] is None
        done = client.patch(
            f"{API}/homeroom/notes/{note['id']}", json={"follow_up_done": 1}
        )
        assert done.status_code == 200, done.text
        body = done.json()
        assert body["follow_up_done"] == 1
        assert body["status"] is None  # 旧档案不镜像 status

    def test_invalid_status_422(self, client, p2c4_seed):
        note = self._open_intervention(client, p2c4_seed.zhi_id)
        resp = client.patch(
            f"{API}/homeroom/notes/{note['id']}", json={"status": "closed"}
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"] == "invalid_scope_param"

    def test_teaching_intervention_hidden_from_homeroom(self, client, p2c4_seed):
        """N01：teaching 域干预经 homeroom 复查/档案路径一律 404。"""
        create = client.post(
            f"{API}/teaching/students/{p2c4_seed.li_t_id}/notes",
            json={
                "date": _iso(0),
                "category": "谈话",
                "content": "物理作业连缺提醒",
                "problem": "物理作业连缺",
                "measures": "每日核对作业",
                "target_metric": "subject_grade:物理",
                "start_date": _iso(14),
            },
        )
        assert create.status_code == 200, create.text
        note_id = create.json()["id"]
        homeroom_view = client.get(
            f"{API}/homeroom/diagnosis/review-contrast?follow_up_id={note_id}"
        )
        assert homeroom_view.status_code == 404, homeroom_view.text
        assert homeroom_view.json()["error"] == "resource_out_of_scope"
