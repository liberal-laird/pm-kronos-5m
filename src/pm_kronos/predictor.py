"""Kronos 模型预测 15m 涨跌"""

import os
import random
import sys
from pathlib import Path

# 项目根目录：src/pm_kronos/predictor.py -> 上两级
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_KRONOS_PATH = _PROJECT_ROOT / "lib" / "kronos"

if _KRONOS_PATH.exists():
    _kronos_str = str(_KRONOS_PATH)
    if _kronos_str not in sys.path:
        sys.path.insert(0, _kronos_str)


def _ensure_kronos():
    if not _KRONOS_PATH.exists():
        raise RuntimeError(
            f"Kronos 未找到于 {_KRONOS_PATH}。请执行:\n"
            "  git clone https://github.com/shiyu-coder/Kronos.git lib/kronos\n"
            "或: git submodule update --init --recursive"
        )


def predict_direction(
    df,
    x_timestamp,
    y_timestamp,
    *,
    device: str | None = None,
    seed: int = 42,
) -> str:
    """
    使用 Kronos 预测下一根 K 线的涨跌方向。

    Args:
        df: 含 open, high, low, close, volume 列的 DataFrame（96 行）
        x_timestamp: 历史时间序列（96 个）
        y_timestamp: 待预测的下一根 K 线时间戳（1 个）
        device: 计算设备，None 表示自动选择 (cuda/mps/cpu)
        seed: 随机种子，固定后可使预测结果可复现

    Returns:
        "涨" 或 "跌"
    """
    _ensure_kronos()

    # 固定随机种子，使预测可复现
    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except ImportError:
        pass
    import torch

    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        pass  # MPS 无单独 seed API，torch.manual_seed 已覆盖

    from model import Kronos, KronosPredictor, KronosTokenizer

    # 加载模型与分词器
    tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    model = Kronos.from_pretrained("NeoQuasar/Kronos-small")

    predictor = KronosPredictor(
        model=model,
        tokenizer=tokenizer,
        device=device,
        max_context=512,
    )

    # Kronos 需要 open, high, low, close；volume/amount 可选
    x_df = df[["open", "high", "low", "close"]].copy()
    if "volume" in df.columns:
        x_df["volume"] = df["volume"]
    else:
        x_df["volume"] = 0.0
    if "amount" not in x_df.columns:
        x_df["amount"] = 0.0

    pred_df = predictor.predict(
        df=x_df,
        x_timestamp=x_timestamp,
        y_timestamp=y_timestamp,
        pred_len=1,
        T=0.01,  # 极低温度 + 固定 seed，结果可复现
        top_p=1.0,
        sample_count=1,
        verbose=False,
    )

    current_close = float(df["close"].iloc[-1])
    pred_close = float(pred_df["close"].iloc[0])

    return "涨" if pred_close > current_close else "跌"
