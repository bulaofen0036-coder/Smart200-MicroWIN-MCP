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

_SYM_OK_RE = re.compile(r"SYMSET '([^']*)' -> '([^']*)' 表=\S+ 行=(\d+) SetName=(-?\d+)")
_SYM_ERR_RE = re.compile(r"SYMSET '([^']*)' ERR=(\S+)")


def symbols(log):
    """符号设置结果：{地址: {"name":符号名, "row":行号, "ok":是否成功}}

    地址找不到对应的行时记 ok=False —— 绝不能当成功放过去，
    否则程序里的符号名解析不了，整个网络会变成无效程序段。
    """
    out = {}
    for addr, name, row, ret in _SYM_OK_RE.findall(log):
        out[addr.upper()] = {"name": name, "row": int(row), "ok": ret == "0"}
    for addr, why in _SYM_ERR_RE.findall(log):
        out[addr.upper()] = {"name": None, "row": None, "ok": False, "error": why}
    return out


def symbols_ok(log, wanted):
    """wanted 是 {符号名: 地址}。每个都要真的设上才算过。"""
    got = symbols(log)
    for name, addr in wanted.items():
        r = got.get(addr.upper())
        if not r or not r["ok"] or r["name"] != name:
            return False
    return True


_SYMDUMP_RE = re.compile(r"SYMDUMP ROW ([^|]*)\|([^|]*)\|([^|]*)(?:\|(.*))?$")
_SYMDUMP_TABLE_RE = re.compile(r"SYMDUMP TABLE=(\S+) rows=(\d+)")


def symbol_dump(log):
    """解析 SYMDUMP 的输出 → [{"name","address","type","comment","table"}]。

    引擎按 `SYMDUMP ROW 名字|地址|类型|注释` 一行一条打出来，
    每张表之前有一行 `SYMDUMP TABLE=<id> rows=N`，据此把行归到所属的表
    （I/O 变量表 / POU 名字表 / 系统变量表 …，靠表 id 区分来源）。

    注释放在最后一段：它可能含 `|`，所以最后一段吃掉剩余，不再切分。
    没有第 4 段时 comment 为空 —— 兼容旧格式，别因为格式演进就解析失败。

    单独成函数并单测 —— "解析日志下结论"的代码都要可测（enginelog 的引号 bug
    就是因为判据散在各处、零测试覆盖，活了很久没人发现）。
    """
    out = []
    table = None
    for line in log.splitlines():
        mt = _SYMDUMP_TABLE_RE.search(line)
        if mt:
            table = mt.group(1)
            continue
        m = _SYMDUMP_RE.search(line)
        if not m:
            continue
        name, addr, typ = (x.strip() for x in m.groups()[:3])
        comment = (m.group(4) or "").strip()
        if name and addr:
            out.append({"name": name, "address": addr, "type": typ,
                        "comment": comment, "table": table})
    return out
