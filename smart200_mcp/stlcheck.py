"""STL 网络有效性静态检查 —— 抓"无效程序段"。

为什么需要它：软件的 COMPILE 返回 0 **不代表没有无效程序段**——无效网络会被
标红并排除在编译之外，其余照常编译，所以 ret=0 会骗人（吃过这个亏）。
必须独立检查。

核心规则（从真实数据反推并双向验证）：
  S7-200 SMART 一个 Network 只能包含【一条独立逻辑行(rung)】。

  rung 数 = LD 类指令数 - 多输入功能框额外消耗的 LD
    · CTU / CTD  用 2 个 LD（CU/R、CD/LD），额外消耗 1
    · CTUD       用 3 个 LD（CU/CD/R），额外消耗 2
  rung 数必须 == 1，否则该网络无效。

  例外：只含 NEXT / LBL 等独立指令、不含任何 LD 的网络是合法的。
  另：FOR 与 NEXT 不能出现在同一个网络（NEXT 必须单独成段）。

验证依据：用 demo_v2（用户截图确认无效网络 = 2,3,4,14,15,19,22,25,26）
和 demo_v3（用户确认全部有效）双向验证，两边均 100% 吻合。
"""

import re

# 软件的文本用系统 ANSI 代码页（中文机器上就是 GBK）；写死 gbk 在非中文系统会崩
ANSI = "mbcs"

# 逻辑行起始指令（装载类，含比较装载 LDW= / LDB>= 等）
_LD_RE = re.compile(r"^LD[NIA]?$|^LD[BWDR][=<>]{1,2}$|^LDN$")
# 会"吃掉"额外 LD 的指令：
#   计数器多输入框：CTU/CTD 用 2 个 LD、CTUD 用 3 个，但都只算 1 条 rung
#   块操作 OLD/ALD：把两个逻辑块合并成 1 条 rung，各吃掉 1 个 LD
_MULTI_INPUT = {"CTU": 1, "CTD": 1, "CTUD": 2, "OLD": 1, "ALD": 1}
# 不需要 rung 的独立指令
_STANDALONE = {"NEXT", "LBL", "MEND", "END"}


def _is_ld(op):
    return bool(_LD_RE.match(op))


def parse_networks(text):
    """把 AWL 文本解析成 [{n, comment, ops:[助记符]}]。"""
    text = text.replace("\r\n", "\n")
    nets, cur = [], None
    for line in text.split("\n"):
        m = re.match(r"Network\s+(\d+)", line.strip())
        if m:
            cur = {"n": int(m.group(1)), "comment": None, "ops": []}
            nets.append(cur)
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith("//"):
            if cur["comment"] is None:
                cur["comment"] = s[2:]
            continue
        if line.startswith("\t") and s:
            cur["ops"].append(s.split()[0])
    return nets


# 纯逻辑运算（不产生输出，只在栈上操作）
_LOGIC_ONLY = {"A", "O", "AN", "ON", "AI", "OI", "ANI", "ONI", "NOT",
               "EU", "ED", "LPS", "LRD", "LPP", "OLD", "ALD"}
_CMP_RE = re.compile(r"^[AO][BWDR][=<>]{1,2}$")   # 串/并联比较：AW< OW> 等


def _produces_output(op):
    """这条指令是否产生输出（线圈/功能框）——输出之后再出现 LD 就是新 rung。"""
    if _is_ld(op) or op in _LOGIC_ONLY or op in _STANDALONE:
        return False
    if _CMP_RE.match(op):
        return False
    return True


def check_network(net):
    """检查单个网络。返回 (是否有效, 原因)。

    判据：一个 Network 只能有一条 rung。
    扫描指令流，维护 LPS 分支深度；当【分支已闭合(深度0)】且【已经产生过输出】时
    又遇到 LD，说明开了新的一条 rung → 该网络无效。
    这样能正确处理：多输入计数器框(连续 LD，中间无输出)、OLD/ALD 块操作、
    LPS/LRD/LPP 一条件多输出分支。
    """
    ops = net["ops"]
    if not ops:
        return True, "空网络"

    if "FOR" in ops and "NEXT" in ops:
        return False, "FOR 与 NEXT 在同一网络（NEXT 必须单独成段）"

    if not any(_is_ld(o) for o in ops):
        if all(o in _STANDALONE for o in ops):
            return True, "独立指令段"
        return False, f"缺少 LD 起始（ops={ops[:4]}）"

    rungs = 0
    depth = 0          # LPS 分支深度
    seen_output = False
    for op in ops:
        if _is_ld(op):
            if rungs == 0:
                rungs = 1
            elif depth == 0 and seen_output:
                rungs += 1          # 分支已闭合又出输出后再 LD = 新逻辑行
            continue
        if op == "LPS":
            depth += 1
        elif op == "LPP":
            depth = max(0, depth - 1)
        elif _produces_output(op):
            seen_output = True

    if rungs > 1:
        return False, (f"包含 {rungs} 条独立逻辑行，一个网络只能有 1 条"
                       f"（请拆成 {rungs} 个 Network）")
    return True, "OK"


def check(text):
    """检查整段 AWL。返回 {valid, total, invalid:[{n, reason, comment}]}"""
    nets = parse_networks(text)
    bad = []
    for net in nets:
        ok, why = check_network(net)
        if not ok:
            bad.append({"network": net["n"], "reason": why, "comment": net["comment"]})
    return {"valid": not bad, "total": len(nets), "invalid_count": len(bad), "invalid": bad}


def check_file(path):
    with open(path, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode(ANSI)
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "replace")
    return check(text)
