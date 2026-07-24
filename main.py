"""AIHR — AI 智能招聘助手 后端服务"""
import os
import json
import secrets
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Depends, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from dotenv import load_dotenv
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database import init_db, get_db, Candidate, Interview, Resume, User, Notification, Job, Setting, DEFAULT_SETTINGS
from services.baidu_asr import BaiduASR, ASRError
from services.ai_service import AIService
from services.resume_parser import extract_text, parse_resume_with_ai

load_dotenv()

# ---- 配置 ----
BAIDU_APP_ID = os.getenv("BAIDU_APP_ID", "")
BAIDU_API_KEY = os.getenv("BAIDU_API_KEY", "")
BAIDU_SECRET_KEY = os.getenv("BAIDU_SECRET_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
JWT_SECRET = os.getenv("JWT_SECRET", secrets.token_hex(32))

# ---- 服务实例 ----
baidu_asr = BaiduASR(BAIDU_API_KEY, BAIDU_SECRET_KEY, BAIDU_APP_ID) if BAIDU_API_KEY else None
ai_service = AIService(DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL)

# ---- Token 管理 ----
_token_store = {}  # token -> user_id, 简单内存存储

def _create_token(user_id: int) -> str:
    token = secrets.token_hex(32)
    _token_store[token] = {"user_id": user_id, "created_at": datetime.now(timezone.utc)}
    return token

async def _get_current_user(authorization: str = Header(None), db = Depends(get_db)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    token = authorization.split(" ", 1)[1]
    entry = _token_store.get(token)
    if not entry:
        raise HTTPException(401, "登录已过期")
    result = await db.execute(select(User).where(User.id == entry["user_id"]))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(401, "用户不存在")
    return user


# ---- FastAPI ----
app = FastAPI(title="AIHR API", version="1.0.0")


@app.on_event("startup")
async def startup():
    await init_db()
    print("[AIHR] 数据库初始化完成")


# ---- 序列化辅助 ----

def candidate_to_dict(c: Candidate, include_interviews: bool = False) -> dict:
    """Candidate ORM → 前端 JSON"""
    exp_years = ""
    if c.experience:
        try:
            import re
            first = c.experience[0] if isinstance(c.experience, list) else {}
            period = first.get("period", "")
            match = re.search(r'(\d+)', period)
            if match:
                exp_years = f"{match.group(1)}年"
        except Exception:
            pass

    status_map = {
        "new": "新简历", "screening": "筛选中", "interview": "面试中",
        "passed": "已通过", "rejected": "不推荐"
    }
    stage_map = {
        "new": "待筛选", "screening": "简历筛选", "interview": "面试中",
        "passed": "终面", "rejected": "一面"
    }

    result = {
        "id": c.id,
        "name": c.name,
        "position": c.position,
        "score": c.score or 0.0,
        "status": status_map.get(c.status, c.status),
        "stage": stage_map.get(c.status, c.status),
        "experience": exp_years,
        "education": c.education or "",
        "skills": c.skills or [],
        "work": c.experience or [],
        "evaluation": "",
        "traces": [],
    }

    if include_interviews and c.interviews:
        last = c.interviews[-1]
        if last.notes:
            result["evaluation"] = last.notes.get("overall_impression", "")
            result["traces"] = last.notes.get("section_notes", [])

    return result


# ============================================================
# 静态文件
# ============================================================
static_dir = Path(__file__).parent / "static"


@app.get("/")
async def index():
    return FileResponse(static_dir / "index.html")


if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ============================================================
# 健康检查
# ============================================================
@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
        "baidu_asr_configured": bool(BAIDU_API_KEY and BAIDU_SECRET_KEY),
        "deepseek_configured": bool(DEEPSEEK_API_KEY),
    }


# ============================================================
# 认证
# ============================================================

@app.post("/api/auth/register")
async def register(data: dict, db: AsyncSession = Depends(get_db)):
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    if len(username) < 2 or len(password) < 4:
        raise HTTPException(400, "用户名至少2位，密码至少4位")
    existing = await db.execute(select(User).where(User.username == username))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "用户名已存在")
    user = User(username=username, password_hash=User.hash_password(password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    token = _create_token(user.id)
    return {"token": token, "user": {"id": user.id, "username": user.username, "role": user.role}}


@app.post("/api/auth/login")
async def login(data: dict, db: AsyncSession = Depends(get_db)):
    username = data.get("username", "").strip()
    password = data.get("password", "")
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user or not User.verify_password(password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    token = _create_token(user.id)
    return {"token": token, "user": {"id": user.id, "username": user.username, "role": user.role}}


@app.get("/api/auth/me")
async def me(user = Depends(_get_current_user)):
    return {"id": user.id, "username": user.username, "email": user.email, "role": user.role}


# ============================================================
# 通知
# ============================================================

@app.get("/api/notifications")
async def list_notifications(user = Depends(_get_current_user), db = Depends(get_db)):
    result = await db.execute(
        select(Notification).where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc()).limit(20)
    )
    items = result.scalars().all()
    return [{
        "id": n.id, "type": n.type, "title": n.title, "message": n.message,
        "is_read": n.is_read, "created_at": n.created_at.isoformat() if n.created_at else None
    } for n in items]


@app.post("/api/notifications/{nid}/read")
async def read_notification(nid: int, user = Depends(_get_current_user), db = Depends(get_db)):
    result = await db.execute(select(Notification).where(Notification.id == nid, Notification.user_id == user.id))
    n = result.scalar_one_or_none()
    if n:
        n.is_read = True
        await db.commit()
    return {"ok": True}


@app.get("/api/notifications/unread-count")
async def unread_count(user = Depends(_get_current_user), db = Depends(get_db)):
    from sqlalchemy import func as sqlfunc
    result = await db.execute(
        select(sqlfunc.count()).select_from(Notification)
        .where(Notification.user_id == user.id, Notification.is_read == False)
    )
    return {"count": result.scalar()}


# ============================================================
# 帮助中心
# ============================================================

HELP_ARTICLES = [
    {"id": 1, "title": "快速开始", "icon": "rocket",
     "content": "AIHR 是一个 AI 驱动的智能招聘助手，帮助 HR 完成从简历筛选到面试评估的全流程工作。\n\n核心功能：简历解析、语音转写、AI 面试笔记提取、面试题生成、岗位管理。\n\n三步上手：\n1. 创建岗位 - 填写岗位名称，点击 AI 生成 JD 自动生成职位描述\n2. 上传简历 - 选择对应岗位，拖拽上传 PDF/Word\n3. 面试评估 - 选择候选人，录音或粘贴文本，AI 自动提取面试笔记\n\n技术栈：Python FastAPI + SQLAlchemy + SQLite\nAI 引擎：DeepSeek + 百度 ASR\n前端：单页面 HTML，零框架依赖"},
    {"id": 2, "title": "简历上传与解析", "icon": "file",
     "content": "支持格式：PDF（推荐）、Word (.docx/.doc)、TXT\n\n上传方式：\n1. 进入简历管理页面，展开上传区域\n2. 选择对应岗位（建议）\n3. 拖拽文件或点击选择\n4. 等待进度条完成，系统自动解析并创建候选人\n\nAI 自动提取：姓名、联系方式、学历、技能标签、工作经历\n\n注意：文件建议 5MB 以内，解析需 3-10 秒，可手动编辑解析结果"},
    {"id": 3, "title": "面试语音转写", "icon": "mic",
     "content": "录音转写：\n1. 进入面试管理页面，选择候选人\n2. 点击麦克风按钮开始录音\n3. 点击停止 - 音频自动上传并转写\n4. 转写结果可编辑\n\n粘贴转写（备选）：\n若录音不可用（需 HTTPS），可粘贴已有文本\n\n长音频支持：\n- 55秒以内直接识别\n- 超过55秒自动切片后逐片识别拼接\n- 最多支持约30分钟\n\n格式：WebM/WAV/MP3，需麦克风权限"},
    {"id": 4, "title": "AI 面试笔记", "icon": "brain",
     "content": "基于转写文本，DeepSeek AI 从 6 个维度评估：\n技术能力、沟通表达、项目经验、团队协作、学习能力、文化契合\n\n操作：\n1. 完成转写\n2. 点击 AI 提取面试重点（5-15秒）\n3. 系统填充 6 维度评价和打分\n4. 手动调整后点击保存\n\n保存后自动：计算平均分、更新候选人状态"},
    {"id": 5, "title": "AI 面试题生成", "icon": "list",
     "content": "根据候选人简历自动生成个性化面试题。\n\n两个入口：\n- 候选人详情面板点击生成面试题\n- 面试管理页面选择候选人后点击生成\n\n5 个板块：自我介绍与动机、技术深度、项目经验深挖、软技能与团队协作、文化契合\n\n每题含 3 档答题标准：优秀/中等/较差\n\n生成需 5-15 秒，按钮显示加载状态"},
    {"id": 6, "title": "候选人管理", "icon": "users",
     "content": "5 个状态：新简历、筛选中、面试中、已通过、不推荐\n\n操作：\n- 点击行查看详情（技能、工作经历、面试记录）\n- 标记状态（通过/淘汰/面试中）\n- 使用岗位和评分筛选\n\nAI 评估报告：综合评分、六维雷达图、技能标签、关键语录"},
    {"id": 7, "title": "岗位管理", "icon": "rocket",
     "content": "创建岗位：\n1. 进入岗位管理页面，点击新建\n2. 填写名称、部门、薪资、紧急程度\n3. 点击 AI 生成 JD（可手动修改）\n4. 点击创建\n\nPipeline 看板展示各岗位招聘进度，5 个阶段候选人数量一目了然"},
    {"id": 8, "title": "常见问题 FAQ", "icon": "users",
     "content": "Q: 录音不能用？\n需 HTTPS 或 localhost 环境。用 http://localhost:8000 访问，或用手机录音+粘贴。\n\nQ: AI 分析慢？\nDeepSeek API 需 5-15 秒，取决于文本长度。耐心等待。\n\nQ: 简历解析不准？\nPDF 效果最好，扫描版无法解析。可手动编辑。\n\nQ: 数据在哪？\n存储在 data.db SQLite 文件，建议定期备份。\n\nQ: 如何部署？\n有 Dockerfile，可部署到 Zeabur/Railway 或任何云服务器。"},
]


@app.get("/api/help")
async def get_help_articles():
    return HELP_ARTICLES


@app.get("/api/help/{article_id}")
async def get_help_article(article_id: int):
    for a in HELP_ARTICLES:
        if a["id"] == article_id:
            return a
    raise HTTPException(404, "文章不存在")


# ============================================================
# 系统设置
# ============================================================

@app.get("/api/settings")
async def get_settings(db: AsyncSession = Depends(get_db)):
    """获取所有系统设置"""
    result = await db.execute(select(Setting))
    rows = result.scalars().all()
    settings = {}
    for row in rows:
        settings[row.key] = row.value
    # 补全缺失的默认值
    for k, v in DEFAULT_SETTINGS.items():
        if k not in settings:
            settings[k] = v
    return settings


@app.put("/api/settings")
async def update_settings(data: dict, db: AsyncSession = Depends(get_db)):
    """批量更新系统设置"""
    for key, value in data.items():
        if key not in DEFAULT_SETTINGS:
            continue  # 忽略未知的 key
        result = await db.execute(select(Setting).where(Setting.key == key))
        row = result.scalar_one_or_none()
        if row:
            row.value = value
        else:
            db.add(Setting(key=key, value=value))
    await db.commit()
    return {"ok": True}


# ============================================================
# 候选人 CRUD
# ============================================================

@app.get("/api/candidates")
async def list_candidates(db: AsyncSession = Depends(get_db)):
    """候选人列表"""
    result = await db.execute(
        select(Candidate).order_by(Candidate.created_at.desc())
    )
    candidates = result.scalars().all()
    return [candidate_to_dict(c) for c in candidates]


@app.get("/api/candidates/{candidate_id}")
async def get_candidate(candidate_id: int, db: AsyncSession = Depends(get_db)):
    """候选人详情（含面试记录）"""
    result = await db.execute(
        select(Candidate).where(Candidate.id == candidate_id)
    )
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(404, "候选人不存在")

    # eager load interviews
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(Candidate).options(selectinload(Candidate.interviews))
        .where(Candidate.id == candidate_id)
    )
    c = result.scalar_one()

    return candidate_to_dict(c, include_interviews=True)


@app.post("/api/candidates")
async def create_candidate(data: dict, db: AsyncSession = Depends(get_db)):
    """手动添加候选人"""
    c = Candidate(
        name=data.get("name", ""),
        position=data.get("position", ""),
        email=data.get("email", ""),
        phone=data.get("phone", ""),
        education=data.get("education", ""),
        skills=data.get("skills", []),
        experience=data.get("experience", []),
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return candidate_to_dict(c)


# ============================================================
# 岗位 CRUD
# ============================================================

@app.get("/api/jobs")
async def list_jobs(db: AsyncSession = Depends(get_db)):
    """岗位列表"""
    result = await db.execute(select(Job).order_by(Job.created_at.desc()))
    jobs = result.scalars().all()

    # 统计每个岗位的候选人数量（按状态分组）
    data = []
    for j in jobs:
        count_result = await db.execute(
            select(func.count()).select_from(Candidate).where(Candidate.job_id == j.id)
        )
        total = count_result.scalar()
        # 按状态统计
        stage_counts = {}
        for status in ("new", "screening", "interview", "passed", "rejected"):
            r = await db.execute(
                select(func.count()).select_from(Candidate)
                .where(Candidate.job_id == j.id, Candidate.status == status)
            )
            stage_counts[status] = r.scalar()

        data.append({
            "id": j.id,
            "title": j.title,
            "department": j.department,
            "salary_min": j.salary_min,
            "salary_max": j.salary_max,
            "urgency": j.urgency,
            "description": j.description,
            "status": j.status,
            "total_candidates": total,
            "stage_counts": stage_counts,
            "created_at": j.created_at.isoformat() if j.created_at else None,
        })

    return data


@app.post("/api/jobs")
async def create_job(data: dict, db: AsyncSession = Depends(get_db)):
    """创建岗位"""
    job = Job(
        title=data.get("title", ""),
        department=data.get("department", ""),
        salary_min=str(data.get("salary_min", "")),
        salary_max=str(data.get("salary_max", "")),
        urgency=data.get("urgency", "一般"),
        description=data.get("description", ""),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return {
        "id": job.id,
        "title": job.title,
        "department": job.department,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "urgency": job.urgency,
        "status": job.status,
    }


@app.patch("/api/jobs/{job_id}")
async def update_job(job_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    """更新岗位"""
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "岗位不存在")
    for field in ("title", "department", "salary_min", "salary_max", "urgency", "description", "status"):
        if field in data:
            setattr(job, field, data[field])
    await db.commit()
    return {"ok": True}


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: int, db: AsyncSession = Depends(get_db)):
    """删除岗位"""
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "岗位不存在")
    await db.delete(job)
    await db.commit()
    return {"ok": True}


@app.post("/api/jobs/generate-jd")
async def generate_jd(data: dict):
    """AI 生成岗位描述"""
    title = data.get("title", "").strip()
    if not title:
        raise HTTPException(400, "岗位名称不能为空")
    jd = await ai_service.generate_jd(
        title=title,
        department=data.get("department", ""),
        salary_min=data.get("salary_min", ""),
        salary_max=data.get("salary_max", ""),
    )
    return {"jd": jd}


# ============================================================
# 语音转写
# ============================================================

@app.post("/api/interviews/transcribe")
async def transcribe_interview(
    file: UploadFile = File(...),
    candidate_id: int = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """上传录音 → 百度 ASR（自动处理短/长音频） → 保存转写 → 返回文本"""
    if baidu_asr is None:
        raise HTTPException(503, "百度 ASR 未配置")

    if not file.filename:
        raise HTTPException(400, "无效的文件")

    suffix = Path(file.filename).suffix or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    file_size_kb = round(len(content) / 1024, 1)
    duration = baidu_asr._get_duration(tmp_path)
    print(f"[ASR] 收到录音: {file.filename}, {file_size_kb}KB, {duration:.1f}s")

    # 保存原始录音用于调试
    import shutil
    shutil.copy2(tmp_path, Path(__file__).parent / "static" / "_debug_raw" + suffix)
    print(f"[ASR] 原始录音已保存: _debug_raw{suffix}")

    try:
        text = await baidu_asr.transcribe(tmp_path)

        # 保存到数据库
        interview = Interview(
            candidate_id=candidate_id or 0,
            transcript=text,
            audio_filename=file.filename,
        )
        db.add(interview)
        await db.commit()
        await db.refresh(interview)

        return {
            "id": interview.id,
            "text": text,
            "duration_s": round(duration, 1),
            "file_size_kb": file_size_kb,
            "chunked": duration > 55,
        }
    except ASRError as e:
        print(f"[ASR] 业务错误: {e}")
        raise HTTPException(500, f"语音识别失败 ({e.err_no}): {e.err_msg}")
    except Exception as e:
        print(f"[ASR] 转写失败: {e}")
        raise HTTPException(500, f"语音转写失败: {str(e)}")
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# ============================================================
# AI 面试笔记
# ============================================================

@app.post("/api/interviews/extract-notes")
async def extract_interview_notes(
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    """从转写文本提取结构化面试笔记，保存到数据库"""
    if not DEEPSEEK_API_KEY:
        raise HTTPException(503, "DeepSeek API 未配置")

    transcript = data.get("transcript", "").strip()
    candidate_name = data.get("candidate_name", "")
    position = data.get("position", "")
    interview_id = data.get("interview_id")

    if not transcript:
        raise HTTPException(400, "转写文本不能为空")
    if len(transcript) < 20:
        raise HTTPException(400, "转写文本过短（至少20字）")

    try:
        # 从设置中读取评分维度
        dims = None
        result = await db.execute(select(Setting).where(Setting.key == "dimensions"))
        dim_row = result.scalar_one_or_none()
        if dim_row and dim_row.value:
            dims = [d["name"] for d in dim_row.value]

        notes = await ai_service.extract_notes(transcript, candidate_name, position, dimensions=dims)

        # 保存到数据库
        if interview_id:
            result = await db.execute(
                select(Interview).where(Interview.id == interview_id)
            )
            interview = result.scalar_one_or_none()
            if interview:
                interview.notes = notes
                await db.commit()

        return notes
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"AI 提取失败: {str(e)}")


# ============================================================
# 简历上传 & AI 解析
# ============================================================

@app.post("/api/resumes/upload")
async def upload_resume(
    file: UploadFile = File(...),
    job_id: int = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """上传简历 → 提取文本 → AI 结构化 → 创建候选人 → 返回结构化数据"""
    if not file.filename:
        raise HTTPException(400, "无效的文件")

    print(f"[Resume] 收到简历: {file.filename}")

    # 保存临时文件
    suffix = Path(file.filename).suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # 1. 提取文本
        raw_text = extract_text(tmp_path, file.filename)
        print(f"[Resume] 提取文本: {len(raw_text)} 字")

        # 2. AI 结构化（如果有 AI 服务）
        parsed = {}
        if DEEPSEEK_API_KEY:
            parsed = await parse_resume_with_ai(raw_text, ai_service)
        else:
            parsed = {"name": "", "skills": [], "experience": [], "summary": ""}

        # 3. 保存简历记录
        resume_record = Resume(
            filename=file.filename,
            raw_text=raw_text,
            parsed_data=parsed,
        )
        db.add(resume_record)
        await db.commit()
        await db.refresh(resume_record)

        # 4. 自动创建候选人
        candidate = Candidate(
            name=parsed.get("name") or Path(file.filename).stem,
            position=parsed.get("position", ""),
            email=parsed.get("email", ""),
            phone=parsed.get("phone", ""),
            education=parsed.get("education", ""),
            skills=parsed.get("skills", []),
            experience=parsed.get("experience", []),
            resume_text=raw_text,
            job_id=job_id if job_id else None,
        )
        db.add(candidate)
        await db.commit()
        await db.refresh(candidate)

        # 关联简历到候选人
        resume_record.candidate_id = candidate.id
        await db.commit()

        return {
            "id": candidate.id,
            "filename": file.filename,
            "status": "parsed",
            "message": "简历已解析并创建候选人",
            "data": candidate_to_dict(candidate),
        }

    except Exception as e:
        print(f"[Resume] 解析失败: {e}")
        raise HTTPException(500, f"简历解析失败: {str(e)}")
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# ============================================================
# 面试题
# ============================================================

@app.get("/api/candidates/{candidate_id}/questions")
async def get_candidate_questions(candidate_id: int, db: AsyncSession = Depends(get_db)):
    """获取候选人的面试题（已生成的直接用，未生成的返回空）"""
    result = await db.execute(
        select(Interview)
        .where(Interview.candidate_id == candidate_id)
        .where(Interview.questions.isnot(None))
        .order_by(Interview.created_at.desc())
        .limit(1)
    )
    interview = result.scalar_one_or_none()

    result = await db.execute(
        select(Candidate).where(Candidate.id == candidate_id)
    )
    candidate = result.scalar_one_or_none()

    if interview and interview.questions:
        return {
            "sections": interview.questions["sections"],
            "candidate": candidate_to_dict(candidate) if candidate else None,
        }

    return {
        "sections": [],
        "candidate": candidate_to_dict(candidate) if candidate else None,
        "message": "尚未生成面试题，请调用 POST 生成",
    }


@app.post("/api/candidates/{candidate_id}/generate-questions")
async def generate_questions(
    candidate_id: int,
    data: dict = None,
    db: AsyncSession = Depends(get_db),
):
    """AI 生成面试题，保存到最近的面试记录"""
    if not DEEPSEEK_API_KEY:
        raise HTTPException(503, "DeepSeek API 未配置")

    result = await db.execute(
        select(Candidate).where(Candidate.id == candidate_id)
    )
    candidate = result.scalar_one_or_none()
    if not candidate:
        raise HTTPException(404, "候选人不存在")

    try:
        resume_dict = candidate_to_dict(candidate)
        sections = await ai_service.generate_questions(resume_dict)

        # 保存到最近一次面试记录
        result = await db.execute(
            select(Interview)
            .where(Interview.candidate_id == candidate_id)
            .order_by(Interview.created_at.desc())
            .limit(1)
        )
        interview = result.scalar_one_or_none()

        if interview:
            interview.questions = {"sections": sections}
            await db.commit()

        return {
            "sections": sections,
            "candidate": resume_dict,
        }
    except Exception as e:
        print(f"[AI] 生成面试题失败: {e}")
        raise HTTPException(500, f"AI 生成失败: {str(e)}")


# ============================================================
# 面试记录
# ============================================================

@app.get("/api/interviews")
async def list_interviews(
    candidate_id: int = None,
    db: AsyncSession = Depends(get_db),
):
    """面试记录列表"""
    query = select(Interview).order_by(Interview.created_at.desc())
    if candidate_id:
        query = query.where(Interview.candidate_id == candidate_id)

    result = await db.execute(query)
    interviews = result.scalars().all()

    return [
        {
            "id": i.id,
            "candidate_id": i.candidate_id,
            "transcript_preview": i.transcript[:200] if i.transcript else "",
            "has_notes": bool(i.notes),
            "has_questions": bool(i.questions),
            "audio_filename": i.audio_filename,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        }
        for i in interviews
    ]


@app.get("/api/interviews/{interview_id}")
async def get_interview(interview_id: int, db: AsyncSession = Depends(get_db)):
    """面试记录详情"""
    result = await db.execute(
        select(Interview).where(Interview.id == interview_id)
    )
    i = result.scalar_one_or_none()
    if not i:
        raise HTTPException(404, "面试记录不存在")

    return {
        "id": i.id,
        "candidate_id": i.candidate_id,
        "transcript": i.transcript,
        "notes": i.notes,
        "questions": i.questions,
        "audio_filename": i.audio_filename,
        "created_at": i.created_at.isoformat() if i.created_at else None,
    }


@app.put("/api/interviews/{interview_id}/notes")
async def save_interview_notes(
    interview_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    """保存面试笔记（评分 + 评价 + 录用倾向）"""
    result = await db.execute(select(Interview).where(Interview.id == interview_id))
    interview = result.scalar_one_or_none()
    if not interview:
        raise HTTPException(404, "面试记录不存在")

    notes = interview.notes or {}
    notes.update({
        "overall_impression": data.get("overall_impression", ""),
        "section_notes": data.get("section_notes", []),
        "key_quotes": data.get("key_quotes", []),
        "tendency": data.get("tendency", ""),
        "tags": data.get("tags", []),
    })
    interview.notes = notes
    await db.commit()

    # 同步更新候选人状态和评分
    if interview.candidate_id:
        result = await db.execute(select(Candidate).where(Candidate.id == interview.candidate_id))
        candidate = result.scalar_one_or_none()
        if candidate:
            scores = [s.get("score", 0) for s in notes.get("section_notes", []) if s.get("score")]
            if scores:
                candidate.score = round(sum(scores) / len(scores), 1)
            tendency = notes.get("tendency", "")
            # 从设置中读取倾向→状态映射
            status_map = {}
            map_result = await db.execute(select(Setting).where(Setting.key == "tendency_map"))
            map_row = map_result.scalar_one_or_none()
            if map_row and map_row.value:
                for item in map_row.value:
                    status_map[item.get("label", "")] = item.get("targetStatus", "")
            if not status_map:
                status_map = {"强烈推荐": "passed", "推荐": "passed", "保留": "interview", "不推荐": "rejected"}
            if tendency in status_map:
                candidate.status = status_map[tendency]
            await db.commit()

    return {"ok": True, "id": interview_id}


@app.patch("/api/candidates/{candidate_id}")
async def update_candidate(
    candidate_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    """更新候选人信息（状态、评分等）"""
    result = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(404, "候选人不存在")

    for field in ("status", "position", "email", "phone", "education"):
        if field in data:
            setattr(c, field, data[field])
    if "skills" in data:
        c.skills = data["skills"]
    if "score" in data:
        c.score = data["score"]
    if "experience" in data:
        c.experience = data["experience"]

    await db.commit()
    await db.refresh(c)
    return candidate_to_dict(c)


@app.get("/api/candidates/{candidate_id}/interviews")
async def get_candidate_interviews(candidate_id: int, db: AsyncSession = Depends(get_db)):
    """获取候选人的所有面试记录"""
    result = await db.execute(
        select(Interview)
        .where(Interview.candidate_id == candidate_id)
        .order_by(Interview.created_at.desc())
    )
    interviews = result.scalars().all()
    return [{
        "id": i.id,
        "transcript_preview": (i.transcript or "")[:200],
        "has_notes": bool(i.notes),
        "has_questions": bool(i.questions),
        "notes": i.notes,
        "created_at": i.created_at.isoformat() if i.created_at else None,
    } for i in interviews]
