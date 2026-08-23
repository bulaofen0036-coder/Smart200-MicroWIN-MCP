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
（不是静默通过）。`smart_deploy` 不传 `project_path` 时需要 `template_project`。

`smart200_mcp/engine.py` 顶部还有两条本机绝对路径需按实际改：`INJECTOR`、`MWSMART`。

## 工具（20 个）

**全自动主入口**：`smart_deploy`（三关验证部署）`smart_check_stl`（静态查无效程序段）
`smart_open_project` `smart_run_workflow` `smart_import_blocks` `smart_export_blocks`
`smart_compile_and_export` `smart_awl_analyze`
**离线**：`smart_probe` `smart_list_projects` `smart_analyze` `smart_symbols`
`smart_function_blocks` `smart_compare`
**在线**：`smart_plc_info` `smart_plc_read` `smart_plc_write`
**界面**：`smart_ui_project_tree` `smart_ui_output` `smart_ui_compile`

## 三关验证（`smart_deploy`）

**血泪教训：软件的 `COMPILE ret=0` 不能单独当通过判据。** 无效程序段会被标红并
**排除在编译之外**，其余照常编译 → 返回成功，据此报"已验证"会骗人（真踩过）。

1. **静态结构**（`stlcheck.py`）—— 抓"无效程序段"。铁律：一个 Network 只能有一条独立
   逻辑行。规则用真实样本双向验证：已知坏样本精确命中 9/9、已知好样本零误报。
2. **导入 + 编译** —— 抓语法与交叉引用错误（`CALL`/`ATCH` 指向不存在的块）。
3. **往返导出核对** —— 抓被软件静默丢弃的指令。

任何一关不过都如实报 FAIL。`awl_files` 顺序有讲究：被依赖的块（中断程序、被 `CALL`
的子程序）要排在引用它们的块之前；**一个 .awl 含多个 BLOCK 时导入只吃到一个**，要拆文件。

## 安全红线（照搬 TIA MCP 的教训）

1. UI 层**只接管已打开的实例**，绝不替你打开/新建工程去顶掉正在编辑的内容
2. 绝不按进程名批量杀 `MWSmartV3.exe`
3. 写 PLC、点编译等会改变状态的操作，一律要求显式 `confirm=True`
4. 找不到控件/解不开文件就抛异常，**绝不静默返回空**——那会把失败伪装成成功

## 测试

```bash
python tests/test_all.py       # 容器/解析/地址层
python tests/test_stlcheck.py  # 无效程序段检查器防回归
```

`test_all` 含 3 个必错哨兵（V3 必须被拒、损坏文件必须被拒、非法地址必须被拒）。
`test_stlcheck` 用已知答案样本双向验证，另含 3 个反向哨兵 + 4 个正向哨兵。
**哨兵若 PASS 就说明测试本身坏了。**

## 已知边界

- 不还原逐网络 LAD/STL 逻辑（定长记录字段布局未逆向完，强行输出等于编造）
- 离线拿不到当前 CPU 型号（工程存模块 ID），改由 UI 层读
- `project_name` 是工程内部名，可能与文件名不同（另存改名后不同步），两者都对
- UI 层依赖简体中文界面 + V3.2，换语言或版本需重新探查控件名

详细逆向记录见 [docs/FORMAT.md](docs/FORMAT.md)。
