using System;
using System.Diagnostics;
using System.Runtime.InteropServices;

class Injector {
    [DllImport("kernel32", SetLastError=true)] static extern IntPtr OpenProcess(uint a, bool inh, int pid);
    [DllImport("kernel32", SetLastError=true)] static extern IntPtr VirtualAllocEx(IntPtr h, IntPtr addr, uint size, uint type, uint prot);
    [DllImport("kernel32", SetLastError=true)] static extern bool WriteProcessMemory(IntPtr h, IntPtr addr, byte[] buf, uint size, out uint written);
    [DllImport("kernel32", SetLastError=true)] static extern IntPtr CreateRemoteThread(IntPtr h, IntPtr sa, uint stack, IntPtr start, IntPtr param, uint flags, out uint tid);
    [DllImport("kernel32", SetLastError=true, CharSet=CharSet.Unicode)] static extern IntPtr GetModuleHandleW(string n);
    [DllImport("kernel32", SetLastError=true, CharSet=CharSet.Ansi)] static extern IntPtr GetProcAddress(IntPtr h, string n);
    [DllImport("kernel32")] static extern uint WaitForSingleObject(IntPtr h, uint ms);

    static void Main(string[] a) {
        int pid = int.Parse(a[0]);
        string dll = a[1];
        Console.WriteLine($"注入 {dll} -> PID {pid}");
        IntPtr h = OpenProcess(0x1FFFFF, false, pid);
        if (h == IntPtr.Zero) { Console.WriteLine("OpenProcess 失败 " + Marshal.GetLastWin32Error()); return; }
        byte[] path = System.Text.Encoding.Unicode.GetBytes(dll + "\0");
        IntPtr mem = VirtualAllocEx(h, IntPtr.Zero, (uint)path.Length, 0x3000, 0x04);
        WriteProcessMemory(h, mem, path, (uint)path.Length, out _);
        IntPtr loadLib = GetProcAddress(GetModuleHandleW("kernel32.dll"), "LoadLibraryW");
        Console.WriteLine($"本进程 kernel32=0x{GetModuleHandleW("kernel32.dll").ToInt64():x}  LoadLibraryW=0x{loadLib.ToInt64():x}");
        Console.WriteLine($"注入器位数: {(IntPtr.Size==4 ? "x86" : "x64")}");
        IntPtr th = CreateRemoteThread(h, IntPtr.Zero, 0, loadLib, mem, 0, out uint tid);
        if (th == IntPtr.Zero) { Console.WriteLine("CreateRemoteThread 失败 " + Marshal.GetLastWin32Error()); return; }
        Console.WriteLine($"远程线程已创建 tid={tid}，等待 LoadLibrary 完成...");
        WaitForSingleObject(th, 15000);
        Console.WriteLine("注入完成");
    }
}
