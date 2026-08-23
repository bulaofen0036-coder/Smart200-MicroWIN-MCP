// trigger.exe <pid> —— 向指定进程的 SmartApp 窗口发 WM_SMART_RUN，触发已常驻的引擎钩子执行命令文件。
using System;
using System.Runtime.InteropServices;

class Trigger {
    const uint WM_SMART_RUN = 0x8000 + 0x1234;  // WM_APP + 0x1234
    [DllImport("user32")] static extern bool EnumWindows(EnumProc cb, IntPtr p);
    [DllImport("user32")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32", CharSet=CharSet.Unicode)] static extern int GetClassNameW(IntPtr h, System.Text.StringBuilder s, int n);
    [DllImport("user32", CharSet=CharSet.Unicode)] static extern IntPtr SendMessageW(IntPtr h, uint msg, IntPtr wp, IntPtr lp);
    delegate bool EnumProc(IntPtr h, IntPtr p);

    static int target; static IntPtr found = IntPtr.Zero;
    static bool Cb(IntPtr h, IntPtr p) {
        GetWindowThreadProcessId(h, out uint pid);
        if (pid != (uint)target) return true;
        var sb = new System.Text.StringBuilder(64); GetClassNameW(h, sb, 63);
        if (sb.ToString() == "SmartApp") { found = h; return false; }
        return true;
    }
    static void Main(string[] a) {
        target = int.Parse(a[0]);
        EnumWindows(Cb, IntPtr.Zero);
        if (found == IntPtr.Zero) { Console.WriteLine("NO_WINDOW"); return; }
        SendMessageW(found, WM_SMART_RUN, IntPtr.Zero, IntPtr.Zero);  // 同步：主线程执行完才返回
        Console.WriteLine("TRIGGERED");
    }
}
