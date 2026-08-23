@echo off
call "D:\BuildTools\VC\Auxiliary\Build\vcvars32.bat" >nul
cd /d E:\Smart200_Mcp\native\bootstrap
cl /nologo /LD /O2 /EHsc /std:c++17 /utf-8 /MT smarthook.cpp /Fe:smarthook_WORKING.dll /link user32.lib
