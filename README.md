# Binance USDⓈ-M 合约量化交易系统（测试网）

一套可运行的 Binance USDⓈ-M 永续合约量化交易系统，内置 **三种趋势跟踪策略**（VGAS / 海龟 / EMA），
配合**交易所原生止损止盈**、**仓位风控**、**回撤/日亏熔断**与**交易时段控制**，跑通
「数据 → 信号 → 下单 → 挂保护单 → 持仓跟踪/移动止损」全链路。

> ⚠️ **重要声明：没有任何系统能"确保收益"。** 交易永远有风险，历史回测不代表未来表现。
> 本系统能做到的是**严格执行纪律**：每笔单固定风险、进场即挂交易所原生止损止盈、回撤/日亏熔断。
> 策略本身是否盈利取决于市场环境。

## 特性

- **多策略可切换**：VGAS（默认，波动率自适应趋势突破）、Turtle（海龟突破 + 金字塔加仓）、EMA（均线交叉）
- **多币种并行**：默认自动选取前 40 个流动性主流合约，独立信号、独立开平仓、独立挂保护单
- **交易所原生保护单**：进场即挂 `STOP_MARKET` 止损 + `TAKE_PROFIT_MARKET` 止盈（`closePosition=true`），程序宕机/断网也生效
- **移动止损**：随价格新高/新低按 `峰值 - 2×ATR` 只进不退地抬升止损
- **严格风控**：单笔风险上限、名义价值上限、同时持仓上限、回撤熔断、日亏熔断
- **交易时段**：默认北京时间 09:00 → 次日 01:00 内开新仓/反手，时段外只监控不新开
- **状态持久化**：持仓/成交/权益曲线落盘，重启后熔断器状态不丢失
- **H5 可视化面板**：手机可看账户、持仓、权益曲线、系统状态
- **命令行工具**：`python main.py` 一键查询行情/账户/下单/撤单

## 系统架构

```
demo测试系统/
├── config.py               # 配置中心（密钥 + 策略/风控参数，环境变量可覆盖）
├── main.py                 # 命令行工具（行情/账户/下单/撤单）
├── bot.py                  # 实盘入口
├── backtest.py             # 回测入口（vgas / turtle / ema）
├── optimize.py             # 参数扫描（训练/测试分段防过拟合）
├── repair_tp.py            # 补挂止盈止损修复脚本
├── _protect.py             # 一次性补挂止损（旧仓修复用）
├── _vgas_check.py          # VGAS 信号诊断
├── binance_futures/        # REST 客户端
│   ├── client.py           # 行情/账户/订单/数据流封装
│   ├── utils.py            # tickSize/stepSize 精度换算
│   └── exceptions.py
├── quant/                  # 量化引擎
│   ├── indicators.py       # EMA / ATR / RSI / 布林带
│   ├── strategy.py         # VGASStrategy / TurtleStrategy / EMACrossoverStrategy
│   ├── risk.py             # 仓位计算 + 熔断器
│   ├── state.py            # 持仓/成交/权益历史持久化
│   ├── session.py          # 交易时段判断
│   ├── engine.py           # 实盘循环（多币种 + 保护单 + 移动止损 + 加仓）
│   └── backtest.py         # 回测 + 绩效指标
├── dashboard/              # H5 可视化面板
│   ├── server.py
│   └── index.html
├── state/trading_state.json# 运行状态（自动生成，已 gitignore）
├── logs/bot.log            # 运行日志（自动生成，已 gitignore）
├── 启动.bat                # 一键启动 bot + 面板（Windows）
└── 放行8080端口.bat         # 防火墙放行面板端口（Windows）
```

## 策略

`BOT_STRATEGY` 选择策略，默认 `vgas`。三者共用 ATR 止损/止盈与同一套风控。

### 1. VGAS（默认）— 波动率自适应趋势突破

双 Donchian 通道突破叠加多重过滤器，只在"趋势 + 动量 + 波动率"都到位时入场：

