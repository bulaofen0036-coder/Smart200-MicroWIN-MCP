# AWL / STL 程序块文本格式

S7-200 SMART「文件→导出→程序块」产出的文本格式，也是引擎 `PRJ_ExportPOU` 的产出。
GBK 编码，CRLF 换行。样本：`samples/manual_exported_1212.awl`（手动导出）与
`samples/auto_exported_SBR0.awl`（引擎自动导出）—— 二者逐字节一致。

## 结构

```
<BLOCK_KIND> <名称>:<ID>          # 如 SUBROUTINE_BLOCK 初始化:SBR0
TITLE=<块注释>                    # 可选
BEGIN
Network <n>                       # 网络（rung），从 1 开始
//<网络注释>                       # 可选，紧跟 Network 行
	<助记符>  <操作数>[, <操作数>]   # 制表符缩进，一条指令一行
	...
Network <n+1>
	...
END_<BLOCK_KIND>
```

`BLOCK_KIND` ∈ {SUBROUTINE_BLOCK, PROGRAM_BLOCK, INTERRUPT_BLOCK, DATA_BLOCK}，
`ID` 形如 SBR0 / OB1 / INT0 / FB0。

## 指令样例（真实工程）

```
	LD     Always_On            # 装载
	LDN    V203.7               # 装载取反
	MOVB   1, VB200             # 传送字节 源,目标
	MOVW   +88, VW204           # 传送字（+ 表示常量）
	MOVD   +102400, VD260       # 传送双字
	SI     CPU_输出2, 1          # 立即置位
	RI     CPU_输出3, 1          # 立即复位
	R      V110.0, 1            # 复位
```

操作数可以是符号名（`Always_On`、`CPU_输出2`）或直接地址（`VB200`、`V203.7`）。
常量前带正负号（`+88`、`+102400`）。

## 解析器（smart200_mcp/awl.py）

- `parse_file(path)` → dict：块名/ID/kind/title + networks[{n, comment, instructions[{op, operands}]}]
- `analyze(block)` → 程序验证：网络数、指令直方图、写入地址、只读地址、网络注释

用于：改完程序后的验证、块与块对比、审查程序逻辑。地址读写分析可粗判未初始化/未使用地址。

## 编码注意

文件是 GBK。解析时 `open(path,"rb").read().decode("gbk")`。
注入引擎的命令文件也必须 GBK（软件内部按 GBK 比较块名）；
但注入 DLL 的日志字面量是 UTF-8 —— 判成功用 ASCII 标记，别混。
