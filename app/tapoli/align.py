"""Reajuste automático de la zona de la cuna cuando la cámara se ha movido.

Compara los BORDES (no los colores: así vale de día y con infrarrojos) de la imagen
actual con la imagen de referencia guardada al dibujar la zona, y calcula el
desplazamiento por correlación de fase. La C200 en una balda se mueve sobre todo
de lado/arriba-abajo, que es justo lo que corrige.
"""
import subprocess

import numpy as np

W, H = 320, 180


def load_gray(path):
    """JPG → matriz de grises 320×180 (con ffmpeg, que ya está en la imagen)."""
    r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", path,
                        "-vf", f"scale={W}:{H},format=gray", "-f", "rawvideo", "-"],
                       capture_output=True, timeout=30)
    if r.returncode != 0 or len(r.stdout) < W * H:
        return None
    return np.frombuffer(r.stdout[:W * H], np.uint8).reshape(H, W).astype(np.float32)


def _edges(img, excl):
    # suavizado 3×3 + magnitud del gradiente, normalizado (independiente del brillo)
    p = np.pad(img, 1, mode="edge")
    s = sum(p[i:i + H, j:j + W] for i in range(3) for j in range(3)) / 9
    gx = np.zeros_like(s)
    gy = np.zeros_like(s)
    gx[:, 1:-1] = s[:, 2:] - s[:, :-2]
    gy[1:-1, :] = s[2:, :] - s[:-2, :]
    e = np.hypot(gx, gy)
    e[excl] = 0                       # reloj y logo de la cámara: no se mueven con la imagen
    e[:2, :] = e[-2:, :] = 0
    e[:, :2] = e[:, -2:] = 0
    m = e.mean()
    return (e - m) / (e.std() + 1e-6)


def sharpness(img):
    """Cantidad de detalle: una imagen tapada/desenfocada/negra tiene muy poca."""
    p = img
    return float(np.abs(np.diff(p, axis=1)).mean() + np.abs(np.diff(p, axis=0)).mean())


def estimate_shift(ref, cur, excl_rects):
    """Desplazamiento (dx, dy) en fracción de la imagen que hay que sumar a la zona
    dibujada sobre `ref` para que caiga en el mismo sitio en `cur`, y confianza."""
    excl = np.zeros((H, W), bool)
    for x1, y1, x2, y2 in excl_rects or []:
        excl[int(y1 * H):int(np.ceil(y2 * H)), int(x1 * W):int(np.ceil(x2 * W))] = True
    a, b = _edges(ref, excl), _edges(cur, excl)
    win = np.outer(np.hanning(H), np.hanning(W)).astype(np.float32)
    Fa, Fb = np.fft.fft2(a * win), np.fft.fft2(b * win)
    R = Fb * np.conj(Fa)
    R /= np.abs(R) + 1e-9
    r = np.fft.ifft2(R).real
    iy, ix = np.unravel_index(np.argmax(r), r.shape)
    peak = r[iy, ix]
    dy = iy if iy <= H // 2 else iy - H
    dx = ix if ix <= W // 2 else ix - W
    # confianza: cuánto destaca el pico sobre el resto de la correlación
    conf = float((peak - r.mean()) / (r.std() + 1e-9))
    return dx / W, dy / H, conf


def shift_poly(poly, dx, dy):
    return [[round(min(1, max(0, x + dx)), 3), round(min(1, max(0, y + dy)), 3)] for x, y in poly]
