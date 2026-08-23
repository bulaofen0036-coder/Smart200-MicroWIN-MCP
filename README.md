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

## 安装

```bash
pip install -r requirements.txt
claude mcp add smart200 -s user -- python E:/Smart200_Mcp/run.py
```

### 本机私有配置

模板工程、回归样本、UIA 交叉验证真值都是**客户工程内容，不入库**。
在仓库根建 `.smart200_local.json`（已 gitignore）：

```json
{
  "template_project": "建新工程用的模板 .smart 绝对路径",
  "samples_glob": "回归样本通配路径，如 D:/proj/**/*.smart",
  "truth_project": "UIA 交叉验证过的那个工程路径",
  "truth_pou_names": ["POU名1", "POU名2"]
}
```

缺这个文件不影响 MCP 主功能，只会让 `tests/test_all.py` 相关小节**如实打印 SKIP**
（不是静默通过）。`smart_deploy` 新建工程默认用**软件自带的空白模板**
（`template.smartV3`，在软件安装目录下），不需要 `template_project`；
配了它只是为了"以某个已有工程为底"。

`smart200_mcp/engine.py` 顶部还有两条本机绝对路径需按实际改：`INJECTOR`、`MWSMART`。

## 工具（21 个）

**全自动主入口**：`smart_deploy`（四关验证部署）`smart_validate_project`（问引擎要真值）
`smart_check_stl`（离线预检）`smart_open_project` `smart_run_workflow`
`smart_import_blocks` `smart_export_blocks` `smart_compile_and_export` `smart_awl_analyze`
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

## 安全红线（照搬 TIA MCP 的教训）

1. **注入只允许打到本模块自己启动的实例**（`engine._OWN_PIDS` 白名单，机器强制）。
   以前这里是个从没人往里加 PID 的黑名单，等于没有防护。
2. UI 层**只接管已打开的实例**，绝不替你打开/新建工程去顶掉正在编辑的内容
3. 绝不按进程名批量杀 `MWSmartV3.exe`
4. 写 PLC、点编译等会改变状态的操作，一律要求显式 `confirm=True`
5. 找不到控件/解不开文件就抛异常，**绝不静默返回空**——那会把失败伪装成成功

## 测试

```bash
python tests/test_all.py        # 容器/解析/地址层
python tests/test_stlcheck.py   # 无效程序段静态检查器防回归
python tests/test_enginelog.py  # 引擎日志判据防回归
```

三套都含**必错哨兵**（哨兵若 PASS 就说明测试本身坏了）：
`test_all` 3 个、`test_stlcheck` 3 反向 + 4 正向、`test_enginelog` 7 个。

`test_enginelog` 是补的欠账：判据以前散在 `engine`/`autoflow` 里且零覆盖，
于是"日志里路径带引号、判据按不带引号匹配"的 bug 长期存活（导入成功却恒报失败）。

## 已知边界

- **建不了符号表**（生成的程序只能用**绝对地址**；读已有工程时符号名正常带出来）。
  已排除的错路见 docs/ENGINE_INJECTION.md「符号表探路记录」：`PRJ_Import` 是 AWL 导入器、
  不管符号表（任何符号表文本都在第一个 token 报错）；扩展名是 `.sym` 不是 `.sdf`，但换了也没用。
  能用的部分：`PRJ_ExportGVT` **传全零 MW_ID 就能导出整张符号表**（但是二进制，编辑不了）。
  卡点：导入必须要真实的表 MW_ID。已能用 `GLBVAR_CreateUndefinedVariableTable` 拿到一个非零句柄，
  但 `SYM_InsertSymbol` 仍返 `0xA00007DA` —— 多半那张不是该写入的表。
- **枚举不了工程里有哪些块**：`POU_GetCount` 按 MW_IDType 枚举恒返 0，只能按名字查。
  想知道有哪些块得走 UI 层 `smart_ui_project_tree`。
- 下载到 PLC 未打通（`PRJ_Download` 未接线，snap7 层未经真机验证）
- 不还原逐网络 LAD/STL 逻辑（定长记录字段布局未逆向完，强行输出等于编造）
- 离线拿不到当前 CPU 型号（工程存模块 ID），改由 UI 层读
- `project_name` 是工程内部名，可能与文件名不同（另存改名后不同步），两者都对
- UI 层依赖简体中文界面 + V3.2，换语言或版本需重新探查控件名

详细逆向记录见 [docs/FORMAT.md](docs/FORMAT.md)。
