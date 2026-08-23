# 进程内引擎调用（注入 MWSmartV3.exe）—— ✅ 已打通

目标：达到博途 Openness 的效果（导出/编译/保存/分析），走**调接口**而非 UI。
状态：**2026-08-20 完全打通**。一次注入可执行整条工作流（导入+编译+导出多块+保存），导出的 AWL 与手动导出【逐字节一致】，进程稳定不崩。
**导入已闭环验证**：改 AWL(MOVB 1→88)→导入→再导出，改动确实落进工程（不是 ret=0 假通过）。

## 一、最终可用架构

```
Python 编排 (engine.py)
  → 启动独立 MWSmartV3 实例（载入工程）
  → 写命令脚本文件 (GBK)
  → 注入 smarthook.dll（x86，静态 CRT）
      DllMain → 子类化【本进程】主窗口 → SendMessage(WM_APP+x) → 主线程执行脚本 → 还原 → 返回
  → 轮询结果文件直到 __DONE__
  → 关实例
```

脚本子命令（一次注入顺序执行全部）：
```
EXPORT 块名|路径     PRJ_ExportPOU  导出 AWL
XML    块名|路径     PRJ_ExportXML  导出 XML
COMPILE             PRJ_CompileAll 编译
SAVE                PRJ_Save       保存
SAVEAS 路径         PRJ_SaveAs     另存
```

## 二、关键 API（storeretrieveverify.dll，__thiscall）

| 用途 | 修饰名（节选） | this |
|---|---|---|
| 按名查块 MW_ID | `?POU_FindPouByName@MWRetrieve@@...` | g_Retrieve |
| 导出 AWL | `?PRJ_ExportPOU@MWRetrieve@@...` | g_Retrieve |
| 导出 XML | `?PRJ_ExportXML@MWRetrieve@@...` | g_Retrieve |
| 编译 | `?PRJ_CompileAll@MWStore@@QAEJXZ` | g_Store |
| 保存 | `?PRJ_Save@MWRetrieve@@QBEJXZ` | g_Retrieve |
| 当前工程号 | `?PRJ_GetCurrentProject@MWRetrieve@@...` | g_Retrieve |

全局单例（宿主初始化、持有工程树）：
`?g_Retrieve@@3VMWRetrieve@@A` @ srv+0x4db790，`?g_Store@@3VMWStore@@A` @ srv+0x4db788。

**MW_ID 不靠枚举**：`POU_GetCount` 那套（按 MW_IDType 枚举）始终返回 0，废弃；
改用 `POU_FindPouByName("初始化", &MW_ID)` 按块名拿 ID。
**⚠ 前提：必须先 PRJ_GetCurrentProject + PRJ_SetCurrentProject 设工程上下文**，
否则 FindPouByName 返回负数、id 全 0（详见第六节实录）。

## 三、踩过的坑（每条都真实付出过代价）

1. **injector 的 GetModuleHandleW 必须 CharSet=Unicode**：否则返 0 → 远程线程起始地址 NULL → 目标静默崩。
2. **注入 DLL 必须 /MT 静态 CRT**：MWSmartV3 静态链接 CRT，进程内无 VCRUNTIME140，动态链接的注入 DLL 因缺依赖 LoadLibrary 失败。
3. **必须找【本进程】的窗口**：`FindWindowW("SmartApp")` 会命中系统里第一个（可能是用户的实例），
   对跨进程窗口子类化失败、SendMessage 白发、DoWork 不执行。用 `EnumWindows + GetWindowThreadProcessId==GetCurrentProcessId` 精确匹配。
4. **必须回主线程**：数据管理器（g_PouDataMgr 等）上下文属主线程；旁路线程（CreateThread）直接调 API 读空/崩溃。
   经窗口消息在主线程执行才行。
5. **跨进程 SendMessage 会崩、常驻轮询也崩**：给 MFC 窗口跨进程发消息、或 DLL 内常驻轮询循环，都导致崩溃。
   稳定方案 = **一次注入执行一整条脚本后立即还原退出**（脚本模式）。
