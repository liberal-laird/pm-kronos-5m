# pm-kronos

加密 15 分钟涨跌预测：从币安获取 96 根 15m K 线，使用 [Kronos](https://github.com/shiyu-coder/Kronos) 模型预测下一根 K 线的涨跌方向，并可按预测在 [Polymarket](https://polymarket.com/crypto/15M) 对应的 15M Up/Down 市场下单（涨买 UP，跌买 DOWN）。

## 安装

### 1. 克隆 Kronos

Kronos 需从 GitHub 克隆到 `lib/kronos`：

```bash
git clone https://github.com/shiyu-coder/Kronos.git lib/kronos
```

若使用 Git Submodule：

```bash
git submodule add https://github.com/shiyu-coder/Kronos.git lib/kronos
git submodule update --init --recursive
```

### 2. 安装依赖

```bash
pip install -e .
pip install -r lib/kronos/requirements.txt
```

## 运行

### 单次运行

```bash
python main.py
```

默认预测 **BTCUSDT** 下一根 15m K 线的涨跌。

### 定时运行（每 15 分钟整点）

在每小时的 **0、15、30、45 分钟（UTC）** 自动执行一次：

```bash
python run_cron.py
```

可附带 `main.py` 的参数，如 `python run_cron.py --amount 5`。

### 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--symbol` | `BTCUSDT` | 交易对 |
| `--limit` | `96` | K 线根数 |
| `--seed` | `42` | 随机种子（可复现预测） |
| `--amount` | `1` | Polymarket 下单金额（USD） |
| `--no-trade` | - | 仅模拟，不实际下单 |

示例：

```bash
python main.py --symbol ETHUSDT
python main.py --no-trade              # 只预测，不下单
python main.py --amount 5              # 下单 5 USD
```

### 环境变量

- `PM_KRONOS_SYMBOL`：默认交易对（可替代 `--symbol`）
- `TRADE_ENABLED`：`1` 时根据预测在 Polymarket 下单，否则仅模拟
- `PRIVATE_KEY`：钱包私钥（Polymarket 导出：reveal.magic.link/polymarket）
- `POLYMARKET_PROXY`：Polymarket 充币地址（Profile 页可查）
- `SIGNATURE_TYPE`：下单签名类型，`2` 为浏览器钱包（MetaMask 等）
- `CLAIM_SIGNATURE_TYPE`：Claim 签名类型，`1`=pre-validated（默认，推荐），`2`=ECDSA
- `HF_TOKEN`：Hugging Face API Token（可选，可提升模型下载速率与限流阈值，见 https://huggingface.co/settings/tokens）

### Polymarket 下单说明

当 `TRADE_ENABLED=1` 且未使用 `--no-trade` 时，预测完成后会：

1. 获取与下一 15 分钟时间窗口对应的 Polymarket BTC Up/Down 市场
2. 涨 → 买 UP token，跌 → 买 DOWN token
3. 使用 FOK 市价单，金额由 `--amount` 指定（默认 1 USD）

部分市场的 `orderMinSize` 为 5，若 1 USD 被拒，可尝试 `--amount 5`。

### 自动 Claim

市场结算后，可兑换仓位需手动 claim。运行：

```bash
python claim_positions.py
```

会获取 `POLYMARKET_PROXY` 下所有可 redeem 仓位，并按 condition 逐个执行 claim。

**两种方式**：

1. **MetaMask / Safe 直接执行**（默认）：无需 Builder 凭证，需配置 `PRIVATE_KEY`、`POLYMARKET_PROXY`，以及可选 `RPC_URL`（默认 `https://polygon-rpc.com`）、`CLAIM_SIGNATURE_TYPE=1`（pre-validated）。私钥对应的 EOA 需为 Safe 的 owner，claim 时支付 Polygon gas。
2. **Relayer 免 gas**：配置 `POLY_BUILDER_API_KEY`、`POLY_BUILDER_SECRET`、`POLY_BUILDER_PASSPHRASE` 后自动改用 Relayer（申请：https://polymarket.com/builder）。

## 流程说明

1. 从币安公开 API 获取 96 根 15m K 线
2. 转换为 Kronos 所需的 DataFrame 格式（OHLCV + timestamps）
3. 加载 Kronos-small 模型，预测下一根 K 线的 OHLC
4. 根据预测收盘价与当前收盘价比较，输出「涨」或「跌」
5. 若开启交易，在 Polymarket 对应市场下单（涨买 UP，跌买 DOWN）
