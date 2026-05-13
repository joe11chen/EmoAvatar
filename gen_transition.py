import subprocess
import os
import re

# # --- 配置参数 ---
# VIDEO_1 = "assets/default.mp4"
# VIDEO_2 = "assets/sad.mp4"

# FCVG_DIR = "./FCVG"                  # FCVG 代码所在目录
# FINAL_FRAMES_DIR = "data/transitions"  # 最终 1.5 倍速图片存放处
# SPEED_FACTOR = 3              # 加速倍率

# VIDEO_1_NAME = os.path.basename(VIDEO_1).split('.')[0]  # 提取视频文件名
# VIDEO_2_NAME = os.path.basename(VIDEO_2).split('.')[0]  # 提取视频文件名

# if not os.path.exists(FINAL_FRAMES_DIR):
#     os.makedirs(FINAL_FRAMES_DIR)

# OUT_VIDEO_1 = f"{VIDEO_1_NAME}2{VIDEO_2_NAME}".upper()
# OUT_VIDEO_2 = f"{VIDEO_2_NAME}2{VIDEO_1_NAME}".upper()
# if not os.path.exists(os.path.join(FINAL_FRAMES_DIR, f"{OUT_VIDEO_1}")):
#     os.makedirs(os.path.join(FINAL_FRAMES_DIR, f"{OUT_VIDEO_1}"))

# if not os.path.exists(os.path.join(FINAL_FRAMES_DIR, f"{OUT_VIDEO_2}")):
#     os.makedirs(os.path.join(FINAL_FRAMES_DIR, f"{OUT_VIDEO_2}"))

def run_command(cmd, description):
    print(f"🚀 正在执行: {cmd} \n {description}...")
    try:
        # 使用 shell=True 以支持 conda activate 和 cd 连写
        subprocess.run(cmd, shell=True, check=True, executable="/bin/bash" if os.name != 'nt' else None)
    except subprocess.CalledProcessError as e:
        print(f"❌ 执行失败: {description}\n错误信息: {e}")
        exit(1)

# TRNASITION1 = f"{VIDEO_1_NAME}2{VIDEO_2_NAME}.mp4"
# TRNASITION2 = f"{VIDEO_2_NAME}2{VIDEO_1_NAME}.mp4"


# # --- 步骤 1: 提取视频 B 的第一帧和最后一帧 ---
# print("--- Step 1: 提取首尾帧 ---")
# # 提取第一帧
# run_command(
#     f"ffmpeg -y -i {VIDEO_1} -frames:v 1 first_frame_{VIDEO_1_NAME}.png", 
#     "提取第一帧"
# )
# # 提取最后一帧 (使用 sseof 倒序定位最后一帧，效率最高)
# run_command(
#     f"ffmpeg -y -sseof -0.1 -i {VIDEO_1} -update 1 -q:v 2 last_frame_{VIDEO_1_NAME}.png", 
#     "提取最后一帧"
# )

# run_command(
#     f"ffmpeg -y -i {VIDEO_2} -frames:v 1 first_frame_{VIDEO_2_NAME}.png", 
#     "提取第一帧"
# )
# # 提取最后一帧 (使用 sseof 倒序定位最后一帧，效率最高)
# run_command(
#     f"ffmpeg -y -sseof -0.1 -i {VIDEO_2} -update 1 -q:v 2 last_frame_{VIDEO_2_NAME}.png", 
#     "提取最后一帧"
# )



# # --- 步骤 2: 生成视频 A (FCVG) ---
# print("\n--- Step 2: 运行 FCVG 模型生成视频 A ---")
# # 注意：这里将多个 shell 命令组合在一起执行
# fcvg_cmd = (
#     f"cd {FCVG_DIR} && "
#     f"CUDA_VISIBLE_DEVICES=0 python demo_FCVG.py "
#     f"--pretrained_model_name_or_path stabilityai/stable-video-diffusion-img2vid-xt-1-1 "
#     f"--controlnext_path checkpoints/controlnext.safetensors "
#     f"--unet_path checkpoints/unet.safetensors "
#     f"--image1_path ../last_frame_{VIDEO_1_NAME}.png " # 注意路径回退
#     f"--image2_path ../first_frame_{VIDEO_2_NAME}.png "
#     f"--output_dir ../assets/transitions "
#     f"--file_name {TRNASITION1} "
#     f"--control_weight 1.0 "
#     f"--num_inference_steps 25 "
#     f"--height 576 "
#     f"--width 1024"
# )
# run_command(fcvg_cmd, f"生成视频 {TRNASITION1}")

# fcvg_cmd = (
#     f"cd {FCVG_DIR} && "
#     f"CUDA_VISIBLE_DEVICES=0 python demo_FCVG.py "
#     f"--pretrained_model_name_or_path stabilityai/stable-video-diffusion-img2vid-xt-1-1 "
#     f"--controlnext_path checkpoints/controlnext.safetensors "
#     f"--unet_path checkpoints/unet.safetensors "
#     f"--image1_path ../last_frame_{VIDEO_2_NAME}.png " # 注意路径回退
#     f"--image2_path ../first_frame_{VIDEO_1_NAME}.png "
#     f"--output_dir ../assets/transitions "
#     f"--file_name {TRNASITION2} "
#     f"--control_weight 1.0 "
#     f"--num_inference_steps 25 "
#     f"--height 576 "
#     f"--width 1024"
# )
# run_command(fcvg_cmd, f"生成视频 {TRNASITION2}")


# --- 步骤 3: 视频 A 加速并转图片序列 ---
print("\n--- Step 3: 加速 1.5x 并导出图片序列 ---")

videos = [
'assets/transitions/DEFAULT2OPEN_ACCEPTANCE.mp4',
'assets/transitions/DEFAULT2RELIEF_GROWTH.mp4',
'assets/transitions/DEFAULT2RIGID_DEFENSE.mp4',
'assets/transitions/DEFAULT2WAVERING_DOUBT.mp4',

'assets/transitions/OPEN_ACCEPTANCE2DEFAULT.mp4',
'assets/transitions/RELIEF_GROWTH2DEFAULT.mp4',
'assets/transitions/RIGID_DEFENSE2DEFAULT.mp4',
'assets/transitions/WAVERING_DOUBT2DEFAULT.mp4'
]

for v in videos:
    name = os.path.basename(v).split('.')[0]
    if not os.path.exists(f"./data/transitions/{name}"):
        os.makedirs(f"./data/transitions/{name}")
        

    pts_factor = 1 
    # 这里的 INPUT 指向 Step 2 生成的结果
    # 请确认 demo_FCVG.py 生成的文件名，这里暂定为 output.mp4
    run_command(
        f"ffmpeg -y -i {v} "
        f"-vf 'setpts={pts_factor}*PTS' "
        f"-r 25 "
        f"-an ./data/transitions/{name}/frame_%04d.png",
        "加速并提取 PNG 序列"
    )

# run_command(
#     f"ffmpeg -y -i ./assets/transitions/{TRNASITION2} "
#     f"-vf 'minterpolate=fps=60,setpts={pts_factor}*PTS' "
#     f"-r 25 "
#     f"-an ./data/transitions/{OUT_VIDEO_2}/frame_%04d.png",
#     "加速并提取 PNG 序列"
# )

print("\n✨ 全部任务完成！")
