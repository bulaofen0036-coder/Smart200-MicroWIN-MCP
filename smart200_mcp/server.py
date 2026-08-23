"""Smart200 MCP Server —— S7-200 SMART (STEP 7-Micro/WIN SMART V3) 自动化。

三层能力，各自的可靠度不同，工具描述里都如实标注：
  离线工程解析  V2 .smart 完全可用；V3 .smartV3 加密，不支持
  在线通讯      snap7，代码完备但【未经真机验证】
  UI 自动化     读项目树已实测；编译未实测
"""

import glob
import os

from mcp.server import MCPServer

from . import container, online, project, ui, awl, engine, paths, stlcheck, autoflow

mcp = MCPServer("smart200", version="0.3.0")


@mcp.tool()
def smart_doctor() -> dict:
    """环境自检：一次说清缺什么、怎么补。装好之后先跑这个。

    检查引擎 DLL、注入器、MicroWIN 安装位置、空白模板是否都就位。
    """
    missing = paths.check()
    return {
        "ok": not missing,
        "仓库根": paths.ROOT,
        "引擎DLL": paths.DLL,
        "注入器": paths.INJECTOR,
        "MicroWIN": (paths.mwsmart() if not missing else "未找到"),
        "空白模板": paths.blank_template() or "未找到（新建工程会失败，可传 project_path 绕开）",
        "脚本超时秒": engine.SCRIPT_TIMEOUT,
        "问题": missing,
    }


# ---------- 离线工程解析 ----------

@mcp.tool()
def smart_probe(path: str) -> dict:
    """探查一个 .smart/.smartV3 工程能否离线解析，不解包。

    V2(.smart) 可解析；V3(.smartV3) 数据段加密，只能走 UI 自动化。
    """
    return container.probe(path)


@mcp.tool()
def smart_list_projects(directory: str, recursive: bool = True) -> dict:
    """扫描目录下的 S7-200 SMART 工程，并标出每个能否离线解析。"""
    pat = "**/*.smart*" if recursive else "*.smart*"
    files = [p for p in glob.glob(os.path.join(directory, pat), recursive=recursive)
             if p.lower().endswith((".smart", ".smartv3"))]
    return {"directory": directory, "count": len(files),
            "projects": [container.probe(p) for p in sorted(files)]}


@mcp.tool()
def smart_analyze(path: str) -> dict:
    """解析 V2 工程并汇总：项目名、版本、用户符号数、POU 名、功能框使用统计。

    注意：不还原逐网络 LAD/STL 逻辑（容器网络记录结构尚未逆向完成）；
    CPU 型号离线不可靠，需用 smart_ui_project_tree 读。
    """
    return project.summary(container.load(path))


@mcp.tool()
def smart_symbols(path: str) -> dict:
    """列出工程里用户定义的符号名（已剔除西门子系统 SM 符号表）。"""
    proj = container.load(path)
    syms = project.symbols(proj)
    return {"path": path, "count": len(syms), "symbols": syms}


@mcp.tool()
def smart_function_blocks(path: str) -> dict:
    """功能框（MOV_DW / MUL_DI / ...）使用直方图，用于快速判断程序在干什么。"""
    proj = container.load(path)
    fb = project.function_blocks(proj)
    return {"path": path, "kinds": len(fb),
            "total": sum(n for _, n in fb),
            "histogram": [{"name": k, "count": v} for k, v in fb]}


@mcp.tool()
def smart_compare(path_a: str, path_b: str) -> dict:
    """对比两个 V2 工程的符号与功能框差异 —— 同系列天车程序找改动点很有用。"""
    a, b = container.load(path_a), container.load(path_b)
    sa, sb = set(project.symbols(a)), set(project.symbols(b))
    fa, fb_ = dict(project.function_blocks(a)), dict(project.function_blocks(b))
    changed = {k: [fa.get(k, 0), fb_.get(k, 0)]
               for k in set(fa) | set(fb_) if fa.get(k, 0) != fb_.get(k, 0)}
    return {
        "a": project.info(a), "b": project.info(b),
        "symbols_only_in_a": sorted(sa - sb),
        "symbols_only_in_b": sorted(sb - sa),
        "function_block_count_diff": changed,
    }


# ---------- 在线通讯（snap7）----------

@mcp.tool()
def smart_plc_info(ip: str) -> dict:
    """读 CPU 型号/序列号/运行状态。⚠ 本层未经真机验证，接真机请先用此工具试探。"""
    with online.Plc(ip) as plc:
        return plc.cpu_info()


