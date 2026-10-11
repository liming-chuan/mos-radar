# V6.11.3：先让机会完整、及时、可验证

10月5–9日10封日报均收到，但港股4天开盘后才送达，美股10月8日也明显迟到；本周五只可执行候选均未模拟成交。13次短线定时研究失败，其中10次港股基准未就绪，另3次美股行情覆盖不足；3次完整估值任务约65分钟后取消。修复运行问题比凭少量结果调松策略更有依据。

## 参考成熟系统

- [QuantConnect/LEAN数据预热](https://www.quantconnect.com/docs/v2/writing-algorithms/historical-data/warm-up-periods)：历史数据就绪与可交易状态分开。
- [QuantConnect定时事件](https://www.quantconnect.com/docs/v2/writing-algorithms/scheduled-events)：市场日历、事件时间和数据实际到达需要区别处理。
- [Freqtrade未来函数检查](https://www.freqtrade.io/en/stable/lookahead-analysis/)：可疑的漂亮回测需要切片对照；没有证据不能声称策略更赚钱。
- [Backtrader滑点说明](https://www.backtrader.com/docu/slippage/slippage/)：模拟成交依赖价格和成本假设，必须解释执行边界。

借鉴这些工程和验证原则，没有移植其他系统的买卖策略，没有把加密货币实盘结论套用到美股/港股。

## 缓存与估值续跑

空字符串配置使用默认值，`USE_FUNDAMENTALS_CACHE=false`仍能明确关闭缓存。缓存保留估值模型版本、港股公告指纹和7天有效期，原子写入。

完整估值默认每波最多500次新分析；缓存命中不计入这个上限。`VALUE_SCAN_BUDGET_SECONDS=2700`为45分钟软预算，预留10分钟输出，超过35分钟不再开始新的单股分析。已有单股请求不会被强杀；网络和后处理仍可能超出预算，这不是65分钟内完成的硬保证。

`cache/fundamentals/{market}/_scan_progress.json`保存已完成工作，后续每日/手动任务自动续跑。股票范围、市场、模型、公告指纹、缓存有效期改变时重置，损坏进度不沿用。续跑最终完成时重新更新全池价格和趋势。部分结果标`PARTIAL_BUDGET`或`PARTIAL_SOURCE_FAILURE`，不替换上次完整公开估值状态；完成前财报池仍可能较旧。运行摘要明确显示进度，不能把部分扫描称为全池刷新。

工作流显式restore/save，部分任务正常结束或失败时也尝试保存缓存；强制取消/运行器故障仍可能丢失该波新增缓存。当前报价分50只请求，5分钟后不再开始新批次，缺失价格保留原缺失/旧价标记。

## 关键行情恢复

- 基准与已有模拟记录优先请求；主下载校验不通过时，各尝试一次`Ticker.history`，明确`repair=False`、`auto_adjust=False`及10秒请求超时。
- 两条请求路径仍来自Yahoo，不是独立数据源。备用结果同样需要完整OHLCV、复权/公司行动、60交易日连续性与价格合法性验证，禁止由高低价推算收盘价。
- 验证通过的60日日线存到`cache/prices/{market}`，故障时保留已有缓存；只有同一最近完整交易日、且观察时间不晚于本轮，才可复用。陈旧缓存不能用作当前计划。
- `coverage.price_valid/price_failed`描述个股行情校验；`benchmark_ready`描述基准；原`data_failed`仍是计划证据失败数量。恒指缺失不再被解释成所有个股行情都缺失。
- 新增港股17:13UTC（次日香港01:13）、美股03:47UTC恢复波次；原日历、故障门槛与每市场/交易日一次投递回执继续生效。强制手动短线扫描不发邮件。

[GitHub说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)确认schedule可能延迟或丢弃。增加恢复和缓存有助于提前准备，不能保证盘前送达；若仍持续迟到，需要另行部署可用的独立定时服务。当前没有新购服务器或接入券商。

## 机会和未成交解释

新过期记录分别写入`NOT_TRIGGERED`、`ABOVE_ENTRY_LIMIT`、`OPEN_BELOW_STOP`、`LATE_PLAN`，并保存当天开盘、最高价、触发价、上限及含滑点的估计成交价。只增加诊断，没有改变入场公式、有效期或追价上限。旧账本的合并原因保留“未拆分”，不编造历史。

报告分开显示待触发与延迟/过期展示，历史模拟列出最近平仓日、独立入场组数，两个退出政策不能当两笔独立证据。价值标签仍须读清：基本面待复核不等于厚安全边际。账户资料缺失时仍不输出实际股数。

## 验证与尚待观察

离线回归包括空配置/显式false、分批与时间预算、续跑、缓存范围和期限、备用数据严格校验、陈旧缓存拒绝、原因区分及不重复统计。运行命令：`python -m unittest discover -s tests -v`。

2026-10-11本机公开行情小样本：^HSI、0700.HK、^GSPC、GME、SNOW均通过主路径校验。另用测试桩让主下载返回空表，实际调用恒指history备用接口成功并通过校验。这不能证明开盘前或GitHub共享IP的数据一定可用；备用结果缺失时的拒绝行为由离线测试覆盖，云端验证另看Actions结果。

下周观察盘前收到比例、数据失败原因、估值续跑进度、实际模拟触发与未成交原因。稳定积累前瞻样本后再决定是否建立新的策略对照，不能承诺收益提高或约20%账户回撤上限。
