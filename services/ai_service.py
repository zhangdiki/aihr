"""AI 分析服务 — DeepSeek API"""
import json
import re

from openai import AsyncOpenAI


EXTRACT_NOTES_PROMPT = """你是一位资深 HR 面试官，拥有 10 年以上的招聘经验。请根据以下面试录音转写文本，提取关键信息。

## 要求

请仔细阅读转写文本，从中提取：

1. **overall_impression**（整体印象）：2-3 句话概括候选人表现，包含优点和不足
2. **section_notes**（各维度评价）：对以下 6 个维度分别给出评价和打分(1-5分)：
   - 技术能力
   - 沟通表达
   - 项目经验
   - 团队协作
   - 学习能力
   - 文化契合
   每个维度格式：{"dimension": "维度名", "score": 4, "note": "评价内容"}
3. **key_quotes**（关键语录）：3-5 条候选人的原话或核心观点，值得记录
4. **tendency**（录用建议）：四选一 —— "强烈推荐"、"推荐"、"保留"、"不推荐"
5. **tags**：2-4 个标签概括候选人特点（如"技术深度好"、"沟通偏弱"等）

## 输出格式

严格输出 JSON，不要任何其他文字：

```json
{
  "overall_impression": "...",
  "section_notes": [
    {"dimension": "技术能力", "score": 4, "note": "..."},
    ...
  ],
  "key_quotes": ["...", "..."],
  "tendency": "推荐",
  "tags": ["...", "..."]
}
```

## 面试转写文本

{transcript}
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
        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 尝试从 markdown code block 提取
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # 尝试找到 { ... } 块
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        raise ValueError(f"无法从 AI 回复中解析 JSON: {text[:200]}...")

    # === 面经提取 ===

    async def extract_notes(self, transcript: str, candidate_name: str = "",
                            position: str = "") -> dict:
        """从面试转写文本中提取结构化笔记"""
        # 截断过长的文本（DeepSeek 上下文够大，但避免不必要成本）
        if len(transcript) > 15000:
            print(f"[AI] 转写文本过长({len(transcript)}字)，截取前15000字")
            transcript = transcript[:15000]

        prompt = EXTRACT_NOTES_PROMPT.replace("{transcript}", transcript)

        response = await self._chat(
            system="你是一位资深HR面试官。你只输出 JSON，不输出任何其他内容。",
            user=prompt,
            temperature=0.3,
        )

        notes = self._parse_json(response)

        # 补全缺失字段
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
            temperature=0.7,  # 稍高一点以增加题目多样性
        )

        result = self._parse_json(response)
        return result.get("sections", [])
