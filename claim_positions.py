#!/usr/bin/env python3
"""
Claim 服务：单独运行 claim，不启动价格监控和交易。
用法: python claim_positions.py
      uv run claim_positions.py
      python claim_positions.py --once   # 单次执行后退出
"""

import argparse
import os
import signal
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

DATA_POSITIONS_URL = "https://data-api.polymarket.com/positions"
USDC_POLYGON = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_POLYGON = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
HASH_ZERO = "0x" + "0" * 64
RELAYER_URL = "https://relayer-v2.polymarket.com"
CHAIN_ID = 137
POLYGON_RPC = "https://polygon-rpc.com"
DEFAULT_POLL_INTERVAL = 60  # 秒

SAFE_EXEC_ABI = [
    {
        "name": "execTransaction",
        "type": "function",
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "data", "type": "bytes"},
            {"name": "operation", "type": "uint8"},
            {"name": "safeTxGas", "type": "uint256"},
            {"name": "baseGas", "type": "uint256"},
            {"name": "gasPrice", "type": "uint256"},
            {"name": "gasToken", "type": "address"},
            {"name": "refundReceiver", "type": "address"},
            {"name": "signatures", "type": "bytes"},
        ],
        "outputs": [{"name": "success", "type": "bool"}],
    },
    {"name": "nonce", "type": "function", "inputs": [], "outputs": [{"name": "nonce", "type": "uint256"}]},
]


def get_redeemable_positions(user: str) -> list[dict]:
    """获取可 redeem 的仓位列表。user 为 proxy 或 EOA 地址。"""
    try:
        resp = httpx.get(
            DATA_POSITIONS_URL,
            params={"user": user, "redeemable": True, "sizeThreshold": 0},
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, httpx.HTTPStatusError) as e:
        print(f"获取仓位失败: {e}")
        return []


def group_by_condition(positions: list[dict]) -> dict[str, list[dict]]:
    """按 conditionId 分组（同一 condition 可一次 redeem）。"""
    groups: dict[str, list[dict]] = {}
    for p in positions:
        cid = p.get("conditionId", "")
        if not cid:
            continue
        if cid not in groups:
            groups[cid] = []
        groups[cid].append(p)
    return groups


def encode_redeem_positions(
    collateral_token: str, parent_collection_id: str, condition_id: str, index_sets: list[int]
) -> str:
    """编码 redeemPositions 调用数据。"""
    from eth_abi import encode
    from eth_utils import keccak

    sig = b"redeemPositions(address,bytes32,bytes32,uint256[])"
    selector = keccak(sig)[:4]
    parent_b = bytes.fromhex(parent_collection_id.replace("0x", "").zfill(64))
    cond_b = bytes.fromhex(condition_id.replace("0x", "").zfill(64))
    args = encode(
        ["address", "bytes32", "bytes32", "uint256[]"],
        [collateral_token, parent_b, cond_b, index_sets],
    )
    return "0x" + (selector + args).hex()


def claim_via_relayer(
    condition_id: str,
    private_key: str,
    builder_key: str,
    builder_secret: str,
    builder_passphrase: str,
) -> bool:
    """通过 Polymarket Relayer 执行 claim（免 gas）。"""
    from py_builder_relayer_client.client import RelayClient, RelayerTxType
    from py_builder_relayer_client.models import OperationType, SafeTransaction
    from py_builder_signing_sdk.config import BuilderConfig, BuilderApiKeyCreds

    pk = private_key.strip()
    if not pk.startswith("0x"):
        pk = "0x" + pk

    builder_config = BuilderConfig(
        local_builder_creds=BuilderApiKeyCreds(
            key=builder_key,
            secret=builder_secret,
            passphrase=builder_passphrase,
        )
    )

    client = RelayClient(
        RELAYER_URL,
        CHAIN_ID,
        pk,
        builder_config,
        RelayerTxType.SAFE,
    )

    data = encode_redeem_positions(USDC_POLYGON, HASH_ZERO, condition_id, [1, 2])

    txn = SafeTransaction(
        to=CTF_POLYGON,
        operation=OperationType.Call,
        data=data,
        value="0",
    )

    try:
        resp = client.execute([txn], "Redeem positions")
        result = resp.wait()
        if result and result.get("state") in ("STATE_CONFIRMED", "STATE_MINED"):
            print(f"  ✓ claim 成功: {result.get('transactionHash', '')}")
            return True
        print(f"  ✗ claim 失败: {result}")
        return False
    except Exception as e:
        print(f"  ✗ 异常: {e}")
        return False


