import logging
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from routers import recognize, pdf_ocr

logger = logging.getLogger("uvicorn.error")

app = FastAPI(
    title="QwenLM OCR API",
    description="基于 QwenLM OCR 接口的 FastAPI 实现，支持图片 URL、Base64、文件识别以及代理上传。",
    version="1.1.0",
)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"未捕获的异常: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "内部服务器错误"})

# 简单的 HTML 首页
@app.get("/", response_class=HTMLResponse, summary="首页")
async def root():
    html = """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>QwenLM OCR 服务</title>
    </head>
    <body>
      <h1>欢迎使用 QwenLM OCR 服务</h1>
      <p>请访问 <a href="/docs" target="_blank">接口文档</a> 查看详细接口说明。</p>
    </body>
    </html>
    """
    return html

# 将 recognize 路由挂载到 /api 路径下
app.include_router(recognize.router)
# 将 PDF OCR 路由挂载到 /api 路径下
app.include_router(pdf_ocr.router)
