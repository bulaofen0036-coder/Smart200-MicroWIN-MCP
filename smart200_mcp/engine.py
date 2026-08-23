"""进程内引擎调用编排 —— 脚本模式（稳定）。

核心：一次注入执行一整条脚本（导出多个块 + 编译 + 保存），全部在【主线程】顺序完成。
这是唯一稳定的方式 —— 多次注入/跨进程触发都会崩，一次注入干一整批则稳。

已实测（2026-08-20，V3.2）：脚本内 EXPORT×N + COMPILE + SAVE 一次跑通，
导出的 AWL 与手动导出逐字节一致。

红线：只对【自己启动的独立实例】注入，绝不注入用户正在编辑的进程。
"""

import os
import subprocess
import time

BASE = r"E:\Smart200_Mcp\native\bootstrap"
INJECTOR = r"E:\Smart200_Mcp\native\injector\bin\Release\net8.0\win-x86\injector.exe"
DLL = os.path.join(BASE, "smarthook_WORKING.dll")
CMD_FILE = os.path.join(BASE, "inject_cmd.txt")
RESULT_FILE = os.path.join(BASE, "inject_result.txt")
MWSMART = r"D:\smart200\MWSmartV3.exe"

USER_PROTECTED_PIDS = set()


class EngineError(Exception):
    pass


def _read_result():
    if not os.path.exists(RESULT_FILE):
        return ""
    with open(RESULT_FILE, "rb") as f:
        return f.read().decode("utf-8", "replace")


def _wait_done(timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        log = _read_result()
        if "__DONE__" in log:
            return log
        time.sleep(0.4)
    return _read_result()


def launch_instance(project_path, wait=26):
    """启动【独立】实例载入工程，返回 PID。"""
    p = subprocess.Popen([MWSMART, project_path])
    time.sleep(wait)
    if p.poll() is not None:
        raise EngineError(f"实例启动后立即退出：{project_path}")
    return p.pid


def kill_instance(pid):
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)


def run_script(pid, commands):
    """一次注入执行整条脚本。commands 是子命令行列表，如：
        ["EXPORT 初始化|E:\\out\\a.awl", "XML 地址判定|E:\\out\\b.xml", "COMPILE", "SAVE"]
    返回结果日志。这是全部动作的唯一入口。
    """
    if pid in USER_PROTECTED_PIDS:
        raise EngineError(f"PID {pid} 是用户正在编辑的实例，拒绝注入")
    if not os.path.exists(DLL):
        raise EngineError(f"找不到引擎 DLL: {DLL}")
    # 防护：命令里出现控制字符 = 上游路径拼接时反斜杠转义塌缩（如 \f \n \t），
    # 会让软件写到错误路径、导出静默失败。宁可早报错。
    for c in commands:
        bad = [hex(ord(ch)) for ch in c if ord(ch) < 0x20 and ch not in "\t"]
        if bad:
            raise EngineError(f"命令含控制字符 {bad}，疑似路径转义塌缩：{c!r}。"
                              f"请用原始字符串 r'...' 或 os.path.join 构造路径。")
    lines = ["script"] + commands
    body = ("\r\n".join(lines) + "\r\n").encode("gbk")
    if os.path.exists(RESULT_FILE):
        os.remove(RESULT_FILE)
    with open(CMD_FILE, "wb") as f:
        f.write(body)
    subprocess.run([INJECTOR, str(pid), DLL], capture_output=True, text=True, timeout=60)
    log = _wait_done()
    if "g_Retrieve=" not in log:
        raise EngineError(f"脚本未触发：\n{log}")
    return log


# ---- 高层：从工程路径一步到位（自动起实例、跑脚本、关实例）----

def _run_on_project(project_path, commands, keep_open=False):
    pid = launch_instance(project_path)
    try:
        log = run_script(pid, commands)
        return log, pid
    finally:
        if not keep_open:
            kill_instance(pid)


def export_blocks(project_path, names_to_paths):
    """从工程导出若干块为 AWL。names_to_paths = {块名: 输出路径}。返回 {路径: 是否成功}。"""
    for p in names_to_paths.values():
        if os.path.exists(p):
            os.remove(p)
    cmds = [f"EXPORT {n}|{p}" for n, p in names_to_paths.items()]
    log, _ = _run_on_project(project_path, cmds)
    return {p: os.path.exists(p) for p in names_to_paths.values()}, log


def compile_and_export(project_path, names_to_paths):
    """编译工程 + 导出若干块（一次注入）。返回 (编译成功, {路径:是否导出}, 日志)。"""
    for p in names_to_paths.values():
        if os.path.exists(p):
            os.remove(p)
    cmds = ["COMPILE"] + [f"EXPORT {n}|{p}" for n, p in names_to_paths.items()]
    log, _ = _run_on_project(project_path, cmds)
    compiled = "COMPILE ret=0" in log
    return compiled, {p: os.path.exists(p) for p in names_to_paths.values()}, log


def import_and_compile(project_path, awl_files, save=True):
    """导入若干 AWL 程序块到工程并编译（一次注入）。已闭环验证：导入的改动真实落进工程。

    awl_files: AWL 文件路径列表。返回 (每个是否 ret=0 dict, 编译成功, 日志)。
    ⚠ 会修改工程（save=True 时落盘）。project_path 应是可写的目标工程（副本或新工程）。
    """
    cmds = [f"IMPORTPOU {p}" for p in awl_files] + ["COMPILE"]
    if save:
        cmds.append("SAVE")
    log, _ = _run_on_project(project_path, cmds)
    imported = {p: (f"IMPORTPOU {p} ret=0" in log) for p in awl_files}
    compiled = "COMPILE ret=0" in log
    return imported, compiled, log
