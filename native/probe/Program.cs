using System;
using System.Runtime.InteropServices;

class P {
    [DllImport("kernel32", SetLastError=true, CharSet=CharSet.Unicode)]
    static extern IntPtr LoadLibraryW(string p);
    [DllImport("kernel32", SetLastError=true, CharSet=CharSet.Unicode)]
    static extern bool SetDllDirectoryW(string p);
    [DllImport("kernel32", SetLastError=true)]
    static extern IntPtr GetProcAddress(IntPtr h, string n);

    static void Main() {
        string dir = @"D:\smart200";
        SetDllDirectoryW(dir);
        Environment.CurrentDirectory = dir;
        Console.WriteLine($"进程位数: {(IntPtr.Size==4 ? "32位" : "64位")}");
        string[] dlls = {
            "log.dll","evtmgr.dll","regmgr.dll","systemdata.dll","Interface Classes.dll",
            "objectmanagers.dll","datamanagers.dll","storeretrieveverify.dll",
            "compilers.dll","executive.dll","migration.dll"
        };
        foreach (var d in dlls) {
            IntPtr h = LoadLibraryW(System.IO.Path.Combine(dir, d));
            int err = Marshal.GetLastWin32Error();
            Console.WriteLine($"  {d,-26} {(h!=IntPtr.Zero ? "加载成功 0x"+h.ToString("x") : "失败 err="+err)}");
        }
        // 试着解析 executive.dll 的 C 导出
        IntPtr ex = LoadLibraryW(System.IO.Path.Combine(dir,"executive.dll"));
        if (ex != IntPtr.Zero) {
            foreach (var fn in new[]{"PRJ_CompileVariableTest","PRJ_ExtractInstanceTableToStream","PRJ_SetLibSignatureConflictCallback"}) {
                IntPtr p = GetProcAddress(ex, fn);
                Console.WriteLine($"  GetProcAddress {fn,-42} {(p!=IntPtr.Zero?"OK":"未找到")}");
            }
        }
    }
}
