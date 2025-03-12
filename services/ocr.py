import re
import time
import uuid
import base64
import httpx
import logging
import json
from typing import Optional, Dict, Any, Union, Callable, TypeVar, Awaitable
import asyncio
from .auth import check_token_validity, extract_token_from_cookie, signin
from .config_manager import ConfigManager

# 设置日志级别为 DEBUG
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# 确保至少有一个处理器来输出日志
if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

DEFAULT_PROMPT = (
    "请识别图片中的内容，注意以下要求：\n"
    "对于数学公式和普通文本：\n"
    "1.所有数学公式和数学符号都必须使用标准的LaTeX格式\n"
    "2.行内公式使用单个$符号包裹，如：$x^2$\n"
    "3.独立公式块使用两个$$符号包裹，如：$$\\sum_{i=1}^ni^2$$\n"
    "4.普通文本保持原样，不要使用LaTeX格式\n"
    "5.保持原文的段落格式和换行\n"
    #"6.明显的换行使用\\n表示\n"
    "6.确保所有数学符号都被正确包裹在$或$$中\n\n"
    "对于验证码图片：\n"
    "1.只输出验证码字符，不要加任何额外解释\n"
    "2.忽略干扰线和噪点\n"
    "3.注意区分相似字符，如0和O、1和l、2和Z等\n"
    "4.验证码通常为4-6位字母数字组合\n\n"
    "不要输出任何额外的解释或说明"
)

class OCRError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None, raw_response: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.raw_response = raw_response

# 定义泛型类型变量
T = TypeVar('T')

async def handle_api_error(e: httpx.HTTPError, operation_name: str) -> None:
    """统一处理API错误"""
    error_detail = ""
    if hasattr(e, 'response') and e.response is not None:
        try:
            error_detail = f"\n响应状态码: {e.response.status_code}"
            error_detail += f"\n响应头: {dict(e.response.headers)}"
            error_detail += f"\n响应内容: {e.response.text}"
        except Exception as parse_error:
            error_detail = f"\n无法解析响应内容: {str(parse_error)}"
    
    logger.error(f"{operation_name}失败: {str(e)}{error_detail}")
    raise OCRError(
        f"{operation_name}失败: {str(e)}",
        status_code=e.response.status_code if hasattr(e, 'response') else None,
        raw_response=e.response.text if hasattr(e, 'response') else None
    )

async def retry_with_token_refresh(operation: Callable[..., Awaitable[T]], *args, operation_name: str = "操作", **kwargs) -> T:
    """
    带token刷新的重试函数
    """
    max_retries = 3
    delay = 1.0
    last_exception = None
    
    # 检查参数中是否包含cookie
    cookie = None
    for arg in args:
        if isinstance(arg, str) and 'token=' in arg:
            cookie = arg
            break
    if not cookie:
        for value in kwargs.values():
            if isinstance(value, str) and 'token=' in value:
                cookie = value
                break
    
    if not cookie:
        logger.warning(f"未找到cookie参数，将不会进行token刷新")
    
    for attempt in range(max_retries):
        try:
            return await operation(*args, **kwargs)
        except httpx.HTTPError as e:
            last_exception = e
            if hasattr(e, 'response') and e.response is not None and e.response.status_code == 401 and cookie:
                logger.warning(f"遇到401错误，尝试刷新token...")
                try:
                    new_token = await _get_valid_token(cookie)
                    if 'headers' in kwargs:
                        kwargs['headers']['authorization'] = f"Bearer {new_token}"
                    logger.info("token已刷新，重试操作...")
                    continue
                except Exception as refresh_error:
                    logger.error(f"刷新token失败: {str(refresh_error)}")
            
            if attempt < max_retries - 1:
                wait_time = delay * (attempt + 1)
                logger.warning(f"第{attempt + 1}次尝试失败，等待{wait_time}秒后重试...")
                await asyncio.sleep(wait_time)
                continue
            
            await handle_api_error(e, operation_name)
        except Exception as e:
            last_exception = e
            if attempt < max_retries - 1:
                wait_time = delay * (attempt + 1)
                logger.warning(f"第{attempt + 1}次尝试失败: {str(e)}，等待{wait_time}秒后重试...")
                await asyncio.sleep(wait_time)
                continue
            
            logger.error(f"{operation_name}失败: {str(e)}\n详细信息: {repr(e)}")
            raise OCRError(f"{operation_name}失败: {str(e)}")
    
    raise last_exception

