"""Scripted WebRTC client (aiortc) that verifies a real voice call end to
end without a human or a real browser microphone.

Why this exists: the Claude Code Browser pane (and most CI sandboxes)
block getUserMedia, so a real click-through test of `/voice` can't exercise
real audio. This script does the same SDP offer/answer exchange a browser
would, sends a silent audio track (so Sarvam STT never transcribes
anything - this only proves the pipeline itself is wired correctly, not a
full conversation), and checks that real synthesized speech audio (not
silence padding) comes back for the call's opening line.

Requires: `pip install -e ".[voice]"`, SARVAM_API_KEY set, and a running
`uvicorn app.main:app` on the given base URL.

Usage:
    # place a call first
    curl -s -X POST http://127.0.0.1:8000/api/voice/start \\
        -H "Content-Type: application/json" \\
        -d '{"scenario_id": "card_expired"}'
    # then, using the call_id from that response:
    python scripts/verify_voice_call.py call_xxxxxxxxxx
"""

import argparse
import asyncio
import fractions
import struct

import httpx
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import AudioStreamTrack
from av import AudioFrame

# Below this max-abs-sample-value, received audio is silence padding, not
# real speech - distinguishes "frames arrived" from "TTS actually spoke".
_SILENCE_THRESHOLD = 200


class SilentAudioTrack(AudioStreamTrack):
    kind = "audio"

    def __init__(self, sample_rate: int = 8000, frame_ms: int = 20):
        super().__init__()
        self._pts = 0
        self._sample_rate = sample_rate
        self._samples_per_frame = sample_rate * frame_ms // 1000

    async def recv(self):
        await asyncio.sleep(self._samples_per_frame / self._sample_rate)
        frame = AudioFrame(format="s16", layout="mono", samples=self._samples_per_frame)
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        frame.sample_rate = self._sample_rate
        frame.pts = self._pts
        frame.time_base = fractions.Fraction(1, self._sample_rate)
        self._pts += self._samples_per_frame
        return frame


async def verify(base_url: str, call_id: str, listen_secs: float) -> bool:
    pc = RTCPeerConnection()
    pc.addTrack(SilentAudioTrack())
    received: list[AudioFrame] = []

    @pc.on("track")
    def on_track(track):
        print(f"[verify] received remote track: kind={track.kind}")

        async def consume():
            try:
                while True:
                    received.append(await track.recv())
            except Exception:
                pass  # track ended - normal at hangup

        asyncio.ensure_future(consume())

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    for _ in range(50):  # non-trickle: wait for full ICE gathering, like a browser would
        if pc.iceGatheringState == "complete":
            break
        await asyncio.sleep(0.1)

    local = pc.localDescription
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            f"{base_url}/api/voice/{call_id}/offer",
            json={"sdp": local.sdp, "type": local.type},
            timeout=30,
        )
    if resp.status_code != 200:
        print(f"[verify] FAIL: offer rejected ({resp.status_code}): {resp.text}")
        await pc.close()
        return False

    answer = resp.json()
    print(f"[verify] connected, pc_id={answer.get('pc_id')}")
    await pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))

    print(f"[verify] listening for {listen_secs}s...")
    await asyncio.sleep(listen_secs)
    await pc.close()

    if not received:
        print("[verify] FAIL: no audio frames received at all")
        return False

    max_abs = 0
    for frame in received:
        raw = bytes(frame.planes[0])
        samples = struct.unpack(f"<{len(raw) // 2}h", raw)
        max_abs = max(max_abs, max(abs(s) for s in samples))

    total_bytes = sum(f.planes[0].buffer_size for f in received)
    print(f"[verify] {len(received)} frames, {total_bytes} bytes, max|sample|={max_abs}")
    if max_abs <= _SILENCE_THRESHOLD:
        print("[verify] FAIL: frames received but they're silence, not real speech")
        return False

    print("[verify] PASS: real speech audio flowed back over WebRTC")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("call_id", help="call_id from POST /api/voice/start")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--listen-secs", type=float, default=8.0)
    args = parser.parse_args()

    ok = asyncio.run(verify(args.base_url, args.call_id, args.listen_secs))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
