# AIHR — AI 智能招聘助手

AI 驱动的招聘辅助工具，帮助 HR 高效完成简历解析、面试转写、AI 分析和候选人管理。

## 核心功能

- 📄 **简历上传 & AI 解析**：PDF/Word/TXT 上传 → 自动提取姓名、技能、工作经历，创建候选人档案
- 🎙️ **面试语音转写**：浏览器录音 → 百度 ASR 自动转文字（短音频秒级返回，长音频自动切片分段识别）
- 🤖 **AI 面试笔记**：转写文本 → DeepSeek AI → 6 维度评分（技术/沟通/项目/协作/学习/文化）+ 关键语录 + 录用建议
- 📝 **AI 面试题生成**：根据候选人简历，自动生成个性化面试题（含优/中/差回答标准）
- 📊 **候选人管理**：看板视图、AI 评分、工作经历、技能标签
- 🔐 **认证系统**：注册/登录，Token 鉴权

## 技术栈

| 层 | 技术 |
|-----|------|
| 前端 | 单页 HTML（原生 JS + CSS），暗色/亮色双主题 |
| 后端 | Python FastAPI + SQLAlchemy async + SQLite |
| 语音识别 | 百度短语音 REST API（支持自动长音频切片） |
| AI 分析 | DeepSeek API（openai SDK 兼容） |
| 部署 | Docker + 阿里云 FC Custom Container + NAS |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API 密钥

```bash
cp .env.example .env
# 编辑 .env：
#   BAIDU_APP_ID=xxx       百度语音应用 ID
#   BAIDU_API_KEY=xxx      百度语音 API Key
#   BAIDU_SECRET_KEY=xxx   百度语音 Secret Key
#   DEEPSEEK_API_KEY=xxx   DeepSeek API Key
```

### 3. 启动

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

打开 `http://localhost:8000`，注册账户即可使用。

### 4. 使用流程

1. **上传简历**：简历管理页 → 上传 PDF/Word → AI 自动解析创建候选人
2. **面试转写**：面试管理页 → 选择候选人 → 录音或粘贴转写文本 → AI 语音转文字
3. **AI 提取笔记**：转写完成后 → 点击「AI 提取重点」→ 自动填充 6 维度评分和综合评价
4. **AI 生成面试题**：点击「AI 生成面试题」→ 根据简历生成个性化题目

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/health` | 健康检查 |
| `POST` | `/api/auth/register` | 注册 |
| `POST` | `/api/auth/login` | 登录 |
| `GET` | `/api/candidates` | 候选人列表 |
| `GET` | `/api/candidates/:id` | 候选人详情（含面试记录） |
| `POST` | `/api/candidates` | 手动添加候选人 |
| `POST` | `/api/resumes/upload` | 上传简历 → AI 解析 |
| `POST` | `/api/interviews/transcribe` | 上传录音 → 百度 ASR 转写 |
| `POST` | `/api/interviews/extract-notes` | 转写文本 → AI 结构化笔记 |
| `GET` | `/api/candidates/:id/questions` | 获取面试题 |
| `POST` | `/api/candidates/:id/generate-questions` | AI 生成面试题 |
| `GET` | `/api/interviews` | 面试记录列表 |
| `GET` | `/api/notifications` | 通知列表 |

## 项目结构

```
AIHR/
├── main.py                    # FastAPI 入口 + 所有路由
├── database.py                # SQLAlchemy 模型 + SQLite
├── services/
│   ├── baidu_asr.py           # 百度语音识别（短/长音频） + 重试
│   ├── ai_service.py          # DeepSeek AI 分析
│   └── resume_parser.py       # PDF/DOCX 文本提取 + AI 结构化
├── static/
│   └── index.html             # 前端 SPA
├── deploy.py                  # 阿里云 FC 一键部署脚本
├── Dockerfile                 # Docker 构建
├── requirements.txt
├── .env.example
└── README.md
```

## 部署

### 阿里云 FC（函数计算）

```bash
export ALIBABA_ACCESS_KEY_ID=LTAI5t...
export ALIBABA_ACCESS_KEY_SECRET=...
python deploy.py
```

脚本自动完成：Docker 构建 → ACR 推送 → VPC/NAS 创建 → FC 函数部署。

### Docker

```bash
docker build -t aihr .
docker run -p 8000:8000 --env-file .env aihr
```
