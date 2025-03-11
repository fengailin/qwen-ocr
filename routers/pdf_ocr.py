from fastapi import APIRouter, BackgroundTasks, UploadFile, HTTPException, File
from typing import List, Dict, Any
import asyncio
import json
import os
import uuid
import re
from datetime import datetime
import zipfile
import io
import aiofiles
from services.ocr import recognize_image, upload_image_info
import random
import logging
from fastapi.responses import PlainTextResponse
from services.ocr import process_base64_image, OCRError
from services.config_manager import ConfigManager

router = APIRouter(prefix="/api", tags=["pdf_ocr"])
logger = logging.getLogger(__name__)

def natural_sort_key(s):
    """实现自然排序的key函数"""
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split('([0-9]+)', s)]

# 创建数据存储目录
DATA_DIR = "data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

def get_cookie_config() -> str:
    """
    从配置中随机返回一个包含公共字段的完整 cookie 字符串
    """
    try:
        config_manager = ConfigManager.get_instance()
        accounts = config_manager.accounts
        if not accounts:
            raise HTTPException(status_code=500, detail="未找到任何账号配置")
        account = random.choice(accounts)
        return config_manager.get_cookie_with_common_fields(account['cookie'])
    except Exception as e:
        logger.error(f"获取cookie配置失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取cookie配置失败: {str(e)}")

def get_task_dir(task_id: str) -> str:
    """获取任务目录路径"""
    task_dir = os.path.join(DATA_DIR, task_id)
    if not os.path.exists(task_dir):
        os.makedirs(task_dir)
    return task_dir

def get_task_file_path(task_id: str) -> str:
    """获取任务JSON文件路径"""
    return os.path.join(get_task_dir(task_id), "task.json")

def get_content_file_path(task_id: str, filename: str) -> str:
    """获取内容文件路径"""
    return os.path.join(get_task_dir(task_id), f"{filename}.txt")

async def save_task_data(task_id: str, data: dict):
    """异步保存任务数据到JSON文件"""
    file_path = get_task_file_path(task_id)
    async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
        await f.write(json.dumps(data, ensure_ascii=False, indent=2))

async def save_content_file(task_id: str, filename: str, content: str):
    """异步保存OCR内容到文本文件"""
    file_path = get_content_file_path(task_id, filename)
    async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
        await f.write(content)

async def load_task_data(task_id: str) -> dict:
    """异步加载任务数据"""
    file_path = get_task_file_path(task_id)
    try:
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            content = await f.read()
            return json.loads(content)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="任务不存在")

