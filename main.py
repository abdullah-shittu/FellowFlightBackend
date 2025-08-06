# main.py
import uvicorn
import os
import httpx
from fastapi import FastAPI, Depends, HTTPException, status, Query
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request, Form
from fastapi.responses import JSONResponse
import urllib.parse
from fastapi import Request
import logging

user_scope = urllib.parse.quote(
    "identity.basic,identity.email,identity.team,identity.avatar"
)
bot_scope = urllib.parse.quote("chat:write,im:write")

import db_utils
import auth
from models import (
    UserResponse,
    UserUpdate,
    FlightCreate,
    FlightResponse,
    Token,
    MatchResponse,
    FormDataModel,
)
from typing import Dict, Any

# --- App Setup ---
app = FastAPI(
    title="FlightMate API",
    description="API for matching travelers on the same flights.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Add Hoppscotch or "*" if okay
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Internal Notification Function ---
def _send_slack_dm(slack_id: str, message: str):
    """Placeholder function to send a Slack DM."""
    bot_token = os.getenv("SLACK_BOT_TOKEN")
    if not bot_token:
        print("WARNING: SLACK_BOT_TOKEN not set. Skipping notification.")
        return

    try:
        with httpx.Client() as client:
            response = client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {bot_token}"},
                json={"channel": slack_id, "text": message},
            )
            response.raise_for_status()
            print(f"Successfully sent notification to {slack_id}")
    except httpx.HTTPStatusError as e:
        print(f"Error sending Slack DM to {slack_id}: {e.response.text}")


def _trigger_match_notifications(new_flight_id: int, user_info: Dict[str, Any]):
    """Internal function to find and notify matches for a new flight entry."""
    print(
        f"Triggering notifications for new flight {new_flight_id} by {user_info['name']}"
    )

    same_flight_matches = db_utils.find_matches_for_flight(
        new_flight_id, user_info["id"]
    )
    overlap_matches = db_utils.find_overlaps_for_flight(new_flight_id, user_info["id"])

    message = f"You have a new FlightMate! {user_info['name']} just registered a flight that matches yours."

    notified_users = set()
    for match in same_flight_matches + overlap_matches:
        if match["slack_id"] not in notified_users:
            _send_slack_dm(match["slack_id"], message)
            notified_users.add(match["slack_id"])


# --- Endpoints ---


@app.get("/", include_in_schema=False)
async def root():
    return {"message": "Welcome to the FlightMate API. See /docs for documentation."}


# == Authentication ==
@app.get("/api/v1/auth/slack", tags=["Authentication"])
async def auth_slack_login():
    """Initiates the Slack OAuth2 flow by redirecting the user."""
    client_id = os.getenv("SLACK_CLIENT_ID")
    user_scope = urllib.parse.quote(
        "identity.basic,identity.email,identity.team,identity.avatar"
    )
    bot_scope = urllib.parse.quote(
        "chat:write,im:write,users.profile:read"
    )  # only include if you're installing a bot
    redirect_url = f"https://slack.com/oauth/v2/authorize?client_id={client_id}&user_scope={user_scope}&scope={bot_scope}"
    return RedirectResponse(url=redirect_url)


