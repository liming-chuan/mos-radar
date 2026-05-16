# MOS Radar：安全边际雷达 V1

每天盘后完整扫描一次美股候选池；开盘前、午盘、下午通过 SMTP 邮件服务发送报告。报告只做候选筛选，不做自动买卖建议。

## 运行逻辑

- 美东 18:37：盘后完整扫描，更新财报/价格估值，保存 `data/results/mos_latest.csv` 和 `reports/latest_report.md`
- 美东 08:31：开盘前邮件，发送昨晚完整报告
- 美东 12:31：午盘价格更新，只更新股价并重算安全边际
- 美东 15:31：下午价格更新，只更新股价并重算安全边际

## 安全边际公式

```text
安全边际 = (保守内在价值/股 - 当前股价) / 当前股价
```

V1 的保守内在价值取以下估值候选中的最低值：

```text
5年平均FCF × 行业保守倍数
TTM/最近FCF × 10倍或更低
5年正常化净利润 × 行业保守PE
净现金 + 5年平均FCF × 8倍
```

## 评级

- S：安全边际 > 50%，总分较高
- A：安全边际 35%—50%，强候选
- B：安全边际 20%—35%，观察
- C_THIN：安全边际太薄
- D_TRAP：疑似价值陷阱
- PASS：没有安全边际
- NO_DATA / ERROR：数据不足或抓取失败
- SKIP：V1 暂不覆盖的行业，例如金融、REIT、生物科技

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
```

说明：

- `TOP_MOS_COUNT=50`：邮件里显示更多安全边际厚的公司。
- `MAX_TICKERS=0`：扫描 `data/universe.csv` 里的全部股票；测试时可设为 10。
- `SEND_AFTER_CLOSE=false`：盘后只生成报告不发邮件；如果想盘后也发，改为 `true`。

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

1. V1 使用 yfinance，适合个人研究原型，不适合机构级数据可靠性。
2. 价值股、周期股、半导体股必须人工复核最新财报和行业周期。
3. 金融、REIT、生物科技 V1 默认跳过，因为估值模型完全不同。
4. 本项目不会自动下单，报告不构成投资建议。
