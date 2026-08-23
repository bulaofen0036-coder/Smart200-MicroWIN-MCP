"""全自动流程：从 AWL 源文件到"工程里可用且验证过"的一条龙。

设计原则（血泪换来）：
  软件的 COMPILE ret=0 **不能作为通过判据** —— 无效程序段会被标红并排除在
  编译之外，其余照常编译，所以 ret=0 会骗人。必须四关都过：

    第1关 静态预检(stlcheck)   —— 不启动软件，秒级挡掉明显的无效程序段
    第2关 导入 + 编译 ret=0     —— 抓语法/交叉引用错误(CALL/ATCH 指向不存在的块)
    第3关 引擎真值(VALIDATE)   —— 逐网络问软件本人 POU_IsValidNet，这是权威判据
    第4关 往返导出 + 指令核对   —— 抓被软件静默丢弃的指令，【每个块各验各的】

  第3关才是权威：第1关是我写的启发式规则(可能有盲区)，第3关是软件自己的答案。
  两者在已知样本上一致（坏样本 9/9、好样本 0 误报），不一致时以第3关为准。

  任何一关不过就如实报错，不吹"成功"。
"""

import os
import re
import shutil
import tempfile

from . import engine, enginelog, localcfg, paths, stlcheck

# 软件的文本用系统 ANSI 代码页（中文机器上就是 GBK）；写死 gbk 在非中文系统会崩
ANSI = "mbcs"

# 建新工程用的模板：默认用【软件自带的空白模板】——干净、零客户数据。
# 以前默认是拿一个客户工程当模板，自动建出来的每个工程都带着客户的程序块。
# 想以某个已有工程为底，才在 .smart200_local.json 里配 template_project。
TEMPLATE = localcfg.get("template_project", "")


def _pick_template(explicit=None):
    for cand in (explicit, paths.blank_template(), TEMPLATE):
        if cand and os.path.exists(cand):
            return cand
    return None


def backup_project(project_path):
    """改工程之前先备份一份。返回备份路径；已有备份就不重复覆盖。

    为什么要机器强制：以前只在 docstring 里写"请用副本"，靠人自觉。
    一旦有人把客户原始工程传进来，改完就回不去了。
    """
    bak = project_path + ".bak"
    if not os.path.exists(bak):
        shutil.copy2(project_path, bak)
    return bak


class FlowError(Exception):
    pass


def _block_name(awl_text):
    m = re.search(r"^(?:SUBROUTINE|PROGRAM|ORGANIZATION|INTERRUPT)_BLOCK\s+(.+?):", awl_text, re.M)
    return m.group(1).strip() if m else None


def _section(text, name):
    """从 AWL 文本里切出【指定块】那一段。

    坑：PRJ_ExportPOU 的最后一个 bool 传 true 表示"连依赖一起导出"，
    所以导出的 .awl 里可能有好几个 BLOCK（目标块 + 它 CALL/ATCH 的块）。
    不切段直接数 Network，会把依赖块的网络算进来，误判成"网络数对不上"。
    """
    text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    head = re.compile(r"^(SUBROUTINE|PROGRAM|ORGANIZATION|INTERRUPT|DATA)_BLOCK\s+(.+?):")
    start = None
    for i, l in enumerate(lines):
        m = head.match(l.strip())
        if m and m.group(2).strip() == name:
            start = i
            kind = m.group(1)
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip() == "END_" + kind + "_BLOCK":
            end = j
            break
    return "\n".join(lines[start:end])


def _ops(text):
    out = []
    for line in text.replace("\r\n", "\n").split("\n"):
        m = re.match(r"\t([A-Z][A-Z0-9_=<>+\-*/.]*)", line)
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


def _net_count(text):
    return len(re.findall(r"^Network\s+\d+", text.replace("\r\n", "\n"), re.M))


def _read(path):
    raw = open(path, "rb").read()
    try:
        return raw.decode(ANSI)
    except UnicodeDecodeError:
        return raw.decode("utf-8", "replace")


