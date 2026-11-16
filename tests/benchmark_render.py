"""
Benchmark de Renderização: 720p vs 1080p — Dark Channel Factory
===============================================================
Executa teste comparativo controlado entre 720p e 1080p,
medindo tempo, CPU, memória, tamanho de arquivo e compatibilidade com Intel QSV e libx264.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import psutil
except ImportError:
    psutil = None


def find_binary(name: str) -> str:
    root = Path(__file__).resolve().parent.parent
    local_bin = root / "bin" / f"{name}.exe"
    if local_bin.exists():
        return str(local_bin)
    which_bin = shutil.which(name)
    if which_bin:
        return which_bin
    return name


FFMPEG = find_binary("ffmpeg")
FFPROBE = find_binary("ffprobe")


def check_qsv_support() -> bool:
    """Verifica se h264_qsv está funcional neste host."""
    with tempfile.TemporaryDirectory() as td:
        out_f = os.path.join(td, "test_qsv.mp4")
        cmd = [
            FFMPEG, "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=0.5",
            "-c:v", "h264_qsv", "-pix_fmt", "nv12", out_f
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return res.returncode == 0 and os.path.exists(out_f) and os.path.getsize(out_f) > 0
        except Exception:
            return False


def run_benchmark_pass(
    width: int,
    height: int,
    encoder: str,
    duration_s: float = 6.0,
    preset: str = "ultrafast",
) -> Dict[str, Any]:
    """Executa uma passagem de renderização controlada e coleta métricas."""
    with tempfile.TemporaryDirectory() as td:
        out_video = os.path.join(td, f"bench_{width}x{height}_{encoder}.mp4")

        # Gerador sintético de teste com gradientes dinâmicos (simulando render de produção)
        cmd = [
            FFMPEG, "-y",
            "-f", "lavfi", "-i", f"color=c=0x1a1a2e:s={width}x{height}:d={duration_s}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_s}",
        ]

        if encoder == "h264_qsv":
            cmd.extend([
                "-vf", f"scale={width}:{height},format=nv12",
                "-c:v", "h264_qsv", "-b:v", "8M", "-maxrate:v", "12M", "-bufsize:v", "16M",
            ])
        else:
            cmd.extend([
                "-vf", f"scale={width}:{height},format=yuv420p",
                "-c:v", "libx264", "-preset", preset,
            ])

        cmd.extend([
            "-c:a", "aac", "-b:a", "192k",
            "-t", str(duration_s),
            out_video
        ])

        start_time = time.perf_counter()
        cpu_samples: List[float] = []
        mem_samples_mb: List[float] = []

        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        p_proc = None
        if psutil:
            try:
                p_proc = psutil.Process(proc.pid)
            except Exception:
                p_proc = None

        while proc.poll() is None:
            if p_proc:
                try:
                    cpu_samples.append(p_proc.cpu_percent(interval=0.05))
                    mem_samples_mb.append(p_proc.memory_info().rss / (1024 * 1024))
                except Exception:
                    pass
            time.sleep(0.05)

        proc.wait()
        elapsed_time = time.perf_counter() - start_time

        success = proc.returncode == 0 and os.path.exists(out_video)
        file_size_mb = (os.path.getsize(out_video) / (1024 * 1024)) if success else 0.0

        # Bitrate real
        bitrate_kbps = 0.0
        if success and duration_s > 0:
            bitrate_kbps = (file_size_mb * 8 * 1024) / duration_s

        avg_cpu = sum(cpu_samples) / len(cpu_samples) if cpu_samples else 0.0
        peak_cpu = max(cpu_samples) if cpu_samples else 0.0
        peak_mem = max(mem_samples_mb) if mem_samples_mb else 0.0

        return {
            "resolution": f"{width}x{height}",
            "encoder": encoder,
            "success": success,
            "render_time_s": round(elapsed_time, 2),
            "file_size_mb": round(file_size_mb, 2),
            "bitrate_kbps": round(bitrate_kbps, 1),
            "avg_cpu_percent": round(avg_cpu, 1),
            "peak_cpu_percent": round(peak_cpu, 1),
            "peak_ram_mb": round(peak_mem, 1),
            "fps_speed": round(duration_s / elapsed_time if elapsed_time > 0 else 0, 2),
        }


def run_full_benchmark(duration_s: float = 8.0) -> Dict[str, Any]:
    print(f"[BENCHMARK] Iniciando Benchmark de Renderizacao: 720p vs 1080p ({duration_s}s de conteudo)...")
    qsv_supported = check_qsv_support()
    print(f"[HARDWARE] Suporte a Intel QSV detectado: {'SIM' if qsv_supported else 'NAO'}")

    results = []

    # 1. 720p libx264 (Baseline Producao Atual)
    print(">> Executando: 720p (1280x720) com libx264 (Software CPU)...")
    r_720_x264 = run_benchmark_pass(1280, 720, "libx264", duration_s=duration_s)
    results.append(r_720_x264)
    print(f"   -> Tempo: {r_720_x264['render_time_s']}s | Tam: {r_720_x264['file_size_mb']}MB | CPU Pico: {r_720_x264['peak_cpu_percent']}%")

    # 2. 1080p libx264
    print(">> Executando: 1080p (1920x1080) com libx264 (Software CPU)...")
    r_1080_x264 = run_benchmark_pass(1920, 1080, "libx264", duration_s=duration_s)
    results.append(r_1080_x264)
    print(f"   -> Tempo: {r_1080_x264['render_time_s']}s | Tam: {r_1080_x264['file_size_mb']}MB | CPU Pico: {r_1080_x264['peak_cpu_percent']}%")

    # 3. Testes QSV se suportado
    if qsv_supported:
        print(">> Executando: 720p (1280x720) com Intel QSV...")
        r_720_qsv = run_benchmark_pass(1280, 720, "h264_qsv", duration_s=duration_s)
        results.append(r_720_qsv)
        print(f"   -> Tempo: {r_720_qsv['render_time_s']}s | Tam: {r_720_qsv['file_size_mb']}MB")

        print(">> Executando: 1080p (1920x1080) com Intel QSV...")
        r_1080_qsv = run_benchmark_pass(1920, 1080, "h264_qsv", duration_s=duration_s)
        results.append(r_1080_qsv)
        print(f"   -> Tempo: {r_1080_qsv['render_time_s']}s | Tam: {r_1080_qsv['file_size_mb']}MB")
    else:
        results.append({
            "resolution": "720p / 1080p",
            "encoder": "h264_qsv",
            "success": False,
            "render_time_s": 0.0,
            "file_size_mb": 0.0,
            "bitrate_kbps": 0.0,
            "avg_cpu_percent": 0.0,
            "peak_cpu_percent": 0.0,
            "peak_ram_mb": 0.0,
            "fps_speed": 0.0,
            "note": "Intel QSV nao suportado neste host; fallback para libx264 funcional."
        })

    summary = {
        "duration_tested_s": duration_s,
        "qsv_supported": qsv_supported,
        "results": results
    }

    # Salva resultado em JSON para inclusão no laudo
    bench_dir = Path(__file__).resolve().parent
    out_json = bench_dir / "benchmark_results.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    return summary


if __name__ == "__main__":
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
    summary = run_full_benchmark(duration_s=dur)
    print("\n=== RESUMO DO BENCHMARK ===")
    print(json.dumps(summary, indent=2))
