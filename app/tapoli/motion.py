"""Detección de movimiento por diferencia entre fotogramas (escala de grises, baja resolución)."""
import numpy as np


def polygon_mask(poly, w, h):
    """Máscara booleana (h, w) de un polígono con coordenadas relativas 0–1."""
    pts = np.array(poly, dtype=np.float64) * [w, h]
    yy, xx = np.mgrid[0:h, 0:w] + 0.5
    inside = np.zeros((h, w), bool)
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if y1 == y2:
            continue
        cross = ((y1 > yy) != (y2 > yy)) & (xx < (x2 - x1) * (yy - y1) / (y2 - y1) + x1)
        inside ^= cross
    return inside


def rects_mask(rects, w, h):
    m = np.zeros((h, w), bool)
    for x1, y1, x2, y2 in rects or []:
        m[int(y1 * h):int(np.ceil(y2 * h)), int(x1 * w):int(np.ceil(x2 * w))] = True
    return m


class MotionDetector:
    def __init__(self, cfg):
        self.width = cfg["video"]["ancho"]
        self.height = cfg["video"]["alto"]
        self.w = self.width // 2
        self.h = self.height // 2
        self.prev = None
        self.configure(cfg)

    def configure(self, cfg):
        mv = cfg["movimiento"]
        self.pixel_thr = float(mv["umbral_pixel"])
        self.global_max = float(mv["cambio_global_max"])
        excl = rects_mask(mv.get("zonas_excluidas"), self.w, self.h)
        roi = polygon_mask(mv["zona_cuna"], self.w, self.h) & ~excl
        if roi.sum() < 20:  # zona mal definida: usar toda la imagen
            roi = ~excl
        self.roi = roi
        self.out = ~roi & ~excl
        self.valid = ~excl

    def _prep(self, frame):
        # reduce a la mitad (promedio 2x2) + desenfoque 3x3: suaviza el grano del infrarrojo
        x = frame.reshape(self.h, 2, self.w, 2).mean(axis=(1, 3), dtype=np.float32)
        p = np.pad(x, 1, mode="edge")
        acc = np.zeros_like(x)
        for i in range(3):
            for j in range(3):
                acc += p[i:i + self.h, j:j + self.w]
        return acc / 9.0

    def process(self, frame_bytes):
        """Devuelve (fracción cuna, fracción fuera, cambio_global) o None en el primer fotograma."""
        frame = np.frombuffer(frame_bytes, np.uint8).reshape(self.height, self.width)
        cur = self._prep(frame)
        prev, self.prev = self.prev, cur
        if prev is None:
            return None
        # compensa cambios suaves de brillo global (autoexposición)
        m_prev = float(prev[self.valid].mean())
        m_cur = float(cur[self.valid].mean())
        if m_cur > 1:
            cur_n = cur * (m_prev / m_cur)
        else:
            cur_n = cur
        changed = np.abs(cur_n - prev) > self.pixel_thr
        glob = float(changed[self.valid].mean())
        if glob > self.global_max:
            return 0.0, 0.0, True
        return float(changed[self.roi].mean()), float(changed[self.out].mean()), False
