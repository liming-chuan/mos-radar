# MOS Radar：安全边际雷达 V6.6.1

MOS Radar 目前支持美股和港股两套独立扫描通道。V6.5.2 起，自动任务改为每个交易日盘前各完整扫描一次并发送邮件：美股盘前一次，港股盘前一次。港股使用独立 Action、独立股票池、独立结果文件，不影响美股扫描。报告只做候选筛选，不做自动买卖建议。

## 运行逻辑

美股：

- 周一至周五美东盘前约 08:31：完整扫描美股候选池，保存 `data/results/mos_latest.csv` 和 `reports/latest_report.md`，并发送邮件
- 自动任务不再运行午盘、下午和盘后多次扫描，节省 GitHub Actions 免费运行时间

港股：

- 周一至周五香港时间 09:00：完整扫描港股候选池，保存 `data/results/hk_mos_latest.csv` 和 `reports/hk/latest_report.md`，并发送邮件
- 港股当前扫描、历史价格压力测试、股票池更新均使用独立 workflow，不会覆盖美股结果
- 如果 `data/hk_universe.csv` 被更新成空文件，扫描会自动回退到 `data/hk_universe_seed.csv`，避免发送空报告
- 如果 Yahoo 对某个港股返回缺价/疑似退市，系统会静默跳过该 ticker，避免 Action 日志被 404 和 failed download 刷屏
- 港股报告使用 `HK$` 显示股价和每股估值，行业、估值法、模型状态和风险理由统一转成中文展示，避免把内部英文枚举发到邮件里
- 港股股票池更新默认从 HKEX 官方证券清单读取普通股候选，再用 Yahoo 10日均量和价格验证；如果 HKEX 下载失败，才回退到 85 家种子池
- 港股股票池更新默认最小市值为 5亿报价货币；如果 Yahoo 缺少港股市值，系统不会把该股票直接删除，而是按价格和10日平均成交量先保留，市值只作为排序和可用时的过滤条件

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


## V6.6.1 熊市区间方向性验证

- 新增美股/港股独立熊市区间验证 Action：可以输入一个熊市开始日期和结束日期，在区间内按固定交易日间隔采样。
- 财务数据只扫描一次，历史价格按批量矩阵下载，然后本地循环重算多个采样日的安全边际，避免“连续多天 × 全市场 × 财报请求”触发免费数据源风控。
- 系统记录每个采样日出现的 S/A/B 候选，并按股票聚合出现次数、出现频率、中位安全边际、中位评分、后续收益、Alpha 和跑赢比例。
- 输出三张 CSV：`bear_range_signals.csv`、`bear_range_ticker_rank.csv`、`bear_range_summary.csv`。
- 这个功能用于验证“熊市期间反复出现安全边际的股票，后续牛市是否更容易跑赢大盘”，比单个熊市日期更接近真实操作。
- 建议每次只跑一个熊市区间，`sample_every_n_days=5`，`max_sample_dates=20`，分多次跑不同熊市时期，不要一次把所有时期和每日采样全塞进同一个 Action。

## V6.6.0 熊市候选方向性验证

- 新增美股/港股独立熊市验证 Action：一次输入 3 个或更多熊市日期，系统会在每个熊市价格点重新筛出 S/A/B 候选。
- 候选会按后续观察窗口计算收益、大盘收益、超额收益和跑赢比例；默认观察 365、730、1095 天。
- 美股默认基准为 `SPY`，港股默认基准为 `2800.HK`；可以在 Action 表单里手动改成其他指数或 ETF。
- 输出两张 CSV：`bear_validation_summary.csv` 汇总每个熊市日期/观察窗口的胜率和 alpha，`bear_validation_candidates.csv` 保留候选明细。
- 本功能用于验证系统方向性：熊市时筛出的安全边际候选，后续是否倾向于跑赢大盘；它不是严格 point-in-time 回测。
- 报告顶部会明确提示未来函数和幸存者偏差：系统仍使用当前可得财务数据、当前股票池和历史价格。

## V6.5.9 估值引擎鲁棒性升级

- 周期行业进一步退守资产底线：能源、材料、工业、半导体、贵金属和农业周期股不再使用 DCF、PE 或正常化 FCF 高弹性估值，只保留资产加低倍现金流、NCAV 和有形账面价值折价。
- 控股/投资型公司折价由名称硬编码升级为算法判定：长期投资资产占总资产超过 40% 时，自动触发控股公司折价，并记录投资资产占比。
- yfinance 请求如果出现 401/429、Unauthorized 或 Too Many Requests，当前 ticker 立即停止后续财报请求并标记 `YAHOO_RATE_LIMIT`，避免 GitHub Actions 累积重试导致超时。
- 历史价格压力测试警示文案加强，明确说明回放存在未来函数，不代表历史真实投资机会。

