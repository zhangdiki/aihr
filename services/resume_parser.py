"""简历解析服务 — PDF/DOCX 提取 + AI 结构化"""
import os

from services.ai_service import AIService


# ---- 文件文本提取 ----

def extract_text(file_path: str, filename: str) -> str:
    """从 PDF / DOCX / TXT 中提取纯文本"""
    ext = os.path.splitext(filename)[1].lower()

    if ext == ".txt":
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    if ext == ".pdf":
        return _extract_pdf(file_path)

    if ext in (".docx", ".doc"):
        return _extract_docx(file_path)

    raise ValueError(f"不支持的简历格式: {ext}（支持 PDF / DOCX / TXT）")


def _extract_pdf(file_path: str) -> str:
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text_parts.append(t)
        text = "\n".join(text_parts)
        if text.strip():
            return text
    except ImportError:
        pass

    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(file_path)
        text_parts = [page.extract_text() or "" for page in reader.pages]
        text = "\n".join(text_parts)
        if text.strip():
            return text
    except ImportError:
        pass

    raise ImportError("需要安装 pdfplumber 或 PyPDF2 来解析 PDF 简历（pip install pdfplumber）")


def _extract_docx(file_path: str) -> str:
    from docx import Document
    doc = Document(file_path)
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


# ---- AI 结构化提取 ----

RESUME_EXTRACT_PROMPT = """你是一个专业的简历解析器。从以下简历文本中提取关键信息，返回严格的 JSON（不要 markdown 标记）。

{
  "name": "姓名",
  "email": "邮箱",
  "phone": "电话",
  "education": "最高学历及学校",
  "skills": ["技能1", "技能2", ...],
  "experience": [
    {"company": "公司名", "role": "职位", "period": "起止时间", "desc": "主要工作描述"}
  ],
  "summary": "一句话总结候选人亮点"
}

简历文本：
{text}

只返回 JSON："""


async def parse_resume_with_ai(text: str, ai_service: AIService) -> dict:
    """用 AI 从简历文本中提取结构化信息"""
    prompt = RESUME_EXTRACT_PROMPT.replace("{text}", text)

    try:
        result = await ai_service.call(prompt, temperature=0.1)
        return result
    except Exception:
        # AI 解析失败时返回空结构，保留原始文本
        return {
            "name": "", "email": "", "phone": "",
            "education": "", "skills": [], "experience": [],
            "summary": "",
        }
