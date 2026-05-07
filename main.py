# main.py —— QQ协议包工具（取信息/取链接/看卡/发卡/取黑条/向上获取/发包/签名）
import json, re, asyncio, time
from pathlib import Path
from collections import defaultdict, deque
import aiohttp
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import (
    Json, Reply, Plain, Node, Nodes, Image,
)
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from astrbot.core.star.filter.permission import PermissionType

DEFAULT_SIGN_SERVER = ""
URL_PATTERN = re.compile(r'https?://[^\s]+', re.IGNORECASE)
BLACK_BAR_KEYWORDS = [
    "群聊", "风险", "提醒", "违规", "警告", "被撤", "已撤回",
    "该账号", "非大陆地区", "打卡", "红包", "通知", "公告"
]

@register(
    "astrbot_plugin_packet_tool",
    "YourName",
    "QQ协议包工具（取信息/取链接/看卡/发卡/取黑条/向上获取/发包）",
    "4.2.4",
    "https://github.com/xqe-bkflda/astrbot_plugin_packet_tool",
)
class PacketToolPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.plugin_name = "astrbot_plugin_packet_tool"
        data_root = get_astrbot_data_path()
        if isinstance(data_root, str):
            data_root = Path(data_root)
        self.data_dir = data_root / "plugin_data" / self.plugin_name
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.data_dir / "config.json"
        self.sign_server = self._load_config()
        self.message_cache = defaultdict(lambda: deque(maxlen=100))

    # ---------- 配置 ----------
    def _load_config(self) -> str:
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    return config.get("sign_server", DEFAULT_SIGN_SERVER)
            except Exception as e:
                logger.error(f"加载配置失败: {e}")
        return DEFAULT_SIGN_SERVER

    def _save_config(self):
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(
                    {"sign_server": self.sign_server}, f, ensure_ascii=False, indent=2
                )
        except Exception as e:
            logger.error(f"保存配置失败: {e}")

    # ---------- 签名 ----------
    async def _sign_data(self, json_data: str) -> str:
        if not self.sign_server:
            return json_data
        try:
            async with aiohttp.ClientSession() as session:
                payload = {"data": json_data}
                async with session.post(
                    self.sign_server, json=payload, timeout=10
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"签名服务器响应错误: {resp.status}")
                        return json_data
                    result = await resp.json()
                    sign = result.get("sign")
                    if not sign:
                        logger.error("签名服务器返回无 sign 字段")
                        return json_data
                    obj = json.loads(json_data)
                    obj["sign"] = sign
                    return json.dumps(obj, ensure_ascii=False)
        except Exception as e:
            logger.error(f"签名请求失败: {e}")
            return json_data

    # ---------- 工具 ----------
    def _get_full_text(self, event: AstrMessageEvent) -> str:
        return "".join([comp.text for comp in event.message_obj.message if isinstance(comp, Plain)])

    def _get_session_key(self, event: AstrMessageEvent) -> str:
        msg_obj = event.message_obj
        if msg_obj.group_id:
            return f"group_{msg_obj.group_id}"
        else:
            return f"private_{msg_obj.sender.user_id}"

    def _extract_json_from_chain(self, message_chain) -> dict | None:
        for comp in message_chain:
            if isinstance(comp, Json):
                data = comp.data
                if isinstance(data, str):
                    try:
                        return json.loads(data)
                    except json.JSONDecodeError:
                        return None
                elif isinstance(data, dict):
                    return data
        return None

    def _extract_all_urls(self, message_chain) -> list:
        urls = set()
        self._recursive_find_urls(message_chain, urls)
        return list(urls)

    def _recursive_find_urls(self, obj, urls_set):
        if isinstance(obj, str):
            found = URL_PATTERN.findall(obj)
            urls_set.update(found)
        elif isinstance(obj, dict):
            for k, v in obj.items():
                if k.lower() in ('url', 'jumpurl', 'source_url', 'thumb', 'preview', 'icon'):
                    if isinstance(v, str) and v.startswith('http'):
                        urls_set.add(v)
                else:
                    self._recursive_find_urls(v, urls_set)
        elif isinstance(obj, list):
            for item in obj:
                if hasattr(item, 'url') and item.url:
                    urls_set.add(item.url)
                if isinstance(item, Json):
                    data = item.data
                    if isinstance(data, str):
                        try:
                            data = json.loads(data)
                        except:
                            continue
                    self._recursive_find_urls(data, urls_set)
                if isinstance(item, Plain) and item.text:
                    self._recursive_find_urls(item.text, urls_set)
                if hasattr(item, '__dict__'):
                    self._recursive_find_urls(item.__dict__, urls_set)
                elif isinstance(item, dict):
                    self._recursive_find_urls(item, urls_set)
                elif isinstance(item, list):
                    self._recursive_find_urls(item, urls_set)

    # ---------- 消息缓存 ----------
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        session_key = self._get_session_key(event)
        self.message_cache[session_key].append(
            {"message_obj": event.message_obj, "message_id": event.message_obj.message_id}
        )

    # ---------- 发送消息卡片 ----------
    async def _build_forward_card(self, event: AstrMessageEvent, target_id: str):
        try:
            result = await event.bot.call_action("get_msg", message_id=int(target_id))
            if not result:
                return None
            formatted = json.dumps(result, indent=2, ensure_ascii=False, default=str)
            uin = str(event.get_sender_id())
            # 分段发送
            chunks = [formatted[i:i+1500] for i in range(0, len(formatted), 1500)]
            nodes = []
            for chunk in chunks:
                node = Node(uin=uin, name="绒绒说的话呀", content=[Plain(chunk)])
                nodes.append(node)
            return Nodes(nodes)
        except Exception as e:
            logger.error(f"构建消息卡片失败: {e}")
            return None

    # ========== 取信息 ==========
    @filter.command("取信息")
    async def fetch_info(self, event: AstrMessageEvent):
        reply_id = None
        for comp in event.message_obj.message:
            if isinstance(comp, Reply):
                reply_id = comp.id
                break
        if reply_id:
            target_id = reply_id
        else:
            session_key = self._get_session_key(event)
            cache = self.message_cache.get(session_key, [])
            current_id = event.message_obj.message_id
            prev_msg = None
            for msg_info in reversed(cache):
                if msg_info["message_id"] == current_id:
                    continue
                prev_msg = msg_info
                break
            if not prev_msg:
                yield event.plain_result("未找到上一条消息")
                return
            target_id = prev_msg["message_id"]

        card = await self._build_forward_card(event, target_id)
        if card is None:
            yield event.plain_result("获取消息失败")
            return
        yield event.chain_result([card])

    @filter.command("抓包")
    async def capture_packet_alias(self, event: AstrMessageEvent):
        await self.fetch_info(event)

    # ========== 取链接 ==========
    @filter.command("取链接")
    async def extract_links(self, event: AstrMessageEvent):
        reply_id = None
        for comp in event.message_obj.message:
            if isinstance(comp, Reply):
                reply_id = comp.id
                break
        if not reply_id:
            yield event.plain_result("请引用一条消息")
            return
        try:
            msg_data = await event.bot.call_action("get_msg", message_id=int(reply_id))
            if not msg_data:
                yield event.plain_result("获取消息失败")
                return
            urls = self._extract_all_urls(msg_data.get('message', []))
            if not urls:
                yield event.plain_result("未找到链接")
                return
            yield event.plain_result("找到的链接：\n" + "\n".join(urls))
        except Exception as e:
            yield event.plain_result(f"提取链接失败: {e}")

    # ========== 发卡 ==========
    @filter.command("发卡")
    async def send_card(self, event: AstrMessageEvent):
        full_text = self._get_full_text(event)
        if not full_text.startswith("/发卡"):
            return
        json_str = full_text[len("/发卡"):].strip()
        if not json_str:
            yield event.plain_result("用法: /发卡 <JSON>")
            return
        try:
            json.loads(json_str)
        except json.JSONDecodeError as e:
            yield event.plain_result(f"无效JSON: {e}")
            return
        signed = await self._sign_data(json_str)
        yield event.chain_result([Json(data=signed)])

    # ========== 看卡 ==========
    @filter.command("看卡")
    async def parse_card(self, event: AstrMessageEvent):
        reply = None
        for comp in event.message_obj.message:
            if isinstance(comp, Reply):
                reply = comp
                break
        if reply and hasattr(reply, "chain") and reply.chain:
            card = self._extract_json_from_chain(reply.chain)
            if card:
                yield event.plain_result(f"JSON卡片：\n{json.dumps(card, indent=2, ensure_ascii=False)}")
                return

        # 从上一条消息中查找
        session_key = self._get_session_key(event)
        cache = self.message_cache.get(session_key, [])
        current_id = event.message_obj.message_id
        for msg_info in reversed(cache):
            if msg_info["message_id"] == current_id:
                continue
            card = self._extract_json_from_chain(msg_info["message_obj"].message)
            if card:
                yield event.plain_result(f"上一条 JSON 卡片：\n{json.dumps(card, indent=2, ensure_ascii=False)}")
                return
        yield event.plain_result("未找到 JSON 卡片")

    # ========== 发小程序 ==========
    @filter.command("发小程序")
    async def send_miniprogram(self, event: AstrMessageEvent):
        full_text = self._get_full_text(event)
        if not full_text.startswith("/发小程序"):
            return
        json_str = full_text[len("/发小程序"):].strip()
        if not json_str:
            yield event.plain_result("用法: /发小程序 <JSON>")
            return
        try:
            json.loads(json_str)
        except json.JSONDecodeError as e:
            yield event.plain_result(f"无效JSON: {e}")
            return
        signed = await self._sign_data(json_str)
        yield event.chain_result([Json(data=signed)])

    # ========== 取黑条消息 ==========
    @filter.command("取黑条消息")
    async def get_black_bar_message(self, event: AstrMessageEvent):
        if not event.message_obj.group_id:
            yield event.plain_result("仅群聊可用")
            return
        session_key = self._get_session_key(event)
        cache = self.message_cache.get(session_key, [])
        if len(cache) < 2:
            yield event.plain_result("历史消息不足")
            return
        current_id = event.message_obj.message_id
        current_index = -1
        for i, msg_info in enumerate(cache):
            if msg_info["message_id"] == current_id:
                current_index = i
                break
        if current_index == -1:
            yield event.plain_result("未找到当前消息位置")
            return
        for msg_info in list(cache)[current_index-1::-1]:
            msg_obj = msg_info["message_obj"]
            raw = getattr(msg_obj, 'raw_message', None)
            if raw:
                if isinstance(raw, dict) and raw.get('post_type') == 'notice':
                    yield event.plain_result(f"系统通知:\n{json.dumps(raw, indent=2, ensure_ascii=False)[:1500]}")
                    return
                raw_str = str(raw)
                if any(kw in raw_str for kw in BLACK_BAR_KEYWORDS):
                    yield event.plain_result(f"黑条消息:\n{json.dumps(raw, indent=2, ensure_ascii=False)[:1500] if isinstance(raw, dict) else raw_str[:1500]}")
                    return
            message_str = getattr(msg_obj, 'message_str', '') or ''
            if isinstance(raw, dict):
                for seg in raw.get('message', []):
                    if isinstance(seg, dict) and seg.get('type') == 'text':
                        message_str += seg.get('data', {}).get('text', '')
            if any(kw in message_str for kw in BLACK_BAR_KEYWORDS):
                yield event.plain_result(f"疑似黑条:\n{message_str[:1500]}")
                return
        yield event.plain_result("未找到黑条消息")

    # ========== 发包 ==========
    @filter.command("发包")
    async def send_raw_packet(self, event: AstrMessageEvent):
        full_text = self._get_full_text(event)
        if not full_text.startswith("/发包"):
            return
        json_str = full_text[len("/发包"):].strip()
        if not json_str:
            yield event.plain_result("用法: /发包 <JSON>")
            return
        try:
            json.loads(json_str)
        except json.JSONDecodeError as e:
            yield event.plain_result(f"无效JSON: {e}")
            return
        signed = await self._sign_data(json_str)
        yield event.chain_result([Json(data=signed)])

    # ========== 向上获取 ==========
    @filter.command("向上获取")
    async def fetch_upward(self, event: AstrMessageEvent, n: int = 1):
        if n <= 0:
            yield event.plain_result("数量必须大于0")
            return
        session_key = self._get_session_key(event)
        cache = self.message_cache.get(session_key, [])
        current_index = -1
        for i, msg_info in enumerate(cache):
            if msg_info["message_id"] == event.message_obj.message_id:
                current_index = i
                break
        if current_index == -1:
            yield event.plain_result("未找到当前消息")
            return
        target_index = current_index - n
        if target_index < 0:
            yield event.plain_result(f"历史消息不足，只有 {current_index} 条更早的消息")
            return
        target_msg = cache[target_index]
        card = await self._build_forward_card(event, target_msg["message_id"])
        if card is None:
            yield event.plain_result("获取消息失败")
            return
        yield event.chain_result([card])

    # ========== 签名服务器配置 ==========
    @filter.command("配置签名服务器")
    @filter.permission_type(PermissionType.ADMIN)
    async def config_sign(self, event: AstrMessageEvent):
        full_text = self._get_full_text(event)
        parts = full_text.split(maxsplit=1)
        if len(parts) < 2:
            yield event.plain_result(f"当前签名服务器：{self.sign_server}\n用法：/配置签名服务器 <url> 或 /配置签名服务器 空 以清空")
            return
        new_url = parts[1].strip()
        if new_url.lower() == "空":
            self.sign_server = ""
            self._save_config()
            yield event.plain_result("已清空签名服务器")
        else:
            if not new_url.startswith("http"):
                yield event.plain_result("请输入完整的 URL")
                return
            self.sign_server = new_url
            self._save_config()
            yield event.plain_result(f"签名服务器已设置为：{self.sign_server}")

    @filter.command("setsign")
    async def setsign_alias(self, event: AstrMessageEvent):
        await self.config_sign(event)

    async def terminate(self):
        logger.info("协议包工具已卸载")
