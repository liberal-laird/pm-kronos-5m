#!/usr/bin/env python3
"""加密 5m 涨跌预测入口"""

import argparse
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

# 确保 src 在路径中，便于导入 pm_kronos
_project_root = Path(__file__).resolve().parent
_src_path = _project_root / "src"
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

load_dotenv(_project_root / ".env")

from pm_kronos.binance import fetch_klines
from pm_kronos.transform import binance_klines_to_kronos_df, get_next_candle_timestamp
from pm_kronos.predictor import predict_direction
from pm_kronos.polymarket import get_btc_5m_market, get_usdc_balance, has_position_in_event, place_5m_order


def _resolve_trade_amount(args_amount: float | None) -> tuple[float, str | None]:
    """
    解析下单金额：--amount 优先，否则 TRADE_AMOUNT；auto 表示账户余额的 10%。
    Returns:
        (amount, info_msg): info_msg 在 auto 模式时有值，用于打印
    """
    if args_amount is not None:
        return args_amount, None
    env_val = os.environ.get("TRADE_AMOUNT", "auto").strip().lower()
    if env_val in ("", "auto"):
        proxy = os.environ.get("POLYMARKET_PROXY", "").strip()
        if not proxy:
            return 1.0, None
        balance = get_usdc_balance(proxy)
        amount = max(0.01, round(balance * 0.1, 2))
        return amount, f"账户余额 {balance:.2f} USDC，下单 10% = {amount:.2f} USD"
    return float(env_val), None


def main() -> None:
    parser = argparse.ArgumentParser(description="加密 5m 涨跌预测")
    parser.add_argument(
        "--symbol",
        default=os.environ.get("PM_KRONOS_SYMBOL", "BTCUSDT"),
        help="交易对，如 BTCUSDT（可通过环境变量 PM_KRONOS_SYMBOL 设置）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=48,
        help="K 线根数（默认 48）",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="随机种子，用于可复现预测（默认 42）",
    )
    parser.add_argument(
        "--amount",
        type=float,
        default=None,
        help="Polymarket 下单金额 USD；不传则按 TRADE_AMOUNT（auto=账户余额10%%）",
    )
    parser.add_argument(
        "--no-trade",
        action="store_true",
        help="仅模拟，不实际下单（覆盖 TRADE_ENABLED）",
    )
    args = parser.parse_args()
    amount, amount_info = _resolve_trade_amount(args.amount)
    if amount_info:
        print(amount_info)

    print(f"正在获取 {args.symbol} 最近 {args.limit} 根 5m K 线...")
    klines = fetch_klines(symbol=args.symbol, interval="5m", limit=args.limit)

    print("正在转换为 Kronos 格式...")
    df, timestamps = binance_klines_to_kronos_df(klines)

    x_df = df
    x_timestamp = timestamps
    last_close_time_ms = int(klines[-1][6])
    next_ts = get_next_candle_timestamp(last_close_time_ms)
    y_timestamp = pd.Series([next_ts])

    print("正在加载 Kronos 模型并预测...")
    direction = predict_direction(
        df=x_df, x_timestamp=x_timestamp, y_timestamp=y_timestamp, seed=args.seed
    )

    print(f"5M 预测：{direction}")

    # Polymarket 下单逻辑
    trade_enabled = os.environ.get("TRADE_ENABLED", "0") == "1" and not args.no_trade
    if not trade_enabled:
        side = "UP" if direction == "涨" else "DOWN"
        print(f"[模拟] {direction} -> 买 {side}（{amount:.2f} USD）")
        return

    market = get_btc_5m_market(next_ts)
    if not market:
        print("对应 5M 市场未开放，无法下单")
        return

    private_key = os.environ.get("PRIVATE_KEY", "").strip()
    proxy = os.environ.get("POLYMARKET_PROXY", "").strip()
    if not private_key or not proxy:
        print("请配置 .env 中的 PRIVATE_KEY 与 POLYMARKET_PROXY")
        return

    if has_position_in_event(proxy, market.get("event_id", "")):
        print("该事件已有仓位，跳过下单")
        return

    token_id = market["up_token_id"] if direction == "涨" else market["down_token_id"]
    side = "UP" if direction == "涨" else "DOWN"

    signature_type = int(os.environ.get("SIGNATURE_TYPE", "2"))

    try:
        resp = place_5m_order(
            token_id=token_id,
            amount_usd=amount,
            private_key=private_key,
            proxy=proxy,
            signature_type=signature_type,
        )
        if resp.get("success"):
            print(f"已下单：{direction} -> 买 {side}（{amount:.2f} USD）")
        else:
            print(f"下单失败：{resp.get('errorMsg', resp)}")
    except Exception as e:
        print(f"下单异常：{e}")


if __name__ == "__main__":
    main()
