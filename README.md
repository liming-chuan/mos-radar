# MOS Radar：安全边际雷达 V6

美股交易日周一至周五运行：盘后完整扫描一次美股候选池；开盘前、午盘、下午通过 SMTP 邮件服务发送报告。周六、周日不自动运行，因为美股不开盘。报告只做候选筛选，不做自动买卖建议。

## 运行逻辑

- 周一至周五美东 18:37：盘后完整扫描，更新财报/价格估值，保存 `data/results/mos_latest.csv` 和 `reports/latest_report.md`
- 周一至周五美东 08:31：开盘前邮件，发送昨晚完整报告
- 周一至周五美东 12:31：午盘价格更新，只更新股价并重算安全边际
- 周一至周五美东 15:31：下午价格更新，只更新股价并重算安全边际

## 安全边际公式

```text
安全边际 = (保守内在价值/股 - 当前股价) / 当前股价
```

V6 的保守内在价值按行业模型生成估值候选，并取最低的有效估值：

```text
最近/3年/5年 FCF 中较低的正值 × 行业保守倍数
最近 FCF × 10倍或更低
5年保守 DCF
净现金 + 保守 FCF 基数 × 8倍
最近/正常化净利润 × 行业保守 PE
金融股：按 ROE 分层的 PB 模型
```

REIT/地产类公司需要 AFFO/NOI 专门模型，V6 默认跳过。

## V6 风险控制

- yfinance 数据抓取增加轻量重试，适配 GitHub Actions 免费服务器上的偶发网络抖动。
- 增加价值陷阱识别：收入连续下降、毛利率/营业利润率恶化、FCF 波动过大、利息覆盖不足、债务过高、股权稀释等。
- 增加评级封顶：现金流、质量、债务或数据质量不过关时，即使安全边际很高，也不会直接给 S/A。
- 报告显示 20% / 35% / 50% 安全边际对应观察价，方便人工复核价格触发区。
- 支持 `DRY_RUN=true`，本地或 GitHub 手动测试时只生成报告，不发送邮件。

## 评级

- S：安全边际 > 50%，总分较高
- A：安全边际 35%—50%，强候选
- B：安全边际 20%—35%，观察
- C_THIN：安全边际太薄
- D_TRAP：疑似价值陷阱
- PASS：没有安全边际
- NO_DATA / ERROR：数据不足或抓取失败
- SKIP：V6 暂不自动估值的行业或模型，例如 REIT/地产类公司

## GitHub Secrets

进入仓库：Settings → Secrets and variables → Actions → Repository secrets，添加：

```text
SMTP_HOST=smtp.qq.com
SMTP_PORT=587
SMTP_USER=你的发件邮箱，例如 example@example.com
SMTP_PASSWORD=邮箱 SMTP 授权码，不是网页登录密码
SMTP_SSL=false
MAIL_TO=你的Outlook邮箱，例如 yourname@outlook.com
MAIL_FROM_NAME=MOS Radar
```

也可以用 465 SSL：

```text
SMTP_PORT=465
SMTP_SSL=true
```

## GitHub Variables

进入仓库：Settings → Secrets and variables → Actions → Variables，添加：

```text
TOP_MOS_COUNT=50
TRAP_COUNT=30
THIN_COUNT=30
MAX_TICKERS=0
REQUEST_SLEEP_SECONDS=0.2
PRICE_SLEEP_SECONDS=0.05
SEND_AFTER_CLOSE=false
DRY_RUN=false
```

说明：

- `TOP_MOS_COUNT=50`：邮件里显示更多安全边际厚的公司。
- `MAX_TICKERS=0`：扫描 `data/universe.csv` 里的全部股票；测试时可设为 10。
- `SEND_AFTER_CLOSE=false`：盘后只生成报告不发邮件；如果想盘后也发，改为 `true`。
- `DRY_RUN=false`：线上正常发邮件；本地或手动测试可设为 `true`，只生成报告不发邮件。

## 股票池更新

GitHub Actions 里的 `Update Universe` 会从 Nasdaq Trader 官方列表获取美国上市股票，再用 Yahoo quote 批量验证价格、市值和成交量。V6.0.1 起使用批量请求，避免单个 ticker 卡住整个更新任务。

建议参数：

```text
How many tickers to keep = 1000
Minimum market cap = 1000000000
Minimum average volume = 100000
```

## 本地测试

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export SMTP_HOST=smtp.qq.com
export SMTP_PORT=587
export SMTP_USER=你的发件邮箱
export SMTP_PASSWORD=你的邮箱 SMTP 授权码
export SMTP_SSL=false
export MAIL_TO=你的Outlook邮箱
export TOP_MOS_COUNT=50
export MAX_TICKERS=10
export RUN_MODE=manual
export DRY_RUN=true
python src/main.py
```

## 扩展股票池

编辑 `data/universe.csv`，保持一列：

```csv
ticker
AAPL
MSFT
TXN
QCOM
```

第一版建议先扫 200—1000 只大中盘股，不建议直接扫所有低流动性小票。

## 重要提醒

1. V6 使用 yfinance，适合个人研究原型，不适合机构级数据可靠性。
2. 价值股、周期股、半导体股必须人工复核最新财报和行业周期。
3. REIT/地产类公司 V6 默认跳过，因为需要 AFFO/NOI 专门模型。
4. 本项目不会自动下单，报告不构成投资建议。
