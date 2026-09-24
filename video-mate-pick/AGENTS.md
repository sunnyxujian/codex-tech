# video-mate-pick 子项目

- 范围仅为后续 Skill 提取本机视频的原始素材：原音轨、WAV、可选 Qwen 识别段落、截图及时间/媒体信息。不要引入文章生成、知识点整理、浏览器扩展或 Codex 调用。
- 使用本目录 `.venv/Scripts/python.exe`、`models/`、`.runtime/` 和 `output/`，不要在工作区根目录安装依赖或写业务输出。
- Skill 优先通过 `extract.py` 调用；API 输入为本机源文件绝对路径，完整接口见 README.md。源文件只读。
- 音频 8765，画面 8766；启停使用本目录 `services.ps1`。必须核对健康信息的 service 和 project_root，不能按端口误停其他项目进程。
- 语音识别默认 Qwen3-ASR 0.6B INT8，可 `--no-transcript` 关闭，不自动下载、不降级。保留识别原文；时间是粗粒度片段范围，不宣称逐词精确或自动去除边界重复。
- 画面支持 interval、timestamps、scene，以及面向 Watchless 的 keyframes、scan；可输出 PNG、裁剪、缩放、灰度画面。输出截图和 JSON 时间索引；SSIM、语义判断和笔记制作留给调用方。不能把抽帧当成完整视觉理解，不给截图编造语义标题。超出数量上限明确失败，不静默丢弃。
- 调整核心逻辑后运行 `python -m unittest discover -s tests -v`（使用本项目 Python）；启动和路径变化后再验证服务身份与真实素材输出。
