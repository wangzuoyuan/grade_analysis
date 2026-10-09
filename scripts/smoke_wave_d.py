"""终局真实数据冒烟（脱敏输出）：P3 教研统计 + 全家桶抽检。"""
import os
import sys

import httpx

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:8901")


def main():
    c = httpx.Client(base_url=BASE, timeout=60)
    out = []
    exams = c.get("/api/exams", params={"grade": 1}).json()["exams"]
    earliest, latest = exams[-1], exams[0]

    # 1) 队列清单
    r = c.get("/api/v1/homeroom/diagnosis/research/cohorts",
              params={"academic_year_id": 1, "class_id": 1})
    assert r.status_code == 200, r.text[:300]
    co = r.json()
    keys = list(co.keys())
    out.append(f"[D1] cohorts 顶层键={keys[:8]}")

    # 2) 干预队列结局（真实库无干预 → 空队列诚实路径）
    r = c.get("/api/v1/homeroom/diagnosis/research/outcome",
              params={"cohort": "interventions", "from_exam": earliest["name"],
                      "to_exam": latest["name"], "academic_year_id": 1, "class_id": 1,
                      "metric": "total:主三门"})
    assert r.status_code == 200, r.text[:300]
    oc = r.json()
    out.append(
        f"[D1] 干预队列 outcome → status={oc.get('status')} comparable_n={oc.get('comparable_n')} "
        f"三段={(oc.get('improved_n'), oc.get('flat_n'), oc.get('declined_n'))} "
        f"limitation存在={'limitations' in oc} rules_version={oc.get('rules_version')}"
    )

    # 3) 类型队列（当前时点口径）
    r = c.get("/api/v1/homeroom/diagnosis/research/outcome",
              params={"cohort": f"type:综合风险型@{latest['name']}", "from_exam": earliest["name"],
                      "to_exam": latest["name"], "academic_year_id": 1, "class_id": 1,
                      "metric": "total:主三门"})
    assert r.status_code == 200, r.text[:300]
    tc = r.json()
    out.append(
        f"[D1] 类型队列 outcome → status={tc.get('status')} comparable_n={tc.get('comparable_n')} "
        f"三段={(tc.get('improved_n'), tc.get('flat_n'), tc.get('declined_n'))} "
        f"median={tc.get('median_change')} 明细人数={len(tc.get('students') or [])} "
        f"membership_basis={tc.get('membership_basis')} limitation存在={'limitations' in tc}"
    )

    # 4) 全家桶抽检（四波核心端点仍绿）
    checks = [
        ("A3 配置", "/api/analysis-config"),
        ("B1 特征", "/api/v1/homeroom/diagnosis/features?person_id=9&academic_year_id=1&class_id=1"),
        ("B2 类型", "/api/v1/homeroom/diagnosis/types?person_id=9&academic_year_id=1&class_id=1"),
        ("C2 行动", "/api/v1/homeroom/diagnosis/action-summary?academic_year_id=1&class_id=1"),
        ("C3 报告", "/api/v1/homeroom/diagnosis/report?person_id=9&academic_year_id=1&class_id=1"),
    ]
    for label, url in checks:
        rr = c.get(url)
        out.append(f"[回归] {label} → {rr.status_code}")

    print("\n".join(out))
    print("SMOKE_D_OK")


if __name__ == "__main__":
    sys.exit(main())
