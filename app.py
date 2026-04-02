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

def process_message_async(from_user, content):
    """后台异步处理线程"""
    target_magnet = None
    is_search = False
    
    if content.startswith("magnet:?"):
        target_magnet = content
    elif len(content) > 3: 
        send_wechat_reply(from_user, f"🔍 正在本地索引库搜索【{content}】...")
        is_search = True
        target_magnet = search_magnet(content)
    
    if target_magnet:
        success, detail = cd2_offline_download(target_magnet)
        hash_code = target_magnet.split("urn:btih:")[1][:10].upper() + "..." if "urn:btih:" in target_magnet else "未知特征码"
        
        if success:
            prefix = "✅ 本地检索并离线成功" if is_search else "✅ 离线任务已建立"
            reply_text = f"{prefix}\n🧲 {hash_code}\n🤖 状态: {detail}"
        else:
            reply_text = f"❌ 离线任务失败\n⚠️ 原因: {detail}"
            
        send_wechat_reply(from_user, reply_text)
    else:
        if is_search:
            send_wechat_reply(from_user, f"😭 抱歉，本地索引库未能找到【{content}】。建议在 Prowlarr 添加更多 Indexer。")
        else:
            send_wechat_reply(from_user, "💡 请发送合法的磁力链接或番号关键词。")

@app.route('/wechat', methods=['GET', 'POST'])
def wechat_callback():
    signature = request.args.get('msg_signature', '')
    timestamp = request.args.get('timestamp', '')
    nonce = request.args.get('nonce', '')

    if request.method == 'GET':
        echostr = request.args.get('echostr', '')
        try:
            return crypto.check_signature(signature, timestamp, nonce, echostr)
        except Exception as e:
            return f"验证失败: {e}", 403

    if request.method == 'POST':
        try:
            msg_xml = crypto.decrypt_message(request.data, signature, timestamp, nonce)
            tree = ET.fromstring(msg_xml)
            
            # --- 消息防重复逻辑 ---
            msg_id_node = tree.find('MsgId')
            if msg_id_node is not None:
                msg_id = msg_id_node.text
                if msg_id in recent_msg_ids:
                    return "success"
                recent_msg_ids.append(msg_id)
                if len(recent_msg_ids) > 100:
                    recent_msg_ids.pop(0)
            
            msg_type = tree.find('MsgType').text
            from_user = tree.find('FromUserName').text
            
            if msg_type == 'text':
                content = tree.find('Content').text.strip()
                # 开启新线程去处理业务，主线程秒回微信防止重复提醒
                threading.Thread(target=process_message
