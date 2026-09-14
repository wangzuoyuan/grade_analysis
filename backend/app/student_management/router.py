"""学生管理 REST 路由（/api/manage 前缀）。

所有端点的作用域都由服务层强制为教师当前绑定的 grade + class_num（active_grade
驱动），不接收裸 class_num 参数——越权请求没有入口。异常映射：
  ValueError → 422；NotFoundError → 404；ScopeError / ConfirmRequired /
  MergeConflict → 409（携带影响计数或冲突明细）。
"""

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.db.models import get_db
from app.student_management import service

router = APIRouter(prefix="/manage", tags=["student_management"])


def _dispatch(db, fn, *args, **kwargs):
    """统一异常映射的执行器：service 层异常 → HTTP 状态码。"""
    try:
        return fn(db, *args, **kwargs)
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.ScopeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except service.ConfirmRequired as exc:
        raise HTTPException(status_code=409, detail=exc.payload) from exc
    except service.MergeConflict as exc:
        raise HTTPException(status_code=409, detail=exc.payload) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        db.close()


# ─────────────────────────── 列表 / 详情 ───────────────────────────


@router.get("/students")
async def manage_students(
    search: Optional[str] = Query(None),
    include_archived: bool = Query(False),
):
    """学生管理列表（当前作用域，默认不含已归档学生）。"""
    db = next(get_db())
    return _dispatch(db, service.list_students, search=search, include_archived=include_archived)


@router.get("/students/{student_id}")
async def manage_student_detail(student_id: str):
    """单个学生管理详情（主档 + 花名册 + 计数 + 历史学号）。"""
    db = next(get_db())
    return _dispatch(db, service.student_detail, student_id)


# ─────────────────────────── 新建 / 编辑 ───────────────────────────


class CreateStudentPayload(BaseModel):
    name: str
    student_id: Optional[str] = None
    gender: Optional[str] = None
    seat_no: Optional[int] = None
    note: Optional[str] = None


@router.post("/students")
async def manage_create_student(payload: CreateStudentPayload):
    """新建学生（花名册行 + 主档 + alias 一次建齐）。"""
    db = next(get_db())
    return _dispatch(
        db,
        service.create_student,
        name=payload.name,
        student_id=payload.student_id,
        gender=payload.gender,
        seat_no=payload.seat_no,
        note=payload.note,
    )


class UpdateStudentPayload(BaseModel):
    name: Optional[str] = None
    gender: Optional[str] = None
    seat_no: Optional[int] = None
    note: Optional[str] = None
    status: Optional[Literal["active", "transferred", "graduated"]] = None


@router.put("/students/{student_id}")
async def manage_update_student(student_id: str, payload: UpdateStudentPayload):
    """编辑学生（规范姓名/性别/备注写主档，座号/在班状态写花名册）。

    只转发请求里显式出现的字段（model_fields_set）：缺省字段保持原值，
    显式 null 表示清空——二者语义不同，绝不混淆。"""
    db = next(get_db())
    kwargs = {
        field: getattr(payload, field)
        for field in ("name", "gender", "seat_no", "note", "status")
        if field in payload.model_fields_set
    }
    return _dispatch(db, service.update_student, student_id, **kwargs)


# ─────────────────────────── 学号管理 ───────────────────────────


class CorrectSidPayload(BaseModel):
    new_student_id: str


@router.post("/students/{student_id}/correct-sid")
async def manage_correct_sid(student_id: str, payload: CorrectSidPayload):
    """纠正录错学号：单事务迁移全部引用，冲突整体拒绝回滚。"""
    db = next(get_db())
    return _dispatch(db, service.correct_student_id, student_id, payload.new_student_id)


class NewYearSidPayload(BaseModel):
    new_student_id: str
    grade: int
    class_num: int


@router.post("/students/{student_id}/new-year-sid")
async def manage_new_year_sid(student_id: str, payload: NewYearSidPayload):
    """新增学年学号：同一身份新增 alias + 目标学年花名册行，历史保留。"""
    db = next(get_db())
    return _dispatch(
        db,
        service.add_new_year_student_id,
        student_id,
        payload.new_student_id,
        payload.grade,
        payload.class_num,
    )


# ─────────────────────────── 删除 ───────────────────────────


@router.get("/students/{student_id}/delete-preview")
async def manage_delete_preview(student_id: str):
    """删除影响预览：各业务表关联计数。"""
    db = next(get_db())
    return _dispatch(db, service.delete_preview, student_id)


class DeleteStudentPayload(BaseModel):
    confirm: bool = False


@router.delete("/students/{student_id}")
async def manage_delete_student(student_id: str, payload: DeleteStudentPayload):
    """删除学生：有关联数据时需 confirm=true，删除前自动备份。"""
    db = next(get_db())
    return _dispatch(db, service.delete_student, student_id, confirm=payload.confirm)


# ─────────────────────────── 归档 ───────────────────────────


class ArchivePayload(BaseModel):
    status: Literal["transferred", "graduated", "active"]


@router.post("/students/{student_id}/archive")
async def manage_archive_student(student_id: str, payload: ArchivePayload):
    """设置在班状态：transferred / graduated / active（恢复）。"""
    db = next(get_db())
    return _dispatch(db, service.archive_student, student_id, payload.status)


# ─────────────────────────── 重复学生合并 ───────────────────────────


class MergePreviewPayload(BaseModel):
    primary_student_id: str
    duplicate_student_id: str


@router.post("/students/merge-preview")
async def manage_merge_preview(payload: MergePreviewPayload):
    """合并预览：将迁入计数 + 同场考试冲突清单。"""
    db = next(get_db())
    return _dispatch(
        db,
        service.merge_preview,
        payload.primary_student_id,
        payload.duplicate_student_id,
    )


class MergePayload(MergePreviewPayload):
    confirm: bool = False


@router.post("/students/merge")
async def manage_merge(payload: MergePayload):
    """事务性合并重复学生；同场考试冲突拒绝，有数据需 confirm 且自动备份。"""
    db = next(get_db())
    return _dispatch(
        db,
        service.merge_students,
        payload.primary_student_id,
        payload.duplicate_student_id,
        confirm=payload.confirm,
    )


# ─────────────────────────── 身份回填 ───────────────────────────


@router.get("/backfill-preview")
async def manage_backfill_preview():
    """当前班还没有主档（identity）的学生预览。"""
    db = next(get_db())
    return _dispatch(db, service.backfill_preview)


@router.post("/backfill-identities")
async def manage_backfill_identities():
    """为当前班没有主档的学生逐人建 identity（幂等，同名绝不合并）。"""
    db = next(get_db())
    return _dispatch(db, service.backfill_identities)


# ─────────────────────────── 变更日志 ───────────────────────────


@router.get("/change-log")
async def manage_change_log(
    student_id: Optional[str] = Query(None),
    limit: int = Query(50),
):
    """学生信息变更日志（倒序，不含任何凭据信息）。"""
    db = next(get_db())
    return _dispatch(db, service.list_change_log, student_id=student_id, limit=limit)
