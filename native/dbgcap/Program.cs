// dbgcap —— 附加到运行中的 MWSmartV3.exe，在 POU_GetCount 下 INT3 断点，
// 捕获 UI 自己调用它时的真实 this(ECX) 和 MW_IDType 值。这是拿到正确调用参数的确定方法。
// 用法: dbgcap <pid>   然后在 MWSmartV3 里点一下项目树/POU，触发它枚举。
using System;
using System.Runtime.InteropServices;

class DbgCap {
    const uint DBG_CONTINUE = 0x00010002;
    const uint DBG_EXCEPTION_NOT_HANDLED = 0x80010001;
    const uint EXCEPTION_DEBUG_EVENT = 1;
    const uint EXCEPTION_BREAKPOINT = 0x80000003;
    const uint EXCEPTION_SINGLE_STEP = 0x80000004;
    const uint CONTEXT_FULL = 0x10007;

    [DllImport("kernel32")] static extern bool DebugActiveProcess(int pid);
    [DllImport("kernel32")] static extern bool DebugActiveProcessStop(int pid);
    [DllImport("kernel32")] static extern bool DebugSetProcessKillOnExit(bool b);
    [DllImport("kernel32")] static extern bool WaitForDebugEvent(byte[] e, uint ms);
    [DllImport("kernel32")] static extern bool ContinueDebugEvent(int pid, int tid, uint status);
    [DllImport("kernel32")] static extern IntPtr OpenThread(uint acc, bool inh, int tid);
    [DllImport("kernel32")] static extern IntPtr OpenProcess(uint acc, bool inh, int pid);
    [DllImport("kernel32")] static extern bool GetThreadContext(IntPtr h, byte[] ctx);
    [DllImport("kernel32")] static extern bool SetThreadContext(IntPtr h, byte[] ctx);
    [DllImport("kernel32")] static extern bool ReadProcessMemory(IntPtr h, IntPtr addr, byte[] buf, int n, out int read);
    [DllImport("kernel32")] static extern bool WriteProcessMemory(IntPtr h, IntPtr addr, byte[] buf, int n, out int wr);
    [DllImport("kernel32")] static extern bool FlushInstructionCache(IntPtr h, IntPtr addr, int n);
    [DllImport("psapi")] static extern bool EnumProcessModulesEx(IntPtr h, IntPtr[] m, int cb, out int need, uint filter);
    [DllImport("psapi", CharSet=CharSet.Unicode)] static extern int GetModuleFileNameExW(IntPtr h, IntPtr m, System.Text.StringBuilder b, int sz);

    // x86 CONTEXT 关键偏移（CONTEXT_i386）
    const int CTX_SIZE = 716;
    const int OFF_EIP = 0xB8, OFF_ESP = 0xC4, OFF_EAX = 0xB0, OFF_ECX = 0xAC, OFF_EDX = 0xA8, OFF_EFLAGS = 0xC0;

    static IntPtr hProc;
    static byte origByte;
    static IntPtr bpAddr;
    static int hitCount = 0;

    static uint RD32(IntPtr a){ var b=new byte[4]; ReadProcessMemory(hProc,a,b,4,out _); return BitConverter.ToUInt32(b,0); }

