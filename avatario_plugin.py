import os
import time
import asyncio
import fractions
import numpy as np

from dataclasses import dataclass, asdict
from typing import Optional
from av import AudioFrame, VideoFrame
from vsaiortc.mediastreams import MediaStreamError
from videosdk import CustomVideoTrack, CustomAudioTrack

from api import AvatarioClient


# --- Constants ---
AUDIO_SAMPLE_RATE = 48000
AUDIO_CHANNELS = 1
AUDIO_SAMPLE_WIDTH = 2
AUDIO_FRAME_DURATION_S = 0.02
AUDIO_SAMPLES_PER_FRAME = int(AUDIO_FRAME_DURATION_S * AUDIO_SAMPLE_RATE)
AUDIO_CHUNK_SIZE = AUDIO_SAMPLES_PER_FRAME * AUDIO_CHANNELS * AUDIO_SAMPLE_WIDTH
AUDIO_TIME_BASE_FRACTION = fractions.Fraction(1, AUDIO_SAMPLE_RATE)
VIDEO_FRAME_RATE = 25
VIDEO_TIME_BASE = 90000

class AvatarioAudioTrack(CustomAudioTrack):
    def __init__(self, loop):
        super().__init__()
        self.kind = "audio"
        self.loop = loop
        self._timestamp = 0
        self.queue = asyncio.Queue(maxsize=10)
        self.audio_data_buffer = bytearray()
        self.frame_time = 0
        self.sample_rate = AUDIO_SAMPLE_RATE
        self.channels = AUDIO_CHANNELS
        self.sample_width = AUDIO_SAMPLE_WIDTH
        self.time_base_fraction = AUDIO_TIME_BASE_FRACTION
        self.samples = AUDIO_SAMPLES_PER_FRAME
        self.chunk_size = AUDIO_CHUNK_SIZE
        self._start_time = None
        self._shared_start_time = None
        self._frame_duration = AUDIO_FRAME_DURATION_S
        self._last_frame_time = 0
        self._frame_count = 0

    def interrupt(self):
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self.audio_data_buffer.clear()

    async def add_new_bytes(self, audio_data: bytes):
        """Required method for compatibility with existing audio track interface"""
        self.audio_data_buffer += audio_data

        while len(self.audio_data_buffer) >= self.chunk_size:
            chunk = self.audio_data_buffer[:self.chunk_size]
            self.audio_data_buffer = self.audio_data_buffer[self.chunk_size:]
            try:
                audio_frame = self.buildAudioFrames(chunk)
                if self.queue.full():
                    # Drop oldest frame
                    self.queue.get_nowait()
                self.queue.put_nowait(audio_frame)
            except Exception as e:
                break

    def buildAudioFrames(self, chunk: bytes) -> AudioFrame:
        if len(chunk) != self.chunk_size:
            print(f"Warning: Incorrect Avatario chunk size received {len(chunk)}, expected {self.chunk_size}")

        if len(chunk) % 2 != 0:
            chunk = chunk + b'\x00'

        data = np.frombuffer(chunk, dtype=np.int16)
        expected_samples = self.samples * self.channels
        if len(data) != expected_samples:
            print(f"Warning: Incorrect number of samples in Avatario chunk {len(data)}, expected {expected_samples}")

        data = data.reshape(-1, self.channels)
        layout = "mono" if self.channels == 1 else "stereo"

        audio_frame = AudioFrame.from_ndarray(data.T, format="s16", layout=layout)
        return audio_frame

    def next_timestamp(self):
        pts = int(self.frame_time)
        time_base = self.time_base_fraction
        self.frame_time += self.samples
        return pts, time_base

    async def recv(self) -> AudioFrame:
        """Return next audio frame to VideoSDK."""
        try:
            if self.readyState != "live":
                raise MediaStreamError

            if self._start_time is None:
                self._start_time = time.time()
                self._timestamp = 0
            else:
                self._timestamp += self.samples
            wait = self._start_time + (self._timestamp / self.sample_rate) - time.time()
            if wait > 0:
                await asyncio.sleep(wait)

            pts = self._timestamp
            time_base = self.time_base_fraction

            try:
                frame = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                frame = self._create_silence_frame()

            frame.pts = pts
            frame.time_base = time_base
            frame.sample_rate = self.sample_rate
            return frame

        except Exception as e:
            import traceback
            traceback.print_exc()
            return self._create_silence_frame()
    
    def _create_silence_frame(self) -> AudioFrame:
        """Create a properly formatted silence frame"""
        layout = "mono" if self.channels == 1 else "stereo"
        frame = AudioFrame(format="s16", layout=layout, samples=self.samples)
        for p in frame.planes:
            p.update(bytes(p.buffer_size))
        frame.sample_rate = self.sample_rate
        return frame
            
    async def cleanup(self):
        self.interrupt()
        self.stop()

    def add_frame(self, frame: AudioFrame):
        """Add frame from Avatario stream - add AudioFrame directly to buffer with quality validation"""
        if frame is None:
            return
        try:
            if hasattr(frame, 'sample_rate') and frame.sample_rate != self.sample_rate:
                frame.sample_rate = self.sample_rate
                print("frame has different sample rate then expected")
            
            try:
                if self.queue.full():
                    self.queue.get_nowait()
                self.queue.put_nowait(frame)
            except asyncio.QueueEmpty:
                pass
            except asyncio.QueueFull:
                print("Avatario: Audio frame queue is full. Frame dropped.")
                
        except Exception as e:
            print(f"Error adding Avatario audio frame: {e}")
            try:
                array = frame.to_ndarray()
            except:
                pass


