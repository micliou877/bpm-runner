#!/usr/bin/env python3
"""BPM Converter Web App"""

import io
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file

AUDIO_EXTENSIONS = frozenset({".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".opus"})

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

DOWNLOADS_DIR = Path(__file__).parent / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)

jobs: dict = {}

app = Flask(__name__)


# 智慧模式：與踏頻同步的音樂倍率（目標BPM = 踏頻 * p/q）
_SMART_RATIOS = [
    (1, 1, "每拍一步"),
    (1, 2, "每兩拍一步"),
    (1, 3, "每三拍一步"),
    (1, 4, "每四拍一步"),
    (2, 3, "三步配兩拍"),
    (3, 4, "四步配三拍"),
    (4, 5, "五步配四拍"),
    (3, 2, "兩步配三拍"),
]

def find_smart_target(original_bpm: float, cadence: float = 180.0) -> tuple:
    """回傳 (target_bpm, stretch_pct, description) 失真最小的音樂倍率目標。
    只考慮 >= original_bpm 的目標（只能加速，不能減速）。"""
    best = None
    for p, q, desc in _SMART_RATIOS:
        target = cadence * p / q
        if not (40 <= target <= 220):
            continue
        if target < original_bpm:          # 只能加速
            continue
        stretch = abs(original_bpm - target) / original_bpm * 100
        if best is None or stretch < best[0]:
            best = (stretch, round(target, 1), desc)
    return best  # (stretch_pct, target_bpm, description)


def safe_filename(title: str) -> str:
    result = []
    for c in title:
        if c.isalnum() or c in " -_" or "一" <= c <= "鿿":
            result.append(c)
        else:
            result.append("_")
    return "".join(result).strip("_ ") or "audio"


def build_atempo_chain(factor: float) -> str:
    filters = []
    remaining = factor
    if factor >= 1.0:
        while remaining > 2.0:
            filters.append("atempo=2.0")
            remaining /= 2.0
    else:
        while remaining < 0.5:
            filters.append("atempo=0.5")
            remaining /= 0.5
    filters.append(f"atempo={remaining:.6f}")
    return ",".join(filters)



def get_audio_duration(path: str) -> float | None:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        data = json.loads(result.stdout)
        return float(data.get("format", {}).get("duration", 0)) or None
    return None


def run_conversion(job_id: str, url: str, target_bpm: float):
    import librosa
    import yt_dlp

    job = jobs[job_id]

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            # ── Step 1: Download ──────────────────────────────────────
            job.update(status="downloading", progress=5, message="準備下載...")

            title = "audio"

            def progress_hook(d):
                if d["status"] == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 1
                    done = d.get("downloaded_bytes", 0)
                    pct = int(5 + (done / total) * 35)
                    job["progress"] = min(pct, 39)
                    job["message"] = f"下載中... {int(done/total*100)}%"
                elif d["status"] == "finished":
                    job["progress"] = 40
                    job["message"] = "下載完成"

            ydl_opts = {
                "format": "bestaudio/best",
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav", "preferredquality": "192"}],
                "outtmpl": os.path.join(temp_dir, "%(title)s.%(ext)s"),
                "noplaylist": True,
                "progress_hooks": [progress_hook],
                "quiet": True,
                "no_warnings": True,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get("title", "audio")

            job["title"] = title

            wav_files = list(Path(temp_dir).glob("*.wav"))
            if not wav_files:
                raise FileNotFoundError("下載失敗，找不到音訊檔案，請確認 URL 正確且影片可播放")
            wav_path = str(wav_files[0])

            # ── Step 2: Detect BPM ────────────────────────────────────
            job.update(status="analyzing", progress=45, message="偵測 BPM 中...")

            y, sr = librosa.load(wav_path, sr=None, mono=True)
            tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
            original_bpm = float(tempo[0] if hasattr(tempo, "__len__") else tempo)

            job["original_bpm"] = round(original_bpm, 1)

            # 規則：只能加速，不能減速
            if not job.get("smart") and target_bpm < original_bpm:
                raise ValueError(
                    f"目標 BPM ({target_bpm:.0f}) 低於原始 BPM ({original_bpm:.1f})，"
                    f"依規則只能加速不能減速，請選擇 {int(original_bpm) + 1} BPM 以上"
                )

            # 智慧模式：自動選最小失真的音樂倍率
            smart_reason = None
            if job.get("smart"):
                stretch_pct, smart_target, desc = find_smart_target(original_bpm, target_bpm)
                smart_reason = f"{desc}（失真 {stretch_pct:.1f}%）"
                target_bpm = smart_target
                job["smart_reason"] = smart_reason

            job.update(progress=60, message=f"偵測到 {original_bpm:.1f} BPM，轉換為 {target_bpm:.0f} BPM...")

            # ── Step 3: Time-stretch ──────────────────────────────────
            job.update(status="converting", progress=65, message=f"轉換為 {target_bpm:.0f} BPM 中...")

            safe_title = safe_filename(title)
            output_filename = f"{job_id}_{safe_title}_{target_bpm:.0f}bpm.mp3"
            output_path = str(DOWNLOADS_DIR / output_filename)

            filter_str = build_atempo_chain(target_bpm / original_bpm)
            total_duration = get_audio_duration(wav_path)

            cmd = [
                "ffmpeg", "-y",
                "-i", wav_path,
                "-af", filter_str,
                "-q:a", "2",
                "-progress", "pipe:1",
                "-nostats",
                output_path,
            ]

            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            # Drain stderr to prevent pipe deadlock
            def drain_stderr():
                for _ in proc.stderr:
                    pass

            threading.Thread(target=drain_stderr, daemon=True).start()

            for line in proc.stdout:
                line = line.strip()
                if line.startswith("out_time_ms=") and total_duration:
                    try:
                        ms = int(line.split("=")[1])
                        frac = (ms / 1_000_000) / total_duration
                        job["progress"] = min(int(65 + frac * 30), 94)
                    except (ValueError, ZeroDivisionError):
                        pass

            proc.wait()
            if proc.returncode != 0:
                raise RuntimeError("FFmpeg 轉換失敗，請重試")

            job.update(
                status="done",
                progress=100,
                message="轉換完成！",
                output_file=output_filename,
                display_name=f"{safe_title}_{target_bpm:.0f}bpm.mp3",
                target_bpm=target_bpm,
            )

    except Exception as e:
        job.update(status="error", progress=0, message=f"錯誤：{e}")


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/convert", methods=["POST"])
def convert():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    try:
        target_bpm = float(data.get("bpm", 180))
    except (TypeError, ValueError):
        return jsonify({"error": "BPM 格式錯誤"}), 400

    if not url:
        return jsonify({"error": "請輸入 YouTube 網址"}), 400
    if not (60 <= target_bpm <= 300):
        return jsonify({"error": "BPM 請輸入 60–300 之間的數值"}), 400

    smart = bool(data.get("smart", False))

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "pending",
        "progress": 0,
        "message": "準備中...",
        "output_file": None,
        "display_name": None,
        "title": None,
        "original_bpm": None,
        "target_bpm": target_bpm,
        "smart": smart,
        "smart_reason": None,
    }

    threading.Thread(target=run_conversion, args=(job_id, url, target_bpm), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "找不到此任務"}), 404
    return jsonify(job)


