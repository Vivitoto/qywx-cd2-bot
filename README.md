# 🤖 企业微信 CD2 离线下载机器人 (qywx-cd2-bot)

基于 Python + Flask + gRPC 构建、使用 Gunicorn 运行的企业微信机器人。将你的企业微信打造成一个**直链 / 磁链 / ED2K 离线下载中枢**，把消息直接推送到本地的 CloudDrive2 进行离线下载。

> 本项目 fork 自 [jiumian8/jiumian-cd2-bot](https://github.com/jiumian8/jiumian-cd2-bot)，在此基础上重构为**YAML 路由化下载目录**，并新增了**按路由独立日期归档**、**自定义离线子目录**、**ED2K 支持**、**多链接批量提交**等功能。

## ✨ 核心功能 (Features)

* 🧲 **直链解析：** 直接发送磁力链接 (`magnet:?`)、种子下载链接 (`http://...*.torrent`)、ED2K 链接 (`ed2k://...`) 或 40 位 info_hash，秒推 CD2 离线下载。
* ⚡ **底层通信：** 彻底抛弃低效的网页模拟，采用官方标准的 **gRPC 协议 + JWT Token** 与 CloudDrive2 通信，极速且稳定。
* 🛡️ **防重防抖：** 内置异步线程与消息去重机制，完美绕过企业微信服务器“5秒内无响应自动重试三次”的变态机制。
* 📅 **日期归档（新增）：** 每个下载路由可独立决定是否在目标路径末尾创建 `YYYY-MM-DD` 日期目录。
* 🗂️ **YAML 路由配置：** 下载目录改为 `download-routes.yml` 管理，可自由定义 `/main`、`/sub`、`/temp` 等任意路由。
* 🪄 **自定义子目录：** 支持 `/sub @你好 磁链/哈希` 这种格式，自动落到类似 `/115open/手动转存/@你好/2026-04-22` 的路径，不存在则自动创建。
* 📦 **批量提交：** 支持一条消息中提交多个 magnet / ed2k / 直链，统一落到同一路径。
* 🧹 **清洗过滤（新增）：** 支持按文件后缀 + 体积阈值过滤垃圾文件，ed2k / magnet 链接可在提交前自动识别并跳过。
* 🔄 **中转清洗（新增）：** 磁力链接先下载到中转目录，完成后根据真实文件自动清洗并转存到目标目录（ed2k 直接提交，不走中转）。

---

## 📦 准备工作 (Prerequisites)

在开始部署之前，你需要准备好以下基础设施：

1.  **企业微信管理员权限：** 需要创建一个【自建应用】，并获取 `CORP_ID`, `APP_SECRET`, `AGENT_ID`, `APP_TOKEN`, `ENCODING_AES_KEY`。
2.  **企业微信 API 反向代理：** 企微新规要求回调地址必须有固定 IP。你需要一台拥有公网固定 IP 的服务器搭建反代（如 Nginx），代理目标为 `https://qyapi.weixin.qq.com`。
3.  **CloudDrive2：** 运行在本地 NAS/PVE 上。需在后台生成 **API 令牌 (Token)**。

---

## 🚀 部署指南 (Deployment)

推荐使用 Docker Compose 进行部署。

### 🛠️ 部署：qywx-cd2-bot

#### 1. 创建 `docker-compose.yml`

新建一个目录，创建并编辑 `docker-compose.yml` 文件：

```yaml
version: '3.8'

services:
  qywx-cd2-bot:
    image: vivitoto/qywx-cd2-bot:latest
    container_name: qywx-cd2-bot
    restart: unless-stopped
    ports:
      - "5110:5000"  # 左侧可以改为你想要暴露的外部端口
    environment:
      # --- 企业微信凭证 ---
      - CORP_ID=企业ID
      - APP_SECRET=你的自建应用Secret
      - AGENT_ID=应用id
      - APP_TOKEN=你的接收消息Token
      - ENCODING_AES_KEY=你的43位消息加解密Key

      # --- 企业微信 API 代理 ---
      - WECHAT_PROXY=http://你的反向代理IP:端口

      # --- CloudDrive2 配置 ---
      - CD2_HOST=192.168.x.x:19798            # CD2 的内网 IP 和端口，不要带 http://
      - CD2_TOKEN=你的CD2_API令牌              # token 权限至少要给离线下载（建议网盘权限全开）

      # --- 清洗过滤配置（可选） ---
      - ENABLE_CLEANUP=false                    # true=开启清洗过滤
      - JUNK_EXTENSIONS=txt,url,html,mhtml,htm,mht,mp4,exe,rar,apk,gif,png,jpg  # 垃圾后缀黑名单
      - JUNK_SIZE_THRESHOLD_MB=                 # 体积阈值（MB），留空则不执行清洗

      # --- 中转清洗配置（可选） ---
      # 中转目录在 download-routes.yml 里配置 staging_folder
      # 磁力链接先下载到中转目录，完成后自动清洗并转存到目标目录
      # ed2k 直接提交，不走中转
      # 不配置则保持原有直接提交模式
    volumes:
      - ./config:/config
```

> 💡 **首次启动说明**
> - 容器首次启动时，若 `/config/download-routes.yml` 不存在，会自动从镜像里的示例文件初始化一份。
> - 初始化后可直接编辑宿主机上的 `./config/download-routes.yml`。
> - 修改路由配置后，重启容器即可生效。
> - 容器现在使用 **Gunicorn** 启动，不再出现 Flask development server 的那条警告。

> 💡 **清洗过滤说明**
> - `ENABLE_CLEANUP=true` 时开启清洗。
> - 支持 **ed2k** 和 **磁力链接（magnet 带 dn/xl 参数）** 的文件名 + 体积解析。
> - **同时满足**“后缀在黑名单”且“体积 < 阈值”时，该文件会被跳过，不提交离线任务。
> - 被清洗的文件会在企微通知里列出。
> - 如果仅配置了后缀黑名单、没配置体积阈值，则**只按后缀清洗**（不管大小）。
> - 示例：
>   - `JUNK_EXTENSIONS=txt`
>   - `JUNK_SIZE_THRESHOLD_MB=50`
>   - 则体积 < 50MB 的 txt 文件会被过滤掉。

> 💡 **中转清洗说明**
> - 在 `download-routes.yml` 里配置全局 `staging_folder: /115open/staging` 时启用中转清洗。
> - **仅磁力链接**走中转，ed2k 直接提交到目标目录。
> - 磁力链接先提交到中转目录下载，完成后根据**真实文件名和体积**自动清洗。
> - 清洗规则：后缀在黑名单 AND 体积 < 阈值 → 删除垃圾文件（两个条件必须同时满足）。
> - 保留的文件自动 `MoveFile` 到目标目录，垃圾文件自动 `DeleteFile` 删除。
> - 移动和删除操作逐个文件执行，每两个操作之间间隔 **5 秒**。
> - 提交后企微回复：`📦 已提交到中转目录... ⏳ 下载完成后自动清洗...`
> - 完成后企微回复：`✅ 中转任务完成 📦 保留文件 X 个`（如有清洗则追加 `🧹 已清洗垃圾文件 Y 个`）
> - **不配置 `staging_folder`** 时磁力链接直接提交，ed2k 也直接提交。

#### 1.1 `download-routes.yml`

> 转存路径路由配置统一在 `./config/download-routes.yml` 里修改。
> 容器首次启动时会自动生成这个文件。
> 命令使用方式写在本文后面的“使用说明”里，YAML 文件里只保留必要字段和简要备注。  

#### 2. 配置企业微信回调

前往企业微信后台 -> 应用管理 -> 你的应用 -> 接收消息 -> 设置 API 接收。

- **URL:** 若按上面的端口映射 `5110:5000` 部署，则填写 `http://你的公网穿透域名或IP:5110/wechat` **(注意结尾必须带 `/wechat`)**
- **Token / EncodingAESKey:** 与 docker-compose 中的配置保持一致。

点击保存，提示成功即可！

---

## 💡 使用说明 (Usage)

直接在微信中找到你的自建应用机器人，发送消息即可交互：

### 场景 1：直接下载

发送：`magnet:?xt=urn:btih:XXXXXX`

回复：✅ 离线任务建立成功 → `/115open/磁力/2026-04-22`

### 场景 1.1：直接发送 ed2k

发送：`ed2k://|file|demo.mkv|123456|ABCDEF1234567890ABCDEF1234567890|/`

回复：✅ 离线任务建立成功 → `/115open/磁力/2026-04-22`

### 场景 2：离线到 sub 目录

发送：`/sub E808151805F0A2C8C281FBEFA682AD29EDA73FF2`

回复：✅ 离线任务建立成功！→ `/115open/手动转存/2026-04-22`

### 场景 3：离线到自定义子目录

发送：`/sub @你好 E808151805F0A2C8C281FBEFA682AD29EDA73FF2`

回复：✅ 离线任务建立成功！→ `/115open/手动转存/@你好/2026-04-22`

### 场景 4：一次提交多个 magnet / ed2k

发送：
```text
/sub @你好
ed2k://|file|a.mkv|111|HASH1|/
magnet:?xt=urn:btih:E808151805F0A2C8C281FBEFA682AD29EDA73FF2
```

回复：✅ 离线任务建立成功，统一落到 `/115open/手动转存/@你好/2026-04-22`

### 场景 5：清洗过滤示例

环境变量配置：
```
ENABLE_CLEANUP=true
JUNK_EXTENSIONS=txt,url
JUNK_SIZE_THRESHOLD_MB=50
```

发送：
```text
/sub @你好
ed2k://|file|test.txt|1000000|ABCDEF1234567890ABCDEF1234567890|/
ed2k://|file|movie.mkv|60000000|ABCDEF1234567890ABCDEF1234567890|/
```

回复：
> ✅ 离线任务建立成功
> 📦 提交数量: 1
> 🧹 已过滤垃圾文件: 1 个
>   - test.txt (txt, 1.0MB < 50.0MB)
> 🤖 状态: 提交成功 → /115open/手动转存/@你好