- **突破触发**：收盘价突破前 `BOT_ENTRY_PERIOD`(20) 根 K 线最高价 → 做多；跌破最低价 → 做空
- **趋势过滤**：收盘价在慢速 EMA(`BOT_TREND_PERIOD`=55) 上方才做多、下方才做空
- **波动率过滤**：`ATR / 收盘价 ≥ BOT_MIN_ATR_PCT`(0.1%)，避免死水/震荡行情
- **布林带过滤**：做多要求收盘价在中轨与上轨之间，做空要求在下轨与中轨之间（不过度延伸）
- **RSI 过滤**：做多 RSI∈[50,80]，做空 RSI∈[20,50]，避开超买/超卖
- **离场**：反向快速通道(`BOT_EXIT_PERIOD`=10)突破 或 2×ATR 止损
- **加仓**：每 0.5×ATR 顺势加一仓，最多 `BOT_MAX_UNITS`(3) 仓

### 2. Turtle（海龟）— 突破 + 金字塔加仓

- 进场：收盘价突破前 `BOT_ENTRY_PERIOD` 根最高/最低价
- 离场：反向突破前 `BOT_EXIT_PERIOD` 根通道
- 止损：`2 × ATR`（`BOT_STOP_ATR_MULT`）
- 加仓：每 `0.5 × ATR` 加一仓，最多 `BOT_MAX_UNITS` 仓

### 3. EMA — 均线交叉 + 趋势过滤

- 金叉（EMA9 上穿 EMA55）且价格在长趋势线(`BOT_TREND_FILTER_PERIOD`=100)上方 → 做多
- 死叉（EMA9 下穿 EMA55）且价格在长趋势线下方 → 做空
- 反向交叉 → 平仓并反手
- 止损 = 入场价 ± ATR × `BOT_ATR_MULTIPLIER`；止盈 = 止损距离 × `BOT_RISK_REWARD`

## 严格风控（核心）

| 控制项 | 说明 | 默认 |
| --- | --- | --- |
| 单笔风险 | 每笔最多亏账户权益的 **1.5%**（仓位 = 风险额 ÷ 止损距离） | `BOT_RISK_PER_TRADE=0.015` |
| 交易所原生止损 | 进场立即挂 `STOP_MARKET`（`closePosition`），**程序宕机也生效** | — |
| 交易所原生止盈 | 进场立即挂 `TAKE_PROFIT_MARKET`（`closePosition`） | — |
| 移动止损 | 峰值 ± 2×ATR，只向有利方向移动 | `BOT_STOP_ATR_MULT=2.0` |
| 仓位上限 | 名义价值 ≤ 权益 × 50% × 杠杆 | `BOT_MAX_POSITION_PCT=0.5` |
| 同时持仓上限 | 多币种总持仓数上限 | `BOT_MAX_OPEN_POSITIONS=10` |
| 最大回撤熔断 | 权益回撤 ≥ 15% → 停止交易 | `BOT_MAX_DRAWDOWN_PCT=0.15` |
| 每日亏损熔断 | 当日亏损 ≥ 3% → 停止交易 | `BOT_DAILY_LOSS_LIMIT_PCT=0.03` |
| 交易时段 | 只在时段内开新仓/反手 | `09:00~01:00 UTC+8` |

止损止盈是**挂在交易所上的真实订单**（algo 订单，`workingType=MARK_PRICE`），不是本地判断，
所以即使程序崩溃、断网，仓位仍受保护。熔断器状态持久化到 `state/`，重启不会重置限额。

## 快速开始

```powershell
# 1) 依赖
pip install -r requirements.txt

# 2) 配置密钥：复制 .env.example 为 .env，填入测试网密钥
#    （测试网密钥在 https://testnet.binancefuture.com 生成）

# 3) 回测（先验证逻辑，默认 VGAS）
python backtest.py                          # BTCUSDT 15m 默认 VGAS 参数
python backtest.py --strategy turtle --interval 1h --bars 4000
python backtest.py --strategy ema --symbol ETHUSDT --interval 4h

# 4) 实盘跑测试网
python bot.py --once                        # 跑一个周期（检查连通/信号）
python bot.py --dry-run                     # 只模拟决策，不下单（安全演练）
python bot.py                               # 持续运行，每 BOT_POLL_SECONDS 秒一轮
python bot.py --symbol ETHUSDT --interval 15m   # 单币种覆盖
python bot.py --no-session                  # 临时忽略交易时段（测试用）
```

