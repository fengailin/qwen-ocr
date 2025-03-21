import os
import yaml
import json
import logging
import asyncio
import aiofiles
import time
from typing import Dict, Any, Optional, List
from pathlib import Path

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

class ConfigManager:
    _instance = None
    _config_file = "config.yaml"
    _lock = asyncio.Lock()
    _save_lock = asyncio.Lock()
    _save_timer = None
    _dirty = False
    _last_load_time = 0
    _config_cache = None
    _save_delay = 5  # 延迟保存时间（秒）
    _cache_ttl = 60  # 缓存生存时间（秒）

    def __init__(self):
        self._base_api_url = 'https://chat.qwen.ai'
        self._accounts = []
        self._default_model = 'qwen-turbo-latest'
        self._available_models = ['qwen-turbo-latest']
        self._load_config_if_needed()

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def default_model(self):
        """获取默认模型名称"""
        self._load_config_if_needed()
        return self._default_model

    @property
    def available_models(self):
        """获取可用模型列表"""
        self._load_config_if_needed()
        return self._available_models

    def _should_reload_config(self):
        """检查是否需要重新加载配置"""
        if self._config_cache is None:
            return True
        
        current_time = time.time()
        # 如果缓存超过TTL，重新加载
        if current_time - self._last_load_time > self._cache_ttl:
            return True
            
        # 检查文件是否被修改
        try:
            mtime = os.path.getmtime(self._config_file)
            return mtime > self._last_load_time
        except OSError:
            return True

    def _load_config_if_needed(self):
        """按需加载配置"""
        if not self._should_reload_config():
            return

        try:
            logger.debug(f"开始加载配置文件: {self._config_file}")
            with open(self._config_file, 'r', encoding='utf-8') as f:
                self._config_cache = yaml.safe_load(f)
                self._base_api_url = self._config_cache.get('base_api_url', 'https://chat.qwen.ai')
                self._accounts = self._config_cache.get('accounts', [])
                
                # 加载模型配置
                model_config = self._config_cache.get('model_config', {})
                self._default_model = model_config.get('default_model', 'qwen-turbo-latest')
                self._available_models = model_config.get('available_models', ['qwen-turbo-latest'])
                
                # 确保每个账号都有 enabled 字段
                for account in self._accounts:
                    if 'enabled' not in account:
                        account['enabled'] = True
            self._last_load_time = time.time()
            logger.debug(f"配置文件加载完成，包含 {len(self._accounts)} 个账号")
        except FileNotFoundError:
            logger.warning(f"配置文件 {self._config_file} 不存在，使用默认配置")
            self._base_api_url = 'https://chat.qwen.ai'
            self._accounts = []
            self._default_model = 'qwen-turbo-latest'
            self._available_models = ['qwen-turbo-latest']
            self._config_cache = {
                'base_api_url': self._base_api_url,
                'accounts': self._accounts,
                'model_config': {
                    'default_model': self._default_model,
                    'available_models': self._available_models
                }
            }
        except Exception as e:
            logger.error(f"加载配置文件时发生错误: {str(e)}", exc_info=True)
            raise

    async def _schedule_save(self):
        """调度延迟保存操作"""
        if self._save_timer is not None:
            self._save_timer.cancel()
        
        self._dirty = True
        
        async def delayed_save():
            await asyncio.sleep(self._save_delay)
            if self._dirty:
                await self._save_config_to_file()
                self._dirty = False
        
        self._save_timer = asyncio.create_task(delayed_save())

    async def _save_config_to_file(self):
        """实际的文件保存操作"""
        async with self._save_lock:
            try:
                logger.debug("开始保存配置文件...")
                config = {
                    'base_api_url': self._base_api_url,
                    'accounts': self._accounts,
                    'common_cookies': self.common_cookies,  # 确保保存公共cookie配置
                    'model_config': {
                        'default_model': self._default_model,
                        'available_models': self._available_models
                    }
                }
                
                # 先将配置写入临时文件
                temp_file = f"{self._config_file}.tmp"
                async with aiofiles.open(temp_file, 'w', encoding='utf-8') as f:
                    await f.write(yaml.safe_dump(config, allow_unicode=True))
                
                # 如果写入成功，直接替换原文件
                os.replace(temp_file, self._config_file)
                
                self._last_load_time = time.time()  # 更新最后加载时间
                logger.debug("配置文件保存完成")
            except Exception as e:
                logger.error(f"保存配置文件时发生错误: {str(e)}", exc_info=True)
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except:
                        pass
                raise

    def get_enabled_accounts(self) -> List[Dict[str, Any]]:
        """获取所有启用的账号"""
        self._load_config_if_needed()
        return [account for account in self._accounts if account.get('enabled', True)]

    def get_account_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """根据用户名获取账号信息"""
        self._load_config_if_needed()
        for account in self._accounts:
            if account.get('username') == username:
                return account
        return None

    @property
    def common_cookies(self) -> dict:
        """获取公共 cookie 配置"""
        self._load_config_if_needed()
        return self._config_cache.get('common_cookies', {})

    def _merge_cookies(self, account_cookie: str) -> str:
        """
        合并账号 cookie 和公共 cookie
        
        Args:
            account_cookie: 账号的原始 cookie 字符串
            
        Returns:
            str: 合并后的 cookie 字符串
        """
        # 获取公共 cookie
        common_cookies = self.common_cookies
        
        # 如果没有公共 cookie，直接返回账号 cookie
        if not common_cookies:
            return account_cookie
            
        # 将公共 cookie 转换为字符串形式
        common_cookie_str = '; '.join([f"{k}={v}" for k, v in common_cookies.items()])
        
        # 合并 cookie
        if account_cookie:
            return f"{account_cookie}; {common_cookie_str}"
        return common_cookie_str

    def get_account_by_cookie(self, cookie: str) -> Optional[Dict[str, Any]]:
        """
        根据cookie获取账号信息。
        现在支持包含公共字段的cookie匹配。
        
        Args:
            cookie: cookie字符串
            
        Returns:
            Optional[Dict[str, Any]]: 匹配的账号信息
        """
        self._load_config_if_needed()
        
        # 如果cookie为空，直接返回None
        if not cookie:
            return None
            
        # 解析cookie字符串为字典
        def parse_cookie(cookie_str: str) -> dict:
            cookie_dict = {}
            for item in cookie_str.split(';'):
                item = item.strip()
                if not item:
                    continue
                if '=' in item:
                    key, value = item.split('=', 1)
                    cookie_dict[key.strip()] = value.strip()
            return cookie_dict
            
        # 解析输入的cookie
        input_cookies = parse_cookie(cookie)
        
        # 获取公共cookie字段
        common_cookies = self.common_cookies
        
        for account in self._accounts:
            if not account.get('enabled', True):
                continue
                
            account_cookie = account.get('cookie', '')
            if not account_cookie:
                continue
                
            # 解析账号的cookie
            account_cookies = parse_cookie(account_cookie)
            
            # 检查账号特有的cookie字段是否匹配
            # 我们只需要检查账号cookie中的关键字段
            key_fields = {'token', 'SERVERID', 'SERVERCORSID'}
            match = True
            for field in key_fields:
                if field in account_cookies and field in input_cookies:
                    if account_cookies[field] != input_cookies[field]:
                        match = False
                        break
            
            if match:
                return account
                
        return None

    def get_cookie_with_common_fields(self, account_cookie: str) -> str:
        """
        获取包含公共字段的完整 cookie
        
        Args:
            account_cookie: 账号的原始 cookie 字符串
            
        Returns:
            str: 包含公共字段的完整 cookie 字符串
        """
        return self._merge_cookies(account_cookie)

    async def add_account(self, username: str, password: str = None, cookie: str = None, token: str = None, enabled: bool = True) -> None:
        """添加新账号"""
        async with self._lock:
            self._load_config_if_needed()
            account = self.get_account_by_username(username)
            if account:
                account.update({
                    'password': password,
                    'cookie': cookie,
                    'token': token,
                    'enabled': enabled
                })
            else:
                self._accounts.append({
                    'username': username,
                    'password': password,
                    'cookie': cookie,
                    'token': token,
                    'enabled': enabled
                })
            await self._schedule_save()

    async def update_account(self, username: str, token: str, cookie: str, expires_at: int, enabled: bool = True) -> None:
        """更新账号信息"""
        logger.debug(f"开始更新账号信息: {username}")
        async with self._lock:
            try:
                self._load_config_if_needed()
                account = self.get_account_by_username(username)
                if account:
                    logger.debug(f"更新现有账号: {username}")
                    account.update({
                        'token': token,
                        'cookie': cookie,
                        'expires_at': expires_at,
                        'enabled': enabled
                    })
                else:
                    logger.debug(f"添加新账号: {username}")
                    self._accounts.append({
                        'username': username,
                        'token': token,
                        'cookie': cookie,
                        'expires_at': expires_at,
                        'enabled': enabled
                    })
                
                logger.debug(f"调度保存账号配置: {username}")
                await self._schedule_save()
                logger.debug(f"账号信息更新完成: {username}")
            except Exception as e:
                logger.error(f"更新账号信息时发生错误: {str(e)}", exc_info=True)
                raise

    async def disable_account(self, username: str) -> None:
        """禁用账号"""
        logger.debug(f"开始禁用账号: {username}")
        async with self._lock:
            try:
                self._load_config_if_needed()
                account = self.get_account_by_username(username)
                if account:
                    account['enabled'] = False
                    await self._schedule_save()
                    logger.debug(f"账号已禁用: {username}")
                else:
                    logger.warning(f"未找到要禁用的账号: {username}")
            except Exception as e:
                logger.error(f"禁用账号时发生错误: {str(e)}", exc_info=True)
                raise

    async def enable_account(self, username: str) -> None:
        """启用账号"""
        logger.debug(f"开始启用账号: {username}")
        async with self._lock:
            try:
                self._load_config_if_needed()
                account = self.get_account_by_username(username)
                if account:
                    account['enabled'] = True
                    await self._schedule_save()
                    logger.debug(f"账号已启用: {username}")
                else:
                    logger.warning(f"未找到要启用的账号: {username}")
            except Exception as e:
                logger.error(f"启用账号时发生错误: {str(e)}", exc_info=True)
                raise

    async def remove_account(self, username: str) -> None:
        """删除账号"""
        async with self._lock:
            self._load_config_if_needed()
            self._accounts = [acc for acc in self._accounts if acc.get('username') != username]
            await self._schedule_save()

    @property
    def base_api_url(self) -> str:
        """获取基础API URL"""
        self._load_config_if_needed()
        return self._base_api_url

    @property
    def accounts(self) -> list:
        """获取所有账号信息"""
        self._load_config_if_needed()
        return self._accounts 