"""在线通讯层（snap7 / S7 协议，TCP 102）。

S7-200 SMART 的 V 存储区在 S7 协议上映射为 DB1，这是本层全部读写的基础。
连接参数固定 rack=0, slot=1。

⚠ 状态：本模块尚未在真机 S7-200 SMART 上验证过（开发时手边无 CPU）。
   代码按协议规范编写，首次接真机请先用 cpu_info() 试探，勿直接写值。
"""

import re

import snap7

V_DB_NUMBER = 1  # S7-200 SMART: V 区 == DB1

_ADDR_RE = re.compile(
    r"^(?P<area>VB|VW|VD|MB|MW|MD|IB|IW|ID|QB|QW|QD|V|M|I|Q)"
    r"(?P<byte>\d+)(?:\.(?P<bit>[0-7]))?$", re.I)

_SIZE = {"B": 1, "W": 2, "D": 4}


class OnlineError(Exception):
    pass


def parse_address(addr):
    """'VW100' / 'V10.3' / 'M0.0' -> (area, byte, bit, size)。非法地址抛异常。"""
    m = _ADDR_RE.match(addr.strip())
    if not m:
        raise OnlineError(f"无法解析地址 {addr!r}（支持 VB/VW/VD/MB/MW/MD/IB/IW/ID/QB/QW/QD 与位地址如 V10.3）")
    area = m.group("area").upper()
    byte = int(m.group("byte"))
    bit = m.group("bit")
    bit = int(bit) if bit is not None else None
    if len(area) == 2:
        if bit is not None:
            raise OnlineError(f"{addr}: 字节/字/双字地址不能带位号")
        return area[0], byte, None, _SIZE[area[1]]
    if bit is None:
        raise OnlineError(f"{addr}: 位地址需写成 {area}{byte}.0 形式")
    return area, byte, bit, 1


class Plc:
    """一次连接的上下文。用法：with Plc('192.168.2.1') as plc: ..."""

    def __init__(self, ip, rack=0, slot=1):
        self.ip, self.rack, self.slot = ip, rack, slot
        self._c = None

    def __enter__(self):
        self._c = snap7.client.Client()
        try:
            self._c.connect(self.ip, self.rack, self.slot)
        except Exception as e:
            raise OnlineError(f"连接 {self.ip} 失败: {e}") from e
        return self

    def __exit__(self, *exc):
        if self._c is not None:
            try:
                self._c.disconnect()
            finally:
                self._c = None

    def _area_read(self, area, byte, size):
        if area == "V":
            return self._c.db_read(V_DB_NUMBER, byte, size)
        area_map = {"M": snap7.type.Area.MK, "I": snap7.type.Area.PE,
                    "Q": snap7.type.Area.PA}
        return self._c.read_area(area_map[area], 0, byte, size)

    def _area_write(self, area, byte, payload):
        if area == "V":
            return self._c.db_write(V_DB_NUMBER, byte, payload)
        area_map = {"M": snap7.type.Area.MK, "I": snap7.type.Area.PE,
                    "Q": snap7.type.Area.PA}
        return self._c.write_area(area_map[area], 0, byte, payload)

    def cpu_info(self):
        """型号、固件、运行状态。接真机时先跑这个。"""
        info = self._c.get_cpu_info()
        return {
            "ip": self.ip,
            "module_type": info.ModuleTypeName.decode("latin1", "ignore").strip("\x00"),
            "serial": info.SerialNumber.decode("latin1", "ignore").strip("\x00"),
            "as_name": info.ASName.decode("latin1", "ignore").strip("\x00"),
            "module": info.ModuleName.decode("latin1", "ignore").strip("\x00"),
            "cpu_state": self._c.get_cpu_state(),
        }

    def read(self, addr):
        """按地址读一个值。位地址返回 bool，其余返回无符号整数。"""
        area, byte, bit, size = parse_address(addr)
        raw = self._area_read(area, byte, size)
        if bit is not None:
            return bool(raw[0] >> bit & 1)
        return int.from_bytes(raw, "big")

    def read_many(self, addrs):
        return {a: self.read(a) for a in addrs}

    def write(self, addr, value):
        """按地址写一个值。位地址走读-改-写。"""
        area, byte, bit, size = parse_address(addr)
        if bit is not None:
            raw = bytearray(self._area_read(area, byte, 1))
            if value:
                raw[0] |= 1 << bit
            else:
                raw[0] &= ~(1 << bit) & 0xFF
            self._area_write(area, byte, bytes(raw))
        else:
            self._area_write(area, byte, int(value).to_bytes(size, "big"))
        return {"address": addr, "written": value}
