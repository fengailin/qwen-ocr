# QwenLM OCR API 服务

## 免责声明

本项目仅供学习和研究使用，禁止用于商业用途。使用本项目即表示您同意以下条款：

1. 本项目是一个基于通义千问大模型的 OCR 的开源实现，仅用于学习和研究目的。
2. 使用者需遵守相关法律法规，不得将本项目用于任何违法或侵犯他人权益的活动。
3. 使用者在使用本项目过程中造成的任何直接或间接损失，项目作者不承担任何责任。
4. 本项目不提供任何形式的保证，包括但不限于适销性和特定用途适用性的保证。
5. 使用者应自行承担使用本项目的风险和后果。

## 项目介绍

这是一个基于 FastAPI 框架实现的 OCR 服务，提供了图片文字识别的 RESTful API 接口。支持多种输入方式：

- 图片 URL 识别
- Base64 编码图片识别
- 文件上传识别
- 代理上传识别

## 快速开始

### 环境要求

- Python 3.8 或更高版本

### 安装步骤

1. 克隆项目到本地：
```bash
git clone [项目地址]
cd qwen-ocr
```

2. 创建并激活虚拟环境：
```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Linux/Mac
source venv/bin/activate
```

3. 安装依赖：
```bash
pip install -r requirements.txt
```

4. 配置服务：
   - 复制 `config.yaml.example` 为 `config.yaml`
   - 根据实际情况修改配置文件

### 启动服务

```bash
python run.py
```

默认配置：
- 服务地址：http://127.0.0.1:8000
- Swagger 文档：http://127.0.0.1:8000/docs
- ReDoc 文档：http://127.0.0.1:8000/redoc

### 命令行参数

- `--host`: 服务器监听地址（默认：127.0.0.1）
- `--port`: 服务器监听端口（默认：8000）
- `--reload`: 是否启用热重载（默认：True）
- `--workers`: 工作进程数（默认：1）

示例：
```bash
python run.py --host 0.0.0.0 --port 8080 --workers 4
```

## API 文档

详细的 API 文档请访问服务启动后的 Swagger 文档页面：`http://[服务地址]/docs`

## 注意事项

1. 本项目仅供学习研究使用，严禁用于商业用途
2. 使用前请确保已经阅读并同意免责声明
3. 请遵守相关法律法规和服务条款
4. 建议在生产环境中增加适当的安全措施


## 许可证

本项目采用 MIT 许可证。 