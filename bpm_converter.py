#!/usr/bin/env python3
"""
BPM Converter - 從 YouTube 下載音樂並轉換為指定 BPM
用法: python bpm_converter.py <YouTube URL> [--target-bpm 180] [--output ./output]
也可以直接轉換本地音檔: python bpm_converter.py <本地音檔路徑> --local
"""

import argparse
import os
import sys
import tempfile
import subprocess
import shutil

# Windows 終端機強制 UTF-8 輸出，避免 cp950 編碼錯誤
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def check_dependencies():
    missing = []
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        missing.append("yt-dlp")
    try:
        import librosa  # noqa: F401
    except ImportError:
        missing.append("librosa")
    try:
        import soundfile  # noqa: F401
    except ImportError:
        missing.append("soundfile")
    try:
        import numpy  # noqa: F401
    except ImportError:
        missing.append("numpy")

    if not shutil.which("ffmpeg"):
        missing.append("ffmpeg (請至 https://ffmpeg.org/download.html 下載並加入 PATH)")

    if missing:
        print("缺少以下依賴，請先安裝：")
        for dep in missing:
            if dep.startswith("ffmpeg"):
                print(f"  {dep}")
            else:
                print(f"  pip install {dep}")
        sys.exit(1)


def download_audio(url: str, output_dir: str) -> tuple:
    """從 YouTube 下載音訊，回傳 (檔案路徑, 標題)。"""
    import yt_dlp

    output_template = os.path.join(output_dir, "%(title)s.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "wav",
            "preferredquality": "192",
        }],
        "outtmpl": output_template,
        "noplaylist": True,  # 只下載單首，忽略播放清單
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get("title", "audio")

    wav_files = [f for f in os.listdir(output_dir) if f.endswith(".wav")]
    if not wav_files:
        raise FileNotFoundError("找不到下載的音訊檔案，請確認 ffmpeg 已安裝。")
    return os.path.join(output_dir, wav_files[0]), title


def detect_bpm(audio_path: str) -> float:
    """用 librosa 偵測音檔 BPM。"""
    import librosa

    print("  載入音檔分析中...")
    y, sr = librosa.load(audio_path, sr=None, mono=True)
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)

    if hasattr(tempo, "__len__"):
        tempo = float(tempo[0])
    else:
        tempo = float(tempo)
    return tempo


def build_atempo_chain(factor: float) -> str:
    """
    FFmpeg atempo 每個值限制在 [0.5, 2.0]，
    超過範圍時串接多個 atempo 濾鏡。
    """
    filters = []
    remaining = factor
    if factor > 1.0:
        while remaining > 2.0:
            filters.append("atempo=2.0")
            remaining /= 2.0
    else:
        while remaining < 0.5:
            filters.append("atempo=0.5")
            remaining /= 0.5
    filters.append(f"atempo={remaining:.6f}")
    return ",".join(filters)


def stretch_audio(input_path: str, output_path: str, original_bpm: float, target_bpm: float):
    """用 FFmpeg atempo 做時間拉伸（保持音調），輸出 MP3。"""
    tempo_factor = target_bpm / original_bpm
    filter_str = build_atempo_chain(tempo_factor)

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-af", filter_str,
        "-q:a", "2",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg 錯誤:\n{result.stderr}")


def safe_filename(title: str) -> str:
    allowed = set(" -_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    # 保留中文字元
    result = []
    for c in title:
        if c in allowed or "一" <= c <= "鿿":
            result.append(c)
        else:
            result.append("_")
    return "".join(result).strip("_ ")


def convert_from_url(url: str, target_bpm: float, output_dir: str):
    with tempfile.TemporaryDirectory() as temp_dir:
        print("\n[1/3] 下載 YouTube 音訊...")
        audio_path, title = download_audio(url, temp_dir)
        print(f"      下載完成：{title}")

        print("\n[2/3] 偵測 BPM...")
        original_bpm = detect_bpm(audio_path)
        print(f"      偵測結果：{original_bpm:.1f} BPM")
        print(f"      目標 BPM ：{target_bpm:.1f} BPM")

        _do_stretch(audio_path, title, original_bpm, target_bpm, output_dir)


def convert_from_file(input_path: str, target_bpm: float, output_dir: str):
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"找不到檔案：{input_path}")

    title = os.path.splitext(os.path.basename(input_path))[0]

    print("\n[1/2] 偵測 BPM...")
    original_bpm = detect_bpm(input_path)
    print(f"      偵測結果：{original_bpm:.1f} BPM")
    print(f"      目標 BPM ：{target_bpm:.1f} BPM")

    _do_stretch(input_path, title, original_bpm, target_bpm, output_dir)


def _do_stretch(audio_path: str, title: str, original_bpm: float, target_bpm: float, output_dir: str):
    if abs(original_bpm - target_bpm) < 0.5:
        print("\n原始 BPM 已接近目標，直接複製輸出。")
        out = os.path.join(output_dir, f"{safe_filename(title)}_{target_bpm:.0f}bpm.mp3")
        shutil.copy2(audio_path, out)
        print(f"\n輸出：{out}")
        return

    ratio = target_bpm / original_bpm
    print(f"\n[3/3] 時間拉伸中（速率比：{ratio:.3f}）...")

    out = os.path.join(output_dir, f"{safe_filename(title)}_{target_bpm:.0f}bpm.mp3")
    stretch_audio(audio_path, out, original_bpm, target_bpm)

    print(f"\n完成！")
    print(f"  原始 BPM：{original_bpm:.1f}  →  目標 BPM：{target_bpm:.1f}")
    print(f"  輸出檔案：{out}")


def main():
    parser = argparse.ArgumentParser(
        description="從 YouTube 下載音樂或轉換本地音檔到指定 BPM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
範例：
  # 從 YouTube 下載並轉為 180 BPM
  python bpm_converter.py "https://www.youtube.com/watch?v=xxxxx"

  # 自訂目標 BPM
  python bpm_converter.py "https://www.youtube.com/watch?v=xxxxx" --target-bpm 160

  # 轉換本地音檔
  python bpm_converter.py mysong.mp3 --local

  # 指定輸出資料夾
  python bpm_converter.py "https://..." --output ./converted
        """,
    )
    parser.add_argument("source", help="YouTube URL 或本地音檔路徑（搭配 --local）")
    parser.add_argument("--target-bpm", type=float, default=180.0, help="目標 BPM（預設：180）")
    parser.add_argument("--output", default=".", help="輸出資料夾（預設：當前目錄）")
    parser.add_argument("--local", action="store_true", help="輸入來源為本地音檔而非 YouTube URL")

    args = parser.parse_args()

    check_dependencies()
    os.makedirs(args.output, exist_ok=True)

    try:
        if args.local:
            convert_from_file(args.source, args.target_bpm, args.output)
        else:
            convert_from_url(args.source, args.target_bpm, args.output)
    except KeyboardInterrupt:
        print("\n已取消。")
    except Exception as e:
        print(f"\n錯誤：{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