@mcp.tool()
def smart_plc_read(ip: str, addresses: list[str]) -> dict:
    """读一批地址，如 ["VW100","V10.3","QB0"]。V 区映射为 DB1。⚠ 未经真机验证。"""
    with online.Plc(ip) as plc:
        return {"ip": ip, "values": plc.read_many(addresses)}


@mcp.tool()
def smart_plc_write(ip: str, address: str, value: int, confirm: bool = False) -> dict:
    """向 PLC 写值。

    ⚠ 写运行中的 PLC 是不可逆的现场操作，必须 confirm=True。本层未经真机验证。
    """
    if not confirm:
        return {"refused": True,
                "reason": "写 PLC 是不可逆现场操作，需显式 confirm=True；且本层尚未真机验证"}
    with online.Plc(ip) as plc:
        return plc.write(address, value)


# ---------- UI 自动化 ----------

@mcp.tool()
def smart_ui_project_tree() -> dict:
    """读取【已打开的】MicroWIN 里的工程：文件名、CPU 型号、POU 列表。已实测。

    只接管已打开的实例，不会替你启动软件或打开工程（以免顶掉你正编辑的工程）。
    """
    return ui.read_project_tree()


@mcp.tool()
def smart_ui_output() -> dict:
    """回读 MicroWIN 输出窗口（编译结果 / 错误列表）。只读。"""
    return ui.read_output_window()


@mcp.tool()
def smart_ui_compile(confirm: bool = False) -> dict:
    """点击 Ribbon【编译】。⚠ 会操作你正在用的界面，需 confirm=True；且尚未实测。"""
    return ui.compile_project(confirm=confirm)


# ---------- 引擎调用（注入路线，全自动，无 UI）----------

@mcp.tool()
def smart_awl_analyze(awl_path: str) -> dict:
    """解析并分析一个 AWL/STL 程序块文件（软件导出或引擎导出的 .awl）。

    返回块名/ID、网络数、指令统计、读写地址、网络注释 —— 用于程序验证与审查。
    """
    block = awl.parse_file(awl_path)
    return awl.analyze(block)


@mcp.tool()
def smart_export_blocks(project_path: str, blocks: dict) -> dict:
    """【全自动】从 V3 工程导出若干程序块为 AWL 文本。

    blocks = {"块名": "输出路径", ...}，块名如 "初始化"、"地址判定"。
    自动启动独立实例、注入引擎、按名查块、导出、关实例 —— 全程无 UI，不碰你正编辑的工程。
    返回每个输出路径是否成功。V3 加密工程也适用（引擎在软件内部执行）。
    """
    result, log = engine.export_blocks(project_path, blocks)
    return {"exported": result, "all_ok": all(result.values())}


@mcp.tool()
def smart_compile_and_export(project_path: str, blocks: dict) -> dict:
    """【全自动】编译工程并导出若干块（一次注入完成）。

    用于"改完验证"：编译看是否通过，同时导出块文本供检查。
    返回编译是否成功 + 每块导出是否成功。
    """
    compiled, exported, log = engine.compile_and_export(project_path, blocks)
    return {"compiled": compiled, "exported": exported}


@mcp.tool()
def smart_check_stl(awl_path: str) -> dict:
    """【离线秒级预检】静态检查 AWL 的网络结构，抓"无效程序段"。

    不启动软件，写完 AWL 先过这一关，省得白跑一趟部署。
    规则：一个 Network 只能有一条独立逻辑行(rung)。已双向验证（坏样本 9/9、好样本零误报）。

    注意这是【启发式规则】，权威判据是 smart_validate_project（问软件本人）。
    另：软件的编译 ret=0 **不代表没有无效程序段** —— 无效网络被排除在编译之外。
    """
    return stlcheck.check_file(awl_path)


@mcp.tool()
def smart_validate_project(project_path: str, block_names: list[str]) -> dict:
    """【权威判据】问软件本人：这些块里有没有"无效程序段"（打开工程会标红的那种）。

    走引擎 POU_IsValidNet 逐网络判定，是软件自己的答案，不是静态猜测。
    已用真实案例验证：已知坏样本精确报出 9 个无效网络、已知好样本 0 误报。
    只读 —— 不导入、不编译、不保存。返回每个块的网络总数与无效网络号。
    """
    return autoflow.validate_project(project_path, block_names)


