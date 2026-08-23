"""S7-200 SMART 工程文件（.smart / .smartV3）容器层。

格式为逆向所得，依据见 docs/FORMAT.md：
  V2 (.smart)    头 b"SH3\\x00" + ASCII 版本串，0x70 起为 zlib 流 —— 可解包
  V3 (.smartV3)  头 4 字节 0 + ASCII 版本串，数据段加密（熵 7.99）—— 不可解包
"""

import zlib

MAGIC_V2 = b"SH3\x00"
ZLIB_OFFSET = 0x70


class UnsupportedProject(Exception):
    """工程无法离线解包（V3 加密或格式不识别）。"""


class SmartProject:
    """已解包的工程。data 为解压后的原始字节。"""

    def __init__(self, path, version, data):
        self.path = path
        self.version = version
        self.data = data


def probe(path):
    """只读探查，不解包。返回 dict：能否离线解析、为什么。"""
    with open(path, "rb") as f:
        head = f.read(0x30)
    version = head[4:20].split(b"\x00")[0].decode("latin1", "ignore")
    if head[:4] == MAGIC_V2:
        return {"path": str(path), "format": "V2", "version": version,
                "offline_parsable": True, "reason": "zlib 压缩，可解包"}
    if head[:4] == b"\x00\x00\x00\x00" and version.startswith("R03"):
        return {"path": str(path), "format": "V3", "version": version,
                "offline_parsable": False,
                "reason": "V3 工程数据段为加密内容（熵 7.99，无压缩魔数），"
                          "离线无法解析；请改用 UI 自动化导出 .awl"}
    return {"path": str(path), "format": "unknown", "version": version,
            "offline_parsable": False, "reason": "未识别的文件头"}


def load(path):
    """解包工程。V3 或损坏文件抛 UnsupportedProject（绝不静默返回空）。"""
    info = probe(path)
    if not info["offline_parsable"]:
        raise UnsupportedProject(f"{path}: {info['reason']}")

    with open(path, "rb") as f:
        raw = f.read()
    try:
        data = zlib.decompressobj().decompress(raw[ZLIB_OFFSET:])
    except zlib.error as e:
        raise UnsupportedProject(f"{path}: zlib 解压失败 ({e})") from e
    if not data:
        raise UnsupportedProject(f"{path}: 解压结果为空")
    return SmartProject(str(path), info["version"], data)
