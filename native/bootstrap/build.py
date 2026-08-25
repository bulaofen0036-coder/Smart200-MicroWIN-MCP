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
    force = "--force" in sys.argv
    vcvars = find_vcvars()
    if not vcvars:
        print("[错误] 找不到 vcvars32.bat。请安装 Visual Studio Build Tools 的 "
              "「使用 C++ 的桌面开发」(需含 x86 工具集)，或设环境变量 VCVARS 指向它。")
        return 1
    print("[工具链] " + vcvars)

    # 两份源码必须一致。SRC(smarthook.cpp) 是【权威源】，WORK(_WORKING.cpp) 是它的副本。
    #
    # ⚠ 这里以前是无条件 copyfile，会【静默抹掉】改在 WORK 上的修改：
    #   文件名叫 "_WORKING" 反而像"正在用的那份"，很容易改错地方，
    #   然后编出来的 DLL 功能没变、字节数还分毫不差，极难看出问题（踩过，2026-08-25）。
    # 现在改成：内容不一致就停下来问，不替人做决定。
    src_p, work_p = os.path.join(HERE, SRC), os.path.join(HERE, WORK)
    if os.path.exists(work_p):
        a = open(src_p, "rb").read()
        b = open(work_p, "rb").read()
        if a != b and os.path.getmtime(work_p) > os.path.getmtime(src_p) and not force:
            print("[停止] %s 比 %s 新且内容不同 —— 你多半改错了文件。" % (WORK, SRC))
            print("        %s 是【权威源】，%s 只是它的副本，编译前会被覆盖。" % (SRC, WORK))
            print("        要保留 %s 上的改动，先把它拷回 %s：" % (WORK, SRC))
            print("            copy /Y %s %s" % (WORK, SRC))
            print("        确认要丢弃 %s 的改动，就加 --force 重跑。" % WORK)
            return 1
    shutil.copyfile(src_p, work_p)

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
