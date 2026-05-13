###############################################################################
#  Copyright (C) 2024 LiveTalking@lipku https://github.com/lipku/LiveTalking
#  email: lipku@foxmail.com
# 
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#  
#       http://www.apache.org/licenses/LICENSE-2.0
# 
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
###############################################################################

from collections import defaultdict
from dataclasses import dataclass
import math
import torch
import numpy as np

#from .utils import *
import subprocess
import os
import time
import torch.nn.functional as F
import cv2
import glob
import pickle
import copy

import queue
from queue import Queue
from threading import Thread, Event
import torch.multiprocessing as mp

from data import EMOTION, DEFAULT_EMOTION
from musetalk.utils.utils import get_file_type,get_video_fps,datagen
#from musetalk.utils.preprocessing import get_landmark_and_bbox,read_imgs,coord_placeholder
from musetalk.myutil import get_image_blending
from musetalk.utils.utils import load_all_model
from musetalk.whisper.audio2feature import Audio2Feature

from museasr import MuseASR
import asyncio
from av import AudioFrame, VideoFrame
from basereal import BaseReal

from tqdm import tqdm
from logger import logger

@dataclass
class AvatarMeta:
    frame_list_cycle: list
    mask_list_cycle: list
    coord_list_cycle: list
    mask_coords_list_cycle: list
    input_latent_list_cycle: list
    length: int
    index: int = 0
        
def load_multi_avatar(avatar_ids: list[EMOTION]):
    avatars = {}
    for avatar_id in avatar_ids:
        frame_list_cycle, mask_list_cycle, coord_list_cycle, mask_coords_list_cycle, input_latent_list_cycle = load_avatar(avatar_id.value)
        avatars[avatar_id] = AvatarMeta(frame_list_cycle=frame_list_cycle,
                                        mask_list_cycle=mask_list_cycle,
                                        coord_list_cycle=coord_list_cycle,
                                        mask_coords_list_cycle=mask_coords_list_cycle,
                                        input_latent_list_cycle=input_latent_list_cycle,
                                        length=len(input_latent_list_cycle),
                                        )
    return avatars

def load_transitions():
    transition_path = "./data/transitions"

    transitions = {}
    for emo1 in EMOTION:
        transitions[emo1] = {}
        for emo2 in EMOTION:
            if emo1 != emo2:
                if os.path.exists(os.path.join(transition_path, f"{emo1.name}2{emo2.name}")):
                    logger.info(f"Loading transition frames for {emo1} to {emo2}")
                    image_list = glob.glob(os.path.join(transition_path, f"{emo1.name}2{emo2.name}", '*.[jpJP][pnPN]*[gG]'))
                    image_list = sorted(image_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0].split('_')[-1]))
                    frames = read_imgs(image_list)
                    transitions[emo1][emo2] = frames
                else:
                    logger.warning(f"No transition frames found for {emo1} to {emo2}. Expected folder: {os.path.join(transition_path, f'{emo1.name}2{emo2.name}')}")
                    transitions[emo1][emo2] = []


    # for file in os.listdir(transition_path):
    #     emotions = file.split('2')
    #     if len(emotions) != 2:
    #         logger.warning(f"Invalid transition folder name: {file}. Expected format 'emo1toemo2'. Skipping.")
    #         continue
        
    #     if emotions[0] not in EMOTION.__members__ or emotions[1] not in EMOTION.__members__:
    #         print(f"Emotion1: {emotions[0]}, Emotion2: {emotions[1]}")
    #         logger.warning(f"Invalid emotions in transition folder name: {file}. Expected emotions from {list(EMOTION.__members__.keys())}. Skipping.")
    #         continue

    #     image_list = glob.glob(os.path.join(transition_path, file, '*.[jpJP][pnPN]*[gG]'))
    #     image_list = sorted(image_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0].split('_')[-1]))
    #     frames = read_imgs(image_list)
    #     transitions[emotions[0]][emotions[1]] = frames

    return transitions

