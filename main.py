"""AIHR — AI 智能招聘助手 后端服务"""
import os
import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import init_db, get_db, Candidate, Interview, Resume
from services.baidu_asr import BaiduASR
from services.ai_service import AIService
from services.resume_parser import extract_text, parse_resume_with_ai

load_dotenv()

# ---- 配置 ----
BAIDU_APP_ID = os.getenv("BAIDU_APP_ID", "")
BAIDU_API_KEY = os.getenv("BAIDU_API_KEY", "")
BAIDU_SECRET_KEY = os.getenv("BAIDU_SECRET_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

# ---- 服务实例 ----
baidu_asr = BaiduASR(BAIDU_API_KEY, BAIDU_SECRET_KEY, BAIDU_APP_ID) if BAIDU_API_KEY else None
ai_service = AIService(DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL)


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
# 语音转写
# ============================================================

@app.post("/api/interviews/transcribe")
async def transcribe_interview(
    file: UploadFile = File(...),
    candidate_id: int = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """上传录音 → 百度 ASR → 保存转写 → 返回文本"""
    if baidu_asr is None:
        raise HTTPException(503, "百度 ASR 未配置")

    if not file.filename:
        raise HTTPException(400, "无效的文件")

    print(f"[ASR] 收到录音: {file.filename}")

    # 保存临时文件
    suffix = Path(file.filename).suffix or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    file_size = len(content)

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
            "file_size_kb": round(file_size / 1024, 1),
        }
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
        notes = await ai_service.extract_notes(transcript, candidate_name, position)

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
