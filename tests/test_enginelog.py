# -*- coding: utf-8 -*-
"""引擎日志判据的防回归测试 —— 用【真实日志样本】。

为什么要有这个文件：判据以前散在 engine/autoflow 里且零覆盖，
于是 `IMPORTPOU 路径 ret=0`（无引号）与真实日志 `IMPORTPOU '路径' ret=0`（带引号）
不一致的 bug 活了很久：导入明明成功，工具恒报失败。

含必错哨兵：哨兵若 PASS = 判据被改松了，测试本身失去意义。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from smart200_mcp import enginelog

NL = chr(10)
fails = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  <- " + str(detail)))
    if not cond:
        fails.append(name)


# ---- 真实日志（2026-08-23 实跑抓的，路径与引号原样保留）----
REAL_OK = NL.join([
    "找到主窗口 hwnd=0x00241B16，子类化并投递 WM_SMART_RUN",
    "[主线程] g_Retrieve=0x6A3DB790 g_Store=0x6A3DB788",
    "[主线程] 当前工程号=1 action=script",
    "script IMPORTPOU 'E:\\Smart200_Mcp\\samples\\v4_int.awl' ret=0",
    "script IMPORTPOU 'E:\\Smart200_Mcp\\samples\\v3_basic.awl' ret=0",
    "script COMPILE ret=0",
    "script VALIDATE '指令大全示例' nets=43 invalid=0 lang=0",
    "script EXPORT '指令大全示例' -> E:\\out\\a.awl ret=0",
    "script SAVE ret=0",
    "[主线程] 完成",
    "__DONE__",
])

REAL_BAD = NL.join([
    "script IMPORTPOU 'E:\\Smart200_Mcp\\samples\\demo_v2_gbk.awl' ret=0",
    "script COMPILE ret=0",
    "script VALIDATE '指令大全示例' net=2 INVALID",
    "script VALIDATE '指令大全示例' net=3 INVALID",
    "script VALIDATE '指令大全示例' net=26 INVALID",
    "script VALIDATE '指令大全示例' nets=27 invalid=3 lang=0",
    "__DONE__",
])

# 软件中途崩掉：日志断在半截，没有 __DONE__
TRUNCATED = NL.join([
    "[主线程] g_Retrieve=0x6A3DB790 g_Store=0x6A3DB788",
    "script IMPORTPOU 'E:\\a.awl' ret=0",
    "script COMPILE ret=0",
])

FILES = ["E:\\Smart200_Mcp\\samples\\v4_int.awl", "E:\\Smart200_Mcp\\samples\\v3_basic.awl"]

print("=== 1. 导入判据（带引号路径）===")
check("成功日志判为导入成功", enginelog.imports_ok(REAL_OK, FILES))
check("大小写不同的路径也认", enginelog.imports_ok(REAL_OK, [f.upper() for f in FILES]))
check("编译判为通过", enginelog.compiled(REAL_OK))
check("导出路径解析出来", enginelog.exports(REAL_OK) == {"E:\\out\\a.awl": True},
      enginelog.exports(REAL_OK))

print("=== 2. 引擎真值判据 ===")
v = enginelog.validation(REAL_OK)
check("好日志：0 个无效网络", v["指令大全示例"]["invalid"] == [] and v["指令大全示例"]["nets"] == 43, v)
vb = enginelog.validation(REAL_BAD)
check("坏日志：抓到 2,3,26", vb["指令大全示例"]["invalid"] == [2, 3, 26], vb)
check("好日志 validation_ok", enginelog.validation_ok(REAL_OK, ["指令大全示例"]))

print("=== 3. 必错哨兵（这些若 PASS 说明判据被改松了）===")
check("哨兵：坏日志必须判不通过",
      not enginelog.validation_ok(REAL_BAD, ["指令大全示例"]))
check("哨兵：没验过的块必须判不通过（不能默认放行）",
      not enginelog.validation_ok(REAL_OK, ["根本不存在的块"]))
check("哨兵：半截日志必须判未完成", not enginelog.done(TRUNCATED))
check("哨兵：少导入一个文件必须判失败",
      not enginelog.imports_ok(REAL_OK, FILES + ["E:\\没导过.awl"]))
check("哨兵：ret 非 0 必须判失败",
      not enginelog.imports_ok("script IMPORTPOU 'E:\\a.awl' ret=-1610612428", ["E:\\a.awl"]))
check("哨兵：空日志必须判失败", not enginelog.imports_ok("", FILES))
check("哨兵：编译 ret 非 0 必须判失败", not enginelog.compiled("script COMPILE ret=-5"))
check("哨兵：VALIDATE 报错(块没找到)必须判不通过",
      not enginelog.validation_ok("script VALIDATE 'X' ERR=块未找到 find_ret=0" + NL + "__DONE__", ["X"]))

print("=== 4. 旧判据必须已经死掉（B1 回归）===")
old_style = "IMPORTPOU " + FILES[0] + " ret=0"
check("旧的无引号拼串在真实日志里确实匹配不到（证明 B1 是真 bug）",
      old_style not in REAL_OK)


print("=== 5. 符号判据（SYMSET）===")
SYM_OK = NL.join([
    "script SYMSET 'I0.0' -> '电机启动' 表=00000000ae0d00000000000000000080 行=0 SetName=0",
    "script SYMSET 'Q0.0' -> '电机运行' 表=00000000ae0d00000000000000000080 行=16 SetName=0",
    "__DONE__",
])
SYM_BAD = NL.join([
    "script SYMSET 'I0.0' -> '电机启动' 表=00000000ae0d00000000000000000080 行=0 SetName=0",
    "script SYMSET 'I9.9' ERR=没找到该地址所在的行",
    "__DONE__",
])
WANT = {"电机启动": "I0.0", "电机运行": "Q0.0"}
check("解析出两个符号", set(enginelog.symbols(SYM_OK)) == {"I0.0", "Q0.0"},
      enginelog.symbols(SYM_OK))
check("全部设上判为通过", enginelog.symbols_ok(SYM_OK, WANT))
check("地址大小写不敏感", enginelog.symbols_ok(SYM_OK, {"电机启动": "i0.0", "电机运行": "q0.0"}))

print("=== 6. 符号判据的必错哨兵 ===")
check("哨兵：地址找不到行必须判失败",
      not enginelog.symbols_ok(SYM_BAD, {"电机启动": "I0.0", "急停": "I9.9"}))
check("哨兵：少设一个符号必须判失败",
      not enginelog.symbols_ok(SYM_OK, dict(WANT, 急停="I0.7")))
check("哨兵：名字对不上必须判失败",
      not enginelog.symbols_ok(SYM_OK, {"别的名字": "I0.0"}))
check("哨兵：SetName 非 0 必须判失败",
      not enginelog.symbols_ok(
          "script SYMSET 'I0.0' -> '电机启动' 表=x 行=0 SetName=-5", {"电机启动": "I0.0"}))

print("")
print("全部通过" if not fails else str(len(fails)) + " 项失败: " + str(fails))
sys.exit(1 if fails else 0)
