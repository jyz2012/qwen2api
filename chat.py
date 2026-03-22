import hashlib
import json
import uuid
import time
import asyncio
import re
import mimetypes
import base64
from typing import List, Dict, Optional, AsyncGenerator
from dataclasses import dataclass, field
 
import httpx
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from sth import login_with_password, parse_api_key

import logging
 
# ==================== 配置 ====================
BASE_URL = "https://chat.qwen.ai"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0",
    "Content-Type": "application/json",
    "Accept": "*/*",
    "Connection": "keep-alive",
}

def create_client(timeout: float = 30.0) -> httpx.AsyncClient:
    """创建带有重试机制的HTTP客户端"""
    transport = httpx.AsyncHTTPTransport(retries=3, verify=False)
    return httpx.AsyncClient(timeout=timeout, transport=transport)

# ==================== 日志工具 ====================
# 使用 FastAPI/Uvicorn 的日志工具
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("uvicorn.error")
 
# ==================== 缓存管理 ====================
@dataclass
class CachedImage:
    url: str
    timestamp: int = field(default_factory=lambda: int(time.time()))
 
image_cache: Dict[str, CachedImage] = {}
 
 
@dataclass
class CachedToken:
    token: str
    expires_at: int
    email: str
 
token_cache: Dict[str, CachedToken] = {}
cache_lock = asyncio.Lock()
 
 
# ==================== Token管理 ====================
def get_cache_key(email: str) -> str:
    return hashlib.md5(email.encode()).hexdigest()
 
 
async def get_cached_token(email: str) -> Optional[str]:
    key = get_cache_key(email)
    async with cache_lock:
        if key in token_cache:
            cached = token_cache[key]
            if time.time() < cached.expires_at:
                return cached.token
            else:
                del token_cache[key]
    return None
 
 
async def cache_token(email: str, token: str, expires_at: int):
    key = get_cache_key(email)
    async with cache_lock:
        token_cache[key] = CachedToken(token=token, expires_at=expires_at, email=email)
 
 
# ==================== 工具函数 ====================
def sha256_encrypt(data: str) -> str:
    """SHA256加密"""
    return hashlib.sha256(data.encode()).hexdigest()
 
 
def get_file_extension(mime_type: str) -> str:
    """从MIME类型获取文件扩展名"""
    extensions = {
        'image/jpeg': 'jpg',
        'image/png': 'png',
        'image/gif': 'gif',
        'image/webp': 'webp',
        'image/bmp': 'bmp',
    }
    return extensions.get(mime_type, 'png')
 
 
def generate_uuid() -> str:
    """生成UUID"""
    return str(uuid.uuid4())
 
 
# ==================== 图片上传到阿里云OSS ====================
async def request_sts_token(filename: str, filesize: int, filetype: str, token: str) -> Dict:
    """请求STS Token"""
    headers = DEFAULT_HEADERS.copy()
    headers["Authorization"] = f"Bearer {token}"
    
    payload = {
        "filename": filename,
        "filesize": filesize,
        "filetype": filetype
    }
    
    logger.info(f"[UPLOAD] 请求STS Token: {filename} ({filesize} bytes, {filetype})")
    
    async with create_client(timeout=30.0) as client:
        resp = await client.post(
            f"{BASE_URL}/api/v1/files/getstsToken",
            json=payload,
            headers=headers
        )
        
        if resp.status_code != 200:
            logger.error(f"[UPLOAD] 获取STS Token失败: {resp.text}")
            raise HTTPException(status_code=500, detail=f"获取STS Token失败: {resp.text}")
        
        data = resp.json()
        
        logger.info("[UPLOAD] STS Token获取成功")
        
        return {
            "credentials": {
                "access_key_id": data["access_key_id"],
                "access_key_secret": data["access_key_secret"],
                "security_token": data["security_token"],
            },
            "file_info": {
                "url": data["file_url"],
                "path": data["file_path"],
                "bucket": data["bucketname"],
                "endpoint": f"{data['region']}.aliyuncs.com",
                "id": data["file_id"]
            }
        }
 
 
