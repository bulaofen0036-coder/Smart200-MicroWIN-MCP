"""本机私有配置读取。

模板工程、回归样本、UIA 交叉验证真值都是客户工程内容，不进版本库；
放在仓库根的 .smart200_local.json（已 gitignore）。缺失时调用方应如实报"跳过"，
不要静默当成通过。
"""

import json
import os

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    ".smart200_local.json")


def load():
    if not os.path.exists(PATH):
        return {}
    with open(PATH, encoding="utf-8") as f:
        return json.load(f)


def get(key, default=None):
    return load().get(key, default)