def claim_via_safe(
    condition_id: str,
    private_key: str,
    proxy: str,
    rpc_url: str,
    signature_type: int = 1,
) -> bool:
    """
    通过 Safe.execTransaction 执行 claim。
    signature_type 1: pre-validated（执行者即 owner）
    signature_type 2: ECDSA EIP-712 签名（备选）
    """
    from web3 import Web3
    from web3.middleware import ExtraDataToPOAMiddleware
    from eth_account import Account
    from eth_account.messages import encode_typed_data

    pk = private_key.strip()
    if not pk.startswith("0x"):
        pk = "0x" + pk

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    if not w3.is_connected():
        print(f"  ✗ RPC 连接失败: {rpc_url}")
        return False

    account = Account.from_key(pk)
    safe = Web3.to_checksum_address(proxy)
    safe_contract = w3.eth.contract(safe, abi=SAFE_EXEC_ABI)

    data = encode_redeem_positions(USDC_POLYGON, HASH_ZERO, condition_id, [1, 2])
    zero_addr = "0x0000000000000000000000000000000000000000"

    if signature_type == 1:
        # Pre-validated 签名 (type 1): 32 字节 padded owner + 32 字节占位 + 0x01
        owner_bytes = bytes.fromhex(account.address[2:].lower())
        packed_sig = bytes(12) + owner_bytes + bytes(32) + bytes([1])
    else:
        # ECDSA EIP-712 签名
        nonce = safe_contract.functions.nonce().call()
        domain = {"chainId": CHAIN_ID, "verifyingContract": safe}
        types = {
            "EIP712Domain": [
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "SafeTx": [
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "data", "type": "bytes"},
                {"name": "operation", "type": "uint8"},
                {"name": "safeTxGas", "type": "uint256"},
                {"name": "baseGas", "type": "uint256"},
                {"name": "gasPrice", "type": "uint256"},
                {"name": "gasToken", "type": "address"},
                {"name": "refundReceiver", "type": "address"},
                {"name": "nonce", "type": "uint256"},
            ],
        }
        message = {
            "to": CTF_POLYGON,
            "value": 0,
            "data": bytes.fromhex(data[2:]),
            "operation": 0,
            "safeTxGas": 0,
            "baseGas": 0,
            "gasPrice": 0,
            "gasToken": zero_addr,
            "refundReceiver": zero_addr,
            "nonce": nonce,
        }
        payload = {"types": types, "primaryType": "SafeTx", "domain": domain, "message": message}
        signable = encode_typed_data(full_message=payload)
        signed = account.sign_message(signable)
        r, s, v = signed.r, signed.s, signed.v
        if v <= 1:
            v += 27
        packed_sig = bytes(12) + bytes.fromhex(account.address[2:]) + r.to_bytes(32, "big") + s.to_bytes(32, "big") + v.to_bytes(1, "big")

    params = {
        "to": CTF_POLYGON,
        "value": 0,
        "data": data,
        "operation": 0,
        "safeTxGas": 0,
        "baseGas": 0,
        "gasPrice": 0,
        "gasToken": zero_addr,
        "refundReceiver": zero_addr,
        "signatures": "0x" + packed_sig.hex(),
    }

    for attempt in range(3):
        try:
            calldata = safe_contract.encode_abi("execTransaction", list(params.values()))
            tx = {
                "from": account.address,
                "to": safe,
                "value": 0,
                "data": calldata,
                "chainId": CHAIN_ID,
                "nonce": w3.eth.get_transaction_count(account.address),
            }
            tx["gas"] = int(w3.eth.estimate_gas(tx) * 1.2)
            tx["maxFeePerGas"] = w3.eth.max_priority_fee + (2 * w3.eth.get_block("latest")["baseFeePerGas"])
            tx["maxPriorityFeePerGas"] = w3.eth.max_priority_fee

            signed_tx = w3.eth.account.sign_transaction(tx, pk)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
            if receipt["status"] == 1:
                print(f"  ✓ claim 成功: {w3.to_hex(tx_hash)}")
                return True
            print(f"  ✗ 交易失败: {w3.to_hex(tx_hash)}")
            return False
        except Exception as e:
            err_str = str(e)
            if ("rate limit" in err_str.lower() or "too many requests" in err_str.lower() or "-32090" in err_str) and attempt < 2:
                print(f"  RPC 限流，12s 后重试 ({attempt + 1}/3)...")
                time.sleep(12)
            else:
                print(f"  ✗ 异常: {e}")
                return False
    return False


def claim_via_eoa(condition_id: str, private_key: str, rpc_url: str) -> bool:
    """EOA 直接调用 CTF.redeemPositions（仓位需在 EOA）。"""
    from web3 import Web3
    from web3.middleware import ExtraDataToPOAMiddleware
    from eth_account import Account

    pk = private_key.strip()
    if not pk.startswith("0x"):
        pk = "0x" + pk

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    if not w3.is_connected():
        print(f"  ✗ RPC 连接失败: {rpc_url}")
        return False

    account = Account.from_key(pk)
    data = encode_redeem_positions(USDC_POLYGON, HASH_ZERO, condition_id, [1, 2])

    try:
        tx = {
            "from": account.address,
            "to": CTF_POLYGON,
            "value": 0,
            "data": data,
            "chainId": CHAIN_ID,
            "nonce": w3.eth.get_transaction_count(account.address),
        }
        tx["gas"] = int(w3.eth.estimate_gas(tx) * 1.2)
        tx["maxFeePerGas"] = w3.eth.max_priority_fee + (2 * w3.eth.get_block("latest")["baseFeePerGas"])
        tx["maxPriorityFeePerGas"] = w3.eth.max_priority_fee

        signed_tx = w3.eth.account.sign_transaction(tx, pk)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        if receipt["status"] == 1:
            print(f"  ✓ claim 成功: {w3.to_hex(tx_hash)}")
            return True
        print(f"  ✗ 交易失败: {w3.to_hex(tx_hash)}")
        return False
    except Exception as e:
        print(f"  ✗ 异常: {e}")
        return False


