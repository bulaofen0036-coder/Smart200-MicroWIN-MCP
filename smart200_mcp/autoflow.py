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

import hashlib
import os
import re
import shutil
import tempfile

from . import engine, enginelog, localcfg, paths, stlcheck

# 软件的文本用系统 ANSI 代码页（中文机器上就是 GBK）；写死 gbk 在非中文系统会崩
ANSI = "mbcs"
# 软件的 AWL 用 CRLF 行尾；写成 chr() 是为了避免被各层转义搞坏
CRLF = chr(13) + chr(10)

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


def _ops(text, dedup=True):
    """抽助记符。dedup=True 只留种类；False 返回完整指令流（按顺序、不去重）。

    第4关往返核对要用 dedup=False：只比对"种类集合"的话，
    同一助记符少了几条是看不出来的（源里 20 条 MOVW、回来只剩 3 条也算过），
    而这正是"被软件静默丢弃"最可能的形态。
    """
    out = []
    for line in text.replace("\r\n", "\n").split("\n"):
        # 首字符必须允许 = 和 + - * /：输出线圈是 "="、立即输出 "=I"，
        # 算术指令是 "+I" "-D" "*R" "/D" 这一族。只写 [A-Z] 会把它们【整族漏掉】，
        # 往返核对就等于从没检查过线圈和四则运算（2026-08-25 被单测抓出来）。
        m = re.match(r"\t([A-Z=+\-*/][A-Z0-9_=<>+\-*/.]*)", line)
        if not m:
            continue
        if dedup and m.group(1) in out:
            continue
        out.append(m.group(1))
    return out


def _net_count(text):
    return len(re.findall(r"^Network\s+\d+", text.replace("\r\n", "\n"), re.M))


def compare_roundtrip(src_text, back_text):
    """第4关的判据：源块文本 vs 从工程导出回来的同一块，逐条核对。

    单独成函数是为了能脱离 MicroWIN 直接测 —— 凡是"拿输出下结论"的代码
    都要可单测，否则判据写错了没人发现（enginelog 的引号 bug 就是这么活很久的）。

    返回 (是否一致, 明细dict)。比对的是【完整指令流】而不是助记符种类集合：
    只比种类的话，源里 20 条 MOVW、回来只剩 3 条也算过 ——
    而"被软件静默丢弃几条"正是最需要抓的形态。
    """
    src_stream = _ops(src_text, dedup=False)
    back_stream = _ops(back_text, dedup=False)
    n_src, n_back = _net_count(src_text), _net_count(back_text)
    entry = {"instructions_src": len(src_stream),
             "instructions_back": len(back_stream),
             "missing_kinds": [o for o in _ops(src_text)
                               if o not in set(back_stream)],
             "networks_src": n_src, "networks_back": n_back}
    if n_src != n_back:
        entry["error"] = "网络数对不上：源 %d 段，回来 %d 段" % (n_src, n_back)
        return False, entry
    if src_stream != back_stream:
        # 指出第一条分歧在哪，便于直接定位，不用人肉 diff
        i = next((k for k, (a, b) in enumerate(zip(src_stream, back_stream))
                  if a != b), min(len(src_stream), len(back_stream)))
        entry["error"] = (
            "指令流对不上：源 %d 条、回来 %d 条，第 %d 条起分歧（源 %s，回来 %s）"
            % (len(src_stream), len(back_stream), i + 1,
               src_stream[i:i + 3] or "(没有了)",
               back_stream[i:i + 3] or "(没有了)"))
        return False, entry
    return True, entry


def _read(path):
    """读 AWL 文本。

    编码探测顺序是【先 UTF-8 再 ANSI】，不能反过来：
    GBK 几乎接受任意字节，UTF-8 的中文常被它静默解成乱码而不报错
    （实测 "ASCII↔HEX" 的 UTF-8 字节被 GBK 解成 "ASCII鈫揌EX"，一声不吭）；
    反方向则很安全 —— 18 个真实 GBK 块文件用严格 UTF-8 解码全部正确拒绝、零误判。
    """
    raw = open(path, "rb").read()
    if raw.startswith(bytes((0xEF, 0xBB, 0xBF))):
        return raw[3:].decode("utf-8")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(ANSI, "replace")


