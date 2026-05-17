# MOS Radar：安全边际雷达 V6.3.4

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

V6.3 的保守内在价值按行业模型生成估值候选，并取最低的有效估值：

```text
Owner FCF = 经营现金流 - 资本开支 - 股权激励 SBC
最近/3年/5年 Owner FCF 中较低的正值 × 行业保守倍数
最近 Owner FCF × 10倍或更低
5年保守 DCF，折现率至少为 10年期美债收益率 + 行业风险溢价
净现金 + 保守 FCF 基数 × 8倍
最近/正常化净利润 × 行业保守 PE
资产型/周期型公司：NCAV 或有形账面价值折价口径
金融股：按 ROE 分层的 PB/TBV 模型
```

REIT/地产类公司需要 AFFO/NOI 专门模型，V6 默认跳过。

## V6.3 风险控制

- yfinance 数据抓取增加轻量重试，适配 GitHub Actions 免费服务器上的偶发网络抖动。
- V6.3 使用 Owner FCF，不再把 SBC 当成免费现金流；科技股和软件股的伪 FCF 会被压低。
- V6.3 引入 `^TNX` 作为 10 年期美债收益率参考，折现率使用“行业最低折现率”和“无风险利率 + 行业风险溢价”两者较高值。
- V6.3 增加 NCAV 和有形账面价值折价估值，用于资产型、周期型和部分金融公司，避免只看现金流。
- V6.3 增加应计利润陷阱：净利润长期明显高于 Owner FCF 时，会触发 `high_accrual_ratio` 或 `very_high_accrual_ratio`。
- V6.3 对周期行业进一步保守化：能源、材料、工业、半导体周期股不再直接使用最近一年 FCF 的高点估值，优先使用正常化现金流和高点折扣。
- 增加价值陷阱识别：收入连续下降、毛利率/营业利润率恶化、FCF 波动过大、利息覆盖不足、债务过高、股权稀释等。
- 增加评级封顶：现金流、质量、债务或数据质量不过关时，即使安全边际很高，也不会直接给 S/A。
- 增加 ADR/海外股票币种防护：报价币种和财报币种不一致时暂不自动估值，避免台币/卢比等财报数据被当成美元。
- 贵金属/矿业股使用更保守的周期模型，且最高评级封顶，避免把金价高点 FCF 当成永久现金流。
- 报告显示 20% / 35% / 50% 安全边际对应观察价，方便人工复核价格触发区。
- 报告显示完整评级分布和未进入 S/A/B 的样本原因，避免空报告无法诊断。
- 报告显示市场行业分布，用来判断问题来自股票池结构，还是估值/排序逻辑。
- 支持独立历史价格回放压力测试：当前市场扫描和历史回放拆成两个 GitHub Actions，互不干扰。
- 当前市场和历史回放都会上传 Actions artifact，运行结束后可在本次 workflow 页面下载 CSV 和报告。
- 历史回放可直接发送邮件；当时未上市或无历史价格的股票会标记为 `SKIP`，并在报告里显示历史价格覆盖率，避免 Yahoo 缺价日志干扰判断。
- 报告单独显示接近候选的 `C_THIN` 股票，并使用紧凑诊断表避免理由列竖排。
- 报告把非金融经营型公司和金融股分开显示；金融股使用 PB/ROE 口径，不再和普通 FCF 公司混排；非金融诊断表不再重复显示金融股。
- 金融股不再使用 FCF Yield 和债务/EBITDA 评分；银行、保险等优先使用有形权益 PB/ROE；基金、BDC、封闭式基金等 NAV/NII 驱动资产默认跳过，等待专门模型。
- 评级理由区分“安全边际偏薄”和“安全边际够但综合分/质量不足”，避免误读。
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
HOLDINGS_TICKERS=你的持仓代码，用英文逗号分隔，例如 AAPL,MSFT,TSM
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

V6.3.3 起，如果 Yahoo quote 临时返回空结果，`Update Universe` 会保留已有 `data/universe.csv`，不会写入空股票池，也不会因为缺少 `ticker` 列报错。运行结束后会上传 `mos-radar-universe` artifact，方便检查本次股票池文件。

建议参数：

```text
How many tickers to keep = 2000
Minimum market cap = 1000000000
Minimum average volume = 100000
```

`Update Universe` 日志会打印实际收到的 `limit / min_market_cap / min_avg_volume`，以及 quote 验证数量、过滤后数量。如果你填了 2000 但最终仍是 1000，要先看日志里的 `Universe config: limit=...` 是否真的等于 2000。

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

## GitHub Actions 运行

### 当前市场扫描

GitHub Actions → `MOS Radar Daily Scanner` → `Run workflow`

这个 workflow 扫描当前市场，也负责工作日定时运行。运行结束后，在本次 run 页面下载 artifact：

```text
mos-radar-current-results
```

里面包含：

```text
data/results/mos_latest.csv
data/results/mos_snapshot_latest.csv
reports/latest_report.md
```

### 历史价格回放

GitHub Actions → `MOS Radar Historical Replay` → `Run workflow`：

```text
backtest_date = 2022-10-14
backtest_use_latest = false
dry_run = false
```

说明：

- 这是历史价格压力测试，不是严格 point-in-time 财报回测。
- 系统会先按当前 V6.3 模型计算保守价值，再用历史日期附近收盘价重算安全边际。
- 输出文件保存为 `data/results/historical_replay_YYYY-MM-DD.csv`，报告标题为“历史价格回放压力测试”。
- 运行结束后，在本次 run 页面下载 artifact：`mos-radar-historical-YYYY-MM-DD`。
- `dry_run=false` 会发送邮件；`dry_run=true` 只生成 artifact 不发邮件。
- `backtest_use_latest=true` 会跳过重新扫描，直接用已有 `mos_latest.csv` 做价格回放，速度更快但依赖旧结果。
- 当时尚未上市、改名、分拆或 Yahoo 缺少历史价的股票会显示为 `SKIP`，不会作为模型失败处理。
- 严格历史回测需要 SEC/财报公告日期和当时可见财报数据，免费 yfinance 不能可靠完成这一层。

## 重要提醒

1. V6.3.4 使用 yfinance，适合个人研究原型，不适合机构级数据可靠性。
2. 价值股、周期股、半导体股必须人工复核最新财报和行业周期。
3. REIT/地产类公司 V6 默认跳过，因为需要 AFFO/NOI 专门模型。
4. 本项目不会自动下单，报告不构成投资建议。
