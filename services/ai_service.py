"""AI 分析服务 — DeepSeek API"""
import json
import re

from openai import AsyncOpenAI


def _build_extract_prompt(dimensions=None):
    """根据维度配置动态生成面试笔记提取 prompt"""
    if not dimensions:
        dimensions = ["技术能力", "沟通表达", "项目经验", "团队协作", "学习能力", "文化契合"]
    dim_list = "\n".join([f"   - {d}" for d in dimensions])
    dim_count = len(dimensions)
    first_dim = dimensions[0]
    dim_example = ", ".join([
        '{"dimension": "' + first_dim + '", "score": 4, "note": "..."}',
        "..."
    ])

    return f"""你是一位资深 HR 面试官，拥有 10 年以上的招聘经验。请根据以下面试录音转写文本，提取关键信息。

## 要求

请仔细阅读转写文本，从中提取：

1. **overall_impression**（整体印象）：2-3 句话概括候选人表现，包含优点和不足
2. **section_notes**（各维度评价）：对以下 {dim_count} 个维度分别给出评价和打分(1-5分)：
{dim_list}
   每个维度格式：{{"dimension": "维度名", "score": 4, "note": "评价内容"}}
3. **key_quotes**（关键语录）：3-5 条候选人的原话或核心观点，值得记录
4. **tendency**（录用建议）：四选一 —— "强烈推荐"、"推荐"、"保留"、"不推荐"
5. **tags**：2-4 个标签概括候选人特点（如"技术深度好"、"沟通偏弱"等）

## 输出格式

严格输出 JSON，不要任何其他文字：

```json
{{
  "overall_impression": "...",
  "section_notes": [
    {dim_example}
  ],
  "key_quotes": ["...", "..."],
  "tendency": "推荐",
  "tags": ["...", "..."]
}}
```

## 面试转写文本

{{transcript}}
"""


GENERATE_QUESTIONS_PROMPT = """你是一位资深技术面试官。根据候选人简历，生成一套结构化面试题。

## 候选人信息

{resume_info}

## 要求

生成 5 个面试板块，每个板块 2-4 题。每题需要包含：
- q_num: 题号
- text: 面试题目
- tags: 考察点标签
- answers: 标准答案参考，分三个档次：
  - good: 优秀回答标准
  - medium: 中等回答标准
  - bad: 较差回答标准

5 个板块：
1. 自我介绍与动机（考察候选人为什么来、职业规划是否清晰）
2. 技术深度（根据简历中的技术栈出题）
3. 项目经验深挖（针对简历中的具体项目提问）
4. 软技能与团队协作（沟通、冲突处理、协作）
5. 文化契合与职业规划（价值观、成长意愿）

## 输出格式

严格输出 JSON：

```json
{
  "sections": [
    {
      "num": 1,
      "name": "自我介绍与动机",
      "questions": [
        {
          "q_num": "Q1",
          "text": "...",
          "tags": "动机/适配",
          "answers": {
            "good": "...",
            "medium": "...",
            "bad": "..."
          }
        }
      ]
    }
  ]
}
```
"""


class AIService:
    """DeepSeek AI 分析服务"""

    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com"):
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url + "/v1" if not base_url.endswith("/v1") else base_url,
        ) if api_key else None

    async def _chat(self, system: str, user: str, temperature: float = 0.3) -> str:
        """调用 DeepSeek chat completion"""
        print(f"[AI] 调用 DeepSeek, prompt 长度: {len(user)}")
        resp = await self.client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=4096,
        )
        content = resp.choices[0].message.content or ""
        print(f"[AI] DeepSeek 返回: {len(content)} 字符, 前200字: {content[:200]}")
        return content

    @staticmethod
    def _parse_json(text: str) -> dict:
        """从 AI 回复中提取 JSON"""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        raise ValueError(f"无法从 AI 回复中解析 JSON: {text[:200]}...")

    # === 面经提取 ===

    async def extract_notes(self, transcript: str, candidate_name: str = "",
                            position: str = "", dimensions: list = None) -> dict:
        """从面试转写文本中提取结构化笔记"""
        if len(transcript) > 15000:
            print(f"[AI] 转写文本过长({len(transcript)}字)，截取前15000字")
            transcript = transcript[:15000]

        prompt = _build_extract_prompt(dimensions).replace("{transcript}", transcript)

        response = await self._chat(
            system="你是一位资深HR面试官。你只输出 JSON，不输出任何其他内容。",
            user=prompt,
            temperature=0.3,
        )

        notes = self._parse_json(response)

        notes.setdefault("overall_impression", "")
        notes.setdefault("section_notes", [])
        notes.setdefault("key_quotes", [])
        notes.setdefault("tendency", "保留")
        notes.setdefault("tags", [])

        return notes

    # === 通用调用 ===

    async def call(self, prompt: str, temperature: float = 0.3) -> dict:
        """通用 AI 调用 — 发送 prompt 并解析 JSON 返回"""
        response = await self._chat(
            system="你只输出 JSON，不输出任何其他内容。",
            user=prompt,
            temperature=temperature,
        )
        return self._parse_json(response)

    # === JD 生成 ===

    async def generate_jd(self, title: str, department: str = "",
                          salary_min: str = "", salary_max: str = "") -> str:
        """根据岗位信息生成职位描述"""
        prompt = f"""你是一位资深 HR 和招聘专家。请根据以下岗位基本信息，生成一份专业、详细的职位描述（JD）。

## 基本信息
- 岗位名称：{title}
- 所属部门：{department or '未指定'}
- 薪资范围：{salary_min or '面议'}K - {salary_max or '面议'}K

## 要求
请生成包含以下部分的 JD：
1. **岗位职责** — 4-6 条具体的日常工作内容，用数字列表
2. **任职要求** — 4-6 条硬性技能和经验要求，用数字列表
3. **加分项** — 2-4 条锦上添花的条件，用短横线列表

要求专业、具体、有针对性，避免空泛的套话。根据岗位名称行业特点来写。

## 输出格式
直接输出 JD 文本，不要额外的说明文字。"""

        response = await self._chat(
            system="你是一位资深 HR 招聘专家，只输出 JD 文本，不要其他内容。",
            user=prompt,
            temperature=0.7,
        )
        return response.strip()

    # === 面试题生成 ===

    async def generate_questions(self, resume: dict | None) -> list[dict]:
        """根据候选人简历生成面试题"""
        if resume:
            info_parts = [
                f"姓名：{resume.get('name', '未知')}",
                f"目标岗位：{resume.get('position', '未知')}",
                f"工作经验：{resume.get('experience', '未知')}",
                f"技能：{', '.join(resume.get('skills', []))}",
            ]
            if resume.get("work"):
                info_parts.append("工作经历：")
                for w in resume["work"]:
                    info_parts.append(f"- {w.get('company', '')} | {w.get('role', '')} | {w.get('period', '')}")
                    info_parts.append(f"  {w.get('desc', '')}")
            resume_info = "\n".join(info_parts)
        else:
            resume_info = "无候选人简历信息"

        prompt = GENERATE_QUESTIONS_PROMPT.replace("{resume_info}", resume_info)

        response = await self._chat(
            system="你是一位资深技术面试官。你只输出 JSON，不输出任何其他内容。",
            user=prompt,
            temperature=0.7,
        )

        result = self._parse_json(response)
        return result.get("sections", [])
