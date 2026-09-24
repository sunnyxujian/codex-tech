# video-mate-pick

给后续 Skill 提供视频原始素材的本地子项目。仅负责音轨、可选语音识别和画面提取，不生成文章、标题、知识点、PPT 或摘要，不调用 Codex/外部模型服务。

## 启动

双击 `start-services.cmd`。状态和停止分别使用 `status-services.cmd`、`stop-services.cmd`。服务只监听本机：

| 服务 | 地址 | 功能 |
| --- | --- | --- |
| audio | http://127.0.0.1:8765 | 原音轨、标准 WAV、可选 Qwen 逐字稿 |
| video | http://127.0.0.1:8766 | 按间隔、指定秒数或场景变化提取截图 |

命令行管理：`powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\services.ps1 start|status|stop|restart [audio|video|all]`。退出终端不影响后台服务；重启电脑后重新启动。启动器核对项目路径及进程归属，不会接管或关闭其他项目的服务。停止会中断正在运行的任务，已写出的素材保留，可重新提交。

## Skill 首选调用入口

在本目录运行，路径含空格时加引号：

```powershell
# 音轨 + Qwen 原始逐字稿 + 每 5 秒截图；自动跳过不存在的音频/视频流
.\.venv\Scripts\python.exe .\extract.py "D:\素材\教学.mp4"

# 只提取音轨，不运行识别
.\.venv\Scripts\python.exe .\extract.py "D:\素材\教学.mp4" --audio-only --no-transcript

# 只提取画面，精确指定需要回看的时间点
.\.venv\Scripts\python.exe .\extract.py "D:\素材\教学.mp4" --frames-only --timestamps 0 12.5 38

# 按场景变化提取，不做知识点判断
.\.venv\Scripts\python.exe .\extract.py "D:\素材\教学.mp4" --frames-only --mode scene --threshold 0.25
```

进度写入 stderr，最终 JSON 清单写入 stdout，方便 Skill 解析。清单同时落盘到 `output/bundles/<编号>/manifest.json`，包含各任务编号、输出绝对路径及原始媒体信息。进程退出码为 0 表示所有请求完成，非 0 表示失败或等待超时；超时不取消后台任务，可按编号查询。

其他参数：`--interval 2`（秒）、`--max-frames 2000`、`--language auto|zh|en`、`--timeout 86400`。默认截图上限 2000；超限会明确失败，不会悄悄截断。指定时间点排序并去重，不能超过视频时长。

## 输出约定

每个任务独立写入 `output/audio/<编号>/` 或 `output/video/<编号>/`：

| 文件 | 含义 |
| --- | --- |
| `request.json` | 原始本机文件路径和请求参数 |
| `metadata.json` | 时长、音视频流、编码、分辨率、帧率、起始时间与 time_base |
| `audio.mka` | 原音轨重封装，使用 stream copy，不重新编码 |
| `audio.wav` | 为识别生成的 16 kHz、单声道、16 位 PCM WAV |
| `transcript.json` / `transcript.txt` | 可选的 Qwen 原始识别段落和连续文本 |
| `frames/*.jpg` / `frames.json` | 视频截图及时间索引 |
| `result.json` | 成功后的文件清单，文件相对路径基于当前任务输出目录 |
| `error.json` | 失败原因；已提取的素材仍保留 |

音轨与 WAV 的时间从所选音轨起点计算，源音轨的起始时间另行记录。Qwen 段落时间只是约 25 秒分段的范围，含边界重叠上下文，**不是逐词对齐**；文本不摘要、不翻译、不自动简繁转换或去重。语音模型可能听错或在重叠处重复，后续 Skill 可结合音画核对。

固定间隔/指定时间点记录的是请求的播放秒数，对应画面取该位置可解码的帧；场景模式记录解码帧的实际相对时间。场景变化阈值不保证捕捉每次板书或鼠标变化，Skill 可根据需要补充指定时间点截图。截图保留原分辨率，不叠加时间水印。没有 OCR 或视觉语义识别模块。

原始输入文件只读，不会复制、移动或修改。异步任务运行期间请保留输入文件。

## HTTP API

两项服务均提供 `GET /health`、`POST /api/jobs`、`GET /api/jobs/<id>`、`GET /output/<id>/<filename>`。

提交音频任务到 8765：

```json
{"source":"D:\\素材\\教学.mp4","options":{"transcribe":true,"language":"auto"}}
```

提交画面任务到 8766：

```json
{"source":"D:\\素材\\教学.mp4","options":{"mode":"timestamps","timestamps":[0,12.5,38]}}
```

`source` 必须是现存本机文件的绝对路径，API 不接受远程 URL 或浏览器 Origin 请求。音频可加 `stream_index` 选择 metadata 中指定音轨，默认第一个音轨；画面使用第一个视频流。画面模式参数：interval 使用 `interval`，scene 使用 `threshold`，timestamps 使用 `timestamps`，都支持 `max_frames`。

供 Watchless 等调用方进行视觉分析时，还支持 `keyframes`（编码关键帧）和 `scan`（连续解码后定频采样，使用 `start`、`end`、`interval`）。画面可选 `format: "png"` 无损输出、`crop: [x,y,width,height]` 像素裁剪、`size: [width,height]` 缩放和 `grayscale: true` 灰度输出。服务仅提供原始画面变换，SSIM 和内容判断留给调用方。`scan` 时间索引为采样网格时间；`keyframes` 保留解码帧时间。

提交返回 HTTP 202、`id`、`output` 和 `status_url`。参数格式问题返回 400；媒体处理错误在任务状态中返回 `failed`。任务状态为 `queued`、`running`、`done`、`failed`。同一服务的任务串行处理，两项服务独立运行。重启后已完成任务仍可查询，未完成任务明确标为中断。

## 模块与维护

| 模块 | 职责 |
| --- | --- |
| `media.py` | 读取媒体信息、音轨/WAV 提取、三种画面提取方式 |
| `asr.py` | 可选本地 Qwen 识别，逐段读 WAV，限制长音频内存使用 |
| `service.py` | 共用的本地任务 API、串行队列、结果状态保存 |
| `audio_service.py` / `frame_service.py` | 两个服务入口 |
| `extract.py` | Skill 的统一调用入口及素材清单 |
| `services.ps1` / `scripts/launch_service.py` | 后台启停、状态、进程归属检查 |

专用环境、模型、日志、输出均在本子目录内。运行依赖锁定为 12 个包；重建 Python 3.10 环境运行 `setup.cmd`。语音识别使用本地 Qwen3-ASR 0.6B INT8。模型缺失不会自动降级或联网下载；`--no-transcript` 仍可提取音轨。

测试：`.\.venv\Scripts\python.exe -m unittest discover -s tests -v`。`tests/fixtures/sample.mp4` 是模型自带语音片段与合成画面的测试样本。
