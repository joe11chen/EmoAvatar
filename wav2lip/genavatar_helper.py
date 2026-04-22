

cmd1 = "python genavatar.py --video_path ./avatars/1.mp4 --img_size 256 --avatar_id defualt1"
cmd2 = "python genavatar.py --video_path ./avatars/2.mp4 --img_size 256 --avatar_id sad1"

import os

def run_cmd(cmd):
    print(f"Running command: {cmd}")
    os.system(cmd)

if __name__ == "__main__":
    run_cmd(cmd1)
    run_cmd(cmd2)