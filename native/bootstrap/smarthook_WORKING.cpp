// smarthook.dll —— 注入后子类化 MWSmartV3 主窗口，用自定义消息在【主线程】执行引擎调用。
// 数据管理器(g_PouDataMgr 等)的上下文属于主线程，旁路线程读到的是空的 —— 必须回到主线程。
#include <windows.h>
#include <cstdio>
#include <cstdint>
#include <cstdarg>

// 命令/结果文件放在【本 DLL 自己旁边】，路径运行时从模块句柄取。
// 以前这里写死绝对路径，别人 clone 到别的盘就跑不了，而且改 Python 没用 ——
// 路径是编译进 DLL 的，必须装 VS 工具链重编。自寻路径后整个仓库可以随便搬。
static char g_result[MAX_PATH] = {0};
static char g_cmdfile[MAX_PATH] = {0};

static void InitPaths(HINSTANCE hSelf) {
    char dir[MAX_PATH] = {0};
    GetModuleFileNameA(hSelf, dir, MAX_PATH);
    char* slash = strrchr(dir, '\\');
    if (slash) *(slash + 1) = 0; else dir[0] = 0;
    sprintf_s(g_result, "%sinject_result.txt", dir);
    sprintf_s(g_cmdfile, "%sinject_cmd.txt", dir);
}

#define RESULT  g_result
#define CMDFILE g_cmdfile
#define WM_SMART_RUN (WM_APP + 0x1234)

static void Log(const char* fmt, ...) {
    char buf[1200]; va_list ap; va_start(ap, fmt); vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap);
    HANDLE h = CreateFileA(RESULT, FILE_APPEND_DATA, FILE_SHARE_READ|FILE_SHARE_WRITE, nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h != INVALID_HANDLE_VALUE) { DWORD w; WriteFile(h, buf, (DWORD)strlen(buf), &w, nullptr); WriteFile(h, "\r\n", 2, &w, nullptr); CloseHandle(h); }
}

// 日志编码统一成 UTF-8。
// 坑：源码用 /utf-8 编译，所以字面量是 UTF-8；但命令文件里的块名/路径是 GBK 字节，
// 直接 %s 打进去 → 同一个日志文件里两种编码混着，Python 侧按 UTF-8 解就成乱码，
// 块名匹配不上，明明验过的块被判成"没验过"。所有来自命令文件的串都先过这个转换。
struct U8 {
    char b[1024];
    explicit U8(const char* gbk) {
        b[0] = 0;
        if (!gbk) return;
        wchar_t w[512];
        int n = MultiByteToWideChar(936, 0, gbk, -1, w, 512);
        if (n > 0) WideCharToMultiByte(CP_UTF8, 0, w, -1, b, sizeof(b), nullptr, nullptr);
        else { strncpy_s(b, gbk, sizeof(b) - 1); }
    }
    const char* c() const { return b; }
};

// ATL CString 的字符串管理器指针。
// 坑：以前这里填 NULL —— 只读字符串的 API（导出/导入路径）没事，
// 但凡是会【拷贝存储】CString 的 API（如 GLBVAR_InsertVariable），
// 拷贝构造要走 pStringMgr->Clone()，NULL 就当场崩进程。
// 解法：让引擎自己造一个 CString，从它的 16 字节头里把真 manager 抠出来（见 ProbeStrMgr）。
static void* g_strMgr = nullptr;

struct CStr {
    uint8_t* block; char** slot;
    // refs：引用计数。默认 -1（"锁定"字符串，用于 const& 参数，被调方不会释放）。
    // 但凡参数是【按值】传的 CString（如 SetDataTypeByAddress），被调方会析构那个临时对象，
    // Release() 把 nRefs 递减后 <=0 就会调 pStringMgr->Free() 释放我 malloc 的块 → 堆崩。
    // 这种场合传一个很大的 refs，让它永远减不到 0。
    CStr(const char* s, int refs=-1){ int n=(int)strlen(s); block=(uint8_t*)malloc(16+n+1);
        *(void**)block=g_strMgr; *(int*)(block+4)=n; *(int*)(block+8)=n; *(int*)(block+12)=refs;
        memcpy(block+16,s,n); block[16+n]=0; slot=(char**)malloc(4); *slot=(char*)(block+16); }
    void* obj(){ return slot; }
    ~CStr(){ free(block); free(slot); }
};

// 从 CString 出参里安全取字符串。
// 坑：引擎给出参赋值后会换掉数据指针，直接当 C 串 %s 打可能读越界（日志会被截断在半截）。
// ATL 头 16 字节里 +4 处是 nDataLength，按它做有界拷贝才稳。
static void SafeStr(void* cstrObj, char* out, int outsz){
    out[0]=0;
    if(!cstrObj) return;
    const char* d=*(const char**)cstrObj;
    if(!d) return;
    int n=*(const int*)((const uint8_t*)d-12);
    if(n<0) n=0;
    if(n>outsz-1) n=outsz-1;
    memcpy(out,d,n); out[n]=0;
}

static HMODULE g_srv;
static void* Sym(const char* m){ return (void*)GetProcAddress(g_srv, m); }
static WNDPROC g_oldProc = nullptr;
static HWND g_hwnd = nullptr;
static char g_pouName[128] = {0};

