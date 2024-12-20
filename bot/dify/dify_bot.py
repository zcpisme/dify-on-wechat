# encoding:utf-8
import io
import os
import mimetypes
import random
import threading
import json
import re
import time
from threading import Timer, Lock
from datetime import datetime, timedelta
import pytz
from PIL import Image

import requests
from urllib.parse import urlparse, unquote

from bot.bot import Bot
from lib.dify.dify_client import DifyClient, ChatClient
from bot.dify.dify_session import DifySession, DifySessionManager
from bridge.context import ContextType, Context
from bridge.reply import Reply, ReplyType
from common.log import logger
from common import const, memory
from common.utils import parse_markdown_text
from common.tmp_dir import TmpDir
from config import conf

import time
from threading import Timer, Lock

class DifyBot(Bot):
    def __init__(self):
        super().__init__()
        self.sessions = DifySessionManager(DifySession, model=conf().get("model", const.DIFY))
        # 新增的属性
        self.pending_queries = {}  # 存储待处理的查询
        self.buffer_time = 15.0    # 缓冲时间(秒)
        self.query_lock = Lock()  # 全局锁
        self.MAX_QUERY_LENGTH = 1000  # 最大查询长度
        self.MAX_PENDING_USERS = 100  # 最大等待用户数
        self.MAX_AGE = 300  # 查询最大存活时间(秒)

        self.greeting_timers = {}  # 存储问候计时器
        self.greeting_lock = Lock()  # 问候计时器的锁
        self.MIN_GREETING_INTERVAL = 12 * 3600  # 5小时
        self.MAX_GREETING_INTERVAL = 14 * 3600  # 7小时
        self.beijing_tz = pytz.timezone('Asia/Shanghai')

        self.auto_greet_users_file = "auto_greet_users.json"  # 白名单文件路径
        self.last_whitelist_check = 0
        self.whitelist_check_interval = 60  # 检查白名单的间隔时间(秒)
        self.auto_greet_users = self._load_auto_greet_users()

    def _load_auto_greet_users(self):
        """动态加载自动问候用户白名单"""
        try:
            if os.path.exists(self.auto_greet_users_file):
                with open(self.auto_greet_users_file, 'r', encoding='utf-8') as f:
                    return set(json.load(f))
            return set()
        except Exception as e:
            logger.error(f"Error loading auto greet users: {e}")
            return set()

    def _should_update_whitelist(self):
        """检查是否需要更新白名单"""
        current_time = time.time()
        if current_time - self.last_whitelist_check >= self.whitelist_check_interval:
            self.auto_greet_users = self._load_auto_greet_users()
            self.last_whitelist_check = current_time
            return True
        return False

    def _is_user_in_whitelist(self, user_identifier):
        """检查用户是否在白名单中"""
        self._should_update_whitelist()
        return user_identifier in self.auto_greet_users
    
    def _is_greeting_time(self):
        """检查当前是否是合适的问候时间（上午或晚上）"""
        now = datetime.now(self.beijing_tz)
        hour = now.hour
        # 上午 8-12 点或晚上 7-10 点
        return (8 <= hour < 12) or (19 <= hour < 22)

    def _schedule_next_greeting(self, session_id, context):
        """安排下一次问候"""
        try:
            # 获取用户标识符
            user_identifier = self._get_user_identifier(context)
            
            # 检查用户是否在白名单中
            if not self._is_user_in_whitelist(user_identifier):
                logger.info(f"User {user_identifier} not in auto greet whitelist, skipping greeting")
                return
            
            # 生成5-7小时之间的随机间隔时间
            random_interval = random.uniform(self.MIN_GREETING_INTERVAL, self.MAX_GREETING_INTERVAL)
            
            # 计算下一个合适的问候时间
            now = datetime.now(self.beijing_tz)
            next_time = now + timedelta(seconds=random_interval)
            
            # 如果下一次问候时间不在合适的时间段，调整到下一个合适的时间
            if not self._is_greeting_time():
                if next_time.hour < 8:
                    # 调整到当天上午8点
                    next_time = next_time.replace(hour=8, minute=0, second=0)
                elif 12 <= next_time.hour < 19:
                    # 调整到当天晚上7点
                    next_time = next_time.replace(hour=19, minute=0, second=0)
                elif next_time.hour >= 22:
                    # 调整到第二天上午8点
                    next_time = (next_time + timedelta(days=1)).replace(hour=8, minute=0, second=0)

            # 计算延迟秒数
            delay = (next_time - now).total_seconds()
            if delay < 0:
                delay = self.GREETING_INTERVAL

            with self.greeting_lock:
                # 取消现有的计时器（如果存在）
                if session_id in self.greeting_timers and self.greeting_timers[session_id]:
                    self.greeting_timers[session_id].cancel()
                
                # 创建新的计时器
                timer = Timer(delay, self._send_greeting, args=[session_id, context])
                timer.start()
                self.greeting_timers[session_id] = timer
                
                logger.info(f"[DIFY] Scheduled next greeting for session {session_id} in {delay:.2f} sec")
        except Exception as e:
            logger.error(f"Error scheduling next greeting: {e}")

    def _send_greeting(self, session_id, context):
        """发送问候消息"""
        try:
            user_identifier = self._get_user_identifier(context)
            
            # 再次检查用户是否在白名单中
            if not self._is_user_in_whitelist(user_identifier):
                logger.info(f"User {user_identifier} no longer in whitelist, cancelling greeting")
                return
            
            if self._is_greeting_time():
                # 创建一个新的问候查询
                greeting_query = "[系统招呼]随便打声招呼"
                logger.info(f"[DIFY] Sending greeting to session {session_id}")
                
                # 获取session并处理查询
                session = self.sessions.get_session(session_id, self._get_user_identifier(context))
                reply, err = self._reply(greeting_query, session, context)
                
                if err is not None:
                    logger.error(f"Error sending greeting: {err}")
                elif context.get("channel"):
                    if isinstance(reply, list):
                        for r in reply:
                            context["channel"].send(r, context)
                    else:
                        context["channel"].send(reply, context)
            
            # 安排下一次问候
            self._schedule_next_greeting(session_id, context)
        except Exception as e:
            logger.error(f"Error in send_greeting: {e}")

    def reply(self, query, context: Context=None):
        """处理用户查询的主要方法"""
        # 处理非文本类型的消息
        if context.type not in [ContextType.TEXT, ContextType.IMAGE_CREATE]:
            return Reply(ReplyType.ERROR, "Bot不支持处理{}类型的消息".format(context.type))

        if context.type == ContextType.IMAGE_CREATE:
            query = conf().get('image_create_prefix', ['画'])[0] + query

        session_id = context["session_id"]
        logger.info("[DIFY] query={}".format(query))

        with self.greeting_lock:
            if session_id in self.greeting_timers and self.greeting_timers[session_id]:
                self.greeting_timers[session_id].cancel()

        # 清理过期查询
        self.cleanup_old_queries()

        with self.query_lock:
            # 检查是否达到最大等待用户数
            if len(self.pending_queries) >= self.MAX_PENDING_USERS and session_id not in self.pending_queries:
                session = self.sessions.get_session(session_id, self._get_user_identifier(context))
                return self._reply(query, session, context)

            if session_id in self.pending_queries:
                with self.pending_queries[session_id]['lock']:
                    # 检查合并后的查询长度
                    total_length = len(self.pending_queries[session_id]['query']) + len(query)
                    if total_length > self.MAX_QUERY_LENGTH:
                        # 处理当前缓存的查询
                        self._process_buffered_query(session_id, context)
                        # 创建新的查询缓存
                        return self._create_new_query(query, session_id, context)

                    # 取消之前的定时器
                    self.pending_queries[session_id]['timer'].cancel()
                    # 合并查询
                    self.pending_queries[session_id]['query'] += "\n" + query
                    # 更新最后活动时间
                    self.pending_queries[session_id]['last_active'] = time.time()
                    # 创建新定时器
                    timer = Timer(self.buffer_time, self._process_buffered_query, args=[session_id, context])
                    timer.start()
                    self.pending_queries[session_id]['timer'] = timer
            else:
                # 创建新的待处理查询
                self._create_new_query(query, session_id, context)

        return None

    def _create_new_query(self, query, session_id, context):
        """创建新的查询缓存"""
        logger.info(f"[DIFY] Creating new query buffer for session {session_id}")
        timer = Timer(self.buffer_time, self._process_buffered_query, args=[session_id, context])
        self.pending_queries[session_id] = {
            'query': query,
            'timer': timer,
            'lock': Lock(),
            'created_at': time.time(),
            'last_active': time.time()
        }
        timer.start()
        return None

    def _process_buffered_query(self, session_id, context):
        try:
            with self.query_lock:
                if session_id not in self.pending_queries:
                    return None
                
                with self.pending_queries[session_id]['lock']:
                    query = self.pending_queries[session_id]['query']
                    logger.info(f"[DIFY] Processing buffered query for session {session_id}, merged query length: {len(query)}")
                    # 清理缓存
                    self.pending_queries[session_id]['timer'].cancel()
                    del self.pending_queries[session_id]

            session = self.sessions.get_session(session_id, self._get_user_identifier(context))
            if context.get("isgroup", False):
                # 群聊：根据是否是共享会话群来决定是否设置用户信息
                if not context.get("is_shared_session_group", False):
                    # 非共享会话群：设置发送者信息
                    session.set_user_info(context["msg"].actual_user_id, context["msg"].actual_user_nickname)
                else:
                    # 共享会话群：不设置用户信息
                    session.set_user_info('', '')
                # 设置群聊信息
                session.set_room_info(context["msg"].other_user_id, context["msg"].other_user_nickname)
            else:
                # 私聊：使用发送者信息作为用户信息，房间信息留空
                session.set_user_info(context["msg"].other_user_id, context["msg"].other_user_nickname)
                session.set_room_info('', '')

            # 打印设置的session信息
            logger.debug(f"[DIFY] Session user and room info - user_id: {session.get_user_id()}, user_name: {session.get_user_name()}, room_id: {session.get_room_id()}, room_name: {session.get_room_name()}")
            logger.debug(f"[DIFY] session={session} query={query}")
            reply, err = self._reply(query, session, context)
            
            if err is not None:
                error_msg = conf().get("error_reply", "我暂时遇到了一些问题，请您稍后重试~")
                reply = Reply(ReplyType.TEXT, error_msg)

            if context.get("channel"):
                if isinstance(reply, list):
                    for r in reply:
                        context["channel"].send(r, context)
                else:
                    context["channel"].send(reply, context)

            # 用户输入处理完成后，等待buffer_time后再安排问候
            timer = Timer(self.buffer_time, lambda: self._schedule_next_greeting(session_id, context))
            timer.start()
            logger.info(f"[DIFY] 将在 {self.buffer_time} 秒后安排问候计时器 {session_id}")
            return reply
        except Exception as e:
            logger.error(f"Error processing buffered query: {e}")
            return None
        
    def cleanup_old_queries(self):
        """清理过期的查询和问候计时器"""
        current_time = time.time()
        with self.query_lock:
            for session_id in list(self.pending_queries.keys()):
                try:
                    with self.pending_queries[session_id]['lock']:
                        if current_time - self.pending_queries[session_id]['last_active'] > self.MAX_AGE:
                            self.pending_queries[session_id]['timer'].cancel()
                            # 同时清理对应的问候计时器
                            with self.greeting_lock:
                                if session_id in self.greeting_timers:
                                    self.greeting_timers[session_id].cancel()
                                    del self.greeting_timers[session_id]
                            del self.pending_queries[session_id]
                except Exception as e:
                    logger.error(f"Error cleaning up query for session {session_id}: {e}")

    def _get_user_identifier(self, context):
        """获取用户标识符"""
        channel_type = conf().get("channel_type", "wx")
        
        if channel_type in ["wx", "wework", "gewechat"]:
            return (context["msg"].other_user_remarkname or context["msg"].other_user_nickname) if context.get("msg") else "default"
        elif channel_type in ["wechatcom_app", "wechatmp", "wechatmp_service", "wechatcom_service", "web"]:
            return context["msg"].other_user_id if context.get("msg") else "default"
        else:
            logger.warning(f"Unsupported channel type: {channel_type}")
            return "default"

    # TODO: delete this function
    def _get_payload(self, query, session: DifySession, response_mode):
        return {
            'inputs': {},
            "query": query,
            "response_mode": response_mode,
            "conversation_id": session.get_conversation_id(),
            "user": session.get_user()
        }

    def _get_dify_conf(self, context: Context, key, default=None):
        return context.get(key, conf().get(key, default))

    def _reply(self, query: str, session: DifySession, context: Context):
        try:
            session.count_user_message() # 限制一个conversation中消息数，防止conversation过长
            dify_app_type = self._get_dify_conf(context, "dify_app_type", 'chatbot')
            if dify_app_type == 'chatbot':
                return self._handle_chatbot(query, session, context)
            elif dify_app_type == 'agent':
                return self._handle_agent(query, session, context)
            elif dify_app_type == 'workflow':
                return self._handle_workflow(query, session, context)
            else:
                return None, "dify_app_type must be agent, chatbot or workflow"

        except Exception as e:
            error_info = f"[DIFY] Exception: {e}"
            logger.exception(error_info)
            return None, error_info


    def _dealwithStream(self, response):
        complete_answer = ""

        # Process the streaming response
        for line in response.iter_lines():
            if line:
                decoded_line = line.decode('utf-8')
                # Check if the line contains "data:" which holds the actual message
                if decoded_line.startswith("data: "):
                    try:
                        # Parse the JSON content in the "data" field
                        rsp_data = json.loads(decoded_line[6:])
                        # Check if "answer" field exists and append it to the sentence
                        if "answer" in rsp_data and rsp_data["answer"].strip():
                            complete_answer += rsp_data["answer"]
                    except json.JSONDecodeError:
                        # In case there's a parsing error, skip to the next line
                        continue
        return rsp_data, complete_answer
    
    def split_sentence(self, sentence):
        # Using regex to split by period, question mark, or exclamation mark, keeping the delimiter
        sentences = re.split(r'(?<=[。？！? ! ])', sentence)
        # Remove any empty strings from the result
        sentences = [s for s in sentences if s]
        
        # If fewer than three sentences, return as is and pad with empty strings
        if len(sentences) < 4:
            return sentences
        
        # If more than three, attempt to split evenly
        total_length = sum(len(s) for s in sentences)
        avg_length = total_length // 4  # Target length per part
        
        result = []
        temp = ""
        for s in sentences:
            if len(temp) + len(s) <= avg_length or len(result) >= 3:
                temp += s  # Accumulate current segment
            else:
                result.append(temp)  # Add to results when segment reaches desired length
                temp = s  # Reset for new segment
        result.append(temp)  # Append last segment

        return result
    
    def _handle_chatbot(self, query: str, session: DifySession, context: Context):
        api_key = self._get_dify_conf(context, "dify_api_key", '')
        api_base = self._get_dify_conf(context, "dify_api_base", "https://api.dify.ai/v1")
        chat_client = ChatClient(api_key, api_base)
        response_mode = 'blocking'
        payload = self._get_payload(query, session, response_mode)
        files = self._get_upload_files(session, context)
        response = chat_client.create_chat_message(
            inputs=payload['inputs'],
            query=payload['query'],
            user=payload['user'],
            response_mode=payload['response_mode'],
            conversation_id=payload['conversation_id'],
            files=files
        )

        if response.status_code != 200:
            error_info = f"[DIFY] payload={payload} response text={response.text} status_code={response.status_code}"
            logger.warn(error_info)
            return None, error_info

        # response(blocking):
        # {
        #     "event": "message",
        #     "message_id": "9da23599-e713-473b-982c-4328d4f5c78a",
        #     "conversation_id": "45701982-8118-4bc5-8e9b-64562b4555f2",
        #     "mode": "chat",
        #     "answer": "xxx",
        #     "metadata": {
        #         "usage": {
        #         },
        #         "retriever_resources": []
        #     },
        #     "created_at": 1705407629
        # }

        # response(streaming):
        #
        #event: ping
        # data: {"event": "message", "conversation_id": "fe134579-f2aa-4ceb-8090-f974bc32c1af", "message_id": "417da858-2a76-4cbb-8fd1-e277dc5cb0e5", "created_at": 1729611314, "task_id": "2eddc385-6a25-48ca-8ee7-69cae3a788f2", "id": "417da858-2a76-4cbb-8fd1-e277dc5cb0e5", "answer": " ", "from_variable_selector": null}
        # data: {"event": "message", "conversation_id": "fe134579-f2aa-4ceb-8090-f974bc32c1af", "message_id": "417da858-2a76-4cbb-8fd1-e277dc5cb0e5", "created_at": 1729611314, "task_id": "2eddc385-6a25-48ca-8ee7-69cae3a788f2", "id": "417da858-2a76-4cbb-8fd1-e277dc5cb0e5", "answer": "H", "from_variable_selector": null}
        if response_mode == 'blocking':
            rsp_data = response.json()
            logger.debug("[DIFY] usage {}".format(rsp_data.get('metadata', {}).get('usage', 0)))

            answer = rsp_data['answer']
            parsed_content = parse_markdown_text(answer)
        elif response_mode == 'streaming':
            rsp_data, answer = self._dealwithStream(response)
            logger.debug("[DIFY] usage {}".format(rsp_data.get('metadata', {}).get('usage', 0)))
            parsed_content = parse_markdown_text(answer)
        # {"answer": "![image](/files/tools/dbf9cd7c-2110-4383-9ba8-50d9fd1a4815.png?timestamp=1713970391&nonce=0d5badf2e39466042113a4ba9fd9bf83&sign=OVmdCxCEuEYwc9add3YNFFdUpn4VdFKgl84Cg54iLnU=)"}
        at_prefix = ""
        channel = context.get("channel")
        is_group = context.get("isgroup", False)
        if is_group:
            at_prefix = "@" + context["msg"].actual_user_nickname + "\n"
        for item in parsed_content[:-1]:
            reply = None
            if item['type'] == 'text':
                content = at_prefix + item['content']
                reply = Reply(ReplyType.TEXT, content)
            elif item['type'] == 'image':
                image_url = self._fill_file_base_url(item['content'])
                image = self._download_image(image_url)
                if image:
                    reply = Reply(ReplyType.IMAGE, image)
                else:
                    reply = Reply(ReplyType.TEXT, f"图片链接：{image_url}")
            elif item['type'] == 'file':
                file_url = self._fill_file_base_url(item['content'])
                file_path = self._download_file(file_url)
                if file_path:
                    reply = Reply(ReplyType.FILE, file_path)
                else:
                    reply = Reply(ReplyType.TEXT, f"文件链接：{file_url}")
            logger.debug(f"[DIFY] reply={reply}")
            if reply and channel:
                channel.send(reply, context)
        # parsed_content 没有数据时，直接不回复
        if not parsed_content:
            return None, None
        final_item = parsed_content[-1]
        final_reply = None
        if final_item['type'] == 'text':
            content = final_item['content']
            if is_group:
                at_prefix = "@" + context["msg"].actual_user_nickname + "\n"
                content = at_prefix + content
            try:
                conA = self.split_sentence(content)

                final_reply = []
                for c in conA:
                    final_reply.append(Reply(ReplyType.TEXT, c))
            except:
                final_reply = Reply(ReplyType.TEXT, final_item['content'])

            # image = self._load_local_image()
            # final_reply = Reply(ReplyType.IMAGE, image)
        elif final_item['type'] == 'image':
            image_url = self._fill_file_base_url(final_item['content'])
            image = self._download_image(image_url)
            if image:
                final_reply = Reply(ReplyType.IMAGE, image)
            else:
                final_reply = Reply(ReplyType.TEXT, f"图片链接：{image_url}")
        elif final_item['type'] == 'file':
            file_url = self._fill_file_base_url(final_item['content'])
            file_path = self._download_file(file_url)
            if file_path:
                final_reply = Reply(ReplyType.FILE, file_path)
            else:
                final_reply = Reply(ReplyType.TEXT, f"文件链接：{file_url}")

        # 设置dify conversation_id, 依靠dify管理上下文
        if session.get_conversation_id() == '':
            session.set_conversation_id(rsp_data['conversation_id'])
        return final_reply, None

    def _download_file(self, url):
        try:
            response = requests.get(url)
            response.raise_for_status()
            parsed_url = urlparse(url)
            logger.debug(f"Downloading file from {url}")
            url_path = unquote(parsed_url.path)
            # 从路径中提取文件名
            file_name = url_path.split('/')[-1]
            logger.debug(f"Saving file as {file_name}")
            file_path = os.path.join(TmpDir().path(), file_name)
            with open(file_path, 'wb') as file:
                file.write(response.content)
            return file_path
        except Exception as e:
            logger.error(f"Error downloading {url}: {e}")
        return None

    def _load_local_image(self):
        try:
            image_path = r"D:\desktop\个人信息及各文档\1\微信图片_20190618113648.png"
            with open(image_path, "rb") as image_file:
                image_storage = io.BytesIO(image_file.read())
                size = image_storage.getbuffer().nbytes
                logger.debug(f"[WX] load local image success, size={size}, img_path={image_path}")
                image_storage.seek(0)
                return image_storage
        except Exception as e:
            logger.error(f"Error loading image from {image_path}: {e}")
            return None

    # def _download_image(self, url):
    #     try:
    #         pic_res = requests.get(url, stream=True)
    #         pic_res.raise_for_status()
    #         image_storage = io.BytesIO()
    #         size = 0
    #         for block in pic_res.iter_content(1024):
    #             size += len(block)
    #             image_storage.write(block)
    #         logger.debug(f"[WX] download image success, size={size}, img_url={url}")
    #         image_storage.seek(0)
    #         return image_storage
    #     except Exception as e:
    #         logger.error(f"Error downloading {url}: {e}")
    #     return None

    def _download_image(self, url):
        try:
            # 下载图片
            pic_res = requests.get(url, stream=True)
            pic_res.raise_for_status()

            # 将下载的图片保存到内存
            image_storage = io.BytesIO()
            for block in pic_res.iter_content(1024):
                image_storage.write(block)
            image_storage.seek(0)

            # 使用 Pillow 重新保存为 PNG 格式
            img = Image.open(image_storage)
            png_storage = io.BytesIO()
            img.convert("RGBA").save(png_storage, format="PNG")
            
            png_size = png_storage.getbuffer().nbytes
            logger.debug(f"[WX] download and converted image to PNG, size={png_size}, img_url={url}")

            png_storage.seek(0)
            return png_storage

        except Exception as e:
            logger.error(f"Error downloading or converting {url}: {e}")
            return None

    def _handle_agent(self, query: str, session: DifySession, context: Context):
        api_key = self._get_dify_conf(context, "dify_api_key", '')
        api_base = self._get_dify_conf(context, "dify_api_base", "https://api.dify.ai/v1")
        chat_client = ChatClient(api_key, api_base)
        response_mode = 'streaming'
        payload = self._get_payload(query, session, response_mode)
        files = self._get_upload_files(session, context)
        response = chat_client.create_chat_message(
            inputs=payload['inputs'],
            query=payload['query'],
            user= payload['user'],
            response_mode=payload['response_mode'],
            conversation_id=payload['conversation_id'],
            files=files
        )

        if response.status_code != 200:
            error_info = f"[DIFY] payload={payload} response text={response.text} status_code={response.status_code}"
            logger.warn(error_info)
            return None, error_info
        # response:
        # data: {"event": "agent_thought", "id": "8dcf3648-fbad-407a-85dd-73a6f43aeb9f", "task_id": "9cf1ddd7-f94b-459b-b942-b77b26c59e9b", "message_id": "1fb10045-55fd-4040-99e6-d048d07cbad3", "position": 1, "thought": "", "observation": "", "tool": "", "tool_input": "", "created_at": 1705639511, "message_files": [], "conversation_id": "c216c595-2d89-438c-b33c-aae5ddddd142"}
        # data: {"event": "agent_thought", "id": "8dcf3648-fbad-407a-85dd-73a6f43aeb9f", "task_id": "9cf1ddd7-f94b-459b-b942-b77b26c59e9b", "message_id": "1fb10045-55fd-4040-99e6-d048d07cbad3", "position": 1, "thought": "", "observation": "", "tool": "dalle3", "tool_input": "{\"dalle3\": {\"prompt\": \"cute Japanese anime girl with white hair, blue eyes, bunny girl suit\"}}", "created_at": 1705639511, "message_files": [], "conversation_id": "c216c595-2d89-438c-b33c-aae5ddddd142"}
        # data: {"event": "agent_message", "id": "1fb10045-55fd-4040-99e6-d048d07cbad3", "task_id": "9cf1ddd7-f94b-459b-b942-b77b26c59e9b", "message_id": "1fb10045-55fd-4040-99e6-d048d07cbad3", "answer": "I have created an image of a cute Japanese", "created_at": 1705639511, "conversation_id": "c216c595-2d89-438c-b33c-aae5ddddd142"}
        # data: {"event": "message_end", "task_id": "9cf1ddd7-f94b-459b-b942-b77b26c59e9b", "id": "1fb10045-55fd-4040-99e6-d048d07cbad3", "message_id": "1fb10045-55fd-4040-99e6-d048d07cbad3", "conversation_id": "c216c595-2d89-438c-b33c-aae5ddddd142", "metadata": {"usage": {"prompt_tokens": 305, "prompt_unit_price": "0.001", "prompt_price_unit": "0.001", "prompt_price": "0.0003050", "completion_tokens": 97, "completion_unit_price": "0.002", "completion_price_unit": "0.001", "completion_price": "0.0001940", "total_tokens": 184, "total_price": "0.0002290", "currency": "USD", "latency": 1.771092874929309}}}
        msgs, conversation_id = self._handle_sse_response(response)
        channel = context.get("channel")
        # TODO: 适配除微信以外的其他channel
        is_group = context.get("isgroup", False)
        for msg in msgs[:-1]:
            if msg['type'] == 'agent_message':
                if is_group:
                    at_prefix = "@" + context["msg"].actual_user_nickname + "\n"
                    msg['content'] = at_prefix + msg['content']
                reply = Reply(ReplyType.TEXT, msg['content'])
                channel.send(reply, context)
            elif msg['type'] == 'message_file':
                url = self._fill_file_base_url(msg['content']['url'])
                reply = Reply(ReplyType.IMAGE_URL, url)
                thread = threading.Thread(target=channel.send, args=(reply, context))
                thread.start()
        final_msg = msgs[-1]
        reply = None
        if final_msg['type'] == 'agent_message':
            reply = Reply(ReplyType.TEXT, final_msg['content'])
        elif final_msg['type'] == 'message_file':
            url = self._fill_file_base_url(final_msg['content']['url'])
            reply = Reply(ReplyType.IMAGE_URL, url)
        # 设置dify conversation_id, 依靠dify管理上下文
        if session.get_conversation_id() == '':
            session.set_conversation_id(conversation_id)
        return reply, None

    def _handle_workflow(self, query: str, session: DifySession, context: Context):
        payload = self._get_workflow_payload(query, session)
        api_key = self._get_dify_conf(context, "dify_api_key", '')
        api_base = self._get_dify_conf(context, "dify_api_base", "https://api.dify.ai/v1")
        dify_client = DifyClient(api_key, api_base)
        response = dify_client._send_request("POST", "/workflows/run", json=payload)
        if response.status_code != 200:
            error_info = f"[DIFY] payload={payload} response text={response.text} status_code={response.status_code}"
            logger.warn(error_info)
            return None, error_info

        #  {
        #      "log_id": "djflajgkldjgd",
        #      "task_id": "9da23599-e713-473b-982c-4328d4f5c78a",
        #      "data": {
        #          "id": "fdlsjfjejkghjda",
        #          "workflow_id": "fldjaslkfjlsda",
        #          "status": "succeeded",
        #          "outputs": {
        #          "text": "Nice to meet you."
        #          },
        #          "error": null,
        #          "elapsed_time": 0.875,
        #          "total_tokens": 3562,
        #          "total_steps": 8,
        #          "created_at": 1705407629,
        #          "finished_at": 1727807631
        #      }
        #  }

        rsp_data = response.json()
        if 'data' not in rsp_data or 'outputs' not in rsp_data['data'] or 'text' not in rsp_data['data']['outputs']:
            error_info = f"[DIFY] Unexpected response format: {rsp_data}"
            logger.warn(error_info)
            return None, error_info
        reply = Reply(ReplyType.TEXT, rsp_data['data']['outputs']['text'])
        return reply, None

    def _get_upload_files(self, session: DifySession, context: Context):
        session_id = session.get_session_id()
        img_cache = memory.USER_IMAGE_CACHE.get(session_id)
        if not img_cache or not self._get_dify_conf(context, "image_recognition", False):
            return None
        # 清理图片缓存
        memory.USER_IMAGE_CACHE[session_id] = None
        api_key = self._get_dify_conf(context, "dify_api_key", '')
        api_base = self._get_dify_conf(context, "dify_api_base", "https://api.dify.ai/v1")
        dify_client = DifyClient(api_key, api_base)
        msg = img_cache.get("msg")
        path = img_cache.get("path")
        msg.prepare()

        with open(path, 'rb') as file:
            file_name = os.path.basename(path)
            file_type, _ = mimetypes.guess_type(file_name)
            files = {
                'file': (file_name, file, file_type)
            }
            response = dify_client.file_upload(user=session.get_user(), files=files)

        if response.status_code != 200 and response.status_code != 201:
            error_info = f"[DIFY] response text={response.text} status_code={response.status_code} when upload file"
            logger.warn(error_info)
            return None, error_info
        # {
        #     'id': 'f508165a-10dc-4256-a7be-480301e630e6',
        #     'name': '0.png',
        #     'size': 17023,
        #     'extension': 'png',
        #     'mime_type': 'image/png',
        #     'created_by': '0d501495-cfd4-4dd4-a78b-a15ed4ed77d1',
        #     'created_at': 1722781568
        # }
        file_upload_data = response.json()
        logger.debug("[DIFY] upload file {}".format(file_upload_data))
        return [
            {
                "type": "image",
                "transfer_method": "local_file",
                "upload_file_id": file_upload_data['id']
            }
        ]

    def _fill_file_base_url(self, url: str):
        if url.startswith("https://") or url.startswith("http://"):
            return url
        # 补全文件base url, 默认使用去掉"/v1"的dify api base url
        return self._get_file_base_url() + url

    def _get_file_base_url(self) -> str:
        api_base = conf().get("dify_api_base", "https://api.dify.ai/v1")
        return api_base.replace("/v1", "")

    def _get_workflow_payload(self, query, session: DifySession):
        return {
            'inputs': {
                "query": query
            },
            "response_mode": "blocking",
            "user": session.get_user()
        }

    def _parse_sse_event(self, event_str):
        """
        Parses a single SSE event string and returns a dictionary of its data.
        """
        event_prefix = "data: "
        if not event_str.startswith(event_prefix):
            return None
        trimmed_event_str = event_str[len(event_prefix):]

        # Check if trimmed_event_str is not empty and is a valid JSON string
        if trimmed_event_str:
            try:
                event = json.loads(trimmed_event_str)
                return event
            except json.JSONDecodeError:
                logger.error(f"Failed to decode JSON from SSE event: {trimmed_event_str}")
                return None
        else:
            logger.warn("Received an empty SSE event.")
            return None

    # TODO: 异步返回events
    def _handle_sse_response(self, response: requests.Response):
        events = []
        for line in response.iter_lines():
            if line:
                decoded_line = line.decode('utf-8')
                event = self._parse_sse_event(decoded_line)
                if event:
                    events.append(event)

        merged_message = []
        accumulated_agent_message = ''
        conversation_id = None
        for event in events:
            event_name = event['event']
            if event_name == 'agent_message' or event_name == 'message':
                accumulated_agent_message += event['answer']
                logger.debug("[DIFY] accumulated_agent_message: {}".format(accumulated_agent_message))
                # 保存conversation_id
                if not conversation_id:
                    conversation_id = event['conversation_id']
            elif event_name == 'agent_thought':
                self._append_agent_message(accumulated_agent_message, merged_message)
                accumulated_agent_message = ''
                logger.debug("[DIFY] agent_thought: {}".format(event))
            elif event_name == 'message_file':
                self._append_agent_message(accumulated_agent_message, merged_message)
                accumulated_agent_message = ''
                self._append_message_file(event, merged_message)
            elif event_name == 'message_replace':
                # TODO: handle message_replace
                pass
            elif event_name == 'error':
                logger.error("[DIFY] error: {}".format(event))
                raise Exception(event)
            elif event_name == 'message_end':
                self._append_agent_message(accumulated_agent_message, merged_message)
                logger.debug("[DIFY] message_end usage: {}".format(event['metadata']['usage']))
                break
            else:
                logger.warn("[DIFY] unknown event: {}".format(event))

        if not conversation_id:
            raise Exception("conversation_id not found")

        return merged_message, conversation_id

    def _append_agent_message(self, accumulated_agent_message,  merged_message):
        if accumulated_agent_message:
            merged_message.append({
                'type': 'agent_message',
                'content': accumulated_agent_message,
            })

    def _append_message_file(self, event: dict, merged_message: list):
        if event.get('type') != 'image':
            logger.warn("[DIFY] unsupported message file type: {}".format(event))
        merged_message.append({
            'type': 'message_file',
            'content': event,
        })
