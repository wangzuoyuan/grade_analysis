# 后端：FastAPI + SQLAlchemy（构建上下文 = backend/）
FROM python:3.11-slim

WORKDIR /srv

# 国内网络：pip 走清华镜像。用 ENV 设 PIP_INDEX_URL，构建隔离的子进程
# （装 setuptools 等 build 依赖）也会继承,避免连 pypi.org 超时。
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 先装依赖（利用层缓存），再拷代码。
# 依赖以 requirements-lock.txt 全量 pin 为准（含全部传递依赖，构建可复现，
# Q07"含锁定依赖"）；本包本体随后 --no-deps 安装，元数据来自 pyproject。
COPY requirements-lock.txt ./
RUN pip install --no-cache-dir -r requirements-lock.txt

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir --no-deps .

# 迁移资源（Q07）：空库初始化（create_all + stamp head）与后续版本演进
# 都要读 alembic.ini + alembic/；已 head 的库启动不读它们（schema.py 短路），
# 但镜像必须携带，否则全新数据目录无法完成初始化。
# 放在依赖安装层之后：迁移脚本变更不触发依赖重装，保持层缓存结构。
COPY alembic.ini ./
COPY alembic ./alembic

# 数据落到挂载卷；备份目录也在卷内，随 NAS 快照一起保护
ENV EXAM_TRACKER_DIR=/data \
    EXAM_TRACKER_BACKUP_DIR=/data/backups \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