// —— 在主线程执行的实际引擎工作 ——
static void DoWork() {
    g_srv = GetModuleHandleA("storeretrieveverify.dll");
    void* gR = Sym("?g_Retrieve@@3VMWRetrieve@@A");
    void* gS = Sym("?g_Store@@3VMWStore@@A");
    Log("[主线程] g_Retrieve=0x%p g_Store=0x%p", gR, gS);

    char action[32]={0}, outpath[512]={0}; unsigned char mwid[16]={0};
    FILE* cf=nullptr; fopen_s(&cf, CMDFILE, "rb"); if(!cf){ Log("无命令"); return; }
    char line[600];
    if(fgets(line,sizeof(line),cf)){ line[strcspn(line,"\r\n")]=0; strncpy_s(action,line,31); }
    // 第2行：对 find_pou/export_pou 是 POU 名字(GBK)；对其它是 32 hex 的 MW_ID
    if(fgets(line,sizeof(line),cf)){
        line[strcspn(line,"\r\n")]=0;
        strncpy_s(g_pouName, line, 127);
        for(int i=0;i<16;i++){ unsigned v=0; if(sscanf_s(line+i*2,"%2x",&v)==1) mwid[i]=(unsigned char)v; }
    }
    if(fgets(line,sizeof(line),cf)){ line[strcspn(line,"\r\n")]=0; strncpy_s(outpath,line,511); }
    fclose(cf);

    typedef int (__thiscall *GetCur)(void*, unsigned short*);
    typedef int (__thiscall *SetCur)(void*, const unsigned short*);
    typedef int (__thiscall *GetCount)(void*, const int*, unsigned short*);
    typedef int (__thiscall *GetId)(void*, const int*, unsigned short, unsigned char*);
    typedef int (__thiscall *GetName)(void*, const unsigned char*, void*);
    typedef int (__thiscall *ExportPou)(void*, const unsigned char*, void*, bool);
    typedef int (__thiscall *ExportXml)(void*, const unsigned char*, void*);

    GetCur getCur=(GetCur)Sym("?PRJ_GetCurrentProject@MWRetrieve@@QBEJAAG@Z");
    SetCur setCur=(SetCur)Sym("?PRJ_SetCurrentProject@MWStore@@QAEJABG@Z");
    unsigned short cur=0xFFFF; if(getCur){ getCur(gR,&cur); }
    if(setCur && cur!=0xFFFF) setCur(gS,&cur);

    // 抠 ATL 字符串管理器：调一个【按值返回 CString】的引擎函数，
    // __thiscall 按值返回 = this 在 ecx、返回缓冲区指针作为第一个栈参。
    // 返回的 CString 对象是个指向字符数据的指针槽，数据前 16 字节就是头，头首字段即 manager。
    {
        typedef void* (__thiscall *GetDataSz)(void*, void*, const unsigned char*, int);
        GetDataSz gds=(GetDataSz)Sym("?GLBVAR_GetDataSize@MWRetrieve@@QAE?AV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@ABVMW_ID@@H@Z");
        if(gds){
            unsigned char zero[16]={0};
            void* ret=nullptr;
            gds(gR, &ret, zero, 0);
            if(ret){
                uint8_t* data=(uint8_t*)ret;
                void* mgr=*(void**)(data-16);
                if(mgr){ g_strMgr=mgr; }
            }
            Log("[主线程] 字符串管理器=%p", g_strMgr);
        }
    }
    Log("[主线程] 当前工程号=%u action=%s", cur, action);

    if (strcmp(action,"enum_pou")==0) {
        GetCount getCnt=(GetCount)Sym("?POU_GetCount@MWRetrieve@@QBEJABW4MW_IDType@@AAG@Z");
        GetId getId=(GetId)Sym("?POU_GetId@MWRetrieve@@QBEJABW4MW_IDType@@GAAVMW_ID@@@Z");
        GetName getNm=(GetName)Sym("?POU_GetName@MWRetrieve@@QBEJABVMW_ID@@AAV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@@Z");
        int anyNonzero=0;
        for (int idtype=0; idtype<=255; idtype++) {
            unsigned short cnt=0xFFFF; int r=getCnt(gR,&idtype,&cnt);
            if (cnt!=0 && cnt!=0xFFFF) { Log("idtype=%d ret=%d cnt=%u", idtype, r, cnt); anyNonzero++; }
            if (r==0 && cnt>0 && cnt<2000) {
                Log("*** MW_IDType=%d cnt=%u ***", idtype, cnt);
                for (unsigned short i=0;i<cnt && i<60;i++){
                    unsigned char id[16]={0}; if(getId(gR,&idtype,i,id)!=0) continue;
                    char* nm=nullptr; getNm(gR,id,&nm);
                    char hx[40]; for(int k=0;k<16;k++) sprintf_s(hx+k*2,3,"%02x",id[k]);
                    Log("   [%u] id=%s name=%s", i, hx, nm?nm:"(null)");
                }
            }
        }
        Log("扫描 0..255 完成，非零 cnt 的 idtype 共 %d 个", anyNonzero);
    } else if (strcmp(action,"export_xml")==0) {
        ExportXml fn=(ExportXml)Sym("?PRJ_ExportXML@MWRetrieve@@QBEJABVMW_ID@@ABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@@Z");
        CStr p(outpath); int r=fn(gR,mwid,p.obj()); Log("PRJ_ExportXML ret=%d(0x%x)", r, r);
    } else if (strcmp(action,"validate")==0) {
        // 逐网络查梯形图尺寸：无效网络画不出 LAD，会返错或尺寸为 0 → 抓出"无效程序段"
        struct MWID16 { unsigned char b[16]; };
        typedef int (__thiscall *FindByName)(void*, void*, unsigned char*);
        typedef int (__thiscall *GetNetCnt)(void*, const unsigned char*, unsigned short*);
        typedef int (__thiscall *LadDim)(void*, MWID16, unsigned short, unsigned char*, unsigned char*, unsigned char*);
        FindByName find=(FindByName)Sym("?POU_FindPouByName@MWRetrieve@@QAEJABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@AAVMW_ID@@@Z");
        GetNetCnt getCnt=(GetNetCnt)Sym("?POU_GetNetCnt@MWRetrieve@@QBEJABVMW_ID@@AAG@Z");
        LadDim ladDim=(LadDim)Sym("?LAD_GetNetworkDimensions@MWRetrieve@@QAEJVMW_ID@@GPAE11@Z");
        if(!find||!getCnt||!ladDim){ Log("validate: API 缺失 find=%p cnt=%p dim=%p", find,getCnt,ladDim); }
        else {
            MWID16 id; memset(&id,0,16);
            CStr nm(g_pouName);
            find(gR, nm.obj(), id.b);
            unsigned short cnt=0; getCnt(gR, id.b, &cnt);
            Log("validate '%s': 网络总数=%u", g_pouName, cnt);
            int bad=0;
            for(unsigned short i=1;i<=cnt;i++){
                unsigned char w=0,h=0,x=0;
                int r=ladDim(gR, id, i, &w, &h, &x);
                bool invalid = (r!=0) || (w==0 && h==0);
                if(invalid){ Log("  网络 %u: 无效(ret=%d w=%u h=%u)", i, r, w, h); bad++; }
            }
            Log("validate 结果: %d 个无效网络 / 共 %u", bad, cnt);
        }
    } else if (strcmp(action,"find_pou")==0) {
        // 第2行当作 POU 名字（GBK），按名查 MW_ID —— 只读，验证注入调 API 稳定性
        typedef int (__thiscall *FindByName)(void*, void*, unsigned char*);
        FindByName fn=(FindByName)Sym("?POU_FindPouByName@MWRetrieve@@QAEJABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@AAVMW_ID@@@Z");
        if(!fn){ Log("ERR: 无 POU_FindPouByName"); }
        else {
            CStr nm(g_pouName); unsigned char id[16]={0};
            int r=fn(gR, nm.obj(), id);
            char hx[40]; for(int k=0;k<16;k++) sprintf_s(hx+k*2,3,"%02x",id[k]);
            Log("POU_FindPouByName('%s') ret=%d(0x%x) MW_ID=%s", g_pouName, r, r, hx);
        }
    } else if (strcmp(action,"export_pou")==0) {
        // 先按名字查到 MW_ID，再导出到 outpath
        typedef int (__thiscall *FindByName)(void*, void*, unsigned char*);
        FindByName find=(FindByName)Sym("?POU_FindPouByName@MWRetrieve@@QAEJABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@AAVMW_ID@@@Z");
        ExportPou fn=(ExportPou)Sym("?PRJ_ExportPOU@MWRetrieve@@QBEJABVMW_ID@@ABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@_N@Z");
        unsigned char id[16]={0};
        if(find){ CStr nm(g_pouName); int rf=find(gR,nm.obj(),id);
            char hx[40]; for(int k=0;k<16;k++) sprintf_s(hx+k*2,3,"%02x",id[k]);
            Log("查名 '%s' ret=%d MW_ID=%s", g_pouName, rf, hx); }
        CStr p(outpath); int r=fn(gR,id,p.obj(),true); Log("PRJ_ExportPOU ret=%d(0x%x)", r, r);
    } else if (strcmp(action,"script")==0) {
        // 通用脚本：第2行起每行一条子命令，一次注入顺序执行整条工作流。
        //   EXPORT 名字|路径      导出 POU 为 AWL
        //   XML    名字|路径      导出 POU 为 XML
        //   COMPILE               编译
        //   SAVE                  保存
        //   SAVEAS 路径           另存
        typedef int (__thiscall *FindByName)(void*, void*, unsigned char*);
        typedef int (__thiscall *Compile)(void*);
        typedef int (__thiscall *Save)(void*);
        typedef int (__thiscall *SaveAs)(void*, void*);
        FindByName find=(FindByName)Sym("?POU_FindPouByName@MWRetrieve@@QAEJABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@AAVMW_ID@@@Z");
        ExportPou epou=(ExportPou)Sym("?PRJ_ExportPOU@MWRetrieve@@QBEJABVMW_ID@@ABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@_N@Z");
        ExportXml exml=(ExportXml)Sym("?PRJ_ExportXML@MWRetrieve@@QBEJABVMW_ID@@ABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@@Z");
        Compile comp=(Compile)Sym("?PRJ_CompileAll@MWStore@@QAEJXZ");
        Save save=(Save)Sym("?PRJ_Save@MWRetrieve@@QBEJXZ");
        SaveAs saveas=(SaveAs)Sym("?PRJ_SaveAs@MWRetrieve@@QBEJABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@@Z");
        FILE* mf=nullptr; fopen_s(&mf, CMDFILE, "rb");
        if(mf){
            char ln[700]; int lineno=0;
            while(fgets(ln,sizeof(ln),mf)){
                lineno++; if(lineno==1) continue;
                ln[strcspn(ln,"\r\n")]=0; if(!ln[0]) continue;
                char* sp=strchr(ln,' '); char* arg = sp ? sp+1 : (char*)"";
                if(sp) *sp=0;
                if(strcmp(ln,"EXPORT")==0 || strcmp(ln,"XML")==0){
                    char* bar=strchr(arg,'|'); if(!bar){ Log("script: %s 缺|", ln); continue; }
                    *bar=0; char* nm=arg; char* op=bar+1;
                    unsigned char id[16]={0}; if(find){ CStr c(nm); find(gR,c.obj(),id); }
                    CStr p(op);
                    int r = (ln[0]=='X') ? exml(gR,id,p.obj()) : epou(gR,id,p.obj(),true);
                    Log("script EXPORT '%s' -> %s ret=%d", U8(nm).c(), U8(op).c(), r);
                } else if(strcmp(ln,"IMPORT")==0){
                    // PRJ_Import(CString const& path, unsigned short& out) —— 简单入口
                    typedef int (__thiscall *Imp)(void*, void*, unsigned short*);
                    Imp imp=(Imp)Sym("?PRJ_Import@MWStore@@QAEJABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@AAG@Z");
                    if(!imp){ Log("script IMPORT: 无 PRJ_Import"); }
                    else { CStr p(arg); unsigned short w=0; int r=imp(gS,p.obj(),&w); Log("script IMPORT '%s' ret=%d out=%u", U8(arg).c(), r, w); }
                } else if(strcmp(ln,"IMPORTPOU")==0){
                    // PRJ_ImportPouFile(CString path, vector<UDT> const&, bool const&, vector<Log>&)
                    // 空 vector = {null,null,null}（12B 零）；bool 用指针。
                    typedef int (__thiscall *ImpPou)(void*, void*, void*, void*, void*);
                    ImpPou imp=(ImpPou)Sym("?PRJ_ImportPouFile@MWStore@@QAEJABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@ABV?$vector@UUDT_TYPE_INFO@@V?$allocator@UUDT_TYPE_INFO@@@std@@@std@@AB_NAAV?$vector@UImportUdtLogInfo@@V?$allocator@UImportUdtLogInfo@@@std@@@5@@Z");
                    if(!imp){ Log("script IMPORTPOU: 无 PRJ_ImportPouFile"); }
                    else {
                        CStr p(arg);
                        void* emptyVec1[3]={0,0,0};
                        void* emptyVec2[3]={0,0,0};
                        unsigned char bfalse=0;
                        int r=imp(gS, p.obj(), emptyVec1, &bfalse, emptyVec2);
                        Log("script IMPORTPOU '%s' ret=%d", U8(arg).c(), r);
                    }
                } else if(strcmp(ln,"COMPILE")==0){
                    int r=comp(gS); Log("script COMPILE ret=%d", r);
                } else if(strcmp(ln,"SAVE")==0){
                    int r=save(gR); Log("script SAVE ret=%d", r);
                } else if(strcmp(ln,"SAVEAS")==0){
                    CStr p(arg); int r=saveas(gR,p.obj()); Log("script SAVEAS '%s' ret=%d", U8(arg).c(), r);
                } else if(strcmp(ln,"SYMSET")==0){
                    // "地址|名字"：把某个绝对地址(I0.0/Q0.1/...)所在的行改成这个符号名。
                    // 自己扫表定位，调用方不用关心表 id（表 id 各工程可能不同）。
                    // 为什么是"改名"而不是"新建"：I/O 变量表本来就把每个 I/O 点列全了，
                    // 地址是现成的；而在空行上 SetAddressValue 恒报 6019（新行不让设地址）。
                    typedef int (__thiscall *GRows)(void*, const unsigned char*, int*);
                    typedef int (__thiscall *GetRow)(void*, const unsigned char*, int, void*);
                    typedef int (__thiscall *SetNm)(void*, const unsigned char*, int, void*, int, int, int);
                    GRows grw=(GRows)Sym("?GLBVAR_GetNumberRows@MWRetrieve@@QBEJABVMW_ID@@AAH@Z");
                    GetRow gr=(GetRow)Sym("?GLBVAR_GetRow@MWRetrieve@@QBEJABVMW_ID@@HAAUVARIABLE_ELEMENT@@@Z");
                    SetNm sn=(SetNm)Sym("?GLBVAR_SetName@MWStore@@QAEJABVMW_ID@@HABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@HHH@Z");
                    char* b=strchr(arg,'|');
                    if(!grw||!gr||!sn||!b){ Log("script SYMSET ERR api"); }
                    else {
                        *b=0; char* want=arg; char* newname=b+1;
                        const int N=128;
                        static CStr blank5("", 0x40000000);
                        void* filler=*(void**)blank5.obj();
                        void** buf=(void**)malloc(N*4);
                        bool done=false;
                        for(int n=0x0d80;n<=0x0dd0 && !done;n++){
                            for(int variant=0;variant<=1 && !done;variant++){
                                unsigned char id[16]={0};
                                id[4]=(unsigned char)(n&0xff); id[5]=(unsigned char)((n>>8)&0xff);
                                id[8]=(unsigned char)variant; id[15]=0x80;
                                int rows=-1;
                                if(grw(gR,id,&rows)!=0) continue;
                                if(rows<=0||rows>5000) continue;
                                for(int r=0;r<rows;r++){
                                    for(int i=0;i<N;i++) buf[i]=filler;
                                    if(gr(gR,id,r,buf)!=0) continue;
                                    char addr[128]; SafeStr(&buf[3], addr, sizeof(addr));
                                    if(_stricmp(addr,want)!=0) continue;
                                    CStr cn(newname);
                                    int sr=sn(gS,id,r,cn.obj(),0,0,0);
                                    char hx[40]; for(int k=0;k<16;k++) sprintf_s(hx+k*2,3,"%02x",id[k]);
                                    Log("script SYMSET '%s' -> '%s' 表=%s 行=%d SetName=%d",
                                        U8(want).c(), U8(newname).c(), hx, r, sr);
                                    done=true; break;
                                }
                            }
                        }
                        if(!done) Log("script SYMSET '%s' ERR=没找到该地址所在的行", U8(want).c());
                        free(buf);
                    }
                } else if(strcmp(ln,"GVTIMPORT")==0){
                    // "表id|文本文件路径"：走 UI 真正用的符号表导入管线
                    //   ParseImportGVTFileContent(表id, 路径, out 向量, 标志)
                    //   → ImportGVTInfo(表id, 向量, 重名策略, out 计数)
                    // 向量整个当黑盒传，不必知道 GLV_IMPORT_ELEMENT 的布局。
                    typedef int (__thiscall *ParseFn)(void*, unsigned char*, void*, void*, int);
                    typedef int (__thiscall *ImpFn)(void*, const unsigned char*, void*, int, int*);
                    ParseFn pf=(ParseFn)Sym("?GLBVAR_ParseImportGVTFileContent@MWStore@@QAEJAAVMW_ID@@ABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@AAV?$vector@UGLV_IMPORT_ELEMENT@@V?$allocator@UGLV_IMPORT_ELEMENT@@@std@@@std@@H@Z");
                    ImpFn im=(ImpFn)Sym("?GLBVAR_ImportGVTInfo@MWStore@@QAEJABVMW_ID@@AAV?$vector@UGLV_IMPORT_ELEMENT@@V?$allocator@UGLV_IMPORT_ELEMENT@@@std@@@std@@W4GLV_IMPORT_DUPLICATE_NAME_PROCESS@@AAH@Z");
                    char* b=strchr(arg,'|');
                    if(!pf||!im||!b){ Log("script GVTIMPORT ERR pf=%p im=%p", pf, im); }
                    else {
                        *b=0; char* path=b+1;
                        unsigned char id[16]={0};
                        for(int k=0;k<16;k++){ unsigned v=0; sscanf_s(arg+k*2,"%2x",&v); id[k]=(unsigned char)v; }
                        // 函数名是 FileContent —— 要的是【文件内容】不是路径（传路径返 E_INVALIDARG）。
                        int flag=0;
                        char* fb=strchr(path,'*');       // 路径*标志
                        if(fb){ *fb=0; flag=atoi(fb+1); }
                        char* content=nullptr;
                        {
                            FILE* fh=nullptr; fopen_s(&fh, path, "rb");
                            if(fh){ fseek(fh,0,SEEK_END); long n=ftell(fh); fseek(fh,0,SEEK_SET);
                                    content=(char*)malloc(n+1); fread(content,1,n,fh); content[n]=0; fclose(fh); }
                        }
                        if(!content){ Log("script GVTIMPORT ERR=读不到文件 %s", U8(path).c()); free(content); break; }
                        void* vec[3]={0,0,0};          // 空 std::vector（三个指针）
                        CStr cp(content);
                        int r1=pf(gS, id, cp.obj(), vec, flag);
                        int bytes=(int)((char*)vec[1]-(char*)vec[0]);
                        Log("script GVTIMPORT Parse flag=%d ret=%d(0x%X) vec=[%p,%p,%p] 元素字节=%d",
                            flag, r1, r1, vec[0], vec[1], vec[2], bytes);
                        int n=0;
                        int r2=im(gS, id, vec, 0, &n);
                        Log("script GVTIMPORT ImportGVTInfo ret=%d n=%d", r2, n);
                    }
                } else if(strcmp(ln,"GVTROWSET")==0){
                    // "表id|行|地址"：整行读出来 → 把 +012 的地址字段换成我的字符串 → SetRow 写回。
                    // 因为 SetAddressValue 在"干净行"上恒报 6019，而 InsertVariable 虽然能写地址
                    // 却会把标志位弄成 0x0C11（查不到）。这条路两头的好处都要。
                    typedef int (__thiscall *GetRow)(void*, const unsigned char*, int, void*);
                    typedef int (__thiscall *SetRowFn)(void*, const unsigned char*, const int*, const void*);
                    GetRow gr=(GetRow)Sym("?GLBVAR_GetRow@MWRetrieve@@QBEJABVMW_ID@@HAAUVARIABLE_ELEMENT@@@Z");
                    SetRowFn sr=(SetRowFn)Sym("?GLBVAR_SetRow@MWStore@@QAEJABVMW_ID@@ABHABUVARIABLE_ELEMENT@@@Z");
                    char* f[3]={0,0,0}; int nf=0; char* cur=arg;
                    while(nf<3){ f[nf++]=cur; char* b=strchr(cur,'|'); if(!b) break; *b=0; cur=b+1; }
                    if(nf<3||!gr||!sr){ Log("script GVTROWSET ERR nf=%d gr=%p sr=%p", nf, gr, sr); }
                    else {
                        int row=atoi(f[1]);
                        unsigned char id[16]={0};
                        for(int k=0;k<16;k++){ unsigned v=0; sscanf_s(f[0]+k*2,"%2x",&v); id[k]=(unsigned char)v; }
                        const int N=128;
                        static CStr blank4("", 0x40000000);
                        void* filler=*(void**)blank4.obj();
                        void** buf=(void**)malloc(N*4);
                        for(int i=0;i<N;i++) buf[i]=filler;
                        int r1=gr(gR,id,row,buf);
                        // 故意不释放：这块字符串要一直活到引擎把它拷走
                        CStr* addr=new CStr(f[2], 0x40000000);
                        buf[3]=*(void**)addr->obj();          // +012 = 地址
                        int r2=sr(gS,id,&row,buf);
                        Log("script GVTROWSET row=%d GetRow=%d 地址='%s' SetRow=%d",
                            row, r1, U8(f[2]).c(), r2);
                        free(buf);
                    }
                } else if(strcmp(ln,"GVTNAME")==0){
                    typedef int (__thiscall *SetNm)(void*, const unsigned char*, int, void*, int, int, int);
                    SetNm sn=(SetNm)Sym("?GLBVAR_SetName@MWStore@@QAEJABVMW_ID@@HABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@HHH@Z");
                    char* f[3]={0,0,0}; int nf=0; char* cur=arg;
                    while(nf<3){ f[nf++]=cur; char* b=strchr(cur,'|'); if(!b) break; *b=0; cur=b+1; }
                    if(nf<3||!sn){ Log("script GVTNAME ERR nf=%d sn=%p", nf, sn); }
                    else {
                        unsigned char id[16]={0};
                        for(int k=0;k<16;k++){ unsigned v=0; sscanf_s(f[0]+k*2,"%2x",&v); id[k]=(unsigned char)v; }
                        CStr cn(f[2]); int r=sn(gS,id,atoi(f[1]),cn.obj(),0,0,0);
                        Log("script GVTNAME row=%s '%s' ret=%d", f[1], U8(f[2]).c(), r);
                    }
                } else if(strcmp(ln,"GVTADDR")==0){
                    typedef int (__thiscall *SetAddr)(void*, const unsigned char*, int, void*, int, int);
                    SetAddr sa=(SetAddr)Sym("?GLBVAR_SetAddressValue@MWStore@@QAEJABVMW_ID@@HABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@HH@Z");
                    char* f[5]={0,0,0,0,0}; int nf=0; char* cur=arg;
                    while(nf<5){ f[nf++]=cur; char* b=strchr(cur,'|'); if(!b) break; *b=0; cur=b+1; }
                    if(nf<3||!sa){ Log("script GVTADDR ERR nf=%d sa=%p", nf, sa); }
                    else {
                        unsigned char id[16]={0};
                        for(int k=0;k<16;k++){ unsigned v=0; sscanf_s(f[0]+k*2,"%2x",&v); id[k]=(unsigned char)v; }
                        int q1=(nf>=4&&f[3])?atoi(f[3]):0;
                        int q2=(nf>=5&&f[4])?atoi(f[4]):0;
                        CStr ca(f[2]); int r=sa(gS,id,atoi(f[1]),ca.obj(),q1,q2);
                        Log("script GVTADDR row=%s '%s' q=%d,%d ret=%d", f[1], U8(f[2]).c(), q1, q2, r);
                    }
                } else if(strcmp(ln,"GVTINS")==0){
                    typedef int (__thiscall *InsRow)(void*, const unsigned char*, int, int);
                    InsRow ir=(InsRow)Sym("?GLBVAR_InsertRow@MWStore@@QAEJABVMW_ID@@HH@Z");
                    char* f[3]={0,0,0}; int nf=0; char* cur=arg;
                    while(nf<3){ f[nf++]=cur; char* b=strchr(cur,'|'); if(!b) break; *b=0; cur=b+1; }
                    if(nf<2||!ir){ Log("script GVTINS ERR"); }
                    else {
                        unsigned char id[16]={0};
                        for(int k=0;k<16;k++){ unsigned v=0; sscanf_s(f[0]+k*2,"%2x",&v); id[k]=(unsigned char)v; }
                        int cnt=(nf>=3&&f[2])?atoi(f[2]):1;
                        int r=ir(gS,id,atoi(f[1]),cnt);
                        Log("script GVTINS row=%s cnt=%d ret=%d", f[1], cnt, r);
                    }
                } else if(strcmp(ln,"GVTADD2")==0){
                    // "表id|行|名字|地址|注释"：走 InsertRow + SetName + SetAddressValue + SetComment
                    // 的正规路子（InsertVariable 建出来的行标志位是 0x0C11，和正常行 0x0C00 不同）。
                    typedef int (__thiscall *InsRow)(void*, const unsigned char*, int, int);
                    typedef int (__thiscall *SetNm)(void*, const unsigned char*, int, void*, int, int, int);
                    typedef int (__thiscall *SetAddr)(void*, const unsigned char*, int, void*, int, int);
                    typedef int (__thiscall *SetType)(void*, const unsigned char*, int, char*);
                    typedef int (__thiscall *SetCmt)(void*, const unsigned char*, int, void*);
                    InsRow ir=(InsRow)Sym("?GLBVAR_InsertRow@MWStore@@QAEJABVMW_ID@@HH@Z");
                    SetNm sn=(SetNm)Sym("?GLBVAR_SetName@MWStore@@QAEJABVMW_ID@@HABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@HHH@Z");
                    SetAddr sa=(SetAddr)Sym("?GLBVAR_SetAddressValue@MWStore@@QAEJABVMW_ID@@HABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@HH@Z");
                    SetType st=(SetType)Sym("?GLBVAR_SetDataTypeByAddress@MWStore@@QAEJABVMW_ID@@HV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@@Z");
                    SetCmt sc=(SetCmt)Sym("?GLBVAR_SetComment@MWStore@@QAEJABVMW_ID@@HABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@@Z");
                    char* f[5]={0,0,0,0,0}; int nf=0; char* cur=arg;
                    while(nf<5){ f[nf++]=cur; char* b=strchr(cur,'|'); if(!b) break; *b=0; cur=b+1; }
                    if(nf<4){ Log("script GVTADD2 ERR=参数不足 nf=%d", nf); }
                    else {
                        int row=atoi(f[1]);
                        unsigned char id[16]={0};
                        for(int k=0;k<16;k++){ unsigned v=0; sscanf_s(f[0]+k*2,"%2x",&v); id[k]=(unsigned char)v; }
                        int r0=-1,r1=-1,r2=-1,r3=-1,r4=-1;
                        if(ir) r0=ir(gS,id,row,1);
                        if(sn){ CStr cn(f[2]); r1=sn(gS,id,row,cn.obj(),0,0,0); }
                        if(sa){ CStr ca(f[3]); r2=sa(gS,id,row,ca.obj(),0,0); }
                        if(st){ CStr ct(f[3],0x40000000); r3=st(gS,id,row,*(char**)ct.obj()); }
                        if(sc && nf>=5 && f[4]){ CStr cc(f[4]); r4=sc(gS,id,row,cc.obj()); }
                        Log("script GVTADD2 row=%d InsertRow=%d SetName=%d SetAddr=%d SetType=%d SetCmt=%d",
                            row, r0, r1, r2, r3, r4);
                    }
                } else if(strcmp(ln,"GVTFIX")==0){
                    // "表id|行|op"：试着把自建行的标志位 0x0C11 修成正常的 0x0C00。
                    // op: init=清初始值 bind=置绑定标志 edit=走官方 EditVariable
                    typedef int (__thiscall *SetInit)(void*, const unsigned char*, int, char*, int);
                    typedef int (__thiscall *SetBind)(void*, const unsigned char*, int, unsigned char, int);
                    typedef int (__thiscall *GetRow)(void*, const unsigned char*, int, void*);
                    typedef int (__thiscall *EditVar)(void*, const unsigned char*, const void*, void*);
                    SetInit si=(SetInit)Sym("?GLBVAR_SetInitValue@MWStore@@QAEJABVMW_ID@@HV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@H@Z");
                    SetBind sb=(SetBind)Sym("?GLBVAR_SetBindFlag@MWStore@@QAEJABVMW_ID@@H_NH@Z");
                    GetRow gr=(GetRow)Sym("?GLBVAR_GetRow@MWRetrieve@@QBEJABVMW_ID@@HAAUVARIABLE_ELEMENT@@@Z");
                    EditVar ev=(EditVar)Sym("?GLBVAR_EditVariable@MWStore@@QAEJABVMW_ID@@ABUVARIABLE_ELEMENT@@AAUPANEL_CHANGED_EVENT_MSG@@@Z");
                    char* f[3]={0,0,0}; int nf=0; char* cur=arg;
                    while(nf<3){ f[nf++]=cur; char* b=strchr(cur,'|'); if(!b) break; *b=0; cur=b+1; }
                    if(nf<3){ Log("script GVTFIX ERR=参数不足"); }
                    else {
                        int row=atoi(f[1]);
                        unsigned char id[16]={0};
                        for(int k=0;k<16;k++){ unsigned v=0; sscanf_s(f[0]+k*2,"%2x",&v); id[k]=(unsigned char)v; }
                        int r=-999;
                        if(strcmp(f[2],"init")==0 && si){
                            CStr e("", 0x40000000); r=si(gS,id,row,*(char**)e.obj(),0);
                        } else if(strcmp(f[2],"bind")==0 && sb){
                            r=sb(gS,id,row,1,0);
                        } else if(strcmp(f[2],"edit")==0 && gr && ev){
                            const int N=128;
                            static CStr blank3("", 0x40000000);
                            void* filler=*(void**)blank3.obj();
                            void** buf=(void**)malloc(N*4);
                            for(int i=0;i<N;i++) buf[i]=filler;
                            gr(gR,id,row,buf);
                            unsigned char msg[512]={0};
                            r=ev(gS,id,buf,msg);
                            free(buf);
                        }
                        Log("script GVTFIX row=%d op=%s ret=%d", row, f[2], r);
                    }
                } else if(strcmp(ln,"GVTDEF")==0){
                    // "表id|行[|清标志位掩码hex]"：把整行读出来再交回给引擎 DefineVariable 提交。
                    // 实测自建行的 +96 标志位比正常行多 0x11，疑似"未提交/未校验"。
                    typedef int (__thiscall *GetRow)(void*, const unsigned char*, int, void*);
                    typedef int (__thiscall *DefVar)(void*, const unsigned char*, const void*);
                    GetRow gr=(GetRow)Sym("?GLBVAR_GetRow@MWRetrieve@@QBEJABVMW_ID@@HAAUVARIABLE_ELEMENT@@@Z");
                    DefVar dv=(DefVar)Sym("?GLBVAR_DefineVariable@MWStore@@QAEJABVMW_ID@@ABUVARIABLE_ELEMENT@@@Z");
                    char* f[3]={0,0,0}; int nf=0; char* cur=arg;
                    while(nf<3){ f[nf++]=cur; char* b=strchr(cur,'|'); if(!b) break; *b=0; cur=b+1; }
                    if(!gr||!dv||nf<2){ Log("script GVTDEF ERR gr=%p dv=%p nf=%d", gr, dv, nf); }
                    else {
                        int row=atoi(f[1]);
                        unsigned mask = (nf>=3 && f[2]) ? (unsigned)strtoul(f[2],0,16) : 0;
                        unsigned char id[16]={0};
                        for(int k=0;k<16;k++){ unsigned v=0; sscanf_s(f[0]+k*2,"%2x",&v); id[k]=(unsigned char)v; }
                        const int N=128;
                        static CStr blank2("", 0x40000000);
                        void* filler=*(void**)blank2.obj();
                        void** buf=(void**)malloc(N*4);
                        for(int i=0;i<N;i++) buf[i]=filler;
                        int r1=gr(gR,id,row,buf);
                        unsigned before=(unsigned)(size_t)buf[24];
                        if(mask){ buf[24]=(void*)(size_t)(before & ~mask); }
                        int r2=dv(gS,id,buf);
                        Log("script GVTDEF row=%d GetRow=%d 标志 0x%X->0x%X DefineVariable=%d",
                            row, r1, before, (unsigned)(size_t)buf[24], r2);
                        free(buf);
                    }
                } else if(strcmp(ln,"GVTDUMP")==0){
                    // "表id|行"：把 VARIABLE_ELEMENT 结构 dump 出来。
                    // 缓冲区先用【合法的空 CString 指针】铺满 —— 结构里的 CString 字段
                    // 被赋值时要先读旧值的头，铺 0 会当场读空指针崩。
                    // 识别 CString 字段的判据：该 dword 指向的数据往前 16 字节，首字段 == 字符串管理器。
                    typedef int (__thiscall *GetRow)(void*, const unsigned char*, int, void*);
                    GetRow gr=(GetRow)Sym("?GLBVAR_GetRow@MWRetrieve@@QBEJABVMW_ID@@HAAUVARIABLE_ELEMENT@@@Z");
                    char* b=strchr(arg,'|');
                    if(!gr || !b){ Log("script GVTDUMP ERR gr=%p", gr); }
                    else {
                        *b=0; int row=atoi(b+1);
                        unsigned char id[16]={0};
                        for(int k=0;k<16;k++){ unsigned v=0; sscanf_s(arg+k*2,"%2x",&v); id[k]=(unsigned char)v; }
                        const int N=128;                      // 128 个 dword = 512 字节
                        static CStr blank("", 0x40000000);
                        void* filler=*(void**)blank.obj();
                        void** buf=(void**)malloc(N*4);
                        for(int i=0;i<N;i++) buf[i]=filler;
                        int r=gr(gR,id,row,buf);
                        Log("script GVTDUMP row=%d GetRow ret=%d", row, r);
                        for(int i=0;i<N;i++){
                            void* v=buf[i];
                            if(v==filler) continue;
                            char line[300];
                            const uint8_t* d=(const uint8_t*)v;
                            bool isStr=false;
                            if(v && !IsBadReadPtr(d-16,20)){
                                void* mgr=*(void**)(d-16);
                                int len=*(const int*)(d-12);
                                if(mgr==g_strMgr && len>=0 && len<512 && !IsBadReadPtr(d,len+1)){
                                    char tmp[300]; int n=len<250?len:250;
                                    memcpy(tmp,d,n); tmp[n]=0;
                                    sprintf_s(line,"  +%03d 字符串 len=%d [%s]", i*4, len, U8(tmp).c());
                                    isStr=true;
                                }
                            }
                            if(!isStr) sprintf_s(line,"  +%03d = 0x%08X (%d)", i*4, (unsigned)(size_t)v, (int)(size_t)v);
                            Log("script GVTDUMP %s", line);
                        }
                        free(buf);
                    }
                } else if(strcmp(ln,"GVTLOAD")==0){
                    typedef int (__thiscall *LoadTab)(void*, const unsigned char*);
                    LoadTab lt=(LoadTab)Sym("?GLBVAR_LoadVariableTable@MWRetrieve@@QAEJABVMW_ID@@@Z");
                    unsigned char id[16]={0};
                    for(int k=0;k<16;k++){ unsigned v=0; sscanf_s(arg+k*2,"%2x",&v); id[k]=(unsigned char)v; }
                    int r=-1; if(lt) r=lt(gR,id);
                    Log("script GVTLOAD ret=%d (lt=%p)", r, lt);
                } else if(strcmp(ln,"GVTSAVE")==0){
                    typedef int (__thiscall *SaveTab)(void*, const unsigned char*);
                    SaveTab sv=(SaveTab)Sym("?GLBVAR_SaveVariableTable@MWStore@@QAEJABVMW_ID@@@Z");
                    unsigned char id[16]={0};
                    for(int k=0;k<16;k++){ unsigned v=0; sscanf_s(arg+k*2,"%2x",&v); id[k]=(unsigned char)v; }
                    int r=-1; if(sv) r=sv(gS,id);
                    Log("script GVTSAVE ret=%d (sv=%p)", r, sv);
                } else if(strcmp(ln,"GVTSCAN")==0){
                    // 表的 MW_ID 形如 {0, 序号, 变体, 0x80000000}，序号是连着的。
                    // GLBVAR_GetCount 那套枚举恒返 0，只能按这个规律扫。
                    typedef int (__thiscall *GRows)(void*, const unsigned char*, int*);
                    typedef int (__thiscall *GNameTab)(void*, const unsigned char*, void*);
                    typedef int (__thiscall *GNameRow)(void*, const unsigned char*, int, void*);
                    GRows grw=(GRows)Sym("?GLBVAR_GetNumberRows@MWRetrieve@@QBEJABVMW_ID@@AAH@Z");
                    GNameTab gnt=(GNameTab)Sym("?GLBVAR_GetName@MWRetrieve@@QBEJABVMW_ID@@AAV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@@Z");
                    GNameRow gnr=(GNameRow)Sym("?GLBVAR_GetName@MWRetrieve@@QBEJABVMW_ID@@HAAV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@@Z");
                    int lo=0x0d80, hi=0x0dd0;
                    char* b=strchr(arg,'|'); if(b){ *b=0; lo=(int)strtol(arg,0,16); hi=(int)strtol(b+1,0,16); }
                    int hits=0;
                    for(int n=lo;n<=hi;n++){
                        for(int variant=0;variant<=1;variant++){
                            unsigned char id[16]={0};
                            id[4]=(unsigned char)(n&0xff); id[5]=(unsigned char)((n>>8)&0xff);
                            id[8]=(unsigned char)variant;
                            id[15]=0x80;
                            int rows=-1;
                            if(!grw) continue;
                            if(grw(gR,id,&rows)!=0) continue;
                            if(rows<=0 || rows>100000) continue;
                            char tn[256]={0}, r0[256]={0};
                            CStr o1("",0x40000000), o2("",0x40000000);
                            if(gnt) gnt(gR,id,o1.obj());
                            SafeStr(o1.obj(), tn, sizeof(tn));
                            if(gnr) gnr(gR,id,0,o2.obj());
                            SafeStr(o2.obj(), r0, sizeof(r0));
                            char hx[40]; for(int k=0;k<16;k++) sprintf_s(hx+k*2,3,"%02x",id[k]);
                            Log("script GVTSCAN id=%s rows=%d 表名=[%s] 首行=[%s]",
                                hx, rows, U8(tn).c(), U8(r0).c());
                            hits++;
                        }
                    }
                    Log("script GVTSCAN 扫到 %d 张表 (范围 %04x-%04x)", hits, lo, hi);
                } else if(strcmp(ln,"GVTROW")==0){
                    // "表id|行" 逐行 dump：直接问引擎这一行的名字和类型串
                    typedef int (__thiscall *GNameRow)(void*, const unsigned char*, int, void*);
                    typedef int (__thiscall *GTypes)(void*, const unsigned char*, int, void*);
                    GNameRow gnr=(GNameRow)Sym("?GLBVAR_GetName@MWRetrieve@@QBEJABVMW_ID@@HAAV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@@Z");
                    GTypes gt=(GTypes)Sym("?GLBVAR_GetTypesForRow@MWRetrieve@@QBEJABVMW_ID@@HAAV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@@Z");
                    char* b=strchr(arg,'|');
                    if(!b){ Log("script GVTROW ERR=缺|"); }
                    else {
                        *b=0; int row=atoi(b+1);
                        unsigned char id[16]={0};
                        for(int k=0;k<16;k++){ unsigned v=0; sscanf_s(arg+k*2,"%2x",&v); id[k]=(unsigned char)v; }
                        CStr o1("", 0x40000000), o2("", 0x40000000);
                        int r1=-1,r2=-1;
                        if(gnr) r1=gnr(gR,id,row,o1.obj());
                        if(gt)  r2=gt(gR,id,row,o2.obj());
                        char b1[256], b2[256];
                        SafeStr(o1.obj(), b1, sizeof(b1));
                        SafeStr(o2.obj(), b2, sizeof(b2));
                        Log("script GVTROW row=%d name_ret=%d name=[%s] types_ret=%d types=[%s]",
                            row, r1, U8(b1).c(), r2, U8(b2).c());
                    }
                } else if(strcmp(ln,"GVTSET")==0){
                    // "表id|行|地址|注释"：给已存在的行补地址、数据类型、注释。
                    // InsertVariable 只建了名字，地址/类型是空的（GVTGET 回读证实），要单独设。
                    typedef int (__thiscall *SetAddr)(void*, const unsigned char*, int, void*, int, int);
                    typedef int (__thiscall *SetType)(void*, const unsigned char*, int, char*);   // CString 按【值】传
                    typedef int (__thiscall *SetCmt)(void*, const unsigned char*, int, void*);
                    SetAddr sa=(SetAddr)Sym("?GLBVAR_SetAddressValue@MWStore@@QAEJABVMW_ID@@HABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@HH@Z");
                    SetType st=(SetType)Sym("?GLBVAR_SetDataTypeByAddress@MWStore@@QAEJABVMW_ID@@HV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@@Z");
                    SetCmt sc=(SetCmt)Sym("?GLBVAR_SetComment@MWStore@@QAEJABVMW_ID@@HABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@@Z");
                    char* f[6]={0,0,0,0,0,0}; int nf=0; char* cur=arg;
                    while(nf<6){ f[nf++]=cur; char* b=strchr(cur,'|'); if(!b) break; *b=0; cur=b+1; }
                    if(nf<3){ Log("script GVTSET ERR=参数不足 nf=%d", nf); }
                    else {
                        unsigned char id[16]={0};
                        for(int k=0;k<16;k++){ unsigned v=0; sscanf_s(f[0]+k*2,"%2x",&v); id[k]=(unsigned char)v; }
                        int row=atoi(f[1]);
                        int ra=-1, rt=-1, rc=-1;
                        int q1 = (nf>=5 && f[4]) ? atoi(f[4]) : 0;
                        int q2 = (nf>=6 && f[5]) ? atoi(f[5]) : 0;
                        if(sa){ CStr ca(f[2]); ra=sa(gS,id,row,ca.obj(),q1,q2); }
                        Log("script GVTSET row=%d SetAddressValue('%s',%d,%d) ret=%d", row, U8(f[2]).c(), q1, q2, ra);
                        if(st){ CStr ct(f[2], 0x40000000); rt=st(gS,id,row,*(char**)ct.obj()); }
                        Log("script GVTSET row=%d SetDataTypeByAddress ret=%d", row, rt);
                        if(sc && nf>=4 && f[3]){ CStr cc(f[3]); rc=sc(gS,id,row,cc.obj()); }
                        Log("script GVTSET row=%d SetComment ret=%d", row, rc);
                    }
                } else if(strcmp(ln,"GVTGET")==0){
                    // 按名回读符号：两个 CString 出参（现在 CStr 带真 manager，赋值不再崩）
                    typedef int (__thiscall *GetByName)(void*, void*, void*, void*);
                    GetByName gb=(GetByName)Sym("?GLBVAR_GetVariableByName@MWRetrieve@@QAEJABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@AAV23@1@Z");
                    if(!gb){ Log("script GVTGET ERR=无 API"); }
                    else {
                        CStr nm(arg), o1("", 0x40000000), o2("", 0x40000000);
                        int r=gb(gR, nm.obj(), o1.obj(), o2.obj());
                        char b1[256], b2[256];
                        SafeStr(o1.obj(), b1, sizeof(b1));
                        SafeStr(o2.obj(), b2, sizeof(b2));
                        Log("script GVTGET '%s' ret=%d out1=[%s] out2=[%s]",
                            U8(arg).c(), r, U8(b1).c(), U8(b2).c());
                    }
                } else if(strcmp(ln,"GVTCOMPILE")==0){
                    // 符号表(用户变量表)要单独编译，程序里的符号引用才解析得了
                    typedef int (__thiscall *PreComp)(void*, unsigned char);
                    typedef int (__thiscall *Comp)(void*, int*, int*, int*, int*);
                    PreComp pc=(PreComp)Sym("?GLBVAR_PreCompileUserVariableTables@MWStore@@QAEJ_N@Z");
                    Comp cp=(Comp)Sym("?GLBVAR_CompileUserVariableTables@MWStore@@QBEJAAH000@Z");
                    int pr=-1; if(pc) pr=pc(gS,1);
                    Log("script GVTCOMPILE PreCompile ret=%d", pr);
                    int a=0,b=0,c=0,d=0; int cr=-1;
                    if(cp) cr=cp(gS,&a,&b,&c,&d);
                    Log("script GVTCOMPILE Compile ret=%d out=%d,%d,%d,%d", cr, a,b,c,d);
                } else if(strcmp(ln,"GVTADD")==0){
                    // "表id(32hex)|行|名字|地址|注释"：往指定变量表插一个符号。
                    typedef int (__thiscall *InsVar)(void*, const unsigned char*, int, void*, void*, void*, int, int);
                    typedef int (__thiscall *SaveTab)(void*, const unsigned char*);
                    typedef int (__thiscall *GRows)(void*, const unsigned char*, int*);
                    InsVar iv=(InsVar)Sym("?GLBVAR_InsertVariable@MWStore@@QAEJABVMW_ID@@HABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@11HH@Z");
                    SaveTab sv=(SaveTab)Sym("?GLBVAR_SaveVariableTable@MWStore@@QAEJABVMW_ID@@@Z");
                    GRows grw=(GRows)Sym("?GLBVAR_GetNumberRows@MWRetrieve@@QBEJABVMW_ID@@AAH@Z");
                    char* f[5]={0,0,0,0,0}; int nf=0; char* cur=arg;
                    while(nf<5){ f[nf++]=cur; char* b=strchr(cur,'|'); if(!b) break; *b=0; cur=b+1; }
                    Log("script GVTADD step0 nf=%d iv=%p sv=%p grw=%p", nf, iv, sv, grw);
                    if(nf<4 || !iv){ Log("script GVTADD ERR=参数不足(nf=%d) iv=%p", nf, iv); }
                    else {
                        unsigned char id[16]={0};
                        for(int k=0;k<16;k++){ unsigned v=0; sscanf_s(f[0]+k*2,"%2x",&v); id[k]=(unsigned char)v; }
                        int row=atoi(f[1]);
                        const char* cmt = (nf>=5 && f[4]) ? f[4] : "";
                        int before=-1; if(grw) grw(gR,id,&before);
                        int p6 = (nf>=6 && f[5]) ? atoi(f[5]) : 0;
                        int p7 = (nf>=7 && f[6]) ? atoi(f[6]) : 0;
                        Log("script GVTADD step1 解析ok id=%.8s.. row=%d 名=%s 址=%s p6=%d p7=%d mgr=%p",
                            f[0], row, U8(f[2]).c(), U8(f[3]).c(), p6, p7, g_strMgr);
                        CStr cn(f[2]), ca(f[3]), cc(cmt);
                        Log("script GVTADD step2 CString 构造ok");
                        int r=iv(gS, id, row, cn.obj(), ca.obj(), cc.obj(), p6, p7);
                        Log("script GVTADD step3 InsertVariable 返回 ret=%d", r);
                        int after=-1; if(grw) grw(gR,id,&after);
                        Log("script GVTADD step4 取行数ok rows %d->%d", before, after);
                        // 不调 GLBVAR_SaveVariableTable —— 实测它会当场崩进程（插入本身已 ret=0、
                        // 行数也涨了）。靠工程级 PRJ_Save 落盘即可。
                        int sr=-1; (void)sv;
                        Log("script GVTADD '%s'=%s row=%d ret=%d rows %d->%d save=%d",
                            U8(f[2]).c(), U8(f[3]).c(), row, r, before, after, sr);
                    }
                } else if(strcmp(ln,"GVTFIND")==0){
                    // 拿一个【已存在】的符号去反查它所在的变量表 MW_ID。
                    // GLBVAR_GetCount 那套枚举恒返 0（和 POU 一样），只能靠按名反查。
                    typedef int (__thiscall *FindVar)(void*, void*, unsigned char*, int*);
                    typedef int (__thiscall *FindAcc)(void*, void*, unsigned char*);
                    typedef int (__thiscall *GRows)(void*, const unsigned char*, int*);
                    typedef int (__thiscall *GName)(void*, const unsigned char*, void*);
                    FindVar fv=(FindVar)Sym("?GLBVAR_FindVariable@MWStore@@QAEJABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@AAVMW_ID@@AAH@Z");
                    FindAcc fa=(FindAcc)Sym("?GLBVAR_FindVariableByAccessName@MWStore@@QAEJABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@AAVMW_ID@@@Z");
                    GRows grw=(GRows)Sym("?GLBVAR_GetNumberRows@MWRetrieve@@QBEJABVMW_ID@@AAH@Z");
                    GName gn=(GName)Sym("?GLBVAR_GetName@MWRetrieve@@QBEJABVMW_ID@@AAV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@@Z");
                    unsigned char id[16]={0}; int row=-1; int r1=-1;
                    if(fv){ CStr c(arg); r1=fv(gS,c.obj(),id,&row); }
                    char hx[40]; for(int k=0;k<16;k++) sprintf_s(hx+k*2,3,"%02x",id[k]);
                    int rows=-1; if(grw) grw(gR,id,&rows);
                    // 注意：不调 GLBVAR_GetName —— 它的出参是 CString&，
                    // 往一个空 char* 上赋值会让 ATL 写空指针，直接崩进程（已踩）。
                    (void)gn;
                    Log("script GVTFIND '%s' FindVariable ret=%d row=%d table=%s rows=%d",
                        U8(arg).c(), r1, row, hx, rows);
                    unsigned char id2[16]={0}; int r2=-1;
                    if(fa){ CStr c2(arg); r2=fa(gS,c2.obj(),id2); }
                    char hx2[40]; for(int k=0;k<16;k++) sprintf_s(hx2+k*2,3,"%02x",id2[k]);
                    Log("script GVTFIND '%s' ByAccessName ret=%d table=%s", U8(arg).c(), r2, hx2);
                } else if(strcmp(ln,"GVTLIST")==0){
                    // 扫 MW_IDType 0..255，把所有【变量表(=符号表)】的 id 和名字列出来。
                    // POU 那套 POU_GetCount 枚举恒返 0，但 GLBVAR 有自己的一套。
                    typedef int (__thiscall *GCount)(void*, const int*, unsigned short*);
                    typedef int (__thiscall *GId)(void*, const int*, unsigned short, unsigned char*);
                    typedef int (__thiscall *GName)(void*, const unsigned char*, void*);
                    typedef int (__thiscall *GRows)(void*, const unsigned char*, int*);
                    GCount gc=(GCount)Sym("?GLBVAR_GetCount@MWRetrieve@@QBEJABW4MW_IDType@@AAG@Z");
                    GId gi=(GId)Sym("?GLBVAR_GetId@MWRetrieve@@QBEJABW4MW_IDType@@GAAVMW_ID@@@Z");
                    GName gn=(GName)Sym("?GLBVAR_GetName@MWRetrieve@@QBEJABVMW_ID@@AAV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@@Z");
                    GRows grw=(GRows)Sym("?GLBVAR_GetNumberRows@MWRetrieve@@QBEJABVMW_ID@@AAH@Z");
                    Log("script GVTLIST api gc=%p gi=%p gn=%p rows=%p", gc, gi, gn, grw);
                    if(gc&&gi){
                        int found=0;
                        for(int t=0;t<=255;t++){
                            unsigned short cnt=0xFFFF; int r=gc(gR,&t,&cnt);
                            if(r!=0 || cnt==0 || cnt==0xFFFF || cnt>500) continue;
                            Log("script GVTLIST type=%d cnt=%u", t, cnt);
                            for(unsigned short i=0;i<cnt && i<40;i++){
                                unsigned char id[16]={0};
                                if(gi(gR,&t,i,id)!=0) continue;
                                char hx[40]; for(int k=0;k<16;k++) sprintf_s(hx+k*2,3,"%02x",id[k]);
                                int rows=-1; if(grw) grw(gR,id,&rows);
                                (void)gn;
                                Log("script GVTLIST   type=%d[%u] id=%s rows=%d", t, i, hx, rows);
                                found++;
                            }
                        }
                        Log("script GVTLIST 共列出 %d 个表", found);
                    }
                } else if(strcmp(ln,"SYMADD")==0){
                    // "名字|地址|注释"：先向引擎【要一个变量表句柄】，再往里插符号。
                    // 卡点一直是拿不到表的 MW_ID —— 这两个 Create* 就是专门发句柄的。
                    typedef int (__thiscall *MkTab)(void*, unsigned char*);
                    typedef int (__thiscall *InsSym)(void*, const unsigned char*, unsigned short, void*, void*, void*, int);
                    MkTab mkU=(MkTab)Sym("?GLBVAR_CreateUndefinedVariableTable@MWStore@@QAEJAAVMW_ID@@@Z");
                    MkTab mkI=(MkTab)Sym("?GLBVAR_CreateIOVariableTable@MWStore@@QAEJAAVMW_ID@@@Z");
                    InsSym ins=(InsSym)Sym("?SYM_InsertSymbol@MWStore@@QAEJABVMW_ID@@GABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@11H@Z");
                    Log("script SYMADD api mkU=%p mkI=%p ins=%p", mkU, mkI, ins);
                    unsigned char id[16]={0}; int mr=-1; const char* which="none";
                    if(mkU){ mr=mkU(gS,id); which="Undefined"; }
                    bool zero=true; for(int k=0;k<16;k++) if(id[k]){ zero=false; break; }
                    if(zero && mkI){ memset(id,0,16); mr=mkI(gS,id); which="IO"; 
                        zero=true; for(int k=0;k<16;k++) if(id[k]){ zero=false; break; } }
                    char hx[40]; for(int k=0;k<16;k++) sprintf_s(hx+k*2,3,"%02x",id[k]);
                    Log("script SYMADD table(%s) ret=%d id=%s", which, mr, hx);
                    if(!zero && ins){
                        char* b1=strchr(arg,'|');
                        if(b1){ *b1=0; char* nm=arg; char* rest=b1+1;
                            char* b2=strchr(rest,'|'); char* ad=rest; char* cm=(char*)"";
                            if(b2){ *b2=0; cm=b2+1; }
                            CStr cn(nm), ca(ad), cc(cm);
                            int r=ins(gS, id, 0, cn.obj(), ca.obj(), cc.obj(), 0);
                            Log("script SYMADD '%s'=%s ret=%d", U8(nm).c(), U8(ad).c(), r);
                        }
                    }
                } else if(strcmp(ln,"IMPORTGVT")==0){
                    // 导入全局变量表(符号表)二进制。路径是 char* 不是 CString。
                    typedef int (__thiscall *ImpGvt)(void*, const char*, const unsigned char*);
                    ImpGvt ig=(ImpGvt)Sym("?GLBVAR_ImportBinaryVariableTable@MWRetrieve@@QAEJPBDABVMW_ID@@@Z");
                    if(!ig){ Log("script IMPORTGVT ERR=无 API"); }
                    else {
                        // "表id|路径"；只给路径则用全零 id（实测全零导入不接受）
                        unsigned char id[16]={0};
                        char* path=arg;
                        char* b=strchr(arg,'|');
                        if(b && (b-arg)==32){
                            *b=0; path=b+1;
                            for(int k=0;k<16;k++){ unsigned v=0; sscanf_s(arg+k*2,"%2x",&v); id[k]=(unsigned char)v; }
                        }
                        int r=ig(gR, path, id);
                        Log("script IMPORTGVT '%s' ret=%d(0x%X)", U8(path).c(), r, r);
                    }
                } else if(strcmp(ln,"EXPORTGVT")==0){
                    // 导出全局变量表(=符号表)。"名字|路径"；名字写 * 表示用全零 MW_ID 试。
                    typedef int (__thiscall *ExpGvt)(void*, const unsigned char*, void*);
                    ExpGvt eg=(ExpGvt)Sym("?PRJ_ExportGVT@MWRetrieve@@QBEJABVMW_ID@@ABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@@Z");
                    ExpGvt eg1=(ExpGvt)Sym("?PRJ_ExportSingleGVT@MWRetrieve@@QBEJABVMW_ID@@ABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@@Z");
                    char* bar=strchr(arg,'|');
                    if(!bar || !eg){ Log("script EXPORTGVT ERR=缺| 或无 API eg=%p eg1=%p", eg, eg1); }
                    else {
                        *bar=0; char* nm=arg; char* op=bar+1;
                        unsigned char id[16]={0};
                        int fr=0;
                        if(strcmp(nm,"*")!=0 && find){ CStr c(nm); fr=find(gR,c.obj(),id); }
                        char hx[40]; for(int k=0;k<16;k++) sprintf_s(hx+k*2,3,"%02x",id[k]);
                        CStr p1(op); int r=eg(gR,id,p1.obj());
                        Log("script EXPORTGVT '%s' find=%d id=%s -> %s ret=%d", U8(nm).c(), fr, hx, U8(op).c(), r);
                        if(r!=0 && eg1){
                            char op2[600]; sprintf_s(op2,"%s.single", op);
                            CStr p2(op2); int r2=eg1(gR,id,p2.obj());
                            Log("script EXPORTGVT '%s' SingleGVT ret=%d", U8(nm).c(), r2);
                        }
                    }
                } else if(strcmp(ln,"SYMFIND")==0){
                    // 探路：按符号名查它所在的符号表 MW_ID 与行号
                    typedef int (__thiscall *FindSym)(void*, void*, unsigned char*, unsigned short*);
                    typedef int (__thiscall *GetRows)(void*, const unsigned char*, unsigned short*);
                    FindSym fs=(FindSym)Sym("?SYM_FindSymbol@MWStore@@QAEJABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@AAVMW_ID@@AAG@Z");
                    GetRows gr=(GetRows)Sym("?SYM_GetSymbolRows@MWStore@@QAEJABVMW_ID@@AAG@Z");
                    if(!fs){ Log("script SYMFIND '%s' ERR=无 SYM_FindSymbol", U8(arg).c()); }
                    else {
                        CStr nm(arg); unsigned char id[16]={0}; unsigned short row=0xFFFF;
                        int r=fs(gS, nm.obj(), id, &row);
                        char hx[40]; for(int k=0;k<16;k++) sprintf_s(hx+k*2,3,"%02x",id[k]);
                        unsigned short rows=0xFFFF; int rr=-1;
                        if(gr) rr=gr(gS, id, &rows);
                        Log("script SYMFIND '%s' ret=%d table=%s row=%u rows_ret=%d rows=%u",
                            U8(arg).c(), r, hx, row, rr, rows);
                    }
                } else if(strcmp(ln,"VALIDATE")==0){
                    // 引擎自己的"无效程序段"真值：POU_IsValidNet 逐网络问软件本人。
                    // MW_ID 走【引用】传参(和已验证可用的 POU_GetNetCnt 同款)，
                    // 别用 LAD_GetNetworkDimensions —— 那个按值传 MW_ID，恒返 0xA00007D3 且会崩。
                    typedef int (__thiscall *GetNetCnt)(void*, const unsigned char*, unsigned short*);
                    typedef int (__thiscall *IsValidNet)(void*, const unsigned char*, unsigned short, int*, int);
                    GetNetCnt getCnt=(GetNetCnt)Sym("?POU_GetNetCnt@MWRetrieve@@QBEJABVMW_ID@@AAG@Z");
                    IsValidNet isVal=(IsValidNet)Sym("?POU_IsValidNet@MWRetrieve@@QBEJABVMW_ID@@GAAHW4LANGUAGE@@@Z");
                    if(!getCnt||!isVal||!find){
                        Log("script VALIDATE '%s' ERR=API缺失 cnt=%p val=%p find=%p", U8(arg).c(), getCnt, isVal, find);
                    } else {
                        // 语言枚举可选：写成 "VALIDATE 块名|0"；不写默认 0(梯形图)
                        int g_lang=0; char* lb=strchr(arg,'|');
                        if(lb){ *lb=0; g_lang=atoi(lb+1); }
                        unsigned char id[16]={0};
                        CStr nm(arg); int fr=find(gR, nm.obj(), id);
                        bool zero=true; for(int k=0;k<16;k++) if(id[k]) { zero=false; break; }
                        if(zero){
                            Log("script VALIDATE '%s' ERR=块未找到 find_ret=%d", U8(arg).c(), fr);
                        } else {
                            unsigned short cnt=0; int cr=getCnt(gR, id, &cnt);
                            if(cr!=0){ Log("script VALIDATE '%s' ERR=取网络数失败 ret=%d", U8(arg).c(), cr); }
                            else {
                                // 网络索引【从 0 起】：实测 i 对应 AWL 里的 Network i+1。
                                // 传 i=cnt 会返 0xA00007D3(越界)，别把它当成"最后一段无效"。
                                int bad=0;
                                for(unsigned short i=0;i<cnt;i++){
                                    int out=-999;
                                    int r=isVal(gR, id, i, &out, g_lang);
                                    if(r!=0){
                                        Log("script VALIDATE '%s' net=%u ERR ret=%d", U8(arg).c(), (unsigned)(i+1), r);
                                        bad++;
                                    } else if(out==0){
                                        Log("script VALIDATE '%s' net=%u INVALID", U8(arg).c(), (unsigned)(i+1));
                                        bad++;
                                    }
                                }
                                Log("script VALIDATE '%s' nets=%u invalid=%d lang=%d", U8(arg).c(), cnt, bad, g_lang);
                            }
                        }
                    }
                } else if(strcmp(ln,"GETLANG")==0){
                    // 读【工程级】编程语言。S7-200 SMART 的编程语言是整个工程一个设置
                    // (PRJ_ 前缀，不是 POU_) —— 和博途"每个块各选一种语言"完全不同。
                    typedef int (__thiscall *GetLang)(void*, int*);
                    GetLang gl=(GetLang)Sym("?PRJ_GetLang@MWRetrieve@@QBEJAAW4LANGUAGE@@@Z");
                    if(!gl){ Log("script GETLANG ERR=无 PRJ_GetLang ret=-1"); }
                    else { int lang=-999; int r=gl(gR,&lang); Log("script GETLANG ret=%d lang=%d", r, lang); }
                } else if(strcmp(ln,"SETLANG")==0){
                    // 设工程级编程语言。LANGUAGE 是枚举，按值传(4字节)。
                    // ⚠ 【尚未打通，别当成功用】2026-08-25 实测：ret=0 但语言没变 ——
                    //   同一实例内随后 GETLANG 仍返回原值，SAVEAS 后新实例读也是原值，
                    //   所以不是读缓存的问题，是真没生效。又一个"报成功其实没做"。
                    //   线索：语言可能真正存在 MWPrjDataMgr(?SetLang@MWPrjDataMgr@@QAEJW4LANGUAGE@@@Z)，
                    //   PRJ_SetLang 或许只是转发、还需要提交/刷新那一步；
                    //   另有 ?ReverseCompilePous@MWPouDataMgr@@QAEJW4LANGUAGE@@@Z
                    //   看名字是"把所有 POU 反编译到指定语言"，切换语言多半要靠它真正做转换。
                    //   在打通之前不要包装成 MCP 工具 —— 宁可没有，也不给一个报成功不干活的。
                    typedef int (__thiscall *SetLang)(void*, int);
                    SetLang sl=(SetLang)Sym("?PRJ_SetLang@MWStore@@QAEJW4LANGUAGE@@@Z");
                    if(!sl){ Log("script SETLANG ERR=无 PRJ_SetLang ret=-1"); }
                    else { int lang=atoi(arg); int r=sl(gS,lang); Log("script SETLANG lang=%d ret=%d", lang, r); }
                } else Log("script: 未知命令 %s", ln);
            }
            fclose(mf);
        }
    } else if (strcmp(action,"export_multi")==0) {
        // 批量导出：从第2行起每行 "名字|路径"，一次注入导出多个块。
        typedef int (__thiscall *FindByName)(void*, void*, unsigned char*);
        FindByName find=(FindByName)Sym("?POU_FindPouByName@MWRetrieve@@QAEJABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@AAVMW_ID@@@Z");
        ExportPou fn=(ExportPou)Sym("?PRJ_ExportPOU@MWRetrieve@@QBEJABVMW_ID@@ABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@_N@Z");
        FILE* mf=nullptr; fopen_s(&mf, CMDFILE, "rb");
        if(mf){
            char ln[700]; int lineno=0;
            while(fgets(ln,sizeof(ln),mf)){
                lineno++;
                if(lineno==1) continue;  // 跳过 action 行
                ln[strcspn(ln,"\r\n")]=0;
                char* bar=strchr(ln,'|'); if(!bar) continue;
                *bar=0; char* nm=ln; char* op=bar+1;
                if(!*nm||!*op) continue;
                unsigned char id[16]={0};
                if(find){ CStr c(nm); find(gR,c.obj(),id); }
                CStr p(op); int r=fn(gR,id,p.obj(),true);
                Log("批量导出 '%s' -> %s : PRJ_ExportPOU ret=%d", nm, op, r);
            }
            fclose(mf);
        }
    } else if (strcmp(action,"compile")==0) {
        typedef int (__thiscall *Compile)(void*);
        Compile fn=(Compile)Sym("?PRJ_CompileAll@MWStore@@QAEJXZ");
        if(!fn){ Log("ERR: 无 PRJ_CompileAll"); }
        else { int r=fn(gS); Log("PRJ_CompileAll ret=%d(0x%x)", r, r); }
    } else if (strcmp(action,"export_xml_byname")==0) {
        // 按名查 id → ExportXML（结构化 XML，比 AWL 信息更全）
        typedef int (__thiscall *FindByName)(void*, void*, unsigned char*);
        FindByName find=(FindByName)Sym("?POU_FindPouByName@MWRetrieve@@QAEJABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@AAVMW_ID@@@Z");
        ExportXml fn=(ExportXml)Sym("?PRJ_ExportXML@MWRetrieve@@QBEJABVMW_ID@@ABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@@Z");
        unsigned char id[16]={0};
        if(find){ CStr nm(g_pouName); find(gR,nm.obj(),id); }
        CStr p(outpath); int r=fn(gR,id,p.obj()); Log("PRJ_ExportXML ret=%d(0x%x)", r, r);
    } else if (strcmp(action,"save")==0) {
        typedef int (__thiscall *Save)(void*);
        Save fn=(Save)Sym("?PRJ_Save@MWRetrieve@@QBEJXZ");
        if(!fn){ Log("ERR: 无 PRJ_Save"); }
        else { int r=fn(gR); Log("PRJ_Save ret=%d(0x%x)", r, r); }
    } else if (strcmp(action,"saveas")==0) {
        typedef int (__thiscall *SaveAs)(void*, void*);
        SaveAs fn=(SaveAs)Sym("?PRJ_SaveAs@MWRetrieve@@QBEJABV?$CStringT@DV?$StrTraitMFC_DLL@DV?$ChTraitsCRT@D@ATL@@@@@ATL@@@Z");
        if(!fn){ Log("ERR: 无 PRJ_SaveAs"); }
        else { CStr p(outpath); int r=fn(gR,p.obj()); Log("PRJ_SaveAs('%s') ret=%d(0x%x)", outpath, r, r); }
    }
    Log("[主线程] 完成");
    Log("__DONE__");   // ASCII 完成标记，编排器据此判断（编码无关）
}

