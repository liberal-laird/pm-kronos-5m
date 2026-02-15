"""从币安获取 K 线数据"""

import httpx

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"


def fetch_klines(
    symbol: str = "BTCUSDT",
    interval: str = "15m",
    limit: int = 96,
) -> list:
    """
    从币安获取 K 线数据。

    Args:
        symbol: 交易对，如 BTCUSDT
        interval: K 线周期，如 15m
        limit: 获取的 K 线根数，默认 96

    Returns:
        原始 K 线数组列表，每根格式为 [Open time, Open, High, Low, Close, Volume, ...]

    Raises:
        httpx.HTTPError: 网络或 API 错误
        ValueError: API 返回错误响应
    """
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    }
    response = httpx.get(BINANCE_KLINES_URL, params=params, timeout=30.0)
    response.raise_for_status()
    data = response.json()

    if not isinstance(data, list):
        raise ValueError(f"Unexpected API response: {data}")

    if len(data) < limit:
        raise ValueError(
            f"Expected at least {limit} klines, got {len(data)}. "
            "Symbol may be invalid or data unavailable."
        )

    return data
