@echo off
REM 薄壳：真正的工具链探测与编译在 build.py 里（cmd 处理 %%ProgramFiles(x86)%% 这类带括号变量名太容易出错）
python "%~dp0build.py" %*
