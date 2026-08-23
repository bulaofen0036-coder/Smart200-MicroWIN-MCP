@echo off
call "D:\BuildTools\VC\Auxiliary\Build\vcvars32.bat" >nul 2>&1
cl /nologo /LD /O2 /EHsc /std:c++17 %1 /Fe:%2 /link /OUT:%2 2>&1
