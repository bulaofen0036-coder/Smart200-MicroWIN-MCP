"""全自动流程：从 AWL 源文件到"工程里可用且验证过"的一条龙。

设计原则（血泪换来）：
  软件的 COMPILE ret=0 **不能单独作为通过判据** —— 无效程序段会被标红并排除在
  编译之外，其余照常编译，所以 ret=0 会骗人。必须三关都过：

    第1关 静态结构检查(stlcheck)  —— 抓"无效程序段"(一个网络多条 rung 等)
    第2关 导入 + 编译 ret=0        —— 抓语法/交叉引用错误
    第3关 往返导出 + 指令核对      —— 抓被软件静默丢弃的指令

  任何一关不过就如实报错，不吹"成功"。
"""

import os
import re
import shutil

from . import engine, localcfg, stlcheck

# 建新工程用的模板 .smart（本机私有路径，见 .smart200_local.json）
TEMPLATE = localcfg.get("template_project", "")


class FlowError(Exception):
    pass


def _block_name(awl_text):
    m = re.search(r"^(?:SUBROUTINE|PROGRAM|INTERRUPT)_BLOCK\s+(.+?):", awl_text, re.M)
    return m.group(1).strip() if m else None


def _ops(text):
    out = []
    for line in text.replace("\r\n", "\n").split("\n"):
        m = re.match(r"\t([A-Z][A-Z0-9_=<>+\-*/.]*)", line)
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


def _read(path):
    raw = open(path, "rb").read()
    try:
        return raw.decode("gbk")
    except UnicodeDecodeError:
        return raw.decode("utf-8", "replace")


def deploy(awl_files, project_path=None, template=None, open_after=False, verify_block=None):
    """把若干 AWL 块部署进一个工程并三关验证。

    awl_files: AWL 文件路径列表（有依赖关系时，被依赖的块排前面：
               中断程序/被调用子程序 要在引用它们的块之前导入）
    project_path: 目标工程（不存在则从 template 复制）
    verify_block: 用于第3关往返核对的块名（默认取最后一个文件的块名）
    返回 dict：三关结果 + 是否整体通过。
    """
    report = {"stage1_structure": None, "stage2_compile": None,
              "stage3_roundtrip": None, "passed": False, "detail": {}}

    # ---- 第1关：静态结构检查 ----
    bad = []
    for f in awl_files:
        r = stlcheck.check_file(f)
        if not r["valid"]:
            bad.append({"file": os.path.basename(f), "invalid": r["invalid"]})
    report["stage1_structure"] = "PASS" if not bad else "FAIL"
    report["detail"]["structure"] = bad
    if bad:
        return report

    # ---- 准备工程 ----
    if project_path is None:
        project_path = os.path.join(os.path.dirname(awl_files[0]), "_autoflow.smart")
    if not os.path.exists(project_path):
        shutil.copy(template or TEMPLATE, project_path)

    if verify_block is None:
        verify_block = _block_name(_read(awl_files[-1]))
    out_awl = os.path.join(os.path.dirname(project_path), "_verify_roundtrip.awl")
    if os.path.exists(out_awl):
        os.remove(out_awl)

    # ---- 第2关：导入 + 编译（一次注入） ----
    cmds = [f"IMPORTPOU {f}" for f in awl_files]
    cmds += ["COMPILE", f"EXPORT {verify_block}|{out_awl}", "SAVE"]
    pid = engine.launch_instance(project_path)
    try:
        log = engine.run_script(pid, cmds)
    finally:
        if not open_after:
            engine.kill_instance(pid)

    # 日志形如：script IMPORTPOU 'E:\...\x.awl' ret=0  —— 路径带引号，按文件名+ret 匹配
    import_rets = dict(re.findall(r"IMPORTPOU '([^']+)' ret=(-?\d+)", log))
    imports_ok = bool(import_rets) and all(
        any(os.path.normcase(k) == os.path.normcase(f) and v == "0"
            for k, v in import_rets.items())
        for f in awl_files)
    compile_ok = "COMPILE ret=0" in log
    report["stage2_compile"] = "PASS" if (imports_ok and compile_ok) else "FAIL"
    report["detail"]["log"] = [l for l in log.splitlines() if "ret=" in l]
    if not (imports_ok and compile_ok):
        return report

    # ---- 第3关：往返导出核对指令 ----
    if not os.path.exists(out_awl) or os.path.getsize(out_awl) == 0:
        report["stage3_roundtrip"] = "FAIL"
        report["detail"]["roundtrip"] = "导出为空 —— 块可能没真正建立"
        return report
    src_ops = _ops(_read(awl_files[-1]))
    back_ops = set(_ops(_read(out_awl)))
    missing = [o for o in src_ops if o not in back_ops]
    report["stage3_roundtrip"] = "PASS" if not missing else "FAIL"
    report["detail"]["roundtrip"] = {
        "instructions": len(src_ops),
        "missing": missing,
        "exported_bytes": os.path.getsize(out_awl),
    }
    report["passed"] = not missing
    report["project"] = project_path
    if open_after:
        report["opened_pid"] = pid
    return report


def open_project(project_path, foreground=True):
    """打开工程给人看（从磁盘加载，项目树才会正确显示新导入的块）。"""
    pid = engine.launch_instance(project_path)
    if foreground:
        try:
            import ctypes
            import ctypes.wintypes as w
            user32 = ctypes.windll.user32
            found = []

            def cb(h, _):
                p = w.DWORD()
                user32.GetWindowThreadProcessId(h, ctypes.byref(p))
                if p.value == pid:
                    buf = ctypes.create_unicode_buffer(64)
                    user32.GetClassNameW(h, buf, 63)
                    if buf.value == "SmartApp":
                        found.append(h)
                        return False
                return True

            CB = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            user32.EnumWindows(CB(cb), 0)
            if found:
                user32.ShowWindow(found[0], 3)
                user32.SetForegroundWindow(found[0])
        except Exception:
            pass
    return pid