def _engine_ready_awl(path, tmpdir, idx):
    """把 AWL 转成引擎能吃的【系统 ANSI 代码页 + CRLF】临时文件。

    为什么需要：引擎的 IMPORTPOU 是按 ANSI 读文件的。直接喂 UTF-8 会
    ret=0（又一个"报成功"），但块名导进去是乱码 —— 随后按中文块名找就是
    "块未找到"（2026-08-25 实测）。以前只能让用户自己维护一份 GBK 副本，
    这是每次都要过的门槛，现在在这里自动抹平。

    已经是 ANSI+CRLF 的原样返回，不做多余拷贝。
    """
    raw = open(path, "rb").read()
    # 判"是不是已经是 ANSI"必须先试 UTF-8，不能反过来问 ANSI 能不能解 ——
    # UTF-8 常能被 GBK 解码成功(乱码却不报错)，那样会把 UTF-8 文件误判成
    # "已经是 ANSI"直接原样交给引擎，又绕回块名乱码。踩过。
    try:
        raw.decode("utf-8")
        is_utf8 = True
    except UnicodeDecodeError:
        is_utf8 = False
    is_ascii = not any(b > 127 for b in raw)
    # 纯 ASCII 时两种编码等价，只要行尾已是 CRLF 就不用动
    if (is_ascii or not is_utf8) and CRLF.encode("ascii") in raw:
        return path

    # splitlines() 认所有行尾形式(CR / LF / CRLF)，重组成软件要的 CRLF
    text = CRLF.join(_read(path).splitlines()) + CRLF
    try:
        data = text.encode(ANSI)
    except UnicodeEncodeError:
        # UnicodeEncodeError.start 在 mbcs 上不可信（实测指到第 0 个字符），
        # 自己逐字符找，报出行号和具体是哪个字 —— 报错要能直接定位才有用。
        where, ch = None, None
        for n, line in enumerate(text.split(CRLF), 1):
            for c in line:
                try:
                    c.encode(ANSI)
                except UnicodeEncodeError:
                    where, ch = n, c
                    break
            if where:
                break
        raise FlowError(
            "%s 第 %s 行有 GBK 表示不了的字符 %r（U+%04X）。"
            "软件的 AWL 只认系统 ANSI 代码页 —— 换个能表示的写法即可"
            "（踩过的例子：全角箭头 U+2194 不在 GBK 里，写成 <-> 就好）。"
            % (os.path.basename(path), where, ch, ord(ch)))
    dst = os.path.join(tmpdir, "in_%d_%s" % (idx, os.path.basename(path)))
    with open(dst, "wb") as fh:
        fh.write(data)
    return dst


