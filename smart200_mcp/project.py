"""工程内容分析。

能力边界（务必如实向调用方说明）：
  可靠 —— 版本、项目名、用户符号、用户网络注释、功能框使用统计
  不支持 —— 逐网络还原 LAD/STL 逻辑。容器内网络记录的定长结构尚未逆向完成，
            强行输出等于编造。要完整程序请走 UI 自动化导出 .awl。

用户内容 / 系统内置内容的切分：容器末尾是西门子固定的系统符号表
（SM 位符号及其说明，各工程稳定约 506 条）。以段标题 "系统符号" 的偏移为锚点，
锚点之前算用户内容。已在 9/9 个真实工程上验证锚点均命中。
"""

import re
from collections import Counter

from . import strings

# 功能框名：带下划线的大写助记符（MOV_DW / MUL_DI / WAND_W / EXTERN_RESET ...）
# 直接从真实工程数据匹配，不依赖任何硬编码指令表。
_FB_RE = re.compile(rb"[A-Z][A-Z0-9]{1,9}_[A-Z0-9]{1,8}")
_SYM_RE = re.compile(r"[A-Za-z_一-龥][\w一-龥]{1,63}$")
_META = {"程序块", "程序段注释", "系统符号", "表格 1", "POU Symbols"}
_SYSTEM_ANCHOR = "系统符号"


def _split(proj):
    """返回 (用户段文本, 系统段文本)。锚点缺失时全部算用户段并在 info 中标注。"""
    ex = strings.extract(proj.data)
    anchor = next((off for off, _, t in ex if t == _SYSTEM_ANCHOR), None)
    if anchor is None:
        return [t for _, _, t in ex], []
    user = [t for off, _, t in ex if off < anchor]
    system = [t for off, _, t in ex if off >= anchor]
    return user, system


def _dedup(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def info(proj):
    user, system = _split(proj)
    name = next((t for t in user if not t.startswith("V0") and len(t) > 1
                 and t not in _META and "%" not in t), None)
    return {
        "path": proj.path,
        "container_version": proj.version,
        "internal_version": next((t for t in user if t.startswith("V0")), None),
        "project_name": name,  # 工程内部名，可能与文件名不同（工程另存后不同步）
        "decompressed_bytes": len(proj.data),
        "system_table_found": bool(system),
    }


def symbols(proj):
    """用户定义的符号名（不含西门子系统 SM 符号）。"""
    user, _ = _split(proj)
    name = info(proj)["project_name"]
    out = [t for t in _dedup(user)
           if t not in _META and t != name and not t.startswith("V0")
           and _SYM_RE.match(t) and not re.fullmatch(r"[A-Z]{2,4}", t)]
    return sorted(out)


def pou_names(proj):
    """用户 POU（子程序/中断程序）名称。

    已用 UIA 项目树在真实工程上交叉验证：4/4 命中
    （初始化 SBR0 / 并行查询读地址 SBR16 / 地址判定 SBR17 / 中址锁存 SBR18）。
    系统默认名的 OB1、FB_0 不在其中。
    """
    user, _ = _split(proj)
    name = info(proj)["project_name"]
    out = []
    for t in _dedup(user):
        if t in _META or t == name or not re.search(r"[一-龥]", t):
            continue
        if re.fullmatch(r"[A-Za-z_]+[\w一-龥]*", t):
            continue  # CPU_输入0 这类算符号
        out.append(t)
    return sorted(out)


def function_blocks(proj):
    """功能框使用直方图，按出现次数降序。"""
    names = [m.group().decode("ascii") for m in _FB_RE.finditer(proj.data)]
    return Counter(names).most_common()


def summary(proj):
    fb = function_blocks(proj)
    return {
        **info(proj),
        "symbol_count": len(symbols(proj)),
        "pou_names": pou_names(proj),
        "function_block_kinds": len(fb),
        "function_block_total": sum(n for _, n in fb),
        "top_function_blocks": fb[:15],
        "limitations": (
            "未还原逐网络 LAD/STL 逻辑；如需完整程序请用 UI 自动化导出 .awl。"
            "CPU 型号离线不可靠（工程存模块 ID 而非型号串），需经 ui.read_project_tree 读取。"
        ),
    }
