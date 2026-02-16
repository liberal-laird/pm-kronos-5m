"""Polymarket 5M 市场获取与下单"""

import json
import os
import time
from typing import Any

import httpx

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
DATA_POSITIONS_URL = "https://data-api.polymarket.com/positions"
USDC_POLYGON = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
USDC_DECIMALS = 6


def _fetch_market_by_slug(slug: str) -> dict | None:
    """内部：按 slug 请求并解析市场，成功返回 event dict，失败返回 None。"""
    try:
        resp = httpx.get(
            GAMMA_EVENTS_URL,
            params={"slug": slug},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, httpx.HTTPStatusError):
        return None

    if not data or not isinstance(data, list):
        return None

    return data[0]


def get_btc_5m_market(next_open_ts=None) -> dict[str, str] | None:
    """
    获取当前活动的 Polymarket BTC 5M Up/Down 市场信息。

    使用下一根 5m 开盘时间或当前时间向下对齐到 5 分钟边界，
    通过 /events?slug= 查询活动市场。

    Args:
        next_open_ts: 可选，下一根 5m K 线的开盘时间；未传则用当前时间对齐

    Returns:
        {"up_token_id": "...", "down_token_id": "...", "event_id": "..."} 或 None
    """
    ts_now = int(time.time() // 300 * 300)  # 300 = 5 * 60 秒
    slugs_to_try = [f"btc-updown-5m-{ts_now}"]
    if next_open_ts is not None:
        ts_next = int(next_open_ts.timestamp())
        if ts_next != ts_now:
            slugs_to_try.insert(0, f"btc-updown-5m-{ts_next}")

    for slug in slugs_to_try:
        event = _fetch_market_by_slug(slug)
        if not event:
            continue
        markets = event.get("markets") or []
        if not markets:
            continue

        market = markets[0]
        if not market.get("acceptingOrders", True):
            continue

        raw_ids = market.get("clobTokenIds")
        if not raw_ids:
            continue

        if isinstance(raw_ids, str):
            token_ids = json.loads(raw_ids)
        else:
            token_ids = raw_ids

        if len(token_ids) < 2:
            continue

        return {
            "up_token_id": token_ids[0],
            "down_token_id": token_ids[1],
            "event_id": str(event.get("id", "")),
        }

    return None


def get_usdc_balance(proxy: str, rpc_url: str | None = None) -> float:
    """
    获取 Polymarket 充币地址（proxy）在 Polygon 上的 USDC 余额。

    Args:
        proxy: 充币地址（proxy 或 EOA）
        rpc_url: Polygon RPC URL，默认使用 RPC_URL 环境变量或 polygon-rpc.com

    Returns:
        余额（USD），失败返回 0.0
    """
    from web3 import Web3

    url = rpc_url or os.environ.get("RPC_URL", "https://polygon-rpc.com").strip()
    try:
        w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 15}))
        if not w3.is_connected():
            return 0.0
        # ERC20 balanceOf
        usdc = w3.eth.contract(
            address=w3.to_checksum_address(USDC_POLYGON),
            abi=[{"inputs": [{"name": "account", "type": "address"}], "name": "balanceOf", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"}],
        )
        raw = usdc.functions.balanceOf(w3.to_checksum_address(proxy)).call()
        return float(raw) / (10**USDC_DECIMALS)
    except Exception:
        return 0.0


def has_position_in_event(proxy: str, event_id: str) -> bool:
    """
    检查用户在该事件是否已有仓位。
    对应事件只能有一个仓位，若已有则不应再下单。

    Args:
        proxy: Polymarket 充币地址（用户）
        event_id: Gamma 返回的 event id

    Returns:
        True 表示已有仓位
    """
    if not event_id:
        return False
    try:
        resp = httpx.get(
            DATA_POSITIONS_URL,
            params={"user": proxy, "eventId": event_id, "sizeThreshold": 0},
            timeout=10.0,
        )
        resp.raise_for_status()
        positions = resp.json()
        return len(positions) > 0
    except (httpx.HTTPError, httpx.HTTPStatusError):
        return False


def place_5m_order(
    token_id: str,
    amount_usd: float,
    *,
    private_key: str,
    proxy: str,
    signature_type: int = 2,
) -> dict[str, Any]:
    """
    在 Polymarket 上下 5M 市价单（FAK：能成交多少算多少，未成交部分取消）。

    Args:
        token_id: Up 或 Down 的 token ID
        amount_usd: 下单金额（USD）
        private_key: 钱包私钥
        proxy: Polymarket 充币地址（funder）
        signature_type: 签名类型，2 为浏览器钱包

    Returns:
        CLOB API 响应 dict
    """
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import MarketOrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY

    host = "https://clob.polymarket.com"
    chain_id = 137

    client = ClobClient(
        host,
        key=private_key,
        chain_id=chain_id,
        signature_type=signature_type,
        funder=proxy,
    )
    client.set_api_creds(client.create_or_derive_api_creds())

    order_args = MarketOrderArgs(
        token_id=token_id,
        amount=amount_usd,
        side=BUY,
        order_type=OrderType.FAK,
    )
    signed = client.create_market_order(order_args)
    return client.post_order(signed, OrderType.FAK)
