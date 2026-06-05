# LiveTalking

面向情绪数字人的工程化运行与素材生成项目。当前主链路为：

```text
TTS(indextts2) -> ASR(museasr) -> Renderer(musetalk) -> WebRTC / RTCPush / HTTPFile
```

项目支持三类核心场景：

- 实时数字人会话：WebRTC 或 RTCPush 输出。
- HTTPFile 异步任务：提交文本，生成 MP4 或 WAV 文件。
- Avatar 素材生成：上传用户图片，批量生成用户专属 MuseTalk 数字人资源。

## 快速启动

准备环境：

```bash
conda activate livetalking
```

启动 HTTPFile 模式：

```bash
python app.py --config config/httpfile.yaml
```

启动 RTCPush 模式：

```bash
python app.py --config config/rtcpush.yaml
```

启动 WebRTC 模式：

```bash
python app.py --config config/webrtc.yaml
```

常用页面：

- 导航页：`http://<server-ip>:6006/`
- HTTP 视频任务：`http://<server-ip>:6006/httpfile.html`
- HTTP 纯音频任务：`http://<server-ip>:6006/audiofile.html`
- Avatar 素材生成：`http://<server-ip>:6006/avatar_jobs.html`
- WebRTC 示例：`http://<server-ip>:6006/webrtcapi.html`
- RTCPush 示例：`http://<server-ip>:6006/rtcpushapi.html`

## 核心目录

```text
app.py
core/
  config.py                    # YAML 配置加载
  plugin_system.py             # 插件注册/创建
  runtime/
    tts/base.py
    asr/base.py
    renderer/base.py
plugins/
  tts/indextts2.py             # 当前 TTS
  asr/musetalk_asr.py          # 当前 ASR
  renderer/musetalk/
    runtime.py                 # MuseTalk 会话与共享模型资源
    assets.py                  # 用户资源/transition 加载
    inference_workers.py       # 推理 worker
server/
  http_routes.py               # HTTP 接口
  video_jobs.py                # HTTPFile MP4 任务
  audio_jobs.py                # HTTPFile WAV 任务
  avatar_jobs.py               # Avatar 素材生成任务
docs/
  httpfile_jobs_api.md
  avatar_jobs_api.md
```

## 用户资源结构

当前推荐资源结构按用户隔离：

```text
data/{user_id}/
  avatar_profile.json
  avatars/
    EMOTIONAL_FLOODING/
    RIGID_DEFENSE/
    WAVERING_DOUBT/
    OPEN_ACCEPTANCE/
    RELIEF_GROWTH/
  transitions/
    DEFAULT2EMOTIONAL_FLOODING/
    EMOTIONAL_FLOODING2DEFAULT/
```

说明：

- `avatar_profile.json` 当前用于绑定 TTS 音色，示例：`{"user_id":"user_ccy","voice":"male.wav"}`。
- `video_jobs` 会按 `user_id` 读取 `avatar_profile.json`，`voice=male.wav` 使用 `tts.default_male`，`voice=female.wav` 使用 `tts.default_female`。
- profile 缺失或 voice 非法时回落到男声；如果对应参考音频文件不存在，会直接报错。
- `DEFAULT` 是运行时基线/回落状态，不再作为 avatar job 默认生成情绪。
- `data/avatars` 和 `data/transitions` 是 legacy 兼容目录。
- 非默认用户不会自动 fallback 到 legacy transition，缺 transition 时会跳过 transition 插入。
- 新资源生成完成后会尝试热重载对应 `user_id` 的 MuseTalk 资源。

## 情绪枚举

业务 avatar 默认只生成五个阶段：

```text
EMOTIONAL_FLOODING
RIGID_DEFENSE
WAVERING_DOUBT
OPEN_ACCEPTANCE
RELIEF_GROWTH
```

`DEFAULT` 仍保留为 TTS/渲染基线状态。

## 配置重点

配置入口统一为：

```bash
python app.py --config <yaml>
```

常用配置片段：

```yaml
renderer:
  user_id: default
  multi_avatar: true
  batch_size: 16
  enabletransition: false

tts:
  default_male: data/audios/male.wav
  default_female: data/audios/female.wav

transport:
  mode: httpfile
  httpfile_batch_cap: 8

avatar_jobs:
  enable_transition: false
  continue_on_error: false
  emotion_prompts:
    EMOTIONAL_FLOODING: ""
    RIGID_DEFENSE: ""
    WAVERING_DOUBT: ""
    OPEN_ACCEPTANCE: ""
    RELIEF_GROWTH: ""
```

两个 transition 开关含义不同：

