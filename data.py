
from enum import Enum


class EMOTION(Enum):
     DEFAULT = 'xyw_default'
     CRY = '..//musetalk//crying'
     ANGRY = '..//musetalk//angry'
     HAPPY = '..//musetalk//happy'
     EMOTIONAL = 'xyw_emotional'


EMOTION_VECTOR = {
     # vec1~vec8: happy, sad, angry, afraid, disgusted, melancholy, suprised, calm
     EMOTION.DEFAULT: [0, 0, 0, 0, 0, 0, 0, 0],
     EMOTION.CRY: [0, 0.2, 0.2, 0.05, 0.1, 0.1, 0, 0],
     EMOTION.EMOTIONAL: [0, 0.1, 0.1, 0.1, 0.1, 0.1, 0, 0],
     EMOTION.HAPPY: [0.3, 0, 0, 0, 0, 0, 0.2, 0],

}
