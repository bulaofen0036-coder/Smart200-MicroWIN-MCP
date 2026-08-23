"""进程内引擎调用编排 —— 脚本模式（稳定）。

核心：一次注入执行一整条脚本（导出多个块 + 编译 + 保存），全部在【主线程】顺序完成。
这是唯一稳定的方式 —— 多次注入/跨进程触发都会崩，一次注入干一整批则稳。

已实测（2026-08-20，V3.2）：脚本内 EXPORT×N + COMPILE + SAVE 一次跑通，
导出的 AWL 与手动导出逐字节一致。

红线：只对【自己启动的独立实例】注入，绝不注入用户正在编辑的进程。
"""

import atexit
import ctypes
import ctypes.wintypes as wintypes
import os
import subprocess
import threading
import time

from . import enginelog, paths

# 路径一律走 paths 模块（自动探测 + 环境变量/配置覆盖），这里不再有硬编码
BASE = paths.BOOTSTRAP
INJECTOR = paths.INJECTOR
DLL = paths.DLL
CMD_FILE = paths.CMD_FILE
RESULT_FILE = paths.RESULT_FILE

# 与软件交换文本用【系统 ANSI 代码页】，不是写死 GBK ——
# 写死 gbk 在非中文 Windows 上直接崩。简体中文机器上 mbcs 就是 GBK，行为不变。
ANSI = "mbcs"

# 命令/结果文件是【固定的单份】，两个操作同时跑会互相覆盖、日志串台，
# 而所有判据都读这个日志 —— 串台的结果长得跟正常结果一样。加锁串行化。
_INJECT_LOCK = threading.Lock()

# 红线的机器强制点：只允许注入【本模块自己启动的】实例。
# 以前这里是一个空的 USER_PROTECTED_PIDS 黑名单，从没人往里加 PID —— 等于没有防护。
# 改成白名单：不在这里面的 PID 一律拒绝，用户手上那个实例天然进不来。
_OWN_PIDS = set()


class EngineError(Exception):
    pass


@atexit.register
def _cleanup_own_instances():
    """进程退出时把自己起的实例收掉。

    不做这件事的话，MCP 服务一退，启动过的 MicroWIN 就留在后台 —— 实测漏过 4 个。
    只收自己起的（_OWN_PIDS），绝不按进程名批量杀。
    """
    for pid in list(_OWN_PIDS):
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, timeout=10)
        except Exception:
            pass
    _OWN_PIDS.clear()


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


# 一次注入的默认等待上限。大工程编译会久，可用环境变量调。
SCRIPT_TIMEOUT = int(os.environ.get("SMART200_SCRIPT_TIMEOUT", "180"))


def _wait_done(timeout=None):
    """等脚本跑完（日志出现 __DONE__）。

    超时【必须抛异常】：以前这里直接 return 半截日志，调用方拿它当正常结果用，
    等于把"软件中途崩了"伪装成"跑完了"。判据全建立在这个日志上，绝不能含糊。
    """
    timeout = SCRIPT_TIMEOUT if timeout is None else timeout
    deadline = time.time() + timeout
    while time.time() < deadline:
        log = _read_result()
        if "__DONE__" in log:
            return log
        time.sleep(0.4)
    log = _read_result()
    raise EngineError(
        "引擎脚本 %d 秒内没跑完（日志无 __DONE__）—— 软件可能中途崩了或这活确实久。"
        "大工程可设环境变量 SMART200_SCRIPT_TIMEOUT 放宽。已拿到的半截日志：%s"
        % (timeout, chr(10) + log[-800:] if log else "(空)"))


def launch_instance(project_path, timeout=90):
    """启动【独立】实例载入工程，等到工程真的载入后返回 PID。"""
    if not os.path.exists(project_path):
        raise EngineError(f"工程不存在：{project_path}")
    p = subprocess.Popen([paths.mwsmart(), project_path])
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
    """收掉自己起的实例。杀不掉时返回 False —— 不静默吞掉。"""
    try:
        r = subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, timeout=15)
        ok = (r.returncode == 0)
    except Exception:
        ok = False
    _OWN_PIDS.discard(pid)
    return ok


MAX_CMD_BYTES = 650      # C++ 侧逐行缓冲区 700 字节，留点余量


def validate_commands(commands):
    """下发前校验命令。单独一个函数是为了能脱离软件直接测。

    两类问题都会造成【静默走偏】，必须提前拦：
      · 控制字符 —— 上游路径拼接时反斜杠转义塌缩(\\f \\n \\t)，软件会写到错误路径还报成功
      · 超长     —— 引擎侧按 700 字节逐行读，超了直接截断，截断后的路径指向别处
    """
    for c in commands:
        bad = [hex(ord(ch)) for ch in c if ord(ch) < 0x20 and ch not in "\t"]
        if bad:
            raise EngineError(f"命令含控制字符 {bad}，疑似路径转义塌缩：{c!r}。"
                              f"请用原始字符串 r'...' 或 os.path.join 构造路径。")
        n = len(c.encode(ANSI, "replace"))
        if n > MAX_CMD_BYTES:
            raise EngineError(
                "命令过长（%d 字节，上限 %d）：%.80s…"
                "引擎侧按 700 字节缓冲逐行读，超了会静默截断。请把输出路径改短一些。"
                % (n, MAX_CMD_BYTES, c))
    return True


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
    validate_commands(commands)
    lines = ["script"] + commands
    body = ("\r\n".join(lines) + "\r\n").encode(ANSI, "replace")
    # 命令/结果文件全局只有一份，并发会串台 —— 整段注入串行化
    with _INJECT_LOCK:
        if os.path.exists(RESULT_FILE):
            os.remove(RESULT_FILE)
        with open(CMD_FILE, "wb") as f:
            f.write(body)
        # 注意别加 text=True：注入器输出是本地代码页，用默认解码会在读取线程里抛
        # UnicodeDecodeError（线程内异常，主流程看不见，只在 stderr 冒一堆栈）。
        try:
            subprocess.run([INJECTOR, str(pid), DLL], capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            raise EngineError("注入器 60 秒没返回，可能目标进程已经卡死")
        log = _wait_done()
    if "g_Retrieve=" not in log:
        raise EngineError(f"脚本未触发：\n{log}")
    return log


# ---- 高层：从工程路径一步到位（自动起实例、跑脚本、关实例）----

def _run_on_project(project_path, commands, keep_open=False):
    """跑一条脚本并【确认真的跑完了】。

    以前这里不查 __DONE__，上层三个函数（export/compile/import）拿半截日志就下结论 ——
    软件中途崩掉会被当成"跑完了但没成功"，甚至更糟：崩之前已完成的那步让判据判成成功。
    """
    pid = launch_instance(project_path)
    try:
        log = run_script(pid, commands)
    finally:
        if not keep_open:
            kill_instance(pid)
    if not enginelog.done(log):
        raise EngineError("引擎脚本没跑完（日志无 __DONE__），结果不可信：" + chr(10) + log[-600:])
    return log, pid


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
