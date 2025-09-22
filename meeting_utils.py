import os
import datetime
from typing import (
    Dict,
    Union,
    Optional
)

import jwt
import requests

from dotenv import load_dotenv
load_dotenv()

def generate_token(meeting_id: str) -> str:
    """
    Generate Emphemral token for a provided meeting ID to let participants join
    """
    expiration_in_seconds = 600
    expiration = datetime.datetime.now() + datetime.timedelta(seconds=expiration_in_seconds)
    payload={
        'exp': expiration,
        'apikey': os.getenv("BACKEND_VIDEOSDK_API_KEY"),
        'permissions': ["allow_join", "allow_mod"],
    }

    if meeting_id is not None:
        payload['version'] = 2
        payload['roles'] = ["rtc"]

    payload['roomId'] = meeting_id

    token = jwt.encode(
        payload=payload,
        key=os.getenv("BACKEND_VIDEOSDK_SECRET_KEY"),
        algorithm= "HS256"
    )

    return token


def create_meeting(auth_token: Optional[str]=None) -> Union[str, Dict[str, str]]:
    """
    Create a new meeting, the auth_token is only provided for the case of generating
    a meeting for the agent playground. If not given we first generate the meeting and then
    its tokens for 2 participants, one that joins with the agent and one for the video rendering
    and streaming backend
    """
    url = f"{os.getenv('VIDEOSDK_API_ENDPOINT')}/rooms"
    
    generate_for_backend = True
    if auth_token is not None:
        generate_for_backend = False

    headers = {
        "authorization": auth_token if auth_token is not None else os.getenv("BACKEND_VIDEOSDK_AUTH_TOKEN"),
        "Content-Type": "application/json"
    }
    response = requests.post(url, headers=headers, json={})
    response_data = response.json()
    room_id = response_data.get("roomId")

    if not generate_for_backend:
        return room_id

    return {
        "id": room_id,
        "token": generate_token(room_id),
        "backend_token": generate_token(room_id),
    }
