import re
import logging
from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from fastapi.responses import JSONResponse
from models.schemas import RecognizeUrlRequest, RecognizeBase64Request, RecognizeFileRequest
from services import ocr
from services.ocr import OCRError
import httpx
import random
import asyncio
from pydantic import BaseModel
from typing import Optional, Dict, Any, Union
from services.config_manager import ConfigManager

router = APIRouter(prefix="/api", tags=["recognize"])
logger = logging.getLogger(__name__)

def create_error_response(e: Exception, detail: str = "处理失败") -> dict:
    """
    创建统一的错误响应格式
    """
    error_response = {
        "error": str(e),
        "detail": detail
    }
    if isinstance(e, OCRError):
        if e.raw_response:
            error_response["raw_response"] = e.raw_response
        if e.status_code:
            error_response["status_code"] = e.status_code
    return error_response

def get_cookie_config() -> str:
    """
    从配置中随机返回一个 cookie 字符串
    """
    try:
        config_manager = ConfigManager.get_instance()
        accounts = config_manager.accounts
        if not accounts:
            raise HTTPException(status_code=500, detail="未找到任何账号配置")
        return random.choice(accounts)['cookie']
    except Exception as e:
        logger.error(f"获取cookie配置失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取cookie配置失败: {str(e)}")

@router.post("/recognize/url")
async def recognize_url(req: RecognizeUrlRequest):
    cookie = get_cookie_config()
    
    try:
        result = await ocr.process_image_url(req.imageUrl, cookie)
        return JSONResponse(content=result)
    except OCRError as e:
        logger.error(f"OCR处理失败: {str(e)}")
        return JSONResponse(
            status_code=e.status_code if e.status_code else 500,
            content=create_error_response(e, "OCR处理失败")
        )
    except Exception as e:
        logger.error(f"处理请求时发生未知错误: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=create_error_response(e, "服务器内部错误")
        )

@router.post("/recognize/base64")
async def recognize_base64(req: RecognizeBase64Request):
    cookie = get_cookie_config()
    
    try:
        result = await ocr.process_base64_image(req.base64Image, cookie)
        return JSONResponse(content=result)
    except OCRError as e:
        logger.error(f"Base64图片处理失败: {str(e)}")
        return JSONResponse(
            status_code=e.status_code if e.status_code else 500,
            content=create_error_response(e, "Base64图片处理失败")
        )
    except Exception as e:
        logger.error(f"处理Base64请求时发生未知错误: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=create_error_response(e, "服务器内部错误")
        )

@router.post("/recognize/file")
async def recognize_file(req: RecognizeFileRequest):
    cookie = get_cookie_config()
    
    try:
        logger.debug(f"开始处理文件识别请求，imageId: {req.imageId}")
        result = await ocr.recognize_image(cookie, req.imageId)
        logger.debug(f"文件识别结果: {result}")
        return JSONResponse(content=result)
    except OCRError as e:
        logger.error(f"文件识别失败: {str(e)}")
        return JSONResponse(
            status_code=e.status_code if e.status_code else 500,
            content=create_error_response(e, "文件识别失败")
        )
    except Exception as e:
        logger.error(f"处理文件识别请求时发生未知错误: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=create_error_response(e, "服务器内部错误")
        )

@router.post("/recognize/upload")
async def recognize_upload(file: UploadFile = File(...)):
    cookie = get_cookie_config()
    config_manager = ConfigManager.get_instance()
    
    try:
        # 验证文件大小
        file_size = 0
        chunk_size = 8192
        chunks = []
        
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            file_size += len(chunk)
            chunks.append(chunk)
            
            # 如果文件大于10MB，提前终止
            if file_size > 10 * 1024 * 1024:
                raise HTTPException(status_code=413, detail="文件大小超过限制(10MB)")
        
        file_bytes = b''.join(chunks)
        
        # 验证文件类型
        content_type = file.content_type
        if not content_type.startswith('image/'):
            raise HTTPException(status_code=415, detail="仅支持图片文件上传")
            
        # 使用OCR服务的上传功能
        result = await ocr.upload_image_info(file_bytes, file.filename, cookie)
        logger.debug(f"文件上传成功: {file.filename}")
        return JSONResponse(content=result)
            
    except OCRError as e:
        logger.error(f"文件上传失败: {str(e)}")
        return JSONResponse(
            status_code=e.status_code if e.status_code else 500,
            content=create_error_response(e, "文件上传失败")
        )
    except Exception as e:
        logger.error(f"处理文件上传请求时发生未知错误: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=create_error_response(e, "服务器内部错误")
        )