class AvatarioVideoTrack(CustomVideoTrack):
    def __init__(self):
        super().__init__()
        self.kind = "video"
        self.queue = asyncio.Queue(maxsize=2)
        self._timestamp = 0
        self._start_time = None
        self._frame_count = 0
        self._readyState = "live"
        self._frame_rate = VIDEO_FRAME_RATE
        self._frame_duration = 1.0 / self._frame_rate
        self._shared_start_time = None

    @property
    def readyState(self):
        return self._readyState

    async def recv(self) -> VideoFrame:
        frame = await self.queue.get()
        if self._start_time is None:
            self._start_time = self._shared_start_time if self._shared_start_time else time.time()
            self._timestamp = 0
        
        current_time = time.time()
        elapsed = current_time - self._start_time
        self._timestamp = int(elapsed * VIDEO_TIME_BASE)
        
        frame.pts = self._timestamp
        frame.time_base = fractions.Fraction(1, VIDEO_TIME_BASE)
        
        self._frame_count += 1

        return frame

    def add_frame(self, frame: VideoFrame):
        # Keep only the latest frame by clearing the queue before adding a new one.
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            pass

@dataclass
class VideoInfo:
    avatario_face_id: str
    video_height: int = 720
    video_width: int = 1280
    custom_background_url: Optional[str] = None


