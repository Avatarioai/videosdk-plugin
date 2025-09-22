import asyncio
import aiohttp
from typing import Dict, Union

from videosdk import (
    MeetingConfig, 
    VideoSDK, 
    Participant, 
    Stream, 
    MeetingEventHandler, 
    ParticipantEventHandler, 
    ReliabilityModes,
)

from meeting_utils import create_meeting

BASE_URL = "https://app.onezot.work/api/sdk"

type ipc_dtype = Dict[str, Union[asyncio.Queue, asyncio.Event]]
type video_info_dtype = Dict[str, Union[int, str]]

class AvatarioParticipantHandler(ParticipantEventHandler):
    def __init__(
        self, 
        participant: Participant,
        ipc_data: ipc_dtype
    ):
        super().__init__()
        self.participant = participant
        self.ipc_data = ipc_data

        self.loop = asyncio.get_event_loop()

    def on_stream_enabled(self, stream: Stream):
        if stream.kind == "audio":
            self.loop.create_task(
                self.get_output_audio(
                    track=stream.track
                )
            )
        if stream.kind == "video":
            self.loop.create_task(
                self.get_output_video(
                    track=stream.track
                )
            )

    async def get_output_audio(self, track):
        while not self.ipc_data["kill_signal"].is_set():
            try:
                frame = await asyncio.wait_for(
                        track.recv(),
                        timeout=2
                    )
                await self.ipc_data["output_audio"].put(frame)
            except asyncio.TimeoutError:
                continue

    async def get_output_video(self, track):
        while not self.ipc_data["kill_signal"].is_set():
            try:
                frame = await asyncio.wait_for(
                        track.recv(),
                        timeout=2
                    )
                await self.ipc_data["output_video"].put(frame)
            except asyncio.TimeoutError:
                continue

class AvatarioMeetingHandler(MeetingEventHandler):
    def __init__(
        self, 
        meeting, 
        ipc_queues: ipc_dtype,
        participant_joined: asyncio.Event
    ):
        super().__init__()
        self.meeting = meeting
        self._ipc_data = ipc_queues
        self.participant_joined = participant_joined

    async def send_tts_audio(self):
        while True:
            audio_data = await self._ipc_data["tts_audio"].get()        
            if isinstance(audio_data, str):
                print(f"WARNING: tts AUDIO RECV AS STRING EXPECTED BYTES: {audio_data}")

            await self.meeting.send(
                audio_data,
                {"reliability": ReliabilityModes.UNRELIABLE.value}
            )

    def on_participant_joined(
        self, 
        participant: Participant,
    ):
        if participant.display_name == "backend_participant":
            participant.add_event_listener(
                AvatarioParticipantHandler(
                    participant=participant.id,
                    ipc_data=self._ipc_data
                )
            )

            self.participant_joined.set()

            asyncio.create_task(
                self.send_tts_audio()
            )

class AvatarioAgentParticipant:
    def __init__(self, ipc_queues):
        self.meeting = None
        self.name = "AgentParticipant"
        self._ipc_queues = ipc_queues
        self.participant_joined = asyncio.Event()

    def join_meeting(self, meeting_id, token):
        meeting_config = MeetingConfig(
            meeting_id=meeting_id,
            name=self.name,
            mic_enabled=False,
            webcam_enabled=False,
            token=token,
        )

        # Initialize the meeting
        self.meeting = VideoSDK.init_meeting(**meeting_config)

        self.meeting.add_event_listener(
            AvatarioMeetingHandler(
                meeting=self.meeting,
                ipc_queues=self._ipc_queues,
                participant_joined=self.participant_joined
            )
        )

        # Join the meeting
        self.meeting.join()


class AvatarioClient:
    """Helper class for interacting with the Avatario API (v2).

    Provides methods for creating and managing conversations with Avatario avatars,
    including conversation lifecycle management.
    """

    def __init__(
            self,
            api_key: str,
            ipc_data: ipc_dtype,
        ):
        """Initialize the AvatarioApi client.

        Args:
            api_key: Avatario API key for authentication.
            session: An aiohttp session for making HTTP requests.
        """

        assert api_key is not None, "AVATARIO_API_KEY must be set in the environment variable"
        self._api_key = api_key
        self._agent_participant = AvatarioAgentParticipant(ipc_data)
        self._headers = {
            "Content-Type": "application/json",
            "x-api-key": self._api_key
        }
        
    async def create_conversation(
        self,
        video_info: video_info_dtype,
    ) -> dict:
        """Create a new conversation with the specified AVATAR_ID.

        Args:
            avatar_id: ID of the replica to use in the conversation.
            video_info: Dictionary containing video metadata like avatar_id, 
            frame height, frame width, and custom background url
        """

        url = f"{BASE_URL}/start-session"
        meeting_info =  create_meeting()
        payload = {
            "agent_id": "backend_participant",
            "livekit": {
                "url": meeting_info["id"],
                "token": meeting_info["backend_token"]
            }
        }
        payload.update(video_info)

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self._headers, json=payload) as r:
                r.raise_for_status()
                response = await r.json()
                print(f"Created Avatario conversation: {response}")
        self._agent_participant.join_meeting(
            meeting_info["id"],
            meeting_info["token"],
        )

        await self._agent_participant.participant_joined.wait()

        return