async def _get_valid_token(cookie: str) -> str:
    """
    从cookie中获取有效的token，如果token无效或不存在则尝试重新登录
    
    Args:
        cookie: cookie字符串
        
    Returns:
        str: 有效的token
        
    Raises:
        OCRError: 当无法获取有效token时抛出
    """
    config_manager = ConfigManager.get_instance()
    
    try:
        # 从配置中获取账号信息
        account = config_manager.get_account_by_cookie(cookie)
        if not account:
            logger.error("无法找到对应的账号信息")
            logger.debug(f"当前cookie: {cookie}")
            raise OCRError("无法找到对应的账号信息")
        
        # 检查现有token是否有效
        token = account.get('token')
        if token and await check_token_validity(token):
            return token
            
        # 如果token无效或不存在，尝试重新登录
        username = account.get('username')
        password = account.get('password')
        if not username or not password:
            raise OCRError("账号信息不完整，无法重新登录")
            
        new_token, new_cookie, expires_at = await signin(username, password, is_password_hashed=True)
        
        # 更新配置信息
        logger.info(f"更新账号 {username} 的token和cookie信息")
        await config_manager.update_account(username, new_token, new_cookie, expires_at)
        
        return new_token
    except OCRError:
        raise
    except Exception as e:
        logger.error(f"获取token时发生错误: {str(e)}", exc_info=True)
        raise OCRError(f"获取新token失败: {str(e)}")

async def _raw_upload_image(client: httpx.AsyncClient, url: str, headers: Dict[str, str], files: Dict[str, Any]) -> Dict[str, Any]:
    """执行实际的图片上传请求"""
    resp = await client.post(url, headers=headers, files=files)
    resp.raise_for_status()
    file_info = resp.json()
    
    if not file_info.get("id"):
        raise OCRError("文件上传成功，但未返回有效 id", raw_response=resp.text)
    
    return file_info

async def _upload_image_info(image_bytes: bytes, filename: str, cookie: str) -> dict:
    """
    上传图片到 QwenLM，返回完整的文件信息（file_info）。
    """
    # 获取有效的token和配置管理器
    token = await _get_valid_token(cookie)
    config_manager = ConfigManager.get_instance()
    
    upload_url = f"{config_manager.base_api_url}/api/v1/files/"
    headers = {
        "accept": "application/json",
        "accept-language": "zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2",
        "accept-encoding": "gzip, deflate, br, zstd",
        "authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0",
        "cookie": cookie,
        "origin": "https://chat.qwen.ai",
        "referer": "https://chat.qwen.ai/",
        "dnt": "1",
        "sec-gpc": "1",
        "connection": "keep-alive"
    }
    
    # 根据文件扩展名确定content_type
    content_type = "application/octet-stream"
    if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp')):
        ext = filename.lower().split('.')[-1]
        content_type = f"image/{ext if ext != 'jpg' else 'jpeg'}"
    
    files = {
        "file": (
            filename,
            image_bytes,
            content_type
        )
    }
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        return await _raw_upload_image(client, upload_url, headers, files)

async def upload_image_info(image_bytes: bytes, filename: str, cookie: str) -> dict:
    """
    带重试的上传图片函数
    """
    return await retry_with_token_refresh(_upload_image_info, image_bytes, filename, cookie, operation_name="文件上传")

