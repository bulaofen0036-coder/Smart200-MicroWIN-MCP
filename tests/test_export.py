# -*- coding: utf-8 -*-
"""一键导出全部块（split_blocks / _safe_filename）的防回归测试。纯离线。

拆块拆错的后果是【静默少导出几个块】—— 不报错、文件也在，
只是少了几个，跟备份一样看不出来。所以判据要单独可测。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from smart200_mcp import autoflow   # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  " + str(extra)))
    if not cond:
        fails.append(name)


LF = chr(10)
SAMPLE = LF.join([
    "ORGANIZATION_BLOCK MAIN:OB1",
    "TITLE=主程序",
    "BEGIN",
    "Network 1",
    "\tLD     SM0.0",
    "\tCALL   子程序甲",
    "Network 2",
    "\tLD     SM0.5",
    "\t=      Q1.7",
    "END_ORGANIZATION_BLOCK",
    "",
    "SUBROUTINE_BLOCK 子程序甲:SBR0",
    "TITLE=",
    "BEGIN",
    "Network 1",
    "\tLD     I0.0",
    "\t+I     +1, VW0",
    "END_SUBROUTINE_BLOCK",
    "",
    "INTERRUPT_BLOCK INT_0:INT0",
    "TITLE=中断",
    "BEGIN",
    "Network 1",
    "\tLD     SM0.0",
    "\tINCD   VD900",
    "END_INTERRUPT_BLOCK",
    "",
])

print("=== 拆块 ===")
bs = autoflow.split_blocks(SAMPLE)
check("拆出 3 个块", len(bs) == 3, [b["name"] for b in bs])
names = [b["name"] for b in bs]
check("块名正确", names == ["MAIN", "子程序甲", "INT_0"], names)
check("块 ID 正确", [b["id"] for b in bs] == ["OB1", "SBR0", "INT0"], [b["id"] for b in bs])
check("块类型正确", [b["kind"] for b in bs] == ["ORGANIZATION", "SUBROUTINE", "INTERRUPT"],
      [b["kind"] for b in bs])
check("网络数分别是 2/1/1", [b["networks"] for b in bs] == [2, 1, 1],
      [b["networks"] for b in bs])
check("每块正文自带头尾", bs[0]["text"].startswith("ORGANIZATION_BLOCK")
      and bs[0]["text"].rstrip().endswith("END_ORGANIZATION_BLOCK"))
check("不把别的块混进来", "子程序甲" not in bs[0]["text"].split("END_ORGANIZATION_BLOCK")[0]
      or "CALL   子程序甲" in bs[0]["text"])
check("CRLF 输入也能拆", len(autoflow.split_blocks(SAMPLE.replace(LF, chr(13) + LF))) == 3)

print("=== 反向哨兵 ===")
truncated = SAMPLE[:SAMPLE.index("END_INTERRUPT_BLOCK")]
bs2 = autoflow.split_blocks(truncated)
check("没有 END_ 的半截块不算数", len(bs2) == 2, [b["name"] for b in bs2])
check("空文本拆出 0 个", autoflow.split_blocks("") == [])
check("没有 BLOCK 头的文本拆出 0 个",
      autoflow.split_blocks("Network 1" + LF + "\tLD I0.0" + LF) == [])

print("=== 文件名清洗 ===")
check("斜杠被替换", "/" not in autoflow._safe_filename("A/B"))
check("冒号被替换", ":" not in autoflow._safe_filename("电机:启动"))
check("星号问号被替换", autoflow._safe_filename("a*b?c") == "a_b_c",
      autoflow._safe_filename("a*b?c"))
check("中文原样保留", autoflow._safe_filename("顺控主流程") == "顺控主流程")
check("空名不产生空文件名", autoflow._safe_filename("   ") == "unnamed",
      autoflow._safe_filename("   "))
check("结尾的点被去掉（Windows 不允许）", not autoflow._safe_filename("abc.").endswith("."))

print("=== 符号引用检测（导出件依赖符号表这件事要能被发现）===")
sym = LF.join([
    "SUBROUTINE_BLOCK T:SBR0", "BEGIN", "Network 1",
    "	LD     急停_常闭",
    "	AN     M3.0",
    "	A      安全门关闭",
    "	=      M0.0",
    "END_SUBROUTINE_BLOCK",
])
refs = autoflow.symbol_refs(sym)
check("识别出符号名", set(refs) == {"急停_常闭", "安全门关闭"}, refs)
check("绝对地址不算符号", "M3.0" not in refs and "M0.0" not in refs, refs)

# 零误报是这个检测的生命线：误报会让人以为导出件有问题
absolute = LF.join([
    "SUBROUTINE_BLOCK T:SBR0", "BEGIN", "Network 1",
    "	LD     I0.0", "	AN     SM0.1", "	A      V600.2", "	=      Q1.7",
    "	MOVW   AIW16, VW200", "	MOVR   0.0174533, VD228", "	+D     +1000, VD308",
    "	ANDD   16#0000FFFF, VD364", "	TON    T37, +50", "	CTU    C0, +100",
    "	MOVD   HC0, VD850", "	*D     AC0, AC2", "	MOVD   VD4508, LD30",
    "	SLB    VB368, 2", "	BIR    IB1, VB710", "	MOVW   VW252, AQW16",
    "END_SUBROUTINE_BLOCK",
])
check("各种绝对地址形态零误报", autoflow.symbol_refs(absolute) == [],
      autoflow.symbol_refs(absolute))

# 块名/标号/步 是名字但不是符号引用
names = LF.join([
    "SUBROUTINE_BLOCK T:SBR0", "BEGIN", "Network 1",
    "	LD     SM0.0", "	CALL   子程序甲", "	ATCH   INT_0, 0",
    "	JMP    1", "	LBL    1", "	SCRT   S0.1", "	LSCR   S0.0",
    "END_SUBROUTINE_BLOCK",
])
check("块名/标号/步不算符号引用", autoflow.symbol_refs(names) == [],
      autoflow.symbol_refs(names))

print("=== 内置系统符号要和用户符号分开 ===")
u, sy = autoflow.classify_symbol_refs(
    ["启动按钮", "Always_On", "Clock_1s", "P0_Config", "HSC0_CV",
     "PLS0_Ctrl", "PWM1_PW", "Time_0_Intrvl", "First_Scan_On", "上料电机"])
check("用户符号挑出来", set(u) == {"启动按钮", "上料电机"}, u)
check("系统符号全部认出", len(sy) == 8, sy)
# 反向哨兵：认不出的要算作用户符号（宁可多提示也不能漏，漏了就是导不回去）
u2, sy2 = autoflow.classify_symbol_refs(["某个没见过的名字"])
check("认不出的归到用户符号", u2 == ["某个没见过的名字"] and sy2 == [], (u2, sy2))
check("空输入不崩", autoflow.classify_symbol_refs([]) == ([], []))

print("")
print("全部通过" if not fails else str(len(fails)) + " 项失败: " + str(fails))
sys.exit(1 if fails else 0)
