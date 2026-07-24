"""数据库层 — SQLite + SQLAlchemy async"""
import os
from datetime import datetime, timezone
from typing import Optional

import hashlib
import secrets

from sqlalchemy import Column, Integer, String, Text, Float, DateTime, JSON, ForeignKey, Boolean
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, relationship


# ---- 基础 ----
class Base(DeclarativeBase):
    pass


# ---- 模型 ----

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(128), nullable=False)
    email = Column(String(100), default="")
    role = Column(String(20), default="user")    # admin / user
    avatar_url = Column(String(200), default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    @staticmethod
    def hash_password(password: str) -> str:
        salt = secrets.token_hex(16)
        h = hashlib.sha256((password + salt).encode()).hexdigest()
        return f"{salt}${h}"

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        salt, h = password_hash.split("$", 1)
        return h == hashlib.sha256((password + salt).encode()).hexdigest()


class Candidate(Base):
    __tablename__ = "candidates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), nullable=False)
    position = Column(String(100), default="")
    email = Column(String(100), default="")
    phone = Column(String(30), default="")
    resume_text = Column(Text, default="")          # 原始简历文本
    skills = Column(JSON, default=list)             # ["React", "TypeScript", ...]
    experience = Column(JSON, default=list)         # [{company, role, period, desc}, ...]
    education = Column(String(200), default="")
    status = Column(String(20), default="new")      # new / screening / interview / passed / rejected
    score = Column(Float, default=0.0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # 关联
    interviews = relationship("Interview", back_populates="candidate", lazy="raise")
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    job = relationship("Job", back_populates="candidates", lazy="joined")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(100), nullable=False)
    department = Column(String(50), default="")
    salary_min = Column(String(20), default="")
    salary_max = Column(String(20), default="")
    urgency = Column(String(20), default="一般")   # 紧急 / 一般 / 不急
    description = Column(Text, default="")
    status = Column(String(20), default="open")     # open / closed
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    candidates = relationship("Candidate", back_populates="job", lazy="raise")


class Interview(Base):
    __tablename__ = "interviews"

    id = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=False)
    transcript = Column(Text, default="")           # 原始转写文本
    notes = Column(JSON, default=None)              # AI 提取的结构化笔记
    questions = Column(JSON, default=None)           # AI 生成的面试题
    audio_filename = Column(String(200), default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # 关联
    candidate = relationship("Candidate", back_populates="interviews", lazy="joined")


class Resume(Base):
    __tablename__ = "resumes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=True)
    filename = Column(String(200), nullable=False)
    raw_text = Column(Text, default="")             # 提取的原始文本
    parsed_data = Column(JSON, default=None)         # AI 解析的结构化数据
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    type = Column(String(30), default="info")       # info / success / warning / error
    title = Column(String(200), default="")
    message = Column(Text, default="")
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(50), unique=True, nullable=False)
    value = Column(JSON, nullable=False)


# ---- 默认配置 ----

DEFAULT_SETTINGS = {
    "dimensions": [
        {"name": "技术能力", "defaultScore": 3},
        {"name": "沟通表达", "defaultScore": 3},
        {"name": "项目经验", "defaultScore": 3},
        {"name": "团队协作", "defaultScore": 3},
        {"name": "学习能力", "defaultScore": 3},
        {"name": "文化契合", "defaultScore": 3},
    ],
    "statuses": [
        {"key": "new", "label": "新简历", "color": "#6366F1", "stageLabel": "待筛选"},
        {"key": "screening", "label": "筛选中", "color": "#FBBF24", "stageLabel": "简历筛选"},
        {"key": "interview", "label": "面试中", "color": "#22D3EE", "stageLabel": "面试中"},
        {"key": "passed", "label": "已通过", "color": "#34D399", "stageLabel": "已通过"},
        {"key": "rejected", "label": "不推荐", "color": "#F87171", "stageLabel": "已淘汰"},
    ],
    "tendency_map": [
        {"tendency": "strong", "label": "强烈推荐", "targetStatus": "passed"},
        {"tendency": "recommend", "label": "推荐", "targetStatus": "passed"},
        {"tendency": "hold", "label": "保留", "targetStatus": "interview"},
        {"tendency": "reject", "label": "不推荐", "targetStatus": "rejected"},
    ],
    "thresholds": {"pass": 4.5, "reject": 3.0, "evaluationPass": 4.0},
    "departments": ["技术部", "产品部", "内容部", "运营部", "市场部"],
}


# ---- 引擎 & Session ----

_db_path = os.getenv("DATABASE_PATH", "data.db")
DATABASE_URL = f"sqlite+aiosqlite:///{_db_path}"
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """创建所有表并插入种子数据"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 初始管理员账户（仅当用户表为空时创建）
    async with async_session() as session:
        from sqlalchemy import select, func
        result = await session.execute(select(func.count()).select_from(User))
        if result.scalar() > 0:
            return

        session.add(User(
            username="admin",
            password_hash=User.hash_password("admin123"),
            email="admin@aihr.local",
            role="admin",
        ))
        await session.commit()

    # 写入默认设置（仅当设置表为空时）
    async with async_session() as session:
        from sqlalchemy import select, func
        result = await session.execute(select(func.count()).select_from(Setting))
        if result.scalar() == 0:
            for k, v in DEFAULT_SETTINGS.items():
                session.add(Setting(key=k, value=v))
            await session.commit()
