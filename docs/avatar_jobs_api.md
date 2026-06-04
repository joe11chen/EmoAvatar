# Avatar 生成任务接口文档

## 通用格式

baseurl: u888898-jbca-203ff24a.westd.seetacloud.com:8443

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


## 1. 提交 Avatar 生成任务

`POST /avatar_jobs`

请求类型：`multipart/form-data`

请求字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `image` | File | 是 | 用户上传的人物图片 |
| `avatar_id` | string | 是 | 标识用户数字人的ID，不能重复 |
| `emotions` | string | 否 | 不填时默认生成五个stage情绪视频（枚举 `EMOTIONAL_FLOODING,RIGID_DEFENSE`等） |

说明：

- 默认五个情绪为 `EMOTIONAL_FLOODING`、`RIGID_DEFENSE`、`WAVERING_DOUBT`、`OPEN_ACCEPTANCE`、`RELIEF_GROWTH`。

curl 示例：

```bash
curl -X POST https://baseurl/avatar_jobs \
  -F "image=@/path/to/person.png" \
  -F "avatar_id=user_ccy" \
  -F "emotions=EMOTIONAL_FLOODING,RIGID_DEFENSE"
```

响应示例：

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

## 2. 查询 Avatar 任务状态

`GET /avatar_jobs/{job_id}`

响应字段：

| 字段 | 说明 |
| --- | --- |
| `status` | 任务整体状态：`queued` / `running` / `succeeded` / `partial` / `failed` |
| `emotions` | 本次生成的 emotion 列表 |
| `current_emotion` | 当前正在处理的 emotion 或 transition |
| `results` | emotion 生成的视频文件路径 |
| `emotion_status` | 每个 emotion 的阶段状态 |
| `transition_results` | transition 视频结果 |
| `transition_status` | 每个 transition 的阶段状态 |
| `errors` | 失败信息 |
| `extra` | manifest、reload 等附加信息 |

emotion_status 中可能出现的状态：

| 状态 | 含义 |
| --- | --- |
| `queued` | 等待处理 |
| `api_submitted` | 已提交生成 API |
| `video_done` | 已收到视频结果 |
| `musetalk_submitted` | 已提交 MuseTalk 资源生成 |
| `musetalk_done` | MuseTalk 资源生成完成 |
| `failed` | 失败 |

出现`musetalk_done`代表某个情绪的视频已经生成完成

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

    "emotion_status": {
      "EMOTIONAL_FLOODING": {
        "status": "musetalk_done",
        "message": "已完成MuseTalk资源生成",
        "video_path": "assets/user/user_ccy/EMOTIONAL_FLOODING_f57b2624.mp4",
        "avatar_dir": "data/user_ccy/avatars/EMOTIONAL_FLOODING"
      }
    },
    "transition_results": {},
    "transition_status": {},
    "errors": {}
  }
}
```

## 3. 前端轮询建议

提交任务后，每 1 到 2 秒请求：

```http
GET /avatar_jobs/{job_id}
```

当 `data.status` 为以下状态时停止轮询：

- `succeeded`
- `partial`
- `failed`