def _fingerprint(path):
    """工程文件指纹 —— 只用来判断"内容有没有变"，不需要密码学强度。"""
    if not os.path.exists(path):
        return None
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return "%d:%s" % (os.path.getsize(path), h.hexdigest())


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
              "stage5_persisted": None,
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
    # 交给引擎前统一规范编码/行尾 —— 用户可以直接用 UTF-8 写 AWL
    engine_files = {f: _engine_ready_awl(f, tmpdir, i)
                    for i, f in enumerate(awl_files)}
    converted = [os.path.basename(f) for f in awl_files if engine_files[f] != f]
    if converted:
        report["detail"]["encoding_normalized"] = converted
    cmds += ["IMPORTPOU " + engine_files[f] for f in awl_files]
    cmds.append("COMPILE")
    cmds += ["VALIDATE %s|0" % blocks[f] for f in awl_files]
    cmds += ["EXPORT %s|%s" % (blocks[f], out_awl[f]) for f in awl_files]
    # 落盘【一律】用 SAVEAS，绝不用 SAVE —— 两个已实测的理由：
    #   1) PRJ_Save 不落盘变量表（带符号时存完重开符号全没）；
    #   2) 对 .smartV3 工程，PRJ_Save 会把内容【静默写进同名的 .smart(V2)】，
    #      原 .smartV3 字节不变，而且照样 ret=0。
    #      2026-08-25 实测：模板 18511B 的 savetest.smartV3 导入一个块后 SAVE，
    #      MD5 纹丝不动，旁边多出 21117B 的 savetest.smart —— 四关全绿但工程是空的。
    # SAVEAS 到临时文件，实例退出后再替换回去。
    # 临时文件必须保持同样的扩展名 —— SAVEAS 是按扩展名认格式的
    _stem, _ext = os.path.splitext(project_path)
    tmp_proj = _stem + "_saveas_tmp" + _ext
    cmds.append("SAVEAS " + tmp_proj)

    # 第1~4关全在同一个内存实例里判，证明不了"落没落盘"。
    # 记下导入前的文件指纹，收工后比对 —— 这是独立于软件自报成功的判据。
    before_fp = _fingerprint(project_path)

    pid = engine.launch_instance(project_path)
    try:
        log = engine.run_script(pid, cmds)
    finally:
        if not open_after:
            engine.kill_instance(pid)

    # ---- 落盘：SAVEAS 的产物换回 project_path，并留下证据供第5关判定 ----
    persisted = {"saveas_target": tmp_proj, "produced": os.path.exists(tmp_proj)}
    if persisted["produced"]:
        persisted["bytes"] = os.path.getsize(tmp_proj)
        os.replace(tmp_proj, project_path)
    # SAVE 的老毛病会在这里留下痕迹：同名 .smart 是 PRJ_Save 干的，不该出现
    stray = _stem + ".smart"
    if _ext.lower() != ".smart" and os.path.exists(stray):
        persisted["stray_v2_file"] = stray

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
    # 日志里记的是【实际交给引擎的】路径（编码规范化后可能是临时文件），
    # 拿原始路径去比对会永远匹配不上 —— 判据必须和被判对象对齐。
    ok2 = (enginelog.imports_ok(log, [engine_files[f] for f in awl_files])
           and enginelog.compiled(log))
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
        ok_rt, entry = compare_roundtrip(src_text, back_text)
        entry["exported_bytes"] = os.path.getsize(dst)
        if not ok_rt:
            all_ok = False
        rt[name] = entry
    report["stage4_roundtrip"] = "PASS" if all_ok else "FAIL"
    report["detail"]["roundtrip"] = rt

    shutil.rmtree(tmpdir, ignore_errors=True)

    # ---- 第5关：真的落盘了吗 ----
    # 前四关全在同一个内存实例里问软件自己，软件说"存好了"不等于文件变了。
    # 实测过 SAVE 报 ret=0 但把内容写进同名 .smart、原 .smartV3 字节不变的情况。
    after_fp = _fingerprint(project_path)
    persisted["fingerprint_before"] = before_fp
    persisted["fingerprint_after"] = after_fp
    ok5 = bool(persisted.get("produced")) and after_fp != before_fp
    report["stage5_persisted"] = "PASS" if ok5 else "FAIL"
    report["detail"]["persisted"] = persisted
    if not ok5:
        report["detail"]["persist_hint"] = (
            "程序在内存里是对的，但没写进 %s（文件指纹没变）。"
            "别用 SAVE —— 对 .smartV3 工程它会把内容静默存成同名 .smart(V2) 还回 ret=0。"
            % os.path.basename(project_path))
    if persisted.get("stray_v2_file"):
        report["detail"]["persist_warn"] = (
            "旁边冒出了 %s —— 这是 PRJ_Save 的手笔，说明有代码回退用了 SAVE。"
            % os.path.basename(persisted["stray_v2_file"]))

    report["passed"] = all_ok and ok5
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


_BLOCK_HEAD = re.compile(
    r"^(SUBROUTINE|PROGRAM|ORGANIZATION|INTERRUPT|FUNCTION|DATA)_BLOCK\s+(.+?):(\S+)")


def split_blocks(text):
    """把一份可能含多个 BLOCK 的 AWL 文本拆开。

    返回 [{"kind","name","id","text","networks"}]。
    单独成函数是为了能脱机测 —— 拆错了会静默少导出几个块。
    """
    text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    out, cur = [], None
    for line in lines:
        m = _BLOCK_HEAD.match(line.strip())
        if m:
            cur = {"kind": m.group(1), "name": m.group(2).strip(),
                   "id": m.group(3), "lines": [line]}
            continue
        if cur is None:
            continue
        cur["lines"].append(line)
        if line.strip().startswith("END_") and line.strip().endswith("_BLOCK"):
            body = "\n".join(cur["lines"])
            out.append({"kind": cur["kind"], "name": cur["name"], "id": cur["id"],
                        "text": body, "networks": _net_count(body)})
            cur = None
    return out


# 绝对地址与常数的形态（操作数长这样就不是符号名）
_ABS_OPERAND = re.compile(
    r"^(?:"
    r"[IQMVSL][BWDX]?\d+(?:\.\d+)?"          # I0.0 Q0.1 VB100 VW100 VD100 M0.0 S0.0 LD30
    r"|SM[BWD]?\d+(?:\.\d+)?"                # SM0.0 SMB34 SMW90 SMD38
    r"|AIW\d+|AQW\d+"                        # 模拟量
    r"|[TC]\d+|HC\d+|AC[0-3]"                # 定时器/计数器/高速计数器/累加器
    r"|[IQ]B\d+"                             # 字节直接寻址
    r"|[+-]?\d+(?:\.\d+)?"                   # 整数/实数常量
    r"|16#[0-9A-Fa-f]+"                      # 十六进制
    r"|&?[A-Z]+\d+(?:\.\d+)?"                # 指针取址等
    r")$")
