"""UI 自动化层：驱动已运行的 STEP 7-Micro/WIN SMART V3（Ribbon 界面，UIAutomation）。

红线（照搬 TIA MCP 的教训，不重犯）：
  1. 只接管【已经打开】的实例，绝不 OpenProject / 新建工程去顶掉用户正在编辑的工程。
  2. 绝不按进程名批量杀 MWSmartV3.exe。
  3. 编译等会改变软件状态的动作，必须调用方显式 confirm=True。
  4. 找不到控件就抛异常，绝不 catch 后静默返回空 —— 那会把失败伪装成成功。

实测状态（本机 V3.2.0 中文界面，2026-08-20）：
  read_project_tree  已实测通过（读出 CPU ST32 与全部 POU）
  compile_project    未实测 —— 用户当时正在该工程上作业，不便触发；代码按实测到的
                     控件名 Button:"编译" 编写，首次使用请在无关紧要的工程上先验。
本层依赖界面语言为简体中文；换语言或换版本控件名会变，需重新探查。
"""

import re

from pywinauto import Desktop

APP_CLASS = "SmartApp"
_POU_RE = re.compile(r"^(?P<name>.+?)\s*\((?P<id>OB\d+|SBR\d+|INT\d+|FB\d+)\)$")


class UiError(Exception):
    pass


def find_app():
    """返回已打开的 MicroWIN 主窗口。没有则抛异常（不代劳启动）。"""
    for w in Desktop(backend="uia").windows():
        try:
            if w.element_info.class_name == APP_CLASS:
                return w
        except Exception:
            continue
    raise UiError("未找到已运行的 STEP 7-Micro/WIN SMART。请先手动打开软件和工程；"
                  "本工具不会替你启动或打开工程，以免顶掉你正在编辑的内容。")


def _walk(el, want, depth=0, max_depth=7, out=None):
    if out is None:
        out = []
    if depth > max_depth:
        return out
    try:
        kids = el.children()
    except Exception:
        return out
    for k in kids:
        try:
            ctype = k.element_info.control_type
            text = (k.window_text() or "").strip()
        except Exception:
            continue
        if text and ctype in want:
            out.append((ctype, text))
        _walk(k, want, depth + 1, max_depth, out)
    return out


def read_project_tree():
    """只读读取当前工程：文件名、CPU 型号、POU 列表。已实测。"""
    app = find_app()
    title = app.window_text()
    items = [t for c, t in _walk(app, {"TreeItem"})]
    cpu = next((t for t in items if t.startswith("CPU ")), None)
    pous = []
    for t in items:
        m = _POU_RE.match(t)
        if m:
            pous.append({"name": m.group("name"), "id": m.group("id")})
    return {
        "window_title": title,
        "project_file": title.split(" - ")[0] if " - " in title else title,
        "cpu": cpu,
        "pous": pous,
        "pou_count": len(pous),
    }


def read_output_window():
    """读输出窗口文本（编译结果/错误列表）。只读。"""
    app = find_app()
    texts = [t for c, t in _walk(app, {"Text", "Edit", "ListItem"})]
    return {"lines": texts}


def compile_project(confirm=False):
    """点击 Ribbon 的【编译】并回读输出窗口。

    ⚠ 未实测。会改变软件状态（生成编译结果、可能弹错误列表），故要求 confirm=True。
    """
    if not confirm:
        raise UiError("compile_project 会操作你正在使用的软件界面，需显式传 confirm=True。"
                      "另注意：本函数尚未在真实界面上实测过。")
    app = find_app()
    for ctype, text in _walk(app, {"Button"}):
        if text == "编译":
            break
    else:
        raise UiError("Ribbon 上未找到【编译】按钮 —— 可能界面语言不是简体中文，"
                      "或当前 Ribbon 选项卡未展开到含该按钮的页。")
    btn = app.child_window(title="编译", control_type="Button")
    btn.wait("enabled", timeout=10)
    btn.click_input()
    return {"clicked": "编译", "note": "请用 read_output_window() 回读结果",
            "verified": False}