def deploy(awl_files, project_path=None, template=None, open_after=False,
           verify_block=None, symbols=None):
    """把若干 AWL 块部署进一个工程并四关验证。

    awl_files: AWL 文件路径列表（有依赖关系时，被依赖的块排前面：
               中断程序/被调用子程序 要在引用它们的块之前导入）
    symbols: {符号名: 绝对地址}，如 {"电机启动": "I0.0", "电机运行": "Q0.0"}。
             会在导入程序【之前】把这些地址所在的行改名，程序里就能直接用符号名。
             ⚠ 带符号时必须用 SAVEAS 落盘 —— PRJ_Save 不保存变量表（实测：存完重开符号没了）。
    verify_block: 已废弃 —— 现在每个块都各自验，不需要指定。传了只作提示。
    返回 dict：四关结果 + 是否整体通过。
    """
    report = {"stage1_structure": None, "stage2_compile": None,
              "stage3_engine_validate": None, "stage4_roundtrip": None,
              "passed": False, "detail": {}}
    if verify_block:
        report["detail"]["note"] = "verify_block 已废弃：现在每个块都单独往返核对"

    awl_files = [os.path.abspath(f) for f in awl_files]
    for f in awl_files:
        if not os.path.exists(f):
            raise FlowError(f"AWL 文件不存在：{f}")

    # 每个文件对应的块名 —— 后面三关都按块逐个验，不再只验最后一个
    blocks = {}
    for f in awl_files:
        name = _block_name(_read(f))
        if not name:
            raise FlowError(f"{os.path.basename(f)} 里找不到 *_BLOCK 块头，不是合法 AWL")
        blocks[f] = name
    report["detail"]["blocks"] = {os.path.basename(f): n for f, n in blocks.items()}

    # 同名块会互相覆盖：后导入的顶掉先导入的，编译随后报交叉引用错，
    # 但日志上四个 IMPORTPOU 全是 ret=0，很难看出真因 —— 所以提前挡住。
    dup = {}
    for f, n in blocks.items():
        dup.setdefault(n, []).append(os.path.basename(f))
    dup = {n: fs for n, fs in dup.items() if len(fs) > 1}
    if dup:
        raise FlowError(
            "这些文件声明了同一个块名，导入时会互相覆盖：" +
            "；".join("%s <- %s" % (n, "、".join(fs)) for n, fs in dup.items()) +
            "。一个块名只能来自一个文件。")

    # 主程序(ORGANIZATION_BLOCK/OB1)必须最先导入。
    # 实测：导入 OB1 会【替换整个程序集】—— 先导的子程序会被它抹掉，
    # 随后编译报交叉引用错(ret=-1610612428)，但 IMPORTPOU 全是 ret=0，看日志找不出真因。
    # （和"导出 OB1 会把所有块一起导出"是对称的：OB1 那一份就代表整个程序。）
    ob1 = [f for f in awl_files
           if re.search(r"^ORGANIZATION_BLOCK", _read(f), re.M)]
    if ob1 and awl_files[0] not in ob1:
        awl_files = ob1 + [f for f in awl_files if f not in ob1]
        report["detail"]["reordered"] = (
            "主程序 %s 已自动排到最前 —— 导入 OB1 会替换整个程序集，"
            "排在后面会把先导入的子程序抹掉。" % "、".join(os.path.basename(f) for f in ob1))

    # ---- 第1关：静态预检（不启动软件）----
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
    src = _pick_template(template)
    if project_path is None:
        # 扩展名跟模板走：.smartV3 存成 .smart 会让软件认错格式
        ext = os.path.splitext(src)[1] if src else ".smart"
        project_path = os.path.join(os.path.dirname(awl_files[0]), "_autoflow" + ext)
    project_path = os.path.abspath(project_path)
    just_created = not os.path.exists(project_path)
    if just_created:
        if not src:
            raise FlowError(
                "没有可用的模板工程：软件安装目录里没找到 template.smartV3，"
                ".smart200_local.json 里也没配 template_project / blank_template。"
                "也可以显式传 project_path 指向一个已存在的工程。")
        shutil.copy(src, project_path)
    report["detail"]["template"] = src if src else "(用已存在的工程)"

    # 往返核对的中间产物放系统临时目录、用完即删 ——
    # 以前落在工程目录里且只在下次运行开头才删，用户的工程文件夹会越积越脏。
    tmpdir = tempfile.mkdtemp(prefix="smart200_rt_")
    out_awl = {f: os.path.join(tmpdir, "rt_%d.awl" % i) for i, f in enumerate(awl_files)}

    # 已存在的工程要先备份 —— 万一传进来的是真工程，改坏了还能退回去
    if os.path.exists(project_path) and not just_created:
        report["detail"]["backup"] = backup_project(project_path)

    # ---- 第2、3、4 关的数据一次注入全部取回 ----
    # 符号必须在导入程序之前设好，否则程序里的符号名解析不了 → 整个网络变无效程序段
    cmds = ["SYMSET %s|%s" % (addr, name) for name, addr in (symbols or {}).items()]
    if symbols:
        cmds.append("GVTCOMPILE x")
    cmds += ["IMPORTPOU " + f for f in awl_files]
    cmds.append("COMPILE")
    cmds += ["VALIDATE %s|0" % blocks[f] for f in awl_files]
    cmds += ["EXPORT %s|%s" % (blocks[f], out_awl[f]) for f in awl_files]
    # 带符号时必须 SAVEAS：PRJ_Save 不落盘变量表（实测存完重开符号全没）。
    # SAVEAS 到临时文件，实例退出后再替换回去。
    # 临时文件必须保持同样的扩展名 —— SAVEAS 是按扩展名认格式的
    _stem, _ext = os.path.splitext(project_path)
    tmp_proj = (_stem + "_saveas_tmp" + _ext) if symbols else None
    cmds.append(("SAVEAS " + tmp_proj) if symbols else "SAVE")

    pid = engine.launch_instance(project_path)
    try:
        log = engine.run_script(pid, cmds)
    finally:
        if not open_after:
            engine.kill_instance(pid)

    if symbols and tmp_proj and os.path.exists(tmp_proj):
        os.replace(tmp_proj, project_path)

    report["detail"]["log"] = enginelog.ret_lines(log)
    if symbols:
        report["detail"]["symbols"] = enginelog.symbols(log)

    # 脚本没跑完 = 软件中途崩了或超时，后面的判据全都不可信
    if not enginelog.done(log):
        report["stage2_compile"] = "FAIL"
        report["detail"]["fatal"] = "引擎脚本未跑完（无 __DONE__）—— 软件可能中途崩溃或超时"
        shutil.rmtree(tmpdir, ignore_errors=True)
        return report

    # ---- 第2关：导入 + 编译（含符号是否真的设上）----
    ok2 = enginelog.imports_ok(log, awl_files) and enginelog.compiled(log)
    if symbols and not enginelog.symbols_ok(log, symbols):
        ok2 = False
        report["detail"]["symbol_hint"] = (
            "有符号没设上（多半是地址在 I/O 变量表里找不到对应行）。"
            "未定义的符号名不会报编译错，而是让整个网络变成无效程序段。")
    report["stage2_compile"] = "PASS" if ok2 else "FAIL"
    if not ok2:
        report["detail"]["imports"] = enginelog.imports(log)
        shutil.rmtree(tmpdir, ignore_errors=True)
        return report

    # ---- 第3关：引擎真值 ----
    v = enginelog.validation(log)
    report["detail"]["engine_validate"] = v
    ok3 = enginelog.validation_ok(log, list(blocks.values()))
    report["stage3_engine_validate"] = "PASS" if ok3 else "FAIL"
    if not ok3:
        report["detail"]["hint"] = (
            "软件判定这些网络是无效程序段（打开工程会看到标红）。"
            "注意：编译仍可能 ret=0，因为无效网络被排除在编译之外。")
        shutil.rmtree(tmpdir, ignore_errors=True)
        return report

    # ---- 第4关：逐块往返核对 ----
    rt = {}
    all_ok = True
    for f in awl_files:
        name, dst = blocks[f], out_awl[f]
        if not os.path.exists(dst) or os.path.getsize(dst) == 0:
            rt[name] = {"error": "导出为空 —— 块可能没真正建立"}
            all_ok = False
            continue
        src_text = _section(_read(f), name)
        back_text = _section(_read(dst), name)
        if back_text is None:
            rt[name] = {"error": "导出文件里找不到块 %r —— 块没真正建立" % name}
            all_ok = False
            continue
        src_ops = _ops(src_text)
        back_ops = set(_ops(back_text))
        missing = [o for o in src_ops if o not in back_ops]
        n_src, n_back = _net_count(src_text), _net_count(back_text)
        entry = {"instructions": len(src_ops), "missing": missing,
                 "networks_src": n_src, "networks_back": n_back,
                 "exported_bytes": os.path.getsize(dst)}
        if missing or n_src != n_back:
            all_ok = False
            if n_src != n_back:
                entry["error"] = "网络数对不上：源 %d 段，回来 %d 段" % (n_src, n_back)
        rt[name] = entry
    report["stage4_roundtrip"] = "PASS" if all_ok else "FAIL"
    report["detail"]["roundtrip"] = rt

    shutil.rmtree(tmpdir, ignore_errors=True)
    report["passed"] = all_ok
    report["project"] = project_path
    if open_after:
        report["opened_pid"] = pid
    return report


