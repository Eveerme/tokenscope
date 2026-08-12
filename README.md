# TokenScope

统计 Hermes / Codex / Claude Code / zcode 四个 AI 编码工具 token 消耗的本地 Web 平台（类似 cc-switch 的用量统计）。

> 详细的安装与使用说明见：
> - 📦 [安装手册](docs/安装手册.md)
> - 📖 [使用手册](docs/使用手册.md)

## 功能一览

- **仪表盘**：输入 / 输出 / 缓存读取 / 推理 tokens、API 调用、会话数、估算成本总览；按天/周/月趋势图；按模型、工具（Hermes / Codex / Claude Code / zcode）、来源（桌面端 / VS Code / 终端 / 子代理…）、任务类型分组图表与明细表
- **全局筛选**：顶部工具下拉 + 时间范围（近 7/30/90 天、全部、自定义），所有视图联动
- **会话明细**：四工具全量会话表格，支持搜索（标题 / ID / 模型 / 工具 / 工作目录）、模型 / 来源筛选、任意列排序、分页；点击行查看详情（工作目录、token 卡片，Hermes 会话另有任务级拆分）
- **数据源**：自动发现四工具本地数据；可手动添加其他路径（如其他机器拷贝的文件）；**只读访问，不修改任何数据**
- **定价设置**：按模型配置单价（USD / 百万 tokens），实时估算成本；内置 DeepSeek / GPT / Claude / GLM / Qwen 示例定价一键填充

## 快速开始

```bash
# 1. 构建前端（仅首次或前端改动后）
cd web
npm install
npm run build

# 2. 启动（默认 http://127.0.0.1:8787）
cd ..
./start.sh              # 或直接: python server.py
```

停止 / 重启：`./stop.sh`、`./restart.sh`

## 数据来源

| 工具 | 数据位置 | 说明 |
|---|---|---|
| Hermes | `%LOCALAPPDATA%\hermes\state.db`（+ profiles/*/state.db） | sessions 表：input / output / cache_read / cache_write / reasoning 全维度 |
| Codex | `~\.codex\state_*.sqlite` + `sessions\**\rollout-*.jsonl` | threads 表会话索引；rollout JSONL 的 token_count 事件拆分明细；模型取自 `~\.codex\config.toml` |
| Claude Code | `~\.claude\projects\*\*.jsonl` | assistant 消息 usage：input / output / cache_creation / cache_read；项目目录名解码为工作目录 |
| zcode | `~\.zcode\cli\db\db.sqlite` | model_usage 表请求级明细（input / output / reasoning / cache），聚合到会话 |

所有数据**只读**访问（SQLite `PRAGMA query_only`），聚合在内存中完成并带缓存（Claude JSONL 首次扫描约 2 秒，之后毫秒级响应）。

## 目录结构

```
tokenscope/
├── server.py        # 后端：REST API + 静态文件伺服（纯标准库，零依赖）
├── parsers.py       # 四工具统一解析器（数据源发现 + 记录解析 + 缓存）
├── start.sh         # 启动脚本（后台运行 + PID 管理 + 健康检查）
├── stop.sh          # 停止脚本
├── restart.sh       # 重启脚本
├── config.json      # 运行时生成：数据源列表 + 定价表
├── docs/
│   ├── 安装手册.md   # 安装部署说明
│   └── 使用手册.md   # 功能使用说明
├── web/             # 前端（Vue3 + TS + Element Plus + Tailwind v4 + ECharts）
│   └── dist/        # 构建产物，由 server.py 伺服
└── README.md
```

## 技术栈

- 后端：Python 标准库（http.server + sqlite3），无第三方依赖
- 前端：Vue 3 + TypeScript + Element Plus + Tailwind CSS v4 + ECharts
