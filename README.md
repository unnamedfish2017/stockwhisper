# 外网访问：[https://stockwhisper.doujie.ccwu.cc/](https://stockwhisper.doujie.ccwu.cc/)

# 股情报 StockWhisper

[![License: CC BY-NC-SA 4.0](https://img.shields.io/badge/License-CC%20BY--NC--SA%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc-sa/4.0/)

股情报是一个面向 A 股线索的共享互助社区：用户分享机构、产业链和社群流传的可验证情报，平台通过结构化提炼、风险烟测、AI/规则评分、分级展示、社区反馈和历史回测，把零散消息沉淀为可复盘的共同情报资产；贡献高质量线索的人也能通过交换、直看额度和信息源成长获得更多高价值内容，实现分享、共享、互惠互利。项目是一个小型 FastAPI 后端加静态前端，默认运行在 `8289` 端口。

> 免责声明：本项目仅用于开源学习和技术交流。所有内容不构成投资建议，不得用于商业用途。投资有风险，入市需谨慎。

## 快速启动

安装依赖：

```bash
rtk python3 -m pip install -r requirements.txt
```

本地开发启动：

```bash
rtk python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8289
```

访问：

```text
http://127.0.0.1:8289
```

未配置 SMTP 时，验证码会降级打印到服务日志，方便本地调试；线上运行建议配置 SMTP。

## 长期运行

仓库提供了一个手动执行的运行管理脚本，不依赖提交密钥：

```bash
chmod +x scripts/stockwhisperctl.sh
scripts/stockwhisperctl.sh start
scripts/stockwhisperctl.sh status
scripts/stockwhisperctl.sh logs
scripts/stockwhisperctl.sh restart
scripts/stockwhisperctl.sh stop
```

脚本默认：

| 项目 | 默认值 |
| --- | --- |
| 监听地址 | `0.0.0.0` |
| 端口 | `8289` |
| PID 文件 | `data/stockwhisper.pid` |
| 日志文件 | `data/stockwhisper.log` |
| 本地环境变量文件 | `.env.local` |

可以在 `.env.local` 写入本地配置。该文件已被 `.gitignore` 忽略，不会提交：

```bash
SMTP_HOST=smtp.example.com
SMTP_PORT=465
SMTP_USER=your_email@example.com
SMTP_PASS=your_smtp_app_password
SMTP_FROM=your_email@example.com
AGUWHISPER_LLM_TIMEOUT=20
```

也可以临时覆盖端口：

```bash
PORT=8290 scripts/stockwhisperctl.sh restart
```

## 数据与本地状态

| 路径 | 用途 | 是否应提交 |
| --- | --- | --- |
| `data/stockwhisper.db` | SQLite 运行时数据库 | 否 |
| `data/stockwhisper.log` | 长期运行日志 | 否 |
| `data/stockwhisper.pid` | 长期运行 PID | 否 |
| `data/stock_lookup.json` | 股票名称/代码缓存 | 否 |
| `../私有信息/info_collection.db` | 私有种子数据，存在时启动导入 | 否 |
| `/home/vscode/workspace/data/store/rsync` | 本地 A 股行情数据，供回测读取 | 外部数据 |
| `../llm_provider/call_llm.py` | LLM Provider 调用队列 | 外部模块 |

不要提交真实数据库、授权码、`.env.local`、邮件密码或任何私有数据。本地这些文件不要删除，否则当前机器上的服务可能无法继续发送邮件、导入数据或完成回测。

## 核心功能

- 邮箱验证码注册、登录、Cookie 会话、游客浏览。
- 忘记密码：用户先填写用户名或注册邮箱，系统匹配历史邮箱并只展示脱敏邮箱，再发送验证码改密。
- 一个邮箱只能注册一个账户，重复注册会提示 `该邮箱已注册`。
- 首页保留两个同级模块：`近3个交易日高收益信号` 和 `当日情报流信号`。
- 当日情报流的日期范围是最近一个交易日及之后日期的信号，并优先展示当前用户有权限阅读的内容，再展示锁定内容。
- 自选股需要登录后生效。信息流和近 3 日高收益信号都可以添加/移除自选股，自选股页按时间倒序展示相关历史情报。
- 社区反馈只保留 `有用` 和 `存疑` 两档；评论、反馈和举报会进入信息源评价和排序。
- 历史回测功能仍保留在后端和详情页，但左侧导航不展示独立历史回测模块。
- 页面、接口、复制内容和详情内容都会附带版权标记、内容指纹或零宽水印。

## 评分与分级规则

内容先经过规则评分，再叠加稀缺性、风控、LLM 逻辑评分和后续社区/回测信号。

基础评分维度：

| 维度 | 权重 | 含义 |
| --- | ---: | --- |
| 标的明确度 | 30 | 股票、代码、产业链、客户或催化越明确越高 |
| 信息密度 | 30 | 事实、订单、产能、业绩、时间线越完整越高 |
| 时效 | 10 | 带推荐日期和短期验证节点会加分 |
| 来源可信度 | 10 | 机构、推荐人或可追踪来源会加分 |
| 可验证性 | 20 | 可回测、可复盘、可被行情或公告验证的内容更高 |
| 稀缺性 | 15 | 重复搬运和同质内容会扣分，独家或差异化验证加权 |

等级阈值：

| 等级 | 分数 |
| --- | ---: |
| S | `>= 86` |
| A | `>= 72` |
| B | `>= 55` |
| C | `< 55` |

查看权限：

| 等级 | 规则 |
| --- | --- |
| C | 免费查看 |
| B | L2 或贡献度 35 |
| A | L3 或贡献度 80 |
| S | L4 或贡献度 150 |

等级经验：

| 用户等级 | XP 门槛 | 等级保底直看额度 | 信息源最低分参考 |
| --- | ---: | ---: | ---: |
| L1 观察员 | 0 | 1 | 0 |
| L2 线索员 | 80 | 3 | 45 |
| L3 研究员 | 220 | 6 | 55 |
| L4 情报官 | 520 | 12 | 65 |

直看额度不会因为重新计算等级而被降低。系统会取现有额度和等级保底额度的较大值。

## 成长与激励规则

| 行为 | 奖励 |
| --- | --- |
| 新用户注册 | 10 次直看额度 |
| 使用邀请码注册 | 20 XP + 10 次直看额度 |
| 邀请一个新用户注册 | 30 XP + 10 次直看额度 + 邀请计数 |
| 投稿 A 级或 S 级信息 | 3 次直看额度 |
| 直看锁定情报 | 消耗 1 次直看额度 |
| 有用/存疑反馈 | 参与 XP 和轻微信誉变化 |
| 评论补充验证 | 参与 XP 和轻微信誉变化 |

信息源等级由 XP、贡献度、信誉分、邀请贡献和社区反馈共同计算：

| 信息源等级 | 源分门槛 | 说明 |
| --- | ---: | --- |
| 新晋观察员 | `< 38` | 起步阶段，主要贡献 C/B 级线索 |
| 可信线索员 | `>= 38` | B 级直看、投稿交换池优先匹配 |
| 核心信息源 | `>= 62` | A 级优先展示、邀请奖励加成 |
| 王牌信息源 | `>= 82` | S 级优先展示、较高直看权益和社区共建权益 |

为避免成长过快，信息源等级不是单靠一次注册或一次投稿决定，而是综合贡献质量、回测表现、社区反馈、独特性、举报扣分和邀请贡献。

## 投稿处理链路

1. 用户粘贴原始内容。
2. `AI 提炼` 先尝试走 `../llm_provider/call_llm.py`，失败时回退到规则提炼。
3. 系统抽取标的、股票代码、核心逻辑、机构/来源、推荐人、关键事实。
4. 规则评分计算基础分和 S/A/B/C 等级。
5. 稀缺性检测会降低重复搬运内容的有效分。
6. 风控烟测会识别保收益、喊单、内幕、满仓等高风险话术并扣分。
7. 如果 LLM 返回 `logic_score`，系统按 `70% LLM 逻辑评分 + 30% 规则评分` 融合。
8. 投稿成功后自动解锁自己提交的线索，并尝试匹配同级交换池。
9. A/S 投稿额外奖励 3 次直看额度。
10. 后端会触发小批量回测刷新，并重新计算用户贡献和信息源分。

LLM Provider 说明：

- 项目不再直接依赖单一 OpenAI 环境变量调用，而是优先导入 `../llm_provider/call_llm.py`。
- `call_llm.py` 内部维护可用 Provider 的 fallback 队列。
- LLM 调用失败后会短暂熔断，期间使用规则提炼，避免录入流程长时间卡住。
- 如需刷新 Provider 队列，到上级 `llm_provider` 目录执行其 README 中的刷新命令。

## 回测与信息流规则

回测读取本地 A 股行情数据，按推荐日期后的可交易日做复盘。核心展示包括 T+1、T+5、T+20、信号价值、最大回撤等字段。近 3 个交易日高收益信号模块来自最近交易日窗口内的回测表现，最多展示 10 条，并只展示可正常解锁的信号。

当日情报流：

- 使用最近一个交易日作为起点。
- 展示该交易日及之后日期的、已经匹配股票代码的信号。
- 排序时先展示当前用户有权限阅读的信号，再展示锁定信号。
- 搜索不局限于当日，可以查历史内容。

历史回测：

- 后端接口和详情页仍保留回测信息。
- 左侧导航不再提供单独历史回测入口。

## 邮件验证码

验证码有效期为 5 分钟。注册邮件使用正式欢迎文案，重置密码邮件使用正式安全提醒文案。

环境变量：

| 变量 | 说明 |
| --- | --- |
| `SMTP_HOST` | SMTP 服务器，默认 `smtp.gmail.com` |
| `SMTP_PORT` | SMTP 端口，默认 `587`，`465` 使用 SSL |
| `SMTP_USER` | 发件邮箱账号 |
| `SMTP_PASS` | SMTP 授权码或应用专用密码 |
| `SMTP_FROM` | 发件人地址，默认使用 `SMTP_USER` |

163 邮箱等服务通常需要使用授权码，而不是网页登录密码。授权码只放在 `.env.local` 或当前进程环境变量中，不要写入 README 或提交到 Git。

## API 概览

| 接口 | 用途 |
| --- | --- |
| `GET /` | 静态首页 |
| `GET /api/me` | 当前用户和等级 |
| `POST /api/send-code` | 注册验证码 |
| `POST /api/register` | 注册 |
| `POST /api/login` | 登录 |
| `POST /api/password-reset/lookup` | 忘记密码账号查询和邮箱脱敏 |
| `POST /api/password-reset/send-code` | 重置密码验证码 |
| `POST /api/password-reset` | 验证码改密 |
| `GET /api/rumors` | 情报流 |
| `POST /api/rumors` | 投稿 |
| `GET /api/rumors/{id}` | 情报详情 |
| `POST /api/rumors/{id}/unlock` | 直看解锁 |
| `POST /api/rumors/{id}/reactions` | 有用/存疑反馈 |
| `POST /api/rumors/{id}/comments` | 评论 |
| `POST /api/rumors/{id}/reports` | 举报 |
| `GET /api/watchlist/history` | 自选股历史情报 |
| `POST /api/watchlist` | 添加自选股 |
| `DELETE /api/watchlist/{code}` | 移除自选股 |
| `GET /api/value-framework` | 评分和权益规则 |
| `GET /api/community-insight` | 首页社区洞察和近 3 日高收益信号 |
| `GET /api/backtests` | 回测列表 |
| `POST /api/backtests/refresh` | 手动刷新回测 |

## 测试与检查

```bash
rtk pytest -q
rtk python3 -m py_compile app/main.py tests/test_growth_mechanics.py tests/test_ui_contracts.py
rtk git diff --check
```

如果只验证最近改动，可以跑定向测试：

```bash
rtk pytest -q tests/test_growth_mechanics.py::test_value_framework_exposes_scoring_and_invite_rewards
rtk pytest -q tests/test_ui_contracts.py
```

## 提交前安全检查

提交前建议执行：

```bash
rtk git status --short
rtk git diff --cached --name-only
rtk git ls-files -o --exclude-standard
rtk rg -n "(SMTP_PASS=.+|OPENAI_API_KEY=.+|AGUWHISPER_LLM_API_KEY=.+|sk-[A-Za-z0-9_-]{20,}|api_key['\\\"]?\\s*[:=])" .
```

预期结果：

- `data/*.db`、`.env.local`、日志、PID 文件不会出现在待提交文件中。
- 真实 SMTP 授权码、API Key、私有数据库不会出现在 diff 或 tracked 文件中。
- `.gitignore` 保留对本地敏感文件的忽略规则。

## 项目结构

```text
app/main.py              FastAPI 应用、数据库、评分、权限、回测和 API
static/index.html        单页前端结构
static/app.js            前端状态、接口调用和交互逻辑
static/styles.css        前端样式
scripts/stockwhisperctl.sh 长期运行管理脚本
scripts/seed_test_data.py 本地测试数据脚本
tests/                   后端和 UI 合同测试
data/                    本地运行数据目录，不提交
```

## 版权与使用

项目采用 `CC BY-NC-SA 4.0`。页面和接口会添加 `StockWhisper::CC-BY-NC-SA-4.0::openclaw-community-intel` 标记；复制内容会追加可见来源和不可见零宽标记。仅限非商业学习、研究和技术交流使用。
