# Avatar Jobs 接口文档

本文档面向前端和上游系统，描述上传用户图片并生成 MuseTalk 数字人资源的异步任务接口。

## 1. 通用约定

Base URL 以部署环境为准，例如：

```text
http://127.0.0.1:6006
```

成功返回：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {}
}
```

失败返回：

```json
{
  "code": -1,
  "msg": "错误信息"
}
```

## 2. 业务情绪

Avatar job 默认只生成以下五个业务情绪：

```text
EMOTIONAL_FLOODING
RIGID_DEFENSE
WAVERING_DOUBT
OPEN_ACCEPTANCE
RELIEF_GROWTH
```

`DEFAULT` 是运行时基线状态，不作为 avatar job 默认生成情绪。即使请求中传入 `DEFAULT`，后端也会过滤。

## 3. 获取可生成选项

`GET /avatar_jobs/options`

用于初始化前端页面，获取可选情绪、驱动视频是否存在、prompt 配置和 transition 生成开关。

响应示例：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "emotions": [
      {
        "name": "EMOTIONAL_FLOODING",
        "video_path": "assets/EMOTIONAL_FLOODING.mp4",
        "available": true
      }
    ],
    "enable_transition": false,
    "positive_prompt_template": "a person speaking with {emotion} emotion",
    "emotion_prompts": {
      "EMOTIONAL_FLOODING": "custom prompt"
    },
    "negative_prompt": "low quality, blurry, overexposed, subtitles, text, watermark"
  }
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `emotions[].name` | 情绪枚举名 |
| `emotions[].video_path` | 当前情绪驱动视频路径 |
| `emotions[].available` | 驱动视频是否存在；不存在时前端应禁选 |
| `enable_transition` | 当前 avatar job 是否会生成 transition 资源 |
| `positive_prompt_template` | 全局正向 prompt 模板 |
| `emotion_prompts` | 每个情绪的默认 prompt 覆盖 |
| `negative_prompt` | 默认负向 prompt |

## 4. 提交 Avatar 生成任务

`POST /avatar_jobs`

请求类型：`multipart/form-data`

请求字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `image` | File | 是 | 用户上传的人物图片 |
| `avatar_id` | string | 建议必填 | 用户/数字人资源 ID；生成到 `data/{avatar_id}` |
| `emotions` | string | 否 | JSON 数组或逗号分隔情绪列表；不传时生成五个业务情绪 |
| `positive_prompt` | string | 否 | 覆盖所有 emotion 的正向 prompt |
| `negative_prompt` | string | 否 | 覆盖默认负向 prompt |
| `voice` | string | 否 | 仅支持 `male.wav` 或 `female.wav`；默认 `male.wav`，会写入 `avatar_profile.json` |

`emotions` 支持两种格式：

```text
EMOTIONAL_FLOODING,RIGID_DEFENSE
```

```json
["EMOTIONAL_FLOODING", "RIGID_DEFENSE"]
```

Prompt 优先级：

1. 请求字段 `positive_prompt`
2. 配置 `avatar_jobs.emotion_prompts.{EMOTION}`
3. 配置 `avatar_jobs.positive_prompt_template`
4. emotion 名称

当配置 `avatar_jobs.enable_transition=true` 时，后端只为每个业务情绪提交两类 transition 生成任务：

- `DEFAULT2{EMOTION}`
- `{EMOTION}2DEFAULT`

当配置为 `false` 时，不提交任何 transition 生成任务。

失败策略：

- 默认 `avatar_jobs.continue_on_error=false`，任意 emotion 视频生成、transition 生成、MuseTalk 生成失败都会立即终止整个 job。
- 中断后，当前失败项状态为 `failed`，尚未开始的 emotion/transition 状态会标记为 `skipped`。
- 只有显式配置 `avatar_jobs.continue_on_error=true` 时，才会继续后续情绪，并可能返回整体状态 `partial`。

curl 示例：

```bash
curl -X POST http://127.0.0.1:6006/avatar_jobs \
  -F "image=@/path/to/person.png" \
  -F "avatar_id=user_ccy" \
  -F "voice=male.wav" \
  -F "emotions=EMOTIONAL_FLOODING,RIGID_DEFENSE"
```

成功响应：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "job_id": "f57b2624aabbccdd",
    "status": "queued"
  }
}
```

## 5. 查询 Avatar 任务状态

`GET /avatar_jobs/{job_id}`

响应字段：