def validate_project(project_path, block_names):
    """只验不改：问引擎某几个块有没有无效程序段（不导入、不编译、不保存）。"""
    cmds = ["VALIDATE %s|0" % b for b in block_names]
    pid = engine.launch_instance(project_path)
    try:
        log = engine.run_script(pid, cmds)
    finally:
        engine.kill_instance(pid)
    return {"blocks": enginelog.validation(log),
            "all_valid": enginelog.validation_ok(log, block_names),
            "completed": enginelog.done(log)}


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

def set_symbols(project_path, symbols):
    """只改符号表，不动程序。symbols = {符号名: 绝对地址}。

    原理：I/O 变量表里每个 I/O 点本来就列好了、地址是现成的，所以是【改那一行的名字】，
    不是新建行 —— 在空行上 SetAddressValue 恒报 6019，新行设不了地址。
    落盘必须 SAVEAS：PRJ_Save 不保存变量表（实测存完重开符号全没）。
    """
    project_path = os.path.abspath(project_path)
    if not os.path.exists(project_path):
        raise FlowError("工程不存在：" + project_path)
    backup = backup_project(project_path)      # 改工程之前先留退路
    stem, ext = os.path.splitext(project_path)
    tmp = stem + "_saveas_tmp" + ext
    cmds = ["SYMSET %s|%s" % (addr, name) for name, addr in symbols.items()]
    cmds += ["GVTCOMPILE x", "SAVEAS " + tmp]
    pid = engine.launch_instance(project_path)
    try:
        log = engine.run_script(pid, cmds)
    finally:
        engine.kill_instance(pid)
    if os.path.exists(tmp):
        os.replace(tmp, project_path)
    return {"symbols": enginelog.symbols(log),
            "all_ok": enginelog.symbols_ok(log, symbols) and enginelog.done(log),
            "completed": enginelog.done(log),
            "backup": backup}
