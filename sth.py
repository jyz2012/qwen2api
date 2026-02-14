import requests
import hashlib
import base64
import secrets
import time
from openai import OpenAI

# Call API
def call_qwen_api(access_token, model, messages, stream=True):
    client = OpenAI(
        api_key=access_token,  # 使用 OAuth token 作为 API key
        base_url='https://portal.qwen.ai/v1'
    )
    
    response = client.chat.completions.create(
        model=model,  # 或其他 qwen 模型
        messages=messages,
        stream=stream
    )
    
    return response

# Authorize
def authorize(user_code,user_token):
    api_url='https://chat.qwen.ai/api/v2/oauth2/authorize'
    authorize_url=f"https://chat.qwen.ai/authorize?user_code={user_code}&client=qwen-code"
    headers={
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": authorize_url,
        "Origin": "https://chat.qwen.ai",
        "Cookie": f"token={user_token};ssxmod_itna=1-iqGx9ii=iQ84uD4kxRhbDBexpPoWDuDl4BU=D2DIqGQGcD8xiKDHzIHQFPHDUhVqTp5NBPqwAxl9xGXPoexiNDAZ40iDCbnLx7Yju0NKQ8Kw5pPMzQY2bQWB6niXplYUm0ZIfOMK/_Z6pDKggP8DCPDExGkNeuH_DiiHx0rD0eDPxDYDG4mDneDexDdO/oAO4GWrjRKDlI_k4Daz4i3OtoDRPxD0oP_DQF3CbDDBr02eGuq77dAY_MKV4D1NDS4dmoD9p4DspDyemgU1V=xm48cHTMbT98LDCKDj6rIDmm_VtqvIbLY2Q0b/vYl_rexk/u4CTxzGxKGVz05A0RmuqexeNT4KGxAGqrThKAYDDWKlYtndqD5_A10kz9qsuie5E4e_iFjYYYxKDqI2_M05DE48Qxo0ro04=jYePm2G44D; ssxmod_itna2=1-iqGx9ii=iQ84uD4kxRhbDBexpPoWDuDl4BU=D2DIqGQGcD8xiKDHzIHQFPHDUhVqTp5NBPqwAxlexD3mKzYRDpeDFO2ifqz4GXHqAi=Fjfo_2kMG0ONsp3xfQ9Ku6cNkHiyGh6NAV7Dsnh2qRrcdGZyFX1YaeEAh=D5hd_yD2lAQO6DIa=DGW8cDT4OwKjgku1czO4IA1=5K=bBdBOGjX4y6X7M4BpBda60Cqcas2bQf=zK6=9YxN9HQalAr=UOnY4YzBoZzo0whNQ5N7CouQd=Px_H4NkMdZrliardPZrxKvrEBWrhx7WjlDYBOpK1EyGf7iYllIPDh4ZKqA=Sew4C5S0w==5Wph5262BrL7mUBo0cwtZmxFtfmNLcPqDtzGYuOEj7L5jo8Ob0Zd0GAvgpIZdK4veGEzO0dbLh6oS1jP8Ye6DrlD4QGwWaf7Ge7dNZuLRv82cjlPrUvYZDVOufQpl15sEF6WpvY4riDM_HAnwsGX3YyRGyMKwDdD; "
    }
    data={
        "approved":True,
        "user_code":user_code
    }
    try:
        resp = requests.post(api_url, headers=headers, json=data, timeout=30)
        # print(resp.json())
    except Exception as e:
        print(f"Error during authorize: {e}")
        raise e

# 刷新 token
def refresh_access_token(refresh_token):
    url = 'https://chat.qwen.ai/api/v1/oauth2/token'
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://chat.qwen.ai/",
        "Origin": "https://chat.qwen.ai"
    }
    data = {
        'client_id': 'f0304373b74a44d2b584a3fb70ca9e56',
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token
    }
    try:
        response = requests.post(url, headers=headers, data=data, timeout=15)
        return response.json()
    except Exception as e:
        print(f"Error refreshing token: {e}")
        raise e

# 生成 PKCE 参数
def generate_pkce_pair():
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8').rstrip('=')
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode('utf-8')).digest()
    ).decode('utf-8').rstrip('=')
    return code_verifier, code_challenge
 
# 请求设备授权
def request_device_code(code_challenge):
    url = "https://chat.qwen.ai/api/v1/oauth2/device/code"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://chat.qwen.ai/",
        "Origin": "https://chat.qwen.ai"
    }
    data = {
        "client_id": "f0304373b74a44d2b584a3fb70ca9e56",
        "scope": "openid profile email model.completion",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256"
    }
    try:
        response = requests.post(url, headers=headers, data=data, timeout=15)
        return response.json()
    except Exception as e:
        print(f"Error requesting device code: {e}")
        raise e
 
# 轮询获取令牌
def poll_for_token(device_code, code_verifier):
    url = "https://chat.qwen.ai/api/v1/oauth2/token"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://chat.qwen.ai/",
        "Origin": "https://chat.qwen.ai"
    }
    data = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "client_id": "f0304373b74a44d2b584a3fb70ca9e56",
        "device_code": device_code,
        "code_verifier": code_verifier
    }
    
    while True:
        try:
            response = requests.post(url, headers=headers, data=data, timeout=15)
            result = response.json()
            
            if 'access_token' in result:
                return result
            
            if result.get('error') == 'authorization_pending':
                time.sleep(5)  # 继续等待
            else:
                # 其他错误，如 expired_token 或 access_denied
                print(f"Polling status: {result}")
                time.sleep(5)
        except Exception as e:
            print(f"Error polling for token: {e}")
            time.sleep(5)