async def _create_chat(cookie: str, file_info: dict, prompt: str) -> dict:
    """
    调用 /api/v1/chats/new 接口创建新对话。新建对话时，
    用户消息中附带提示词和上传的图片文件信息（files 字段），
    返回包含 session_id、chat_id 和 assistant 消息 id 的字典。
    """
    # 获取有效的token和配置管理器
    token = await _get_valid_token(cookie)
    config_manager = ConfigManager.get_instance()
    default_model = config_manager.default_model
    
    new_chat_url = f"{config_manager.base_api_url}/api/v1/chats/new"
    user_msg_id = str(uuid.uuid4())
    assistant_msg_id = str(uuid.uuid4())
    ts = int(time.time())
    
    # 构建文件信息
    file_data = {
        "type": "image",
        "file": file_info,
        "id": file_info["id"],
        "url": f"/api/v1/files/{file_info['id']}",
        "name": file_info.get("filename", "image.png"),
        "status": "uploaded",
        "size": file_info.get("meta", {}).get("size", 0),
        "error": "",
        "file_type": file_info.get("meta", {}).get("content_type", "image/png"),
        "showType": "image",
        "file_class": "vision",
        "image": f"/api/v1/files/{file_info['id']}"
    }
    
    payload = {
        "chat": {
            "id": "",
            "title": "新建对话",
            "models": [default_model],
            "params": {},
            "history": {
                "messages": {
                    user_msg_id: {
                        "id": user_msg_id,
                        "parentId": None,
                        "childrenIds": [assistant_msg_id],
                        "role": "user",
                        "content": prompt,
                        "files": [file_data],
                        "timestamp": ts,
                        "models": [default_model],
                        "chat_type": "t2t",
                        "feature_config": {"thinking_enabled": False}
                    },
                    assistant_msg_id: {
                        "id": assistant_msg_id,
                        "parentId": user_msg_id,
                        "childrenIds": [],
                        "role": "assistant",
                        "content": "",
                        "model": default_model,
                        "modelName": default_model,
                        "modelIdx": 0,
                        "userContext": None,
                        "timestamp": ts,
                        "chat_type": "t2t"
                    }
                },
                "currentId": assistant_msg_id,
                "currentResponseIds": [assistant_msg_id]
            },
            "messages": [
                {
                    "id": user_msg_id,
                    "parentId": None,
                    "childrenIds": [assistant_msg_id],
                    "role": "user",
                    "content": prompt,
                    "files": [file_data],
                    "timestamp": ts,
                    "models": [default_model],
                    "chat_type": "t2t",
                    "feature_config": {"thinking_enabled": False}
                },
                {
                    "id": assistant_msg_id,
                    "parentId": user_msg_id,
                    "childrenIds": [],
                    "role": "assistant",
                    "content": "",
                    "model": default_model,
                    "modelName": default_model,
                    "modelIdx": 0,
                    "userContext": None,
                    "timestamp": ts,
                    "chat_type": "t2t"
                }
            ],
            "tags": [],
            "timestamp": ts * 1000,
            "chat_type": "t2t"
        }
    }
    
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0",
        "Accept": "application/json",
        "Accept-Language": "zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "authorization": f"Bearer {token}",
        "Origin": "https://chat.qwen.ai",
        "Referer": "https://chat.qwen.ai/",
        "cookie": cookie,
        "DNT": "1",
        "Sec-GPC": "1",
        "Connection": "keep-alive"
    }
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(new_chat_url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            logger.debug(f"创建对话响应: {data}")
            
            if "chat" not in data:
                raise OCRError("新建对话失败：响应中未包含 chat 数据", raw_response=resp.text)
                
            chat_info = data["chat"]
            # 若返回中包含 session_id 则使用，否则使用 chat_info 的 id 作为 session_id
            session_id = chat_info.get("session_id", chat_info.get("id"))
            chat_id = chat_info.get("id")
            return {
                "session_id": session_id,
                "chat_id": chat_id,
                "assistant_msg_id": assistant_msg_id
            }
        except httpx.HTTPError as e:
            logger.error(f"创建对话请求失败: {str(e)}")
            raw_response = None
            if hasattr(e, 'response') and e.response is not None:
                try:
                    raw_response = e.response.text
                    logger.error(f"原始响应内容: {raw_response}")
                except:
                    pass
            raise OCRError(
                f"创建对话失败: {str(e)}",
                status_code=e.response.status_code if hasattr(e, 'response') else None,
                raw_response=raw_response
            )
        except json.JSONDecodeError as e:
            logger.error(f"响应解析失败: {str(e)}")
            raise OCRError(f"响应解析失败: {str(e)}", raw_response=e.doc)
        except Exception as e:
            logger.error(f"创建对话过程发生未知错误: {str(e)}", exc_info=True)
            raise OCRError(f"创建对话失败: {str(e)}")

async def create_chat(cookie: str, file_info: dict, prompt: str) -> dict:
    """
    带重试的创建对话函数
    """
    return await retry_with_token_refresh(_create_chat, cookie, file_info, prompt, operation_name="创建对话")

async def create_file_info_from_id(file_id: str) -> dict:
    """
    根据文件ID创建标准的文件信息对象
    """
    return {
        "id": file_id,
        "user_id": "",  # 这些字段在直接识别时可以为空
        "hash": None,
        "filename": "image.png",
        "data": {},
        "meta": {
            "name": "image.png",
            "content_type": "image/png",
            "size": 0
        },
        "created_at": int(time.time()),
        "updated_at": int(time.time())
    }

async def _recognize_image(cookie: str, file_info: Union[str, dict], prompt: str = DEFAULT_PROMPT) -> dict:
    """
    图片识别的核心实现
    """
    logger.debug(f"开始识别图片，file_info: {file_info}, prompt: {prompt}")
    
    # 如果传入的是文件ID字符串，转换为标准的文件信息对象
    if isinstance(file_info, str):
        logger.debug(f"传入的是文件ID字符串: {file_info}，转换为标准格式")
        file_info = await create_file_info_from_id(file_info)
    
    try:
        # 第一步：新建对话
        logger.debug("开始创建对话...")
        chat_data = await create_chat(cookie, file_info, prompt)
        session_id = chat_data["session_id"]
        chat_id = chat_data["chat_id"]
        assistant_msg_id = chat_data["assistant_msg_id"]
        logger.debug(f"对话创建成功，session_id: {session_id}, chat_id: {chat_id}")

        # 第二步：调用 completions 接口
        config_manager = ConfigManager.get_instance()
        recognition_url = f"{config_manager.base_api_url}/api/chat/completions"
        payload = {
            "stream": True,
            "incremental_output": True,
            "chat_type": "t2t",
            "model": config_manager.default_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt,
                            "chat_type": "t2t",
                            "feature_config": {"thinking_enabled": False}
                        },
                        {
                            "type": "image",
                            "image": file_info["id"],
                            "chat_type": "t2t"
                        }
                    ]
                }
            ],
            "session_id": session_id,
            "chat_id": chat_id,
            "id": assistant_msg_id
        }
        
        # 获取有效的token
        token = await _get_valid_token(cookie)
        
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0",
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2",
            "authorization": f"Bearer {token}",
            "Origin": "https://chat.qwen.ai",
            "Referer": "https://chat.qwen.ai/",
            "cookie": cookie,
            "DNT": "1",
            "Sec-GPC": "1",
            "Connection": "keep-alive",
            "Cache-Control": "no-cache"
        }
        
        logger.debug(f"开始发送识别请求，URL: {recognition_url}")
        logger.debug(f"请求头: {headers}")
        logger.debug(f"请求体: {payload}")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                full_response = ""
                is_finished = False
                
                async with client.stream("POST", recognition_url, headers=headers, json=payload) as response:
                    # 记录响应状态和头信息
                    logger.debug(f"响应状态码: {response.status_code}")
                    logger.debug(f"响应头: {dict(response.headers)}")
                    
                    # 如果状态码不是2xx，尝试获取详细的错误信息
                    if response.status_code >= 400:
                        error_content = await response.aread()
                        try:
                            error_json = json.loads(error_content)
                            error_detail = f"\n状态码: {response.status_code}"
                            error_detail += f"\n响应头: {dict(response.headers)}"
                            error_detail += f"\n错误详情: {json.dumps(error_json, ensure_ascii=False, indent=2)}"
                            logger.error(f"请求失败，详细信息：{error_detail}")
                            raise OCRError(
                                f"识别请求失败: {response.status_code} {response.reason_phrase}",
                                status_code=response.status_code,
                                raw_response=error_detail
                            )
                        except json.JSONDecodeError:
                            error_text = error_content.decode('utf-8', errors='replace')
                            error_detail = f"\n状态码: {response.status_code}"
                            error_detail += f"\n响应头: {dict(response.headers)}"
                            error_detail += f"\n响应内容: {error_text}"
                            logger.error(f"请求失败，详细信息：{error_detail}")
                            raise OCRError(
                                f"识别请求失败: {response.status_code} {response.reason_phrase}",
                                status_code=response.status_code,
                                raw_response=error_detail
                            )
                    
                    response.raise_for_status()
                    logger.debug("开始接收流式响应...")
                    has_content = False
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        logger.debug(f"收到原始数据行: {line!r}")
                        if line.startswith("data: "):
                            try:
                                data = json.loads(line[6:])
                                logger.debug(f"解析JSON数据: {data}")
                                if "choices" in data and data["choices"]:
                                    choice = data["choices"][0]
                                    logger.debug(f"处理选择数据: {choice}")
                                    if "finish_reason" in choice and choice["finish_reason"] == "stop":
                                        logger.info("接收到结束标志")
                                        is_finished = True
                                        break
                                    if "delta" in choice and "content" in choice["delta"]:
                                        content = choice["delta"]["content"]
                                        logger.debug(f"接收到内容片段: {content}")
                                        full_response += content
                                        has_content = True
                            except json.JSONDecodeError as e:
                                logger.warning(f"JSON解析失败: {e}, 行内容: {line!r}")
                                continue
                    
                    # 如果收到了内容，就认为是成功的
                    if has_content:
                        is_finished = True
                        logger.info("流式响应完成，已收到内容")
                
                if not is_finished and not has_content:
                    logger.error("流式响应未收到任何有效内容")
                    raise OCRError("识别响应未返回任何内容")
                
                if not full_response:
                    logger.error("识别结果为空")
                    raise OCRError("识别结果为空")
                
                logger.debug(f"完整识别结果: {full_response}")
                
                # 处理结果
                if len(full_response) <= 10 and re.fullmatch(r"[A-Za-z0-9]+", full_response):
                    return {"success": True, "result": full_response.upper(), "type": "captcha"}
                
                result = full_response.replace("\\（", "(").replace("\\）", ")")
                result = re.sub(r'\n{3,}', "\n\n", result)
                result = re.sub(r'([^\n])\n([^\n])', r'\1\n\2', result)
                result = result.strip()
                return {"success": True, "result": result, "type": "text"}
                
            except httpx.HTTPError as e:
                logger.error(f"识别请求失败: {str(e)}")
                error_detail = f"\n异常类型: {type(e).__name__}"
                if hasattr(e, 'response') and e.response is not None:
                    try:
                        error_detail += f"\n状态码: {e.response.status_code}"
                        error_detail += f"\n响应头: {dict(e.response.headers)}"
                        error_content = e.response.text
                        try:
                            error_json = json.loads(error_content)
                            error_detail += f"\n错误详情: {json.dumps(error_json, ensure_ascii=False, indent=2)}"
                        except json.JSONDecodeError:
                            error_detail += f"\n响应内容: {error_content}"
                    except Exception as parse_error:
                        error_detail += f"\n无法解析响应内容: {str(parse_error)}"
                
                logger.error(f"请求失败详细信息：{error_detail}")
                raise OCRError(
                    f"识别请求失败: {str(e)}",
                    status_code=e.response.status_code if hasattr(e, 'response') else None,
                    raw_response=error_detail
                )
            except json.JSONDecodeError as e:
                logger.error(f"响应解析失败: {str(e)}")
                raise OCRError(f"响应解析失败: {str(e)}", raw_response=str(e))
            except Exception as e:
                logger.error(f"识别过程发生未知错误: {str(e)}", exc_info=True)
                raise OCRError(f"识别过程发生错误: {str(e)}")
            
    except OCRError:
        raise
    except Exception as e:
        logger.error(f"识别过程发生未知错误: {str(e)}", exc_info=True)
        raise OCRError(f"识别过程发生错误: {str(e)}")

