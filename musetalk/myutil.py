import numpy as np
import cv2

def _to_gray_mask(mask_array, target_w: int, target_h: int) -> np.ndarray:
    mask = mask_array
    if mask.ndim == 3:
        mask = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
    if mask.shape[:2] != (target_h, target_w):
        mask = cv2.resize(mask, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
    if mask.dtype != np.uint8:
        mask = np.asarray(mask)
        max_val = float(mask.max()) if mask.size else 0.0
        if np.issubdtype(mask.dtype, np.floating) and max_val <= 1.0:
            mask = mask * 255.0
        mask = np.clip(mask, 0, 255).astype(np.uint8)
    return mask


def get_image_blending(image, face, face_box, mask_array, crop_box):
    x, y, x1, y1 = [int(v) for v in face_box]
    x_s, y_s, x_e, y_e = [int(v) for v in crop_box]

    h, w = image.shape[:2]
    x_s = max(0, min(x_s, w))
    x_e = max(0, min(x_e, w))
    y_s = max(0, min(y_s, h))
    y_e = max(0, min(y_e, h))
    if x_s >= x_e or y_s >= y_e:
        return image.copy()

    body = image.copy()
    crop = body[y_s:y_e, x_s:x_e]
    crop_h, crop_w = crop.shape[:2]
    if crop_h == 0 or crop_w == 0:
        return body

    face_large = crop.copy()
    dst_l = max(0, x - x_s)
    dst_t = max(0, y - y_s)
    dst_r = min(crop_w, x1 - x_s)
    dst_b = min(crop_h, y1 - y_s)
    if dst_l >= dst_r or dst_t >= dst_b:
        return body

    src_l = max(0, -(x - x_s))
    src_t = max(0, -(y - y_s))
    src_r = src_l + (dst_r - dst_l)
    src_b = src_t + (dst_b - dst_t)
    if src_l >= src_r or src_t >= src_b:
        return body

    face_large[dst_t:dst_b, dst_l:dst_r] = face[src_t:src_b, src_l:src_r]

    mask = _to_gray_mask(mask_array, crop_w, crop_h)
    alpha = (mask.astype(np.float32) / 255.0)[..., None]
    blended = crop.astype(np.float32) * (1.0 - alpha) + face_large.astype(np.float32) * alpha
    body[y_s:y_e, x_s:x_e] = np.clip(blended, 0, 255).astype(np.uint8)
    return body