async def upload_to_oss(file_buffer: bytes, sts_credentials: Dict, oss_info: Dict, content_type: str) -> str:
    """上传文件到阿里云OSS"""
    try:
        import oss2
    except ImportError:
        logger.error("[UPLOAD] 需要安装 oss2 库: pip install oss2")
        raise HTTPException(status_code=500, detail="需要安装 oss2 库: pip install oss2")
    
    auth = oss2.StsAuth(
        sts_credentials["access_key_id"],
        sts_credentials["access_key_secret"],
        sts_credentials["security_token"]
    )
    
    bucket = oss2.Bucket(
        auth,
        oss_info["endpoint"],
        oss_info["bucket"]
    )
    
    logger.info(f"[UPLOAD] 上传文件到OSS: {oss_info['path']} ({len(file_buffer)} bytes)")
    
    result = bucket.put_object(
        oss_info["path"],
        file_buffer,
        headers={"Content-Type": content_type}
    )
    
    if result.status != 200:
        logger.error(f"[UPLOAD] OSS上传失败，状态码: {result.status}")
        raise HTTPException(status_code=500, detail="OSS上传失败")
    
    logger.info("[UPLOAD] 文件上传到OSS成功")
    
    return oss_info["url"]
 
 
async def upload_file_to_qwen_oss(file_buffer: bytes, filename: str, token: str) -> Dict:
    """完整的文件上传流程"""
    # 1. 获取文件信息
    filesize = len(file_buffer)
    mime_type = mimetypes.guess_type(filename)[0] or 'image/png'
    filetype_simple = mime_type.split('/')[0]  # image, video, audio, file
    
    # 2. 请求STS Token
    sts_data = await request_sts_token(filename, filesize, filetype_simple, token)
    
    # 3. 上传到OSS
    file_url = await upload_to_oss(
        file_buffer,
        sts_data["credentials"],
        sts_data["file_info"],
        mime_type
    )
    
    return {
        "status": 200,
        "file_url": file_url,
        "file_id": sts_data["file_info"]["id"],
        "message": "上传成功"
    }
 
 
async def process_image_upload(image_url: str, token: str) -> Optional[str]:
    """处理图片上传，返回上传后的URL"""
    # 检查是否为base64图片
    if not image_url.startswith("data:"):
        return image_url  # 已经是URL
    
    # 提取base64数据
    match = re.match(r"data:(.+?);base64,(.+)", image_url)
    if not match:
        raise HTTPException(status_code=400, detail="无效的base64图片格式")
    
    mime_type = match.group(1)
    base64_data = match.group(2)
    
    # 生成文件名和签名
    file_extension = get_file_extension(mime_type)
    filename = f"{uuid.uuid4()}.{file_extension}"
    signature = sha256_encrypt(base64_data)
    
    # 检查缓存
    if signature in image_cache:
        cached = image_cache[signature]
        # 缓存1小时有效
        if time.time() - cached.timestamp < 3600:
            logger.info(f"[UPLOAD] 使用缓存图片: {cached.url}")
            return cached.url
        else:
            del image_cache[signature]
    
    # 解码并上传
    try:
        file_buffer = base64.b64decode(base64_data)
        upload_result = await upload_file_to_qwen_oss(file_buffer, filename, token)
        
        if upload_result["status"] == 200:
            # 添加到缓存
            image_cache[signature] = CachedImage(url=upload_result["file_url"])
            return upload_result["file_url"]
    except Exception as e:
        logger.error(f"[UPLOAD] 图片上传失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"图片上传失败: {str(e)}")
    
    return None
 
 
# ==================== 消息解析 ====================
def extract_text_from_content(content) -> str:
    """从消息内容中提取文本"""
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))
        return "".join(text_parts)
    return ""
 
 
def format_single_message(message: Dict) -> str:
    """格式化单条消息为文本（带角色标注）"""
    role = message.get("role", "")
    content = extract_text_from_content(message.get("content", ""))
    return f"{role}:{content}" if content.strip() else ""
 
 
def format_history_messages(messages: List[Dict]) -> str:
    """格式化历史消息"""
    formatted_parts = []
    for message in messages:
        formatted = format_single_message(message)
        if formatted:
            formatted_parts.append(formatted)
    return ";".join(formatted_parts)
 
 