async def recognize_image(cookie: str, file_info: Union[str, dict], prompt: str = DEFAULT_PROMPT) -> dict:
    """
    带重试的图片识别函数
    """
    return await retry_with_token_refresh(_recognize_image, cookie, file_info, prompt, operation_name="图片识别")

async def _process_image_url(image_url: str, cookie: str, prompt: str = DEFAULT_PROMPT) -> dict:
    """
    根据图片 URL 下载图片后上传，然后调用上面的 recognize_image 流程完成识别。
    
    Args:
        image_url: 图片URL
        cookie: cookie字符串
        prompt: 识别提示
        
    Returns:
        dict: 识别结果
        
    Raises:
        OCRError: 当发生错误时抛出，包含详细错误信息
    """
    logger.info(f"开始处理图片URL: {image_url}")
    
    try:
        async with httpx.AsyncClient() as client:
            logger.debug(f"正在下载图片: {image_url}")
            resp = await client.get(image_url)
            resp.raise_for_status()
            image_bytes = resp.content
            logger.debug(f"图片下载成功，大小: {len(image_bytes)} bytes")

        file_info = await upload_image_info(image_bytes, "image", cookie)
        logger.debug(f"图片上传成功，file_info: {file_info}")
        
        try:
            result = await recognize_image(cookie, file_info, prompt)
            logger.info(f"图片识别成功: {result}")
            return result
        except httpx.HTTPError as e:
            logger.error(f"识别请求失败: {str(e)}")
            raw_response = None
            if hasattr(e, 'response') and e.response is not None:
                try:
                    raw_response = e.response.text
                    logger.error(f"原始响应内容: {raw_response}")
                except:
                    pass
            raise OCRError(f"识别请求失败: {str(e)}", 
                         status_code=e.response.status_code if hasattr(e, 'response') else None,
                         raw_response=raw_response)
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析错误: {str(e)}")
            raise OCRError(f"响应解析失败: {str(e)}", raw_response=e.doc)
        except Exception as e:
            logger.error(f"识别过程发生未知错误: {str(e)}", exc_info=True)
            raise OCRError(f"识别过程发生错误: {str(e)}")
            
    except httpx.HTTPError as e:
        logger.error(f"图片下载失败: {str(e)}")
        raise OCRError(f"图片下载失败: {str(e)}")
    except Exception as e:
        logger.error(f"处理过程发生未知错误: {str(e)}", exc_info=True)
        raise OCRError(f"处理过程发生错误: {str(e)}")