| 配置 | 默认 | 作用 |
| --- | --- | --- |
| `renderer.enabletransition` | `true` | 运行时播报是否插入 transition 帧 |
| `avatar_jobs.enable_transition` | `false` | Avatar 素材生成时是否调用 API 生成 transition 资源 |
| `avatar_jobs.continue_on_error` | `false` | Avatar 素材生成失败后是否继续后续情绪；默认失败即终止整个 job |

当 `avatar_jobs.enable_transition=true` 时，只为每个业务情绪生成：

- `DEFAULT2{EMOTION}`
- `{EMOTION}2DEFAULT`

不会生成业务情绪之间互相切换的 transition。

## HTTP 接口文档

上游或前端对接优先看：

- [HTTPFile Video/Audio Jobs](docs/httpfile_jobs_api.md)
- [Avatar Jobs](docs/avatar_jobs_api.md)

常用接口：

| 接口 | 说明 |
| --- | --- |
| `POST /video_jobs` | 提交文本生成 MP4 |
| `GET /video_jobs/{job_id}` | 查询 MP4 任务 |
| `GET /video_jobs/{job_id}/file` | 下载/预览 MP4 |
| `POST /audio_jobs` | 提交文本生成 WAV |
| `GET /audio_jobs/{job_id}` | 查询 WAV 任务 |
| `GET /audio_jobs/{job_id}/file` | 下载/预览 WAV |
| `GET /avatar_jobs/options` | 获取 avatar 生成选项 |
| `POST /avatar_jobs` | 上传图片并提交素材生成任务 |
| `GET /avatar_jobs/{job_id}` | 查询素材生成任务 |
| `POST /avatar_resources/reload` | 热重载指定用户资源 |

统一返回：

```json
{"code":0,"msg":"ok","data":{}}
```

失败返回：

```json
{"code":-1,"msg":"错误信息"}
```

## Avatar 素材生成链路

1. 前端上传用户图片到 `POST /avatar_jobs`。
2. 后端为每个业务情绪上传驱动视频并调用外部工作流 API。
3. API 返回视频后保存到 `assets/user/{user_id}/{EMOTION}_{job}.mp4`。
4. 后端调用 `genavatar_musetalk.py` 生成 MuseTalk 资源到 `data/{user_id}/avatars/{EMOTION}`。
5. 如果 `avatar_jobs.enable_transition=true`，额外生成 `DEFAULT <-> EMOTION` transition 视频并调用 `gen_transition.py` 转帧到 `data/{user_id}/transitions`。
6. 资源完成后尝试热重载 renderer 资源缓存。

## HTTPFile 任务链路

视频：

```text
POST /video_jobs -> tmp/video_jobs/{job_id}.mp4
```

音频：

```text
POST /audio_jobs -> tmp/audio_jobs/{job_id}.wav
```

`video_jobs` 推荐传 `user_id`，用于选择 `data/{user_id}` 下的数字人资源；不传时使用配置中的 `renderer.user_id`。TTS 音色也由该 `user_id` 对应的 `data/{user_id}/avatar_profile.json` 决定。

## TODO

- `audio_jobs` 支持 `user_id`，按用户 profile 选择音色。
- WebRTC/RTCPush 实时会话完整接入用户 profile 音色选择。
- Avatar 生成接口和页面仅支持 `voice=male.wav/female.wav`，后续可扩展更多 voice 文件选择。

## 静态检查

常用检查：

```bash
python -m py_compile app.py core/config.py server/http_routes.py
python -m compileall -q core plugins server app.py
```

文档/代码改动后建议：

```bash
git diff --check
```

## 常见排障

- `unsupported emotion`：检查 emotion 是否为枚举名或支持的中文别名。
- `job is not ready`：任务还在 `queued/running`，等待成功后再下载文件。
- `result video url not found`：外部工作流返回结果中没有可用 output 视频 URL。
- 生成了默认人物：确认请求传了正确 `user_id`，并检查 `data/{user_id}/avatars/{EMOTION}` 是否存在。
- 缺 transition：如果 `renderer.enabletransition=false`，运行时不会插 transition；如果用户 transition 不存在，也会跳过插入。

## 二次开发

新增插件遵循现有三类扩展点：

- TTS：继承 `core.runtime.tts.base.BaseTTS`，注册 `PluginType.TTS`。
- ASR：继承 `core.runtime.asr.base.BaseASR`，注册 `PluginType.ASR`。
- Renderer：继承 `core.runtime.renderer.base.BaseReal`，注册 `PluginType.RENDERER`。

更多架构说明见 [docs/refactor/README.md](docs/refactor/README.md)。
