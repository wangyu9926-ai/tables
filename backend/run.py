# -*- coding: utf-8 -*-
"""启动入口：python run.py（PORT 在 backend/.env 里配，默认 8002）。"""
import uvicorn

from app.config import PORT

if __name__ == '__main__':
    uvicorn.run('app.main:app', host='0.0.0.0', port=PORT, reload=False)
