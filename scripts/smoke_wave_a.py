"""Wave A 合并后真实数据冒烟（脱敏输出）。

只打印聚合计数与语义字段；绝不打印学生姓名、学号或任何可识别信息。
用法：EXAM_TRACKER_DIR 指向快照副本，针对运行中的后端执行。
"""
import json
import os
import sys

import httpx

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:8901")
AY = 1  # 2025-2026 学年（真实数据所在）
CLASS = 1


def mask_name(name):
    return (name[0] + "***") if name else name


def main():
    c = httpx.Client(base_url=BASE, timeout=30)
    results = []

    # 1) A3：长期分段配置
    r = c.get("/api/analysis-config")
    assert r.status_code == 200, r.text
    cfg = r.json()
    results.append(
        f"[A3] /api/analysis-config → defaults={cfg.get('defaults')} is_default={cfg.get('is_default')}"
    )

    # 2) 考试列表（旧端点，拿最新考试名，不打印全名）
    r = c.get("/api/exams", params={"grade": 1})
    assert r.status_code == 200, r.text[:500]
    exams = r.json() if isinstance(r.json(), list) else r.json().get("exams", [])
    results.append(f"[旧路径] /api/exams?grade=1 → 数量={len(exams)} 名称已脱敏={[mask_name(e.get('name','')) for e in exams]}")
    exam_name = exams[0]["name"] if exams else None  # 按 exam_date 倒序，第一个最新

    # 3) A1：总分 rank-range（真实名次应进入名单）
    r = c.get(
        f"/api/v1/homeroom/analysis/exams/{exam_name}/rank-range",
        params={
            "academic_year_id": AY,
            "class_id": CLASS,
            "metric": "total:主三门",
            "rank_min": 1,
            "rank_max": 600,
        },
    )
    assert r.status_code == 200, r.text[:500]
    body = r.json()
    ranks = sorted(s["year_rank"] for s in body["students"] if s.get("year_rank"))
    results.append(
        f"[A1] 总分rank-range 1-600 → 人数={len(body['students'])} "
        f"名次范围=({ranks[0] if ranks else 'NA'}~{ranks[-1] if ranks else 'NA'}) "
        f"note含『不按百分位推算名次』={'不按百分位推算名次' in body['metric_note']}"
    )

    # 4) A1：单科 rank-range（真实库单科无名次 → 空名单 + 缺失说明）
    r = c.get(
        f"/api/v1/homeroom/analysis/exams/{exam_name}/rank-range",
        params={
            "academic_year_id": AY,
            "class_id": CLASS,
            "metric": "subject:语文",
            "rank_min": 1,
            "rank_max": 600,
        },
    )
    assert r.status_code == 200, r.text[:500]
    body = r.json()
    results.append(
        f"[A1] 单科rank-range → 人数={len(body['students'])}（预期 0，单科无真实名次） "
        f"note={body['metric_note']}"
    )

    # 5) A1：单人趋势（取名单里第一个 person_id，不打印姓名）
    pid = None
    r2 = c.get(
        f"/api/v1/homeroom/analysis/exams/{exam_name}/rank-range",
        params={"academic_year_id": AY, "class_id": CLASS, "metric": "total:主三门",
                "rank_min": 1, "rank_max": 700},
    )
    students = r2.json()["students"]
    pid = students[0]["person_id"] if students else None
    if pid:
        r = c.get(
            "/api/v1/homeroom/analysis/trends",
            params={"person_id": pid, "academic_year_id": AY, "class_id": CLASS},
        )
        assert r.status_code == 200, r.text[:500]
        years = r.json().get("years", [])
        for y in years:
            for ttype, points in (y.get("totals") or {}).items():
                if isinstance(points, dict):
                    points = list(points.values())
                bases = [p.get("rank_basis") for p in points]
                none_ct = sum(1 for b in bases if b is None)
                results.append(
                    f"[A1] trends 学年={y.get('academic_year_id')} {ttype} → 点数={len(points)} "
                    f"rank_basis=None(缺考/缺数据)点数={none_ct} 其余basis集合={sorted({str(b) for b in bases if b})}"
                )

    # 6) 旧路径仍工作：band-trend / focus-list
    r = c.get("/api/band-trend", params={"grade": 1})
    ok1 = r.status_code == 200
    exam_id = exams[0]["id"] if exams else None
    r = c.get(f"/api/focus-list/{exam_id}")
    ok2 = r.status_code == 200
    results.append(f"[旧路径] band-trend 200={ok1} focus-list 200={ok2}（兼容入口存活）")

    # 7) /mcp 未启用时不应挂载
    r = c.get("/mcp")
    results.append(f"[A2] MCP_ENABLED 关闭时 /mcp → {r.status_code}（预期 404）")

    print("\n".join(results))
    print("SMOKE_OK")


if __name__ == "__main__":
    sys.exit(main())