@app.get("/api/v1/auth/slack/callback", tags=["Authentication"], response_model=Token)
async def auth_slack_callback(code: str, state=None):
    """
    Callback for Slack OAuth. Exchanges code for a user token, creates the user
    in the DB if they don't exist, and returns a JWT.
    """
    token_url = "https://slack.com/api/oauth.v2.access"
    payload = {
        "code": code,
        "client_id": os.getenv("SLACK_CLIENT_ID"),
        "client_secret": os.getenv("SLACK_CLIENT_SECRET"),
    }
    # return payload
    async with httpx.AsyncClient() as client:
        response = await client.post(token_url, data=payload)

    if not response.is_success or not response.json().get("ok"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Slack token exchange failed.",
        )

    data = response.json()
    authed_user = data.get("authed_user", {})
    slack_id = authed_user.get("id")
    # print(data)
    # Get user's profile name from Slack
    profile_url = "https://slack.com/api/users.identity"
    async with httpx.AsyncClient() as client:
        profile_res = await client.get(
            profile_url,
            headers={"Authorization": f"Bearer {authed_user.get('access_token')}"},
            # params={"user": slack_id},
        )
    #

    profile_data = profile_res.json()
    user_name = profile_data.get("user", {}).get("name", "Unknown User")
    print(user_name)
    image_url = profile_data.get("user", {}).get("image_192", "slack.com")
    print(image_url)
    # print(profile_data, user_name, authed_user.get("access_token"))
    # return authed_user
    # Find or create user in our database
    user_in_db = db_utils.find_or_create_user(slack_id, user_name)

    # Create JWT for our application
    access_token = auth.create_access_token(data={"sub": user_in_db["id"]})
    # return {"access_token": access_token, "token_type": "bearer"}
    response = RedirectResponse(url="https://fellowflightmatch.abdullah.buzz/")
    response.set_cookie(
        key="token",
        value=access_token,
        domain=".abdullah.buzz",  # or try specific domain if still not showing
        httponly=False,
        secure=True,
        samesite="None",
    )
    return response


# == Users ==
@app.get("/api/v1/users/me", tags=["Users"], response_model=UserResponse)
async def get_me(current_user: Dict[str, Any] = Depends(auth.get_current_user)):
    """Retrieves the profile for the currently authenticated user."""
    return current_user


@app.patch("/api/v1/users/me", tags=["Users"], response_model=UserResponse)
async def update_me(
    user_update: UserUpdate,
    current_user: Dict[str, Any] = Depends(auth.get_current_user),
):
    """Updates the authenticated user's profile."""
    updated_user = db_utils.update_user_linkedin(
        current_user["id"], user_update.linkedin_url
    )
    if not updated_user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    return updated_user


@app.delete("/api/v1/users/me", tags=["Users"], status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(current_user: Dict[str, Any] = Depends(auth.get_current_user)):
    """Deletes the authenticated user's account and all their data."""
    db_utils.delete_user(current_user["id"])
    return None


# == Forms ==
@app.post("/api/v1/formHandler")
async def formHandler(request: Request):
    # Check if user is authorized
    # Else return.
    # Sanitize?
    form = await request.form()  # this returns bytes
    form_data = FormDataModel(**form_dict)

    # update LinkedIn Url
    # ...
    # if user has existing flight don't create a flight return
    # create flight


# == Flights ==
@app.post(
    "/api/v1/flights",
    tags=["Flights"],
    status_code=status.HTTP_201_CREATED,
    response_model=FlightResponse,
)
async def create_flight(
    flight: FlightCreate, current_user: Dict[str, Any] = Depends(auth.get_current_user)
):
    """Creates a new flight entry for the authenticated user."""
    new_flight = db_utils.insert_flight(current_user["id"], flight.dict())

    # **TRIGGER INTERNAL NOTIFICATION FLOW**
    # _trigger_match_notifications(new_flight["id"], current_user)

    return new_flight


@app.delete(
    "/api/v1/flights/{flight_id}",
    tags=["Flights"],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_flight(
    flight_id: int, current_user: Dict[str, Any] = Depends(auth.get_current_user)
):
    """Deletes a specific flight entry owned by the user."""
    if not db_utils.check_flight_ownership(flight_id, current_user["id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not own this flight.")

    db_utils.delete_flight(flight_id)
    return None


# == Matches ==
@app.get("/api/v1/matches", tags=["Matches"], response_model=MatchResponse)
async def get_matches(
    flight_id: int = Query(...),
    current_user: Dict[str, Any] = Depends(auth.get_current_user),
):
    """Finds all matches for a specific flight owned by the user."""
    if not db_utils.check_flight_ownership(flight_id, current_user["id"]):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You cannot request matches for a flight you do not own.",
        )

    same_flight = db_utils.find_matches_for_flight(flight_id, current_user["id"])
    time_overlap = db_utils.find_overlaps_for_flight(flight_id, current_user["id"])

    return {"same_flight": same_flight, "time_overlap": time_overlap}
