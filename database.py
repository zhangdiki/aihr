"""数据库层 — SQLite + SQLAlchemy async"""
import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, Integer, String, Text, Float, DateTime, JSON, ForeignKey
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, relationship


# ---- 基础 ----
class Base(DeclarativeBase):
    pass


# ---- 模型 ----

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

    # 种子数据：仅当候选人表为空时插入
    async with async_session() as session:
        from sqlalchemy import select, func
        result = await session.execute(select(func.count()).select_from(Candidate))
        count = result.scalar()
        if count > 0:
            return

        seed_candidates = [
            Candidate(
                name="陈星宇", position="高级前端工程师",
                email="chenxy@example.com", phone="13800001001",
                education="浙江大学 · 计算机科学",
                skills=["React", "TypeScript", "Node.js", "Next.js", "GraphQL"],
                experience=[
                    {"company": "字节跳动", "role": "高级前端工程师", "period": "2021-至今",
                     "desc": "负责抖音电商商家端核心页面重构，使用 React + TypeScript + GraphQL，页面性能提升 40%"},
                    {"company": "阿里巴巴", "role": "前端工程师", "period": "2019-2021",
                     "desc": "参与淘宝商家工具开发，主导微前端架构落地，减少发布耦合 60%"}
                ],
                status="passed", score=4.7,
            ),
            Candidate(
                name="林子涵", position="高级前端工程师",
                email="linzh@example.com", phone="13800001002",
                education="华中科技大学 · 软件工程",
                skills=["Vue", "React", "TypeScript", "Webpack", "CSS"],
                experience=[
                    {"company": "美团", "role": "前端工程师", "period": "2022-至今",
                     "desc": "美团外卖商家端前端开发，Vue3 + TypeScript + Pinia，维护商家菜单管理系统"},
                    {"company": "小米", "role": "初级前端工程师", "period": "2020-2022",
                     "desc": "小米商城活动页开发，参与组件库建设，CSS 还原度要求高"}
                ],
                status="interview", score=4.2,
            ),
            Candidate(
                name="王诗雨", position="高级前端工程师",
                email="wangsy@example.com", phone="13800001003",
                education="武汉理工大学 · 数字媒体",
                skills=["React", "JavaScript", "HTML/CSS", "jQuery"],
                experience=[
                    {"company": "某小型电商公司", "role": "前端开发", "period": "2021-至今",
                     "desc": "负责公司官网和简单后台管理系统开发，使用 React + Ant Design"},
                ],
                status="rejected", score=3.2,
            ),
        ]
        session.add_all(seed_candidates)
        await session.commit()
