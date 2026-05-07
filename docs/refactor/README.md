# LiveTalking Refactor Docs (Current State)

本文档是当前版本的唯一重构说明入口，描述已经落地的实际架构与维护方式。

## 1. Current Status

- 主链路已收敛为：`TTS -> ASR -> MuseTalk Renderer`（输出模式支持 WebRTC/RTCPush）。
- 插件中心已统一为 `core/plugin_system.py`。
- 阶段性迁移文档已归档删除，避免历史状态与当前代码不一致。

## 2. Runtime Layout

```text
app.py
core/
  plugin_system.py
  contracts.py
  runtime/
    tts/base.py
    asr/base.py
    renderer/base.py
plugins/
  tts/indextts2.py
  asr/musetalk_asr.py
  renderer/musetalk/{runtime.py,assets.py,inference_workers.py}
server/
  runtime_context.py
  rtc_runtime.py
  http_routes.py
scripts/smoke/startup_smoke.sh
```

职责边界：
- `core/*`：基类、契约、插件注册与创建机制（通用能力）。
- `plugins/*`：具体实现（可替换能力）。
- `server/*`：会话创建、WebRTC 对接、HTTP 路由。

## 3. Startup and Session Flow

1. `app.py` 仅接收 `--config <yaml>`，严格按 YAML 读取配置。
2. `validate_startup_plugins(config)` 在启动时做插件可用性校验（fail fast）。
3. `renderer_cls = create(PluginType.RENDERER, config.plugins.renderer, instantiate=False)` 取到渲染器类。
4. `renderer_cls.prepare_shared(config)` 一次性加载进程级重资源（模型、素材）。
5. 每个会话由 `renderer_cls.create_session(session_config, prepared)` 创建。
6. `MuseReal.render()` 启动 TTS 线程、推理线程、帧处理线程并持续消费 ASR 步进。

关键点：
- 重模型不会按会话重复加载；会话只持有共享资源引用。

## 4. Plugin System (How It Works)

注册机制分两层：
- `@register(PluginType.X, "name")`：定义“如何注册”。
- `register_builtin_*_plugins()`：在启动时自动发现并导入模块，触发装饰器执行。

`core/plugin_system.py` 当前实现：
- 按目录自动发现模块（`pkgutil.iter_modules`）。
- `plugins.renderer` 支持包插件并优先导入 `<package>.runtime`。
- `_BOOTSTRAPPED` 标记保证每个插件族只初始化一次。

## 5. Active Built-in Plugins

- `tts`: `indextts2`
- `asr`: `museasr`
- `renderer`: `musetalk`

## 6. Extension Guide

### Add a new TTS

1. 在 `plugins/tts/` 新增模块文件（如 `mytts.py`）。
2. 类继承 `core.runtime.tts.base.BaseTTS`。
3. 使用 `@register(PluginType.TTS, "mytts")` 注册。
4. 实现 `txt_to_audio(self, msg)`。
5. 在 YAML 中设置 `plugins.tts: mytts`。

### Add a new ASR

1. 在 `plugins/asr/` 新增模块。
2. 类继承 `core.runtime.asr.base.BaseASR`。
3. `@register(PluginType.ASR, "myasr")`。
4. 实现 `run_step(self)`，向 `output_queue/feat_queue` 写入契约化数据。

### Add a new Renderer (Talk Model)

1. 在 `plugins/renderer/<name>/runtime.py` 放实现类。
2. 类继承 `core.runtime.renderer.base.BaseReal` 并 `@register(PluginType.RENDERER, "<name>")`。
3. 实现类方法：
   - `register_dependencies`
   - `required_plugins`
   - `prepare_shared`
   - `create_session`
4. 在实例中创建并接入对应 ASR/TTS，跑通 `render(...)` 主循环。

## 7. Validation Commands

环境：`conda activate livetalking`

基础校验：
```bash
python -m py_compile core/plugin_system.py app.py
python -m compileall -q core plugins server app.py
```

启动冒烟：
```bash
PORT=6006 STARTUP_TIMEOUT_SEC=300 \
SMOKE_CONFIG=config/webrtc.yaml \
bash scripts/smoke/startup_smoke.sh
```

配置文件示例：
- `config/app.yaml`：项目默认结构化配置。
- `config/rtcpush.yaml`：RTCPush 运行示例（含队列与推流地址）。
- `config/webrtc.yaml`：WebRTC 冒烟与联调示例。

## 8. Notes

- `import cv2` 在 `app.py` 顶层保留，避免与部分网络库加载顺序冲突。
- 当前文档以“代码现状”为准；后续若变更插件入口或目录结构，请优先更新本文档。
- 高频 frame 监控已独立写入 `tmp/frame_monitor/session_<sessionid>.log`，避免污染主日志；可通过 `renderer.frame_monitor_dir` 覆盖目录。

## 9. Redundancy & Complexity Check (Current)

已完成的收敛：
- `core/plugin_system.py` 中多套重复 bootstrap 逻辑已合并为统一 family 注册入口。
- `BaseReal.process_frames` 中重复的索引解析、事件归一化、音视频入队与统计日志逻辑已抽出为私有辅助函数。
- 阶段性文档与历史迁移说明已清理，避免“旧设计指导新代码”。

仍建议持续关注的热点：
- `core/runtime/renderer/base.py` 仍是运行时主干（单文件职责较重），后续可在“保持基类主干”前提下继续拆出纯函数级工具（不新增兼容层）。
- `plugins/renderer/musetalk/runtime.py` 同时包含模型加载与会话运行逻辑，若未来 renderer 增多，可考虑抽一个同目录 `resource` 模块承载共享重资源加载。