async def process_single_message(messages: List[Dict], thinking_config: Dict, chat_type: str, token: str) -> List[Dict]:
    """处理单条消息"""
    for message in messages:
        if message.get("role") not in ["user", "assistant"]:
            continue
        
        message["chat_type"] = chat_type
        message["extra"] = {}
        message["feature_config"] = thinking_config
        
        content = message.get("content")
        if not isinstance(content, list):
            continue
        
        new_content = []
        for item in content:
            if item.get("type") in ["image", "image_url"]:
                # 处理图片上传
                image_url = item.get("image") or item.get("image_url", {}).get("url")
                if image_url:
                    uploaded_url = await process_image_upload(image_url, token)
                    if uploaded_url:
                        new_content.append({
                            "type": "image",
                            "image": uploaded_url
                        })
            elif item.get("type") == "text":
                new_content.append({
                    "type": "text",
                    "text": item.get("text", ""),
                    "chat_type": "t2t",
                    "feature_config": thinking_config
                })
        
        if new_content:
            message["content"] = new_content
    
    return messages
 
 
async def parse_messages(messages: List[Dict], thinking_config: Dict, chat_type: str, token: str) -> List[Dict]:
    """解析消息，处理图片上传和历史消息"""
    
    # 单条消息：使用简单处理
    if len(messages) <= 1:
        logger.info("[PARSER] 单条消息，使用简单处理")
        return await process_single_message(messages, thinking_config, chat_type, token)
    
    # 多条消息：格式化历史消息
    logger.info("[PARSER] 多条消息，格式化处理")
    history_messages = messages[:-1]
    last_message = messages[-1]
    
    # 格式化历史文本
    history_text = format_history_messages(history_messages)
    
    # 处理最后一条消息
    final_content = []
    last_message_text = ""
    
    if isinstance(last_message.get("content"), str):
        last_message_text = last_message["content"]
    elif isinstance(last_message.get("content"), list):
        for item in last_message["content"]:
            if item.get("type") == "text":
                last_message_text += item.get("text", "")
            elif item.get("type") in ["image", "image_url"]:
                # 处理图片上传
                image_url = item.get("image") or item.get("image_url", {}).get("url")
                if image_url:
                    uploaded_url = await process_image_upload(image_url, token)
                    if uploaded_url:
                        final_content.append({
                            "type": "image",
                            "image": uploaded_url
                        })
    
    # 组合文本
    combined_text = ""
    if history_text:
        combined_text += history_text + ";"
    if last_message_text.strip():
        combined_text += f"{last_message['role']}:{last_message_text}"
    
    # 构建最终消息
    if final_content:
        # 有图片的情况
        final_content.insert(0, {
            "type": "text",
            "text": combined_text,
            "chat_type": "t2t",
            "feature_config": thinking_config
        })
        
        return [{
            "role": "user",
            "content": final_content,
            "chat_type": chat_type,
            "extra": {},
            "feature_config": thinking_config
        }]
    else:
        # 纯文本情况
        return [{
            "role": "user",
            "content": combined_text,
            "chat_type": chat_type,
            "extra": {},
            "feature_config": thinking_config
        }]
 
 
# ==================== 辅助函数 ====================
def get_chat_type(model: str) -> str:
    """根据模型名称确定聊天类型"""
    if not model:
        return "t2t"
    
    model_lower = model.lower()
    if "-search" in model_lower:
        return "search"
    elif "-deep-research" in model_lower:
        return "deep_research"
    return "t2t"
 
 
def parse_model(model: str) -> str:
    """解析模型名称，移除特殊后缀"""
    if not model:
        return "qwen3-coder-plus"
    
    for suffix in ["-search", "-thinking", "-image", "-video", "-edit", "-deep-research"]:
        model = model.replace(suffix, "")
    
    return model
 
 
def is_thinking_enabled(model: str, enable_thinking: bool, thinking_budget: int) -> Dict:
    """判断是否启用思考模式"""
    config = {
        "output_schema": "phase",
        "thinking_enabled": False,
        "thinking_budget": 81920
    }
    
    if model and "-thinking" in model.lower():
        config["thinking_enabled"] = True
    
    if enable_thinking:
        config["thinking_enabled"] = True
    
    if 0 < thinking_budget < 38912:
        config["thinking_budget"] = thinking_budget
    
    return config
 
 
