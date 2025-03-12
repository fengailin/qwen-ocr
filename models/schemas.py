

from pydantic import BaseModel, Field

class RecognizeUrlRequest(BaseModel):
    imageUrl: str = Field(..., description="图片的 URL 地址")

class RecognizeBase64Request(BaseModel):
    base64Image: str = Field(..., description="Base64 格式的图片内容")

class RecognizeFileRequest(BaseModel):
    imageId: str = Field(..., description="图片上传后返回的 imageId")
