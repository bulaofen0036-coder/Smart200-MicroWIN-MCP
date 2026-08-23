# Smart200 MCP

用 MCP 驱动 S7-200 SMART（STEP 7-Micro/WIN SMART V3）做工程分析、在线通讯与界面自动化。

对标本机的 TIA Portal MCP，但**架构完全不同**：MicroWIN SMART 没有 Openness
等价物（无 COM、无命令行、内置 JSON-RPC 只管 PROFINET、内置 AI 只连云端问答），
所以做成三层混合，各层可靠度不同，工具描述里都如实标注。

## 能力与可靠度

| 层 | 能力 | 状态 |
|---|---|---|
| **引擎调用（注入）** | **导出 AWL/XML、编译、保存、整条脚本工作流** | ✅ **已打通**，与手动导出逐字节一致，稳定不崩 |
| AWL 解析 | 程序块文本解析 + 程序验证（网络/指令/地址读写） | ✅ 实测 |
| 离线工程解析 | V2 `.smart` 解包、符号、POU 名、功能框统计、工程对比 | ✅ 11/11 真实工程验证 |
| | V3 `.smartV3` 离线 | ❌ 加密 —— 但**引擎注入可处理 V3 工程** |
| UI 自动化 | 读项目树（CPU 型号 + POU 列表） | ✅ 实测（引擎路线打通后已非主力） |
| 在线通讯 | snap7 读写 V/M/I/Q、CPU 信息 | ⚠️ 代码就绪，**未经真机验证** |

**全自动工作流**（对标博途 MCP）：`smart_run_workflow(工程路径, ["COMPILE","EXPORT 初始化|out.awl","SAVE"])`
一次注入执行整条流程，无 UI、毫秒级、进程稳定。详见 docs/ENGINE_INJECTION.md。

**最实用的一点**：V3.x 打不开老的 V2 工程（需迁移，且迁移只吃 V2.8 存的）。
本工具能直接离线读 V2 `.smart`，**不装 V2.8 也能看老天车程序的符号、POU 和用了哪些指令**。

## 三步上手

```bash
# 1. 依赖
pip install -r requirements.txt

# 2. 编译注入用的 DLL（自动找 VS Build Tools，找不到会告诉你缺什么）
python native/bootstrap/build.py

# 3. 挂上 MCP
claude mcp add smart200 -s user -- python <仓库路径>/run.py
```

挂好之后**先跑 `smart_doctor`**，它会一次说清缺什么、怎么补。

**路径不用改。** 仓库放哪个盘都行（注入 DLL 运行时自寻路径），
MicroWIN 安装位置自动探测。探测不到时按 `smart_doctor` 的提示设一个环境变量：

| 环境变量 | 作用 |
|---|---|
| `SMART200_EXE` | MWSmartV3.exe 完整路径 |
| `SMART200_TEMPLATE` | 空白模板工程路径（默认取安装目录下的 `template.smartV3`） |
| `SMART200_SCRIPT_TIMEOUT` | 单次注入等待上限，默认 180 秒；大工程编译慢可调大 |

也可以写进仓库根的 `.smart200_local.json`（已 gitignore）：

```json
{
  "mwsmart_exe": "D:/smart200/MWSmartV3.exe",
  "blank_template": "D:/smart200/template.smartV3",
  "samples_glob": "回归样本通配路径（可选，只影响 test_all 的部分小节）",
  "truth_project": "UIA 交叉验证过的工程（可选）",
  "truth_pou_names": ["POU名1", "POU名2"]
}
```

## 90% 的活只用一个工具

```python
smart_deploy(
    awl_files=["motor.awl"],
    symbols={"电机启动": "I0.0", "电机运行": "Q0.0"},   # 可选：给 I/O 命名
    open_after=True,                                    # 顺手打开给人看
)
```

它一次注入里把**设符号 → 导程序 → 编译 → 四关验证 → 保存**全干完。
别拆成 `smart_set_symbols` + `smart_deploy` + `smart_open_project` 三次调用 ——
每次调用都要重启一个 MicroWIN 实例（约 16 秒等工程载入），拆开就是三倍时间。

## 工具（23 个）

**先跑这个**：`smart_doctor`（环境自检）
**主入口**：`smart_deploy`（设符号+部署+四关验证，一步到位）
**常用**：`smart_validate_project`（问引擎要真值）`smart_check_stl`（离线秒级预检）
`smart_export_blocks`（把现有工程的块导成 AWL 来读）`smart_awl_analyze`
**其余**：`smart_set_symbols` `smart_open_project` `smart_run_workflow`
`smart_import_blocks` `smart_compile_and_export`
**离线**：`smart_probe` `smart_list_projects` `smart_analyze` `smart_symbols`
`smart_function_blocks` `smart_compare`
**在线**：`smart_plc_info` `smart_plc_read` `smart_plc_write`
**界面**：`smart_ui_project_tree` `smart_ui_output` `smart_ui_compile`

## 四关验证（`smart_deploy`）

**血泪教训：软件的 `COMPILE ret=0` 不能当通过判据。** 无效程序段会被标红并
**排除在编译之外**，其余照常编译 → 返回成功，据此报"已验证"会骗人（真踩过）。

| 关 | 干什么 | 性质 |
|---|---|---|
| 1 静态预检 | `stlcheck.py`：一个 Network 只能有一条 rung | 离线秒级，**启发式** |
| 2 导入+编译 | 抓语法与交叉引用错误（`CALL`/`ATCH` 指向不存在的块） | 必要不充分 |
| 3 **引擎真值** | `POU_IsValidNet` 逐网络问软件本人 | **权威判据** |
| 4 往返导出 | 逐块核对指令集 + 网络数，抓静默丢弃 | 抓"说成功其实没做" |

