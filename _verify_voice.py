"""P0.4 语音识别运行时验证：ffmpeg → edge-tts 合成 → whisper 转写 往返测试。

用法（新 backend 容器内）: python /tmp/verify_voice.py
"""
import asyncio
import sys
import warnings

warnings.filterwarnings("ignore")

PHRASE = "依据民法典第一百零七十九条，夫妻一方要求离婚的，可以由有关组织进行调解。"


def main():
    import shutil

    ffmpeg = shutil.which("ffmpeg")
    print(f"[1] ffmpeg: {ffmpeg if ffmpeg else 'MISSING'}")
    if not ffmpeg:
        sys.exit(1)

    # [2] TTS 合成测试音频
    async def synth():
        import edge_tts

        communicate = edge_tts.Communicate(PHRASE, "zh-CN-XiaoxiaoNeural")
        await communicate.save("/tmp/voice_test.mp3")

    asyncio.run(synth())
    import os

    size = os.path.getsize("/tmp/voice_test.mp3")
    print(f"[2] edge-tts 合成: /tmp/voice_test.mp3 ({size} bytes)")
    if size == 0:
        sys.exit(1)

    # [3] Whisper 转写
    import whisper

    print("[3] 加载 Whisper base 模型 ...")
    model = whisper.load_model("base", download_root="/app/models/whisper")
    result = model.transcribe("/tmp/voice_test.mp3", language="zh")
    text = result.get("text", "").strip()
    print(f"[3] Whisper 转写结果: {text}")

    # [4] 相似度判断（字符级重叠）
    import difflib

    ratio = difflib.SequenceMatcher(None, PHRASE, text).ratio()
    print(f"[4] 与原文相似度: {ratio:.0%}")
    ok = ratio > 0.6 and "民法典" in text
    print("VOICE RUNTIME: " + ("PASS" if ok else "FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
