#!/usr/bin/env python
"""Smart200 MCP 启动入口（stdio）。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from smart200_mcp.server import main

if __name__ == "__main__":
    main()