第 3 关才是权威：第 1 关是我写的规则、可能有盲区，第 3 关是软件自己的答案。
两者在已知样本上完全一致（坏样本精确命中 9/9、好样本零误报），不一致时以第 3 关为准。

### 用 AWL 干活必须知道的几条（都是实测踩出来的）

- **主程序的关键字是 `ORGANIZATION_BLOCK`，不是 `PROGRAM_BLOCK`**。写错了
  `IMPORTPOU` 照样返回 `ret=0`，但**什么都没导进去**（第 4 关才抓得到）。
- **导入 OB1 会替换整个程序集** —— 先导入的子程序会被抹掉。所以主程序必须排最前
  （`smart_deploy` 会自动重排并在报告里说明）。这与"导出 OB1 会把所有块一起导出"对称。
- 其余按依赖排：被 `CALL` 的子程序、被 `ATCH` 的中断程序要在引用它们的块之前。
- **一个 .awl 含多个 BLOCK 时导入只吃到一个** → 拆成多个文件。
- **导出会连依赖块一起导出**（`PRJ_ExportPOU` 最后那个 bool 传的 true），所以导出的
  .awl 里常有好几个 BLOCK —— 核对时要先切出目标块那一段，否则网络数对不上。
- **未定义的符号名会让整个网络变成无效程序段**（不是编译报错），第 3 关能抓到。

## 符号表（已打通）

给 I/O 地址命名后，AWL 里就能直接写符号名，可读性和现场维护性好得多：

```python
smart_deploy(["motor.awl"], symbols={"电机启动": "I0.0", "电机停止": "I0.1", "电机运行": "Q0.0"})
```

```
Network 1
	LD     电机启动
	O      电机运行
	AN     电机停止
	=      电机运行
```

**原理（踩了很多弯路才搞清）**：

- **符号表在引擎里叫 GVT（全局变量表）**，一个工程有 7 张：
  `I/O 变量`、`变量表 1`、`常量表 1`、`POU Variables`、`系统变量表`、`FB实例表`、`系统运动控制变量表`。
- **绝对地址符号走「I/O 变量」表**：这张表本来就把每个 I/O 点列全了、地址是现成的，
  所以是**改那一行的名字**，不是新建行 —— 在空行上 `SetAddressValue` 恒报 6019，新行设不了地址。
- **「变量表 1」是 V 区变量表，不是符号表**：那里只给名字和类型，**地址由编译器自动分配**
  （实测分到 `DB2.DBX0.0`）。想绑死 `I0.0` 这种绝对地址就别用它。
- **符号表必须用 SAVEAS 落盘**：`PRJ_Save` 不保存变量表 —— 存完重开符号全没了（踩过）。
  `smart_deploy` 带 `symbols` 时会自动 SAVEAS 到临时文件再替换回去。
- **未定义的符号名不报编译错，而是让整个网络变成无效程序段** —— 第 3 关抓得到。

## 安全红线（照搬 TIA MCP 的教训）

1. **注入只允许打到本模块自己启动的实例**（`engine._OWN_PIDS` 白名单，机器强制）。
   以前这里是个从没人往里加 PID 的黑名单，等于没有防护。
2. UI 层**只接管已打开的实例**，绝不替你打开/新建工程去顶掉正在编辑的内容
3. 绝不按进程名批量杀 `MWSmartV3.exe`
4. 写 PLC、点编译等会改变状态的操作，一律要求显式 `confirm=True`
5. 找不到控件/解不开文件就抛异常，**绝不静默返回空**——那会把失败伪装成成功
6. **改已存在的工程前自动备份** `<工程>.bak`（以前只在文档里写"请用副本"，靠自觉）
7. **进程退出时收掉自己起的实例**（`atexit`），只收自己起的，绝不按进程名批量杀
8. **注入串行化**：命令/结果文件全局只有一份，并发会串台且串台结果看着像正常结果

## 测试

```bash
python tests/test_stlcheck.py   # 纯逻辑，clone 下来就能跑
python tests/test_enginelog.py  # 纯逻辑，clone 下来就能跑
python tests/test_all.py        # 容器/解析层，部分小节需要真实工程样本（缺了会打印 SKIP）
```

三套都含**必错哨兵**（哨兵若 PASS 就说明测试本身坏了）：
`test_all` 3 个、`test_stlcheck` 3 反向 + 4 正向、`test_enginelog` 7 个。

`test_enginelog` 是补的欠账：判据以前散在 `engine`/`autoflow` 里且零覆盖，
于是"日志里路径带引号、判据按不带引号匹配"的 bug 长期存活（导入成功却恒报失败）。

## 已知边界

- **枚举不了工程里有哪些块**：`POU_GetCount` 按 MW_IDType 枚举恒返 0，只能按名字查。
  想知道有哪些块得走 UI 层 `smart_ui_project_tree`。
- 下载到 PLC 未打通（`PRJ_Download` 未接线，snap7 层未经真机验证）
- 不还原逐网络 LAD/STL 逻辑（定长记录字段布局未逆向完，强行输出等于编造）
- 离线拿不到当前 CPU 型号（工程存模块 ID），改由 UI 层读
- `project_name` 是工程内部名，可能与文件名不同（另存改名后不同步），两者都对
- UI 层依赖简体中文界面 + V3.2，换语言或版本需重新探查控件名

详细逆向记录见 [docs/FORMAT.md](docs/FORMAT.md)。
