import re
import traceback
from pathlib import Path

from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api.event.filter import on_llm_request, on_decorating_result
from astrbot.api.message_components import Plain, Record
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from astrbot.core import logger

# TTS 标签的正则：匹配 <tts>...</tts>，内容非贪婪
TTS_PATTERN = re.compile(r"<tts>(.*?)</tts>", re.DOTALL)
TTS_START_TAG = "<tts>"
TTS_END_TAG = "</tts>"
BOUNDARY_SEPARATORS = "$"
BOUNDARY_SEPARATOR_PATTERN = re.compile(rf"[{re.escape(BOUNDARY_SEPARATORS)}]+$")
LEADING_BOUNDARY_SEPARATOR_PATTERN = re.compile(
    rf"^[{re.escape(BOUNDARY_SEPARATORS)}]+"
)


class TTSModifyPlugin(Star):
    """对 LLM 回复中 <tts></tts> 标签包裹的文本进行 TTS 转换。"""

    CONFIG_KEY_TTS_SETTINGS = "provider_tts_settings"
    CONFIG_KEY_ENABLE = "enable"
    CONFIG_KEY_TTS_PROMPT = "tts_prompt"
    CONFIG_KEY_NOTIFY_FAILURE = "notify_on_failure"

    def __init__(self, context: Context, config: dict = None):
        super().__init__(context, config)
        self.config = config or {}

    # ─── 辅助方法 ───

    def _get_global_config(self, event: AstrMessageEvent):
        """安全地获取全局/会话配置。"""
        try:
            return self.context.get_config(event.unified_msg_origin)
        except (KeyError, Exception):
            pass
        try:
            return self.context.get_config()
        except Exception as e:
            logger.error(f"TTS插件获取配置失败: {e}")
            return None

    @staticmethod
    def _trim_boundary_separators(text: str, *, leading: bool = False) -> str:
        if leading:
            return LEADING_BOUNDARY_SEPARATOR_PATTERN.sub("", text)
        return BOUNDARY_SEPARATOR_PATTERN.sub("", text)

    @classmethod
    def _append_text_segment(cls, segments: list[dict], text: str) -> None:
        stripped = text.strip()
        if not stripped:
            return
        if segments and segments[-1]["type"] == "tts":
            stripped = cls._trim_boundary_separators(stripped, leading=True).strip()
        stripped = cls._trim_boundary_separators(stripped).strip()
        if stripped:
            segments.append({"type": "text", "content": stripped})

    @classmethod
    def _split_by_tts_tags(cls, text: str) -> list[dict]:
        """
        将文本按 <tts>...</tts> 标签拆分成段落列表。

        返回列表中每个元素为 dict:
          {"type": "text", "content": "普通文本"}
          {"type": "tts",  "content": "需要TTS的文本"}

        处理场景：
          1. 标签前后有分隔符: "你好$<tts>语音</tts>$后续" → 正常拆分
          2. 标签前后无分隔符: "你好<tts>语音</tts>后续" → 自动拆分成独立段
          3. 多个标签: "a<tts>b</tts>c<tts>d</tts>e" → 5段
          4. 无标签: "纯文本" → 1段
          5. 残缺标签: "<tts>半截" / "半截</tts>" → 剥离标签字面量并降级
        """
        segments = []
        cursor = 0
        text_length = len(text)

        while cursor < text_length:
            start = text.find(TTS_START_TAG, cursor)
            end = text.find(TTS_END_TAG, cursor)

            if start == -1 and end == -1:
                cls._append_text_segment(segments, text[cursor:])
                break

            if end != -1 and (start == -1 or end < start):
                cls._append_text_segment(segments, text[cursor:end])
                cursor = end + len(TTS_END_TAG)
                continue

            if start > cursor:
                cls._append_text_segment(segments, text[cursor:start])

            if start == -1:
                break

            end = text.find(TTS_END_TAG, start + len(TTS_START_TAG))
            if end == -1:
                cls._append_text_segment(segments, text[start + len(TTS_START_TAG) :])
                break

            tts_content = text[start + len(TTS_START_TAG) : end].strip()
            tts_content = cls._trim_boundary_separators(
                cls._trim_boundary_separators(tts_content, leading=True),
            ).strip()
            if tts_content:
                segments.append({"type": "tts", "content": tts_content})
            cursor = end + len(TTS_END_TAG)

        if not segments:
            stripped = text.replace(TTS_START_TAG, "").replace(TTS_END_TAG, "").strip()
            if stripped:
                segments.append({"type": "text", "content": stripped})

        return segments

    @staticmethod
    def _validate_audio_path(audio_path: str) -> bool:
        """校验音频文件路径是否在 AstrBot data 目录下（安全检查）。"""
        try:
            audio_file = Path(audio_path).resolve()
            expected_dir = Path(get_astrbot_data_path()).resolve()
            return audio_file.is_relative_to(expected_dir)
        except Exception:
            return False

    # ─── Hook: LLM 请求前注入 TTS 提示词 ───

    @on_llm_request()
    async def on_llm_req(self, event: AstrMessageEvent, request: ProviderRequest):
        global_config = self._get_global_config(event)
        if not global_config:
            return

        # 检查全局 TTS 是否启用
        provider_tts_settings = global_config.get(self.CONFIG_KEY_TTS_SETTINGS, {})
        if not provider_tts_settings.get(self.CONFIG_KEY_ENABLE, False):
            return

        # 检查 TTS Provider 是否可用
        tts_provider = self.context.get_using_tts_provider(event.unified_msg_origin)
        if not tts_provider:
            return

        # 注入提示词
        tts_prompt = self.config.get(self.CONFIG_KEY_TTS_PROMPT, "")
        if tts_prompt:
            request.system_prompt += f"\n{tts_prompt}"

    # ─── Hook: 结果装饰——处理 TTS 标签 ───

    @on_decorating_result(priority=10)
    async def on_decorate(self, event: AstrMessageEvent):
        result = event.get_result()
        if not result or not result.chain:
            return

        # 获取配置
        global_config = self._get_global_config(event)
        if not global_config:
            return

        provider_tts_settings = global_config.get(self.CONFIG_KEY_TTS_SETTINGS, {})

        # 快速检测：是否有任何 Plain 组件包含 TTS 标签或残缺标签
        has_tts_tag = any(
            isinstance(comp, Plain)
            and (TTS_START_TAG in comp.text or TTS_END_TAG in comp.text)
            for comp in result.chain
        )
        if not has_tts_tag:
            return

        # 获取 TTS 服务提供商
        tts_provider = self.context.get_using_tts_provider(event.unified_msg_origin)
        if not tts_provider:
            logger.warning(
                f"会话 {event.unified_msg_origin} 检测到 <tts> 标签，"
                f"但未找到 TTS 服务提供商，将剥离标签并显示文本。"
            )

        # 构建新的消息链
        new_chain = []
        modified = False

        for comp in result.chain:
            if isinstance(comp, Plain) and (
                TTS_START_TAG in comp.text or TTS_END_TAG in comp.text
            ):
                components = await self._process_tts_text(
                    comp.text, tts_provider, provider_tts_settings
                )
                new_chain.extend(components)
                modified = True
            else:
                new_chain.append(comp)

        if modified:
            result.chain = new_chain

    async def _process_tts_text(
        self, text: str, tts_provider, provider_settings: dict
    ) -> list:
        """
        处理包含 <tts> 标签的文本，将其拆分为 Plain 和 Record 组件。

        关键逻辑：
          - 标签外的文本 → Plain 组件
          - 标签内的文本 → 调用 TTS 生成 → Record 组件
          - 自动处理标签与普通文本之间没有分隔符的情况
        """
        segments = self._split_by_tts_tags(text)
        components = []

        tts_enabled = provider_settings.get(self.CONFIG_KEY_ENABLE, False)
        dual_output = provider_settings.get("dual_output", False)
        use_file_service = provider_settings.get("use_file_service", False)
        notify_failure = self.config.get(self.CONFIG_KEY_NOTIFY_FAILURE, False)

        for seg in segments:
            if seg["type"] == "text":
                # 普通文本，直接作为 Plain 组件
                components.append(Plain(seg["content"]))

            elif seg["type"] == "tts":
                tts_content = seg["content"]
                audio_component = None

                if tts_enabled and tts_provider:
                    audio_component = await self._generate_tts_audio(
                        tts_content, tts_provider, use_file_service
                    )

                if audio_component:
                    # TTS 生成成功
                    components.append(audio_component)
                    if dual_output:
                        components.append(Plain(tts_content))
                else:
                    # TTS 不可用或生成失败，回退为纯文本
                    if not tts_enabled:
                        logger.warning("检测到 <tts> 标签，但全局 TTS 未启用，剥离标签显示文本。")
                    if notify_failure and tts_enabled and tts_provider:
                        components.append(Plain(f"[TTS失败] {tts_content}"))
                    else:
                        components.append(Plain(tts_content))

        return components

    async def _generate_tts_audio(
        self, tts_content: str, tts_provider, use_file_service: bool
    ) -> Record | None:
        """调用 TTS 提供商生成音频，返回 Record 组件或 None。"""
        try:
            audio_path = await tts_provider.get_audio(tts_content)
            if not audio_path:
                logger.error(f"TTS 返回空路径，内容: {tts_content[:50]}...")
                return None

            # 安全校验
            if not self._validate_audio_path(audio_path):
                logger.error(f"TTS 返回路径不安全: {audio_path}")
                return None

            # 构建 Record 组件
            record = Record.fromFileSystem(audio_path, text=tts_content)

            # 如果需要文件服务，注册并获取 URL
            if use_file_service:
                try:
                    url = await record.register_to_file_service()
                    record.url = url
                    record.file = url
                except Exception as e:
                    logger.warning(f"文件服务注册失败，使用本地路径: {e}")

            return record

        except Exception as e:
            logger.error(f"TTS 生成失败: {e}")
            logger.debug(traceback.format_exc())
            return None