static LRESULT CALLBACK NewProc(HWND h, UINT msg, WPARAM wp, LPARAM lp) {
    if (msg == WM_SMART_RUN) { DoWork(); return 0; }
    return CallWindowProcW(g_oldProc, h, msg, wp, lp);
}

// 只匹配【本进程】的 SmartApp 主窗口 —— FindWindow 会跨进程命中别的实例（如用户的），
// 对跨进程窗口子类化会失败、SendMessage 也白发，DoWork 就不执行。
static BOOL CALLBACK EnumProc(HWND h, LPARAM) {
    DWORD wpid = 0; GetWindowThreadProcessId(h, &wpid);
    if (wpid != GetCurrentProcessId()) return TRUE;
    wchar_t cls[64] = {0}; GetClassNameW(h, cls, 63);
    if (wcscmp(cls, L"SmartApp") == 0) { g_hwnd = h; return FALSE; }
    return TRUE;
}

static DWORD WINAPI Setup(LPVOID) {
    // 等【本进程】主窗口就绪
    for (int i=0;i<60 && !g_hwnd;i++){ EnumWindows(EnumProc, 0); if(!g_hwnd) Sleep(500); }
    if (!g_hwnd) { Log("ERR: 找不到本进程的 SmartApp 主窗口"); return 1; }
    Log("找到主窗口 hwnd=0x%p，子类化并投递 WM_SMART_RUN", (void*)g_hwnd);
    // 稳定模式：子类化 → 执行一次命令 → 还原 → 返回。一次注入干一个（批量）动作。
    g_oldProc = (WNDPROC)SetWindowLongPtrW(g_hwnd, GWLP_WNDPROC, (LONG_PTR)NewProc);
    SendMessageW(g_hwnd, WM_SMART_RUN, 0, 0);  // 进程内、主线程执行
    SetWindowLongPtrW(g_hwnd, GWLP_WNDPROC, (LONG_PTR)g_oldProc);
    return 0;
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(h);
        InitPaths(h);            // 必须在起工作线程之前 —— 它一上来就要写日志
        CreateThread(nullptr, 0, Setup, nullptr, 0, nullptr);
    }
    return TRUE;
}