# 这些指令的操作数本来就是名字（块名/标号/步），不算符号引用
_NAME_OPERAND_OPS = {"CALL", "ATCH", "DTCH", "JMP", "LBL", "SCRT", "LSCR",
                     "FOR", "NEXT", "SCRE", "CRET", "CRETI"}


# 西门子【内置系统符号】（系统变量表里的，对应 SM 区）。任何工程都自带，
# 不需要使用者提供 —— 混进"你必须带上的符号"里会误导人。
# 这是启发式名单：认不出来的会被当成用户符号（偏保守，宁可多提示也不漏）。
_SYS_SYMBOL = re.compile(
    r"^(?:Always_On|First_Scan_On|Retentive_Lost|RUN_Power_Up"
    r"|Clock_\w+"                       # Clock_1s / Clock_60s ...
    r"|Time_\d+_Intrvl"                 # 定时中断周期
    r"|P\d_\w+"                         # 自由口：P0_Config / P0_End_Char ...
    r"|HSC\d_\w+"                       # 高速计数：HSC0_CV / HSC0_Ctrl ...
    r"|PLS\d_\w+|PTO\d_\w+|PWM\d_\w+"   # 脉冲输出
    r"|XMT\d?_\w+|RCV\d?_\w+"
    r")$")


def classify_symbol_refs(refs):
    """把符号引用分成"软件内置的"和"你自己定义的"。

    只有后者需要在重新部署时用 symbols= 带上；
    前者是系统变量表里的，新工程自带。
    """
    sysm = [s for s in refs if _SYS_SYMBOL.match(s)]
    user = [s for s in refs if not _SYS_SYMBOL.match(s)]
    return user, sysm


def symbol_refs(text):
    """找出 AWL 里用到的【符号名】（而非绝对地址）操作数。

    为什么需要：软件导出 POU 时，凡是在符号表里有名字的地址都会被
    **替换成符号名**（`LD I0.0` 导出回来是 `LD 急停_常闭`）。
    这样的 AWL 依赖那份符号表 —— 原样导进一个没有符号表的新工程，
    符号名解析不了，整个网络会变成无效程序段、编译报交叉引用错。
    导出工具必须主动把这件事说出来，否则"导出的东西导不回去"会让人摸不着头脑。
    """
    found = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if not line.startswith("\t"):
            continue
        parts = line.strip().split(None, 1)
        if not parts:
            continue
        op = parts[0]
        if op in _NAME_OPERAND_OPS or len(parts) == 1:
            continue
        for tok in parts[1].split(","):
            tok = tok.strip()
            if not tok or _ABS_OPERAND.match(tok):
                continue
            if tok not in found:
                found.append(tok)
    return found


_BAD_FN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_filename(name):
    """块名可能带 Windows 文件名非法字符（/ : * 等），清一下再当文件名用。"""
    s = _BAD_FN.sub("_", name).strip().rstrip(".")
    return s or "unnamed"


