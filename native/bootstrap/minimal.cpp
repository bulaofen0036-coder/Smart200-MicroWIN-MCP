// 最小注入测试：DllMain 里只用 kernel32 写标记文件，证明注入通路本身可行。
#include <windows.h>

static void Mark(const char* tag) {
    HANDLE h = CreateFileA("E:\\Smart200_Mcp\\native\\bootstrap\\mark.txt",
                           FILE_APPEND_DATA, FILE_SHARE_READ|FILE_SHARE_WRITE,
                           nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h != INVALID_HANDLE_VALUE) {
        DWORD w; char buf[128]; int n = 0;
        while (tag[n]) n++;
        WriteFile(h, tag, n, &w, nullptr);
        WriteFile(h, "\r\n", 2, &w, nullptr);
        CloseHandle(h);
    }
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        Mark("DllMain ATTACH 到达");
        // 试探能否找到引擎全局
        HMODULE srv = GetModuleHandleA("storeretrieveverify.dll");
        char b[96]; wsprintfA(b, "storeretrieveverify = 0x%p", (void*)srv);
        Mark(b);
        if (srv) {
            void* g = (void*)GetProcAddress(srv, "?g_Retrieve@@3VMWRetrieve@@A");
            char b2[96]; wsprintfA(b2, "g_Retrieve = 0x%p", g);
            Mark(b2);
        }
    }
    return TRUE;
}
