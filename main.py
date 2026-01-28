import re
import traceback
from pathlib import Path
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.core.message.components import Plain, Record
from astrbot.core import file_token_service, logger
from astrbot.core.star.register import register_on_decorating_result, register_on_llm_request
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.star_tools import StarTools
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

class TTSModifyPlugin(Star):
    TTS_TAG_START = "<tts>"
    TTS_TAG_END = "</tts>"
    CONFIG_KEY_TTS_SETTINGS = "provider_tts_settings"
    CONFIG_KEY_ENABLE = "enable"
    CONFIG_KEY_TTS_PROMPT = "tts_prompt"
    CONFIG_KEY_NOTIFY_FAILURE = "notify_on_failure"

    def __init__(self, context: Context, config: dict = None):
        super().__init__(context, config)
        self.config = config

    @register_on_llm_request()
    async def on_llm_req(self, event: AstrMessageEvent, request: ProviderRequest):
        # 1. 检查配置
        try:
            global_config = self.context.get_config(event.unified_msg_origin)
        except KeyError:
             # 如果没有特定会话配置，尝试获取全局配置
            global_config = self.context.get_config()
        except Exception as e:
            logger.error(f"TTS插件获取配置失败: {e}")
            logger.debug(traceback.format_exc())
            return

        provider_tts_settings = global_config.get(self.CONFIG_KEY_TTS_SETTINGS, {})
        if not provider_tts_settings.get(self.CONFIG_KEY_ENABLE, False):
            return

        # 2. 检查 TTS Provider 是否可用
        tts_provider = self.context.get_using_tts_provider(event.unified_msg_origin)
        if not tts_provider:
            return

        # 3. 注入 Prompt
        if self.config:
            tts_prompt = self.config.get(self.CONFIG_KEY_TTS_PROMPT, "")
            if tts_prompt:
                # Append to system prompt with a newline for safety
                request.system_prompt += f"\n{tts_prompt}"

    @register_on_decorating_result(priority=10)
    async def on_decorate(self, event: AstrMessageEvent):
        result = event.get_result()
        if not result or not result.chain:
            return

        # 1. 获取配置
        try:
            config = self.context.get_config(event.unified_msg_origin)
        except KeyError:
            config = self.context.get_config()
        except Exception as e:
            logger.error(f"TTS插件获取配置失败: {e}")
            return
            
        provider_tts_settings = config.get(self.CONFIG_KEY_TTS_SETTINGS, {})
        
        # 2. 检查消息中是否包含TTS标签
        has_tts_tag = False
        for comp in result.chain:
            if isinstance(comp, Plain) and self.TTS_TAG_START in comp.text:
                has_tts_tag = True
                break
        
        if not has_tts_tag:
            return

        # 3. 获取TTS服务提供商
        tts_provider = self.context.get_using_tts_provider(event.unified_msg_origin)
        if not tts_provider:
            logger.error(f"会话 {event.unified_msg_origin} 缺少 TTS 服务提供商，但检测到 <tts> 标签。将剥离标签并显示文本。")

        # 4. 处理标签
        new_chain = []
        modified = False
        
        for comp in result.chain:
            if isinstance(comp, Plain) and self.TTS_TAG_START in comp.text:
                components = await self._process_tts_tags(comp.text, tts_provider, provider_tts_settings, config)
                new_chain.extend(components)
                modified = True
            else:
                new_chain.append(comp)

        if modified:
            result.chain = new_chain

    async def _process_tts_tags(self, text: str, tts_provider, provider_settings: dict, config: dict) -> list:
        """处理文本中的 TTS 标签，返回组件列表"""
        parts = []
        # 使用非贪婪匹配
        pattern = re.compile(f"{self.TTS_TAG_START}(.*?){self.TTS_TAG_END}", re.DOTALL)
        last_idx = 0
        
        for match in pattern.finditer(text):
            # 添加标签前的文本
            if match.start() > last_idx:
                pre_text = text[last_idx:match.start()]
                if pre_text:
                    parts.append(Plain(pre_text))
            
            # 处理 TTS 内容
            tts_content = match.group(1).strip()
            if tts_content:
                component = await self._create_tts_component(
                    tts_content, tts_provider, provider_settings, config
                )
                if component:
                    parts.extend(component)
            
            last_idx = match.end()
        
        # 添加标签后的文本
        if last_idx < len(text):
            post_text = text[last_idx:]
            if post_text:
                parts.append(Plain(post_text))
        
        return parts

    async def _create_tts_component(self, tts_content: str, tts_provider, provider_settings: dict, config: dict) -> list:
        """生成 TTS 组件"""
        res_components = []
        audio_path = None
        
        tts_enabled = provider_settings.get(self.CONFIG_KEY_ENABLE, False)
        
        if tts_enabled and tts_provider:
            try:
                audio_path = await tts_provider.get_audio(tts_content)
                
                # 安全检查
                if audio_path:
                    audio_file = Path(audio_path).resolve()
                    expected_dir = Path(get_astrbot_data_path()).resolve()
                    # 允许在 data 目录下的文件
                    if not audio_file.is_relative_to(expected_dir):
                        logger.error(f"TTS 返回路径不安全: {audio_path}")
                        audio_path = None
                        
            except Exception as e:
                logger.error(f"TTS 生成失败: {e}")
                logger.debug(traceback.format_exc())
            
            if audio_path:
                # 成功：转换为 Record
                use_file_service = provider_settings.get("use_file_service", False)
                callback_api_base = config.get("callback_api_base", "")
                dual_output = provider_settings.get("dual_output", False)
                
                url = None
                if use_file_service and callback_api_base:
                    try:
                        token = await file_token_service.register_file(audio_path)
                        url = f"{callback_api_base}/api/file/{token}"
                    except Exception as e:
                        logger.error(f"文件注册失败: {e}")

                res_components.append(Record(file=url or audio_path, url=url or audio_path))
                
                if dual_output:
                    res_components.append(Plain(tts_content))
            else:
                # 生成失败 或 路径不安全
                if provider_settings.get(self.CONFIG_KEY_NOTIFY_FAILURE, False):
                    res_components.append(Plain(f"[TTS失败] {tts_content}"))
                else:
                    res_components.append(Plain(tts_content))
                    
        elif not tts_enabled:
            # TTS 未启用
            logger.warning(f"检测到 TTS 标签，但全局配置中 TTS 未启用。剥离标签并显示文本。")
            res_components.append(Plain(tts_content))
        else:
            # 没 provider
             res_components.append(Plain(tts_content))

        return res_components
