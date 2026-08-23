#include <windows.h>
extern "C" __declspec(dllexport) int __stdcall Ping() { return 42; }
BOOL WINAPI DllMain(HINSTANCE, DWORD, LPVOID){ return TRUE; }