@mcp.tool()
def smart_deploy(awl_files: list[str], project_path: str = "",
                 symbols: dict = None, verify_block: str = "",
                 open_after: bool = False) -> dict:
    """【全自动·推荐入口】把 AWL 块部署进工程并做四关验证，一步到位。

    四关（任何一关不过都会如实报 FAIL，不吹成功）：
      1 静态预检   离线秒级，先挡明显问题
      2 导入+编译  抓语法/交叉引用错误（CALL/ATCH 指向不存在的块）
      3 引擎真值   问软件本人 POU_IsValidNet 有无无效程序段 ← 权威判据
      4 往返导出   逐块核对指令与网络数，抓被软件静默丢弃的内容

    awl_files 顺序：主程序(ORGANIZATION_BLOCK/OB1)会自动排到最前（导入 OB1 会替换
    整个程序集）；其余按依赖排，被 CALL 的子程序、被 ATCH 的中断程序排在引用者之前。
    symbols = {"符号名": "绝对地址"}，如 {"电机启动": "I0.0", "电机运行": "Q0.0"}。
    设了就能在 AWL 里直接写符号名（`LD 电机启动`），可读性和客户现场维护性都好得多。
    project_path 留空则用【软件自带的空白模板】新建（不含任何已有工程内容）。
    open_after=True 会把工程留开给人看。
    """
    return autoflow.deploy(awl_files,
                           project_path=project_path or None,
                           symbols=symbols or None,
                           verify_block=verify_block or None,
                           open_after=open_after)


@mcp.tool()
def smart_set_symbols(project_path: str, symbols: dict) -> dict:
    """给 I/O 地址命名（写符号表），之后程序里就能用符号名代替 I0.0 这种绝对地址。

    symbols = {"符号名": "绝对地址"}，如 {"电机启动": "I0.0", "急停": "I0.7"}。
    做法：I/O 变量表里每个 I/O 点本来就有一行、地址是现成的，这里改的是那一行的名字。
    ⚠ 只支持 CPU 上实际存在的 I/Q 点；地址找不到对应行会如实报 ok=False，不会假装成功。
    ⚠ 会修改并另存工程（符号表必须靠 SAVEAS 落盘，普通保存存不下来）。
    """
    return autoflow.set_symbols(project_path, symbols)


@mcp.tool()
def smart_open_project(project_path: str) -> dict:
    """打开工程窗口给人看（从磁盘加载，项目树才会正确显示新导入的块）。

    注意：通过引擎 API 导入的块，对【已经开着的】窗口不会实时刷新项目树，
    必须重新打开工程才看得到 —— 所以给人看之前用这个工具重开。
    """
    pid = autoflow.open_project(project_path)
    return {"pid": pid, "project": project_path, "note": "已打开并置于前台"}


@mcp.tool()
def smart_import_blocks(project_path: str, awl_files: list[str], save: bool = True) -> dict:
    """【全自动】把 AWL 程序块导入工程并编译验证（一次注入）。

    awl_files 是 .awl 文件路径列表（可由你生成或修改）。已闭环验证：导入的改动真实落进工程。
    ⚠ 会修改工程；project_path 请用副本或新工程，别直接改客户原始工程。
    返回每个文件是否导入成功 + 编译是否通过。
    """
    imported, compiled, log = engine.import_and_compile(project_path, awl_files, save=save)
    return {"imported": imported, "compiled": compiled, "all_ok": all(imported.values()) and compiled}


@mcp.tool()
def smart_run_workflow(project_path: str, commands: list[str]) -> dict:
    """【全自动】在工程上执行一整条引擎脚本（一次注入，主线程顺序执行）。

    commands 每条是一个子命令（按顺序在主线程执行）：
      "EXPORT 块名|输出路径"   导出 POU 为 AWL
      "XML 块名|输出路径"      导出 POU 为 XML
      "IMPORTPOU AWL文件路径"  导入 AWL 程序块（改动真实落进工程，已闭环验证）
      "IMPORT 文件路径"        通用导入
      "COMPILE"                编译整个工程
      "VALIDATE 块名|0"        问引擎该块有无无效程序段（权威判据）
      "SAVE"                   保存工程
      "SAVEAS 路径"            另存工程
    返回执行日志里各步的返回码摘要。这是最灵活的入口，可把"导入→编译→导出确认"串成一条。
    ⚠ 含导入/保存时会修改工程，请用副本或新工程。
    """
    pid = engine.launch_instance(project_path)
    try:
        log = engine.run_script(pid, commands)
    finally:
        engine.kill_instance(pid)
    steps = [ln for ln in log.splitlines() if "script " in ln or "ret=" in ln]
    return {"steps": steps, "ok": "__DONE__" in log}


def main():
    mcp.run()


if __name__ == "__main__":
    main()
