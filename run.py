import uvicorn
import argparse

def main():
    """
    启动 FastAPI 应用服务器
    支持通过命令行参数覆盖默认配置
    """
    parser = argparse.ArgumentParser(description='启动 QwenLM OCR API 服务')
    parser.add_argument('--host', default='127.0.0.1', help='服务器监听地址')
    parser.add_argument('--port', type=int, default=8000, help='服务器监听端口')
    parser.add_argument('--reload', action='store_true', default=False, help='是否启用热重载')
    parser.add_argument('--workers', type=int, default=1, help='工作进程数')
    
    args = parser.parse_args()
    
    uvicorn.run(
        "app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers,
        log_level="info"
    )

if __name__ == "__main__":
    main()