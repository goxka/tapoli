"""Nivel de sonido y detección sencilla de voz/llanto (energía sostenida en 300–3000 Hz)."""
from collections import deque

import numpy as np

RATE = 8000
SUB = 4  # subventanas por segundo (250 ms)


class AudioAnalyzer:
    def __init__(self, cfg):
        self.hist = deque()
        self.floor = -70.0
        self._n = 0
        n = RATE // SUB
        self.win = np.hanning(n).astype(np.float32)
        self.freqs = np.fft.rfftfreq(n, 1.0 / RATE)
        self.configure(cfg)

    def configure(self, cfg):
        s = cfg["sonido"]
        lo, hi = s["banda_voz_hz"]
        self.band = (self.freqs >= lo) & (self.freqs <= hi)
        self.total = self.freqs >= 80
        self.margin = float(s["margen_db"])
        self.min_db = float(s["minimo_dbfs"])
        self.ratio_min = float(s["ratio_banda_min"])
        self.frac = float(s["fraccion_subventanas"])
        self.hist_len = int(s["ventana_ruido_fondo_min"]) * 60

    def process_second(self, pcm):
        """pcm: int16 de 1 s. Devuelve (nivel_dbfs, banda_voz_db, voz:bool)."""
        x = pcm.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(x * x)) + 1e-9)
        level = 20 * np.log10(rms)
        n = RATE // SUB
        subs = x[: n * SUB].reshape(SUB, n)
        spec = np.abs(np.fft.rfft(subs * self.win, axis=1)) ** 2
        band_e = spec[:, self.band].sum(axis=1)
        tot_e = spec[:, self.total].sum(axis=1) + 1e-12
        all_e = spec.sum(axis=1) + 1e-12
        # nivel (dBFS) de la energía que cae en la banda de voz, por subventana
        sub_db = 10 * np.log10(np.mean(subs * subs, axis=1) + 1e-12)
        band_db = sub_db + 10 * np.log10(band_e / all_e + 1e-12)
        ratio = band_e / tot_e
        sec_band_db = float(band_db.max())

        thr = max(self.floor + self.margin, self.min_db)
        hits = (band_db > thr) & (ratio >= self.ratio_min)
        voice = hits.mean() >= self.frac

        # ruido de fondo: percentil 20 de los últimos minutos
        self.hist.append(sec_band_db)
        while len(self.hist) > self.hist_len:
            self.hist.popleft()
        self._n += 1
        if self._n % 10 == 0 and len(self.hist) >= 30:
            self.floor = float(np.percentile(self.hist, 20))
        return float(level), sec_band_db, bool(voice)
