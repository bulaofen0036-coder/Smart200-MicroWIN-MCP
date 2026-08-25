# -*- coding: utf-8 -*-
"""deploy 报告裁剪（_slim_report）的防回归测试。纯离线，不需要 MicroWIN。

这个函数决定使用者能看到什么，写错了就是"排错信息被悄悄吃掉"——
和判据写错一样危险，所以必须单独可测。

三条硬要求：
  1. 全过时精简：不带整段引擎日志、不带逐块明细；
  2. 没过时该给的一条不少：哪些块有无效网络、哪些块往返对不上、
     哪些符号没设上、引擎日志；
  3. 警告类（块被重排、文件被转码、旁边冒出 .smart）**无论成败都保留** ——
     那是"虽然过了但你该知道"，最容易在做精简时被误删。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from smart200_mcp.server import _slim_report   # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  " + str(extra)))
    if not cond:
        fails.append(name)


def make(passed=True, **detail):
    d = {
        "engine_validate": {"MAIN": {"nets": 14, "invalid": [], "error": None},
                            "SBR1": {"nets": 15, "invalid": [], "error": None}},
        "roundtrip": {"MAIN": {"instructions_src": 32, "instructions_back": 32,
                               "missing_kinds": []},
                      "SBR1": {"instructions_src": 60, "instructions_back": 60,
                               "missing_kinds": []}},
        "log": ["script IMPORTPOU ... ret=0"] * 40,
        "blocks": {"a.awl": "MAIN"},
        "symbols": {"I0.1": {"name": "启动", "row": 1, "ok": True}},
        "persisted": {"produced": True, "bytes": 58160,
                      "fingerprint_before": "18511:aaa", "fingerprint_after": "58160:bbb"},
    }
    d.update(detail)
    return {"stage1_structure": "PASS", "stage2_compile": "PASS",
            "stage3_engine_validate": "PASS", "stage4_roundtrip": "PASS",
            "stage5_persisted": "PASS", "passed": passed,
            "project": r"C:\x\y.smartV3", "detail": d}


print("=== 全过时应当精简 ===")
r = _slim_report(make())
check("保留五关结果", all(k in r for k in (
    "stage1_structure", "stage2_compile", "stage3_engine_validate",
    "stage4_roundtrip", "stage5_persisted", "passed")))
check("不带整段引擎日志", "log" not in str(r.get("detail", {})) and "log" not in r)
check("不带逐块明细", "engine_validate" not in r and "roundtrip" not in r)
check("给出块数", r["summary"]["blocks"] == 2, r.get("summary"))
check("给出网络数合计", r["summary"]["networks"] == 29, r.get("summary"))
check("给出指令数合计", r["summary"]["instructions"] == 92, r.get("summary"))
check("给出落盘字节数", r.get("persisted_bytes") == 58160, r)

print("=== 反向哨兵：警告类即使全过也必须保留 ===")
r2 = _slim_report(make(reordered="主程序 a.awl 已自动排到最前",
                       encoding_normalized=["x.awl", "y.awl"],
                       persist_warn="旁边冒出了 y.smart"))
check("保留 块被重排 的提示", "reordered" in r2, r2)
check("保留 文件被转码 的提示", r2.get("encoding_normalized") == ["x.awl", "y.awl"], r2)
check("保留 落盘异常 的告警", "persist_warn" in r2, r2)

print("=== 没过时诊断信息一条不少 ===")
bad = make(passed=False)
bad["stage3_engine_validate"] = "FAIL"
bad["detail"]["engine_validate"]["SBR1"] = {"nets": 15, "invalid": [8, 9], "error": None}
bad["detail"]["roundtrip"]["MAIN"] = {"instructions_src": 32, "instructions_back": 30,
                                      "missing_kinds": ["MOVW"],
                                      "error": "指令流对不上：源 32 条、回来 30 条"}
bad["detail"]["symbols"]["Q9.9"] = {"name": "不存在", "row": None, "ok": False}
bad["detail"]["hint"] = "软件判定这些网络是无效程序段"
r3 = _slim_report(bad)
det = r3.get("detail", {})
check("指出哪些块有无效网络", "SBR1" in det.get("blocks_with_invalid_networks", {}), det)
check("只列出问题块，不列全部", "MAIN" not in det.get("blocks_with_invalid_networks", {}), det)
check("指出哪些块往返对不上", "MAIN" in det.get("blocks_failing_roundtrip", {}), det)
check("指出哪些符号没设上", "Q9.9" in det.get("symbols_not_set", {}), det)
check("没设上的符号只列失败的", "I0.1" not in det.get("symbols_not_set", {}), det)
check("附上引擎日志供排错", "log" in det)
check("附上 hint", "hint" in det)

print("=== 边角：报告残缺也不能崩 ===")
check("空 detail 不崩", isinstance(_slim_report({"passed": True, "detail": {}}), dict))
check("没有 detail 键也不崩", isinstance(_slim_report({"passed": False}), dict))
check("第1关就失败时保留 structure", "structure" in _slim_report(
    {"passed": False, "stage1_structure": "FAIL",
     "detail": {"structure": [{"file": "a.awl", "invalid": [1]}]}}).get("detail", {}))

print("")
print("全部通过" if not fails else str(len(fails)) + " 项失败: " + str(fails))
sys.exit(1 if fails else 0)
