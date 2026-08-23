@echo off
call "D:\BuildTools\VC\Auxiliary\Build\vcvars32.bat" >nul
cd /d E:\Smart200_Mcp\native\bootstrap
cl /nologo /LD /O2 /EHsc /utf-8 /MT minimal.cpp /link user32.lib