6. **同名 DLL 不能重注**：`LoadLibrary` 已加载的 DLL 不再触发 DllMain。所以多操作用【脚本】一次注入完成，不要多次注入。
7. **命令文件 GBK、日志混合编码**：软件内部按 GBK 比较块名，故命令文件写 GBK；
   而 DLL 的中文日志字面量是 UTF-8（/utf-8 编译）。判成功用 ASCII 标记（`ret=0`、`__DONE__`），编码无关。
8. **调试器断点会崩进程**：用 INT3 断点抓参数的方案反复弄崩软件，已放弃 —— 用只读内存 + FindPouByName 代替。

## 四、可复现脚手架

- `native/injector/` —— x86 CreateRemoteThread 注入器
- `native/bootstrap/smarthook_WORKING.cpp/.dll` —— 脚本执行钩子（当前可用版）
- `native/bootstrap/mkhook.bat` —— 编译脚本（需 D:\BuildTools 的 vcvars32）
- `smart200_mcp/engine.py` —— Python 编排（launch/inject/script/kill）
- `smart200_mcp/awl.py` —— AWL 解析与程序验证

MCP 工具：`smart_run_workflow`（整条脚本）、`smart_export_blocks`、`smart_compile_and_export`、`smart_awl_analyze`。

## 五、安全红线（已遵守）

- 只对【自己启动的独立实例】注入，绝不注入用户正在编辑的进程（`USER_PROTECTED_PIDS`）。
- 测试只用工程副本（`samples/dbg_target.smart`），绝不动客户原始 `.smart`。
- 收尾 kill 自启实例，不碰用户实例。

---

## 六、【突破实录】名字→导出 全自动打通（2026-08-20）

**全自动工作流已跑通**：给块名，起独立实例→注入脚本→按名查 MW_ID→导出 AWL→关实例，
产物与手动 UI 导出【逐字节一致】。

### 用 dbgcap 抓到 UI 导出的地面真相
用户手动导出 SBR16，dbgcap 在 PRJ_ExportPOU 断点抓到三件事：
- this(ecx) = g_Retrieve（srv+0x4db790）—— 确认注入用 g_Retrieve 当 this 是对的
- MW_ID(16B) = 28430f01 e9030000 1000e000 01000000（SBR16 并行查询读地址）
- bool 参数 = 0（不是 true）—— CString 路径对象是"指向字符数据的指针"槽，直接读出

### dbgcap 的致命 bug（"一导出就崩"的真因，非软件问题）
DEBUG_EVENT(x86) 里 ExceptionAddress 在 **偏移 24**；最初误读偏移 20（那是
ExceptionRecord 嵌套指针）→ 断点命中时 exAddr==bpAddr 恒假 → 把 INT3 异常
原样抛回进程 → 进程遇到自己没下过的 0xCC 崩溃。修正为偏移 24 后导出全程稳定。
（旁证：同版本未被调试的实例导出 OB 完全正常，正是用户指出的。）

### FindPouByName 必须先 SetCurrentProject —— 关键坑
`POU_FindPouByName("初始化", &MW_ID)` 单独调会失败（ret 负数、id 全 0）。
差别在：直接用真实 MW_ID 导出【不需要】工程上下文，但按名查 id【需要】——
smarthook_WORKING 在分发前先 `PRJ_GetCurrentProject` 拿工程号再 `PRJ_SetCurrentProject`
设上，FindPouByName 才返回正确 id。少了这步就是我 validate.cpp 里查名失败的原因。

（`POU_GetCount` 那套按 MW_IDType 枚举始终返 0，已废弃，改用 FindPouByName。）

### 端到端验证
`engine.export_blocks(工程, {"初始化": out})` → 全自动 → 产出 601B 的 AWL，
与用户手动导出的 1212.awl **逐字节完全一致**。

### AWL 就是导出格式
标准 STL 文本（GBK / CRLF）：`SUBROUTINE_BLOCK 名:SBRn` / `Network n` /
`助记符 操作数` / `END_SUBROUTINE_BLOCK`。离线解析器 smart200_mcp/awl.py 可结构化+程序分析。

## 无效程序段的权威判据：POU_IsValidNet（2026-08-23 打通）

```
?POU_IsValidNet@MWRetrieve@@QBEJABVMW_ID@@GAAHW4LANGUAGE@@@Z
long __thiscall MWRetrieve::POU_IsValidNet(MW_ID const&, unsigned short net,
                                          int& out, enum LANGUAGE) const
```