class AvatarioAvatar:
    def __init__(
        self,
        video_info: Optional[VideoInfo]=None
    ):
        super().__init__()
        self.video_info = video_info if video_info is not None else VideoInfo()
        self._stream_start_time = None
        self.audio_track = None
        self.run = True
        self._is_speaking = False
        self._speech_timeout_task = None
        self.ready = asyncio.Event()
        self._avatar_speaking = False
        self._last_reconnect_attempt = 0
        self._message_handler_task = None
        self._retry_count = 3
        self._last_error = None
        self._stopping = False
        self._last_audio_time = 0


        self.ipc_data = {
            "tts_audio": asyncio.Queue(),
            "output_audio": asyncio.Queue(),
            "output_video": asyncio.Queue(),
            "kill_signal": asyncio.Event(),
        }
        self.avatario_client = AvatarioClient(
            os.getenv("AVATARIO_API_KEY"),
            self.ipc_data,
        )
        self.video_streaming_task = None
        self.audio_streaming_task = None

        self.video_track = AvatarioVideoTrack()

    async def connect(self):
        loop = asyncio.get_event_loop()
        self.audio_track = AvatarioAudioTrack(loop)
        
        if self._stream_start_time is None:
            self._stream_start_time = time.time()
            self.video_track._shared_start_time = self._stream_start_time
            self.audio_track._shared_start_time = self._stream_start_time
        
        await self._initialize_connection(loop)
        
        if hasattr(self.video_track, 'start'):
            self.video_track.start()
        if hasattr(self.audio_track, 'start'):
            self.audio_track.start()
        
        self._last_audio_time = time.time()

    async def _initialize_connection(self, loop):
        """Initialize connection with retry logic """
        if self._retry_count == 0:
            raise Exception(f"Failed to connect to Avatario servers. Last error: {self._last_error}")
        
        try:
            await self.avatario_client.create_conversation(
                video_info=asdict(self.video_info)
            )
            self.ready.set()

            self.video_streaming_task = loop.create_task(
                self._process_video_frames()
            )
            self.audio_streaming_task  = loop.create_task(
                self._process_audio_frames()
            )

        except Exception as e:
            self._last_error = e
            self._retry_count -= 1
            if self._retry_count > 0:
                await asyncio.sleep(2) 
                await self._initialize_connection()
            else:
                raise

    async def _cleanup_connections(self):
        """Clean up existing connections before creating new ones"""
        self.ready.clear()
        self._is_speaking = False
        if self._speech_timeout_task and not self._speech_timeout_task.done():
            self._speech_timeout_task.cancel()

    async def _process_video_frames(self):
        """Simple video frame processing for real-time playback"""
        frame_count = 0
        while self.run and not self._stopping:
            try:
                frame = await self.ipc_data["output_video"].get()
                if frame is None:
                    continue
                    
                frame_count += 1
                self.video_track.add_frame(frame)

            except Exception as e:
                print(f"Avatario: Video processing error: {e}")
                if not self.run or self._stopping:
                    break
                await asyncio.sleep(0.1)
                continue

    async def _process_audio_frames(self):
        """Simple audio frame processing for real-time playback"""
        frame_count = 0
        while self.run and not self._stopping:
            try:
                frame = await self.ipc_data["output_audio"].get()
                    
                if frame is None:
                    print("Avatario: Received None audio frame, continuing...")
                    continue
                    
                frame_count += 1
                
                try:
                    if not hasattr(frame, 'sample_rate') or frame.sample_rate != AUDIO_SAMPLE_RATE:
                        frame.sample_rate = AUDIO_SAMPLE_RATE
                        
                    self.audio_track.add_frame(frame)
                    
                except Exception as frame_error:
                    print(f"Avatario: Error processing audio frame #{frame_count}: {frame_error}")
                    continue        
            except Exception as e:
                print(f"Avatario: Audio processing error: {e}")
                if not self.run or self._stopping:
                    break
                await asyncio.sleep(0.1)
                continue

    async def handle_audio_input(self, audio_data: bytes):
        if not self.run or self._stopping:
            return
            
        if self.ready.is_set():
            try:
                if len(audio_data) % 2 != 0:
                    audio_data = audio_data + b'\x00'
                await self._send_audio_data(audio_data)

            except Exception as e:
                print(f"Error processing/sending audio data: {e}")
        else:
            print(f"Avatario: Cannot send audio - ws available: {self.ws is not None}, ready: {self.ready.is_set()}")

    async def _send_audio_data(self, data: bytes):
        """Send audio data via datachannel to Avatario Backend """
        try:            
            for i in range(0, len(data), 6000):
                chunk = data[i:i + 6000]
                await self.ipc_data["tts_audio"].put(chunk)
        except Exception as e:
            print(f"Error sending audio data via data channel: {e}")

    async def aclose(self):
        if self._stopping:
            return
        self._stopping = True
        self.run = False
        
        if self._speech_timeout_task and not self._speech_timeout_task.done():
            self._speech_timeout_task.cancel()

        self.ipc_data["kill_signal"].set()
        
        await self._cleanup_connections()

    def set_agent(self, agent):
        pass
        
