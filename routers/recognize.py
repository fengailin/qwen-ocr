import re
import logging
from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from fastapi.responses import JSONResponse
from models.schemas import RecognizeUrlRequest, RecognizeBase64Request, RecognizeFileRequest
from services import ocr
from services.ocr import OCRError
import httpx
from config import config

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

def get_cookie_config(cookie_name: str = None) -> str:
    """
    根据可选的 cookie_name 参数，从配置中返回对应的 cookie 字符串，
    如果未提供，则返回第一个配置的 cookie。
    """
    try:
        if cookie_name:
            for c in config.cookies:
                if c.name == cookie_name:
                    return c.cookie
            raise HTTPException(status_code=400, detail=f"未找到名称为 {cookie_name} 的 cookie 配置")
        return config.cookies[0].cookie
    except Exception as e:
        logger.error(f"获取cookie配置失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取cookie配置失败: {str(e)}")

@router.post("/recognize/url", summary="通过图片 URL 识别")
async def recognize_url(
    req: RecognizeUrlRequest, cookie_name: str = Query(None, description="使用的 cookie 配置名称")
):
    cookie = get_cookie_config(cookie_name)
    token_match = re.search(r"token=([^;]+)", cookie)
    if not token_match:
        raise HTTPException(status_code=400, detail="Cookie 格式错误：缺少 token 字段")
    token = token_match.group(1)
    
    try:
        result = await ocr.process_image_url(req.imageUrl, token, cookie)
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

@router.post("/recognize/base64", summary="通过 Base64 识别")
async def recognize_base64(
    req: RecognizeBase64Request, cookie_name: str = Query(None, description="使用的 cookie 配置名称")
):
    cookie = get_cookie_config(cookie_name)
    token_match = re.search(r"token=([^;]+)", cookie)
    if not token_match:
        raise HTTPException(status_code=400, detail="Cookie 格式错误：缺少 token 字段")
    token = token_match.group(1)
    
    try:
        result = await ocr.process_base64_image(req.base64Image, token, cookie)
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

@router.post("/recognize/file", summary="通过 imageId 识别（文件识别）")
async def recognize_file(
    req: RecognizeFileRequest, cookie_name: str = Query(None, description="使用的 cookie 配置名称")
):
    cookie = get_cookie_config(cookie_name)
    token_match = re.search(r"token=([^;]+)", cookie)
    if not token_match:
        raise HTTPException(status_code=400, detail="Cookie 格式错误：缺少 token 字段")
    token = token_match.group(1)
    
    try:
        logger.debug(f"开始处理文件识别请求，imageId: {req.imageId}")
        result = await ocr.recognize_image(token, cookie, req.imageId)
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

@router.post("/upload-proxy", summary="代理上传文件")
async def proxy_upload(
    file: UploadFile = File(...), cookie_name: str = Query(None, description="使用的 cookie 配置名称")
):
    cookie = get_cookie_config(cookie_name)
    token_match = re.search(r"token=([^;]+)", cookie)
    if not token_match:
        raise HTTPException(status_code=400, detail="Cookie 格式错误：缺少 token 字段")
    token = token_match.group(1)
    
    upload_url = f"{config.base_api_url}/api/v1/files/"
    headers = {
        "accept": "application/json",
        "authorization": f"Bearer{token}",
        "cookie": cookie,
    }
    
    try:
        file_bytes = await file.read()
        files = {"file": (file.filename, file_bytes, file.content_type)}
        
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(upload_url, headers=headers, files=files)
                resp.raise_for_status()
                data = resp.json()
                logger.debug(f"文件上传成功: {file.filename}")
                return JSONResponse(content=data)
            except httpx.HTTPError as e:
                logger.error(f"文件上传请求失败: {str(e)}")
                raw_response = None
                if hasattr(e, 'response') and e.response is not None:
                    try:
                        raw_response = e.response.text
                        logger.error(f"原始响应内容: {raw_response}")
                    except:
                        pass
                raise OCRError(
                    f"文件上传失败: {str(e)}",
                    status_code=e.response.status_code if hasattr(e, 'response') else None,
                    raw_response=raw_response
                )
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
