# -*- coding: utf-8 -*-
"""개발 서버 런처:  python run.py   (기본 http://127.0.0.1:8000)"""
import os
import uvicorn

if __name__ == "__main__":
    uvicorn.run("novelcopilot.main:app",
                host=os.environ.get("NOVEL_HOST", "127.0.0.1"),
                port=int(os.environ.get("NOVEL_PORT", "8000")),
                reload=bool(os.environ.get("NOVEL_RELOAD")))
