"""Wave B 合并后真实数据冒烟（脱敏输出）：P1 诊断特征层端点。"""
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
    latest, earliest = exams[0], exams[-1]
    out.append(f"[冒烟] 考试数={len(exams)}（名称脱敏）")

    # 1) 单生 features
    r = c.get(
        "/api/v1/homeroom/analysis/exams/%s/rank-range" % latest["name"],
        params={"academic_year_id": AY, "class_id": CLASS, "metric": "total:主三门",
                "rank_min": 1, "rank_max": 700},
    )
    pid = r.json()["students"][0]["person_id"]
    r = c.get(
        "/api/v1/homeroom/diagnosis/features",
        params={"person_id": pid, "academic_year_id": AY, "class_id": CLASS},
    )
    assert r.status_code == 200, r.text[:400]
    f = r.json()
    ind = f["indicators"]
    statuses = {k: v.get("status") for k, v in ind.items()}
    out.append(f"[B1] features calc_version={f['calc_version']} 指标status={statuses}")
    lc = ind["trend"].get("last_change")
    out.append(
        f"[B1] trend.last_change.rank_change={lc and lc.get('rank_change')}（负=进步） "
        f"direction={ind['trend'].get('direction_recent')} stability={ind['stability'].get('label')} "
        f"imbalance_severe={ind['imbalance'].get('severe')} hw30d={ind['homework_behavior'].get('missing_30d')}"
    )

    # 2) 单生 types（接线后走 B1）
    r = c.get(
        "/api/v1/homeroom/diagnosis/types",
        params={"person_id": pid, "academic_year_id": AY, "class_id": CLASS},
    )
    assert r.status_code == 200, r.text[:400]
    t = r.json()
    out.append(
        f"[B2] types main={t.get('main_type')} 次标签={t.get('secondary_tags')} "
        f"status={t.get('classification_status')} evidence条数={len(t.get('evidence', []))}"
    )

    # 3) 变化分解：学生 + 班级
    r = c.get(
        "/api/v1/homeroom/diagnosis/changes/student",
        params={"person_id": pid, "from_exam": earliest["name"], "to_exam": latest["name"],
                "academic_year_id": AY, "class_id": CLASS},
    )
    assert r.status_code == 200, r.text[:400]
    sc = r.json()
    movers = [m.get("subject") for m in (sc.get("top_movers") or [])]
    out.append(f"[B3] 学生分解 top_movers科目数={len(movers)} 方向说明字段={'direction_note' in sc}")

    r = c.get(
        "/api/v1/homeroom/diagnosis/changes/class",
        params={"from_exam": earliest["name"], "to_exam": latest["name"],
                "academic_year_id": AY, "class_id": CLASS},
    )
    assert r.status_code == 200, r.text[:400]
    cc = r.json()
    groups = cc.get("groups") or []
    out.append(
        f"[B3] 班级分解 comparable_n={cc.get('comparable_n')} excluded={cc.get('excluded')} "
        f"组数={len(groups)} 组标签={[g.get('group') or g.get('label') for g in groups][:6]}"
    )

    # 4) 教学域不跨域（需要 teaching scope；快照教师若无教学班则 422/404 也算证伪通过）
    r = c.get(
        "/api/v1/teaching/diagnosis/features",
        params={"person_id": pid, "academic_year_id": AY},
    )
    out.append(f"[隔离] teaching features → {r.status_code}（422/404=教学域无此人/无教学班，即不跨域）")

    print("\n".join(out))
    print("SMOKE_B_OK")


if __name__ == "__main__":
    sys.exit(main())