## 配置（`.env` / 环境变量）

密钥只放 `.env`（已 gitignore），策略/风控参数都可用 `BOT_` 前缀环境变量覆盖。

### 凭证与连接

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `BINANCE_TESTNET_API_KEY` / `_SECRET` | — | 测试网密钥 |
| `BINANCE_TESTNET` | `true` | 测试网/主网切换（主网为真实资金） |
| `BINANCE_RECV_WINDOW` | `5000` | 签名请求时间窗口（毫秒） |
| `BINANCE_REQUEST_TIMEOUT` | `10` | HTTP 超时（秒） |

### 市场与引擎

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `BOT_SYMBOLS` | 内置 50 个主流合约 | 交易对列表（逗号分隔） |
| `BOT_SYMBOLS_AUTO` | `true` | 自动选取前 N 个流动性合约 |
| `BOT_MAX_SYMBOLS` | `40` | 自动选取的监控数量 |
| `BOT_INTERVAL` | `15m` | K 线周期 |
| `BOT_KLINE_LOOKBACK` | `400` | 每次拉取的 K 线数量 |
| `BOT_POLL_SECONDS` | `60` | 轮询间隔 |
| `BOT_STRATEGY` | `vgas` | 策略：`vgas` / `turtle` / `ema` |
| `BOT_WORKING_TYPE` | `MARK_PRICE` | 保护单触发价类型 |

### 策略参数（按策略分组）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `BOT_ATR_PERIOD` | `20` | ATR 周期 |
| `BOT_RISK_REWARD` | `2.0` | 止盈/止损比值 |
| `BOT_ENTRY_PERIOD` | `15` | VGAS/Turtle 进场突破周期 |
| `BOT_EXIT_PERIOD` | `10` | VGAS/Turtle 离场突破周期 |
| `BOT_STOP_ATR_MULT` | `2.0` | 止损 = ATR × 该值 |
| `BOT_ADD_ATR_MULT` | `0.5` | 每 0.5×ATR 加一仓 |
| `BOT_MAX_UNITS` | `3` | 最大加仓单元数 |
| `BOT_TREND_PERIOD` | `55` | VGAS 趋势 EMA |
| `BOT_BB_PERIOD` / `BOT_BB_STD` | `20` / `2.0` | 布林带 |
| `BOT_RSI_PERIOD` | `14` | RSI 周期 |
| `BOT_RSI_LONG_MIN` / `_MAX` | `50` / `80` | 做多 RSI 区间 |
| `BOT_RSI_SHORT_MAX` / `_MIN` | `50` / `20` | 做空 RSI 区间 |
| `BOT_MIN_ATR_PCT` | `0.001` | 最低波动率过滤 |
| `BOT_EMA_FAST` / `_SLOW` | `9` / `55` | EMA 策略快/慢线 |
| `BOT_TREND_FILTER_PERIOD` | `100` | EMA 策略趋势过滤（0=关） |
| `BOT_ATR_MULTIPLIER` | `2.0` | EMA 策略止损距离倍数 |

### 风控与时段

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `BOT_RISK_PER_TRADE` | `0.015` | 单笔风险比例（1.5%） |
| `BOT_MAX_LEVERAGE` | `5` | 杠杆 |
| `BOT_MAX_POSITION_PCT` | `0.5` | 名义价值上限系数 |
| `BOT_MAX_OPEN_POSITIONS` | `10` | 同时持仓上限 |
| `BOT_MAX_DRAWDOWN_PCT` | `0.15` | 回撤熔断阈值 |
| `BOT_DAILY_LOSS_LIMIT_PCT` | `0.03` | 日亏熔断阈值 |
| `BOT_DRY_RUN` | `false` | 模拟模式 |
| `BOT_SESSION_ENABLED` | `true` | 启用交易时段 |
| `BOT_SESSION_START` | `09:00` | 时段开始（本地时间） |
| `BOT_SESSION_END` | `01:00` | 时段结束（次日凌晨，跨午夜） |
| `BOT_SESSION_UTC_OFFSET` | `8` | 时区偏移（+8 = 北京时间） |

