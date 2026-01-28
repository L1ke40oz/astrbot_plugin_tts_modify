# TTS_modify 插件说明

启用插件后，可对特定文本进行TTS请求。实现LLM根据对话情绪自主调用TTS。  
仅对被`<tts></tts>`标记的文本进行TTS请求。  

## 配置项说明

TTS触发提示词：在发送LLM请求前，插件会将`TTS触发提示词`动态注入到System Prompt末尾。提示词可自行修改。

⚠️注意：`<tts></tts>`标签是必要的，即使要修改提示词，也不可省略。

<img src="https://github.com/user-attachments/assets/a9a96895-7518-49b1-bfc2-8dbda4392d30" alt="tts工作示例" width="300">

---

😸经测试，无论`<tts></tts>`标签前后是否带有分段正则表达式，都不会影响TTS请求，可放心食用！  
原仓库地址，这个版本需要修改程序：[AstrBot_mod](https://github.com/L1ke40oz/AstrBot_mod) 