async def process_images(zip_bytes: bytes, task_id: str):
    """后台任务：处理ZIP中的所有图片"""
    task_data = {
        "id": task_id,
        "status": "processing",
        "total_images": 0,
        "processed_images": 0,
        "results": [],
        "errors": [],
        "created_at": datetime.now().isoformat(),
        "completed_at": None
    }
    
    try:
        # 读取ZIP文件
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zip_file:
            # 获取所有图片文件并按自然顺序排序
            image_files = sorted([
                f for f in zip_file.namelist()
                if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp'))
            ], key=natural_sort_key)
            
            task_data["total_images"] = len(image_files)
            await save_task_data(task_id, task_data)

            if not image_files:
                raise ValueError("ZIP文件中没有找到图片文件")

            # 创建信号量来限制并发数
            upload_semaphore = asyncio.Semaphore(10)  # 限制5个并发上传
            chat_semaphore = asyncio.Semaphore(10)    # 限制5个并发聊天

            async def process_single_image(img_name: str, index: int):
                async with upload_semaphore:
                    try:
                        # 读取图片数据
                        img_data = zip_file.read(img_name)
                        
                        # 获取cookie和token
                        cookie = get_cookie_config()
                        
                        # 先上传图片
                        file_info = await upload_image_info(
                            image_bytes=img_data,
                            filename=os.path.basename(img_name),
                            token=cookie,
                            cookie=cookie
                        )
                        
                        # 进行OCR识别
                        async with chat_semaphore:
                            # 使用数字前缀确保顺序
                            file_prefix = f"{index:04d}"
                            content_buffer = []
                            
                            result = await recognize_image(
                                token=cookie,
                                cookie=cookie,
                                file_info=file_info,
                                
                            )
                            
                            if result.get("success") and "result" in result:
                                content = result["result"]
                                content_buffer.append(content)
                                # 实时保存当前累积的内容
                                await save_content_file(task_id, file_prefix, "".join(content_buffer))
                            
                            # 最终内容
                            final_content = "".join(content_buffer)
                            
                            # 更新任务状态
                            task_data["results"].append({
                                "filename": img_name,
                                "content_file": f"{file_prefix}.txt",
                                "timestamp": datetime.now().isoformat()
                            })
                            task_data["processed_images"] += 1
                            await save_task_data(task_id, task_data)
                            
                    except Exception as e:
                        logger.error(f"处理图片 {img_name} 时发生错误: {str(e)}", exc_info=True)
                        task_data["errors"].append({
                            "filename": img_name,
                            "error": str(e),
                            "timestamp": datetime.now().isoformat()
                        })
                        await save_task_data(task_id, task_data)

            # 创建所有图片的处理任务
            tasks = [process_single_image(img_name, i) for i, img_name in enumerate(image_files)]
            
            # 并发执行所有任务
            await asyncio.gather(*tasks)
            
            # 更新任务状态为完成
            task_data["status"] = "completed"
            task_data["completed_at"] = datetime.now().isoformat()
            await save_task_data(task_id, task_data)
            
    except Exception as e:
        logger.error(f"处理任务 {task_id} 时发生错误: {str(e)}", exc_info=True)
        task_data["status"] = "failed"
        task_data["error"] = str(e)
        task_data["completed_at"] = datetime.now().isoformat()
        await save_task_data(task_id, task_data)

@router.post("/zip/ocr")
async def create_zip_ocr_task(
    background_tasks: BackgroundTasks,
    file: UploadFile
):
    """创建ZIP图片OCR任务"""
    if not file.filename.lower().endswith('.zip'):
        raise HTTPException(status_code=400, detail="只支持ZIP文件")
    
    # 读取ZIP文件内容
    zip_bytes = await file.read()
    
    # 创建任务ID和初始状态
    task_id = str(uuid.uuid4())
    initial_data = {
        "id": task_id,
        "filename": file.filename,
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "total_images": 0,
        "processed_images": 0,
        "results": [],
        "errors": []
    }
    
    # 创建任务目录
    get_task_dir(task_id)
    
    # 保存初始状态
    await save_task_data(task_id, initial_data)
    
    # 添加后台任务
    background_tasks.add_task(process_images, zip_bytes, task_id)
    
    return {
        "task_id": task_id,
        "status": "pending",
        "message": "ZIP图片OCR任务已创建"
    }

@router.get("/zip/ocr/{task_id}")
async def get_zip_ocr_results(task_id: str):
    """获取ZIP OCR任务的结果"""
    task_data = await load_task_data(task_id)
    return {
        "task_id": task_id,
        "status": task_data["status"],
        "progress": {
            "total_images": task_data["total_images"],
            "processed_images": task_data["processed_images"]
        },
        "results": task_data["results"],
        "errors": task_data["errors"],
        "created_at": task_data["created_at"],
        "completed_at": task_data.get("completed_at")
    }

@router.get("/zip/ocr/{task_id}/content")
async def get_zip_ocr_content(task_id: str):
    """获取ZIP OCR任务的完整内容"""
    task_data = await load_task_data(task_id)
    
    if task_data["status"] != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成")
    
    # 按顺序读取所有内容文件
    contents = []
    for result in sorted(task_data["results"], key=lambda x: x["content_file"]):
        try:
            file_path = get_content_file_path(task_id, result["content_file"].replace(".txt", ""))
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                contents.append(content)
        except Exception as e:
            logger.error(f"读取文件 {file_path} 失败: {str(e)}")
            continue
    
    # 返回合并后的内容
    return PlainTextResponse("\n\n".join(contents)) 