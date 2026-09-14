from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
import os

from starlette.responses import JSONResponse as _JSONResponse  # noqa: E402

# 只读 MCP 服务端（Streamable HTTP）。MCP_ENABLED 缺省 false：不挂载、不导入
# mcp SDK、现有应用完全不受影响。启用时 token/目录校验失败会让启动直接失败
# （fail closed：宁可不起，也不裸奔）。
_MCP_ENABLED = os.environ.get("MCP_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")
_MCP_MOUNT = None
if _MCP_ENABLED:
    from app.mcp_server import MCP_MOUNT_PATH, MCPMount, mount_mcp

    _MCP_MOUNT: MCPMount = mount_mcp()


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """宿主 lifespan：MCP 启用时进入其 session manager（挂载的子应用
    lifespan 不会被 Starlette 执行，必须由最外层 ASGI 应用负责）。

    schema 初始化放在 lifespan 开头（import 期零 DDL，R8）：普通启动与
    MCP 启动都先建齐 schema 再对外服务。"""
    from app.db.schema import ensure_app_schema

    ensure_app_schema()
    if _MCP_MOUNT is not None:
        async with _MCP_MOUNT.session_manager().run():
            yield
    else:
        yield


app = FastAPI(title="学情追踪 API", version="2.2.4", lifespan=_lifespan)

if _MCP_MOUNT is not None:
    app.mount(MCP_MOUNT_PATH, _MCP_MOUNT.app)

# 生产同源（经反代）时无需 CORS；本地 dev 前端 3000 → 后端 8000 跨源需放行。
# 额外可用 CORS_ORIGINS（逗号分隔）显式追加来源。
_cors_origins = [
    o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=r"http://[\w.\-]+:3000",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 登录鉴权：内网免登录、外网（命中 PUBLIC_HOST）才要求会话。
from app.auth import COOKIE_NAME, auth_required_for, verify_token  # noqa: E402

_AUTH_ALLOWLIST = {"/api/login", "/api/logout", "/api/auth/status", "/api/health"}


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if (
        request.method != "OPTIONS"
        and path.startswith("/api")
        and path not in _AUTH_ALLOWLIST
        and auth_required_for(request)
        and not verify_token(request.cookies.get(COOKIE_NAME, ""))
    ):
        return _JSONResponse({"detail": "需要登录"}, status_code=401)
    return await call_next(request)

from app.paths import DATA_DIR as EXAM_TRACKER_DIR
os.makedirs(EXAM_TRACKER_DIR, exist_ok=True)
os.makedirs(f"{EXAM_TRACKER_DIR}/raw", exist_ok=True)

@app.get("/api/health")
def health():
    return {"ok": True, "version": "2.2.4"}

@app.get("/")
def root():
    return {"message": "学情追踪 API", "docs": "/docs"}

@app.get("/api/teacher")
def get_teacher():
    """获取班主任信息（延迟初始化）"""
    from app.db.models import (
        SessionLocal,
        Teacher,
        Exam,
        SubjectScore,
        ClassRoster,
        StudentAlias,
    )
    db = SessionLocal()
    try:
        teacher = db.query(Teacher).first()
        if not teacher:
            teacher = Teacher()
            db.add(teacher)
            db.commit()
            db.refresh(teacher)
        # 升级待办只看教师已绑定班级中实际有数据的最高年级。
        # 低年级或其他班级的 alias 不能压制当前班未完成的身份接续。
        has_pending_rollover = False
        bound_classes = {
            2: teacher.target_class_high2,
            3: teacher.target_class_high3,
        }
        for grade in (3, 2):
            class_num = bound_classes[grade]
            if class_num is None:
                continue

            score_ids = {
                row[0]
                for row in (
                    db.query(SubjectScore.student_id)
                    .join(Exam, Exam.id == SubjectScore.exam_id)
                    .filter(Exam.grade == grade, SubjectScore.class_num == class_num)
                    .distinct()
                    .all()
                )
            }
            roster_ids = {
                row[0]
                for row in (
                    db.query(ClassRoster.student_id)
                    .filter(
                        ClassRoster.grade == grade,
                        ClassRoster.class_num == class_num,
                    )
                    .distinct()
                    .all()
                )
            }
            current_student_ids = score_ids | roster_ids
            if not current_student_ids:
                continue

            linked_student_ids = {
                row[0]
                for row in (
                    db.query(StudentAlias.student_id)
                    .filter(
                        StudentAlias.grade == grade,
                        StudentAlias.student_id.in_(current_student_ids),
                    )
                    .all()
                )
            }
            has_pending_rollover = bool(current_student_ids - linked_student_ids)
            break
        # 作业看板当前年级（homework_setting.active_grade；缺省回落库内最大年级）
        from app.rollover.service import get_active_grade  # noqa
        active_grade = get_active_grade(db)
    finally:
        db.close()
    return {
        "id": teacher.id,
        "name": teacher.name,
        "target_class_high1": teacher.target_class_high1,
        "target_class_high2": teacher.target_class_high2,
        "target_class_high3": teacher.target_class_high3,
        "has_pending_rollover": has_pending_rollover,
        "active_grade": active_grade,
    }

@app.patch("/api/teacher")
async def update_teacher(request: Request):
    """更新班主任姓名"""
    from app.db.models import SessionLocal, Teacher
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="invalid json")
    name = body.get("name", "").strip()
    db = SessionLocal()
    teacher = db.query(Teacher).first()
    if not teacher:
        teacher = Teacher()
        db.add(teacher)
    teacher.name = name or None
    db.commit()
    db.close()
    return {"ok": True, "name": name or None}

