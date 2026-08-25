# 指令支持性探针 —— 问软件"这条指令你到底认不认"

## 为什么需要

S7-200 SMART 对**不认识的助记符**不报编译错，而是把它当成"未定义的符号名"，
让**整个网络变成无效程序段**。编译照样 `ret=0`，只有 `POU_IsValidNet` 抓得到。
所以"这条指令 SMART 支不支持"不能靠记忆和文档，要问软件本人。

## 方法

一个 Network 放一条待测指令（`LD SM0.0` + 待测指令），导入后逐网络 `VALIDATE`：
**无效的网络 = 不支持的指令**。网络与指令一一对应，结果没有歧义。

```
smart_run_workflow(<模板副本>, [
  "IMPORTPOU <本目录>\probe_ops.awl",
  "VALIDATE 指令支持性探针|0",
])
```

`probe_map.txt` 是网络号 → 指令名的对照表。

## 2026-08-25 实测结果（22 条）

**不支持（2 条）**：`NETR`、`NETW`
> 这是 S7-200 的 PPI 网络读写。SMART 的以太网 S7 通信用 `GET` / `PUT`，
> STL 单操作数：`GET VB780` / `PUT VB800`。
> 两种写法（带端口号和不带）都无效，所以不是操作数格式问题。

**支持（20 条，此前曾被怀疑不支持的都在里面）**：
`GET` `PUT` `GPA` `SPA` `ITA` `DTA` `RTA` `SLEN` `SCPY` `SCAT` `SSCPY`
`SFND` `CFND` `DECO` `ENCO` `BIW` `FND=` `TODRX` `TODWX` `PID`

## 注意

操作数写错也会让网络无效，会和"指令不支持"混淆。
判定某条不支持之前，**换一种操作数写法再试一次**（`NETR` 就是这么排除的）。

结论已收进 `smart200_mcp/stlcheck.py` 的 `_NOT_IN_SMART` 黑名单 ——
**只收实测判定过的，不收凭印象怀疑的**，`tests/test_persist.py` 里有反向哨兵守着。