def export_all_blocks(project_path, out_dir, encoding="utf-8"):
    """把工程里【所有】POU 各导出成一个 .awl 文件，不用先知道块名。

    做法：导出 OB1 时引擎会连依赖一起带出来，而"OB1 那一份就代表整个程序"
    （和"导入 OB1 会替换整个程序集"是对称的），所以导一次再拆开即可。

    encoding: 落盘编码。默认 utf-8（人读/进 git 友好）；
              传 "ansi" 得到软件原生的 ANSI+CRLF。
              两种都能被 deploy 直接吃回去（它会自动规范化）。
    """
    project_path = os.path.abspath(project_path)
    if not os.path.exists(project_path):
        raise FlowError("工程不存在：" + project_path)
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    tmpdir = tempfile.mkdtemp(prefix="smart200_exp_")
    dump = os.path.join(tmpdir, "all.awl")
    try:
        pid = engine.launch_instance(project_path)
        try:
            log = engine.run_script(pid, ["EXPORT MAIN|" + dump])
        finally:
            engine.kill_instance(pid)
        if not enginelog.done(log):
            raise FlowError("引擎脚本没跑完 —— 软件可能中途崩了或超时")
        if not os.path.exists(dump) or os.path.getsize(dump) == 0:
            raise FlowError("导出为空。日志：" + "; ".join(enginelog.ret_lines(log)))
        blocks = split_blocks(_read(dump))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    if not blocks:
        raise FlowError("导出的文件里一个 BLOCK 都没解析出来 —— 格式可能变了")

    written, seen = [], {}
    for b in blocks:
        stem = _safe_filename(b["name"])
        seen[stem] = seen.get(stem, 0) + 1
        if seen[stem] > 1:                       # 同名块（清洗后撞名）不许互相覆盖
            stem = "%s_%d" % (stem, seen[stem])
        dst = os.path.join(out_dir, stem + ".awl")
        body = CRLF.join(b["text"].split("\n")) + CRLF
        enc = ANSI if str(encoding).lower() in ("ansi", "mbcs", "gbk") else "utf-8"
        with open(dst, "wb") as fh:
            fh.write(body.encode(enc, "replace"))
        written.append({"block": b["name"], "id": b["id"], "kind": b["kind"],
                        "networks": b["networks"], "file": dst})

    result = {"count": len(written), "out_dir": out_dir,
              "encoding": "ansi" if enc == ANSI else "utf-8",
              "blocks": written}

    # 导出的 AWL 里若出现符号名，它就依赖那份符号表 —— 必须说出来，
    # 否则"导出的东西导不回去"（编译报交叉引用错）会让人摸不着头脑。
    refs = []
    for b in blocks:
        for s in symbol_refs(b["text"]):
            if s not in refs:
                refs.append(s)
    user_refs, sys_refs = classify_symbol_refs(refs)
    if user_refs:
        result["symbols_you_must_supply"] = user_refs
        result["builtin_symbols_used"] = sys_refs
        result["note"] = (
            "这些块里用的是【符号名】不是绝对地址 —— 软件导出 POU 时会把符号表里"
            "有名字的地址换成符号名。要把它们导回一个新工程，必须带上这 %d 个"
            "自定义符号：smart_deploy(awl_files=[...], symbols={符号名: 绝对地址})，"
            "否则符号解析不了、整个网络会变成无效程序段、编译报交叉引用错。"
            "另外 %d 个是软件内置的系统符号（Always_On / P0_* / HSC0_* 这类），"
            "新工程自带，不用你提供。"
            % (len(user_refs), len(sys_refs)))
    elif sys_refs:
        result["builtin_symbols_used"] = sys_refs
    return result


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

    ⚠ 对【已经有程序】的工程事后改符号表会打断程序与符号表的绑定：
    符号一条条都 ok、GVTCOMPILE 也 ret=0，但随后 COMPILE 报 -1610612428（交叉引用），
    而各块 POU_IsValidNet 全是 invalid=0 —— 程序本身没坏，坏的是绑定关系。
    （2026-08-25 实测；正解是 smart_deploy(symbols=...)，符号在导入程序【之前】设。）
    所以这里改完会补一次 COMPILE 验证；编译不过就【自动回滚】，不把坏工程留给你。
    """
    project_path = os.path.abspath(project_path)
    if not os.path.exists(project_path):
        raise FlowError("工程不存在：" + project_path)
    backup = backup_project(project_path)      # 改工程之前先留退路
    stem, ext = os.path.splitext(project_path)
    tmp = stem + "_saveas_tmp" + ext
    cmds = ["SYMSET %s|%s" % (addr, name) for name, addr in symbols.items()]
    # COMPILE 排在 SAVEAS 之前：编译不过就别存了
    cmds += ["GVTCOMPILE x", "COMPILE", "SAVEAS " + tmp]
    pid = engine.launch_instance(project_path)
    try:
        log = engine.run_script(pid, cmds)
    finally:
        engine.kill_instance(pid)

    compile_ok = enginelog.compiled(log)
    syms_ok = enginelog.symbols_ok(log, symbols) and enginelog.done(log)
    result = {"symbols": enginelog.symbols(log),
              "compile_ok": compile_ok,
              "completed": enginelog.done(log),
              "backup": backup}

    if compile_ok and os.path.exists(tmp):
        os.replace(tmp, project_path)
        result["all_ok"] = syms_ok
        return result

    # 编译没过 —— 工程原样退回，不留半成品
    if os.path.exists(tmp):
        os.remove(tmp)
    shutil.copy(backup, project_path)
    result["all_ok"] = False
    result["rolled_back"] = True
    result["hint"] = (
        "符号写进去了，但工程随后编译不过，已把工程回滚到改动前。"
        "对已有程序的工程事后改符号表会打断程序与符号表的绑定 —— "
        "正确做法是从空白模板走 smart_deploy(awl_files=[...], symbols={...})，"
        "顺序为 SYMSET → GVTCOMPILE → IMPORTPOU → COMPILE → SAVEAS，"
        "符号必须在导入程序【之前】设好。")
    return result