## 命令行工具（`main.py`）

无需跑 bot 即可手动查询/操作账户：

```powershell
python main.py status                       # 连通性 + 账户 + 持仓概览
python main.py price BTCUSDT                # 最新价格
python main.py klines BTCUSDT --interval 15m --limit 10
python main.py depth BTCUSDT --limit 5      # 盘口
python main.py position BTCUSDT             # 持仓风险
python main.py leverage BTCUSDT 5           # 设置杠杆
python main.py order --symbol BTCUSDT --side BUY --type MARKET --qty 0.001
python main.py order --symbol BTCUSDT --side BUY --type LIMIT --qty 0.001 --price 50000 --round
python main.py cancel BTCUSDT --order-id 123456
python main.py cancel-all BTCUSDT
python main.py orders BTCUSDT               # 未成交订单
python main.py history BTCUSDT              # 订单历史
```

## 回测（`backtest.py`）

```powershell
python backtest.py                                   # 默认 vgas, BTCUSDT 15m
python backtest.py --strategy turtle --interval 1h
python backtest.py --strategy ema --symbol ETHUSDT --interval 4h --bars 4000
python backtest.py --strategy vgas --entry-period 20 --trend-period 55
```

输出：总收益、交易数、胜率、盈亏比、最大回撤、年化夏普、毛利/毛损，以及最近 10 笔明细。

## 参数扫描（`optimize.py`）

对 EMA 策略做**训练/测试分段扫描**（前 `--split` 调参、后段样本外验证），
用样本内/样本外收益差距直观暴露过拟合：

```powershell
python optimize.py                                   # BTCUSDT, 15m/1h/4h
python optimize.py --intervals 1h,4h --bars 5000
python optimize.py --ema-fast 5,9,13 --trend-filter 0,100,200
```

> 注意：测试集只在排名时被保留，一旦你从测试集里挑选参数，它就不再"干净"。
> 最终结论需要一个从未触碰的第三段数据。

## 可视化面板（H5，手机可看）

内置轻量 H5 面板（`dashboard/`），展示：账户权益、可用余额、未实现盈亏、权益曲线、
实时持仓、最近成交、系统状态（时段/熔断/持仓数）。

```powershell
python dashboard/server.py     # 监听 0.0.0.0:8080（所有网卡）
```

手机通过 Tailscale 私网访问：`http://<你的Tailscale IP>:8080`（面板每 10 秒刷新）。

> ⚠ 面板**没有鉴权**，只应通过 Tailscale 私网访问，切勿暴露到公网。

## 辅助脚本

| 脚本 | 用途 |
| --- | --- |
| `repair_tp.py` | 为每个持仓补挂止损/止盈 algo 单，并同步写入状态文件 |
| `_protect.py` | 一次性为所有持仓补挂 2×ATR 止损（修复用） |
| `_vgas_check.py` | 诊断所有监控币种的 VGAS 信号，打印可开仓数 |

## Windows 一键启动

```powershell
启动.bat             # 同时打开「交易机器人」和「可视化面板」两个窗口
放行8080端口.bat      # 以管理员身份运行，放行面板端口供手机访问
```

## 安全提示

1. 密钥只放 `.env`（已 gitignore）。测试网密钥一旦泄露，请到
   [测试网后台](https://testnet.binancefuture.com) 重新生成。
2. 主网是真实资金。切主网（`BINANCE_TESTNET=false`）前务必先小资金验证，且测试网/主网密钥不通用。
3. `logs/` 与 `state/` 是运行时数据（含持仓信息），已 gitignore，切勿手动提交。

## 下一步

```powershell
python bot.py --dry-run            # 先用新配置模拟运行，观察信号
python bot.py                      # 测试网实盘运行（09:00~01:00 内自动交易）
python dashboard/server.py         # 可视化面板
python optimize.py                 # 参数扫描（如需单独调优）
```
