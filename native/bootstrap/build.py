# -*- coding: utf-8 -*-
"""编译注入用的 smarthook DLL。

为什么用 Python 而不是纯 .bat：工具链探测要处理 `%ProgramFiles(x86)%` 这种
带括号的变量名，在 cmd 的延迟展开里极易把解析搞乱（试过，报一堆莫名其妙的错）。
Python 本来就是本项目的依赖，用它做探测简单可靠。

用法：python build.py
      set VCVARS=<vcvars32.bat 路径> 可覆盖自动探测
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "smarthook.cpp"
WORK = "smarthook_WORKING.cpp"
OUT = "smarthook_WORKING.dll"


def find_vcvars():
    env = os.environ.get("VCVARS")
    if env and os.path.exists(env):
        return env

    # vswhere 是 VS 官方的定位工具，装了 VS/BuildTools 就有
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if not base:
            continue
        vswhere = os.path.join(base, "Microsoft Visual Studio", "Installer", "vswhere.exe")
        if not os.path.exists(vswhere):
            continue
        try:
            out = subprocess.run([vswhere, "-latest", "-products", "*",
                                  "-property", "installationPath"],
                                 capture_output=True, timeout=30).stdout
            path = out.decode("mbcs", "replace").strip().splitlines()
            if path:
                cand = os.path.join(path[0], "VC", "Auxiliary", "Build", "vcvars32.bat")
                if os.path.exists(cand):
                    return cand
        except Exception:
            pass

    # vswhere 不在时的常见位置
    tails = os.path.join("VC", "Auxiliary", "Build", "vcvars32.bat")
    guesses = []
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if base:
            for ed in ("BuildTools", "Community", "Professional", "Enterprise"):
                for ver in ("2022", "2019"):
                    guesses.append(os.path.join(base, "Microsoft Visual Studio", ver, ed, tails))
    for drive in ("C:", "D:", "E:"):
        guesses.append(os.path.join(drive + os.sep, "BuildTools", tails))
    for g in guesses:
        if os.path.exists(g):
            return g
    return None


def main():
    vcvars = find_vcvars()
    if not vcvars:
        print("[错误] 找不到 vcvars32.bat。请安装 Visual Studio Build Tools 的 "
              "「使用 C++ 的桌面开发」(需含 x86 工具集)，或设环境变量 VCVARS 指向它。")
        return 1
    print("[工具链] " + vcvars)

    # 两份源码必须一致 —— 改了 smarthook.cpp 却编 _WORKING 那份是踩过的坑
    shutil.copyfile(os.path.join(HERE, SRC), os.path.join(HERE, WORK))

    cl = ("cl /nologo /LD /O2 /EHsc /std:c++17 /utf-8 /MT {work} "
          "/Fe:{out} /link user32.lib").format(work=WORK, out=OUT)
    # 注意用 shell=True 传整串：写成 ["cmd","/c", cmd] 时 Python 会把内层引号再转义一层，
    # cmd 收到的是 \"D:\...\vcvars32.bat\"，直接报“不是内部或外部命令”。
    cmd = 'call "{vc}" >nul && {cl}'.format(vc=vcvars, cl=cl)
    r = subprocess.run(cmd, cwd=HERE, capture_output=True, shell=True)
    log = (r.stdout + r.stderr).decode("mbcs", "replace")
    bad = [l for l in log.splitlines() if ": error" in l or ": fatal" in l]
    if r.returncode != 0 or bad:
        print("[错误] 编译失败：")
        for l in (bad or log.splitlines()[-10:]):
            print("   " + l)
        return 1

    dll = os.path.join(HERE, OUT)
    print("[OK] %s  %d 字节" % (OUT, os.path.getsize(dll)))
    # 自检：DLL 里不该再有任何写死的仓库路径（路径改成运行时自寻了）
    data = open(dll, "rb").read()
    for probe in (HERE.encode("mbcs", "replace"), b"inject_result.txt"):
        pass
    if HERE.encode("mbcs", "replace") in data:
        print("[警告] DLL 里出现了本机路径，自寻路径可能没生效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
