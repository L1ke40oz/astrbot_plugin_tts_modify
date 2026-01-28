import re
import traceback
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.all import *
from astrbot.core.message.components import Plain, Record
from astrbot.core import file_token_service, logger
from astrbot.core.star.register import register_on_decorating_result, register_on_llm_request
from astrbot.core.provider.entities import ProviderRequest

@register("astrbot_plugin_tts_modify", "L1ke40oz", "TTS Tag Support Plugin", "1.0.0")
class TTSModifyPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context, config)
        self.config = config

    @register_on_llm_request()
    async def on_llm_req(self, event: AstrMessageEvent, request: ProviderRequest):
        # 1. Check global TTS switch
        try:
            global_config = self.context.get_config(event.unified_msg_origin)
        except Exception:
            global_config = self.context.get_config()
            
        provider_tts_settings = global_config.get("provider_tts_settings", {})
        if not provider_tts_settings.get("enable", False):
            return

        # 2. Check if TTS Provider is active for this session
        tts_provider = self.context.get_using_tts_provider(event.unified_msg_origin)
        if not tts_provider:
            return

        # 3. Inject Prompt from Plugin Config
        if self.config:
            tts_prompt = self.config.get("TTS_Prompt", "")
            if tts_prompt:
                # Append to system prompt with a newline for safety
                request.system_prompt += f"\n{tts_prompt}"

    @register_on_decorating_result(priority=10)
    async def on_decorate(self, event: AstrMessageEvent):
        result = event.get_result()
        if not result or not result.chain:
            return

        # 1. 从配置文件获取TTS总开关是否开启
        try:
            config = self.context.get_config(event.unified_msg_origin)
        except Exception:
            config = self.context.get_config()
            
        provider_tts_settings = config.get("provider_tts_settings", {})
        
        # 2. 检查消息中是否包含TTS标签
        has_tts_tag = False
        for comp in result.chain:
            if isinstance(comp, Plain) and "<tts>" in comp.text:
                has_tts_tag = True
                break
        
        if not has_tts_tag:
            return

        # 3. 获取TTS服务提供商
        tts_provider = self.context.get_using_tts_provider(event.unified_msg_origin)
        if not tts_provider:
            logger.error(f"会话 {event.unified_msg_origin} 缺少 TTS 服务提供商，但检测到 <tts> 标签。将剥离标签并显示文本。")

        new_chain = []
        modified = False
        
        # 4. 使用正则匹配
        pattern = re.compile(r"<tts>(.*?)</tts>", re.DOTALL)

        for comp in result.chain:
            if isinstance(comp, Plain) and "<tts>" in comp.text:
                parts = []
                last_idx = 0
                
                # 查找文本组件中的所有标签
                for match in pattern.finditer(comp.text):
                    # 添加标签前的文本
                    if match.start() > last_idx:
                        pre_text = comp.text[last_idx:match.start()]
                        if pre_text:
                            parts.append(Plain(pre_text))
                    
                    tts_content = match.group(1).strip()
                    if tts_content:
                        audio_path = None
                        # 尝试生成音频
                        # 检查全局开关
                        tts_enabled = provider_tts_settings.get("enable", False)
                        
                        if tts_enabled and tts_provider:
                            try:
                                audio_path = await tts_provider.get_audio(tts_content)
                            except Exception as e:
                                logger.error(f"TTS 生成失败: {e}")
                                logger.error(traceback.format_exc())
                        elif not tts_enabled:
                            logger.warning(f"检测到 TTS 标签，但全局配置中 TTS 未启用。剥离标签并显示文本。")
                        
                        if audio_path:
                             # 成功：转换为 Record
                             use_file_service = provider_tts_settings.get("use_file_service", False)
                             callback_api_base = config.get("callback_api_base", "")
                             dual_output = provider_tts_settings.get("dual_output", False)
                             
                             url = None
                             if use_file_service and callback_api_base:
                                 try:
                                     token = await file_token_service.register_file(audio_path)
                                     url = f"{callback_api_base}/api/file/{token}"
                                 except Exception as e:
                                     logger.error(f"文件注册失败: {e}")

                             parts.append(Record(file=url or audio_path, url=url or audio_path))
                             
                             if dual_output:
                                 parts.append(Plain(tts_content))
                        else:
                            # 生成失败/未配置TTS服务：回退到纯文本（剥离标签）
                            parts.append(Plain(tts_content))
                    
                    last_idx = match.end()
                
                # 添加标签后的文本
                if last_idx < len(comp.text):
                    post_text = comp.text[last_idx:]
                    if post_text:
                        parts.append(Plain(post_text))
                
                # If we found matches or altered structure, we extend
                if parts:
                    new_chain.extend(parts)
                else:
                    # If regex matched nothing (e.g. broken tags), keep original but maybe we should've handled it?
                    # With "<tts>" check, we expect matches. If broken tags like "<tts>..." without end, regex won't match.
                    # Fallback to appending original if parsing yielded nothing but we detected start tag?
                    # The loop logic handles valid matches. Unmatched content stays in "text after last tag" or "all text if no matches"
                    # Wait, if `finditer` returns empty, `last_idx` is 0, so we append the whole text as key `Plain`.
                    # effectively doing nothing to broken tags, which is acceptable safety.
                    pass
                    
                modified = True
            else:
                new_chain.append(comp)

        if modified:
            result.chain = new_chain
