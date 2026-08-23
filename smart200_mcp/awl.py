"""AWL/STL 文本解析与程序验证。

AWL 是 STEP 7-Micro/WIN SMART 的程序块导出文本格式（GBK 编码，CRLF）。
样本见 docs/AWL_FORMAT.md。这是软件「文件→导出→程序块」产出的格式，
也是注入引擎 PRJ_ExportPOU 的产出格式 —— 两条路殊途同归。

结构：
    <BLOCK_KIND> <名称>:<ID>          # SUBROUTINE_BLOCK 初始化:SBR0
    TITLE=<块注释>
    BEGIN
    Network <n>                       # 一个网络（rung）
    //<网络注释>
        <助记符>  <操作数>[, <操作数>...]
    ...
    END_<BLOCK_KIND>
"""

import re

_BLOCK_HEAD = re.compile(r"^(SUBROUTINE_BLOCK|PROGRAM_BLOCK|ORGANIZATION_BLOCK|INTERRUPT_BLOCK|DATA_BLOCK)\s+(.+?):(\w+)\s*$")
_BLOCK_END = re.compile(r"^END_(SUBROUTINE_BLOCK|PROGRAM_BLOCK|ORGANIZATION_BLOCK|INTERRUPT_BLOCK|DATA_BLOCK)\s*$")
_NETWORK = re.compile(r"^Network\s+(\d+)\s*$")
_INSTR = re.compile(r"^[\t ]+([A-Z][A-Z0-9=]*)\b\s*(.*)$")
# 操作数里的地址（V/M/I/Q/SM + 字节/位）
_ADDR = re.compile(r"\b(V[BWD]?\d+(?:\.\d)?|[MIQ][BWD]?\d+(?:\.\d)?|SM[BWD]?\d+(?:\.\d)?|VD\d+|T\d+|C\d+|HC\d+|AC\d+)\b")


def parse(text):
    """解析 AWL 文本为结构化 dict。text 为已解码的字符串。"""
    lines = text.replace("\r\n", "\n").split("\n")
    block = {"kind": None, "name": None, "id": None, "title": None, "networks": []}
    cur = None
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        m = _BLOCK_HEAD.match(s)
        if m:
            block["kind"], block["name"], block["id"] = m.group(1), m.group(2), m.group(3)
            continue
        if _BLOCK_END.match(s):
            break
        if s.startswith("TITLE="):
            block["title"] = s[6:]
            continue
        if s == "BEGIN":
            continue
        m = _NETWORK.match(s)
        if m:
            cur = {"n": int(m.group(1)), "comment": None, "instructions": []}
            block["networks"].append(cur)
            continue
        if s.startswith("//"):
            if cur is not None and cur["comment"] is None:
                cur["comment"] = s[2:]
            continue
        m = _INSTR.match(raw)
        if m and cur is not None:
            op = m.group(1)
            operands = [x.strip() for x in m.group(2).split(",") if x.strip()] if m.group(2).strip() else []
            cur["instructions"].append({"op": op, "operands": operands})
    return block


def decode_bytes(data):
    """AWL 是 GBK 编码。去掉可能的 'SUB'(\\x1a) 尾字符。"""
    return data.decode("gbk", "replace")


def analyze(block):
    """程序验证/分析：地址用量、指令统计、可疑点。"""
    all_ops = []
    written, read = set(), set()
    for net in block["networks"]:
        for ins in net["instructions"]:
            all_ops.append(ins["op"])
            op = ins["op"]
            # 写类指令（MOV*/S/R/=/T*）第一个操作数常是源、最后是目标；粗略判定
            for a in ins["operands"]:
                for m in _ADDR.finditer(a):
                    addr = m.group(1)
                    if op.startswith(("MOV", "S", "R", "T", "=", "INC", "DEC", "ADD", "SUB", "MUL", "DIV")):
                        written.add(addr)
                    else:
                        read.add(addr)
    from collections import Counter
    return {
        "block": f"{block['name']} ({block['id']})",
        "kind": block["kind"],
        "network_count": len(block["networks"]),
        "instruction_count": len(all_ops),
        "instruction_histogram": Counter(all_ops).most_common(),
        "addresses_written": sorted(written),
        "addresses_read_only": sorted(read - written),
        "network_comments": [n["comment"] for n in block["networks"] if n["comment"]],
    }


def parse_file(path):
    with open(path, "rb") as f:
        return parse(decode_bytes(f.read()))
