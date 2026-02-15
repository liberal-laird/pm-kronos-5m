"""Binance K 线格式转换为 Kronos 所需格式"""

import pandas as pd

# Binance kline: [Open time, Open, High, Low, Close, Volume, Close time, ...]
OPEN_TIME_IDX = 0
OPEN_IDX = 1
HIGH_IDX = 2
LOW_IDX = 3
CLOSE_IDX = 4
VOLUME_IDX = 5
CLOSE_TIME_IDX = 6

# 15 分钟 = 15 * 60 * 1000 毫秒
MS_PER_15M = 15 * 60 * 1000


def binance_klines_to_kronos_df(klines: list) -> tuple[pd.DataFrame, pd.Series]:
    """
    将币安 K 线数据转换为 Kronos 所需的 DataFrame 格式。

    Args:
        klines: 币安 API 返回的原始 K 线数组列表

    Returns:
        (df, timestamps):
        - df: 含 timestamps, open, high, low, close, volume 列的 DataFrame
        - timestamps: 对应的时间序列 Series
    """
    records = []
    for kline in klines:
        open_time_ms = int(kline[OPEN_TIME_IDX])
        records.append(
            {
                "timestamps": pd.Timestamp(open_time_ms, unit="ms", tz="UTC"),
                "open": float(kline[OPEN_IDX]),
                "high": float(kline[HIGH_IDX]),
                "low": float(kline[LOW_IDX]),
                "close": float(kline[CLOSE_IDX]),
                "volume": float(kline[VOLUME_IDX]),
            }
        )

    df = pd.DataFrame(records)
    timestamps = df["timestamps"]
    return df, timestamps


def get_next_candle_timestamp(last_close_time_ms: int) -> pd.Timestamp:
    """
    根据最后一根 K 线的收盘时间计算下一根 K 线的开盘时间。

    币安 close time 为当前 K 线最后一毫秒，下一根 open = close + 1ms，
    故下一根开盘时间约等于 close time（秒级对齐）。

    Args:
        last_close_time_ms: 最后一根 K 线的 Close time (毫秒)

    Returns:
        下一根 15m K 线的开盘时间 (UTC)
    """
    # 下一根 K 线开盘 = 上一根收盘 + 1ms（Binance 约定）
    next_open_ms = last_close_time_ms + 1
    return pd.Timestamp(next_open_ms, unit="ms", tz="UTC")
