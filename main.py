#!/usr/bin/env python3
"""加密 15m 涨跌预测入口"""

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
from pm_kronos.polymarket import get_btc_15m_market, has_position_in_event, place_15m_order


def main() -> None:
    parser = argparse.ArgumentParser(description="加密 15m 涨跌预测")
    parser.add_argument(
        "--symbol",
        default=os.environ.get("PM_KRONOS_SYMBOL", "BTCUSDT"),
        help="交易对，如 BTCUSDT（可通过环境变量 PM_KRONOS_SYMBOL 设置）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=96,
        help="K 线根数（默认 96）",
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
        default=1.0,
        help="Polymarket 下单金额 USD（默认 1）",
    )
    parser.add_argument(
        "--no-trade",
        action="store_true",
        help="仅模拟，不实际下单（覆盖 TRADE_ENABLED）",
    )
    args = parser.parse_args()

    print(f"正在获取 {args.symbol} 最近 {args.limit} 根 15m K 线...")
    klines = fetch_klines(symbol=args.symbol, interval="15m", limit=args.limit)

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

    print(f"15M 预测：{direction}")

    # Polymarket 下单逻辑
    trade_enabled = os.environ.get("TRADE_ENABLED", "0") == "1" and not args.no_trade
    if not trade_enabled:
        side = "UP" if direction == "涨" else "DOWN"
        print(f"[模拟] {direction} -> 买 {side}（{args.amount} USD）")
        return

    market = get_btc_15m_market(next_ts)
    if not market:
        print("对应 15M 市场未开放，无法下单")
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
        resp = place_15m_order(
            token_id=token_id,
            amount_usd=args.amount,
            private_key=private_key,
            proxy=proxy,
            signature_type=signature_type,
        )
        if resp.get("success"):
            print(f"已下单：{direction} -> 买 {side}（{args.amount} USD）")
        else:
            print(f"下单失败：{resp.get('errorMsg', resp)}")
    except Exception as e:
        print(f"下单异常：{e}")


if __name__ == "__main__":
    main()
