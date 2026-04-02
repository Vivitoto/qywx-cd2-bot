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

crypto = WeChatCrypto(APP_TOKEN, ENCODING_AES_KEY, CORP_ID)

# --- 3. 消息防重放缓存 ---
# 用于记录最近处理过的 MsgId，防止微信超时重试导致重复下载
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
    """搜索磁力链接"""
    search_url = f"https://bitsearch.to/search?q={keyword}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    try:
        res = requests.get(search_url, headers=headers, timeout=15)
        res.raise_for_status()
        magnets = re.findall(r'magnet:\?xt=urn:btih:[a-zA-Z0-9]{32,40}', res.text)
        return magnets[0] if magnets else None
    except Exception as e:
        print(f"[*] 搜索磁力异常: {e}")
        return None

def cd2_offline_download(magnet_url):
    """使用 gRPC 调用 CloudDrive2 添加离线下载"""
    if not CD2_TOKEN:
        return False, "未配置 CD2_TOKEN"
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
    """后台异步处理线程：负责耗时的搜索和下载动作"""
    target_magnet = None
    is_search = False
    
    # 1. 判断是直链还是需要搜索
    if content.startswith("magnet:?"):
        target_magnet = content
    elif len(content) > 3: 
        send_wechat_reply(from_user, f"🔍 正在全网搜索【{content}】，请稍候...")
        is_search = True
        target_magnet = search_magnet(content)
    
    # 2. 执行下载与结果通知
    if target_magnet:
        success, detail = cd2_offline_download(target_magnet)
        hash_code = target_magnet.split("urn:btih:")[1][:10].upper() + "..." if "urn:btih:" in target_magnet else "未知特征码"
        
        if success:
            prefix = "✅ 搜索并离线成功" if is_search else "✅ 离线任务已建立"
            reply_text = f"{prefix}\n🧲 {hash_code}\n🤖 状态: {detail}"
        else:
            reply_text = f"❌ 离线任务失败\n⚠️ 原因: {detail}"
            
        send_wechat_reply(from_user, reply_text)
        
    else:
        if is_search:
            send_wechat_reply(from_user, f"😭 抱歉，未能搜到关于【{content}】的磁力链接。")
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
            
            # --- 消息去重逻辑 ---
            msg_id_node = tree.find('MsgId')
            if msg_id_node is not None:
                msg_id = msg_id_node.text
                if msg_id in recent_msg_ids:
                    # 如果是处理过的重复消息，直接忽略并返回 success
                    return "success"
                
                # 记录新消息 ID，保持缓存列表最多存 100 条
                recent_msg_ids.append(msg_id)
                if len(recent_msg_ids) > 100:
                    recent_msg_ids.pop(0)
            
            # --- 解析并启动后台线程 ---
            msg_type = tree.find('MsgType').text
            from_user = tree.find('FromUserName').text
            
            if msg_type == 'text':
                content = tree.find('Content').text.strip()
                # 开启新线程去处理业务，让 Flask 主线程秒回微信
                threading.Thread(target=process_message_async, args=(from_user, content)).start()
                
            return "success"
        except Exception as e:
            print(f"[*] 处理异常: {e}")
            return "success"

if __name__ == '__main__':
    print("[*] 机器人(带自动搜索防重复版)已启动，监听 5000 端口...")
    app.run(host='0.0.0.0', port=5000)
