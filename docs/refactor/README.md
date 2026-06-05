# LiveTalking 架构说明

本文档描述当前代码实际结构，供维护和二次开发使用。

## 1. 当前主链路

```text
TTS(indextts2) -> ASR(museasr) -> MuseTalk Renderer -> WebRTC / RTCPush / HTTPFile
```

插件族：

- `tts`
- `asr`
- `renderer`

传输方式由 `transport.mode` 控制，不再作为插件族扩展。

## 2. 启动流程

1. `app.py` 读取 `--config <yaml>`。
2. `core.config.load_app_config()` 将 YAML 转为结构化配置。
3. 插件系统校验并注册内置插件。
4. Renderer 类执行 `prepare_shared(config)`，加载进程级重资源。
5. HTTP 服务注册路由。
6. 根据模式启动 `video_jobs/audio_jobs/avatar_jobs` 等后台任务管理器。

关键点：

- MuseTalk 模型只在进程级共享资源中加载一次。
- 每个会话只创建轻量 session，并复用共享模型。
- 用户 avatar 资源按需缓存并支持 reload。

## 3. 核心目录职责

```text
core/
  config.py                    # YAML 配置结构
  plugin_system.py             # 插件注册与发现
  contracts.py                 # eventpoint 归一化
  runtime/
    tts/base.py
    asr/base.py
    renderer/base.py

plugins/
  tts/indextts2.py             # TTS 与 transition silence 事件
  asr/musetalk_asr.py          # 音频帧到特征队列
  renderer/musetalk/
    runtime.py                 # 共享资源、session、渲染入口
    assets.py                  # avatar/transition 资源加载
    inference_workers.py       # 单/多 avatar 推理 worker

server/
  http_routes.py               # HTTP 路由
  rtc_runtime.py               # 会话创建与 user_id 注入
  video_jobs.py                # HTTPFile MP4 任务
  audio_jobs.py                # HTTPFile WAV 任务
  avatar_jobs.py               # Avatar 素材生成任务
```

## 4. 用户资源模型

推荐结构：

```text
data/{user_id}/
  avatar_profile.json
  avatars/{EMOTION}/
  transitions/{FROM}2{TO}/
```

legacy 结构：

```text
data/avatars/{EMOTION}/
data/transitions/{FROM}2{TO}/
```

当前策略：

- `avatar_profile.json` 当前用于绑定 TTS 音色。
- `video_jobs` 会按 `user_id` 读取 profile，`voice=male.wav` 使用 `tts.default_male`，`voice=female.wav` 使用 `tts.default_female`。
- profile 缺失或 voice 非法时 fallback 到男声；参考音频不存在时任务失败。
- 默认用户可使用 legacy 路径兼容旧素材。
- 非默认用户优先使用 `data/{user_id}`。
- 非默认用户缺 transition 时不 fallback 到 legacy transition。
- 缺 transition 时运行时会跳过 transition 插入，继续使用目标数字人。

## 5. 多用户资源加载

`plugins/renderer/musetalk/runtime.py` 中的 `MuseTalkModelResource` 管理共享模型和用户资源缓存。

缓存 key：

```text
(user_id, enable_transition)
```

这样可以避免同一进程内不同 transition 开关的 session 互相污染。

热重载入口：

```http
POST /avatar_resources/reload
```

资源生成完成后，`server/avatar_jobs.py` 会尝试自动调用 renderer reload。

## 6. transition 开关

有两个独立开关：

| 配置 | 所属模块 | 作用 |
| --- | --- | --- |
| `renderer.enabletransition` | runtime | 播报时是否插入 transition |
| `avatar_jobs.enable_transition` | 素材生成 | 是否调用外部 API 生成 transition 资源 |

`avatar_jobs.enable_transition=true` 时只生成：

```text
DEFAULT2{EMOTION}
{EMOTION}2DEFAULT
```

不会生成业务情绪之间的两两 transition。

## 7. HTTPFile 任务

`transport.mode=httpfile` 时启用：

- `server.video_jobs.VideoJobManager`
- `server.audio_jobs.AudioJobManager`

视频任务：

```text
POST /video_jobs -> tmp/video_jobs/{job_id}.mp4
```

音频任务：

```text
POST /audio_jobs -> tmp/audio_jobs/{job_id}.wav
```

`video_jobs` 会为每个任务临时创建 session，并通过 `user_id` 选择用户资源。
TTS 音色也通过同一个 `user_id` 读取 `data/{user_id}/avatar_profile.json`。

## 8. Avatar Jobs 任务

`server.avatar_jobs.AvatarJobManager` 负责：

1. 上传用户图片。
2. 为业务情绪上传 driving video。
3. 调外部 workflow API 生成 emotion 视频。
4. 保存视频到 `assets/user/{user_id}`。
5. 调 `genavatar_musetalk.py` 生成 `data/{user_id}/avatars/{EMOTION}`。
6. 可选生成 `DEFAULT <-> EMOTION` transition。
7. 热重载 renderer 用户资源。
8. 写入 `data/{user_id}/avatar_profile.json`，当前默认 `voice=male.wav`。

业务情绪来源：

```python
EMOTION_SEQUENCE
```

`DEFAULT` 不作为 avatar job 默认生成情绪。

## 9. 扩展指南

新增 TTS：

1. 在 `plugins/tts/` 新增模块。
2. 继承 `BaseTTS`。
3. 使用 `@register(PluginType.TTS, "name")`。
4. 实现 `txt_to_audio`。

新增 ASR：

1. 在 `plugins/asr/` 新增模块。
2. 继承 `BaseASR`。
3. 使用 `@register(PluginType.ASR, "name")`。
4. 实现 `run_step`。

新增 Renderer：

1. 在 `plugins/renderer/<name>/runtime.py` 新增实现。
2. 继承 `BaseReal`。
3. 注册 `PluginType.RENDERER`。
4. 实现 `prepare_shared` 和 `create_session`。

## 10. 校验命令

```bash
python -m py_compile app.py core/config.py server/http_routes.py
python -m compileall -q core plugins server app.py
git diff --check
```

启动冒烟：

```bash
PORT=6006 STARTUP_TIMEOUT_SEC=300 \
SMOKE_CONFIG=config/webrtc.yaml \
bash scripts/smoke/startup_smoke.sh
```

## 11. 维护注意事项

- 修改接口时同步更新 `docs/httpfile_jobs_api.md` 或 `docs/avatar_jobs_api.md`。
- 修改用户资源目录时同步检查 `plugins/renderer/musetalk/assets.py`。
- 修改 emotion 枚举时同步检查 `data.py`、avatar job 默认列表和前端下拉项。
- 修改 transition 行为时同时确认 runtime 开关和 avatar job 生成开关。
- 后续 TODO：`audio_jobs`、WebRTC/RTCPush 需要继续接入用户 profile；Avatar profile 后续可扩展更多 voice 文件选择。
