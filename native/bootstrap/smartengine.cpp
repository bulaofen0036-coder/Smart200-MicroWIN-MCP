// smartengine.dll —— 注入进 MWSmartV3.exe 后，在其进程内直接调用引擎 API。
// 关键：使用引擎自己的全局单例 g_Retrieve / g_Store（已由宿主初始化、持有对象树），
// 而不是自己 new 对象 —— 后者内部状态为空，一调方法就空指针崩溃。
#include <windows.h>
#include <cstdio>
#include <cstdint>
#include <cstdarg>

static const char* RESULT = "E:\\Smart200_Mcp\\native\\bootstrap\\inject_result.txt";
static const char* CMDFILE = "E:\\Smart200_Mcp\\native\\bootstrap\\inject_cmd.txt";

static void Log(const char* fmt, ...) {
    char buf[1200];
    va_list ap; va_start(ap, fmt); vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap);
    FILE* f = nullptr; fopen_s(&f, RESULT, "a");
    if (f) { fputs(buf, f); fputs("\n", f); fclose(f); }
}

// 手工构造 ATL CString：对象本身是"指向字符数据的指针"这一个槽。
// 字符数据前 16 字节头：[pStringMgr=0][nDataLength][nAllocLength][nRefs=-1(常量)]
struct CStr {
    uint8_t* block; char** slot;
    CStr(const char* s) {
        int n = (int)strlen(s);
        block = (uint8_t*)malloc(16 + n + 1);
        *(void**)(block)   = nullptr;
        *(int*)(block + 4) = n;
        *(int*)(block + 8) = n;
        *(int*)(block + 12)= -1;
        memcpy(block + 16, s, n); block[16 + n] = 0;
        slot = (char**)malloc(sizeof(char*));
        *slot = (char*)(block + 16);
    }
    void* obj() { return slot; }
    ~CStr() { free(block); free(slot); }
};

static HMODULE g_srv;
static void* Sym(const char* m) { return (void*)GetProcAddress(g_srv, m); }

extern "C" __declspec(dllexport) DWORD WINAPI RunEngine(LPVOID) {
    Log("=== RunEngine 进入 ===");
    g_srv = GetModuleHandleA("storeretrieveverify.dll");
    if (!g_srv) { Log("ERR: storeretrieveverify.dll 未在宿主中加载"); return 1; }
    Log("storeretrieveverify.dll 基址 = 0x%p", (void*)g_srv);

    // 全局单例：g_Retrieve @ RVA 0x4db790, g_Store @ RVA 0x4db788
    void* gRetrieve = Sym("?g_Retrieve@@3VMWRetrieve@@A");
    void* gStore    = Sym("?g_Store@@3VMWStore@@A");
    Log("g_Retrieve = 0x%p   g_Store = 0x%p", gRetrieve, gStore);
    if (!gRetrieve) { Log("ERR: 找不到 g_Retrieve 导出"); return 2; }

    // 读命令
    FILE* cf = nullptr; fopen_s(&cf, CMDFILE, "rb");
    if (!cf) { Log("ERR: 无命令文件"); return 3; }
    char action[32] = {0}, outpath[512] = {0}; unsigned char mwid[16] = {0};
    char line[600];
    if (fgets(line, sizeof(line), cf)) { line[strcspn(line, "\r\n")] = 0; strncpy_s(action, line, 31); }
    if (fgets(line, sizeof(line), cf)) { for (int i=0;i<16;i++){ unsigned v=0; sscanf_s(line+i*2,"%2x",&v); mwid[i]=(unsigned char)v; } }
    if (fgets(line, sizeof(line), cf)) { line[strcspn(line, "\r\n")] = 0; strncpy_s(outpath, line, 511); }
    fclose(cf);
    Log("action=%s outpath=%s", action, outpath);

    typedef int (__thiscall *GetCount)(void*, const int*, unsigned short*);
    typedef int (__thiscall *GetId)(void*, const int*, unsigned short, unsigned char*);
    typedef int (__thiscall *GetName)(void*, const unsigned char*, void*);
    typedef int (__thiscall *ExportPou)(void*, const unsigned char*, void*, bool);
    typedef int (__thiscall *ExportXml)(void*, const unsigned char*, void*);

    if (strcmp(action, "enum_pou") == 0) {
        GetCount getCnt = (GetCount)Sym("?POU_GetCount@MWRetrieve@@QBEJABW4MW_IDType@@AAG@Z");
        GetId    getId  = (GetId)Sym("?POU_GetId@MWRetrieve@@QBEJABW4MW_IDType@@GAAVMW_ID@@@Z");
        GetName  getNm  = (GetName)Sym("?POU_GetName@MWRetrieve@@QBEJABVMW_ID@@AAV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@@Z");
        if (!getCnt||!getId||!getNm){ Log("ERR: POU API 缺失"); return 5; }
        // 先看当前工程号
        typedef int (__thiscall *GetCur)(void*, unsigned short*);
        typedef int (__thiscall *SetCur)(void*, const unsigned short*);
        GetCur getCur = (GetCur)Sym("?PRJ_GetCurrentProject@MWRetrieve@@QBEJAAG@Z");
        SetCur setCur = (SetCur)Sym("?PRJ_SetCurrentProject@MWStore@@QAEJABG@Z");
        unsigned short cur=0xFFFF;
        if (getCur) { int r=getCur(gRetrieve,&cur); Log("PRJ_GetCurrentProject ret=%d cur=%u", r, cur); }
        // 确保选中已加载的工程（当前工程号）
        if (setCur && cur != 0xFFFF) { int r=setCur(gStore,&cur); Log("SetCurrentProject(%u) ret=%d", cur, r); }
        // 扫全部 idtype，找出哪个是 POU
        for (int idtype = 0; idtype <= 40; idtype++) {
            unsigned short cnt = 0xFFFF;
            int r = getCnt(gRetrieve, &idtype, &cnt);
            if (r == 0 && cnt > 0 && cnt < 2000)
                Log("*** MW_IDType=%d -> cnt=%u ***", idtype, cnt);
        }
    } else if (strcmp(action, "export_pou") == 0) {
        ExportPou fn = (ExportPou)Sym("?PRJ_ExportPOU@MWRetrieve@@QBEJABVMW_ID@@ABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@_N@Z");
        if (!fn){ Log("ERR: 无 PRJ_ExportPOU"); return 6; }
        CStr p(outpath);
        int r = fn(gRetrieve, mwid, p.obj(), true);
        Log("PRJ_ExportPOU 返回 %d (0x%08x)", r, r);
    } else if (strcmp(action, "export_xml") == 0) {
        ExportXml fn = (ExportXml)Sym("?PRJ_ExportXML@MWRetrieve@@QBEJABVMW_ID@@ABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@@Z");
        if (!fn){ Log("ERR: 无 PRJ_ExportXML"); return 6; }
        CStr p(outpath);
        int r = fn(gRetrieve, mwid, p.obj());
        Log("PRJ_ExportXML 返回 %d (0x%08x)", r, r);
    } else {
        Log("ERR: 未知 action=%s", action);
    }
    Log("=== RunEngine 结束 ===");
    return 0;
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(h);
        CreateThread(nullptr, 0, RunEngine, nullptr, 0, nullptr);
    }
    return TRUE;
}
