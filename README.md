# TTS_modify 插件说明

启用插件后，可对特定文本进行TTS请求。实现LLM根据对话情绪自主调用TTS。  
仅对被`<tts></tts>`标记的文本进行TTS请求。  

## 配置项说明

TTS触发提示词：在发送LLM请求前，插件会将`tts_prompt`动态注入到System Prompt末尾。提示词可自行修改。

⚠️注意：`<tts></tts>`标签是必要的，即使要修改提示词，也不可省略。

<img src="https://github.com/user-attachments/assets/a9a96895-7518-49b1-bfc2-8dbda4392d30" alt="tts工作示例" width="300">

## 使用限制与注意事项

### 1. 标签规范
插件严格解析 `<tts>内容</tts>` 标签。
- **不支持嵌套**：如 `<tts>外层<tts>内层</tts></tts>` 会导致解析错误。
- **必须闭合**：标签必须成对出现，否则可能被忽略或解析异常。
- **位置**：支持在文本的任意位置插入标签，支持多个标签。

### 2. 安全与资源
- **文件路径**：插件会对 TTS 生成的音频文件路径进行安全校验，仅允许 AstrBot 数据目录下的文件。
- **临时文件**：若启用了文件服务（File Service），生成的音频 URL 对应的临时文件由 AstrBot 的 `file_token_service` 管理。

### 3. 错误处理
- **降级策略**：若 TTS 生成失败或路径不安全，插件会自动降级为纯文本回复，剥离 `<tts>` 标签。
- **失败通知**：可在配置中开启 `notify_on_failure`，当 TTS 失败时会在回复中添加提示。

---

😸经测试，无论`<tts></tts>`标签前后是否带有分段正则表达式，都不会影响TTS请求，可放心食用！  
原仓库地址，这个版本需要修改程序：[AstrBot_mod](https://github.com/L1ke40oz/AstrBot_mod) 