def load_model():
    # load model weights
    vae, unet, pe = load_all_model()
    device = torch.device("cuda" if torch.cuda.is_available() else ("mps" if (hasattr(torch.backends, "mps") and torch.backends.mps.is_available()) else "cpu"))
    timesteps = torch.tensor([0], device=device)
    pe = pe.half().to(device)
    vae.vae = vae.vae.half().to(device)
    #vae.vae.share_memory().to(device)
    unet.model = unet.model.half().to(device)
    #unet.model.share_memory()
    # Initialize audio processor and Whisper model
    audio_processor = Audio2Feature(model_path="./models/whisper")
    return vae, unet, pe, timesteps, audio_processor

def load_avatar(avatar_id):
    #self.video_path = '' #video_path
    #self.bbox_shift = opt.bbox_shift
    avatar_path = f"./data/avatars/{avatar_id}"
    full_imgs_path = f"{avatar_path}/full_imgs" 
    coords_path = f"{avatar_path}/coords.pkl"
    latents_out_path= f"{avatar_path}/latents.pt"
    video_out_path = f"{avatar_path}/vid_output/"
    mask_out_path =f"{avatar_path}/mask"
    mask_coords_path =f"{avatar_path}/mask_coords.pkl"
    avatar_info_path = f"{avatar_path}/avator_info.json"
    # self.avatar_info = {
    #     "avatar_id":self.avatar_id,
    #     "video_path":self.video_path,
    #     "bbox_shift":self.bbox_shift   
    # }

    input_latent_list_cycle = torch.load(latents_out_path)  #,weights_only=True
    with open(coords_path, 'rb') as f:
        coord_list_cycle = pickle.load(f)
    input_img_list = glob.glob(os.path.join(full_imgs_path, '*.[jpJP][pnPN]*[gG]'))
    input_img_list = sorted(input_img_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0]))
    frame_list_cycle = read_imgs(input_img_list)
    with open(mask_coords_path, 'rb') as f:
        mask_coords_list_cycle = pickle.load(f)
    input_mask_list = glob.glob(os.path.join(mask_out_path, '*.[jpJP][pnPN]*[gG]'))
    input_mask_list = sorted(input_mask_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0]))
    mask_list_cycle = read_imgs(input_mask_list)
    return frame_list_cycle,mask_list_cycle,coord_list_cycle,mask_coords_list_cycle,input_latent_list_cycle

@torch.no_grad()
def warm_up(batch_size,model):
    # 预热函数
    logger.info('warmup model...')
    vae, unet, pe, timesteps, audio_processor = model
    #batch_size = 16
    #timesteps = torch.tensor([0], device=unet.device)
    whisper_batch = np.ones((batch_size, 50, 384), dtype=np.uint8)
    latent_batch = torch.ones(batch_size, 8, 32, 32).to(unet.device)

    audio_feature_batch = torch.from_numpy(whisper_batch)
    audio_feature_batch = audio_feature_batch.to(device=unet.device, dtype=unet.model.dtype)
    audio_feature_batch = pe(audio_feature_batch)
    latent_batch = latent_batch.to(dtype=unet.model.dtype)
    pred_latents = unet.model(latent_batch,
                              timesteps,
                              encoder_hidden_states=audio_feature_batch).sample
    vae.decode_latents(pred_latents)

def read_imgs(img_list):
    frames = []
    logger.info('reading images...')
    for img_path in tqdm(img_list):
        frame = cv2.imread(img_path)
        frames.append(frame)
    return frames

def __mirror_index(size, index):
    #size = len(self.coord_list_cycle)
    turn = index // size
    res = index % size
    if turn % 2 == 0:
        return res
    else:
        return size - res - 1 

