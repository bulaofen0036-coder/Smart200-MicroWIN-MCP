# -*- coding: utf-8 -*-
"""AWL 编码/行尾自动规范化的防回归测试（纯离线，不需要 MicroWIN）。

护的是 2026-08-25 实测到的两个坑：

1. 引擎的 IMPORTPOU 按【系统 ANSI 代码页】读文件。直接喂 UTF-8 会 ret=0
   （又一个"报成功"），但块名导进去是乱码，随后按中文块名找就是"块未找到"。

2. 探测编码必须【先 UTF-8 再 ANSI】，反过来会出事：
   GBK 几乎接受任意字节，UTF-8 的中文常被它静默解成乱码而不报错
   （"ASCII↔HEX" 的 UTF-8 字节被 GBK 解成 "ASCII鈫揌EX"，一声不吭）。
   反方向很安全 —— 真实 GBK 块文件用严格 UTF-8 解码全部正确拒绝、零误判。
   这个顺序错了，会把 UTF-8 文件误判成"已经是 ANSI"而原样交给引擎。
"""
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from smart200_mcp import autoflow   # noqa: E402

fails = []
CRLF = bytes((13, 10))


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  " + str(extra)))
    if not cond:
        fails.append(name)


D = tempfile.mkdtemp(prefix="smart200_enc_")


def w(name, text, enc, newline=None):
    p = os.path.join(D, name)
    data = text.encode(enc) if newline is None else text.replace("\n", newline).encode(enc)
    with open(p, "wb") as f:
        f.write(data)
    return p


BLOCK = "SUBROUTINE_BLOCK 系统初始化:SBR0\nBEGIN\nNetwork 1\n\tLD     SM0.0\nEND_SUBROUTINE_BLOCK\n"

print("=== 编码探测顺序（先 UTF-8 再 ANSI）===")
u8 = w("u8.awl", BLOCK, "utf-8", "\n")
gbk = w("gbk.awl", BLOCK, "mbcs", "\r\n")
check("UTF-8 文件读出正确块名", "系统初始化" in autoflow._read(u8))
check("GBK 文件读出正确块名", "系统初始化" in autoflow._read(gbk))
# 反向哨兵：顺序若反了，这条会读出乱码
check("UTF-8 没被当成 GBK 读成乱码", "鈫" not in autoflow._read(u8) and "锟" not in autoflow._read(u8))
bom = w("bom.awl", "﻿" + BLOCK, "utf-8", "\n")
check("带 BOM 的 UTF-8 也能读", autoflow._read(bom).startswith("SUBROUTINE_BLOCK"))

print("=== 交给引擎前的规范化 ===")
out = autoflow._engine_ready_awl(u8, D, 0)
check("UTF-8 会被转码", out != u8)
raw = open(out, "rb").read()
check("转码后块名在 ANSI 下正确", "系统初始化" in raw.decode("mbcs"))
check("转码后行尾是 CRLF", CRLF in raw)
check("已是 ANSI+CRLF 的不做多余拷贝", autoflow._engine_ready_awl(gbk, D, 1) == gbk)

ascii_lf = w("a.awl", "SUBROUTINE_BLOCK Plain:SBR2\nBEGIN\nEND_SUBROUTINE_BLOCK\n", "ascii", "\n")
o = autoflow._engine_ready_awl(ascii_lf, D, 2)
check("纯 ASCII 的 LF 会被补成 CRLF", o != ascii_lf and CRLF in open(o, "rb").read())

print("=== 反向哨兵：GBK 表示不了的字符必须报错，不许静默替换成 ? ===")
bad = w("bad.awl", "SUBROUTINE_BLOCK 测试:SBR0\nBEGIN\nNetwork 1\n//ASCII↔HEX\n\tLD SM0.0\nEND_SUBROUTINE_BLOCK\n", "utf-8", "\n")
try:
    autoflow._engine_ready_awl(bad, D, 3)
    check("抓到 GBK 表示不了的字符", False, "放过了 —— 会静默丢字符")
except autoflow.FlowError as e:
    msg = str(e)
    check("抓到 GBK 表示不了的字符", True)
    # 报错要能直接定位：行号 + 具体字符 + 码位
    check("报错给出了行号", "第 4 行" in msg, msg)
    check("报错指出了具体字符", "↔" in msg, msg)
    check("报错给出了码位", "U+2194" in msg, msg)

print("")
print("全部通过" if not fails else str(len(fails)) + " 项失败: " + str(fails))
sys.exit(1 if fails else 0)
