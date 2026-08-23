# -*- coding: utf-8 -*-
"""路径解析：仓库自身位置 + MicroWIN 安装位置，全部自动探测。

为什么单独一层：以前这些是写死的绝对路径（E:\\Smart200_Mcp、D:\\smart200），
别人 clone 到别的盘就跑不起来。现在的优先级是

    环境变量  >  .smart200_local.json  >  自动探测  >  报错说清楚缺什么

仓库内的东西（注入器、DLL）一律相对本文件定位，仓库可以随便搬。
"""

import glob
import os

from . import localcfg

# 仓库根 = 本文件的上两层
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BOOTSTRAP = os.path.join(ROOT, "native", "bootstrap")
DLL = os.path.join(BOOTSTRAP, "smarthook_WORKING.dll")
CMD_FILE = os.path.join(BOOTSTRAP, "inject_cmd.txt")
RESULT_FILE = os.path.join(BOOTSTRAP, "inject_result.txt")
INJECTOR = os.path.join(ROOT, "native", "injector", "bin", "Release",
                        "net8.0", "win-x86", "injector.exe")


class PathError(Exception):
    pass


def _from_env_or_cfg(env_name, cfg_key):
    v = os.environ.get(env_name)
    if v and os.path.exists(v):
        return v
    v = localcfg.get(cfg_key)
    if v and os.path.exists(v):
        return v
    return None


def _scan_install():
    """找 MicroWIN SMART 的安装目录（含 MWSmartV3.exe）。"""
    pats = []
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if base:
            pats.append(os.path.join(base, "Siemens", "*", "MWSmartV3.exe"))
            pats.append(os.path.join(base, "*Micro*WIN*", "MWSmartV3.exe"))
    for drive in ("C:", "D:", "E:", "F:"):
        pats.append(os.path.join(drive + os.sep, "smart200", "MWSmartV3.exe"))
        pats.append(os.path.join(drive + os.sep, "*Micro*WIN*", "MWSmartV3.exe"))
        pats.append(os.path.join(drive + os.sep, "Siemens", "*", "MWSmartV3.exe"))
    for p in pats:
        hits = sorted(glob.glob(p))
        if hits:
            return hits[0]
    return None


def mwsmart():
    """MWSmartV3.exe 的完整路径。找不到就抛异常并说清怎么配。"""
    v = _from_env_or_cfg("SMART200_EXE", "mwsmart_exe")
    if v:
        return v
    v = _scan_install()
    if v:
        return v
    raise PathError(
        "找不到 MWSmartV3.exe。请任选一种方式指明：\n"
        "  1) 设环境变量 SMART200_EXE=<完整路径>\n"
        "  2) 在仓库根的 .smart200_local.json 里加 \"mwsmart_exe\": \"<完整路径>\"\n"
        "（已自动找过 Program Files 下的 Siemens/MicroWIN 目录，和各盘根的 smart200\\）")


def blank_template():
    """软件自带的空白模板工程（建新工程用）。找不到返回 None，调用方自己决定怎么办。"""
    v = _from_env_or_cfg("SMART200_TEMPLATE", "blank_template")
    if v:
        return v
    try:
        d = os.path.dirname(mwsmart())
    except PathError:
        return None
    for name in ("template.smartV3", "template.smart"):
        cand = os.path.join(d, name)
        if os.path.exists(cand):
            return cand
    return None


def check(require_injector=True):
    """开工前自检：缺什么一次说清，别等注入到一半才炸。"""
    missing = []
    if not os.path.exists(DLL):
        missing.append("引擎 DLL 不存在：%s\n     → 跑 python native/bootstrap/build.py 编译" % DLL)
    if require_injector and not os.path.exists(INJECTOR):
        missing.append("注入器不存在：%s\n     → 在 native/injector 下 dotnet publish -c Release -r win-x86" % INJECTOR)
    try:
        mwsmart()
    except PathError as e:
        missing.append(str(e))
    return missing