调用要点：

1. **网络索引从 0 起**，i 对应 AWL 里的 `Network i+1`。传 i=cnt 返 `0xA00007D3`(越界)，
   别把它当成"最后一段无效"。这个偏移一开始就是靠"引擎给的集合正好是真值整体减 1"看出来的。
2. `out==0` 表示该网络无效；`ret!=0` 表示调用本身出错，两者要分开报。
3. LANGUAGE 传 **0**（梯形图）。实测 0 与 2 结果一致；**传 1 会把所有网络都判成无效**，别用。
4. 网络总数用 `?POU_GetNetCnt@MWRetrieve@@QBEJABVMW_ID@@AAG@Z`，MW_ID 走引用，实测准确。

验证：坏样本（用户截图确认 9 个红网络）精确命中 9/9，好样本 0 误报，
且与静态 `stlcheck.py` 独立得出同一结论。

### 别用 LAD_GetNetworkDimensions

`?LAD_GetNetworkDimensions@MWRetrieve@@QAEJVMW_ID@@GPAE11@Z` 看名字像是能用
（问梯形图排版尺寸，画不出的就是无效网络），实测**恒返 `0xA00007D3` 且调用两三次后进程直接死**
（日志断在半截、没有 `__DONE__`）。区别在于它的 MW_ID 是**按值**传的，
而能用的那两个都是**按引用**。要拿真值就用 `POU_IsValidNet`。

## 这一轮踩出来的其它坑（2026-08-23）

- **日志编码不能混**：源码用 `/utf-8` 编译，字面量是 UTF-8；但命令文件里的块名/路径是 GBK 字节，
  直接 `%s` 打进日志 → 同一个文件里两种编码。Python 侧按 UTF-8 解，中文块名成乱码，
  于是"明明验过的块"被判成没验过、整关误报 FAIL。解法：所有来自命令文件的串先过 
  `MultiByteToWideChar(936) -> WideCharToMultiByte(CP_UTF8)` 再打日志。

- **主程序关键字是 `ORGANIZATION_BLOCK`**，不是 `PROGRAM_BLOCK`。写错时 `PRJ_ImportPouFile` 
  **返回 ret=0 但什么都不导入** —— 典型的"报成功其实没做"，只有往返导出核对才抓得到。

- **导入 OB1 = 替换整个程序集**：先导入的子程序会被抹掉，随后编译报 `-1610612428`(交叉引用)，
  但四条 IMPORTPOU 全是 ret=0，看日志找不出真因。主程序必须排在最前面。
  这与"导出 OB1 会把所有块一起导出"是对称的 —— OB1 那份就代表整个程序。

- **导出连依赖一起出**：`PRJ_ExportPOU` 最后那个 bool 传 true 时，导出文件里会有目标块 +
  它 CALL/ATCH 的块。核对网络数前必须先切出目标块那一段。

- **窗口标题带不带扩展名不一致**：`.smart` 显示 `x.smart - STEP 7...`，
  `.smartV3` 显示 `x - STEP 7...`。判就绪只能认**去掉扩展名的主名**。
  实测启动 ~0.8s 出窗口、**~16s 标题才带工程名**（此前是死等 `sleep(26)`）。

- **注入器输出别用 `text=True`**：它是 GBK，默认解码会在 subprocess 读取线程里抛
  `UnicodeDecodeError` —— 线程内异常，主流程看不见，只在 stderr 冒一堆栈。

### 还没拿下

**符号表建不了**。`SYM_InsertSymbol`（有个全字符串参数的重载，很好调）、`SYM_GetSymbolRows`、
`SYM_SaveSymbolTable` 都在，但入口卡在拿不到符号表的 MW_ID：
`SYM_FindSymbol("Always_On", ...)` 恒返 `-1610610729`、表 id 全零（`SetCurrentProject` 已做过）。
`.sdf` 文本走 `PRJ_Import` 也被拒（`-1610610033`，带表头 CSV 和 TAB 分隔两种形态都试过）。
下一步应该是拿 dbgcap 挂在软件的"符号表"界面动作上，抓真实的 MW_ID 与调用顺序。

附带确认：**未定义的符号名不会让编译报错，而是让整个网络变成无效程序段** —— 第 3 关抓得到。