@torch.no_grad()
def inference(quit_event,batch_size,input_latent_list_cycle,audio_feat_queue,audio_out_queue,res_frame_queue,
              vae, unet, pe,timesteps, profiler=None): #vae, unet, pe,timesteps
    
    # vae, unet, pe = load_diffusion_model()
    # device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    # timesteps = torch.tensor([0], device=device)
    # pe = pe.half()
    # vae.vae = vae.vae.half()
    # unet.model = unet.model.half()
    
    length = len(input_latent_list_cycle)
    index = 0
    count=0
    counttime=0
    logger.info('start inference')
    while not quit_event.is_set():
        starttime=time.perf_counter()
        try:
            feat_wait_t0 = time.perf_counter()
            whisper_chunks = audio_feat_queue.get(block=True, timeout=1)
            if profiler is not None:
                profiler.observe("infer.wait_feat_queue", time.perf_counter() - feat_wait_t0)
        except queue.Empty:
            continue
        is_all_silence=True
        audio_frames = []
        audio_out_wait_t0 = time.perf_counter()
        for _ in range(batch_size*2):
            frame,type,eventpoint = audio_out_queue.get()
            audio_frames.append((frame,type,eventpoint))
            if type==0:
                is_all_silence=False
        if profiler is not None:
            profiler.observe("infer.wait_audio_out_queue", time.perf_counter() - audio_out_wait_t0)
            profiler.incr("infer.batch_count")
            profiler.incr("infer.audio_frames_in", batch_size * 2)
        if is_all_silence:
            silence_put_t0 = time.perf_counter()
            for i in range(batch_size):
                res_frame_queue.put((None,__mirror_index(length,index),audio_frames[i*2:i*2+2]))
                index = index + 1
            if profiler is not None:
                profiler.observe("infer.enqueue_silence_res_frame", time.perf_counter() - silence_put_t0)
                profiler.incr("infer.video_frames_out", batch_size)
        else:
            # print('infer=======')
            t=time.perf_counter()
            prep_t0 = t
            whisper_batch = np.stack(whisper_chunks)
            latent_batch = []
            for i in range(batch_size):
                idx = __mirror_index(length,index+i)
                latent = input_latent_list_cycle[idx]
                latent_batch.append(latent)
            latent_batch = torch.cat(latent_batch, dim=0)
            
            # for i, (whisper_batch,latent_batch) in enumerate(gen):
            audio_feature_batch = torch.from_numpy(whisper_batch)
            audio_feature_batch = audio_feature_batch.to(device=unet.device,
                                                            dtype=unet.model.dtype)
            audio_feature_batch = pe(audio_feature_batch)
            latent_batch = latent_batch.to(dtype=unet.model.dtype)
            if profiler is not None:
                profiler.observe("infer.prepare_batch", time.perf_counter() - prep_t0)
            # print('prepare time:',time.perf_counter()-t)
            # t=time.perf_counter()

            unet_t0 = time.perf_counter()
            pred_latents = unet.model(latent_batch, 
                                        timesteps, 
                                        encoder_hidden_states=audio_feature_batch).sample
            if profiler is not None:
                profiler.observe("infer.unet_forward", time.perf_counter() - unet_t0)
            # print('unet time:',time.perf_counter()-t)
            # t=time.perf_counter()
            vae_t0 = time.perf_counter()
            recon = vae.decode_latents(pred_latents)
            if profiler is not None:
                profiler.observe("infer.vae_decode", time.perf_counter() - vae_t0)
            # infer_inqueue.put((whisper_batch,latent_batch,sessionid))
            # recon,outsessionid = infer_outqueue.get()
            # if outsessionid != sessionid:
            #     print('outsessionid:',outsessionid,' mysessionid:',sessionid)

            # print('vae time:',time.perf_counter()-t)
            #print('diffusion len=',len(recon))
            counttime += (time.perf_counter() - t)
            count += batch_size
            #_totalframe += 1
            if count>=100:
                logger.info(f"------actual avg infer fps:{count/counttime:.4f}")
                count=0
                counttime=0
            put_t0 = time.perf_counter()
            for i,res_frame in enumerate(recon):
                #self.__pushmedia(res_frame,loop,audio_track,video_track)
                res_frame_queue.put((res_frame,__mirror_index(length,index),audio_frames[i*2:i*2+2]))
                index = index + 1
            if profiler is not None:
                profiler.observe("infer.enqueue_res_frame", time.perf_counter() - put_t0)
                profiler.incr("infer.video_frames_out", len(recon))
            #print('total batch time:',time.perf_counter()-starttime)            
        if profiler is not None:
            profiler.observe("infer.batch_total", time.perf_counter() - starttime)
    logger.info('musereal inference processor stop')



