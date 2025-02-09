# 1. gewechat channel简介

Gewechat channel是基于[Gewechat](https://github.com/Devo919/Gewechat)项目实现的微信个人号通道，使用ipad协议登录，该协议能获取到wxid，能发送语音条消息，相比itchat协议更稳定。

api文档地址为：[gewechat api](https://apifox.com/apidoc/shared-69ba62ca-cb7d-437e-85e4-6f3d3df271b1/api-197179336)

首先可以简单了解 dify-on-wechat、dify、gewechat服务的调用关系，如下图所示

<div align="center">
<img width="700" src="./gewechat_service_design.png">
</div>


# 2. gewechat 服务部署教程

gewechat 服务需要自行部署，[dify-on-wechat](https://github.com/hanfangyuan4396/dify-on-wechat) 项目只负责对接gewechat服务，请参考下方教程部署gewechat服务。

## 2.1 下载镜像

感谢gewechat交流群中的大佬 `@1H` 重构了镜像,让gewe镜像不依赖cgroup和docker --privilege,可以在更高版本的ubuntu、debian以及macos系统上运行。

```bash
# 从阿里云镜像仓库拉取(国内)
docker pull registry.cn-chengdu.aliyuncs.com/tu1h/wechotd:alpine
docker tag registry.cn-chengdu.aliyuncs.com/tu1h/wechotd:alpine gewe

# 或从GitHub镜像仓库拉取
docker pull ghcr.io/tu1h/wechotd/wechotd:alpine
docker tag ghcr.io/tu1h/wechotd/wechotd:alpine gewe
```

## 2.2 使用docker启动

```bash
mkdir -p gewechat/data  
docker run -itd -v ./gewechat/data:/root/temp -p 2531:2531 -p 2532:2532 --restart=always --name=gewe gewe
```

## 2.3 使用docker compose启动

首先创建必要的数据目录:

```bash
mkdir -p gewechat/data
```

创建 `docker-compose.yml` 文件:

```yaml
version: '3'
services:
  gewechat:
    image: gewe
    container_name: gewe
    volumes:
      - ./gewechat/data:/root/temp
    ports:
      - "2531:2531"
      - "2532:2532"
    restart: always
```

运行:
```bash
docker compose up -d
```

## 2.4 成功日志

看到如下日志，表示gewechat服务启动成功

<div align="center">
<img width="700" src="./gewechat_service_success.jpg">
</div>

# 3. 使用dify-on-wechat对接gewechat服务

## 3.1 gewechat相关参数配置

在dify-on-wechat项目的`config.json`中需要配置以下gewechat相关的参数：

```bash
{
    "channel_type": "gewechat"   # 通道类型，请设置为gewechat    
    "gewechat_token": "",        # gewechat服务的token，用于接口认证
    "gewechat_app_id": "",       # gewechat服务的应用ID
    "gewechat_base_url": "http://本机ip:2531/v2/api",  # gewechat服务的API基础URL
    "gewechat_callback_url": "http://本机ip:9919/v2/api/callback/collect", # 回调URL，用于接收消息
    "gewechat_download_url": "http://本机ip:2532/download", # 文件下载URL
}
```

参数说明：
- `gewechat_token`: gewechat服务的认证token，首次登录时，可以留空，启动dify-on-wechat服务时，会**自动获取token**并**自动保存到config.json**中
- `gewechat_app_id`: gewechat服务分配的设备ID，首次登录时，可以留空，启动dify-on-wechat服务时，会**自动获取appid**并**自动保存到config.json**中
- `gewechat_base_url`: gewechat服务的API基础地址，请根据实际情况配置，如果gewechat服务与dify-on-wechat服务部署在同一台机器上，可以配置为`http://本机ip:2531/v2/api`
- `gewechat_callback_url`: 接收gewechat消息的回调地址，请根据实际情况配置，如果gewechat服务与dify-on-wechat服务部署在同一台机器上，可以配置为`http://本机ip:9919/v2/api/callback/collect`，如无特殊需要，请使用9919端口号
- `gewechat_download_url`: 文件下载地址，用于下载语音、图片等文件，请根据实际部署情况配置，如果gewechat服务与dify-on-wechat服务部署在同一台机器上，可以配置为`http://本机ip:2532/download`

> 请确保您的回调地址(callback_url)，即dify-on-wechat启动的回调服务可以被gewechat服务正常访问到。如果您使用Docker部署，需要注意网络配置，确保容器之间可以正常通信。
> 
> 本机ip是指**局域网ip**或**公网ip**，可通过`ipconfig`或`ifconfig`命令查看
> 
> 对与gewechat_callback_url，ip不能填`127.0.0.1`或`localhost`，否则会报错
> 
> `9919`端口是dify-on-wechat服务监听的端口，如果是用docker启动的dify-on-wechat服务,请把`9919`端口映射到宿主机

## 3.2 dify相关参数配置

在dify-on-wechat项目的`config.json`中需要配置以下dify相关参数：

```bash
{
  "dify_api_base": "https://api.dify.ai/v1",    # dify base url
  "dify_api_key": "app-xxx",                    # dify api key
  "dify_app_type": "chatbot",                   # dify应用类型,对应聊天助手
  "channel_type": "gewechat",                   # 通道类型设置为gewechat
  "model": "dify",                              # 模型名称设置为dify
  "single_chat_prefix": [""],                   # 私聊触发前缀
  "single_chat_reply_prefix": "",               # 私聊回复前缀
  "group_chat_prefix": ["@bot"],                # 群聊触发前缀
  "group_name_white_list": ["ALL_GROUP"],       # 允许响应的群组
}
```

关于dify_api_base、dify_api_key等参数的获取方法,请参考文章 [手摸手教你把 Dify 接入微信生态](https://docs.dify.ai/v/zh-hans/learn-more/use-cases/dify-on-wechat)。

## 3.3 启动dify-on-wechat服务

完成上述配置后，你需要确保gewechat服务已正常启动，dify-on-wechat的依赖已安装(见 [dify-on-wechat项目README](https://github.com/hanfangyuan4396/dify-on-wechat) 或 [手摸手教你把 Dify 接入微信生态](https://docs.dify.ai/v/zh-hans/learn-more/use-cases/dify-on-wechat) )，然后运行以下命令启动服务:

```bash
python app.py
```
启动成功后，可以看到如下日志信息，注意token和appid会自动保存到config.json，无需手动保存

<div align="center">
<img width="700" src="./gewechat_login.jpg">
</div>
⚠️如果遇到gewechat创建设备失败，unexpected EOF错误，请排查网络是否是以下情况：

1️⃣代理：请关闭代理后尝试；

2️⃣国外服务器：请更换为国内服务器；

3️⃣回调地址为外网：请更换为内网地址；

4️⃣异地服务器：请更换为同省服务器；

## 3.4 利用gewechat发送语音条消息

语音相关配置如下，另外需要在dify应用中开启语音转文字以及文字转语音功能，注意语音功能需要**安装ffmpeg依赖**，如使用docker部署dify，已集成ffmpeg依赖，无需额外安装。

```bash
{
  "dify_api_base": "https://api.dify.ai/v1",
  "dify_api_key": "app-xxx",
  "dify_app_type": "chatbot",
  "channel_type": "gewechat",  # 通道类型设置为gewechat
  "model": "dify",    
  "speech_recognition": true,  # 是否开启语音识别
  "voice_reply_voice": true,   # 是否使用语音回复语音
  "always_reply_voice": false, # 是否一直使用语音回复
  "voice_to_text": "dify",     # 语音识别引擎
  "text_to_voice": "dify"      # 语音合成引擎
}
```

gewechat支持**发送语音条消息**，但是gewechat服务只能获取到**20s**以内的语音，所以**你只能给bot发送20s以内的语音**，而**bot给你发送语音时无此限制**。

<div align="center">
<img width="700" src="./gewechat_voice.jpg">
</div>



# 4. gewechat_channel 服务的限制
1. gewechat 要求必须搭建服务到**同省**服务器或者电脑里方可正常使用，即登录微信的手机与gewechat服务必须在同一省
2. gewechat 开源框架**只支持**下载接收到的图片，不支持下载文件
3. gewechat_channel 目前暂时**只支持接收文字消息**，**只支持发送文字消息与图片消息**，后续支持的消息类型会逐步完善
4. 此项目仅用于个人娱乐场景，请**勿用于任何商业场景**