async def generate_chat_id(token: str, model: str) -> Optional[str]:
    """生成chat_id"""
    headers = DEFAULT_HEADERS.copy()
    headers["Authorization"] = f"Bearer {token}"
    
    payload = {
        "title": "New Chat",
        "models": [model],
        "chat_mode": "local",
        "chat_type": "t2i",
        "timestamp": int(time.time() * 1000)
    }
    
    async with create_client(timeout=30.0) as client:
        resp = await client.post(
            f"{BASE_URL}/api/v2/chats/new",
            json=payload,
            headers=headers
        )
        
        if resp.status_code == 200:
            data = resp.json()
            return data.get("data", {}).get("id")
    
    return None
 
 
# ==================== 思考模式处理器 ====================
class ThinkingHandler:
    """思考模式处理器"""
    
    def __init__(self):
        self.thinking_start = False
        self.thinking_end = False
        self.web_search_info = None
        self.web_search_table = None
    
    async def generate_markdown_table(self, web_search_info: Dict, mode: str = "text") -> str:
        """生成搜索结果的Markdown表格"""
        if not web_search_info:
            return ""
        
        if mode == "text":
            # 文本模式：简洁的引用列表
            parts = []
            for item in web_search_info.get("references", []):
                title = item.get("title", "未知标题")
                url = item.get("url", "")
                parts.append(f"- [{title}]({url})")
            return "\n\n---\n\n**参考来源：**\n\n" + "\n".join(parts) if parts else ""
        else:
            # 表格模式：详细的表格
            rows = []
            for item in web_search_info.get("references", []):
                title = item.get("title", "未知标题")
                url = item.get("url", "")
                snippet = item.get("snippet", "")[:100]
                rows.append(f"| [{title}]({url}) | {snippet}... |")
            
            if rows:
                header = "\n\n---\n\n**参考来源：**\n\n| 来源 | 摘要 |\n|------|------|\n"
                return header + "\n".join(rows)
            return ""
    
    async def process_delta(self, delta: Dict, enable_thinking: bool, enable_web_search: bool) -> Optional[str]:
        """处理流式响应的delta，返回需要发送的内容"""
        content = ""
        
        # 处理web_search信息
        if delta.get("name") == "web_search":
            self.web_search_info = delta.get("extra", {}).get("web_search_info")
            return None
        
        # 只处理think和answer阶段的内容
        phase = delta.get("phase")
        if phase not in ["think", "answer"]:
            return None
        
        content = delta.get("content", "")
        if not content:
            return None
        
        # 开始思考
        if phase == "think" and not self.thinking_start:
            self.thinking_start = True
            if self.web_search_info:
                content = f"\n\n---\n\n{await self.generate_markdown_table(self.web_search_info, 'text')}\n\n\n\n\n\n**思考过程：**\n\n{content}"
            else:
                content = f"<think>\n{content}"
        
        # 结束思考，开始回答
        elif phase == "answer" and not self.thinking_end and self.thinking_start:
            self.thinking_end = True
            content = f"</think>\n{content}"
        
        return content
    
    async def finalize_response(self, enable_thinking: bool, enable_web_search: bool) -> Optional[str]:
        """生成最终的响应内容"""
        # 如果禁用了思考模式，但存在搜索信息，添加到末尾
        if not enable_thinking and self.web_search_info:
            return await self.generate_markdown_table(self.web_search_info, "text")
        return None
 
 
# ==================== 图片/视频生成 ====================
async def handle_t2i_response(response, model: str) -> Dict:
    """处理图片生成流式响应"""
    content_url = None
    
    # 使用 aiter_lines() 自动处理编码
    async for line in response.aiter_lines():
        line = line.strip()
        if line.startswith("data:"):
            try:
                json_str = line[5:].strip()
                if json_str == "[DONE]":
                    break
                
                json_obj = json.loads(json_str)
                if json_obj.get("choices") and len(json_obj["choices"]) > 0:
                    delta = json_obj["choices"][0].get("delta", {})
                    url = delta.get("content", "").strip()
                    if url and not content_url:
                        content_url = url
                        logger.info(f"[CHAT] 生成图片URL: {content_url}")
                        break
            except json.JSONDecodeError:
                pass
    
    if content_url:
        return {
            "id": f"chatcmpl-{generate_uuid()}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": f"![image]({content_url})"
                    },
                    "finish_reason": "stop"
                }
            ]
        }
    
    raise HTTPException(status_code=500, detail="Failed to generate image")
 

