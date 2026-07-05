# AIHR — AI 智能招聘助手

AI 驱动的招聘辅助工具，帮助 HR 高效完成面试记录、AI 分析和候选人管理。

## 功能

- 🎙️ **面试录音转写**：浏览器录音 → 飞书语音识别 → 自动转文字
- 🤖 **AI 面经提取**：转写文本 → DeepSeek AI → 结构化面试笔记（各维度评分、关键语录、录用建议）
- 📝 **面试题生成**：根据候选人简历，AI 自动生成个性化面试题（含优/中/差回答标准）
- 📊 **候选人管理**：简历看板、AI 评分、雷达图、人机对比

## 技术栈

- **前端**：单页 HTML（原生 JS + CSS）
- **后端**：Python FastAPI
- **语音识别**：飞书 speech_to_text API
- **AI 分析**：DeepSeek API

## 快速开始

### 1. 环境准备

```bash
# 安装 ffmpeg（音频转换）
# macOS: brew install ffmpeg
# Windows: choco install ffmpeg 或从 ffmpeg.org 下载
# Ubuntu: sudo apt install ffmpeg

# 安装 Python 依赖
pip install -r requirements.txt
```

### 2. 配置 API 密钥

```bash
cp .env.example .env
# 编辑 .env，填入你的 API 密钥
```

| 变量 | 说明 | 获取方式 |
|------|------|----------|
| `FEISHU_APP_ID` | 飞书应用 ID | [飞书开放平台](https://open.feishu.cn) → 创建自建应用 → 凭证与基础信息 |
| `FEISHU_APP_SECRET` | 飞书应用密钥 | 同上 |
| `DEEPSEEK_API_KEY` | DeepSeek API Key | [DeepSeek 开放平台](https://platform.deepseek.com) |

飞书应用需要开启 **语音识别** 权限（`speech_to_text`）。

### 3. 启动

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

打开 `http://localhost:8000`

### 4. 使用流程

**方式一：浏览器录音（需要 HTTPS）**
1. 在「面试管理」页，选择候选人
2. 点击麦克风按钮开始录音
3. 录音结束 → 点击「AI 语音转写」
4. 转写完成 → 点击「AI 提取重点」
5. 表单自动填充面试笔记

**方式二：粘贴转写文本**
1. 用飞书/手机录音 App 录音
2. 将转写文本粘贴到输入框
3. 点击「提交并提取」

## 部署

### Railway（推荐）

```bash
# 1. 安装 Railway CLI
npm i -g @railway/cli

# 2. 登录
railway login

# 3. 初始化
railway init

# 4. 设置环境变量
railway variables set FEISHU_APP_ID=xxx FEISHU_APP_SECRET=xxx DEEPSEEK_API_KEY=xxx

# 5. 部署
railway up
```

Railway 自动检测 Python 项目并提供 HTTPS 域名，录音功能开箱即用。

### 其他平台

确保：
- Python 3.10+
- ffmpeg 可用
- 设置环境变量
- 启动命令：`uvicorn main:app --host 0.0.0.0 --port $PORT`

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/health` | 健康检查 |
| `POST` | `/api/interviews/transcribe` | 上传音频 → 转写 |
| `POST` | `/api/interviews/extract-notes` | 转写文本 → AI 结构化笔记 |
| `POST` | `/api/resumes/upload` | 上传简历 |
| `GET` | `/api/candidates/:id/questions` | 获取面试题 |
| `POST` | `/api/candidates/:id/generate-questions` | AI 生成面试题 |

## 项目结构

```
AIHR/
├── main.py                  # FastAPI 入口 + 路由
├── services/
│   ├── feishu_asr.py        # 飞书语音识别
│   └── ai_service.py        # DeepSeek AI 分析
├── static/
│   └── index.html           # 前端
├── requirements.txt
├── .env.example
└── README.md
```