    static void Main(string[] args) {
        int pid = int.Parse(args[0]);
        hProc = OpenProcess(0x1FFFFF, false, pid);
        // 找 storeretrieveverify.dll 基址
        var mods = new IntPtr[2048];
        EnumProcessModulesEx(hProc, mods, mods.Length*4, out int need, 3);
        IntPtr srvBase = IntPtr.Zero;
        for (int i=0;i<need/4;i++){
            var sb=new System.Text.StringBuilder(260);
            GetModuleFileNameExW(hProc, mods[i], sb, 260);
            if (sb.ToString().ToLower().EndsWith("storeretrieveverify.dll")) { srvBase=mods[i]; break; }
        }
        Console.WriteLine($"storeretrieveverify 基址 = 0x{srvBase.ToInt64():x}");
        // 断点目标由命令行第二参数的 RVA 指定；默认 POU_GetCount。
        long rva = args.Length > 1 ? Convert.ToInt64(args[1], 16) : 0x113980;
        bpAddr = srvBase + (int)rva;
        Console.WriteLine($"断点 @ 0x{bpAddr.ToInt64():x} (RVA 0x{rva:x})");

        DebugSetProcessKillOnExit(false);
        if (!DebugActiveProcess(pid)) { Console.WriteLine("DebugActiveProcess 失败 "+Marshal.GetLastWin32Error()); return; }
        // 写 INT3
        var orig=new byte[1]; ReadProcessMemory(hProc, bpAddr, orig, 1, out _); origByte=orig[0];
        WriteProcessMemory(hProc, bpAddr, new byte[]{0xCC}, 1, out _);
        FlushInstructionCache(hProc, bpAddr, 1);
        Console.WriteLine("断点已下。请在 MWSmartV3 里点项目树/切换 POU 触发枚举。等待命中（最多 60s）...");

        var evt = new byte[192];
        long deadline = Environment.TickCount64 + 300000;
        while (Environment.TickCount64 < deadline && hitCount < 8) {
            if (!WaitForDebugEvent(evt, 500)) continue;
            uint code = BitConverter.ToUInt32(evt,0);
            int epid = BitConverter.ToInt32(evt,4);
            int etid = BitConverter.ToInt32(evt,8);
            uint cont = DBG_CONTINUE;
            if (code == EXCEPTION_DEBUG_EVENT) {
                uint exCode = BitConverter.ToUInt32(evt,12);
                // DEBUG_EVENT(x86): code@0 pid@4 tid@8; EXCEPTION_RECORD 从 12 起：
                // ExceptionCode@12 Flags@16 RecordPtr@20 【ExceptionAddress@24】
                IntPtr exAddr = (IntPtr)BitConverter.ToUInt32(evt,24);
                if (exCode == EXCEPTION_BREAKPOINT && exAddr == bpAddr) {
                    CaptureAndRestore(etid);   // 抓参数 + 永久还原原字节 + 修正 EIP
                    hitCount = 999;            // 抓一次就够，结束循环
                } else if (exCode == EXCEPTION_BREAKPOINT) {
                    // 初始 attach 断点等系统断点：我方处理掉（DBG_CONTINUE），别抛回进程
                    cont = DBG_CONTINUE;
                } else {
                    // 其它异常（第一现场）交回进程自己的处理链
                    cont = DBG_EXCEPTION_NOT_HANDLED;
                }
            }
            ContinueDebugEvent(epid, etid, cont);
        }
        // 还原
        WriteProcessMemory(hProc, bpAddr, new byte[]{origByte}, 1, out _);
        FlushInstructionCache(hProc, bpAddr, 1);
        DebugActiveProcessStop(pid);
        Console.WriteLine($"完成，命中 {hitCount} 次。");
    }

    static void CaptureAndRestore(int tid) {
        hitCount++;
        IntPtr hThread = OpenThread(0x1F03FF, false, tid);
        var ctx = new byte[CTX_SIZE];
        BitConverter.GetBytes(CONTEXT_FULL).CopyTo(ctx, 0);
        GetThreadContext(hThread, ctx);
        uint eip = BitConverter.ToUInt32(ctx, OFF_EIP);
        uint esp = BitConverter.ToUInt32(ctx, OFF_ESP);
        uint ecx = BitConverter.ToUInt32(ctx, OFF_ECX);
        // PRJ_ExportPOU(MW_ID const&, CString const&, bool) __thiscall const:
        //   this=ECX; [esp]=ret; [esp+4]=&MW_ID; [esp+8]=&CString; [esp+0xC]=bool
        uint retAddr = RD32((IntPtr)esp);
        uint pMwId = RD32((IntPtr)(esp+4));
        uint pCStr = RD32((IntPtr)(esp+8));
        uint bFlag = RD32((IntPtr)(esp+0xC));
        // MW_ID 是 16 字节 GUID —— 读出来
        var idBuf = new byte[16]; ReadProcessMemory(hProc, (IntPtr)pMwId, idBuf, 16, out _);
        string idHex = BitConverter.ToString(idBuf).Replace("-", "").ToLower();
        // CString: 对象是指向字符数据的指针；读出路径字符串
        uint pChars = RD32((IntPtr)pCStr);
        var sbuf = new byte[260]; ReadProcessMemory(hProc, (IntPtr)pChars, sbuf, 260, out _);
        int z = Array.IndexOf(sbuf, (byte)0); if (z < 0) z = 260;
        string path = System.Text.Encoding.Default.GetString(sbuf, 0, z);
        Console.WriteLine($"  [命中] this(ecx)=0x{ecx:x}");
        Console.WriteLine($"  MW_ID(16B) = {idHex}");
        Console.WriteLine($"  导出路径   = {path}");
        Console.WriteLine($"  bool参数   = {bFlag}");
        Console.WriteLine($"  返回地址   = 0x{retAddr:x}");
        Console.Out.Flush();

        // 【永久还原原字节】—— 不再重下断点，避免多线程竞态崩溃。让这次调用正常完成。
        WriteProcessMemory(hProc, bpAddr, new byte[]{origByte}, 1, out _);
        FlushInstructionCache(hProc, bpAddr, 1);
        // EIP 回退到指令起点，让 CPU 重新执行已还原的原指令
        BitConverter.GetBytes(eip - 1).CopyTo(ctx, OFF_EIP);
        SetThreadContext(hThread, ctx);
    }
}
