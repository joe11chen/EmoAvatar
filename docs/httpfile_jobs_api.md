# HTTPFile Jobs 接口文档

本文档覆盖 `transport.mode=httpfile` 下的异步任务接口：

- `video_jobs`：生成数字人视频，输出 MP4。
- `audio_jobs`：仅生成语音，输出 WAV。

## 1. 前置条件

服务需使用 HTTPFile 配置启动：

```bash
python app.py --config config/httpfile.yaml
```

Base URL 以部署环境为准，例如：

```text
http://127.0.0.1:6006
```

POST 请求使用：

```http
Content-Type: application/json
```

## 2. 通用约定

成功：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {}
}
```

失败：

```json
{
  "code": -1,
  "msg": "错误信息"
}
```

任务状态：

| 状态 | 说明 |
| --- | --- |
| `queued` | 排队中 |
| `running` | 执行中 |
| `succeeded` | 成功 |
| `failed` | 失败，查看 `error` 字段 |

支持的 emotion：

```text
DEFAULT
EMOTIONAL_FLOODING
RIGID_DEFENSE
WAVERING_DOUBT
OPEN_ACCEPTANCE
RELIEF_GROWTH
```

不传或空字符串时，后端按 `DEFAULT` 处理。

## 3. Video Jobs

### 3.1 提交视频任务

`POST /video_jobs`

请求体：

```json
{
  "text": "你好，这是一个视频任务。",
  "emotion": "EMOTIONAL_FLOODING",
  "user_id": "user_ccy"
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `text` | string | 是 | 待播报文本 |
| `emotion` | string | 否 | 情绪枚举；不传默认 `DEFAULT` |
| `user_id` | string | 否 | 指定使用 `data/{user_id}` 下的数字人资源；不传使用配置 `renderer.user_id` |

TTS 音色选择：

- `video_jobs` 会读取 `data/{user_id}/avatar_profile.json`。
- `voice=male.wav` 使用配置 `tts.default_male`。
- `voice=female.wav` 使用配置 `tts.default_female`。
- profile 缺失或 voice 非法时 fallback 到男声。
- 对应参考音频文件不存在时任务失败。

成功响应：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "job_id": "b6c0c2e0f3d6424ea6a0f86e8e7d2d41",
    "status": "queued"
  }
}
```

### 3.2 查询视频任务

`GET /video_jobs/{job_id}`

响应示例：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "job_id": "b6c0c2e0f3d6424ea6a0f86e8e7d2d41",
    "status": "succeeded",
    "text": "你好，这是一个视频任务。",
    "emotion": "EMOTIONAL_FLOODING",
    "user_id": "user_ccy",
    "created_at": "2026-06-05T08:00:00+00:00",
    "updated_at": "2026-06-05T08:00:05+00:00",
    "file_path": "tmp/video_jobs/b6c0c2e0f3d6424ea6a0f86e8e7d2d41.mp4",
    "error": null
  }
}
```

### 3.3 获取视频文件

`GET /video_jobs/{job_id}/file`

说明：

- 任务未完成时返回 `code=-1`。
- 成功时返回 MP4 文件流。
- 追加 `?download=1` 会使用 `{job_id}.mp4` 作为下载文件名。

下载示例：

```bash
curl -L -o out.mp4 \
  "http://127.0.0.1:6006/video_jobs/<job_id>/file?download=1"
```

## 4. Audio Jobs

### 4.1 提交音频任务

`POST /audio_jobs`

请求体：

```json
{
  "text": "你好，这是一个纯音频任务。",
  "emotion": "OPEN_ACCEPTANCE"
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `text` | string | 是 | 待合成文本 |
| `emotion` | string | 否 | 情绪枚举；不传默认 `DEFAULT` |

成功响应：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "job_id": "8dc9efc8ebfe4a1da06e32887e2b6e4d",
    "status": "queued"
  }
}
```

### 4.2 查询音频任务

`GET /audio_jobs/{job_id}`

响应示例：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "job_id": "8dc9efc8ebfe4a1da06e32887e2b6e4d",
    "status": "succeeded",
    "text": "你好，这是一个纯音频任务。",
    "emotion": "OPEN_ACCEPTANCE",
    "created_at": "2026-06-05T08:05:00+00:00",
    "updated_at": "2026-06-05T08:05:02+00:00",
    "file_path": "tmp/audio_jobs/8dc9efc8ebfe4a1da06e32887e2b6e4d.wav",
    "error": null
  }
}
```

### 4.3 获取音频文件

`GET /audio_jobs/{job_id}/file`

说明：

- 任务未完成时返回 `code=-1`。
- 成功时返回 WAV 文件流。
- 追加 `?download=1` 会使用 `{job_id}.wav` 作为下载文件名。

下载示例：

```bash
curl -L -o out.wav \
  "http://127.0.0.1:6006/audio_jobs/<job_id>/file?download=1"
```

## 5. 推荐调用流程

视频任务：

1. 调用 `POST /video_jobs`，拿到 `job_id`。
2. 每 1 到 2 秒轮询 `GET /video_jobs/{job_id}`。
3. `status=succeeded` 后请求 `GET /video_jobs/{job_id}/file` 获取文件。

音频任务：

1. 调用 `POST /audio_jobs`，拿到 `job_id`。
2. 每 1 到 2 秒轮询 `GET /audio_jobs/{job_id}`。
3. `status=succeeded` 后请求 `GET /audio_jobs/{job_id}/file` 获取文件。

## 6. Curl 示例

提交视频：

```bash
curl -s http://127.0.0.1:6006/video_jobs \
  -H 'Content-Type: application/json' \
  -d '{"text":"你好，这是视频任务。","emotion":"EMOTIONAL_FLOODING","user_id":"user_ccy"}'
```

查询视频：

```bash
curl -s http://127.0.0.1:6006/video_jobs/<job_id>
```

提交音频：

```bash
curl -s http://127.0.0.1:6006/audio_jobs \
  -H 'Content-Type: application/json' \
  -d '{"text":"你好，这是纯音频任务。","emotion":"OPEN_ACCEPTANCE"}'
```

查询音频：

```bash
curl -s http://127.0.0.1:6006/audio_jobs/<job_id>
```

## 7. 常见错误

- `text is required`：`text` 为空。
- `unsupported emotion: XXX`：`emotion` 非法。
- `invalid job_id: ...`：任务 ID 不存在。
- `job is not ready: status=running`：任务未完成就请求文件。
- `video_jobs API is only available when transport.mode=httpfile`：当前不是 HTTPFile 模式。
- `audio_jobs API is only available when transport.mode=httpfile`：当前不是 HTTPFile 模式。

## 8. 资源与 transition 说明

`video_jobs` 使用的数字人资源来自：

```text
data/{user_id}/avatars/{EMOTION}
data/{user_id}/transitions/{FROM}2{TO}
```

如果没有指定 `user_id`，使用配置中的 `renderer.user_id`。

运行时 transition 插入由 `renderer.enabletransition` 控制：

- `true`：情绪变化时尝试插入 transition。
- `false`：跳过所有 transition 插入。

即使开关为 `true`，如果对应用户没有 transition 资源，也会跳过 transition，不会强制回退到 legacy transition。

## 9. TODO

- `audio_jobs` 后续支持 `user_id` 并按用户 profile 选择音色。
- WebRTC/RTCPush 实时会话后续完整接入用户 profile 音色选择。
