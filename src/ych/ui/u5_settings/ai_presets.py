# 预设 AI 服务商（OpenAI 兼容协议）：点击即预填名称与接口地址。
# 仅收录官方文档公开且稳定的多版本前缀地址；未收录的中转站用「自定义」。
from __future__ import annotations

# (名称, 接口地址)
AI_PRESETS: tuple[tuple[str, str], ...] = (
    ("OpenRouter", "https://openrouter.ai/api/v1"),
    ("OpenAI 官方", "https://api.openai.com/v1"),
    ("DeepSeek", "https://api.deepseek.com/v1"),
    ("智谱 GLM", "https://open.bigmodel.cn/api/paas/v4"),
    ("Kimi 月之暗面", "https://api.moonshot.cn/v1"),
    ("硅基流动", "https://api.siliconflow.cn/v1"),
    ("魔搭 ModelScope", "https://api-inference.modelscope.cn/v1"),
    ("阿里云百炼", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    ("火山方舟（豆包）", "https://ark.cn-beijing.volces.com/api/v3"),
    ("NVIDIA NIM", "https://integrate.api.nvidia.com/v1"),
    ("Groq", "https://api.groq.com/openai/v1"),
    ("xAI Grok", "https://api.x.ai/v1"),
    ("Ollama 本地", "http://localhost:11434/v1"),
    ("LM Studio 本地", "http://localhost:1234/v1"),
)