| 字段 | 说明 |
| --- | --- |
| `job_id` | 任务 ID |
| `status` | 整体状态：`queued` / `running` / `succeeded` / `failed`；仅 `avatar_jobs.continue_on_error=true` 时可能出现 `partial` |
| `avatar_id` | 用户/数字人资源 ID |
| `emotions` | 本次生成的业务情绪 |
| `current_emotion` | 当前正在处理的 emotion 或 transition |
| `results` | emotion 生成的视频路径 |
| `transition_results` | transition 视频路径 |
| `emotion_status` | 每个 emotion 的阶段状态 |
| `transition_status` | 每个 transition 的阶段状态 |
| `errors` | 失败信息 |
| `extra` | manifest、资源目录、热重载结果等附加信息 |

emotion 状态：

| 状态 | 含义 |
| --- | --- |
| `queued` | 等待处理 |
| `api_submitted` | 已提交外部生成 API |
| `video_done` | 收到 emotion 视频结果 |
| `musetalk_submitted` | 已提交 MuseTalk 资源生成 |
| `musetalk_done` | MuseTalk 资源生成完成 |
| `skipped` | 前置任务失败，job 已中断 |
| `failed` | 失败 |

transition 状态：

| 状态 | 含义 |
| --- | --- |
| `queued` | 等待处理 |
| `transition_api_submitted` | 已提交 transition API |
| `transition_video_done` | 收到 transition 视频 |
| `transition_frames_submitted` | 已提交 transition 转帧 |
| `transition_done` | transition 帧资源生成完成 |
| `skipped` | 已跳过 |
| `failed` | 失败 |

响应示例：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "job_id": "f57b2624aabbccdd",
    "status": "running",
    "avatar_id": "user_ccy",
    "emotions": ["EMOTIONAL_FLOODING"],
    "results": {
      "EMOTIONAL_FLOODING": "assets/user/user_ccy/EMOTIONAL_FLOODING_f57b2624.mp4"
    },
    "emotion_status": {
      "EMOTIONAL_FLOODING": {
        "status": "musetalk_done",
        "message": "已完成MuseTalk资源生成",
        "video_path": "assets/user/user_ccy/EMOTIONAL_FLOODING_f57b2624.mp4",
        "avatar_id": "EMOTIONAL_FLOODING",
        "avatar_dir": "data/user_ccy/avatars/EMOTIONAL_FLOODING",
        "user_id": "user_ccy"
      }
    },
    "transition_results": {},
    "transition_status": {},
    "errors": {}
  }
}
```

## 6. 轮询建议

提交任务后每 1 到 2 秒请求：

```http
GET /avatar_jobs/{job_id}
```

当 `data.status` 为以下状态时停止轮询：

- `succeeded`
- `failed`

仅当后端显式配置 `avatar_jobs.continue_on_error=true` 时，`partial` 也属于终态。

如果需要展示单个情绪进度，可读取 `data.emotion_status.{EMOTION}.status`。出现 `musetalk_done` 表示该情绪资源已经生成完成。

## 7. 资源输出路径

用户 profile：

```text
data/{avatar_id}/avatar_profile.json
```

当前 avatar job 生成完成后会按请求的 `voice` 写入；不传时默认 `male.wav`：

```json
{
  "user_id": "{avatar_id}",
  "voice": "male.wav"
}
```

中间 emotion 视频：

```text
assets/user/{avatar_id}/{EMOTION}_{job_id前8位}.mp4
```

MuseTalk avatar 资源：

```text
data/{avatar_id}/avatars/{EMOTION}
```

transition 视频，如果开启：

```text
assets/user/{avatar_id}/transitions/{FROM}2{TO}_{job_id前8位}.mp4
```

transition 帧资源，如果开启：

```text
data/{avatar_id}/transitions/{FROM}2{TO}
```

## 8. 热重载用户资源

`POST /avatar_resources/reload`

可在资源手动更新后通知 renderer 重新加载指定用户资源。

请求方式支持 JSON、form 或 query：

```json
{
  "user_id": "user_ccy"
}
```

响应示例：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "user_id": "user_ccy",
    "avatars": ["DEFAULT", "EMOTIONAL_FLOODING"],
    "refreshed_sessions": [123456]
  }
}
```

## 9. 常见错误

- `image is required`：未上传 `image` 字段。
- `unsupported emotion: XXX`：传入了不支持的情绪枚举。
- `emotion video missing in assets`：对应情绪的驱动视频不存在。
- `result video url not found`：外部工作流结果中未找到 output 类型视频。
- `invalid job_id`：任务 ID 不存在。

## 10. TODO

- `avatar_profile.json` 后续支持更多 voice 文件选择。
