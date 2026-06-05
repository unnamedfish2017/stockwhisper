# 股情报 (stockwhisper)

A股小道消息情报社区。用户提交机构/个人推荐消息，AI 自动评分、分级、摘要，并回测历史表现。gamified 声誉体系 + 解锁经济门控高价值内容。

## 快速启动

```bash
pip install -r requirements.txt

# 设置 SMTP（用于邮箱注册验证码）
export SMTP_USER=your@gmail.com
export SMTP_PASS=your_app_password

python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8289
```

访问 http://localhost:8289

## 数据源（均可选，缺失时自动降级）

| 路径 | 用途 |
|------|------|
| `data/stockwhisper.db` | SQLite 运行时数据库，启动自动创建 |
| `../私有信息/info_collection.db` | 私有种子数据，首次启动时导入 |
| `/home/vscode/workspace/data/store/rsync/tonglian_data_daily/` | A 股日线行情，用于回测 |

## 功能

- 邮箱验证码注册，Cookie 会话，游客模式
- AI 价值评分（1-100）+ S/A/B/C 分级
- 投递消息自动解锁同等价值消息
- 5/20/60 日回测 + 用户声誉/经验值/等级体系
- 无限滚动情报流，搜索/筛选

## 环境变量

| 变量 | 说明 |
|------|------|
| `SMTP_HOST` | SMTP 服务器，默认 `smtp.gmail.com` |
| `SMTP_PORT` | 默认 `587` |
| `SMTP_USER` | 发件邮箱 |
| `SMTP_PASS` | 应用专用密码 |
| `AGUWHISPER_LLM_API_KEY` / `OPENAI_API_KEY` | LLM 摘要（可选） |
| `AGUWHISPER_LLM_BASE_URL` | 自定义 LLM 接口地址（可选） |
| `HOST` / `PORT` | 监听地址，默认 `0.0.0.0:8289` |
