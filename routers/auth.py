from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from services.auth import signin, AuthError, hash_password
from services.config_manager import ConfigManager
import logging
import time

router = APIRouter(prefix="/api", tags=["auth"])
logger = logging.getLogger(__name__)

class LoginRequest(BaseModel):
    username: str
    password: str
    is_password_hashed: bool = Field(default=False, description="密码是否已经过SHA256加密")

class LoginResponse(BaseModel):
    success: bool
    message: str
    token: Optional[str] = None
    cookie: Optional[str] = None
    expires_at: Optional[int] = None
    password_hash: Optional[str] = None

@router.post("/auth/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    """
    登录接口，用于手动登录并更新配置文件
    
    - username: 用户名/邮箱
    - password: 密码（原始密码或SHA256加密后的密码）
    - is_password_hashed: 密码是否已经过SHA256加密，默认为False
    """
    start_time = time.time()
    logger.info(f"收到登录请求 - 用户名: {req.username}")
    
    try:
        # 调用登录函数
        logger.debug("调用signin函数...")
        token, cookie, expires_at = await signin(
            req.username, 
            req.password,
            is_password_hashed=req.is_password_hashed
        )
        
        end_time = time.time()
        logger.info(f"登录请求处理完成 - 用户名: {req.username}, 总耗时: {end_time - start_time:.2f}秒")
        
        # 如果使用的是原始密码，返回其哈希值供后续使用
        password_hash = None
        if not req.is_password_hashed:
            password_hash = hash_password(req.password)
        
        return LoginResponse(
            success=True,
            message="登录成功",
            token=token,
            cookie=cookie,
            expires_at=expires_at,
            password_hash=password_hash
        )
    except AuthError as e:
        error_msg = f"登录失败: {str(e)}"
        if e.raw_response:
            error_msg += f"\n详细信息: {e.raw_response}"
        logger.error(error_msg)
        return LoginResponse(
            success=False,
            message=error_msg
        )
    except Exception as e:
        error_msg = f"登录时发生未知错误: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return LoginResponse(
            success=False,
            message=error_msg
        )

@router.post("/auth/enable/{username}")
async def enable_account(username: str):
    """启用账号"""
    config_manager = ConfigManager.get_instance()
    await config_manager.enable_account(username)
    return {"success": True, "message": f"账号 {username} 已启用"}

@router.post("/auth/disable/{username}")
async def disable_account(username: str):
    """禁用账号"""
    config_manager = ConfigManager.get_instance()
    await config_manager.disable_account(username)
    return {"success": True, "message": f"账号 {username} 已禁用"}

@router.get("/auth/accounts")
async def list_accounts():
    """获取所有账号状态"""
    config_manager = ConfigManager.get_instance()
    accounts = []
    for account in config_manager.accounts:
        accounts.append({
            "username": account.get("username"),
            "enabled": account.get("enabled", True),
            "expires_at": account.get("expires_at")
        })
    return {"accounts": accounts} 