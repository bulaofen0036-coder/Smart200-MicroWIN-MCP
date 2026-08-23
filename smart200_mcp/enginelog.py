"""引擎日志解析 —— 全部判据集中在这里，单独可测。

为什么单独一个模块：判据散在 engine/autoflow 里且没有测试时，
`IMPORTPOU 路径 ret=0` 与实际日志的 `IMPORTPOU '路径' ret=0`（带引号）
不一致这种 bug 能长期存活且没人发现（导入明明成功却报失败）。
这里的每个函数都由 tests/test_enginelog.py 用【真实日志样本】覆盖。
"""

import os
import re

_IMPORT_RE = re.compile(r"IMPORTPOU '([^']*)' ret=(-?\d+)")
_EXPORT_RE = re.compile(r"EXPORT '([^']*)' -> (.+?) ret=(-?\d+)")
_COMPILE_RE = re.compile(r"COMPILE ret=(-?\d+)")
_VAL_BAD_RE = re.compile(r"VALIDATE '([^']*)' net=(\d+) (INVALID|ERR)")
_VAL_SUM_RE = re.compile(r"VALIDATE '([^']*)' nets=(\d+) invalid=(\d+)")
_VAL_ERR_RE = re.compile(r"VALIDATE '([^']*)' ERR=(\S+)")


def done(log):
    """脚本是否跑完整。半截日志（软件中途崩了/超时）必须判失败。"""
    return "__DONE__" in log


def imports(log):
    """{AWL 路径: 是否 ret=0}。路径按 normcase 归一，Windows 大小写不敏感。"""
    return {os.path.normcase(p): (r == "0") for p, r in _IMPORT_RE.findall(log)}


def imports_ok(log, awl_files):
    got = imports(log)
    return bool(got) and all(got.get(os.path.normcase(f), False) for f in awl_files)


def compiled(log):
    m = _COMPILE_RE.search(log)
    return m is not None and m.group(1) == "0"


def exports(log):
    """{输出路径: 是否 ret=0}"""
    return {p.strip(): (r == "0") for _, p, r in _EXPORT_RE.findall(log)}


def validation(log):
    """引擎自身的"无效程序段"判定（POU_IsValidNet 真值）。

    返回 {块名: {"nets": 网络总数, "invalid": [网络号...], "error": 出错原因或 None}}
    网络号已换算成 AWL 里的 1 起编号（引擎内部是 0 起）。
    没有 summary 行 = 那个块根本没验成 → error="未完成"，不能当通过。
    """
    out = {}
    pending = {}
    for name, net, kind in _VAL_BAD_RE.findall(log):
        pending.setdefault(name, []).append(int(net))
    for name, reason in _VAL_ERR_RE.findall(log):
        out[name] = {"nets": None, "invalid": [], "error": reason}
    for name, nets, bad in _VAL_SUM_RE.findall(log):
        out[name] = {"nets": int(nets), "invalid": sorted(pending.get(name, [])),
                     "error": None}
    return out


def validation_ok(log, block_names):
    """所有指定块都验过、且都零无效网络，才算过。缺一个块也算不过。"""
    v = validation(log)
    for b in block_names:
        r = v.get(b)
        if r is None or r["error"] or r["invalid"]:
            return False
    return True


def ret_lines(log):
    return [l for l in log.splitlines() if "ret=" in l or "INVALID" in l or "ERR" in l]
