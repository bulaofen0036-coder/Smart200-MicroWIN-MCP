# -*- coding: utf-8 -*-
"""回归测试。含必错哨兵：哨兵若通过 = 测试本身坏了。"""
import glob, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from smart200_mcp import container, project, strings, online, localcfg

# 真实工程样本是客户内容，不入库；路径与真值放本机 .smart200_local.json
_CFG = localcfg.load()

SAMPLES = (sorted(glob.glob(_CFG["samples_glob"], recursive=True))
           if _CFG.get("samples_glob") else [])
V3_SAMPLE = r"D:\smart200\template.smartV3"
fails = []

def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"  <- {detail}"))
    if not cond: fails.append(name)

print("=== 1. V2 容器解包（全部样本）===")
if SAMPLES:
    check("找到样本工程", len(SAMPLES) >= 8, f"只有 {len(SAMPLES)} 个")
else:
    print("  SKIP  未配置 samples_glob（.smart200_local.json），本节未运行")
for p in SAMPLES:
    try:
        pr = container.load(p)
        check(f"解包 {os.path.basename(p)[:20]}", len(pr.data) > 10000, f"{len(pr.data)}B")
    except Exception as e:
        check(f"解包 {os.path.basename(p)[:20]}", False, str(e))

print("=== 2. 哨兵：V3 必须被拒绝（若通过说明加密判定失效）===")
try:
    container.load(V3_SAMPLE); check("V3 被正确拒绝", False, "竟然解包成功了")
except container.UnsupportedProject:
    check("V3 被正确拒绝", True)

print("=== 3. 哨兵：损坏文件必须抛异常，不得静默返回空 ===")
bad = os.path.join(os.environ.get("TEMP", "."), "_broken.smart")
open(bad, "wb").write(b"SH3\x00R02.03.00.00" + b"\x00" * 0x60 + b"\xde\xad\xbe\xef" * 50)
try:
    container.load(bad); check("损坏文件被拒绝", False, "未抛异常")
except container.UnsupportedProject:
    check("损坏文件被拒绝", True)
finally:
    os.remove(bad)

print("=== 4. 字符串提取信噪比 ===")
pr = container.load(SAMPLES[0])
real = strings.texts(pr.data)
random.seed(1)
noise = strings.texts(bytes(random.randrange(256) for _ in range(len(pr.data))))
check("真实工程提取充分", len(real) > 100, f"{len(real)} 条")
check("随机噪声几乎提不出", len(noise) < len(real) / 10, f"噪声 {len(noise)} vs 真实 {len(real)}")

print("=== 5. 分析层（用 UIA 交叉验证过的真值）===")
p = _CFG.get("truth_project", "")
if p and os.path.exists(p):
    s = project.summary(container.load(p))
    check("POU 名 4/4 匹配 UIA 真值",
          set(s["pou_names"]) == set(_CFG.get("truth_pou_names", [])),
          str(s["pou_names"]))
    check("系统符号表锚点命中", s["system_table_found"])
    check("剔除系统 SM 符号后符号数合理", 5 < s["symbol_count"] < 100, str(s["symbol_count"]))
else:
    print("  SKIP  未配置 truth_project（.smart200_local.json），本节未运行")

print("=== 6. 地址解析 + 哨兵 ===")
for a in ["VB100", "VW200", "VD0", "V10.3", "M0.0", "QB0", "IW4"]:
    try: online.parse_address(a); check(f"接受 {a}", True)
    except Exception as e: check(f"接受 {a}", False, str(e))
for a in ["VB10.3", "V10", "XX5", "VW", "M0.8"]:
    try:
        online.parse_address(a); check(f"哨兵拒绝 {a}", False, "本应被拒却通过")
    except online.OnlineError: check(f"哨兵拒绝 {a}", True)

print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败: {fails}"))
sys.exit(1 if fails else 0)
