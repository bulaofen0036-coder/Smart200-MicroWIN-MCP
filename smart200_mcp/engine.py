"""进程内引擎调用编排 —— 脚本模式（稳定）。

核心：一次注入执行一整条脚本（导出多个块 + 编译 + 保存），全部在【主线程】顺序完成。
这是唯一稳定的方式 —— 多次注入/跨进程触发都会崩，一次注入干一整批则稳。

已实测（2026-08-20，V3.2）：脚本内 EXPORT×N + COMPILE + SAVE 一次跑通，
导出的 AWL 与手动导出逐字节一致。

红线：只对【自己启动的独立实例】注入，绝不注入用户正在编辑的进程。
"""

import ctypes
import ctypes.wintypes as wintypes
import os
import subprocess
import time

from . import enginelog

BASE = r"E:\Smart200_Mcp\native\bootstrap"
INJECTOR = r"E:\Smart200_Mcp\native\injector\bin\Release\net8.0\win-x86\injector.exe"
DLL = os.path.join(BASE, "smarthook_WORKING.dll")
CMD_FILE = os.path.join(BASE, "inject_cmd.txt")
RESULT_FILE = os.path.join(BASE, "inject_result.txt")
MWSMART = r"D:\smart200\MWSmartV3.exe"

# 红线的机器强制点：只允许注入【本模块自己启动的】实例。
# 以前这里是一个空的 USER_PROTECTED_PIDS 黑名单，从没人往里加 PID —— 等于没有防护。
# 改成白名单：不在这里面的 PID 一律拒绝，用户手上那个实例天然进不来。
_OWN_PIDS = set()


class EngineError(Exception):
    pass


def _smartapp_title(pid):
    """取该 PID 的 SmartApp 主窗口标题；没有窗口返回 None。"""
    found = []

    def cb(h, _):
        p = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid:
            cls = ctypes.create_unicode_buffer(64)
            ctypes.windll.user32.GetClassNameW(h, cls, 63)
            if cls.value == "SmartApp":
                buf = ctypes.create_unicode_buffer(512)
                ctypes.windll.user32.GetWindowTextW(h, buf, 511)
                found.append(buf.value)
                return False
        return True

    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    ctypes.windll.user32.EnumWindows(CB(cb), 0)
    return found[0] if found else None


def wait_ready(pid, project_path, timeout=90, settle=1.5):
    """等实例把工程真的载入。

    判据：主窗口标题里出现工程主名（实测启动 ~0.8s 出窗口、~16s 标题才带工程名）。
    以前是死等 sleep(26)：慢机器上不够会注入到没载完的进程，快机器上白等 10 秒。

    坑：标题带不带扩展名不一致 —— `.smart` 显示成 "x.smart - STEP 7..."，
    而 `.smartV3` 显示成 "x - STEP 7..."。所以只认【去掉扩展名的主名】。
    """
    stem = os.path.splitext(os.path.basename(project_path))[0]
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = _smartapp_title(pid)
        if t and stem in t:
            time.sleep(settle)
            return time.time()
        time.sleep(0.3)
    raise EngineError(
        "等了 %ds 主窗口标题仍未出现工程名 %r（当前标题 %r）—— 工程可能没打开成功"
        % (timeout, stem, _smartapp_title(pid)))


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


def launch_instance(project_path, timeout=90):
    """启动【独立】实例载入工程，等到工程真的载入后返回 PID。"""
    if not os.path.exists(project_path):
        raise EngineError(f"工程不存在：{project_path}")
    p = subprocess.Popen([MWSMART, project_path])
    _OWN_PIDS.add(p.pid)
    try:
        wait_ready(p.pid, project_path, timeout=timeout)
    except EngineError:
        # 等不到就绪必须把自己起的进程收掉，否则每失败一次就漏一个实例在后台
        died = p.poll() is not None
        kill_instance(p.pid)
        if died:
            raise EngineError(f"实例启动后立即退出：{project_path}")
        raise
    return p.pid


def kill_instance(pid):
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    _OWN_PIDS.discard(pid)


def run_script(pid, commands):
    """一次注入执行整条脚本。commands 是子命令行列表，如：
        ["EXPORT 初始化|E:\\out\\a.awl", "XML 地址判定|E:\\out\\b.xml", "COMPILE", "SAVE"]
    返回结果日志。这是全部动作的唯一入口。
    """
    if pid not in _OWN_PIDS:
        raise EngineError(
            f"拒绝注入 PID {pid}：只允许注入本模块自己启动的实例。"
            f"用户正在编辑的实例绝不能被注入 —— 请先 launch_instance() 起一个独立实例。")
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
    # 注意别加 text=True：注入器输出是 GBK，用默认解码会在读取线程里抛
    # UnicodeDecodeError（线程内异常，主流程看不见，只在 stderr 冒一堆栈）。
    subprocess.run([INJECTOR, str(pid), DLL], capture_output=True, timeout=60)
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
    return enginelog.compiled(log), {p: os.path.exists(p) for p in names_to_paths.values()}, log


def import_and_compile(project_path, awl_files, save=True):
    """导入若干 AWL 程序块到工程并编译（一次注入）。已闭环验证：导入的改动真实落进工程。

    awl_files: AWL 文件路径列表。返回 (每个是否 ret=0 dict, 编译成功, 日志)。
    ⚠ 会修改工程（save=True 时落盘）。project_path 应是可写的目标工程（副本或新工程）。
    """
    cmds = [f"IMPORTPOU {p}" for p in awl_files] + ["COMPILE"]
    if save:
        cmds.append("SAVE")
    log, _ = _run_on_project(project_path, cmds)
    # 判据统一走 enginelog：日志里路径是【带引号】的（IMPORTPOU 'x.awl' ret=0），
    # 以前这里按无引号拼串匹配，导致导入成功也恒报 False。
    got = enginelog.imports(log)
    imported = {p: got.get(os.path.normcase(p), False) for p in awl_files}
    return imported, enginelog.compiled(log), log
