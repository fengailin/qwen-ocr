import re
import json
import time
import httpx
import hashlib
import logging
from typing import Optional, Dict, Any, Tuple
from .config_manager import ConfigManager

# 设置日志
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

def hash_password(password: str) -> str:
    """
    对密码进行SHA256加密
    
    Args:
        password: 原始密码
        
    Returns:
        str: 加密后的密码
    """
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

class AuthError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None, raw_response: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.raw_response = raw_response

async def signin(username: str, password: str, is_password_hashed: bool = False) -> Tuple[str, str, int]:
    """
    执行登录操作，获取token和cookie
    
    Args:
        username: 用户名/邮箱
        password: 密码（原始密码或已经过SHA256加密的密码）
        is_password_hashed: 密码是否已经过SHA256加密
        
    Returns:
        Tuple[str, str, int]: (token, cookie, expires_at)
        
    Raises:
        AuthError: 当登录失败时抛出，包含详细的错误信息
    """
    start_time = time.time()
    logger.info(f"开始登录操作 - 用户名: {username}")
    
    # 如果密码未加密，进行SHA256加密
    if not is_password_hashed:
        password = hash_password(password)
        logger.debug("已对密码进行SHA256加密")
    
    config_manager = ConfigManager.get_instance()
    base_url = config_manager.base_api_url
    
    # 先访问主页获取初始cookie
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "DNT": "1",
        "Sec-GPC": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            logger.debug("开始请求主页获取初始cookie...")
            home_response = await client.get(base_url, headers=headers, follow_redirects=True)
            home_response.raise_for_status()
            initial_cookies = home_response.headers.get("set-cookie", "")
            logger.debug(f"获取到初始cookie: {initial_cookies}")
            
            # 准备登录请求
            signin_url = f"{base_url}/api/v1/auths/signin"
            signin_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0",
                "Accept": "application/json",
                "Accept-Language": "zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Content-Type": "application/json",
                "Origin": base_url,
                "Referer": f"{base_url}/auth?action=signin",
                "Cookie": config_manager.get_cookie_with_common_fields(initial_cookies),
                "bx-v": "2.5.28",
                "DNT": "1",
                "Sec-GPC": "1",
                "Connection": "keep-alive"
            }
            
            logger.debug(f"准备发送登录请求到 {signin_url}")
            response = await client.post(
                signin_url,
                headers=signin_headers,
                json={
                    "email": username,
                    "password": password
                }
            )
            logger.debug(f"收到登录响应 - 状态码: {response.status_code}")
            
            if response.status_code != 200:
                error_detail = {
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                    "body": response.text
                }
                logger.error(f"登录失败 - HTTP {response.status_code}\n详细信息: {json.dumps(error_detail, ensure_ascii=False, indent=2)}")
                await config_manager.disable_account(username)
                raise AuthError(
                    f"登录失败: HTTP {response.status_code}",
                    status_code=response.status_code,
                    raw_response=json.dumps(error_detail, ensure_ascii=False)
                )
            
            logger.debug("解析响应数据...")
            data = response.json()
            token = data.get("token")
            if not token:
                logger.error("响应中未找到token")
                await config_manager.disable_account(username)
                raise AuthError("响应中没有token", raw_response=response.text)
                
            # 合并初始cookie、登录返回的cookie和公共cookie
            login_cookies = response.headers.get("set-cookie", "")
            all_cookies = initial_cookies
            if login_cookies:
                all_cookies = f"{initial_cookies}; {login_cookies}"
            
            # 添加公共cookie字段
            all_cookies = config_manager.get_cookie_with_common_fields(all_cookies)
            
            if not all_cookies:
                logger.error("未获取到任何cookie")
                await config_manager.disable_account(username)
                raise AuthError("未获取到cookie", raw_response=response.text)
                
            logger.debug("获取token过期时间...")
            expires_at = data.get("expires_at")
            if not expires_at:
                try:
                    import jwt
                    token_data = jwt.decode(token, options={"verify_signature": False})
                    expires_at = token_data.get("exp", int(time.time()) + 86400)
                    logger.debug(f"从JWT中解析出过期时间: {expires_at}")
                except Exception as e:
                    logger.warning(f"无法从token解析过期时间: {e}")
                    expires_at = int(time.time()) + 86400
                    logger.debug(f"使用默认过期时间: {expires_at}")
            
            logger.debug("更新配置...")
            await config_manager.update_account(username, token, all_cookies, expires_at, enabled=True)
            
            end_time = time.time()
            logger.info(f"登录成功 - 用户名: {username}, 耗时: {end_time - start_time:.2f}秒")
            return token, all_cookies, expires_at
            
    except httpx.RequestError as e:
        logger.error(f"请求失败: {str(e)}", exc_info=True)
        await config_manager.disable_account(username)
        raise AuthError(f"请求失败: {str(e)}")
    except json.JSONDecodeError as e:
        logger.error(f"解析响应失败: {str(e)}", exc_info=True)
        await config_manager.disable_account(username)
        raise AuthError(f"解析响应失败: {str(e)}", raw_response=e.doc)
    except Exception as e:
        logger.error(f"登录过程发生未知错误: {str(e)}", exc_info=True)
        await config_manager.disable_account(username)
        raise

def extract_token_from_cookie(cookie: str) -> Optional[str]:
    """
    从cookie字符串中提取token
    
    Args:
        cookie: cookie字符串
        
    Returns:
        Optional[str]: token if found, None otherwise
    """
    token_match = re.search(r'token=([^;]+)', cookie)
    if token_match:
        return token_match.group(1)
    return None

async def check_token_validity(token: str) -> bool:
    """
    检查token是否有效
    
    Args:
        token: JWT token
        
    Returns:
        bool: True if valid, False otherwise
    """
    try:
        import jwt
        token_data = jwt.decode(token, options={"verify_signature": False})
        exp = token_data.get("exp", 0)
        return exp > time.time()
    except Exception as e:
        logger.warning(f"Token验证失败: {e}")
        return False 