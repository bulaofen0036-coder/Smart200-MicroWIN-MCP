"""长度前缀字符串提取。

逆向实测（docs/FORMAT.md）：容器内混用两种字节序的 u16 长度前缀 ——
工程数据段用 big-endian，系统符号资源段用 little-endian。
指令助记符不带长度前缀，另行处理（见 project.instruction_histogram）。
文本为 GBK 编码（中文注释）。
"""

import re

# 软件的文本用系统 ANSI 代码页（中文机器上就是 GBK）；写死 gbk 在非中文系统会崩
ANSI = "mbcs"

MIN_LEN = 2
MAX_LEN = 200
# 允许的正文：可打印 ASCII + GBK 双字节区
_ASCII_OK = set(range(0x20, 0x7F))


def _decode(chunk):
    """chunk 能否解成一条像样的文本？不能则返回 None。"""
    if all(b in _ASCII_OK for b in chunk):
        return chunk.decode("ascii")
    try:
        text = chunk.decode(ANSI)
    except UnicodeDecodeError:
        return None
    # 允许中文、ASCII 可打印、常见全角标点；其余判为二进制误报
    if re.fullmatch(r"[一-龥　-〿＀-￯\x20-\x7e]+", text):
        return text
    return None


def extract(data, min_len=MIN_LEN, max_len=MAX_LEN):
    """扫出全部长度前缀字符串。

    返回 [(offset, endian, text)]，按 offset 排序，重叠区段只取最先命中的一条。
    """
    out = []
    n = len(data)
    i = 0
    while i < n - 2:
        for endian in ("big", "little"):
            length = int.from_bytes(data[i:i + 2], endian)
            if not (min_len <= length <= max_len) or i + 2 + length > n:
                continue
            text = _decode(data[i + 2:i + 2 + length])
            if text is None:
                continue
            out.append((i, endian, text))
            i += 2 + length
            break
        else:
            i += 1
            continue
    return out


def texts(data, **kw):
    """只要文本，去重保序。"""
    seen, out = set(), []
    for _, _, t in extract(data, **kw):
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out
