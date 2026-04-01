import os
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
# gRPC 只需要 IP:端口，自动去除 http://
CD2_HOST = os.getenv("CD2_HOST", "192.168.1.10:19798").replace("http://", "").replace("https://", "")
CD2_TOKEN = os.getenv("CD2_TOKEN")
DOWNLOAD_PATH = os.getenv("DOWNLOAD_PATH")

crypto = WeChatCrypto(APP_TOKEN, ENCODING_AES_KEY, CORP_ID)

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

def cd2_offline_download(magnet_url):
    """使用 gRPC 调用 CloudDrive2 添加离线下载"""
    if not CD2_TOKEN:
        return False, "未配置 CD2_TOKEN"
        
    try:
        # 建立 gRPC 连接
        channel = grpc.insecure_channel(CD2_HOST)
        stub = clouddrive_pb2_grpc.CloudDriveFileSrvStub(channel)
        
        # 植入 JWT Token 认证头
        metadata = [('authorization', f'Bearer {CD2_TOKEN}')]
        
        # 构造添加离线任务的请求 (基于官方文档)
        req = clouddrive_pb2.AddOfflineFileRequest(
            urls=magnet_url,
            toFolder=DOWNLOAD_PATH,
            checkFolderAfterSecs=0
        )
        
        # 发送请求
        res = stub.AddOfflineFiles(req, metadata=metadata, timeout=10)
        
        if res.success:
            return True, "提交成功"
        else:
            return False, f"被拒: {res.errorMessage}"
            
    except grpc.RpcError as e:
        return False, f"gRPC错误: {e.code().name}"
    except Exception as e:
        return False, f"系统异常: {str(e)}"

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
            msg_type = tree.find('MsgType').text
            from_user = tree.find('FromUserName').text
            
            if msg_type == 'text':
                content = tree.find('Content').text.strip()
                if content.startswith("magnet:?"):
                    # 提交 gRPC 任务
                    success, detail = cd2_offline_download(content)
                    
                    hash_code = content.split("urn:btih:")[1][:10].upper() + "..." if "urn:btih:" in content else "未知特征码"
                    if success:
                        reply_text = f"✅ 离线任务已建立\n🧲 {hash_code}\n🤖 状态: {detail}"
                    else:
                        reply_text = f"❌ 离线任务失败\n⚠️ 原因: {detail}"
                    
                    send_wechat_reply(from_user, reply_text)
                else:
                    send_wechat_reply(from_user, "💡 请发送合法的磁力链接 (magnet:?)")
            return "success"
        except Exception as e:
            print(f"[*] 处理异常: {e}")
            return "success"

if __name__ == '__main__':
    print("[*] 机器人(gRPC协议版)已启动，监听 5000 端口...")
    app.run(host='0.0.0.0', port=5000)
