# -*- coding: utf-8 -*-
"""落盘判据的防回归测试（不需要装 MicroWIN，纯离线）。

护的是 2026-08-25 实测到的坑：
  PRJ_Save 对 .smartV3 工程会把内容【静默写进同名的 .smart(V2)】，
  原 .smartV3 字节不变，而且照样 ret=0 —— 前四关全绿但工程是空的。
所以 autoflow 里【任何】落盘都必须走 SAVEAS，不许回退成 SAVE。
"""
import io
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from smart200_mcp import autoflow, stlcheck   # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  " + str(extra)))
    if not cond:
        fails.append(name)


print("=== 指纹判据 ===")
d = tempfile.mkdtemp()
p = os.path.join(d, "x.bin")
io.open(p, "wb").write(b"A" * 100)
fp1 = autoflow._fingerprint(p)
check("同内容指纹稳定", autoflow._fingerprint(p) == fp1)
io.open(p, "wb").write(b"A" * 100 + b"B")
check("内容变了指纹就变", autoflow._fingerprint(p) != fp1)
check("文件不存在返回 None", autoflow._fingerprint(os.path.join(d, "nope")) is None)
# 这条是关键：大小一样但内容不同也必须能分辨（只比字节数会漏）
io.open(p, "wb").write(b"A" * 100)
io.open(p + "2", "wb").write(b"C" * 100)
check("等长不同内容能分辨", autoflow._fingerprint(p) != autoflow._fingerprint(p + "2"))

print("=== 源码防回归：落盘不许用 SAVE ===")
src = io.open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "smart200_mcp", "autoflow.py"), encoding="utf-8").read()
# 去掉注释行再找，免得被说明文字里的 "SAVE" 误伤
code = "\n".join(l for l in src.split("\n") if not l.strip().startswith("#"))
bare_save = re.findall(r'["\']SAVE["\']|["\']SAVE\s', code)
check("autoflow 里没有裸 SAVE 命令", not bare_save, bare_save)
check("落盘走的是 SAVEAS", '"SAVEAS ' in code)
check("deploy 会做第5关落盘校验", "stage5_persisted" in code)
check("set_symbols 编译不过会回滚", "rolled_back" in code)

print("=== SMART 不支持的助记符（黑名单）===")
def net(*ops):
    return "SUBROUTINE_BLOCK T:SBR0\nTITLE=\nBEGIN\nNetwork 1\n" + \
           "\n".join("\t" + o for o in ops) + "\nEND_SUBROUTINE_BLOCK\n"

check("NETR 被抓", not stlcheck.check(net("LD     M0.0", "NETR   VB780, 0"))["valid"])
check("NETW 被抓", not stlcheck.check(net("LD     M0.0", "NETW   VB800, 0"))["valid"])
check("GET 放行", stlcheck.check(net("LD     M0.0", "GET    VB780"))["valid"])
check("PUT 放行", stlcheck.check(net("LD     M0.0", "PUT    VB800"))["valid"])
# 反向哨兵：黑名单不能无脑扩大到所有没见过的助记符
check("没见过的助记符不误杀", stlcheck.check(net("LD     M0.0", "MOVB   1, VB0"))["valid"])
check("黑名单只收实测过的两条", set(stlcheck._NOT_IN_SMART) == {"NETR", "NETW"},
      sorted(stlcheck._NOT_IN_SMART))

print("")
print("全部通过" if not fails else str(len(fails)) + " 项失败: " + str(fails))
sys.exit(1 if fails else 0)
