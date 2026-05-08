# LiveTalking 上游接口文档（Video/Audio Jobs）

本文档仅覆盖 `httpfile` 模式下的异步任务接口：

- `video_jobs`：生成数字人视频（MP4）
- `audio_jobs`：生成纯音频（WAV）

---

## 1. 前置条件
 
1. Base URL 示例：`http://<server-ip>:6006`。  
2. 请求头：`Content-Type: application/json`（POST 接口）。  

---

## 2. 通用约定

### 2.1 统一返回格式

- 成功：`{"code":0,"msg":"ok","data":...}`
- 失败：`{"code":-1,"msg":"..."}`

### 2.2 任务状态

- `queued`：排队中
- `running`：执行中
- `succeeded`：成功
- `failed`：失败（`error` 字段会有失败原因）

### 2.3 emotion 参数

推荐传枚举名（不区分大小写）：

- `DEFAULT`
- `CRY`
- `ANGRY`
- `HAPPY`
- `EMOTIONAL`

不传或空字符串时默认 `DEFAULT`。

---

## 3. Video Jobs（数字人视频）

### 3.1 提交任务

`POST /video_jobs`

请求体：

```json
{
  "text": "你好，这是一个视频任务。",
  "emotion": "DEFAULT"
}
```

字段说明：

- `text`：`string`，必填，待播报文本
- `emotion`：`string`，可选，情绪类型

成功响应示例：

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

### 3.2 查询任务

`GET /video_jobs/{job_id}`

成功响应示例（字段可能随版本增加）：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "job_id": "b6c0c2e0f3d6424ea6a0f86e8e7d2d41",
    "status": "running",
    "text": "你好，这是一个视频任务。",
    "emotion": "DEFAULT",
    "created_at": "2026-05-08T20:00:00+00:00",
    "updated_at": "2026-05-08T20:00:03+00:00",
    "file_path": null,
    "error": null
  }
}
```

### 3.3 下载视频文件

`GET /video_jobs/{job_id}/file`

- 任务未完成会返回 `code=-1`
- 成功时返回 MP4 文件流
- 追加 `?download=1` 可强制下载文件名为 `{job_id}.mp4`

---

## 4. Audio Jobs（纯音频）

### 4.1 提交任务

`POST /audio_jobs`

请求体：

```json
{
  "text": "你好，这是一个纯音频任务。",
  "emotion": "HAPPY"
}
```

字段说明：

- `text`：`string`，必填，待合成文本
- `emotion`：`string`，可选，情绪类型

成功响应示例：

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

### 4.2 查询任务

`GET /audio_jobs/{job_id}`

成功响应示例（字段可能随版本增加）：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "job_id": "8dc9efc8ebfe4a1da06e32887e2b6e4d",
    "status": "succeeded",
    "text": "你好，这是一个纯音频任务。",
    "emotion": "HAPPY",
    "created_at": "2026-05-08T20:05:00+00:00",
    "updated_at": "2026-05-08T20:05:02+00:00",
    "file_path": "tmp/audio_jobs/8dc9efc8ebfe4a1da06e32887e2b6e4d.wav",
    "error": null
  }
}
```

### 4.3 下载音频文件

`GET /audio_jobs/{job_id}/file`

- 任务未完成会返回 `code=-1`
- 成功时返回 WAV 文件流
- 追加 `?download=1` 可强制下载文件名为 `{job_id}.wav`

---

## 5. 推荐调用流程（上游）

### 5.1 视频任务

1. `POST /video_jobs` 提交文本，拿到 `job_id`。  
2. 每 1~2 秒轮询 `GET /video_jobs/{job_id}`。  
3. `status=succeeded` 后请求 `GET /video_jobs/{job_id}/file` 下载文件。  

### 5.2 纯音频任务

1. `POST /audio_jobs` 提交文本，拿到 `job_id`。  
2. 每 1~2 秒轮询 `GET /audio_jobs/{job_id}`。  
3. `status=succeeded` 后请求 `GET /audio_jobs/{job_id}/file` 下载文件。  

---

## 6. Curl 示例

### 6.1 提交视频任务

```bash
curl -s http://127.0.0.1:6006/video_jobs \
  -H 'Content-Type: application/json' \
  -d '{"text":"你好，这是视频任务。","emotion":"DEFAULT"}'
```

### 6.2 查询视频任务

```bash
curl -s http://127.0.0.1:6006/video_jobs/<job_id>
```

### 6.3 下载视频

```bash
curl -L -o out.mp4 "http://127.0.0.1:6006/video_jobs/<job_id>/file?download=1"
```

### 6.4 提交纯音频任务

```bash
curl -s http://127.0.0.1:6006/audio_jobs \
  -H 'Content-Type: application/json' \
  -d '{"text":"你好，这是纯音频任务。","emotion":"HAPPY"}'
```

### 6.5 查询纯音频任务

```bash
curl -s http://127.0.0.1:6006/audio_jobs/<job_id>
```

### 6.6 下载音频

```bash
curl -L -o out.wav "http://127.0.0.1:6006/audio_jobs/<job_id>/file?download=1"
```

---

## 7. 常见错误

- `{"code":-1,"msg":"text is required"}`  
  `text` 为空。

- `{"code":-1,"msg":"unsupported emotion: XXX"}`  
  `emotion` 非法。

- `{"code":-1,"msg":"invalid job_id: ..."}`
  任务 ID 不存在。

- `{"code":-1,"msg":"job is not ready: status=running"}`
  任务未完成就下载文件。

- `{"code":-1,"msg":"video_jobs API is only available when transport.mode=httpfile"}`
  当前服务不是 `httpfile` 模式。