@app.post("/api/teacher/bind-class")
async def bind_class(request: Request, class_num: Optional[int] = None, grade: int = 1):
    """绑定班级（隐式初始化确认）"""
    from app.db.models import SessionLocal, Teacher

    try:
        body = await request.json()
    except Exception:
        body = {}
    has_body_class = "class_num" in body
    if has_body_class:
        class_num = body["class_num"]
    grade = body.get("grade", grade)

    grade = int(grade)
    if grade not in (1, 2, 3):
        raise HTTPException(status_code=422, detail="grade must be 1, 2, or 3")
    if class_num is None and not has_body_class:
        raise HTTPException(status_code=422, detail="class_num is required")
    if class_num is not None:
        class_num = int(class_num)
        if class_num <= 0:
            raise HTTPException(status_code=422, detail="class_num must be positive")
    db = SessionLocal()
    teacher = db.query(Teacher).first()
    if not teacher:
        teacher = Teacher()
        db.add(teacher)

    if grade == 1:
        teacher.target_class_high1 = class_num
    elif grade == 2:
        teacher.target_class_high2 = class_num
    elif grade == 3:
        teacher.target_class_high3 = class_num

    db.commit()
    db.close()
    return {"ok": True, "bound_class": class_num, "grade": grade}

# 路由模块导入
# 旧班主任版的「导入即迁移」链路（create_all + migrate_semester_table +
# migrate_homeroom）不再随 import app.main 自动执行：合并库的 schema 变更
# 统一走 Alembic（backend/alembic，见 app/db/workspace_models.py）；旧启动
# 迁移仅适用于隔离的源副本演练，不再对合并库重复运行。保留为显式函数
# 便于溯源，不在 import 时调用。


def run_legacy_startup_migrations() -> dict:
    """旧班主任版启动迁移链（行为与历史版本一致），仅供隔离源副本演练。"""
    from app.db.models import Base, engine
    Base.metadata.create_all(bind=engine)
    from app.db.migrate_semester import migrate_semester_table  # noqa
    migrate_semester_table()

    try:
        from app.db.migrate_homeroom import migrate as _migrate_homeroom  # noqa
        return _migrate_homeroom()
    except Exception as _e:  # noqa
        print(f"[migrate_homeroom] skipped: {_e}")
        return {"skipped": str(_e)}

# 跨届撞号迁移（db/migrate_student_ids.py）不再随启动自动执行：
# 写入守门（db/sid_space.ensure_sid_space）已在所有跨届写入入口前置化解
# 撞车，本迁移的检测永不命中；其「唯一同名自动挂链」按姓名合并身份，
# 不适合在无人值守的启动路径静默发生。保留为手工救援工具——仅当出现
# 守门无法覆盖的脏库（如手工导库造成既有撞号）时，由人决策后手动执行：
#   python -m app.db.migrate_student_ids

from app.ingest.router import router as ingest_router  # noqa
from app.analysis.router import router as analysis_router  # noqa
from app.chat.session import router as chat_router  # noqa
from app.homework.router import router as homework_router  # noqa
from app.notes.router import router as notes_router  # noqa
from app.backup.router import router as backup_router  # noqa
from app.rollover.router import router as rollover_router  # noqa
from app.student_management.router import router as student_management_router  # noqa
from app.auth_router import router as auth_router  # noqa
from app.api import create_api_router  # noqa

app.include_router(auth_router, prefix="/api")
app.include_router(ingest_router, prefix="/api")
app.include_router(analysis_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(homework_router, prefix="/api")
app.include_router(notes_router, prefix="/api")
app.include_router(backup_router, prefix="/api")
app.include_router(rollover_router, prefix="/api")
app.include_router(student_management_router, prefix="/api")
# P1 双工作台契约路由（/api/v1/*），契约见 docs/contracts/p1-api.md
app.include_router(create_api_router())