## V6.5.8 数据防污染与港股风控

- 美股股票池和估值前双层过滤非普通股：债券、票据、次级债、优先股、封闭基金、信托和含 `%` 的固收类证券会被剔除或标记 `SKIP`，避免母公司财务数据错配到证券价格。
- 港股增加流动性熔断：`.HK` 股票若股价低于 HK$1，或 10 日均成交金额低于 HK$500 万，评级会被封顶；二者同时出现时视为低价低流动性陷阱。
- 港股控股/综合企业引入结构性折价，疑似控股公司保守价值按 0.65 进一步折价。
- 周期行业加入农业/农产品周期识别，并对高 FCF Yield 叠加收入或毛利下滑的公司加入利润反转风险。
- 历史价格压力测试新增醒目的未来函数警示和 `DATA_MISMATCH` 标记；历史模式仍不是严格 point-in-time 回测。

## V6.4 自动化与数据口径升级

- 定时任务按 GitHub Actions 的 UTC cron 精确映射运行模式，V6.5.2 自动任务统一为盘前 `premarket_scan`。
- 盘前/手动完整扫描会把公开市场扫描结果保存到 `state/mos_market_latest.csv`；非完整模式只读取该状态，不再因为 runner 是新环境而偷偷重跑 full scan。
- `state/mos_market_latest.csv` 不保存持仓池，持仓仍来自 `data/holdings.csv` 或 `HOLDINGS_TICKERS`，避免把私人持仓提交到 GitHub。
- 估值口径新增 `financial_period_type`：`TTM` 表示最近四个季度滚动数据，`ANNUAL_FALLBACK` 表示季度数据不足时退回最新年报。
- 报告新增最低估值法、估值候选明细、行业模型状态、持仓风险复核和数据质量诊断。
- 午盘/下午价格更新逻辑仍保留给手动模式使用，但默认自动任务不再触发。
- 股票池更新使用 `liquidity_volume` 和 `volume_source`，不再把 Nasdaq 当日成交量无说明地写成平均成交量。
- 历史功能统一称为“历史价格压力测试”，不是严格 point-in-time 回测。

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
- 报告把非金融经营型公司和金融股分开显示；金融股使用市净率/净资产收益率口径，不再和普通自由现金流公司混排；非金融诊断表不再重复显示金融股。
- 金融股不再使用自由现金流收益率和债务/经营利润评分；银行、保险等优先使用有形权益市净率/净资产收益率；基金、BDC、封闭式基金等 NAV/NII 驱动资产默认跳过，等待专门模型。
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
HOLDINGS_TICKERS=你的美股持仓代码，用英文逗号分隔，例如 AAPL,MSFT,TSM
HOLDINGS_TICKERS_HK=你的港股持仓代码，用英文逗号分隔，例如 0700.HK,9988.HK,0005.HK
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
PRICE_SLEEP_SECONDS=0.02
USE_FUNDAMENTALS_CACHE=true
FUNDAMENTALS_CACHE_DAYS=7
SEND_AFTER_CLOSE=false
DRY_RUN=false
HK_MAX_TICKERS=0
PRICE_MATRIX_BATCH_SIZE=150
```

说明：

- `TOP_MOS_COUNT=50`：邮件里显示更多安全边际厚的公司。
- `MAX_TICKERS=0`：扫描美股 `data/universe.csv` 里的全部股票；测试时可设为 10。
- `HK_MAX_TICKERS=0`：扫描港股 `data/hk_universe.csv` 里的全部股票；测试时可设为 10。
- `SEND_AFTER_CLOSE=false`：盘后只生成报告不发邮件；如果想盘后也发，改为 `true`。
- `DRY_RUN=false`：线上正常发邮件；本地或手动测试可设为 `true`，只生成报告不发邮件。定时盘前任务会强制按 `false` 运行，确保每天发送邮件。
- `PRICE_MATRIX_BATCH_SIZE=150`：熊市区间验证下载历史价格矩阵时每批 ticker 数量；遇到 Yahoo 不稳定时可降到 80 或 100。

## 股票池更新

GitHub Actions 里的 `MOS Radar - US - Update Universe` 会从 Nasdaq Trader 官方列表获取美国上市股票，再用 Nasdaq screener 获取价格、市值和成交量数据。V6.3.6 起不再先调用 Yahoo quote 批量接口，避免 GitHub Actions 中大量 `401 Unauthorized` 日志拖慢和干扰股票池更新。

V6.3.6 起，如果 Nasdaq screener 临时返回空结果，`MOS Radar - US - Update Universe` 会保留已有 `data/universe.csv`，不会写入空股票池，也不会因为缺少 `ticker` 列报错。运行结束后会上传 `mos-radar-universe` artifact，方便检查本次股票池文件。

建议参数：

```text
How many tickers to keep = 2000
Minimum market cap = 1000000000
Minimum liquidity volume = 100000
```

`MOS Radar - US - Update Universe` 日志会打印实际收到的 `limit / min_market_cap / min_avg_volume`、Nasdaq screener 行情行数、合并行数、过滤后数量和最终股票池数量。`limit=2000` 且过滤后数量充足时，`data/universe.csv` 应生成 2000 家公司。

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


## 港股支持 V6.5

V6.5 新增港股独立运行通道，不影响原来的美股扫描。港股使用 Yahoo Finance 的 `.HK` 代码格式，例如：

```text
0700.HK = 腾讯控股
9988.HK = 阿里巴巴-W
0005.HK = 汇丰控股
```

新增 GitHub Actions：

```text
MOS Radar - HK - Update Universe
MOS Radar - HK - Daily Scanner
MOS Radar - HK - Historical Replay
MOS Radar - US - Bear Validation
MOS Radar - HK - Bear Validation
MOS Radar - US - Bear Range Validation
MOS Radar - HK - Bear Range Validation
```

港股相关文件独立保存：

```text
data/hk_universe_seed.csv      港股初始种子池
data/hk_universe.csv           港股扫描股票池
data/hk_holdings.example.csv   港股持仓示例
data/results/hk_mos_latest.csv 港股最新扫描结果
reports/hk/latest_report.md     港股最新报告
state/hk_mos_market_latest.csv 港股公开市场状态
```

港股持仓不要提交到仓库；如果要在 GitHub Actions 里加入港股持仓，用 Repository Secret：

```text
HOLDINGS_TICKERS_HK=0700.HK,9988.HK,0005.HK
```

港股财报币种可能是 CNY/USD，报价通常是 HKD。V6.5 对港股常见的 `CNY/CNH/USD -> HKD` 自动做汇率换算；如果无法换算，会标记 `SKIP`，不会强行估值。

## GitHub Actions 运行

### 美股当前扫描

GitHub Actions → `MOS Radar - US - Daily Scanner` → `Run workflow`

这个 workflow 扫描当前美股市场，也负责美股工作日定时运行。运行结束后，在本次 run 页面下载 artifact：

```text
mos-radar-current-results
```

里面包含：

```text
data/results/mos_latest.csv
data/results/mos_snapshot_latest.csv
data/results/data_quality_diagnostics.csv
reports/latest_report.md
state/mos_market_latest.csv
```

### 美股历史价格压力测试

GitHub Actions → `MOS Radar - US - Historical Replay` → `Run workflow`：

```text
backtest_date = 2022-10-14
backtest_use_latest = false
dry_run = false
```

输出 artifact：

```text
mos-radar-historical-YYYY-MM-DD
```

### 美股熊市候选方向性验证

GitHub Actions → `MOS Radar - US - Bear Validation` → `Run workflow`：

```text
bear_dates = 2009-03-09,2020-03-23,2022-10-14
forward_windows = 365,730,1095
benchmark_ticker = SPY
cohort_ratings = S,A,B
backtest_use_latest = false
dry_run = true
```

输出 artifact：

```text
mos-radar-us-bear-validation
```

里面包含：

```text
data/results/bear_validation_summary.csv
data/results/bear_validation_candidates.csv
reports/latest_bear_validation.md
```

### 美股熊市区间方向性验证

GitHub Actions → `MOS Radar - US - Bear Range Validation` → `Run workflow`：

```text
bear_start = 2022-08-15
bear_end = 2022-10-14
sample_every_n_days = 5
max_sample_dates = 20
forward_windows = 365,730,1095
benchmark_ticker = SPY
cohort_ratings = S,A,B
backtest_use_latest = false
dry_run = true
```

输出 artifact：

```text
mos-radar-us-bear-range-validation
```

里面包含：

```text
data/results/bear_range_signals.csv
data/results/bear_range_ticker_rank.csv
data/results/bear_range_summary.csv
reports/latest_bear_range_validation.md
```

建议分多次跑不同熊市区间，例如：

```text
2008-10-01 至 2009-03-09
2020-02-20 至 2020-03-23
2022-08-15 至 2022-10-14
```

### 港股股票池更新

GitHub Actions → `MOS Radar - HK - Update Universe` → `Run workflow`

建议先用较小范围测试：

```text
limit = 120
min_market_cap = 500000000
min_liquidity_volume = 500000
source = hkex
```

输出 artifact：

```text
mos-radar-hk-universe
```

里面包含：

```text
data/hk_universe.csv
```

### 港股当前扫描

GitHub Actions → `MOS Radar - HK - Daily Scanner` → `Run workflow`

第一次测试建议在 GitHub Variables 里设置：

```text
HK_MAX_TICKERS=10
DRY_RUN=true
```

确认报告正常后再改为：

```text
HK_MAX_TICKERS=0
DRY_RUN=false
```

输出 artifact：

```text
mos-radar-hk-current-results
```

里面包含：

```text
data/results/hk_mos_latest.csv
data/results/hk_mos_snapshot_latest.csv
data/results/hk_data_quality_diagnostics.csv
reports/hk/latest_report.md
state/hk_mos_market_latest.csv
```

### 港股历史价格压力测试

GitHub Actions → `MOS Radar - HK - Historical Replay` → `Run workflow`：

```text
backtest_date = 2022-10-31
backtest_use_latest = false
dry_run = true
```

输出 artifact：

```text
mos-radar-hk-historical-YYYY-MM-DD
```

里面包含港股历史价格压力测试 CSV 和 `reports/hk/*.md` 报告。

### 港股熊市候选方向性验证

GitHub Actions → `MOS Radar - HK - Bear Validation` → `Run workflow`：

```text
bear_dates = 2016-02-12,2020-03-23,2022-10-31
forward_windows = 365,730,1095
benchmark_ticker = 2800.HK
cohort_ratings = S,A,B
backtest_use_latest = false
dry_run = true
```

输出 artifact：

```text
mos-radar-hk-bear-validation
```

里面包含：

```text
data/results/hk_bear_validation_summary.csv
data/results/hk_bear_validation_candidates.csv
reports/hk/latest_bear_validation.md
```

### 港股熊市区间方向性验证

GitHub Actions → `MOS Radar - HK - Bear Range Validation` → `Run workflow`：

```text
bear_start = 2022-08-01
bear_end = 2022-10-31
sample_every_n_days = 5
max_sample_dates = 20
forward_windows = 365,730,1095
benchmark_ticker = 2800.HK
cohort_ratings = S,A,B
backtest_use_latest = false
dry_run = true
```

输出 artifact：

```text
mos-radar-hk-bear-range-validation
```

里面包含：

```text
data/results/hk_bear_range_signals.csv
data/results/hk_bear_range_ticker_rank.csv
data/results/hk_bear_range_summary.csv
reports/hk/latest_bear_range_validation.md
```

建议分多次跑不同港股熊市区间，例如：

```text
2015-06-01 至 2016-02-12
2020-02-20 至 2020-03-23
2022-08-01 至 2022-10-31
```

### 历史压力测试说明

- 历史模式是价格压力测试，不是严格 point-in-time 财报回测。
- 系统会先按当前 MOS Radar 模型计算保守价值，再用历史日期附近收盘价重算安全边际。
- 美股输出文件保存为 `data/results/historical_replay_YYYY-MM-DD.csv`。
- 港股输出文件保存为 `data/results/hk_historical_replay_YYYY-MM-DD.csv`。
- `dry_run=false` 会发送邮件；`dry_run=true` 只生成 artifact 不发邮件。
- `backtest_use_latest=true` 会跳过重新扫描，直接用已有 latest CSV 做价格回放，速度更快但依赖旧结果。
- 当时尚未上市、改名、分拆或 Yahoo 缺少历史价的股票会显示为 `SKIP`，不会作为模型失败处理。
- 严格历史回测需要 SEC/港交所公告日期、当时可见财报和当时股本数据，免费 yfinance 不能可靠完成这一层。
- 熊市候选方向性验证同样不是严格回测；它重点看候选组合相对大盘的后续收益、超额收益和跑赢比例，用来评估模型方向是否有用。
- 熊市区间方向性验证比单日验证更接近真实操作：它看同一只股票在熊市区间是否反复出现安全边际信号，以及这些高频信号股票后续是否跑赢大盘。

## 重要提醒

1. V6.6.1 使用 yfinance，适合个人研究原型，不适合机构级数据可靠性。
2. 价值股、周期股、半导体股必须人工复核最新财报和行业周期。
3. REIT/地产类公司 V6 默认跳过，因为需要 AFFO/NOI 专门模型。
4. 本项目不会自动下单，报告不构成投资建议。
