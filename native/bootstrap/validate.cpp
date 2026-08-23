// validate.dll —— 用调试器抓到的【真实 MW_ID】直接注入调 PRJ_ExportPOU，
// 零未知量地证明"注入调 API"完全打通：不靠 UI 就能导出与 UI 相同的 .awl。
// 抓到的地面真相：this=g_Retrieve, MW_ID=28430f01e90300001000e00001000000 (SBR16), bool=0
#include <windows.h>
#include <cstdio>
#include <cstdint>
#include <cstdarg>

#define WM_RUN (WM_APP + 0x1235)
static const char* RESULT = "E:\\Smart200_Mcp\\native\\bootstrap\\validate_result.txt";

static void Log(const char* fmt, ...) {
    char buf[1024]; va_list ap; va_start(ap, fmt); vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap);
    HANDLE h = CreateFileA(RESULT, FILE_APPEND_DATA, FILE_SHARE_READ|FILE_SHARE_WRITE, nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h != INVALID_HANDLE_VALUE) { DWORD w; WriteFile(h, buf, (DWORD)strlen(buf), &w, nullptr); WriteFile(h, "\r\n", 2, &w, nullptr); CloseHandle(h); }
}

struct CStr {
    uint8_t* block; char** slot;
    CStr(const char* s){ int n=(int)strlen(s); block=(uint8_t*)malloc(16+n+1);
        *(void**)block=nullptr; *(int*)(block+4)=n; *(int*)(block+8)=n; *(int*)(block+12)=-1;
        memcpy(block+16,s,n); block[16+n]=0; slot=(char**)malloc(4); *slot=(char*)(block+16); }
    void* obj(){ return slot; }
};

static WNDPROC g_old; static HWND g_hwnd;

static void DoWork() {
    HMODULE srv = GetModuleHandleA("storeretrieveverify.dll");
    void* gR = (void*)GetProcAddress(srv, "?g_Retrieve@@3VMWRetrieve@@A");
    Log("g_Retrieve = 0x%p", gR);

    // 抓到的真实 MW_ID（SBR16 并行查询读地址）
    unsigned char mwid[16] = {0x28,0x43,0x0f,0x01,0xe9,0x03,0x00,0x00,0x10,0x00,0xe0,0x00,0x01,0x00,0x00,0x00};

    // 方式A：直接用真实 MW_ID 导出
    typedef int (__thiscall *ExportPou)(void*, const unsigned char*, void*, bool);
    ExportPou fn = (ExportPou)GetProcAddress(srv, "?PRJ_ExportPOU@MWRetrieve@@QBEJABVMW_ID@@ABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@_N@Z");
    if (!fn) { Log("ERR: 无 PRJ_ExportPOU"); return; }
    CStr pathA("E:\\Smart200_Mcp\\native\\bootstrap\\inject_export_A.awl");
    int rA = fn(gR, mwid, pathA.obj(), false);
    Log("方式A[真实MW_ID直导] PRJ_ExportPOU(bool=0) ret=%d(0x%x)", rA, rA);

    // 方式B：用 POU_FindPouByName 按名字查 id，再导出（若通，则名字→导出全自动）
    typedef int (__thiscall *FindByName)(void*, void*, unsigned char*);
    FindByName find = (FindByName)GetProcAddress(srv, "?POU_FindPouByName@MWRetrieve@@QAEJABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@AAVMW_ID@@@Z");
    if (find) {
        unsigned char id2[16] = {0};
        CStr nm("并行查询读地址");
        int rf = find(gR, nm.obj(), id2);
        char hx[40]; for(int k=0;k<16;k++) sprintf_s(hx+k*2,3,"%02x",id2[k]);
        Log("方式B[按名查] POU_FindPouByName('并行查询读地址') ret=%d MW_ID=%s", rf, hx);
        CStr pathB("E:\\Smart200_Mcp\\native\\bootstrap\\inject_export_B.awl");
        int rB = fn(gR, id2, pathB.obj(), false);
        Log("方式B PRJ_ExportPOU ret=%d(0x%x)", rB, rB);
    } else Log("方式B: 无 POU_FindPouByName");
    Log("完成");
}

static LRESULT CALLBACK NewProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    if (m == WM_RUN) { DoWork(); return 0; }
    return CallWindowProcW(g_old, h, m, w, l);
}
static DWORD WINAPI Setup(LPVOID) {
    for (int i=0;i<60 && !g_hwnd;i++){ g_hwnd=FindWindowW(L"SmartApp",nullptr); if(!g_hwnd) Sleep(500); }
    if(!g_hwnd){ Log("ERR: 无主窗口"); return 1; }
    g_old=(WNDPROC)SetWindowLongPtrW(g_hwnd, GWLP_WNDPROC, (LONG_PTR)NewProc);
    SendMessageW(g_hwnd, WM_RUN, 0, 0);
    SetWindowLongPtrW(g_hwnd, GWLP_WNDPROC, (LONG_PTR)g_old);
    return 0;
}
BOOL WINAPI DllMain(HINSTANCE h, DWORD r, LPVOID) {
    if (r==DLL_PROCESS_ATTACH){ DisableThreadLibraryCalls(h); CreateThread(nullptr,0,Setup,nullptr,0,nullptr); }
    return TRUE;
}