def run_claim_service(
    *,
    private_key: str,
    use_proxy_wallet: bool = True,
    polymarket_proxy: str | None = None,
    signature_type: int = 1,
    rpc_url: str = POLYGON_RPC,
    poll_interval: int = DEFAULT_POLL_INTERVAL,
    once: bool = False,
) -> None:
    """
    Claim 服务主逻辑。
    - use_proxy_wallet: 使用 Proxy/Safe 执行 claim（Polymarket 默认）
    - polymarket_proxy: 用于查询仓位并执行（使用 Safe 时必填）
    - signature_type: 1=pre-validated, 2=ECDSA（仅 use_proxy_wallet 时生效）
    """
    user = (polymarket_proxy or "").strip()
    if use_proxy_wallet and not user:
        print("请配置 .env 中的 POLYMARKET_PROXY（useProxyWallet=True 时必填）")
        sys.exit(1)
    if not user:
        from eth_account import Account
        pk = private_key.strip()
        if not pk.startswith("0x"):
            pk = "0x" + pk
        user = Account.from_key(pk).address

    builder_key = os.environ.get("POLY_BUILDER_API_KEY", "").strip()
    builder_secret = os.environ.get("POLY_BUILDER_SECRET", "").strip()
    builder_passphrase = os.environ.get("POLY_BUILDER_PASSPHRASE", "").strip()
    use_relayer = bool(builder_key and builder_secret and builder_passphrase)

    def do_claim_round() -> int:
        positions = get_redeemable_positions(user)
        if not positions:
            if once:
                print("无可兑换仓位")
            return 0
        groups = group_by_condition(positions)
        print(f"共 {len(positions)} 个可兑换仓位，{len(groups)} 个 condition")
        success = 0
        for i, (condition_id, group) in enumerate(groups.items()):
            if use_proxy_wallet and not use_relayer and i > 0:
                time.sleep(2)  # 降低 RPC 请求频率
            title = (group[0].get("title") or "").strip() or condition_id[:18] + "..."
            print(f"Claim: {title}")
            if use_relayer:
                ok = claim_via_relayer(condition_id, private_key, builder_key, builder_secret, builder_passphrase)
            elif use_proxy_wallet:
                ok = claim_via_safe(condition_id, private_key, user, rpc_url, signature_type)
            else:
                ok = claim_via_eoa(condition_id, private_key, rpc_url)
            if ok:
                success += 1
        return success

    if once:
        success = do_claim_round()
        print(f"\n完成: {success} 个 condition 已 claim")
        return

    stopped = False

    def on_signal(*_args: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    while not stopped:
        success = do_claim_round()
        if success > 0:
            print(f"\n本轮完成: {success} 个 condition 已 claim\n")
        for _ in range(poll_interval):
            if stopped:
                break
            time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Claim 服务（单独调试模式）")
    parser.add_argument("--once", action="store_true", help="单次执行后退出")
    parser.add_argument("--poll", type=int, default=DEFAULT_POLL_INTERVAL, help=f"轮询间隔（秒），默认 {DEFAULT_POLL_INTERVAL}")
    args = parser.parse_args()

    pk = os.environ.get("PRIVATE_KEY", "").strip()
    if not pk:
        print("请设置 .env 中的 PRIVATE_KEY")
        sys.exit(1)

    use_proxy = os.environ.get("USE_PROXY_WALLET", "1") != "0"
    proxy = os.environ.get("POLYMARKET_PROXY", "").strip() or None
    sig_type = 2 if os.environ.get("CLAIM_SIGNATURE_TYPE") == "2" else 1
    rpc_url = os.environ.get("RPC_URL", POLYGON_RPC).strip()

    print("Claim 服务已启动（单独调试模式）")
    print(f"useProxyWallet: {use_proxy} | polymarketProxy: {proxy or '(未设置)'} | signatureType: {sig_type}")
    if not args.once:
        print("按 Ctrl+C 退出")
    print()

    run_claim_service(
        private_key=pk,
        use_proxy_wallet=use_proxy,
        polymarket_proxy=proxy,
        signature_type=sig_type,
        rpc_url=rpc_url,
        poll_interval=args.poll,
        once=args.once,
    )


if __name__ == "__main__":
    main()
