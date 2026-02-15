#!/usr/bin/env python3
"""在每小时的 0、15、30、45 分钟（UTC）运行一次 main.py"""

import subprocess
import sys
import time
from pathlib import Path


def next_quarter_utc() -> float:
    """下一档 0/15/30/45 分钟（UTC）的 Unix 时间戳，留 5 秒缓冲避免重复执行。"""
    now = time.time()
    # 当前 UTC 分钟
    secs = int(now)
    remainder = secs % 900  # 900 = 15 * 60
    # 下一档开始时间
    next_sec = secs - remainder + 900
    # 若已在档内前 5 秒，用当前档；否则用下一档
    if remainder < 5:
        next_sec = secs - remainder
    return float(next_sec)


def main() -> None:
    project_root = Path(__file__).resolve().parent
    main_py = project_root / "main.py"

    while True:
        target = next_quarter_utc()
        wait_sec = max(0, target - time.time())
        if wait_sec > 0:
            next_ts = time.strftime("%H:%M:%S UTC", time.gmtime(target))
            print(f"等待至 {next_ts} 运行... ({wait_sec:.0f}s)")
            time.sleep(wait_sec)

        print("-" * 40)
        result = subprocess.run(
            [sys.executable, str(main_py)] + sys.argv[1:],
            cwd=project_root,
        )
        if result.returncode != 0:
            print(f"main.py 退出码: {result.returncode}")

        # 避免同一分钟内重复执行
        time.sleep(10)


if __name__ == "__main__":
    main()
