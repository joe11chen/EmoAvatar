# LiveTalking（内部重构版）

本项目是面向“情感数字人实时推理”的工程化版本，当前主链路已统一为：

`TTS(indextts2) -> ASR(museasr) -> Renderer(musetalk) -> WebRTC/RTCPush 输出`

目标是让新同事可以快速接手、稳定运行、并在 `tts/asr/renderer` 三个模块内扩展。

---

## 1. 当前状态（请先看）

- 架构已收敛：仅保留三类插件
  - `tts`
  - `asr`
  - `renderer`（模型渲染主干）
- 启动入口统一：`--config <yaml>`
- 传输层不再插件化（直接走固定实现）
- 高频 frame 监控写入：`tmp/frame_monitor/session_<sessionid>.log`

---

## 2. 目录结构（核心）

```text
app.py                        # 启动入口
core/
  plugin_system.py            # 插件注册/创建/启动校验
  contracts.py                # 事件帧数据约定
  runtime/
    tts/base.py               # TTS 基类
    asr/base.py               # ASR 基类
    renderer/base.py          # Renderer 基类（主渲染循环）

plugins/
  tts/indextts2.py            # 当前 TTS 实现
  asr/musetalk_asr.py         # 当前 ASR 实现
  renderer/musetalk/
    runtime.py                # MuseTalk 渲染器
    inference_workers.py      # 推理 worker
    assets.py                 # 素材/过渡帧加载

server/
  runtime_context.py          # 运行上下文
  rtc_runtime.py              # 会话创建、推流会话
  http_routes.py              # HTTP/WebRTC 路由

scripts/smoke/startup_smoke.sh # 启动冒烟脚本
```

---

## 3. 快速启动

### 3.1 环境

```bash
conda activate livetalking
```

### 3.2 配置化启动（推荐）

默认配置文件：`config/app.yaml`

```bash
python app.py --config config/app.yaml
```

当前线上 RTCPush 示例配置：`config/rtcpush.yaml`

```bash
python app.py --config config/rtcpush.yaml
```

WebRTC 示例配置：`config/webrtc.yaml`

```bash
python app.py --config config/webrtc.yaml
```

访问：
- `http://<server-ip>:6006/webrtcapi.html`
- 推荐前端：`http://<server-ip>:6006/dashboard.html`

---

## 4. 冒烟与校验

### 4.1 编译校验

```bash
python -m py_compile app.py core/plugin_system.py
python -m compileall -q core plugins server app.py
```

### 4.2 启动冒烟

```bash
PORT=6006 STARTUP_TIMEOUT_SEC=300 bash scripts/smoke/startup_smoke.sh
```

---

## 5. 配置字段（严格模式）

- 命令行只保留一个参数：`--config`
- 业务参数全部从 YAML 读取，不再支持 CLI 覆盖

YAML 对应结构（节选）：

```yaml
plugins:
  tts: indextts2
  asr: museasr
  renderer: musetalk

renderer:
  avatar_id: avator_1
  multi_avatar: true
  batch_size: 16

transport:
  mode: rtcpush
  rtc_audio_queue_maxsize: 600
  rtc_video_queue_maxsize: 300
  push_url: http://localhost:1985/rtc/v1/whip/?app=live&stream=livestream

server:
  listenport: 6006
```

---

## 6. HTTP 接口（常用）

来自 `server/http_routes.py`：

- `POST /offer`：建立 WebRTC 会话
- `POST /human`：文本输入（echo/chat）
- `POST /humanaudio`：上传音频输入
- `POST /interrupt_talk`：打断当前播报
- `POST /set_audiotype`：切换自定义静默动作
- `POST /record`：开始/结束录制
- `POST /is_speaking`：查询当前是否在说话

---

## 7. 二次开发指南（新同事最常用）

### 7.1 新增 TTS

1. 在 `plugins/tts/` 新建模块  
2. 继承 `core.runtime.tts.base.BaseTTS`  
3. `@register(PluginType.TTS, "your_tts")`  
4. 实现 `txt_to_audio`

### 7.2 新增 ASR

1. 在 `plugins/asr/` 新建模块  
2. 继承 `core.runtime.asr.base.BaseASR`  
3. `@register(PluginType.ASR, "your_asr")`  
4. 实现 `run_step`

### 7.3 新增 Renderer（新 talk 模型）

1. 在 `plugins/renderer/<name>/runtime.py` 实现类  
2. 继承 `core.runtime.renderer.base.BaseReal` 并注册  
3. 实现类方法：
   - `register_dependencies`
   - `required_plugins`
   - `prepare_shared`
   - `create_session`
4. 在 `render` 中接入主循环

---

## 8. 常见排障

- 启动即失败：先看 `validate_startup_plugins` 报错（插件名错误最常见）
- 首帧慢：确认模型与素材目录完整，首次 warmup 正常
- 无画面/无音频：先跑 `startup_smoke.sh`，再看 `livetalking.log`
- 队列堆积：观察日志中的 `rtcpush producer stats` 与 `tmp/frame_monitor/*`

---

## 9. 维护约定

- 统一链路：只沿 `tts/asr/renderer` 扩展，不新增兼容分叉
- 变更后必须执行：
  1. `py_compile/compileall`
  2. `startup_smoke.sh`
- 文档与代码保持同步；架构变化先更新本 README
