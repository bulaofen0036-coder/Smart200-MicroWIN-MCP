// smarthook.dll —— 注入后子类化 MWSmartV3 主窗口，用自定义消息在【主线程】执行引擎调用。
// 数据管理器(g_PouDataMgr 等)的上下文属于主线程，旁路线程读到的是空的 —— 必须回到主线程。
#include <windows.h>
#include <cstdio>
#include <cstdint>
#include <cstdarg>

static const char* RESULT = "E:\\Smart200_Mcp\\native\\bootstrap\\inject_result.txt";
static const char* CMDFILE = "E:\\Smart200_Mcp\\native\\bootstrap\\inject_cmd.txt";
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

struct CStr {
    uint8_t* block; char** slot;
    CStr(const char* s){ int n=(int)strlen(s); block=(uint8_t*)malloc(16+n+1);
        *(void**)block=nullptr; *(int*)(block+4)=n; *(int*)(block+8)=n; *(int*)(block+12)=-1;
        memcpy(block+16,s,n); block[16+n]=0; slot=(char**)malloc(4); *slot=(char*)(block+16); }
    void* obj(){ return slot; }
    ~CStr(){ free(block); free(slot); }
};

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
                        unsigned char id[16]={0};
                        int r=ig(gR, arg, id);
                        Log("script IMPORTGVT '%s' ret=%d", U8(arg).c(), r);
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
        CreateThread(nullptr, 0, Setup, nullptr, 0, nullptr);
    }
    return TRUE;
}