@torch.no_grad()
def multi_avatar_inference(quit_event,batch_size,avatars: dict[str, AvatarMeta],audio_feat_queue,audio_out_queue,res_frame_queue,vae,unet,pe,timesteps,profiler=None): #vae, unet, pe,timesteps
    count=0
    counttime=0
    last_emo = DEFAULT_EMOTION
    logger.info('start multi inference')
    while not quit_event.is_set():
        starttime=time.perf_counter()
        try:
            feat_wait_t0 = time.perf_counter()
            whisper_chunks = audio_feat_queue.get(block=True, timeout=1)
            if profiler is not None:
                profiler.observe("infer.wait_feat_queue", time.perf_counter() - feat_wait_t0)
        except queue.Empty:
            continue
        is_all_silence=True
        audio_frames = []
        audio_out_wait_t0 = time.perf_counter()
        for _ in range(batch_size * 2):
            frame,type,eventpoint = audio_out_queue.get()
            audio_frames.append((frame,type,eventpoint))
            if type==0:
                is_all_silence=False
        if profiler is not None:
            profiler.observe("infer.wait_audio_out_queue", time.perf_counter() - audio_out_wait_t0)
            profiler.incr("infer.batch_count")
            profiler.incr("infer.audio_frames_in", batch_size * 2)

        if is_all_silence:
            silence_put_t0 = time.perf_counter()
            for i in range(batch_size):
                pair_frames = audio_frames[i*2:i*2+2]
                event0 = pair_frames[0][2]
                event1 = pair_frames[1][2]
                emo0 = event0.get("emo") if event0 else None
                emo1 = event1.get("emo") if event1 else None

                emo = emo0 or emo1 or last_emo
                if emo not in avatars:
                    emo = DEFAULT_EMOTION

                idx = __mirror_index(avatars[emo].length, avatars[emo].index)
                res_frame_queue.put((None, (idx, emo), pair_frames))
                avatars[emo].index += 1
                last_emo = emo
            if profiler is not None:
                profiler.observe("infer.enqueue_silence_res_frame", time.perf_counter() - silence_put_t0)
                profiler.incr("infer.video_frames_out", batch_size)
        else:
            # print('infer=======')
            t=time.perf_counter()
            prep_t0 = t
            whisper_batch = np.stack(whisper_chunks)
            latent_batch = []
            face_indexes = []
            for i in range(batch_size):
                event0 = audio_frames[i*2][2]
                event1 = audio_frames[i*2+1][2]
                emo0 = event0.get("emo") if event0 else None
                emo1 = event1.get("emo") if event1 else None

                if emo0 is not None and emo1 is not None and emo0 != emo1:
                    logger.warning("-avatar inference- Emotion conflict, audio frame info : {};{}".format(event0, event1))

                emo = emo0 or emo1 or last_emo
                if emo not in avatars:
                    logger.warning("-avatar inference- Unknown emotion {}, fallback to baseline emotion".format(emo))
                    emo = DEFAULT_EMOTION

                last_emo = emo
                idx = __mirror_index(avatars[emo].length,avatars[emo].index)
                avatars[emo].index += 1
                latent = avatars[emo].input_latent_list_cycle[idx]
                latent_batch.append(latent)
                face_indexes.append((idx,emo))
            latent_batch = torch.cat(latent_batch, dim=0)
            
            # for i, (whisper_batch,latent_batch) in enumerate(gen):
            audio_feature_batch = torch.from_numpy(whisper_batch)
            audio_feature_batch = audio_feature_batch.to(device=unet.device,
                                                            dtype=unet.model.dtype)
            audio_feature_batch = pe(audio_feature_batch)
            latent_batch = latent_batch.to(dtype=unet.model.dtype)
            if profiler is not None:
                profiler.observe("infer.prepare_batch", time.perf_counter() - prep_t0)
            # print('prepare time:',time.perf_counter()-t)
            # t=time.perf_counter()

            unet_t0 = time.perf_counter()
            pred_latents = unet.model(latent_batch, 
                                        timesteps, 
                                        encoder_hidden_states=audio_feature_batch).sample
            if profiler is not None:
                profiler.observe("infer.unet_forward", time.perf_counter() - unet_t0)
            # print('unet time:',time.perf_counter()-t)
            # t=time.perf_counter()
            vae_t0 = time.perf_counter()
            recon = vae.decode_latents(pred_latents)
            if profiler is not None:
                profiler.observe("infer.vae_decode", time.perf_counter() - vae_t0)
            # infer_inqueue.put((whisper_batch,latent_batch,sessionid))
            # recon,outsessionid = infer_outqueue.get()
            # if outsessionid != sessionid:
            #     print('outsessionid:',outsessionid,' mysessionid:',sessionid)

            # print('vae time:',time.perf_counter()-t)
            # print('diffusion len=',len(recon))
            counttime += (time.perf_counter() - t)
            count += batch_size
            #_totalframe += 1
            if count>=100:
                logger.info(f"------actual avg infer fps:{count/counttime:.4f}")
                count=0
                counttime=0
            put_t0 = time.perf_counter()
            for i,res_frame in enumerate(recon):
                #self.__pushmedia(res_frame,loop,audio_track,video_track)
                res_frame_queue.put((res_frame,face_indexes[i],audio_frames[i*2:i*2+2]))
                # avatars[emo].index += 1
            if profiler is not None:
                profiler.observe("infer.enqueue_res_frame", time.perf_counter() - put_t0)
                profiler.incr("infer.video_frames_out", len(recon))
            #print('total batch time:',time.perf_counter()-starttime)            
        if profiler is not None:
            profiler.observe("infer.batch_total", time.perf_counter() - starttime)
    logger.info('musereal inference processor stop')