async def process_image_url(image_url: str, cookie: str, prompt: str = DEFAULT_PROMPT) -> dict:
    """
    带重试的URL图片处理函数
    """
    return await retry_with_token_refresh(_process_image_url, image_url, cookie, prompt, operation_name="URL图片处理")

async def _process_base64_image(base64_image: str, cookie: str, prompt: str = DEFAULT_PROMPT) -> dict:
    """
    Base64图片处理的核心实现
    """
    logger.info("开始处理Base64图片")
    
    try:
        if not base64_image.startswith("data:"):
            base64_image = "data:image/png;base64," + base64_image
        try:
            _, b64_data = base64_image.split("base64,", 1)
            image_bytes = base64.b64decode(b64_data)
            logger.debug(f"Base64解码成功，图片大小: {len(image_bytes)} bytes")
        except Exception as e:
            logger.error(f"Base64解码失败: {str(e)}")
            raise OCRError(f"无效的Base64图片数据: {str(e)}")

        try:
            file_info = await upload_image_info(image_bytes, "image.png", cookie)
            logger.debug(f"Base64图片上传成功，file_info: {file_info}")
        except Exception as e:
            logger.error(f"Base64图片上传失败: {str(e)}")
            raise OCRError(f"图片上传失败: {str(e)}")

        try:
            result = await recognize_image(cookie, file_info, prompt)
            logger.info("Base64图片识别成功")
            return result
        except Exception as e:
            logger.error(f"Base64图片识别失败: {str(e)}")
            raise OCRError(f"图片识别失败: {str(e)}")
            
    except OCRError:
        raise
    except Exception as e:
        logger.error(f"处理Base64图片时发生未知错误: {str(e)}", exc_info=True)
        raise OCRError(f"处理Base64图片失败: {str(e)}")

async def process_base64_image(base64_image: str, cookie: str, prompt: str = DEFAULT_PROMPT) -> dict:
    """
    带重试的Base64图片处理函数
    """
    return await retry_with_token_refresh(_process_base64_image, base64_image, cookie, prompt, operation_name="Base64图片处理")