# ==================== 聊天核心函数 ====================
async def chat(
    email: str,
    password: str,
    model: str,
    messages: List[Dict],
    stream: bool,
    enable_thinking: bool = False,
    thinking_budget: int = 81920,
) -> tuple[Optional[Dict], Optional[AsyncGenerator]]:
    """
    聊天补全接口 - 完整版，支持搜索和思考模式
    
    Returns:
        (response_dict, async_generator) or (response_dict, None)
    """
    # 获取或登录令牌
    token = await get_cached_token(email)
    if not token:
        token, expires_at = await login_with_password(email, password)
        await cache_token(email, token, expires_at)
    
    # 创建新的客户端
    client = create_client(timeout=60.0)
    
    try:
        headers = DEFAULT_HEADERS.copy()
        headers["Authorization"] = f"Bearer {token}"
        
        # 创建聊天会话
        chat_id = await generate_chat_id(token, model)
        if not chat_id:
            await client.aclose()
            raise HTTPException(status_code=500, detail="Failed to create chat session")
        
        # 确定聊天类型和思考配置
        chat_type = get_chat_type(model)
        enable_web_search = chat_type == "search"
        thinking_config = is_thinking_enabled(model, enable_thinking, thinking_budget)
        
        logger.info(f"[CHAT] 聊天类型: {chat_type}, 搜索: {enable_web_search}, 思考: {thinking_config['thinking_enabled']}")
        
        # 解析消息（处理图片上传）
        parsed_messages = await parse_messages(messages, thinking_config, chat_type, token)
        # 去掉最后一条消息的末尾 /no_think 并替换 body 中的 parsed_messages
        if parsed_messages and isinstance(parsed_messages, list):
            last_msg = parsed_messages[-1]
            if isinstance(last_msg, dict) and "content" in last_msg:
                content = last_msg["content"]
                if isinstance(content, str) and content.endswith("/no_think"):
                    last_msg["content"] = content[:-len("/no_think")].rstrip()
                elif isinstance(content, list):
                    # 如果是列表格式，检查最后一个 text 项
                    for item in reversed(content):
                        if item.get("type") == "text":
                            text = item.get("text", "")
                            if text.endswith("/no_think"):
                                item["text"] = text[:-len("/no_think")].rstrip()
                            break
        
        # 构建请求体
        body = {
            "stream": stream,
            "incremental_output": stream,
            "chat_type": chat_type,
            "model": parse_model(model),
            "messages": parsed_messages,
            "session_id": generate_uuid(),
            "id": generate_uuid(),
            "sub_chat_type": chat_type,
            "chat_mode": "normal",
            "chat_id": chat_id,
        }
        print(body)
        url = f"{BASE_URL}/api/v2/chat/completions?chat_id={chat_id}"
        
        if stream:
            # 流式响应
            thinking_handler = ThinkingHandler()
            message_id = generate_uuid()
            
            async def stream_generator() -> AsyncGenerator:
                try:
                    async with client.stream("POST", url, json=body, headers=headers) as resp:
                        resp.raise_for_status()
                        
                        async for line in resp.aiter_lines():
                            if line.startswith("data: "):
                                data = line[6:]
                                if data == "[DONE]":
                                    # 发送结束前的最终内容
                                    final_content = await thinking_handler.finalize_response(enable_thinking, enable_web_search)
                                    if final_content:
                                        yield f"data: {json.dumps({'choices': [{'delta': {'content': final_content}}]})}\n\n"
                                    
                                    yield f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
                                    break
                                
                                try:
                                    chunk = json.loads(data)
                                    
                                    # 处理思考模式和搜索
                                    if chunk.get("choices") and len(chunk["choices"]) > 0:
                                        delta = chunk["choices"][0].get("delta", {})
                                        
                                        # 提取内容和思考信息
                                        content = await thinking_handler.process_delta(delta, enable_thinking, enable_web_search)
                                        
                                        if content:
                                            result = {
                                                "id": chunk.get("id", f"chatcmpl-{message_id}"),
                                                "object": "chat.completion.chunk",
                                                "created": int(time.time()),
                                                "model": model,
                                                "choices": [
                                                    {
                                                        "index": 0,
                                                        "delta": {"content": content},
                                                        "finish_reason": None,
                                                    }
                                                ],
                                            }
                                            yield f"data: {json.dumps(result, ensure_ascii=False)}\n\n"
                                
                                except json.JSONDecodeError:
                                    pass
                
                except Exception as e:
                    logger.error(f"[CHAT] 流式响应错误: {str(e)}")
                    yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
                finally:
                    await client.aclose()
            
            return None, stream_generator()
        
        else:
            # 非流式响应
            resp = await client.post(url, json=body, headers=headers)
            
            if resp.status_code != 200:
                await client.aclose()
                logger.error(f"[CHAT] 请求失败: {resp.text}")
                raise HTTPException(status_code=500, detail=f"Request failed: {resp.text}")
            
            data = resp.json()
            
            # 提取内容
            content_text = ""
            if "choices" in data and len(data["choices"]) > 0:
                choice = data["choices"][0]
                if "message" in choice:
                    content_text = choice["message"].get("content", "")
            
            if not content_text:
                content_text = (
                    data.get("message", {}).get("content")
                    or data.get("content")
                    or data.get("data", {}).get("content")
                    or ""
                )
            
            await client.aclose()
            
            return {
                "id": f"chatcmpl-{generate_uuid()}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": str(content_text)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
            }, None
    
    except HTTPException:
        await client.aclose()
        raise
    except Exception as e:
        await client.aclose()
        logger.error(f"[CHAT] 聊天处理错误: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
 
 
# ==================== FastAPI 应用 ====================
app = FastAPI(title="Qwen2API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
 
 
@app.get("/")
async def root():
    return {
        "name": "Qwen2API",
        "version": "2.0.0",
        "endpoints": {
            "chat": "/v1/chat/completions",
            "models": "/v1/models",
            "images": "/v1/images/generations",
            "videos": "/v1/videos/generations",
        },
        "usage": "API Key format: email:password",
    }
 
 
@app.get("/v1/models")
async def list_models(authorization: str = Header(None)):
    """获取模型列表"""
    try:
        email, password = parse_api_key(authorization)
        token = await get_cached_token(email)
        if not token:
            token, _ = await login_with_password(email, password)
            await cache_token(email, token, int(time.time()) + 3600)
    except Exception:
        # 失败则尝试匿名请求
        headers = DEFAULT_HEADERS.copy()
        async with create_client(timeout=30.0) as client:
            resp = await client.get("https://chat.qwen.ai/api/models", headers=headers)
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
    
    headers = DEFAULT_HEADERS.copy()
    headers["Cookie"] = f"token={token};"
    
    async with create_client(timeout=30.0) as client:
        resp = await client.get("https://chat.qwen.ai/api/models", headers=headers)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()
 
 
@app.post("/v1/chat/completions")
async def chat_completions(req: Request, authorization: str = Header(None)):
    """聊天补全API - 支持文本、图片、思考、搜索等所有功能"""
    try:
        if not authorization:
            raise HTTPException(status_code=401, detail="Missing Authorization header")
        
        # 解析 API Key
        email, password = parse_api_key(authorization)
        
        # 解析请求体
        body = await req.json()
        model = body.get("model", "qwen3-coder-plus")
        messages = body.get("messages", [])
        stream = body.get("stream", False)
        enable_thinking = body.get("enable_thinking", False)
        thinking_budget = body.get("thinking_budget", 81920)
        
        # 调用聊天函数
        resp_dict, gen = await chat(
            email, password, model, messages, stream, enable_thinking, thinking_budget
        )
        
        if stream:
            async def generator():
                try:
                    async for chunk in gen:
                        yield chunk
                except Exception as e:
                    yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
            
            return StreamingResponse(
                generator(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            )
        else:
            return resp_dict
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] API错误: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
 
 
if __name__ == "__main__":
    import uvicorn
    
    logger.info("[SERVER] Qwen2API 启动中...")
    uvicorn.run(app, host="0.0.0.0", port=3000)