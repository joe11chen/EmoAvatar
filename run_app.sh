
python app.py\
    --transport rtcpush \
    --renderer musetalk \
    --multi_avatar True \
    --tts indextts2 \
    --listenport 6006 \
    --push_url 'http://101.200.53.172:1985/rtc/v1/whip/?app=live&stream=livestream' \
    --rtc_audio_queue_maxsize 800 --rtc_video_queue_maxsize 400
