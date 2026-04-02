import os
import re
import threading
import xml.etree.ElementTree as ET
import requests
from flask import Flask, request
from wechatpy.enterprise.crypto import WeChatCrypto
import grpc
import clouddrive_pb2
import clouddrive_pb2_grpc

app = Flask(__name__)

# --- 1. 企微配置 ---
CORP_ID = os.getenv("CORP_ID")
APP_SECRET = os.getenv("APP_SECRET")
AGENT_ID = os.getenv("AGENT_ID")
APP_TOKEN = os.getenv("APP_TOKEN")
ENCODING_AES_KEY = os.getenv("ENCODING_AES_KEY")
WECHAT_PROXY = os.getenv("WECHAT_PROXY", "https://qyapi.weixin.qq.com").rstrip("/")

# --- 2. CD2 gRPC 配置 ---
CD2_HOST = os.getenv("CD2_HOST", "192.168.1.10:19798").replace("http://", "").replace("https://", "")
CD2_TOKEN = os.getenv("CD2_TOKEN")
DOWNLOAD_PATH = os.getenv("DOWNLOAD_PATH")

# --- 3. Prowlarr 聚合搜索配置 ---
PROWLARR_URL = os.getenv("PROWLARR_URL", "http://192.168.1.10:9696").rstrip("/")
PROWLARR_API_KEY = os.getenv("PROWLARR_API_KEY")

crypto = WeChatCrypto(APP_TOKEN, ENCODING_AES_KEY, CORP_ID)

# 消息防重放缓存
recent_msg_ids = []

def send_wechat_reply(touser, content):
    """通过微信代理发回信"""
    try:
        token_url = f"{WECHAT_PROXY}/cgi-bin/gettoken?corpid={CORP_ID}&corpsecret={APP_SECRET}"
        token_res = requests.get(token_url, timeout=10).json()
        access_token = token_res.get("access_token")
        if not access_token: return
        
        send_url = f"{WECHAT_PROXY}/cgi-bin/message/send?access_token={access_token}"
        payload = {
            "touser": touser,
            "msgtype": "text",
            "agentid": AGENT_ID,
            "text": {"content": content}
        }
        requests.post(send_url, json=payload, timeout=10)
    except Exception as e:
        print(f"[*] 微信回复失败: {e}")

def search_magnet(keyword):
    """通过本地的 Prowlarr API 聚合搜索磁力链接"""
    if not PROWLARR_API_KEY:
        print("[*] 未配置 PROWLARR_API_KEY")
        return None
        
    try:
        url = f"{PROWLARR_URL}/api/v1/search"
        headers = {"X-Api-Key": PROWLARR_API_KEY}
        params = {"query": keyword, "type": "search"}
        
        res = requests.get(url, headers=headers, params=params, timeout=20)
        res.raise_for_status()
        results = res.json()
        
        valid_results = []
        for item in results:
            magnet = item.get("magnetUrl") or item.get("downloadUrl")
            if not magnet and str(item.get("guid")).startswith("magnet:"):
                magnet = item.get("guid")
                
            if magnet and magnet.startswith("magnet:"):
                valid_results.append({
                    "magnet": magnet,
                    "seeders": item.get("seeders", 0),
                    "indexer": item.get("indexer", "未知站")
                })
        
        if valid_results:
            valid_results.sort(key=lambda x: x["seeders"], reverse=True)
            best_choice = valid_results[0]
            print(f"[*] 找到资源，来自: {best_choice['indexer']}，做种数: {best_choice['seeders']}")
            return best_choice["magnet"]
            
        return None
    except Exception as e:
        print(f"[*] Prowlarr 搜索异常: {e}")
        return None

def cd2_offline_download(magnet_url):
    """使用 gRPC 调用 CloudDrive2 添加离线下载"""
    if not CD2_TOKEN: return False, "未配置 CD2_TOKEN"
    try:
        channel = grpc.insecure_channel(CD2_HOST)
        stub = clouddrive_pb2_grpc.CloudDriveFileSrvStub(channel)
        metadata = [('authorization', f'Bearer {CD2_TOKEN}')]
        req = clouddrive_pb2.AddOfflineFileRequest(
            urls=magnet_url,
            toFolder=DOWNLOAD_PATH,
            checkFolderAfterSecs=0
        )
        res = stub.AddOfflineFiles(req, metadata=metadata, timeout=10)
        return (True, "提交成功") if res.success else (False, f"被拒: {res.errorMessage}")
    except grpc.RpcError as e:
        return False, f"gRPC错误: {e.code().name}"
    except Exception as e:
        return False, f"系统异常: {str(e)}"
