# Videosdk-Plugin
Integration of VideoSDK Agents and Avatario.ai

## Getting Started

### Prerequisites
- Python >= 3.11
- Stable Internet Connection
- Look into the [example env file](example.env) for environment variables that need to configured to run the project

### Installation
Install the necessary dependencies by running
```
pip install -r requirements.txt
```

### Running the Agent Session
Once all the above steps are completed, run the code using the following command:
```
python3 video_sdk_agent.py
```

### Implementation Details
- This implementation is inspired from the already existing 2 RTC room structure in one of the plugins. This plugin's entrypoint is in the [AvatarioAvatar](avatario_plugin.py#L223) class that requires [VideoInfo](avatario_plugin.py#L216) as input where you can choose the resolution, face_id, and custom_background(in retreivable url form).
- When the session initializes, [AvatarioClient](api.py#L169) creates the meeting and tokens using [this](meeting_utils.py#L42) which is required for our 2 participants to join, one of which we send to our backend along with the meeting id and the VideoInfo. This process only returns once the video rendering backend has joined the room as a participant in order to avoid the agent to start the conversation before the video rendering service is available.
- For information regarding how the the audio is sent to the backend, the videoSDK agent sends the tts audio bytes to the [handle_audio_input](avatario_plugin.py#L360) from where it is added to a queue, and there is a function in our [MeetingEventHandler](api.py#L82) that constantly seeks the values from that queue to further send it using DataChannels.
- For the Audio-Video Streams from the backend this [ParticipantHandler](api.py#L34) subscribes to them and stores the Audio-Video Frames in to a queue which is then retreived by these functions [_process_video_frames](avatario_plugin.py#L312) and [_process_audio_frames](avatario_plugin.py#L331) that are then added to the queues of [AvatarioVideoTrack](avatario_plugin.py#L168) and [AvatarioAudioTrack](avatario_plugin.py#L27) respectively for further streaming on the playground as agent's Audio-Video Streams

### Current Issues
- There is no Interruption Logic being passed to the video agent so it cannot stop speaking if interrupted in the middle by end user
- For some reason at times, even when I have set a [warning](api.py#L85) to check in case it have received audio bytes that are actually strings, I see no warning messages on the agent side of things, but in my backend I receive distored strings like this:
    ```
    /-*,-.,--0-++*,.1100-**)))))'&%%%&(((''&&%$$"&%))"#$"#%%$&'(&!$&&)))%(())%$+()'!#$'')%'#&('**)%%')'((')**+-.-///0122100/0+-,,-,1220142//2132/00127899==@A

    " " !"# #!%&&"!&,,+(%$"#'+29?>=62.)%,28>=<4.)$$'),35840*(+,.045540-),/68;;:?<869::>EEE</+,,.589<<8/)((((.25431-*),-3888:863.03<=>?;78:=BEE>742269:>@D:5*%(*->CGF:+"$1DPWWSIB=3.,05DJNG>775;>BHMPNLJ@5.(,0599558<;>><<>CHGD?>CBFA52-$"',-441'!'06;;96=@A@3&""+5<?:4 
                                                        2KYXH5#+6CLQOF9+! (7JWYRB60221.(%$(+,+-1.+,,-1434343;@JYfhaU

    ```
- The datachannel is still broadcasting the audio bytes to the entire room, which we should fix in case of 3 participant room usecase as it ends up being 2x costly.
