using System;
using System.Runtime.InteropServices;

unsafe class Engine {
    const string DIR = @"D:\smart200";
    [DllImport("kernel32", CharSet=CharSet.Unicode)] static extern IntPtr LoadLibraryW(string p);
    [DllImport("kernel32", CharSet=CharSet.Unicode)] static extern bool SetDllDirectoryW(string p);
    [DllImport("kernel32")] static extern IntPtr GetProcAddress(IntPtr h, string n);
    [DllImport("kernel32")] static extern bool VirtualProtect(IntPtr a, uint sz, uint np, out uint op);
    [DllImport("kernel32")] static extern IntPtr AddVectoredExceptionHandler(uint first, VehHandler h);
    delegate int VehHandler(IntPtr pExceptionInfo);
    static IntPtr srvBase;
    static int Veh(IntPtr info) {
        IntPtr rec = Marshal.ReadIntPtr(info, 0);
        uint code = (uint)Marshal.ReadInt32(rec, 0);
        IntPtr addr = Marshal.ReadIntPtr(rec, 12);
        if (code == 0xC0000005) {
            long off = addr.ToInt64() - srvBase.ToInt64();
            Console.WriteLine($"    !! 访问违规 @0x{addr.ToInt64():x}  = storeretrieveverify+0x{off:x}");
            IntPtr bad = Marshal.ReadIntPtr(rec, 20);
            Console.WriteLine($"    !! 试图访问的地址 = 0x{bad.ToInt64():x}");
        }
        return 0; // EXCEPTION_CONTINUE_SEARCH
    }

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]  delegate IntPtr GetObj();
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]  delegate void   VoidFn();
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate IntPtr Ctor(IntPtr self);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate int    NoArg(IntPtr self);

    static IntPtr NewObj(int size=65536){ var o=Marshal.AllocHGlobal(size); for(int i=0;i<size/4;i++) Marshal.WriteInt32(o,i*4,0); return o; }

    static void Main() {
        SetDllDirectoryW(DIR); Environment.CurrentDirectory = DIR;
        var veh = new VehHandler(Veh); GC.KeepAlive(veh);
        AddVectoredExceptionHandler(1, veh);
        IntPtr reg=IntPtr.Zero, srv=IntPtr.Zero;
        foreach (var d in new[]{"log.dll","evtmgr.dll","regmgr.dll","systemdata.dll","Interface Classes.dll",
                                "objectmanagers.dll","compilers.dll","datamanagers.dll","storeretrieveverify.dll"}) {
            var h = LoadLibraryW(System.IO.Path.Combine(DIR,d));
            if (d=="regmgr.dll") reg=h;
            if (d=="storeretrieveverify.dll") srv=h;
        }
        srvBase = srv;
        Console.WriteLine($"[1] regmgr=0x{reg.ToInt64():x}  storeretrieveverify=0x{srv.ToInt64():x}");

        Console.WriteLine("[2] 读 storeretrieveverify 的全局（DllMain 应已填写）");
        IntPtr gCU = srv + 0x4db76c, gLM = srv + 0x4db768;
        Console.WriteLine($"    CurrentUser 全局 = 0x{Marshal.ReadInt32(gCU):x}");
        Console.WriteLine($"    LocalMachine全局 = 0x{Marshal.ReadInt32(gLM):x}");

        Console.WriteLine("[3] 直接调用 regmgr 的两个 __cdecl 工厂函数");
        var fCU = Marshal.GetDelegateForFunctionPointer<GetObj>(GetProcAddress(reg,"?GetCurrentUserRegistryObject@@YAPAXXZ"));
        var fLM = Marshal.GetDelegateForFunctionPointer<GetObj>(GetProcAddress(reg,"?GetLocalMachineRegistryObject@@YAPAXXZ"));
        IntPtr cu = fCU(), lm = fLM();
        Console.WriteLine($"    GetCurrentUserRegistryObject()  = 0x{cu.ToInt64():x}");
        Console.WriteLine($"    GetLocalMachineRegistryObject() = 0x{lm.ToInt64():x}");

        if (cu == IntPtr.Zero) {
            Console.WriteLine("[3b] 返回 NULL —— 先试 ResMgr_LoadStringLib() 再取一次");
            var p = GetProcAddress(reg, "?ResMgr_LoadStringLib@@YAXXZ");
            if (p != IntPtr.Zero) { Marshal.GetDelegateForFunctionPointer<VoidFn>(p)(); 
                cu = fCU(); lm = fLM();
                Console.WriteLine($"    重试后 CU=0x{cu.ToInt64():x}  LM=0x{lm.ToInt64():x}"); }
            else Console.WriteLine("    ResMgr_LoadStringLib 未找到");
        }

        if (cu != IntPtr.Zero) {
            Console.WriteLine("[4] 把对象写回 storeretrieveverify 的全局（补上 DllMain 没做成的事）");
            uint old; VirtualProtect(gCU, 8, 0x04, out old);
            Marshal.WriteIntPtr(gCU, cu); Marshal.WriteIntPtr(gLM, lm);
            Console.WriteLine($"    写入后 CU 全局 = 0x{Marshal.ReadInt32(gCU):x}");

            Console.WriteLine("[5] 再次尝试 MWStore::PRJ_InitSystemTables()");
            IntPtr store = NewObj();
            Marshal.GetDelegateForFunctionPointer<Ctor>(GetProcAddress(srv,"??0MWStore@@QAE@XZ"))(store);
            int r = Marshal.GetDelegateForFunctionPointer<NoArg>(GetProcAddress(srv,"?PRJ_InitSystemTables@MWStore@@QAEJXZ"))(store);
            Console.WriteLine($"    >>> PRJ_InitSystemTables 返回 {r} (0x{r:x8}) —— 未崩溃！");
        } else {
            Console.WriteLine("[4] 两个工厂函数都返回 NULL，需要更上游的初始化");
        }
    }
}
