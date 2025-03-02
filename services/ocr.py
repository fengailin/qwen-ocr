import re
import time
import uuid
import base64
import httpx
from config import config
import logging
import json
from typing import Optional, Dict, Any, Union

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
    "6.明显的换行使用\\n表示\n"
    "7.确保所有数学符号都被正确包裹在$或$$中\n\n"
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

async def upload_image_info(image_bytes: bytes, filename: str, token: str, cookie: str) -> dict:
    """
    上传图片到 QwenLM，返回完整的文件信息（file_info）。
    """
    upload_url = f"{config.base_api_url}/api/v1/files/"
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
    
    # 使用multipart/form-data格式
    files = {
        "file": (
            filename,
            image_bytes,
            "image/png" if filename.endswith(".png") else "application/octet-stream"
        )
    }
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(upload_url, headers=headers, files=files)
            resp.raise_for_status()
            file_info = resp.json()
            logger.debug(f"文件上传响应: {file_info}")
            
            if not file_info.get("id"):
                raise OCRError("文件上传成功，但未返回有效 id", raw_response=resp.text)
                
            return file_info
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
        except json.JSONDecodeError as e:
            logger.error(f"响应解析失败: {str(e)}")
            raise OCRError(f"响应解析失败: {str(e)}", raw_response=e.doc)
        except Exception as e:
            logger.error(f"文件上传过程发生未知错误: {str(e)}", exc_info=True)
            raise OCRError(f"文件上传失败: {str(e)}")

async def create_chat(token: str, cookie: str, file_info: dict, prompt: str) -> dict:
    """
    调用 /api/v1/chats/new 接口创建新对话。新建对话时，
    用户消息中附带提示词和上传的图片文件信息（files 字段），
    返回包含 session_id、chat_id 和 assistant 消息 id 的字典。
    """
    new_chat_url = f"{config.base_api_url}/api/v1/chats/new"
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
            "models": ["qwen2.5-vl-72b-instruct"],
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
                        "models": ["qwen2.5-vl-72b-instruct"],
                        "chat_type": "t2t",
                        "feature_config": {"thinking_enabled": False}
                    },
                    assistant_msg_id: {
                        "id": assistant_msg_id,
                        "parentId": user_msg_id,
                        "childrenIds": [],
                        "role": "assistant",
                        "content": "",
                        "model": "qwen2.5-vl-72b-instruct",
                        "modelName": "Qwen2.5-VL-72B-Instruct",
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
                    "models": ["qwen2.5-vl-72b-instruct"],
                    "chat_type": "t2t",
                    "feature_config": {"thinking_enabled": False}
                },
                {
                    "id": assistant_msg_id,
                    "parentId": user_msg_id,
                    "childrenIds": [],
                    "role": "assistant",
                    "content": "",
                    "model": "qwen2.5-vl-72b-instruct",
                    "modelName": "Qwen2.5-VL-72B-Instruct",
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

async def recognize_image(token: str, cookie: str, file_info: Union[str, dict], prompt: str = DEFAULT_PROMPT) -> dict:
    """
    优化后的流程：
    1) 调用 new 接口创建对话，新建对话中附带提示词和图片文件信息；
    2) 使用返回的 session_id、chat_id 和 assistant 消息 id 调用 completion 接口，
       提交一条文本 + 图片消息的用户消息，完成识别操作。
    3) 使用流式传输接收响应，等待完整结果。
       
    Args:
        token: 认证token
        cookie: cookie字符串
        file_info: 文件信息，可以是文件ID字符串或完整的文件信息字典
        prompt: 识别提示
    """
    logger.debug(f"开始识别图片，file_info: {file_info}, prompt: {prompt}")
    
    # 如果传入的是文件ID字符串，转换为标准的文件信息对象
    if isinstance(file_info, str):
        logger.debug(f"传入的是文件ID字符串: {file_info}，转换为标准格式")
        file_info = await create_file_info_from_id(file_info)
    
    try:
        # 第一步：新建对话
        logger.debug("开始创建对话...")
        chat_data = await create_chat(token, cookie, file_info, prompt)
        session_id = chat_data["session_id"]
        chat_id = chat_data["chat_id"]
        assistant_msg_id = chat_data["assistant_msg_id"]
        logger.debug(f"对话创建成功，session_id: {session_id}, chat_id: {chat_id}")

        # 第二步：调用 completions 接口
        recognition_url = f"{config.base_api_url}/api/chat/completions"
        payload = {
            "stream": True,
            "incremental_output": True,
            "chat_type": "t2t",
            "model": "qwen2.5-vl-72b-instruct",
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
                    response.raise_for_status()
                    logger.debug("开始接收流式响应...")
                    logger.debug(f"响应头: {response.headers}")
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
                raw_response = None
                if hasattr(e, 'response') and e.response is not None:
                    try:
                        raw_response = e.response.text
                        logger.error(f"原始响应内容: {raw_response}")
                    except:
                        pass
                raise OCRError(
                    f"识别请求失败: {str(e)}",
                    status_code=e.response.status_code if hasattr(e, 'response') else None,
                    raw_response=raw_response
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

async def process_image_url(image_url: str, token: str, cookie: str, prompt: str = DEFAULT_PROMPT) -> dict:
    """
    根据图片 URL 下载图片后上传，然后调用上面的 recognize_image 流程完成识别。
    
    Args:
        image_url: 图片URL
        token: 认证token
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

        file_info = await upload_image_info(image_bytes, "image", token, cookie)
        logger.debug(f"图片上传成功，file_info: {file_info}")
        
        try:
            result = await recognize_image(token, cookie, file_info, prompt)
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

async def process_base64_image(base64_image: str, token: str, cookie: str, prompt: str = DEFAULT_PROMPT) -> dict:
    """
    根据 Base64 图片数据上传后，再调用上面的 recognize_image 流程完成识别。
    
    Args:
        base64_image: Base64编码的图片数据
        token: 认证token
        cookie: cookie字符串
        prompt: 识别提示
        
    Returns:
        dict: 识别结果
        
    Raises:
        OCRError: 当发生错误时抛出，包含详细错误信息
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
            file_info = await upload_image_info(image_bytes, "image.png", token, cookie)
            logger.debug(f"Base64图片上传成功，file_info: {file_info}")
        except Exception as e:
            logger.error(f"Base64图片上传失败: {str(e)}")
            raise OCRError(f"图片上传失败: {str(e)}")

        try:
            result = await recognize_image(token, cookie, file_info, prompt)
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
