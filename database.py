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
                education="浙江大学 · 计算机科学 · 本科",
                skills=["React", "TypeScript", "Node.js", "Next.js", "GraphQL", "Webpack", "Jest", "Docker"],
                experience=[
                    {"company": "字节跳动", "role": "高级前端工程师", "period": "2021.03 - 至今",
                     "desc": "负责抖音电商商家端核心页面重构，React + TypeScript + GraphQL 技术栈，LCP 从 3.2s 优化至 1.4s；主导微前端架构落地，支撑 8 个业务团队并行开发；搭建组件库 200+ 组件，团队复用率 85%"},
                    {"company": "阿里巴巴", "role": "前端工程师", "period": "2019.07 - 2021.02",
                     "desc": "参与淘宝商家工具开发，负责营销活动配置平台前端；自研低代码表单引擎，配置效率提升 3 倍；带 2 名新人完成 onboarding"}
                ],
                status="passed", score=4.7,
            ),
            Candidate(
                name="林子涵", position="高级前端工程师",
                email="linzh@example.com", phone="13800001002",
                education="华中科技大学 · 软件工程 · 硕士",
                skills=["Vue", "React", "TypeScript", "Webpack", "CSS", "Pinia", "Sass", "ECharts"],
                experience=[
                    {"company": "美团", "role": "前端工程师", "period": "2022.01 - 至今",
                     "desc": "美团外卖商家端前端开发，Vue3 + TypeScript + Pinia 技术栈；负责商家菜单管理系统重构，日活 50 万商家使用；优化首屏加载，FCP 降低 40%"},
                    {"company": "小米", "role": "初级前端工程师", "period": "2020.06 - 2021.12",
                     "desc": "小米商城活动页开发，参与内部组件库建设；CSS 还原度要求高，像素级还原设计稿；独立完成 15+ 个营销活动页面上线"}
                ],
                status="interview", score=4.2,
            ),
            Candidate(
                name="王诗雨", position="前端开发工程师",
                email="wangsy@example.com", phone="13800001003",
                education="武汉理工大学 · 数字媒体技术 · 本科",
                skills=["React", "JavaScript", "HTML/CSS", "jQuery", "Bootstrap"],
                experience=[
                    {"company": "某电商科技公司", "role": "前端开发", "period": "2022.06 - 至今",
                     "desc": "负责公司官网和商家后台管理系统开发，React + Ant Design 技术栈；参与小程序开发，uni-app 跨端适配"},
                    {"company": "外包科技公司", "role": "前端开发", "period": "2020.09 - 2022.05",
                     "desc": "参与多个外包项目，涉及企业官网、活动页面等；独立完成 5 个项目的 PC 端和移动端适配"}
                ],
                status="rejected", score=3.2,
            ),
            Candidate(
                name="张伟豪", position="高级后端工程师",
                email="zhangwh@example.com", phone="13800001004",
                education="北京大学 · 软件工程 · 硕士",
                skills=["Go", "Java", "Kubernetes", "MySQL", "Redis", "Kafka", "Docker", "Python"],
                experience=[
                    {"company": "腾讯", "role": "高级后端工程师", "period": "2020.04 - 至今",
                     "desc": "负责微信支付核心交易链路，日均处理 10 亿+ 交易；主导服务网格迁移，P99 延迟降低 35%；带 5 人团队"},
                    {"company": "京东", "role": "后端工程师", "period": "2017.07 - 2020.03",
                     "desc": "京东物流核心系统开发，负责订单调度和仓储管理模块；技术栈 Go + Redis + MySQL"}
                ],
                status="screening", score=4.5,
            ),
            Candidate(
                name="赵晓琳", position="产品经理",
                email="zhaoxl@example.com", phone="13800001005",
                education="复旦大学 · 信息管理与信息系统 · 本科",
                skills=["产品设计", "数据分析", "SQL", "Figma", "Axure", "用户研究", "A/B测试"],
                experience=[
                    {"company": "小红书", "role": "高级产品经理", "period": "2021.08 - 至今",
                     "desc": "负责社区内容分发策略，DAU 增长 40%，用户留存率提升 15%；主导推荐算法迭代，与算法团队协作优化"},
                    {"company": "百度", "role": "产品经理", "period": "2019.06 - 2021.07",
                     "desc": "百度 App 信息流产品设计，负责内容推荐和用户增长方向"}
                ],
                status="screening", score=4.3,
            ),
            Candidate(
                name="刘志远", position="DevOps 工程师",
                email="liuzy@example.com", phone="13800001006",
                education="上海交通大学 · 计算机科学 · 本科",
                skills=["Kubernetes", "Docker", "Terraform", "AWS", "CI/CD", "Prometheus", "Grafana", "Python"],
                experience=[
                    {"company": "B站", "role": "DevOps 工程师", "period": "2020.03 - 至今",
                     "desc": "管理 3000+ 节点的 K8s 集群；搭建 GitOps 流水线，部署效率提升 70%；自研成本优化系统，年度节省 600 万云成本"},
                    {"company": "携程", "role": "运维工程师", "period": "2017.07 - 2020.02",
                     "desc": "负责在线业务的监控告警体系建设和故障响应"}
                ],
                status="interview", score=4.6,
            ),
        ]
        session.add_all(seed_candidates)
        await session.flush()

        # 种子面试记录
        seed_interviews = [
            Interview(
                candidate_id=1,
                transcript="面试官：请做个自我介绍。\n陈星宇：你好，我是陈星宇，浙江大学计算机专业毕业，5年前端开发经验。目前在字节跳动负责抖音电商商家端开发，主要技术栈是 React 和 TypeScript。之前在阿里巴巴参与过淘宝商家工具的开发。\n\n面试官：说说你在字节跳动最有挑战的项目。\n陈星宇：今年主导了商家端的性能优化项目。通过 Code Splitting、图片懒加载、Service Worker 缓存策略，把 LCP 从 3.2 秒优化到了 1.4 秒。另外还推动了微前端架构落地，用 qiankun 把巨石应用拆成了 8 个独立子应用，现在各业务团队可以独立开发、独立部署。\n\n面试官：微前端遇到过什么问题吗？\n陈星宇：最大的挑战是子应用之间的通信和样式隔离。我们用 postMessage + 发布订阅模式做了全局事件总线，样式隔离用了 Shadow DOM + CSS Module。还有个坑是子应用的 chunk 加载失败，我们加了重试机制和灰度回滚能力。\n\n面试官：你怎么看待前端 AI 化的趋势？\n陈星宇：Vercel 的 v0、GitHub Copilot 这些工具确实在改变我们的工作方式。我觉得好的工程师不会被替代，反而可以利用 AI 提升效率。比如我们现在用 Copilot 写样板代码，用 AI 做 Code Review 的初筛。但架构设计、性能优化这些还是需要人的判断力。\n\n面试官：你有什么想问我的？\n陈星宇：想了解一下贵公司技术团队目前的规模和前端技术栈的情况，以及这个岗位未来半年主要负责什么方向。",
                notes={
                    "overall_impression": "技术深度优秀，具备架构思维和工程化能力。字节跳动的微前端落地经验非常有说服力，从性能优化到架构设计都有实际产出。表达清晰有逻辑，对技术趋势有独立思考。唯一可提升的是后端知识面。强烈推荐进入下一轮。",
                    "section_notes": [
                        {"dimension": "技术能力", "score": 5, "note": "React 底层原理扎实，性能优化有量化结果，微前端架构有实操经验，工程化能力突出"},
                        {"dimension": "沟通表达", "score": 4.5, "note": "表达简洁有条理，能清晰描述技术方案和遇到的挑战，不回避踩过的坑"},
                        {"dimension": "项目经验", "score": 5, "note": "字节跳动 + 阿里巴巴双大厂背景，项目复杂度高，从 0 到 1 推动过重大技术变革"},
                        {"dimension": "团队协作", "score": 4.5, "note": "推动过跨团队架构落地，带过新人，有跨部门协调经验"},
                        {"dimension": "学习能力", "score": 4.5, "note": "对前端 AI 化趋势有自己的思考，保持技术敏感度"},
                        {"dimension": "文化契合", "score": 4.5, "note": "注重技术质量，有自驱力，价值观匹配"}
                    ],
                    "key_quotes": [
                        "LCP 从 3.2 秒优化到了 1.4 秒",
                        "用 qiankun 把巨石应用拆成了 8 个独立子应用",
                        "好的工程师不会被替代，反而可以利用 AI 提升效率",
                        "架构设计、性能优化这些还是需要人的判断力"
                    ],
                    "tendency": "强烈推荐",
                    "tags": ["技术深度好", "架构思维", "大厂背景", "沟通清晰"]
                },
                audio_filename="interview-chenxy-20260703.webm"
            ),
            Interview(
                candidate_id=2,
                transcript="面试官：请自我介绍一下。\n林子涵：您好，我是林子涵，华中科技大学软件工程硕士，4年前端经验。目前在美团负责外卖商家端的前端开发，主要用 Vue3 和 TypeScript。之前在小米做过活动页开发。\n\n面试官：说说你在美团做的最有挑战的事。\n林子涵：商家菜单管理系统重构，之前是 jQuery 老项目，我们迁移到了 Vue3 + TypeScript + Pinia。在迁移过程中做了首屏优化，通过懒加载和代码分割把 FCP 降低了 40% 左右。\n\n面试官：如果让你重新设计这个系统，你会怎么做？\n林子涵：可能会考虑用微前端的方式，目前系统越来越庞大，不同业务模块耦合比较紧。另外会引入更好的状态管理方案。\n\n面试官：你用过 React 吗？感觉和 Vue 有什么区别？\n林子涵：用过，之前在小米主要用 React。Vue 上手更简单，模板语法对设计出身的同学友好。React 的 JSX 更灵活，Hooks 的表达力很强。各有优势吧，我现在两个都会。\n\n面试官：你有什么职业规划？\n林子涵：短期想深入前端工程化和性能优化方向，长期希望往技术管理方向发展，带团队。",
                notes={
                    "overall_impression": "基本功扎实，Vue 生态熟练度高，CSS 能力出众。项目经验有亮点但深度不够，架构层面的思考偏表面。沟通态度诚恳，自驱力尚可但缺少技术影响力。建议补充一轮技术深度面试，重点考察复杂系统设计能力。",
                    "section_notes": [
                        {"dimension": "技术能力", "score": 4, "note": "Vue3/TS 熟练，CSS 底层理解好（BFC/层叠上下文），但架构设计经验偏弱，性能优化偏常规手段"},
                        {"dimension": "沟通表达", "score": 4.5, "note": "态度诚恳，回答真实不油腻，但主动引导讨论的能力偏弱"},
                        {"dimension": "项目经验", "score": 3.5, "note": "项目复杂度中等，缺乏从 0 到 1 或重大架构变革的经验"},
                        {"dimension": "团队协作", "score": 4, "note": "团队配合意识好，但缺少跨团队协作和推动力的例子"},
                        {"dimension": "学习能力", "score": 3.5, "note": "技术关注面偏窄，对前端前沿趋势的了解不够主动"},
                        {"dimension": "文化契合", "score": 4, "note": "执行力好，态度踏实，但技术热情和主动性可以更好"}
                    ],
                    "key_quotes": [
                        "把 FCP 降低了 40% 左右",
                        "可能会考虑用微前端的方式",
                        "Vue 上手更简单，React 更灵活"
                    ],
                    "tendency": "保留",
                    "tags": ["Vue 熟练", "CSS 扎实", "架构偏弱", "态度好"]
                },
                audio_filename="interview-linzh-20260702.webm"
            ),
            Interview(
                candidate_id=6,
                transcript="面试官：自我介绍一下吧。\n刘志远：你好，我是刘志远，上海交大计算机本科，B站 DevOps 工程师。目前管理 3000+ 节点的 K8s 集群，负责 CI/CD 平台和成本优化。之前在携程做运维。\n\n面试官：说说成本优化是怎么做的。\n刘志远：我们自己做了一套资源调度系统，通过 HPA+VPA 动态调整 Pod 资源，加上 Spot 实例和预留实例的混合策略，年度省了 600 万。另外把一些非核心服务从实时迁移到异步，减少了峰值资源需求。\n\n面试官：遇到最大的故障是什么？\n刘志远：去年双十一前有一次 K8s 集群升级，新版本的 CNI 插件有 bug，导致部分 Pod 网络不通。我从凌晨 2 点排查到 4 点，最后降级到旧版本才恢复。事后我们完善了灰度发布和回滚 SOP。",
                notes={
                    "overall_impression": "DevOps 领域经验丰富，K8s 集群规模 3000+ 节点体现了技术实力。成本优化量化结果清晰（600 万），故障处理有复盘意识。基础扎实，技术视野广，推荐。",
                    "section_notes": [
                        {"dimension": "技术能力", "score": 5, "note": "K8s 管理经验丰富，自研资源调度系统，CI/CD 流程搭建，多云管理"},
                        {"dimension": "沟通表达", "score": 4, "note": "表达直接有重点，能说清楚技术方案的 trade-off"},
                        {"dimension": "项目经验", "score": 4.5, "note": "B站 3000+ 节点实操，成本优化 600 万量化结果"},
                        {"dimension": "团队协作", "score": 4, "note": "有跨团队协作推动项目的经验"},
                        {"dimension": "学习能力", "score": 4.5, "note": "跟踪云原生技术发展趋势，有持续学习的习惯"},
                        {"dimension": "文化契合", "score": 4.5, "note": "注重自动化、有 engineering mindset"}
                    ],
                    "key_quotes": [
                        "3000+ 节点的 K8s 集群",
                        "年度节省了 600 万云成本",
                        "完善了灰度发布和回滚 SOP"
                    ],
                    "tendency": "推荐",
                    "tags": ["K8s 专家", "成本优化", "工程化思维", "故障处理"]
                },
                audio_filename="interview-liuzy-20260701.webm"
            ),
        ]
        session.add_all(seed_interviews)
        await session.commit()
