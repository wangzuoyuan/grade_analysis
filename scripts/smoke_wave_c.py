"""Wave C 合并后真实数据冒烟（脱敏输出）：P2 相关性/行动首页/诊断报告/干预复查。

前置：对快照副本执行 alembic upgrade head（演练生产升级路径 0017）。
"""
import os
import sys

import httpx

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:8901")
AY = 1
CLASS = 1


def main():
    c = httpx.Client(base_url=BASE, timeout=60)
    out = []
    exams = c.get("/api/exams", params={"grade": 1}).json()["exams"]
    latest = exams[0]
    pid = (
        c.get(
            f"/api/v1/homeroom/analysis/exams/{latest['name']}/rank-range",
            params={"academic_year_id": AY, "class_id": CLASS, "metric": "total:主三门",
                    "rank_min": 1, "rank_max": 700},
        ).json()["students"][0]["person_id"]
    )

    # 1) C1 相关性
    r = c.get(
        "/api/v1/homeroom/diagnosis/correlation",
        params={"exam_name": latest["name"], "window_days": 30, "metric": "subject:语文",
                "academic_year_id": AY, "class_id": CLASS},
    )
    assert r.status_code == 200, r.text[:400]
    corr = r.json()
    sl = corr.get("sample", {}).get("layers") or {}
    stats = {k: (v.get("n"), v.get("status")) for k, v in sl.items() if isinstance(v, dict)}
    out.append(f"[C1] correlation window={corr.get('window_days')} r={corr.get('r')} rho={corr.get('rho')} 层={stats}")
    out.append(f"[C1] 月精度标注={any('month_precision' in str(c) for c in (corr.get('caveats') or []))}")
    out.append(f"[C1] note含因果免责={'不构成因果' in (corr.get('note') or '') or '因果' in (corr.get('note') or '')}")

    # 2) C2 行动首页
    r = c.get(
        "/api/v1/homeroom/diagnosis/action-summary",
        params={"academic_year_id": AY, "class_id": CLASS},
    )
    assert r.status_code == 200, r.text[:400]
    act = r.json()
    pp = act.get("priority_persons") or []
    out.append(
        f"[C2] action-summary 优先关注={len(pp)} 理由样本={[p['reasons'][:2] for p in pp[:2]]} "
        f"sections键={sorted((act.get('sections') or {}).keys())}"
    )

    # 3) C3 诊断报告
    r = c.get(
        "/api/v1/homeroom/diagnosis/report",
        params={"person_id": pid, "academic_year_id": AY, "class_id": CLASS},
    )
    assert r.status_code == 200, r.text[:400]
    rep = r.json()
    sugg_obj = rep.get("suggestions") or {}
    items = sugg_obj.get("items") if isinstance(sugg_obj, dict) else sugg_obj
    items = items or []
    out.append(
        f"[C3] report 建议条数={len(items)}（契约 1-3） generated={sugg_obj.get('generated') if isinstance(sugg_obj, dict) else None} "
        f"layer_legend={'layer_legend' in rep}"
    )

    # 4) C4 干预建档 + 复查对照（合成内容，写入的是一次性冒烟副本）
    note = {
        "date": "2026-05-10",
        "category": "谈话",
        "content": "P2冒烟合成干预记录（非真实）",
        "problem": "数学作业连续缺交",
        "subject_scope": "数学",
        "measures": "每日订正打卡",
        "target_metric": "total:主三门",
        "baseline_value": {"value": 300, "metric": "total:主三门"},
        "start_date": "2026-05-10",
        "review_date": "2026-06-01",
    }
    r = c.post(f"/api/v1/homeroom/students/{pid}/notes", json=note)
    out.append(f"[C4] 干预建档 POST → {r.status_code}")
    note_id = r.json().get("id") if r.status_code in (200, 201) else None
    if note_id:
        r2 = c.post(f"/api/v1/homeroom/students/{pid}/notes", json={**note, "content": "重复测试"})
        out.append(f"[C4] 防重复（同人同科）→ {r2.status_code}（预期 409）")
        r3 = c.get(
            "/api/v1/homeroom/diagnosis/review-contrast",
            params={"follow_up_id": note_id, "academic_year_id": AY, "class_id": CLASS},
        )
        out.append(f"[C4] review-contrast → {r3.status_code} 摘要={str(r3.json())[:220]}")

    print("\n".join(out))
    print("SMOKE_C_OK")


if __name__ == "__main__":
    sys.exit(main())