class MuseReal(BaseReal):
    @torch.no_grad()
    def __init__(self, opt, model, avatar=None, transitions=None):
        super().__init__(opt)
        #self.opt = opt # shared with the trainer's opt to support in-place modification of rendering parameters.
        # self.W = opt.W
        # self.H = opt.H

        self.fps = opt.fps # 20 ms per frame

        self.batch_size = opt.batch_size
        self.idx = 0
        self.res_frame_queue = mp.Queue(self.batch_size*2)

        self.vae, self.unet, self.pe, self.timesteps, self.audio_processor = model
        
        #self.__loadavatar()

        self.asr = MuseASR(opt,self,self.audio_processor)
        self.asr.warm_up()
        
        self.render_event = mp.Event()

        self.multi_avatar = opt.multi_avatar

        self.avatars: dict[str, AvatarMeta] = avatar

        self.transitions: dict[str, dict[str, any]] = transitions


    # def __del__(self):
    #     logger.info(f'musereal({self.sessionid}) delete')
    

    def __mirror_index(self, index):
        size = len(self.coord_list_cycle)
        turn = index // size
        res = index % size
        if turn % 2 == 0:
            return res
        else:
            return size - res - 1  

    def __warm_up(self): 
        self.asr.run_step()
        whisper_chunks = self.asr.get_next_feat()
        whisper_batch = np.stack(whisper_chunks)
        latent_batch = []
        for i in range(self.batch_size):
            idx = self.__mirror_index(self.idx+i)
            latent = self.input_latent_list_cycle[idx]
            latent_batch.append(latent)
        latent_batch = torch.cat(latent_batch, dim=0)
        logger.info('infer=======')
        # for i, (whisper_batch,latent_batch) in enumerate(gen):
        audio_feature_batch = torch.from_numpy(whisper_batch)
        audio_feature_batch = audio_feature_batch.to(device=self.unet.device,
                                                        dtype=self.unet.model.dtype)
        audio_feature_batch = self.pe(audio_feature_batch)
        latent_batch = latent_batch.to(dtype=self.unet.model.dtype)

        pred_latents = self.unet.model(latent_batch, 
                                    self.timesteps, 
                                    encoder_hidden_states=audio_feature_batch).sample
        recon = self.vae.decode_latents(pred_latents)
      

    def paste_back_frame(self,pred_frame,idx:int,emo: str=DEFAULT_EMOTION):
        if self.multi_avatar:
            avatar = self.avatars[emo]
            bbox = avatar.coord_list_cycle[idx]
            ori_frame = copy.deepcopy(avatar.frame_list_cycle[idx])
            x1, y1, x2, y2 = bbox

            # 生成预测框，并调整为bbox大小
            res_frame = cv2.resize(pred_frame.astype(np.uint8), (x2 - x1, y2 - y1))

            # 获取对应的mask和crop_box
            mask = avatar.mask_list_cycle[idx]
            mask_crop_box = avatar.mask_coords_list_cycle[idx]

            # 打印调试信息
            # print(f"ori_frame shape: {ori_frame.shape}")
            # print(f"res_frame shape: {res_frame.shape}")
            # print(f"mask shape: {mask.shape}")
            # print(f"mask_crop_box: {mask_crop_box}")
            # print(f"bbox: {bbox}")
            # print("emotion:", emo)

            # # 确保mask和res_frame大小一致
            # if mask.shape[:2] != (y2 - y1, x2 - x1):
            #     print("Resizing mask to match res_frame dimensions...")
            #     mask = cv2.resize(mask, (x2 - x1, y2 - y1))

            # 调用get_image_blending进行融合
            combine_frame = get_image_blending(ori_frame, res_frame, bbox, mask, mask_crop_box)

            return combine_frame
        else:
            bbox = self.coord_list_cycle[idx]
            ori_frame = copy.deepcopy(self.frame_list_cycle[idx])
            x1, y1, x2, y2 = bbox

            res_frame = cv2.resize(pred_frame.astype(np.uint8),(x2-x1,y2-y1))
            mask = self.mask_list_cycle[idx]
            mask_crop_box = self.mask_coords_list_cycle[idx]

            combine_frame = get_image_blending(ori_frame,res_frame,bbox,mask,mask_crop_box)
            return combine_frame
            
    def render(self,quit_event,loop=None,audio_track=None,video_track=None):
        #if self.opt.asr:
        #     self.asr.warm_up()

        self.init_customindex()
        self.tts.render(quit_event)
        
        #self.render_event.set() #start infer process render
        infer_quit_event = Event()
        profiler = self._get_httpfile_profiler()
        if self.multi_avatar:
            infer_thread = Thread(target=multi_avatar_inference, args=(infer_quit_event,self.batch_size,
                                                                       self.avatars,
                                                                       self.asr.feat_queue,
                                                                       self.asr.output_queue,
                                                                       self.res_frame_queue,
                                                                       self.vae, self.unet, self.pe,self.timesteps,profiler)) #mp.Process
        else:
            infer_thread = Thread(target=inference, args=(infer_quit_event,self.batch_size,self.input_latent_list_cycle,
                                            self.asr.feat_queue,self.asr.output_queue,self.res_frame_queue,
                                            self.vae, self.unet, self.pe,self.timesteps,profiler)) #mp.Process
        infer_thread.start()
        
        process_quit_event = Event()
        process_thread = Thread(target=self.process_frames, args=(process_quit_event,loop,audio_track,video_track))
        process_thread.start()

        
        count=0
        totaltime=0
        _starttime=time.perf_counter()
        #_totalframe=0
        while not quit_event.is_set(): #todo
            # update texture every frame
            # audio stream thread...
            t = time.perf_counter()
            self.asr.run_step()
            #self.test_step(loop,audio_track,video_track)
            # totaltime += (time.perf_counter() - t)
            # count += self.opt.batch_size
            # if count>=100:
            #     print(f"------actual avg infer fps:{count/totaltime:.4f}")
            #     count=0
            #     totaltime=0
            if video_track and video_track._queue.qsize()>=1.5*self.opt.batch_size:
                logger.debug('sleep qsize=%d',video_track._queue.qsize())
                time.sleep(0.04*video_track._queue.qsize()*0.8)
            # if video_track._queue.qsize()>=5:
            #     print('sleep qsize=',video_track._queue.qsize())
            #     time.sleep(0.04*video_track._queue.qsize()*0.8)
                
            # delay = _starttime+_totalframe*0.04-time.perf_counter() #40ms
            # if delay > 0:
            #     time.sleep(delay)
        logger.info('musereal thread stop')

        infer_quit_event.set()
        infer_thread.join()

        process_quit_event.set()
        process_thread.join()
            
