# -*- coding: utf-8 -*-
"""stlcheck 防回归：用已知答案的样本双向验证 + 正反哨兵。

demo_v2 是用户实测确认有 9 个无效网络的坏样本；demo_v3 / demo_v4_ext 是确认全绿的好样本。
检查器必须精确命中前者、对后者零误报 —— 任一失败说明规则被改坏了。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from smart200_mcp import stlcheck

S = r"E:\Smart200_Mcp\samples"
fails = []

NL = chr(10)
TAB = chr(9)


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  <- " + str(detail)))
    if not cond:
        fails.append(name)


def wrap(body):
    return "SUBROUTINE_BLOCK T:SBR9" + NL + "BEGIN" + NL + body + "END_SUBROUTINE_BLOCK" + NL


def net(*lines):
    return "Network 1" + NL + "".join(TAB + l + NL for l in lines)


KNOWN_BAD = {2, 3, 4, 14, 15, 19, 22, 25, 26}

print("=== 已知坏样本 demo_v2（用户截图确认的 9 个无效网络）===")
p2 = os.path.join(S, "demo_v2.awl")
if os.path.exists(p2):
    got = {b["network"] for b in stlcheck.check_file(p2)["invalid"]}
    check("精确命中 9 个无效网络", got == KNOWN_BAD, sorted(got))
else:
    check("demo_v2 样本存在", False, "文件缺失")

print("=== 已知好样本（用户确认全绿）===")
for f in ("demo_v3.awl", "demo_v4_ext.awl"):
    p = os.path.join(S, f)
    if os.path.exists(p):
        check(f + " 零误报", stlcheck.check_file(p)["valid"])

print("=== 反向哨兵（必须被抓）===")
bad_cases = {
    "两条rung塞一个网络": net("LD     I0.0", "=      Q0.0", "LD     I0.1", "=      Q0.1"),
    "FOR与NEXT同段": net("LD     SM0.0", "FOR    VW0, +1, +5", "INCB   VB0", "NEXT"),
    "定时器后接输出": net("LD     I0.0", "TON    T37, +100", "LD     T37", "=      Q0.0"),
}
for name, body in bad_cases.items():
    check("哨兵抓到 " + name, not stlcheck.check(wrap(body))["valid"])

print("=== 正向哨兵（不该误报）===")
good_cases = {
    "计数器多输入": net("LD     I4.0", "LD     I4.1", "CTU    C0, +10"),
    "OLD块或": net("LD     I0.0", "A      I0.1", "LD     I0.2", "A      I0.3", "OLD", "=      Q0.0"),
    "LPS多输出": net("LD     I0.0", "LPS", "A      I0.1", "=      Q0.0", "LPP", "A      I0.2", "=      Q0.1"),
    "NEXT独立段": net("NEXT"),
}
for name, body in good_cases.items():
    check("正确放行 " + name, stlcheck.check(wrap(body))["valid"])

print("")
print("全部通过" if not fails else str(len(fails)) + " 项失败: " + str(fails))
sys.exit(1 if fails else 0)