@app.route("/download/<job_id>")
def download(job_id):
    job = jobs.get(job_id)
    if not job or job["status"] != "done":
        abort(404)
    output_path = DOWNLOADS_DIR / job["output_file"]
    if not output_path.exists():
        abort(404)
    return send_file(
        str(output_path),
        as_attachment=True,
        download_name=job["display_name"],
        mimetype="audio/mpeg",
    )


@app.route("/player")
def player_page():
    return render_template("player.html")


@app.route("/runner")
def runner_page():
    return render_template("runner.html")


@app.route("/api/music-files")
def api_music_files():
    folder = request.args.get("folder", "").strip()
    if not folder:
        folder = str(DOWNLOADS_DIR)

    p = Path(folder)
    if not p.is_dir():
        return jsonify({"error": f"找不到資料夾：{folder}"}), 400

    files = []
    for f in sorted(p.iterdir()):
        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS:
            files.append({"name": f.stem, "filename": f.name, "path": str(f.resolve())})

    return jsonify({"files": files, "folder": str(p.resolve()), "count": len(files)})


@app.route("/api/audio")
def api_audio():
    filepath = request.args.get("path", "").strip()
    if not filepath:
        abort(400)

    p = Path(filepath).resolve()
    if not p.is_file() or p.suffix.lower() not in AUDIO_EXTENSIONS:
        abort(404)

    mime, _ = mimetypes.guess_type(str(p))
    return send_file(str(p), mimetype=mime or "audio/mpeg", conditional=True)


if __name__ == "__main__":
    import webbrowser

    ffmpeg_ok = shutil.which("ffmpeg") is not None
    if not ffmpeg_ok:
        print("錯誤：找不到 ffmpeg，請確認已安裝並加入 PATH")
        sys.exit(1)

    print("=" * 45)
    print("  BPM Converter 啟動")
    print("  開啟瀏覽器：http://localhost:5000")
    print("  按 Ctrl+C 停止")
    print("=" * 45)
    threading.Timer(1.2, lambda: webbrowser.open("http://localhost:5000")).start()
    app.run(debug=False, host="0.0.0.0", port=5000)
